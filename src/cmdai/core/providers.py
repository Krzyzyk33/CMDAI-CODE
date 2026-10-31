import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

import requests
from .capabilities import (
    THINKING_OFF,
    budget_for,
    clamp_effort,
    effort_budget_field,
    get_model_capability,
    normalize_level,
    note_reasoning_observed,
)

TOOL_COMPLETE_PATTERNS = re.compile(
    r'(?:'
    r'<tool:(?!write\b|edit\b|command\b)\w+[^>]*?/>'
    r'|<tool:(?:write|edit|command)\b[^>]*?\b(?:content|code|new|cmd|command)=[^>]*?/>'
    r'|</(?:tool:)?\w+>'
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
    needs_custom_auth: bool = False
    auth_hint: str = ""


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
            "nemotron-3-ultra-free",
            "nemotron-3.5-lightning-free",
            "ling-3.0-flash-fin-free",
            "jev-1.13-free",
            "muse-spark-1.3-contributor-free",
            "qwen3.6-plus-free",
            "minimax-m3-free",
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
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "qwen/qwen-2.5-coder-32b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
            "anthropic/claude-3.7-sonnet",
            "openai/o3-mini",
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
        default_models=[],
        supports_remote_fetch=False,
    ),
    "ollama": ProviderInfo(
        id="ollama",
        name="Ollama (Local)",
        base_url="http://localhost:11434",
        key_url="https://ollama.com",
        provider_type="local",
        default_models=[],
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
    "zai": ProviderInfo(
        id="zai",
        name="Z.AI (Zhipu)",
        base_url="https://api.z.ai/api/paas/v4",
        key_url="https://z.ai/manage-apikey/apikey-list",
        provider_type="cloud",
        default_models=[
            "glm-4.6",
            "glm-4.5",
            "glm-4.5-air",
        ],
    ),
    "github_models": ProviderInfo(
        id="github_models",
        name="GitHub Models",
        base_url="https://models.github.ai/inference",
        key_url="https://github.com/settings/tokens",
        provider_type="cloud",
        default_models=[
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "meta/Llama-3.3-70B-Instruct",
            "deepseek/DeepSeek-R1",
        ],
    ),
    "moonshot": ProviderInfo(
        id="moonshot",
        name="Moonshot AI",
        base_url="https://api.moonshot.ai/v1",
        key_url="https://platform.moonshot.ai/console/api-keys",
        provider_type="cloud",
        default_models=[
            "kimi-k2-0711-preview",
            "moonshot-v1-8k",
            "moonshot-v1-32k",
        ],
    ),
    "nvidia": ProviderInfo(
        id="nvidia",
        name="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        key_url="https://build.nvidia.com",
        provider_type="cloud",
        default_models=[
            "meta/llama-3.3-70b-instruct",
            "deepseek-ai/deepseek-r1",
            "qwen/qwen2.5-coder-32b-instruct",
        ],
    ),
    "nebius": ProviderInfo(
        id="nebius",
        name="Nebius AI Studio",
        base_url="https://api.studio.nebius.com/v1",
        key_url="https://studio.nebius.com/settings/api-keys",
        provider_type="cloud",
        default_models=[
            "meta-llama/Meta-Llama-3.3-70B-Instruct",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-Coder-32B-Instruct",
        ],
    ),
    "minimax": ProviderInfo(
        id="minimax",
        name="MiniMax",
        base_url="https://api.minimax.io/v1",
        key_url="https://platform.minimax.io",
        provider_type="cloud",
        default_models=[
            "MiniMax-M2",
            "MiniMax-Text-01",
        ],
    ),
    "bedrock": ProviderInfo(
        id="bedrock",
        name="AWS Bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        key_url="https://console.aws.amazon.com/bedrock",
        provider_type="cloud",
        default_models=[
            "anthropic.claude-3-7-sonnet-20250219-v1:0",
            "meta.llama3-3-70b-instruct-v1:0",
        ],
        supports_remote_fetch=False,
        needs_custom_auth=True,
        auth_hint="Bedrock uses AWS SigV4 signing, not a Bearer key. Configure AWS credentials (aws configure) and use an AWS SDK proxy or the 'aws bedrock-runtime' CLI.",
    ),
    "vertex": ProviderInfo(
        id="vertex",
        name="Google Vertex AI",
        base_url="https://us-central1-aiplatform.googleapis.com",
        key_url="https://console.cloud.google.com/vertex-ai",
        provider_type="cloud",
        default_models=[
            "gemini-2.0-flash-001",
            "claude-3-7-sonnet@20250219",
        ],
        supports_remote_fetch=False,
        needs_custom_auth=True,
        auth_hint="Vertex AI needs a GCP access token (gcloud auth print-access-token), not a plain API key. Use the Gemini provider entry for key-based access.",
    ),
    "copilot": ProviderInfo(
        id="copilot",
        name="GitHub Copilot",
        base_url="https://api.githubcopilot.com",
        key_url="https://github.com/settings/copilot",
        provider_type="cloud",
        default_models=[
            "gpt-4o",
            "claude-3.7-sonnet",
        ],
        supports_remote_fetch=False,
        needs_custom_auth=True,
        auth_hint="Copilot needs a GitHub OAuth device-flow token, not a classic PAT. Sign in via the official Copilot CLI/extension to obtain one.",
    ),
}


def catalog_models(provider_id: str) -> List[Dict[str, Any]]:
    """Every model models.dev lists for this provider.

    For the providers whose own `/models` returns a bare `id` - or returns
    nothing at all, as `anthropic` does with `supports_remote_fetch=False` -
    this is the only list there is. It says what exists; the provider's own
    endpoint says what *this account* may reach, and the two are not the same
    question.
    """
    try:
        from . import model_catalog as MC
    except Exception:
        return []

    their_id = MC.catalog_provider_id(provider_id)
    if not their_id:
        return []
    provider = MC.load().get(their_id)
    if not isinstance(provider, dict):
        return []
    models = provider.get("models")
    if not isinstance(models, dict):
        return []

    out: List[Dict[str, Any]] = []
    for model_id in models:
        cap = get_model_capability(model_id, provider_id=provider_id)
        badge = []
        if cap.thinking:
            badge.append("reasoning")
        out.append({
            "id": model_id,
            "name": model_id,
            "badge": " · ".join(badge),
            "thinking": cap.thinking,
            "levels": list(cap.levels),
            "param_style": cap.param_style,
            "from_catalog": True,
        })
    out.sort(key=lambda m: (0 if m["thinking"] else 1, str(m["id"]).lower()))
    return out


def has_reasoning(provider_id: str, model_id: str) -> Optional[bool]:
    """Whether a model reasons, or None when nobody will say.

    None is the load-bearing value. Roughly 26 providers publish nothing about
    reasoning, and reporting False for those would be a confident wrong answer
    where the honest one is "unknown" - which is how a model that thinks on
    most turns ends up with no thinking level and no label.
    """
    cap = get_model_capability(model_id, provider_id=provider_id)
    if cap.source == "name_heuristic":
        return None
    return bool(cap.thinking)


def _merge_catalog_facts(provider_id: str, models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach what models.dev knows to a list the provider just returned.

    The provider decides which models exist for this account. models.dev decides
    what each one can do. Neither overrides the other: a model the provider did
    not list stays off the list, and a model it did list is never dropped for
    being unknown to the catalogue.
    """
    if not models:
        return models
    out = []
    for m in models:
        mid = str(m.get("id") or m.get("name") or "")
        if not mid:
            continue
        entry = dict(m)
        cap = get_model_capability(mid, provider_id=provider_id)
        entry["thinking"] = bool(cap.thinking)
        entry["levels"] = list(cap.levels)
        entry["param_style"] = cap.param_style
        if cap.vision:
            entry["vision"] = True
        if not m.get("badge") or "reasoning" not in str(m.get("badge")):
            badge = str(m.get("badge") or "")
            entry["badge"] = (badge + " · reasoning") if badge else "reasoning"
        out.append(entry)
    return out


def fetch_remote_models(provider_id: str, api_key: str = "") -> List[Dict[str, Any]]:
    if provider_id == "opencode_zen":
        provider_id = "opencode"
    info = PROVIDERS_CATALOG.get(provider_id)
    if not info:
        return []

    if provider_id == "local_gguf":
        from .model_paths import list_gguf_files, resolve_models_dir
        # Was six candidates, four of them hardcoded D:/E: paths under a
        # "CMDAI CODE" name that no longer exists.
        models_dir = resolve_models_dir("")
        if not os.path.isdir(models_dir):
            return []

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
                })
        # `thinking` was three substrings against the filename. It is now read
        # out of the file's own chat template, which is the only thing that knows
        # - and which was unreachable behind a hard-coded `gemma` refusal.
        return _merge_catalog_facts(provider_id, found)

    if not api_key and provider_id not in ("openrouter", "ollama", "lmstudio", "vllm", "local_gguf"):
        # No key, so the provider will not tell us anything. The catalogue still
        # can, and a card with a real list beats a card that says "key required"
        # while the list is sitting there.
        return catalog_models(provider_id)

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
                # Ollama. The template is not in this response - it comes from
                # /api/show per model - so nothing here may be read as an answer
                # about reasoning. Was `"<think>" in template` against a field
                # this endpoint does not return, which is why it always fell
                # through to the model name.
                listed = [
                    {
                        "id": m.get("name", m.get("model", "")),
                        "name": m.get("name", m.get("model", "")),
                        "badge": f"{(m.get('size', 0) / (1024**3)):.1f}GB" if m.get("size") else "ollama",
                    }
                    for m in data["models"]
                ]
                return _merge_catalog_facts(provider_id, listed)

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

                    ctx_len = item.get("context_length", 0)

                    # The provider's own answer first. `supported_parameters`
                    # naming `reasoning` is better than anything we could look
                    # up. The name keywords that used to close this expression
                    # are gone: they were a guess dressed as a check, they missed
                    # `gemini` and `qwen3`, and they were the reason a card and
                    # the input bar could disagree about the same model.
                    params = item.get("supported_parameters", [])
                    reported = (
                        "reasoning" in params
                        or "include_reasoning" in params
                        or bool(item.get("reasoning"))
                    )
                    if reported:
                        from .capabilities import register_model_capability
                        register_model_capability(
                            mid, provider_id=provider_id, thinking=True,
                            param_style="reasoning_effort",
                        )
                    cap = get_model_capability(mid, provider_id=provider_id)

                    ctx_str = ""
                    if ctx_len >= 1_000_000:
                        ctx_str = f"{ctx_len // 1_000_000}M"
                    elif ctx_len >= 1_000:
                        ctx_str = f"{ctx_len // 1_000}k"

                    is_free = "free" in mid.lower() or ":free" in mid.lower() or "free" in name.lower()
                    badge_parts = []
                    if is_free:
                        badge_parts.append("FREE")
                    if ctx_str:
                        badge_parts.append(ctx_str)
                    if cap.thinking:
                        badge_parts.append("reasoning")

                    if ctx_len > 0:
                        from .resource_limits import save_model_metadata
                        save_model_metadata(mid, provider_id, ctx_len)

                    res.append({
                        "id": mid,
                        "name": name,
                        "badge": " · ".join(badge_parts) if badge_parts else "",
                        "thinking": cap.thinking,
                        "levels": list(cap.levels),
                        "param_style": cap.param_style,
                        "context_length": ctx_len,
                    })

                def _is_free_item(m_item: Dict[str, Any]) -> bool:
                    m_id = str(m_item.get("id") or "").lower()
                    m_name = str(m_item.get("name") or "").lower()
                    m_badge = str(m_item.get("badge") or "").lower()
                    return "free" in m_id or ":free" in m_id or "free" in m_name or "free" in m_badge

                res.sort(key=lambda m: (0 if _is_free_item(m) else 1, str(m.get("id") or m.get("name") or "")))
                return res

    except Exception:
        pass

    # The provider did not answer - no key, a 403, a timeout, or it simply does
    # not publish a list. The catalogue still does, so the card gets a real list
    # marked as catalogue-derived rather than an empty one that looks like the
    # account has nothing.
    return catalog_models(provider_id)


class StreamResult(tuple):
    """2-tuple of (content, thinking) that also carries usage metadata as .usage."""
    def __new__(cls, content: str, thinking: str, usage: Optional[Dict[str, Any]] = None):
        obj = super().__new__(cls, (content, thinking))
        obj.content = content
        obj.thinking = thinking
        obj.usage = usage or {}
        return obj


def stream_chat_completion(
    provider_id: str,
    model_id: str,
    messages: List[Dict[str, str]],
    api_key: str = "",
    generation_params: Optional[Dict[str, Any]] = None,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    is_aborted: Optional[Callable[[], bool]] = None,
    on_tool: Optional[Callable[[str, str, str], None]] = None,
    on_usage: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[str, str]:
    """One turn against one provider.

    `on_tool(name, phase, detail)` is how a provider that *executes its own
    tools* reports them. `phase` is one of:

        ``"running"``  the provider is running the tool; `detail` is the
                       target - a path, a command, whatever it is acting on
        ``"done"``     it finished; `detail` is a short result summary
        ``"failed"``   it did not finish; `detail` is the error

    Optional, and every provider but a plugin-supplied one leaves it None: the
    shipped providers ask the model for a `<tool:name>` tag in its text and this
    app executes that itself, so there is nothing to report. It exists for a
    provider that runs tools server-side, where the tool call happens without
    the text ever naming it.
    """
    params = generation_params or {}
    cap = get_model_capability(model_id, provider_id=provider_id, api_key=api_key)
    # Normalised at the only place it is read, so a level can arrive from a
    # config file, a plugin or a legacy caller in any spelling and behave the
    # same. Three cases, kept apart on purpose:
    #   * nothing given -> whatever detection said this model defaults to
    #   * given as off    -> reasoning stays off, in any spelling
    #   * given as a level-> that level
    raw_level = params.get("reasoning_level")
    level = normalize_level(raw_level) if raw_level not in (None, "") else ""
    if not level:
        level = normalize_level(cap.default_level) or "medium"
    reasoning_level = level

    if provider_id in ("opencode", "opencode_zen", "opencode_local", "local_opencode") and not api_key:
        try:
            from cmdai_opencode import provider as plugin_provider
            return plugin_provider.stream_completion(
                model_id,
                messages,
                generation_params=generation_params,
                on_token=on_token,
                on_thinking=on_thinking,
                is_aborted=is_aborted,
                on_tool=on_tool,
                on_usage=on_usage,
            )
        except Exception as e:
            err_msg = f"⚠ OpenCode Local Error: {e}"
            if on_token:
                on_token(err_msg)
            return err_msg, ""

    if provider_id == "opencode_zen":
        provider_id = "opencode"
    info = PROVIDERS_CATALOG.get(provider_id)
    if not info:
        return f"Unknown provider: {provider_id}", ""

    if provider_id not in ("local_gguf", "ollama", "lmstudio", "vllm", "opencode_local", "local_opencode") and not api_key:
        err_msg = f"API key required for {info.name}. Please configure your API key with Ctrl+D in /models or in config.json."
        if on_token:
            on_token(err_msg)
        return err_msg, ""

    if info.needs_custom_auth:
        err_msg = (f"{info.name} needs special authentication, plain API key is not enough.\n"
                   f"{info.auth_hint or 'See provider docs for setup.'}")
        if on_token:
            on_token(err_msg)
        return err_msg, ""

    full_text = []
    full_thinking = []
    is_in_thinking = False
    # Detection by doing, not by guessing from the name. A model that streams
    # a reasoning channel has just proved it reasons, which is the one fact no
    # heuristic can supply - and it repairs a cached "no reasoning" verdict.
    observed_reasoning = [False]

    def _mark_reasoning_observed() -> None:
        if observed_reasoning[0]:
            return
        observed_reasoning[0] = True
        try:
            note_reasoning_observed(model_id, provider_id)
        except Exception:
            pass

    url = f"{info.base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CMDAI-CODE/1.0",
    }
    if provider_id in ("opencode", "opencode_zen"):
        session_uid = str(uuid.uuid4())
        headers["User-Agent"] = "opencode/1.1.28"
        headers["x-opencode-session"] = session_uid
        headers["x-opencode-request"] = str(uuid.uuid4())
        headers["x-opencode-project"] = "default"

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key

    clean_messages = []
    for m in messages:
        if isinstance(m, dict):
            cm = {"role": m.get("role", "user"), "content": m.get("content", "")}
            if "name" in m:
                cm["name"] = m["name"]
            clean_messages.append(cm)
        else:
            clean_messages.append(m)

    payload: Dict[str, Any] = {
        "model": model_id,
        "messages": clean_messages,
        "temperature": params.get("temperature", 0.3),
        "stream": True,
    }
    # Request token usage statistics in stream chunks (supported by OpenAI, OpenRouter, Groq, Ollama, DeepSeek, etc.)
    if provider_id not in ("anthropic",):
        payload["stream_options"] = {"include_usage": True}

    mt = params.get("max_tokens", 0)
    if mt and int(mt) > 0:
        payload["max_tokens"] = int(mt)

    level = normalize_level(reasoning_level)
    if cap.thinking and level and level != THINKING_OFF:
        # A local model answers to its own window; everything else to the fixed
        # ladder. Both come through one call so no call site can pick the wrong
        # scale for the provider it happens to be serving.
        budget = budget_for(provider_id, level, cap.context_limit)

        # The budget the user picked is only ever larger than the output we
        # asked for if we let it be. `max` is 128k, and a 4k response cannot
        # spend 128k of thinking - the provider rejects the pair rather than
        # silently truncating, so the request has to fit.
        if mt and int(mt) > 0:
            budget = min(budget, max(1024, int(int(mt) * 0.8)))

        if cap.param_style == "reasoning_effort":
            # `extra` and `max` are our vocabulary, not the API's: it tops out at
            # `high`, and sending them through is a 400.
            payload["reasoning_effort"] = clamp_effort(level)
            # The name alone would throw the number away. Where the provider has
            # a field for a budget, send it, so the level means what the bar says
            # it means. Everywhere else it stays ours, and the UI admits it.
            field = effort_budget_field(provider_id)
            if field == "__nested_reasoning__":
                payload["reasoning"] = {"effort": clamp_effort(level),
                                        "max_tokens": budget}
            elif field:
                payload[field] = budget
        elif cap.param_style == "budget_tokens":
            # A numeric ladder, so the top rungs are real here.
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        elif cap.param_style == "chat_template_kwargs":
            payload["chat_template_kwargs"] = {
                "enable_thinking": True,
                "thinking_budget": budget,
            }
        # `param_style == "stream_only"` falls through on purpose: the model
        # reasons on its own and there is no parameter to configure.

    try:
        resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60.0)
        if resp.status_code in (400, 422) and any(k in payload for k in ("stream_options", "thinking", "reasoning_effort", "reasoning", "chat_template_kwargs")):
            # Some endpoints reject stream_options or custom reasoning fields; retry cleanly
            payload.pop("stream_options", None)
            payload.pop("thinking", None)
            payload.pop("reasoning_effort", None)
            payload.pop("reasoning", None)
            payload.pop("chat_template_kwargs", None)
            resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60.0)
        if resp.status_code != 200:
            err_text = resp.text
            clean_err = ""
            try:
                err_json = resp.json()
                if isinstance(err_json, dict):
                    err_obj = err_json.get("error")
                    if isinstance(err_obj, dict):
                        clean_err = err_obj.get("message") or str(err_obj)
                    elif isinstance(err_obj, str):
                        clean_err = err_obj
                    elif "message" in err_json:
                        clean_err = err_json["message"]
            except Exception:
                clean_err = err_text

            if provider_id in ("opencode", "opencode_zen"):
                if not api_key:
                    err_msg = (
                        f"⚠ OpenCode Zen Error [{resp.status_code}]: {clean_err or 'Access restricted'}\n\n"
                        f"The OpenCode Zen gateway requires an API key for third-party access.\n"
                        f"• Get your API key at: https://opencode.ai/zen\n"
                        f"• Configure it in /models (Ctrl+D) or in config.json\n"
                        f"• Or use local offline models (local_gguf) or free models on OpenRouter."
                    )
                else:
                    err_msg = (
                        f"⚠ OpenCode Zen Error [{resp.status_code}]: {clean_err or err_text}\n\n"
                        f"Model '{model_id}' is temporarily down upstream or your key cannot access it. "
                        f"Try selecting another model in /models or verify your key at https://opencode.ai/zen."
                    )
            elif provider_id == "openrouter" and resp.status_code == 401:
                err_msg = (
                    f"⚠ OpenRouter Error [{resp.status_code}]: {clean_err or 'Authentication required'}\n\n"
                    f"Free models on OpenRouter require an OpenRouter account API key:\n"
                    f"• Create a free key at: https://openrouter.ai/keys\n"
                    f"• Configure it in /models (Ctrl+D) or config.json\n"
                    f"• Or use local offline models from local_gguf."
                )
            else:
                err_msg = f"⚠ Provider Error [{resp.status_code}]: {clean_err or err_text}"

            if on_token:
                on_token(err_msg)
            return err_msg, ""

        captured_usage: Dict[str, Any] = {}

        for line in resp.iter_lines():
            if is_aborted and is_aborted():
                break
            if not line:
                continue
            try:
                line_str = line.decode("utf-8", errors="replace")
            except Exception:
                continue
            if not line_str.startswith("data: "):
                continue
            data_str = line_str[6:].strip()
            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)

                # 1. Capture usage metadata if provided by endpoint (OpenAI, OpenRouter, Groq, Ollama, etc.)
                raw_usage = chunk.get("usage") or chunk.get("x_groq", {}).get("usage")
                if not raw_usage and ("prompt_eval_count" in chunk or "eval_count" in chunk):
                    raw_usage = {
                        "prompt_tokens": chunk.get("prompt_eval_count", 0),
                        "completion_tokens": chunk.get("eval_count", 0),
                        "total_tokens": (chunk.get("prompt_eval_count", 0) or 0) + (chunk.get("eval_count", 0) or 0),
                    }
                if not raw_usage:
                    msg_obj = chunk.get("message") or {}
                    if isinstance(msg_obj, dict) and "usage" in msg_obj:
                        raw_usage = msg_obj["usage"]
                    elif chunk.get("type") == "message_delta" and "usage" in chunk:
                        raw_usage = chunk["usage"]

                if raw_usage and isinstance(raw_usage, dict):
                    p_tok = raw_usage.get("prompt_tokens") or raw_usage.get("input_tokens")
                    c_tok = raw_usage.get("completion_tokens") or raw_usage.get("output_tokens")
                    t_tok = raw_usage.get("total_tokens")
                    if p_tok is not None:
                        captured_usage["prompt_tokens"] = int(p_tok)
                    if c_tok is not None:
                        captured_usage["completion_tokens"] = int(c_tok)
                    if t_tok is not None:
                        captured_usage["total_tokens"] = int(t_tok)
                    elif p_tok is not None and c_tok is not None:
                        captured_usage["total_tokens"] = int(p_tok) + int(c_tok)

                delta = {}
                choices = chunk.get("choices")
                if choices and isinstance(choices, list) and len(choices) > 0:
                    first_choice = choices[0]
                    if isinstance(first_choice, dict):
                        delta = first_choice.get("delta") or {}
                        if not delta and "text" in first_choice:
                            delta = {"content": first_choice.get("text", "")}
                elif "delta" in chunk and isinstance(chunk["delta"], dict):
                    delta = chunk["delta"]
                elif "message" in chunk and isinstance(chunk["message"], dict):
                    delta = chunk["message"]
                elif "response" in chunk and isinstance(chunk["response"], str):
                    delta = {"content": chunk["response"]}
                elif not raw_usage:
                    continue

                reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking") or ""
                if reasoning_delta:
                    _mark_reasoning_observed()
                    full_thinking.append(reasoning_delta)
                    if on_thinking:
                        on_thinking(reasoning_delta)
                    continue

                content_delta = delta.get("content", "") or delta.get("text", "") or ""
                if not content_delta:
                    continue

                if "<think>" in content_delta:
                    _mark_reasoning_observed()
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

        # If API did not return usage, perform accurate fallback estimation
        if not captured_usage or not captured_usage.get("completion_tokens"):
            from .resource_limits import estimate_token_count
            p_tok = captured_usage.get("prompt_tokens") or estimate_token_count(messages)
            full_out = "".join(full_text) + "".join(full_thinking)
            c_tok = estimate_token_count([{"content": full_out}])
            captured_usage["prompt_tokens"] = p_tok
            captured_usage["completion_tokens"] = c_tok
            captured_usage["total_tokens"] = p_tok + c_tok
            captured_usage["estimated"] = True

        if on_usage:
            try:
                on_usage(captured_usage)
            except Exception:
                pass

    except Exception as e:
        err_msg = f"Connection error: {e}"
        if on_token:
            on_token(err_msg)
        return StreamResult(err_msg, "".join(full_thinking), captured_usage)

    return StreamResult("".join(full_text), "".join(full_thinking), captured_usage)
