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


def find_local_model_file(model_id: str, models_dir: str = "models", workdir: str = "") -> str:
    base = os.path.basename(model_id)
    candidates = [
        os.path.join(models_dir, model_id) if models_dir else "",
        os.path.join(models_dir, base) if models_dir else "",
        os.path.join(workdir, "models", model_id) if workdir else "",
        os.path.join(workdir, "models", base) if workdir else "",
        os.path.join("D:/CMDAI CODE/models", model_id),
        os.path.join("D:/CMDAI CODE/models", base),
        os.path.join("E:/CMDAI CODE/models", model_id),
        os.path.join("E:/CMDAI CODE/models", base),
        model_id,
        os.path.abspath(model_id),
    ]
    for c in candidates:
        if c and os.path.exists(c) and os.path.isfile(c):
            return os.path.abspath(c)
    return ""


def read_gguf_metadata(gguf_path: str) -> Dict[str, Any]:
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

    if not GGUFReader:
        _GGUF_META_CACHE[abs_path] = result
        return result

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


def get_dynamic_context_limit(
    model_id: str,
    provider_id: str = "openrouter",
    model_path: Optional[str] = None,
    api_key: str = "",
    models_dir: str = "models",
    workdir: str = "",
) -> int:
    if provider_id == "local_gguf" or (model_path and os.path.exists(model_path)):
        path = ""
        if model_path and os.path.exists(model_path) and os.path.isfile(model_path):
            path = os.path.abspath(model_path)
        else:
            path = find_local_model_file(model_id, models_dir=models_dir, workdir=workdir)
        meta = read_gguf_metadata(path) if path else {
            "n_ctx_train": 8192, "file_size_gb": 0.0, "arch": "unknown",
            "n_layer": None, "n_head_kv": None, "n_embd_head_k": None, "estimated": True,
        }
        train_ctx = meta.get("n_ctx_train", 8192) or 8192

        vm = psutil.virtual_memory()
        available_gb = vm.available / (1024**3)

        model_size_gb = meta.get("file_size_gb", 0.0) or 0.0
        ram_after_model = max(0.5, available_gb - model_size_gb)

        cache_budget_gb = ram_after_model * 0.70

        bytes_per_token = estimate_kv_cache_bytes_per_token(meta)
        max_possible_tokens = int(cache_budget_gb * (1024**3) / bytes_per_token)

        dynamic_limit = max(2048, min(train_ctx, max_possible_tokens))
        return dynamic_limit

    cache = load_model_metadata_cache()
    key = f"{provider_id}:{model_id}".lower()
    if key in cache and cache[key].get("context_length"):
        return cache[key]["context_length"]
    if model_id.lower() in cache and cache[model_id.lower()].get("context_length"):
        return cache[model_id.lower()]["context_length"]

    try:
        import requests
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=3.0)
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                imid = item.get("id", "").lower()
                clen = item.get("context_length", 0)
                if clen:
                    cache[imid] = {"context_length": clen}
                    short = imid.split("/")[-1]
                    cache[short] = {"context_length": clen}

            path = _get_metadata_cache_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)

            if key in cache and cache[key].get("context_length"):
                return cache[key]["context_length"]
            if model_id.lower() in cache and cache[model_id.lower()].get("context_length"):
                return cache[model_id.lower()]["context_length"]
    except Exception:
        pass

    return 32_768


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

