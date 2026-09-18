import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

try:
    from gguf import GGUFReader
except ImportError:
    GGUFReader = None


@dataclass
class ModelCapability:
    model_id: str
    thinking: bool
    param_style: str
    levels: List[str]
    default_level: str
    max_budget: int = 32768
    thinking_tags: Tuple[str, str] = ("<think>", "</think>")


BUDGET_MAP = {
    "Off": 0,
    "Low": 2048,
    "Medium": 8192,
    "High": 24576,
    "xHigh": 48000,
    "Max": 64000,
    "On": 8192,
}

REASONING_EFFORT_MAP = {
    "Off": "none",
    "Low": "low",
    "Medium": "medium",
    "High": "high",
    "xHigh": "high",
    "Max": "high",
    "On": "medium",
}

CACHE_FILE = "cache/capabilities_cache.json"


def _get_cache_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    cache_path = os.path.join(project_root, CACHE_FILE)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    return cache_path


def _load_cache() -> Dict[str, dict]:
    path = _get_cache_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache: Dict[str, dict]) -> None:
    path = _get_cache_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def inspect_gguf_chat_template(gguf_path: str) -> Optional[ModelCapability]:
    """Inspects GGUF file tokenizer.chat_template for thinking tokens and exact levels."""
    if not GGUFReader or not os.path.exists(gguf_path) or "gemma" in gguf_path.lower():
        return None
    try:
        reader = GGUFReader(gguf_path)
        for field in reader.fields.values():
            if field.name == "tokenizer.chat_template":
                raw = field.parts[field.data[0]] if field.data else b""
                if isinstance(raw, (bytes, bytearray)):
                    template_str = raw.decode("utf-8", errors="ignore")
                elif hasattr(raw, "tobytes"):
                    template_str = raw.tobytes().decode("utf-8", errors="ignore")
                elif hasattr(raw, "__iter__"):
                    template_str = bytes(raw).decode("utf-8", errors="ignore")
                else:
                    template_str = str(raw)

                template_lower = template_str.lower()
                has_thinking = any(
                    kw in template_lower
                    for kw in [
                        "enable_thinking",
                        "<think>",
                        "thinking_mode",
                        "reasoning_content",
                        "think_budget",
                        "<|channel>thought",
                        "<|think|>",
                    ]
                )
                if has_thinking:
                    levels = ["Off", "Low", "Medium", "High", "xHigh", "Max"]
                    default_level = "Medium"

                    return ModelCapability(
                        model_id=os.path.basename(gguf_path),
                        thinking=True,
                        param_style="chat_template_kwargs",
                        levels=levels,
                        default_level=default_level,
                        max_budget=32768,
                        thinking_tags=("<think>", "</think>") if "<think>" in template_str else ("<|think|>", "<|turn>model"),
                    )
    except Exception:
        pass
    return None


_MEM_CACHE: Dict[str, ModelCapability] = {}


def get_model_capability(
    model_id: str,
    provider_id: str = "",
    api_key: str = "",
    model_path: str = "",
    timeout: float = 3.0,
) -> ModelCapability:
    """Universal thinking level and capability detector. Zero hardcoded mocks."""
    cache_key = f"{provider_id}:{model_id}" if provider_id else model_id

    if "gemma" in model_id.lower():
        cap = ModelCapability(
            model_id=model_id,
            thinking=False,
            param_style="none",
            levels=[],
            default_level="Off",
        )
        cache = _load_cache()
        cache[cache_key] = asdict(cap)
        _save_cache(cache)
        _MEM_CACHE[cache_key] = cap
        return cap

    if cache_key in _MEM_CACHE:
        return _MEM_CACHE[cache_key]

    cache = _load_cache()

    if cache_key in cache:
        item = cache[cache_key]
        cap = ModelCapability(
            model_id=item.get("model_id", model_id),
            thinking=item.get("thinking", False),
            param_style=item.get("param_style", "none"),
            levels=item.get("levels", []),
            default_level=item.get("default_level", "Medium" if item.get("thinking") else "Off"),
            max_budget=item.get("max_budget", 32768),
            thinking_tags=tuple(item.get("thinking_tags", ("<think>", "</think>"))),
        )
        _MEM_CACHE[cache_key] = cap
        return cap

    if provider_id == "local_gguf" or (model_path and os.path.exists(model_path)) or model_id.endswith(".gguf"):
        actual_path = model_path
        if not actual_path or not os.path.exists(actual_path):
            for candidate in [
                os.path.join("models", model_id),
                os.path.join(os.getcwd(), "models", model_id),
                os.path.join(r"D:\CMDAI CODE\models", os.path.basename(model_id)),
                os.path.join(r"E:\CMDAI CODE\models", os.path.basename(model_id)),
                os.path.join(r"D:\Swift\models", os.path.basename(model_id)),
                model_id,
            ]:
                if os.path.exists(candidate):
                    actual_path = candidate
                    break

        if actual_path and os.path.exists(actual_path):
            cap = inspect_gguf_chat_template(actual_path)
            if cap:
                cache[cache_key] = asdict(cap)
                _save_cache(cache)
                _MEM_CACHE[cache_key] = cap
                return cap

        mid_low = model_id.lower()
        has_thinking = any(k in mid_low for k in ["r1", "qwq", "reason"]) and ("gemma" not in mid_low)
        if has_thinking:
            levels = ["Medium", "High", "Off"]
            cap = ModelCapability(
                model_id=model_id,
                thinking=True,
                param_style="tag_based",
                levels=levels,
                default_level="Medium",
                max_budget=32768,
                thinking_tags=("<think>", "</think>"),
            )
        else:
            cap = ModelCapability(
                model_id=model_id,
                thinking=False,
                param_style="none",
                levels=[],
                default_level="Off",
            )
        cache[cache_key] = asdict(cap)
        _save_cache(cache)
        _MEM_CACHE[cache_key] = cap
        return cap

    if provider_id == "openrouter" and requests:
        try:
            resp = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                for m in data.get("data", []):
                    if m.get("id") == model_id:
                        params = m.get("supported_parameters", [])
                        if "reasoning" in params or "include_reasoning" in params:
                            mid_low = model_id.lower()
                            if any(k in mid_low for k in ["o1", "o3", "o4"]):
                                levels = ["Low", "Medium", "High", "xHigh"] if ("xhigh" in mid_low or "pro" in mid_low) else ["Low", "Medium", "High"]
                                param_style = "reasoning_effort"
                            elif "gemma" in mid_low:
                                levels = ["Off", "On"]
                                param_style = "chat_template_kwargs"
                            elif any(k in mid_low for k in ["claude-3-7", "claude-3.7", "fable"]):
                                levels = ["Off", "Low", "Medium", "High", "xHigh", "Max"]
                                param_style = "budget_tokens"
                            else:
                                levels = ["Low", "Medium", "High"]
                                param_style = "reasoning_effort"

                            cap = ModelCapability(
                                model_id=model_id,
                                thinking=True,
                                param_style=param_style,
                                levels=levels,
                                default_level="Medium" if "Medium" in levels else levels[0],
                            )
                            cache[cache_key] = asdict(cap)
                            _save_cache(cache)
                            return cap
        except Exception:
            pass

    if provider_id == "ollama" and requests:
        try:
            resp = requests.post(
                "http://localhost:11434/api/show",
                json={"name": model_id},
                timeout=timeout,
            )
            if resp.status_code == 200:
                template = resp.json().get("template", "").lower()
                if "<think>" in template or "enable_thinking" in template:
                    levels = ["Off", "On"] if "enable_thinking" in template else ["On (Reasoning)", "Off"]
                    cap = ModelCapability(
                        model_id=model_id,
                        thinking=True,
                        param_style="tag_based",
                        levels=levels,
                        default_level=levels[0],
                    )
                    cache[cache_key] = asdict(cap)
                    _save_cache(cache)
                    return cap
        except Exception:
            pass

    mid = model_id.lower()
    
    if "claude-3-7" in mid or "claude-3.7" in mid or "fable-5" in mid:
        levels = ["Off", "Low", "Medium", "High", "xHigh", "Max"] if "xhigh" in mid else ["Off", "Low", "Medium", "High", "Max"]
        cap = ModelCapability(
            model_id=model_id,
            thinking=True,
            param_style="budget_tokens",
            levels=levels,
            default_level="Medium",
            max_budget=64000,
        )
        cache[cache_key] = asdict(cap)
        _save_cache(cache)
        return cap

    if mid.startswith("o1") or mid.startswith("o3") or mid.startswith("o4") or "reasoning" in mid:
        levels = ["Low", "Medium", "High", "xHigh"] if ("xhigh" in mid or "pro" in mid) else ["Low", "Medium", "High"]
        cap = ModelCapability(
            model_id=model_id,
            thinking=True,
            param_style="reasoning_effort",
            levels=levels,
            default_level="Medium",
        )
        cache[cache_key] = asdict(cap)
        _save_cache(cache)
        return cap

    if "r1" in mid or "deepseek-reasoner" in mid or "qwq" in mid or "qwen3" in mid:
        levels = ["Off", "Low", "Medium", "High", "xHigh", "Max"]
        cap = ModelCapability(
            model_id=model_id,
            thinking=True,
            param_style="tag_based" if provider_id != "deepseek" else "reasoning_effort",
            levels=levels,
            default_level="Medium",
        )
        cache[cache_key] = asdict(cap)
        _save_cache(cache)
        return cap

    cap = ModelCapability(
        model_id=model_id,
        thinking=False,
        param_style="none",
        levels=[],
        default_level="Off",
    )
    cache[cache_key] = asdict(cap)
    _save_cache(cache)
    return cap


def clean_model_name(raw_name: str) -> str:
    """Cleans up raw model filenames or identifiers into polished human-readable display names.
    Examples:
        'gemma-4-E4B-it-Q4_0.gguf' -> 'Gemma 4 E4B IT'
        'Qwen2.5-Coder-7B-Instruct-Q5_K_M.gguf' -> 'Qwen 2.5 Coder 7B Instruct'
        'deepseek-ai/DeepSeek-R1' -> 'DeepSeek R1'
        'meta-llama/Llama-3.1-8B-Instruct' -> 'Llama 3.1 8B Instruct'
    """
    if not raw_name:
        return "Model"
    name = raw_name.strip()
    if "/" in name:
        name = name.split("/")[-1]
    elif "\\" in name:
        name = name.split("\\")[-1]

    name = re.sub(r'\.(gguf|bin|safetensors|pt|onnx)$', '', name, flags=re.IGNORECASE)

    name = re.sub(r'[-_](?:IQ\d_[A-Z\d]+|Q\d_[A-Z\d_]+|Q\d_[0-9]|FP16|BF16|INT[48]|int[48])$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_]\d{8}$', '', name)

    parts = re.split(r'[-_]+', name)

    clean_parts = []
    for p in parts:
        if not p:
            continue
        pl = p.lower()
        if pl == "it":
            clean_parts.append("IT")
        elif pl in ("ai", "llm", "coder", "instruct", "base", "chat"):
            clean_parts.append(p.capitalize())
        elif pl.startswith("q") and len(pl) <= 3 and pl[1:].isdigit():
            continue
        elif re.match(r'^[a-zA-Z]+$', p):
            clean_parts.append(p.capitalize())
        else:
            clean_parts.append(p)

    res = " ".join(clean_parts).strip()
    return res or raw_name

