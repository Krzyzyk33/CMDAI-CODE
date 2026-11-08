import json
import os
from typing import Any, Dict, List, Optional, Tuple

import psutil

try:
    from gguf import GGUFReader
except ImportError:
    GGUFReader = None

COMPACTION_THRESHOLD = 0.97
METADATA_CACHE_FILE = "cache/models_metadata_cache.json"

# Used only when nobody has published a window for the model - not one lookup
# answered, no file to read, no provider plugin to ask. Named so that the places
# which display it can say "not known" instead of presenting a guess as a fact,
# which is what a bare `32_768` in a return statement did.
FALLBACK_CONTEXT_LIMIT = 32_768


def _get_metadata_cache_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    cache_path = os.path.join(project_root, METADATA_CACHE_FILE)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    return cache_path


def load_model_metadata_cache() -> Dict[str, Any]:
    path = _get_metadata_cache_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_model_metadata(model_id: str, provider_id: str, context_length: int) -> None:
    if context_length <= 0:
        return
    cache = load_model_metadata_cache()
    key = f"{provider_id}:{model_id}".lower()
    cache[key] = {"context_length": context_length}
    cache[model_id.lower()] = {"context_length": context_length}
    short = model_id.split("/")[-1].lower()
    cache[short] = {"context_length": context_length}
    path = _get_metadata_cache_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


_GGUF_META_CACHE: Dict[str, Dict[str, Any]] = {}


def _gguf_field_text(field) -> str:
    try:
        raw = field.parts[field.data[0]] if field.data else b""
        if isinstance(raw, (bytes, bytearray)):
            return raw.decode("utf-8", errors="ignore")
        if hasattr(raw, "tobytes"):
            return raw.tobytes().decode("utf-8", errors="ignore")
        if hasattr(raw, "__iter__"):
            return bytes(raw).decode("utf-8", errors="ignore")
        return str(raw)
    except Exception:
        return ""


def _gguf_field_int(field) -> Optional[int]:
    try:
        raw = field.parts[field.data[0]] if field.data else None
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            return int.from_bytes(bytes(raw)[:8], "little")
        if hasattr(raw, "tobytes"):
            return int.from_bytes(raw.tobytes()[:8], "little")
        if hasattr(raw, "__iter__"):
            return int.from_bytes(bytes(raw)[:8], "little")
        return int(raw)
    except Exception:
        return None


def find_local_model_file(model_id: str, models_dir: str = "", workdir: str = "") -> str:
    """Find a model file. Delegates to core.model_paths.

    This used to carry its own candidate list, including four hardcoded
    D:/E:/C: paths under a "CMDAI CODE" name with a space in it. Those
    stopped matching when the checkout moved to E:\\CMDAI-CODE and the
    guessing had been dead weight since, hidden behind a candidate that
    happened to work.
    """
    from .model_paths import resolve_model_file
    return resolve_model_file(model_id, models_dir=models_dir, workdir=workdir)


def _read_gguf_header_binary(gguf_path: str) -> Dict[str, Any]:
    """Pure-Python GGUF binary header reader so metadata works even without `gguf` package."""
    import struct
    out: Dict[str, Any] = {}
    try:
        with open(gguf_path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return out
            ver_b = f.read(4)
            if len(ver_b) < 4:
                return out
            version = struct.unpack("<I", ver_b)[0]
            if version == 1:
                counts_b = f.read(8)
                if len(counts_b) < 8:
                    return out
                _, kv_count = struct.unpack("<II", counts_b)
                u64_len = False
            else:
                counts_b = f.read(16)
                if len(counts_b) < 16:
                    return out
                _, kv_count = struct.unpack("<QQ", counts_b)
                u64_len = True

            def _read_len() -> int:
                b = f.read(8 if u64_len else 4)
                return struct.unpack("<Q" if u64_len else "<I", b)[0]

            def _read_str() -> str:
                n = _read_len()
                return f.read(n).decode("utf-8", errors="ignore")

            scalar_fmt = {
                0: ("<B", 1),
                1: ("<b", 1),
                2: ("<H", 2),
                3: ("<h", 2),
                4: ("<I", 4),
                5: ("<i", 4),
                6: ("<f", 4),
                7: ("<?", 1),
                10: ("<Q", 8),
                11: ("<q", 8),
                12: ("<d", 8),
            }

            def _skip_or_read_val(vtype: int, keep: bool):
                if vtype in scalar_fmt:
                    fmt, sz = scalar_fmt[vtype]
                    raw = f.read(sz)
                    return struct.unpack(fmt, raw)[0] if keep else None
                if vtype == 8:
                    n = _read_len()
                    if keep:
                        return f.read(n).decode("utf-8", errors="ignore")
                    f.seek(n, os.SEEK_CUR)
                    return None
                if vtype == 9:
                    elem_t = struct.unpack("<I", f.read(4))[0]
                    arr_n = _read_len()
                    if elem_t in scalar_fmt:
                        _, sz = scalar_fmt[elem_t]
                        f.seek(sz * arr_n, os.SEEK_CUR)
                    else:
                        for _ in range(arr_n):
                            _skip_or_read_val(elem_t, False)
                    return None
                raise ValueError(f"Unknown GGUF value type: {vtype}")

            for _ in range(min(int(kv_count), 512)):
                key = _read_str()
                vtype = struct.unpack("<I", f.read(4))[0]
                want = (
                    key == "general.architecture"
                    or key.endswith(".context_length")
                    or key.endswith(".block_count")
                    or key.endswith(".attention.head_count_kv")
                    or key.endswith(".attention.key_length")
                    or key.endswith(".embedding_length")
                    or key.endswith(".attention.head_count")
                )
                val = _skip_or_read_val(vtype, want)
                if want and val is not None:
                    out[key] = val
    except Exception:
        pass
    return out


def read_gguf_metadata(gguf_path: str) -> Dict[str, Any]:
    if not gguf_path:
        return {
            "n_ctx_train": 8192,
            "file_size_gb": 0.0,
            "arch": "unknown",
            "n_layer": None,
            "n_head_kv": None,
            "n_embd_head_k": None,
            "estimated": True,
        }
    abs_path = os.path.abspath(gguf_path)
    if abs_path in _GGUF_META_CACHE:
        return _GGUF_META_CACHE[abs_path]

    result: Dict[str, Any] = {
        "n_ctx_train": 8192,
        "file_size_gb": 0.0,
        "arch": "unknown",
        "n_layer": None,
        "n_head_kv": None,
        "n_embd_head_k": None,
        "estimated": True,
    }
    if not os.path.exists(gguf_path):
        return result

    try:
        result["file_size_gb"] = os.path.getsize(gguf_path) / (1024**3)
    except Exception:
        pass

    if GGUFReader:
        try:
            reader = GGUFReader(gguf_path)
            fields = reader.fields
            arch_f = fields.get("general.architecture")
            arch = _gguf_field_text(arch_f).strip().lower() if arch_f is not None else ""
            if arch:
                result["arch"] = arch
                ctx_f = fields.get(f"{arch}.context_length")
                ctx = _gguf_field_int(ctx_f) if ctx_f is not None else None
                if ctx and ctx > 0:
                    result["n_ctx_train"] = ctx
                layer_f = fields.get(f"{arch}.block_count")
                layer = _gguf_field_int(layer_f) if layer_f is not None else None
                if layer and layer > 0:
                    result["n_layer"] = layer
                kv_f = fields.get(f"{arch}.attention.head_count_kv")
                kv = _gguf_field_int(kv_f) if kv_f is not None else None
                if kv and kv > 0:
                    result["n_head_kv"] = kv
                key_f = fields.get(f"{arch}.attention.key_length")
                key = _gguf_field_int(key_f) if key_f is not None else None
                if key and key > 0:
                    result["n_embd_head_k"] = key
                if result["n_embd_head_k"] is None:
                    emb_f = fields.get(f"{arch}.embedding_length")
                    head_f = fields.get(f"{arch}.attention.head_count")
                    emb = _gguf_field_int(emb_f) if emb_f is not None else None
                    heads = _gguf_field_int(head_f) if head_f is not None else None
                    if emb and heads:
                        result["n_embd_head_k"] = emb // heads
            if result["arch"] != "unknown" and result["n_ctx_train"] != 8192:
                result["estimated"] = False
            elif result["n_layer"] is not None:
                result["estimated"] = False
        except Exception:
            pass

    if result["estimated"]:
        raw_hdr = _read_gguf_header_binary(gguf_path)
        arch = str(raw_hdr.get("general.architecture") or "").strip().lower()
        if arch:
            result["arch"] = arch
            ctx = raw_hdr.get(f"{arch}.context_length")
            if isinstance(ctx, int) and ctx > 0:
                result["n_ctx_train"] = ctx
            layer = raw_hdr.get(f"{arch}.block_count")
            if isinstance(layer, int) and layer > 0:
                result["n_layer"] = layer
            kv = raw_hdr.get(f"{arch}.attention.head_count_kv")
            if isinstance(kv, int) and kv > 0:
                result["n_head_kv"] = kv
            key_l = raw_hdr.get(f"{arch}.attention.key_length")
            if isinstance(key_l, int) and key_l > 0:
                result["n_embd_head_k"] = key_l
            if result["n_embd_head_k"] is None:
                emb = raw_hdr.get(f"{arch}.embedding_length")
                heads = raw_hdr.get(f"{arch}.attention.head_count")
                if isinstance(emb, int) and isinstance(heads, int) and heads > 0:
                    result["n_embd_head_k"] = emb // heads
            if result["n_layer"] is not None or result["n_ctx_train"] != 8192:
                result["estimated"] = False

    _GGUF_META_CACHE[abs_path] = result
    return result


def estimate_kv_cache_bytes_per_token(meta: Dict[str, Any], dtype_bytes: int = 2) -> int:
    n_layer = meta.get("n_layer")
    n_head_kv = meta.get("n_head_kv")
    n_embd_head_k = meta.get("n_embd_head_k")
    if n_layer and n_head_kv and n_embd_head_k:
        return 2 * int(n_layer) * int(n_head_kv) * int(n_embd_head_k) * int(dtype_bytes)
    return 256 * 1024


def estimate_model_memory(gguf_path: str, n_ctx: int = 4096) -> Dict[str, float]:
    meta = read_gguf_metadata(gguf_path)
    weights_gb = meta["file_size_gb"]

    kv_cache_gb = n_ctx * estimate_kv_cache_bytes_per_token(meta) / (1024**3)
    total_gb = weights_gb + kv_cache_gb

    vm = psutil.virtual_memory()
    total_sys_gb = vm.total / (1024**3)
    available_sys_gb = vm.available / (1024**3)

    return {
        "weights_gb": round(weights_gb, 2),
        "kv_cache_gb": round(kv_cache_gb, 2),
        "total_required_gb": round(total_gb, 2),
        "system_total_gb": round(total_sys_gb, 1),
        "system_available_gb": round(available_sys_gb, 1),
        "fits_comfortably": (available_sys_gb - total_gb) >= 2.0,
    }


def extract_model_context_length(item: Dict[str, Any]) -> int:
    """Extract context window size from diverse API / custom provider `/models` JSON objects."""
    if not isinstance(item, dict):
        return 0
    for k in (
        "context_length",
        "context_window",
        "max_model_len",
        "max_context_length",
        "max_input_tokens",
        "input_token_limit",
        "max_seq_len",
        "n_ctx",
    ):
        val = item.get(k)
        if isinstance(val, (int, float)) and int(val) > 0:
            return int(val)
    lim = item.get("limit")
    if isinstance(lim, dict):
        c_val = lim.get("context")
        if isinstance(c_val, (int, float)) and int(c_val) > 0:
            return int(c_val)
    top_p = item.get("top_provider")
    if isinstance(top_p, dict):
        c_val = top_p.get("context_length")
        if isinstance(c_val, (int, float)) and int(c_val) > 0:
            return int(c_val)
    return 0


def _normalize_model_key(s: str) -> str:
    import re
    low = (s or "").strip().lower()
    if ":" in low:
        head, tail = low.split(":", 1)
        if tail in ("free", "extended", "beta", "nitro", "thinking", "online"):
            low = head
    low = re.sub(r"-20\d{6}$", "", low)
    low = re.sub(r"-latest$", "", low)
    return re.sub(r"[^a-z0-9]", "", low)


def _lookup_in_metadata_cache(cache: Dict[str, Any], provider_id: str, model_id: str) -> int:
    if not cache or not model_id:
        return 0
    key = f"{provider_id}:{model_id}".lower()
    low_id = (model_id or "").lower()
    short_id = low_id.split("/")[-1] if low_id else ""

    for cand_k in (key, low_id, short_id):
        if cand_k and cand_k in cache and cache[cand_k].get("context_length"):
            return int(cache[cand_k]["context_length"])

    target_norm = _normalize_model_key(short_id or low_id)
    if not target_norm:
        return 0

    for k, val in cache.items():
        if not isinstance(val, dict):
            continue
        clen = val.get("context_length")
        if not clen:
            continue
        k_tail = k.split(":", 1)[-1] if ":" in k else k
        k_short = k_tail.split("/")[-1]
        if _normalize_model_key(k_short) == target_norm or _normalize_model_key(k_tail) == target_norm:
            return int(clen)
    return 0


def _known_family_context_limit(model_id: str) -> int:
    """Return known context window for major published model families when offline or on custom providers."""
    low = (model_id or "").strip().lower().split("/")[-1]
    if not low:
        return 0
    if any(low.startswith(p) for p in ("gemini-1.5", "gemini-2.", "gemini-2-", "gemini-exp", "gemini-pro-1.5", "gemini-flash-1.5")):
        return 1_000_000
    if any(low.startswith(p) for p in ("claude-3", "claude-4", "claude-sonnet-4", "claude-opus-4", "claude-haiku-4")):
        return 200_000
    if any(low.startswith(p) for p in ("o1", "o3", "o4", "gpt-4o", "gpt-4-turbo", "gpt-4.1", "gpt-4.5")):
        return 128_000
    if any(low.startswith(p) for p in ("deepseek-chat", "deepseek-reasoner", "deepseek-r1", "deepseek-v3", "grok-3", "grok-2", "llama-3.1", "llama-3.2", "llama-3.3", "llama-4")):
        return 128_000
    if any(low.startswith(p) for p in ("qwen2.5", "qwen-2.5", "qwq", "codestral", "mistral-small", "mistral-large", "mixtral-8x7b", "command-r")):
        return 32_768
    return 0


def published_context_limit(model_id: str, provider_id: str = "") -> int:
    """The model's real window, as somebody who knows has published it.

    0 means nobody has said. Checks:
    1. models.dev for the exact provider
    2. registered/cached model capabilities (e.g. GGUF or OpenCode plugin)
    3. models.dev cross-provider lookup (for custom user-added providers or relays)
    4. metadata cache (exact + normalized) and known model families
    """
    try:
        from . import model_catalog

        facts = model_catalog.lookup(provider_id, model_id)
        if facts is not None and facts.context_limit:
            return int(facts.context_limit)
    except Exception:
        pass

    try:
        from .capabilities import get_model_capability

        cap = get_model_capability(model_id, provider_id=provider_id)
        if cap is not None and cap.context_limit:
            return int(cap.context_limit)
    except Exception:
        pass

    try:
        from . import model_catalog

        facts_any = model_catalog.lookup_any_provider(model_id, preferred_provider_id=provider_id)
        if facts_any is not None and facts_any.context_limit:
            return int(facts_any.context_limit)
    except Exception:
        pass

    cached = _lookup_in_metadata_cache(load_model_metadata_cache(), provider_id, model_id)
    if cached:
        return cached

    fam = _known_family_context_limit(model_id)
    if fam:
        return fam

    return 0


def resolve_context_limit(
    model_id: str,
    provider_id: str = "openrouter",
    model_path: Optional[str] = None,
    api_key: str = "",
    models_dir: str = "",
    workdir: str = "",
) -> Tuple[int, bool, str]:
    """Return `(context_limit, is_known, source)` for local GGUF, API, and custom providers.

    `is_known` is True whenever the window was obtained from a real source (GGUF
    header, provider `/models` metadata cache, models.dev catalog, or provider API),
    so a model whose genuine window is 32,768 is never mislabelled as an unknown
    fallback.
    """
    if provider_id == "local_gguf" or (model_path and os.path.exists(model_path)) or str(model_id or "").lower().endswith(".gguf"):
        path = ""
        if model_path and os.path.exists(model_path) and os.path.isfile(model_path):
            path = os.path.abspath(model_path)
        else:
            path = find_local_model_file(model_id, models_dir=models_dir, workdir=workdir)
        meta = read_gguf_metadata(path) if path else {
            "n_ctx_train": 8192, "file_size_gb": 0.0, "arch": "unknown",
            "n_layer": None, "n_head_kv": None, "n_embd_head_k": None, "estimated": True,
        }
        train_ctx = int(meta.get("n_ctx_train", 8192) or 8192)

        vm = psutil.virtual_memory()
        available_gb = vm.available / (1024**3)

        model_size_gb = float(meta.get("file_size_gb", 0.0) or 0.0)
        ram_after_model = max(0.5, available_gb - model_size_gb)

        cache_budget_gb = ram_after_model * 0.70

        bytes_per_token = estimate_kv_cache_bytes_per_token(meta)
        max_possible_tokens = int(cache_budget_gb * (1024**3) / bytes_per_token)

        dynamic_limit = max(2048, min(train_ctx, max_possible_tokens))
        is_known = not bool(meta.get("estimated", True))
        return dynamic_limit, is_known, ("gguf" if is_known else "gguf_fallback")

    cache = load_model_metadata_cache()
    cached_limit = _lookup_in_metadata_cache(cache, provider_id, model_id)
    if cached_limit:
        return cached_limit, True, "cache"

    published = published_context_limit(model_id, provider_id)
    if published:
        save_model_metadata(model_id, provider_id, published)
        return published, True, "catalog"

    # Try querying the active provider's own `/models` endpoint (including user-added custom_providers)
    try:
        import requests
        from .providers import PROVIDERS_CATALOG, load_custom_providers
        load_custom_providers()
        pinfo = PROVIDERS_CATALOG.get(provider_id)
        if pinfo and pinfo.base_url and pinfo.supports_remote_fetch and provider_id != "openrouter":
            if not api_key:
                try:
                    from .settings import get_settings
                    api_key = get_settings().get_api_key(provider_id)
                except Exception:
                    api_key = ""
            headers = {"Accept": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            m_url = pinfo.base_url.rstrip("/") + (pinfo.models_endpoint or "/models")
            p_resp = requests.get(m_url, headers=headers, timeout=2.5)
            if p_resp.status_code == 200:
                p_data = p_resp.json()
                items = p_data.get("data") if isinstance(p_data, dict) else None
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        imid = str(item.get("id") or "").strip()
                        clen = extract_model_context_length(item)
                        if imid and clen > 0:
                            save_model_metadata(imid, provider_id, clen)
                    cache = load_model_metadata_cache()
                    p_limit = _lookup_in_metadata_cache(cache, provider_id, model_id)
                    if p_limit:
                        return p_limit, True, "provider_api"
    except Exception:
        pass

    try:
        import requests
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=3.0)
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                imid = str(item.get("id", "")).lower()
                clen = extract_model_context_length(item)
                if imid and clen:
                    cache[imid] = {"context_length": clen}
                    short = imid.split("/")[-1]
                    cache[short] = {"context_length": clen}

            path = _get_metadata_cache_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)

            or_limit = _lookup_in_metadata_cache(cache, provider_id, model_id)
            if or_limit:
                return or_limit, True, "openrouter_api"
    except Exception:
        pass

    return FALLBACK_CONTEXT_LIMIT, False, "fallback"


def get_dynamic_context_limit(
    model_id: str,
    provider_id: str = "openrouter",
    model_path: Optional[str] = None,
    api_key: str = "",
    models_dir: str = "",
    workdir: str = "",
) -> int:
    limit, _, _ = resolve_context_limit(
        model_id=model_id,
        provider_id=provider_id,
        model_path=model_path,
        api_key=api_key,
        models_dir=models_dir,
        workdir=workdir,
    )
    return limit


def should_summarize(current_token_count: int, context_limit: int) -> bool:
    if context_limit <= 0:
        return False
    threshold = int(context_limit * COMPACTION_THRESHOLD)
    return current_token_count >= threshold


def estimate_token_count(messages: List[Dict[str, Any]]) -> int:
    total_toks = 0
    for m in messages:
        c = str(m.get("content", ""))
        total_toks += max(len(c.split()) * 4 // 3, len(c) // 3) + 4
    return max(1, total_toks)


def detect_conversation_language(messages: List[Dict[str, Any]]) -> str:
    user_texts = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user").lower()
    pl_markers = (
        " i ", " w ", " z ", " na ", " do ", " nie ", " to ", " jest ", " jak ",
        "plik", "stwórz", "zrób", "popraw", "dodaj", "usuń", "kod", "zobacz", "teraz", "działa"
    )
    score = sum(1 for marker in pl_markers if marker in user_texts)
    return "pl" if score >= 2 else "en"


def prune_message_for_summarization(msg: Dict[str, Any], max_content_len: int = 1600) -> Dict[str, Any]:
    role = msg.get("role", "user")
    content = str(msg.get("content", ""))

    if len(content) > max_content_len:
        head = content[:900]
        tail = content[-500:]
        condensed = f"{head}\n... [TRUNCATED VERBOSE DATA ({len(content) - 1400} chars)] ...\n{tail}"
        return {"role": role, "content": condensed}

    return {"role": role, "content": content}


def build_summarization_payload(
    messages: List[Dict[str, Any]],
    last_modified_files: Optional[List[str]] = None,
    max_budget_tokens: int = 2500,
) -> List[Dict[str, Any]]:
    if not messages:
        raw_msgs = []
    elif len(messages) <= 4:
        raw_msgs = [prune_message_for_summarization(m) for m in messages]
    else:
        first_m = prune_message_for_summarization(messages[0])
        recent_m = [prune_message_for_summarization(m, max_content_len=1200) for m in messages[-3:]]
        middle_m = [prune_message_for_summarization(m, max_content_len=400) for m in messages[1:-3]]
        raw_msgs = [first_m] + middle_m + recent_m

    pruned_history = []
    total_tokens = 0
    for m in reversed(raw_msgs):
        m_tok = len(str(m.get("content", "")).split())
        if total_tokens + m_tok <= max_budget_tokens or not pruned_history:
            pruned_history.insert(0, m)
            total_tokens += m_tok
        elif m == raw_msgs[0]:
            pruned_history.insert(0, prune_message_for_summarization(m, max_content_len=300))
            break

    lang = detect_conversation_language(messages)
    context_hint = []
    if last_modified_files:
        unique_files = list(dict.fromkeys(last_modified_files))
        context_hint.append(f"Files modified/touched during this session: {', '.join(unique_files)}")

    if lang == "pl":
        sys_prompt = (
            "Jesteś doświadczonym inżynierem oprogramowania i analitykiem kodu.\n"
            "Twoim zadaniem jest sporządzenie wyczerpującego, konkretnego i naturalnego podsumowania dotychczasowej sesji programistycznej w języku polskim.\n"
            "Wymagania:\n"
            "- Pisz w języku polskim, płynnym tekstem ciągłym w spójnych akapitach.\n"
            "- Nie używaj pustych szablonów, list kontrolnych ani sztywnych nagłówków (np. 'Goal:', 'Deliverables:').\n"
            "- Uwzględnij w podsumowaniu:\n"
            "  1. Co użytkownik zlecił i jaki problem był rozwiązywany.\n"
            "  2. Konkretne pliki, które utworzono lub zmodyfikowano, oraz jakie dokładnie zmiany i funkcjonalności wprowadzono.\n"
            "  3. Wyniki wykonanych testów, diagnostyki czy poleceń oraz naprawione błędy.\n"
            "  4. Aktualny stan projektu oraz bezpośrednie kolejne kroki.\n"
            "- Zachowaj profesjonalny, konkretny i czytelny styl techniczny."
        )
        user_prompt = (
            "Przedstaw spójne, profesjonalne podsumowanie dotychczasowych prac technicznych w języku polskim. "
            "Opisz dokładnie zmienione pliki, podjęte decyzje i aktualny stan projektu w formie płynnych akapitów."
        )
    else:
        sys_prompt = (
            "You are a senior software engineering assistant.\n"
            "Your task is to write a natural, comprehensive summary of this programming session that is technically accurate and fluent.\n"
            "Guidelines:\n"
            "- Write in natural, cohesive prose divided into clear paragraphs.\n"
            "- Do NOT use rigid bullet points, empty templates, or repetitive labels ('Goal:', 'Constraints:').\n"
            "- Clearly cover:\n"
            "  1. The user's primary objectives and project requirements.\n"
            "  2. Concrete files created, edited, or inspected, with the specific changes implemented.\n"
            "  3. Any commands, test runs, syntax checks, and errors resolved.\n"
            "  4. Current status of the codebase and immediate next actions.\n"
            "- Be concise, thorough, and technically precise."
        )
        user_prompt = (
            "Provide a natural, continuous text summary of technical progress so far. "
            "Detail the files modified, key implementation choices, test results, and current project state."
        )

    if context_hint:
        user_prompt = f"{' '.join(context_hint)}\n\n{user_prompt}"

    return [
        {"role": "system", "content": sys_prompt},
        *pruned_history,
        {"role": "user", "content": user_prompt},
    ]


def format_compacted_handoff(
    raw_summary: str = "",
    last_modified_files: Optional[List[str]] = None,
    lang: str = "en",
    **kwargs,
) -> str:
    import re
    summary = (raw_summary or kwargs.get("summary_text", "")).strip()
    is_err = any(err_kw in summary.lower() for err_kw in [
        "error:", "exceed context window", "status code", "rate limit", "token limit", "api error", "400 bad request"
    ])
    if is_err or not summary:
        if lang == "pl":
            summary = "Poprzedni stan sesji i kluczowe modyfikacje kodu zostały zachowane."
        else:
            summary = "Previous session state and key code modifications have been preserved."

    summary = re.sub(r"^#+\s*CONTEXT COMPACTION STATE HANDOFF\s*", "", summary, flags=re.IGNORECASE).strip()
    summary = re.sub(r"^#+\s*Context Compaction[^\n]*\n*", "", summary, flags=re.IGNORECASE).strip()
    summary = re.sub(r"^●?\s*Conversation Summary[^\n]*\n*", "", summary, flags=re.IGNORECASE).strip()
    if not summary:
        if lang == "pl":
            summary = "Poprzedni stan sesji i kluczowe modyfikacje kodu zostały zachowane."
        else:
            summary = "Previous session state and key code modifications have been preserved."

    header = "Podsumowanie dotychczasowego kontekstu:" if lang == "pl" else "Summary of previous session context:"
    if last_modified_files:
        unique_files = list(dict.fromkeys(last_modified_files))
        if lang == "pl":
            files_line = f"Zmodyfikowane pliki ({len(unique_files)}): {', '.join(unique_files)}."
        else:
            files_line = f"Files touched so far: {', '.join(unique_files)}."
        return f"{header}\n{files_line}\n\n{summary}"
    return f"{header}\n{summary}"

