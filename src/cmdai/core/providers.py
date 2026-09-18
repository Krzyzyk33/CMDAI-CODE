import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

import requests
from .capabilities import BUDGET_MAP, REASONING_EFFORT_MAP, get_model_capability

TOOL_COMPLETE_PATTERNS = re.compile(
    r'(?:'
    r'<tool:\w+[^>]*?/>'
    r'|</tool:\w+>'
    r'|<tool_call>[^>]*?/>'
    r'|</tool_call>'
    r'|<\|tool_call>[^>]*?/>'
    r'|<tool:(?:ls|read|glob|search|code_search|bugs)[^>]*?>'
    r')',
    re.IGNORECASE
)


@dataclass
class ProviderInfo:
    id: str
    name: str
    base_url: str
    key_url: str
    provider_type: str
    default_models: List[str] = field(default_factory=list)
    supports_remote_fetch: bool = True
    models_endpoint: str = "/models"


PROVIDERS_CATALOG: Dict[str, ProviderInfo] = {
    "local_gguf": ProviderInfo(
        id="local_gguf",
        name="Local GGUF (Vulkan/CPU)",
        base_url="local://models",
        key_url="",
        provider_type="local",
        default_models=[],
        supports_remote_fetch=False,
    ),
    "opencode": ProviderInfo(
        id="opencode",
        name="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        key_url="https://opencode.ai/zen",
        provider_type="cloud",
        default_models=[
            "deepseek-v4-flash-free",
            "mimo-v2.5-free",
            "qwen3.6-plus-free",
            "minimax-m3-free",
            "nemotron-3-ultra-free",
            "big-pickle",
            "claude-3-7-sonnet",
            "gpt-4o",
        ],
        supports_remote_fetch=True,
    ),
    "opencode_zen": ProviderInfo(
        id="opencode_zen",
        name="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        key_url="https://opencode.ai/zen",
        provider_type="cloud",
        default_models=[
            "deepseek-v4-flash-free",
            "mimo-v2.5-free",
            "qwen3.6-plus-free",
            "minimax-m3-free",
            "nemotron-3-ultra-free",
            "big-pickle",
            "claude-3-7-sonnet",
            "gpt-4o",
        ],
        supports_remote_fetch=True,
    ),
    "openrouter": ProviderInfo(
        id="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        key_url="https://openrouter.ai/keys",
        provider_type="cloud",
        default_models=[
            "anthropic/claude-3.7-sonnet",
            "openai/o3-mini",
            "deepseek/deepseek-r1",
            "meta-llama/llama-3.3-70b-instruct",
            "google/gemini-2.0-flash-exp:free",
        ],
    ),
    "openai": ProviderInfo(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        key_url="https://platform.openai.com/api-keys",
        provider_type="cloud",
        default_models=[
            "o3-mini",
            "o1",
            "gpt-4o",
            "gpt-4o-mini",
        ],
    ),
    "anthropic": ProviderInfo(
        id="anthropic",
        name="Anthropic",
        base_url="https://api.anthropic.com/v1",
        key_url="https://console.anthropic.com/settings/keys",
        provider_type="cloud",
        default_models=[
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ],
        supports_remote_fetch=False,
    ),
    "gemini": ProviderInfo(
        id="gemini",
        name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        key_url="https://aistudio.google.com/app/apikey",
        provider_type="cloud",
        default_models=[
            "gemini-2.0-flash",
            "gemini-2.0-pro-exp-02-05",
            "gemini-1.5-pro",
        ],
    ),
    "deepseek": ProviderInfo(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        key_url="https://platform.deepseek.com/api_keys",
        provider_type="cloud",
        default_models=[
            "deepseek-reasoner",
            "deepseek-chat",
        ],
    ),
    "groq": ProviderInfo(
        id="groq",
        name="Groq LPU",
        base_url="https://api.groq.com/openai/v1",
        key_url="https://console.groq.com/keys",
        provider_type="fast_inference",
        default_models=[
            "llama-3.3-70b-versatile",
            "deepseek-r1-distill-llama-70b",
            "llama-3.1-8b-instant",
        ],
    ),
    "cerebras": ProviderInfo(
        id="cerebras",
        name="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        key_url="https://cloud.cerebras.ai",
        provider_type="fast_inference",
        default_models=[
            "llama3.3-70b",
            "llama3.1-8b",
        ],
    ),
    "xai": ProviderInfo(
        id="xai",
        name="xAI (Grok)",
        base_url="https://api.x.ai/v1",
        key_url="https://console.x.ai/",
        provider_type="cloud",
        default_models=[
            "grok-2-1212",
            "grok-2-vision-1212",
        ],
    ),
    "mistral": ProviderInfo(
        id="mistral",
        name="Mistral AI",
        base_url="https://api.mistral.ai/v1",
        key_url="https://console.mistral.ai/api-keys/",
        provider_type="cloud",
        default_models=[
            "mistral-large-latest",
            "codestral-latest",
            "ministral-8b-latest",
        ],
    ),
    "together": ProviderInfo(
        id="together",
        name="Together AI",
        base_url="https://api.together.xyz/v1",
        key_url="https://api.together.ai/settings/api-keys",
        provider_type="fast_inference",
        default_models=[
            "deepseek-ai/DeepSeek-R1",
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "Qwen/Qwen2.5-Coder-32B-Instruct",
        ],
    ),
    "perplexity": ProviderInfo(
        id="perplexity",
        name="Perplexity AI",
        base_url="https://api.perplexity.ai",
        key_url="https://www.perplexity.ai/settings/api",
        provider_type="cloud",
        default_models=[
            "sonar-pro",
            "sonar",
            "sonar-reasoning",
        ],
    ),
    "cohere": ProviderInfo(
        id="cohere",
        name="Cohere",
        base_url="https://api.cohere.com/v2",
        key_url="https://dashboard.cohere.com/api-keys",
        provider_type="cloud",
        default_models=[
            "command-r-plus-08-2024",
            "command-r-08-2024",
        ],
    ),
    "fireworks": ProviderInfo(
        id="fireworks",
        name="Fireworks AI",
        base_url="https://api.fireworks.ai/inference/v1",
        key_url="https://fireworks.ai/api-keys",
        provider_type="fast_inference",
        default_models=[
            "accounts/fireworks/models/deepseek-r1",
            "accounts/fireworks/models/llama-v3p3-70b-instruct",
        ],
    ),
    "sambanova": ProviderInfo(
        id="sambanova",
        name="SambaNova Systems",
        base_url="https://api.sambanova.ai/v1",
        key_url="https://cloud.sambanova.ai/apis",
        provider_type="fast_inference",
        default_models=[
            "Meta-Llama-3.3-70B-Instruct",
            "DeepSeek-R1-Distill-Llama-70B",
        ],
    ),
    "ai21": ProviderInfo(
        id="ai21",
        name="AI21 Labs",
        base_url="https://api.ai21.com/studio/v1",
        key_url="https://studio.ai21.com/account/api-key",
        provider_type="cloud",
        default_models=[
            "jamba-1.5-large",
            "jamba-1.5-mini",
        ],
    ),
    "deepinfra": ProviderInfo(
        id="deepinfra",
        name="DeepInfra",
        base_url="https://api.deepinfra.com/v1/openai",
        key_url="https://deepinfra.com/dash/api_keys",
        provider_type="fast_inference",
        default_models=[
            "deepseek-ai/DeepSeek-R1",
            "meta-llama/Meta-Llama-3.3-70B-Instruct",
        ],
    ),
    "replicate": ProviderInfo(
        id="replicate",
        name="Replicate",
        base_url="https://api.replicate.com/v1",
        key_url="https://replicate.com/account/api-tokens",
        provider_type="cloud",
        default_models=[
            "meta/meta-llama-3.3-70b-instruct",
            "deepseek-ai/deepseek-r1",
        ],
    ),
    "cloudflare": ProviderInfo(
        id="cloudflare",
        name="Cloudflare Workers AI",
        base_url="https://api.cloudflare.com/client/v4/accounts",
        key_url="https://dash.cloudflare.com/profile/api-tokens",
        provider_type="cloud",
        default_models=[
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
        ],
    ),
    "huggingface": ProviderInfo(
        id="huggingface",
        name="Hugging Face Inference",
        base_url="https://api-inference.huggingface.co/v1",
        key_url="https://huggingface.co/settings/tokens",
        provider_type="cloud",
        default_models=[
            "meta-llama/Llama-3.3-70B-Instruct",
            "deepseek-ai/DeepSeek-R1",
        ],
    ),
    "ollama": ProviderInfo(
        id="ollama",
        name="Ollama (Local)",
        base_url="http://localhost:11434",
        key_url="https://ollama.com",
        provider_type="local",
        default_models=["llama3.3:latest", "deepseek-r1:latest"],
        models_endpoint="/api/tags",
    ),
    "lmstudio": ProviderInfo(
        id="lmstudio",
        name="LM Studio (Local)",
        base_url="http://localhost:1234/v1",
        key_url="https://lmstudio.ai",
        provider_type="local",
        default_models=[],
    ),
    "vllm": ProviderInfo(
        id="vllm",
        name="vLLM Server",
        base_url="http://localhost:8000/v1",
        key_url="",
        provider_type="local",
        default_models=[],
    ),
}


def fetch_remote_models(provider_id: str, api_key: str = "") -> List[Dict[str, Any]]:
    """Asynchronously fetches models for a given provider from live API data."""
    info = PROVIDERS_CATALOG.get(provider_id)
    if not info:
        return []

    if provider_id == "local_gguf":
        from .settings import get_settings
        models_dir = get_settings().config.get("models_dir", "models")
        candidates = [
            models_dir,
            os.path.join(os.getcwd(), models_dir),
            "D:/CMDAI CODE/models",
            "D:\\CMDAI CODE\\models",
            "E:/CMDAI CODE/models",
            "E:\\CMDAI CODE\\models",
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models"),
        ]
        found_dir = None
        for cand in candidates:
            if cand and os.path.exists(cand) and os.path.isdir(cand):
                try:
                    if any(f.endswith(".gguf") for f in os.listdir(cand)):
                        found_dir = cand
                        break
                except Exception:
                    pass
        if not found_dir:
            for cand in candidates:
                if cand and os.path.exists(cand) and os.path.isdir(cand):
                    found_dir = cand
                    break
        if not found_dir:
            return []
        models_dir = found_dir

        found = []
        for f in sorted(os.listdir(models_dir)):
            if f.endswith(".gguf"):
                path = os.path.join(models_dir, f)
                size_mb = os.path.getsize(path) / (1024 * 1024)
                size_str = f"{size_mb / 1024:.1f}GB" if size_mb > 1024 else f"{size_mb:.0f}MB"
                found.append({
                    "id": f,
                    "name": f,
                    "badge": size_str,
                    "thinking": "r1" in f.lower() or "qwq" in f.lower() or "thinking" in f.lower(),
                })
        return found

    if not api_key and provider_id not in ("opencode", "opencode_zen", "openrouter", "ollama", "lmstudio", "vllm", "local_gguf"):
        return []

    url = f"{info.base_url}{info.models_endpoint}"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CMDAI-CODE/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key

    try:
        resp = requests.get(url, headers=headers, timeout=6.0)
        if resp.status_code == 200:
            data = resp.json()

            if "models" in data and isinstance(data["models"], list):
                return [
                    {
                        "id": m.get("name", m.get("model", "")),
                        "name": m.get("name", m.get("model", "")),
                        "badge": f"{(m.get('size', 0) / (1024**3)):.1f}GB" if m.get("size") else "ollama",
                        "thinking": "<think>" in str(m.get("template", "")).lower() or "r1" in str(m.get("name", "")).lower(),
                    }
                    for m in data["models"]
                ]

            if "data" in data and isinstance(data["data"], list):
                raw_models = data["data"]

                if provider_id == "openrouter":
                    raw_models.sort(key=lambda x: x.get("created", 0), reverse=True)

                res = []
                for item in raw_models:
                    if not isinstance(item, dict):
                        continue
                    mid = item.get("id", "")
                    if not mid:
                        continue
                    name = item.get("name", mid)

                    params = item.get("supported_parameters", [])
                    has_reasoning = (
                        "reasoning" in params
                        or "include_reasoning" in params
                        or bool(item.get("reasoning"))
                        or any(kw in mid.lower() for kw in ["r1", "o1", "o3", "o4", "claude-3-7", "claude-3.7"])
                    )

                    ctx_len = item.get("context_length", 0)
                    ctx_str = ""
                    if ctx_len >= 1_000_000:
                        ctx_str = f"{ctx_len // 1_000_000}M"
                    elif ctx_len >= 1_000:
                        ctx_str = f"{ctx_len // 1_000}k"

                    badge_parts = []
                    if ctx_str:
                        badge_parts.append(ctx_str)
                    if has_reasoning:
                        badge_parts.append("reasoning")

                    if ctx_len > 0:
                        from .resource_limits import save_model_metadata
                        save_model_metadata(mid, provider_id, ctx_len)

                    res.append({
                        "id": mid,
                        "name": name,
                        "badge": " · ".join(badge_parts) if badge_parts else "",
                        "thinking": has_reasoning,
                        "context_length": ctx_len,
                    })
                return res

    except Exception:
        pass

    return []


def stream_chat_completion(
    provider_id: str,
    model_id: str,
    messages: List[Dict[str, str]],
    api_key: str = "",
    generation_params: Optional[Dict[str, Any]] = None,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    is_aborted: Optional[Callable[[], bool]] = None,
) -> Tuple[str, str]:
    """Streams chat completions with transparent separation of thinking and response tokens.
    
    Returns:
        (full_response, full_thinking)
    """
    params = generation_params or {}
    cap = get_model_capability(model_id, provider_id=provider_id, api_key=api_key)
    reasoning_level = params.get("reasoning_level", "Medium")

    info = PROVIDERS_CATALOG.get(provider_id)
    if not info:
        return f"Unknown provider: {provider_id}", ""

    full_text = []
    full_thinking = []
    is_in_thinking = False

    url = f"{info.base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CMDAI-CODE/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key

    payload: Dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "temperature": params.get("temperature", 0.6),
        "max_tokens": params.get("max_tokens", 4096),
        "stream": True,
    }

    if cap.thinking and reasoning_level != "Off":
        if cap.param_style == "reasoning_effort":
            effort = REASONING_EFFORT_MAP.get(reasoning_level, "medium")
            payload["reasoning_effort"] = effort
        elif cap.param_style == "budget_tokens":
            budget = BUDGET_MAP.get(reasoning_level, 8192)
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}

    try:
        resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60.0)
        if resp.status_code != 200:
            err_msg = f"API Error [{resp.status_code}]: {resp.text}"
            if on_token:
                on_token(err_msg)
            return err_msg, ""

        for line in resp.iter_lines():
            if is_aborted and is_aborted():
                break
            if not line:
                continue
            line_str = line.decode("utf-8")
            if not line_str.startswith("data: "):
                continue
            data_str = line_str[6:].strip()
            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})

                reasoning_delta = delta.get("reasoning_content")
                if reasoning_delta:
                    full_thinking.append(reasoning_delta)
                    if on_thinking:
                        on_thinking(reasoning_delta)
                    continue

                content_delta = delta.get("content", "")
                if not content_delta:
                    continue

                if "<think>" in content_delta:
                    parts = content_delta.split("<think>", 1)
                    if parts[0] and on_token:
                        full_text.append(parts[0])
                        on_token(parts[0])
                    is_in_thinking = True
                    remainder = parts[1]
                    if "</think>" in remainder:
                        th_parts = remainder.split("</think>", 1)
                        full_thinking.append(th_parts[0])
                        if on_thinking:
                            on_thinking(th_parts[0])
                        is_in_thinking = False
                        if th_parts[1] and on_token:
                            full_text.append(th_parts[1])
                            on_token(th_parts[1])
                    else:
                        full_thinking.append(remainder)
                        if on_thinking:
                            on_thinking(remainder)
                    continue

                if is_in_thinking:
                    if "</think>" in content_delta:
                        parts = content_delta.split("</think>", 1)
                        full_thinking.append(parts[0])
                        if on_thinking:
                            on_thinking(parts[0])
                        is_in_thinking = False
                        if parts[1] and on_token:
                            full_text.append(parts[1])
                            on_token(parts[1])
                    else:
                        full_thinking.append(content_delta)
                        if on_thinking:
                            on_thinking(content_delta)
                    continue

                full_text.append(content_delta)
                if on_token:
                    on_token(content_delta)
                if TOOL_COMPLETE_PATTERNS.search("".join(full_text)):
                    break

            except Exception:
                continue

    except Exception as e:
        err_msg = f"Connection error: {e}"
        if on_token:
            on_token(err_msg)
        return err_msg, "".join(full_thinking)

    return "".join(full_text), "".join(full_thinking)
