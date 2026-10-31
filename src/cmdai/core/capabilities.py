import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

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
    vision: bool = False
    image_param_style: str = "none"                                       
    mmproj_path: Optional[str] = None
    confidence: str = "high"
    # Where this answer came from. A name guess is not worth keeping once a
    # better source exists, so the cache only persists the durable ones.
    source: str = "unknown"
    # The model's own window, when something reported one. A local model's
    # thinking budget is a share of this rather than a fixed number, so the
    # request path needs it without re-reading the file.
    context_limit: int = 0


# --------------------------------------------------------------- levels

# The one vocabulary. Providers spell their own levels differently - OpenCode
# reports `xhigh`, APIs accept `high`, older CMDAI code used `xHigh` and two
# pseudo-levels (`On`, `On (Reasoning)`) that were really just boolean
# switches. Everything funnels through `normalize_levels` now, so no call site
# builds a list by hand.
THINKING_OFF = "off"

# The one vocabulary. Providers spell their own levels differently - OpenCode
# reports `xhigh`, APIs accept `high`, older CMDAI code used `xHigh` and two
# pseudo-levels (`On`, `On (Reasoning)`) that were really just boolean
# switches. Everything funnels through `normalize_levels` now, so no call site
# builds a list by hand.
#
# Six rungs, strongest first. `minimal` was folded into `low` until it was
# checked what the APIs actually accept: OpenAI's documented effort values are
# `none, minimal, low, medium, high, xhigh, max` - seven, of which `none` is the
# disabled state we already call `off`. Collapsing `minimal` into `low` meant
# sending a value the provider had told us about, as a value it had not.
THINKING_LEVELS: Tuple[str, ...] = ("max", "extra", "high", "medium", "low", "minimal")
LEVEL_INTENSITY: Dict[str, int] = {name: i for i, name in enumerate(THINKING_LEVELS)}

# Aliases seen in the wild, mapped onto the vocabulary above.
_LEVEL_ALIASES = {
    "off": THINKING_OFF,
    "none": THINKING_OFF,
    "on": "medium",          # a boolean switch, not an effort level
    "on (reasoning)": "medium",
    "default": "medium",
    "minimal": "minimal",
    "xhigh": "extra",
    "x_high": "extra",
    "xhigh ": "extra",
    "very high": "extra",
    "max_": "max",
    "o1": "high",
}

# Sentinel names that providers used to publish as levels. They are not effort
# levels: `off` is the disabled state and `on` is a boolean switch, so both are
# dropped and the caller keeps only a real ladder.
_NOT_A_LEVEL = frozenset({THINKING_OFF})


def normalize_level(value: Any) -> str:
    """One level name -> the canonical spelling, or "" when unrecognised."""
    key = str(value or "").strip().lower()
    if not key:
        return ""
    if key in LEVEL_INTENSITY:
        return key
    return _LEVEL_ALIASES.get(key, "")


def normalize_levels(levels: Any) -> List[str]:
    """Any provider's level list -> canonical names, weak to strong, no duplicates.

    Unknown names are dropped rather than passed through, so a model can never
    end up offering a level the rest of the app does not understand. An empty
    result means the model has no effort ladder to offer - which, combined with
    `thinking=False`, is how the UI knows to stay out of the way.
    """
    if not isinstance(levels, (list, tuple)):
        return []
    out: List[str] = []
    for raw in levels:
        name = normalize_level(raw)
        if name and name not in out and name not in _NOT_A_LEVEL:
            out.append(name)
    out.sort(key=lambda n: -LEVEL_INTENSITY.get(n, 0))
    return out


DEFAULT_LEVELS: List[str] = ["low", "medium", "high"]


def cycle_order(levels: Any) -> List[str]:
    """The levels Ctrl+T walks, weakest first.

    There is deliberately no `off` at the front. Whether a model reasons is a
    property of the model, not a preference the user toggles: a model without
    reasoning support reports no levels at all and the key does nothing, and a
    model with reasoning support always reasons at some level.
    """
    return normalize_levels(levels)


def clamp_effort(level: Any) -> str:
    """Canonical level -> what an OpenAI-compatible API will actually accept.

    `extra` and `max` are vocabulary, not wire values: the API's top setting is
    `high`. Sending them through verbatim is a 400. Anthropic's budget ladder
    is numeric and needs no clamp, so this is only used on the effort path.
    """
    name = normalize_level(level)
    if not name or name == THINKING_OFF:
        return "none"
    if name in ("max", "extra"):
        return "high"
    return name


def to_variant_name(level: Any) -> str:
    """Canonical level -> the spelling a given provider uses for it.

    OpenCode names the same rung `xhigh`, which is where the `extra` name came
    from. Only the OpenCode route needs this today, but keeping the mapping
    here stops the alias table from being copied into a plugin.
    """
    name = normalize_level(level)
    return "xhigh" if name == "extra" else name


BUDGET_MAP = {
    THINKING_OFF: 0,
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "extra": 32768,
    "max": 64000,
}

# The old scale ran 2048 / 8192 / 24576 / 48000 / 64000 - Anthropic's ceiling,
# applied to everything. Every real configuration that sets a budget uses
# 512 / 2048 / 4096 / 8192 and sits at the bottom of it, so `low` was eight
# times coarser than where anyone actually works and four of the five rungs sat
# where nobody would ever put them. Powers of two, anchored low.
#
# These go out on the budget paths - Anthropic's `budget_tokens`, a GGUF's
# `thinking_budget`, a local server's `reasoning_budget_tokens`. On the effort
# path they are what the level *means*, and a number only reaches the wire
# where the provider has a field for one; see `EFFORT_BUDGET_PROVIDERS`.

# Canonical names only. Legacy spellings (`Medium`, `xHigh`, `On`) are not
# accepted here: every read of a level goes through `normalize_level` first, so
# a duplicate entry could only hide a missed normalisation.
REASONING_EFFORT_MAP = {
    THINKING_OFF: "none",
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "extra": "high",
    "max": "high",
}

# Providers that take a token budget *as well as* an effort name, and the field
# each one calls it.
#
# The effort path otherwise throws the number away: `high`, `extra` and `max`
# all go out as the string `high`, so the budget shown in `/thinking` would be a
# number that never left the machine. These are the providers where it can
# actually go.
#
# `None` for everything else is deliberate, not laziness. OpenAI answers 400
# "Unrecognized request argument" for a field it does not know, so a number
# added everywhere would break the strict APIs to improve the local ones.
EFFORT_BUDGET_FIELDS: Dict[str, str] = {
    # llama.cpp reads `--reasoning-budget` off exactly this field, and vLLM and
    # sglang ignore it if they are not configured for it. OpenCode sends it to
    # local servers the same way.
    "local_gguf": "reasoning_budget_tokens",
    "lmstudio": "reasoning_budget_tokens",
    "vllm": "reasoning_budget_tokens",
    "ollama": "reasoning_budget_tokens",
    # OpenRouter documents `reasoning: { effort, max_tokens }` and honours the
    # budget when the upstream model takes one.
    "openrouter": "__nested_reasoning__",
}


def effort_budget_field(provider_id: str) -> str:
    """The wire field for a budget on this provider, or "" for none.

    "" means the number is ours only. The UI says so rather than printing a
    figure that never left the process.
    """
    return EFFORT_BUDGET_FIELDS.get(provider_id or "", "")


# ------------------------------------------------------------ local budgets

# Providers whose thinking budget is a share of the model's own window.
#
# The fixed ladder is an Anthropic-shaped scale applied to everything, and it is
# the wrong shape for a model running on this machine. A 32k context Qwen cannot
# spend 32k of thinking and still have an answer, and `max` at 128k on a local
# server is a request it can only answer by ignoring it. So for these the levels
# are a percentage of the context, and the absolute numbers are derived from it.
LOCAL_BUDGET_PROVIDERS = frozenset({"local_gguf", "lmstudio", "vllm", "ollama"})

# Providers a plugin has declared to be running on this machine, as opposed to
# the four the core ships with. A provider that cannot be reached from a data
# centre should not be on the fixed Anthropic-shaped ladder, and the honest
# place for that fact is the plugin that talks to it - not a fourth hardcoded id
# here that every future plugin would have to come and edit.
_DECLARED_LOCAL_PROVIDERS: set = set()


def is_local_provider(provider_id: str) -> bool:
    """Does this provider serve models from this machine?

    Declared, not inferred. `opencode_local` is a server on localhost by name,
    but the name is a convention and the plugin is the thing that knows.
    """
    pid = provider_id or ""
    return pid in LOCAL_BUDGET_PROVIDERS or pid in _DECLARED_LOCAL_PROVIDERS


def declare_local_provider(provider_id: str) -> None:
    """Called by a provider plugin when it registers its models."""
    if provider_id:
        _DECLARED_LOCAL_PROVIDERS.add(provider_id)

# Share of the context window each rung may think.
#
# The top stops at 75%: a thinking budget is spent out of the same window as the
# answer, so asking for all of it leaves nowhere to put the reply.
#
# The spacing is wider at the bottom than the fixed ladder used, because a local
# model is not working against Anthropic's ceiling - it is working against
# whatever the server was started with. A 32k window has 32k to divide, and 2% of
# that is 655 tokens, which rounds away to nothing.
LOCAL_LEVEL_SHARES: Dict[str, float] = {
    "minimal": 0.05,
    "low": 0.10,
    "medium": 0.20,
    "high": 0.35,
    "extra": 0.55,
    "max": 0.75,
}

# Weakest first, so the ladder can be built upwards and each rung compared with
# the one below it. `THINKING_LEVELS` is the other way round - it is the order
# Ctrl+T walks - and walking that list while enforcing strict growth accepts
# `max` and then rejects everything else for being smaller.
_LOCAL_LADDER_ASCENDING = ("minimal", "low", "medium", "high", "extra", "max")


MAX_REASONING_BUDGET = 64000  # Anthropic Claude & OpenAI API maximum ceiling


def local_budgets(context: int, output_limit: int = 0) -> Dict[str, int]:
    """A budget per level, matching Claude & OpenAI standard rungs, capped safely.

    Thinking budget is an output generation limit, not context window.
    Even for models with large context windows (128k, 1M), budgets are anchored
    to standard OpenAI/Claude tiers (1k, 2k, 8k, 16k, 32k, 64k) and never exceed
    MAX_REASONING_BUDGET (64,000 tokens).
    """
    if context <= 0:
        return {}

    ceiling = MAX_REASONING_BUDGET
    if output_limit > 0:
        ceiling = min(ceiling, output_limit)
    else:
        ceiling = min(ceiling, max(1024, int(context * 0.5)))

    standard_targets = [
        ("minimal", 1024),
        ("low", 2048),
        ("medium", 8192),
        ("high", 16384),
        ("extra", 32768),
        ("max", 64000),
    ]

    step = 1024
    budgets: Dict[str, int] = {}
    previous = 0
    for name, target in standard_targets:
        if ceiling >= target:
            rung = target
        else:
            rung = (ceiling // step) * step
        if rung <= previous:
            continue
        budgets[name] = rung
        previous = rung
    return budgets


def budget_for(provider_id: str, level: str, context: int = 0) -> int:
    """The number that goes on the wire for this level on this provider.

    Local models answer to their own window; everything else to the fixed
    ladder.

    A local model is also capped by that window whatever level was asked for. A
    level can outlive the ladder that produced it - the config says `medium`,
    the model is reloaded with a smaller window, and the fixed ladder's 8,192 is
    then twice the whole budget - and a picker that prints `medium 8,192` above
    `high 1,024` is showing a scale that runs backwards.
    """
    fixed = BUDGET_MAP.get(level, BUDGET_MAP["medium"])
    if is_local_provider(provider_id):
        local = local_budgets(context)
        if level in local:
            return local[level]
        if local:
            return min(fixed, max(local.values()))
    return fixed


def local_levels(context: int, output_limit: int = 0) -> List[str]:
    """Which rungs a local model can actually reach.

    Two or nothing. One is not a choice, and an empty list is the honest answer
    for a window too small to divide - the caller then keeps the fixed ladder,
    which is at least valid even where it is optimistic.
    """
    budgets = local_budgets(context, output_limit)
    return ([n for n in _LOCAL_LADDER_ASCENDING if n in budgets]
            if len(budgets) >= 2 else [])

CACHE_FILE = "cache/capabilities_cache.json"

# Bumped when the meaning of a stored entry changed. v1 entries were name
# guesses with no record of where they came from, so on read they are rebuilt
# rather than trusted: an old cache says `thinking: false` about models that
# reason, and there is no way to tell such an entry from a real observation.
CAPABILITIES_CACHE_VERSION = 2


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
                data = json.load(f)
            if isinstance(data, dict):
                return _migrate_cache(data)
        except Exception:
            pass
    return {}


def _migrate_cache(data: Dict[str, dict]) -> Dict[str, dict]:
    """Bring an older cache up to date, keeping what still carries meaning.

    Two things are thrown away on the way:

    * Entries with no model in the key. There are real ones - `local_gguf:default`,
      `local_gguf:model`, `""`, `"model"` - all recorded as incapable, all from
      a call made before a model had actually been picked.
    * Version 1 entries, which predate `source` and therefore cannot be told
      apart from a name guess. They are dropped so detection runs again.
      `vision` and `mmproj_path` are the exception: those are read out of the
      model file, and re-deriving them means opening a multi-gigabyte GGUF.
    """
    version = data.get("__version__", 1)
    kept: Dict[str, dict] = {}
    for key, item in data.items():
        if key.startswith("__") or not isinstance(item, dict):
            continue
        model_part = key.split(":", 1)[-1].strip()
        if not model_part or model_part in ("default", "model"):
            continue  # a placeholder, not a model

        if version >= CAPABILITIES_CACHE_VERSION or item.get("source"):
            kept[key] = item
            continue

        # No `source`: keep it only for what cannot be recomputed cheaply.
        if item.get("vision") or item.get("mmproj_path"):
            item["source"] = "gguf"
            item.setdefault("thinking", False)
            kept[key] = item
    return kept


def _save_cache(cache: Dict[str, dict]) -> None:
    payload = {"__version__": CAPABILITIES_CACHE_VERSION}
    payload.update({k: v for k, v in cache.items() if not k.startswith("__")})
    path = _get_cache_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _field_text(field) -> str:
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


def _field_number(field) -> int:
    """An integer or float GGUF field, or 0.

    Numeric fields come back from `GGUFReader` as arrays of the type the file
    declared, so the value has to be read out of `parts` and widened. A field
    that is absent, of some type nobody expected, or nonsense reads as 0, and 0
    is the right answer for every caller here: it means "this file did not say".
    """
    try:
        if field is None:
            return 0
        raw = field.parts[field.data[0]] if field.data else 0
        if hasattr(raw, "item"):
            raw = raw.item()
        return int(raw)
    except Exception:
        return 0


def find_mmproj_sidecar(gguf_path: str) -> Optional[str]:
    try:
        d = os.path.dirname(os.path.abspath(gguf_path))
        base = os.path.splitext(os.path.basename(gguf_path))[0].lower()
        for f in os.listdir(d):
            fl = f.lower()
            if fl.endswith(".gguf") and "mmproj" in fl:
                if base.split("-")[0] in fl or fl.split("-")[0] in base:
                    return os.path.join(d, f)
                return os.path.join(d, f)
    except Exception:
        pass
    return None


def inspect_gguf_metadata(gguf_path: str) -> Optional[ModelCapability]:
    if not GGUFReader or not os.path.exists(gguf_path):
        return None
    try:
        reader = GGUFReader(gguf_path)
        fields = reader.fields

                                            
        vision = any(n.startswith("clip.") or "vision" in n.lower() for n in fields)

        # The window the model was trained for, read out of the file itself. It
        # was here all along and nobody read it, so every local thinking level
        # came from a fixed scale written for a different kind of model: `max`
        # asked a 32k Qwen to spend 128k of thinking, which it can only answer by
        # ignoring the request. A percentage of what the model actually has is
        # the same number of choices, all of them reachable.
        arch_f = fields.get("general.architecture")
        arch = _field_text(arch_f).strip().lower() if arch_f is not None else ""
        context_limit = _field_number(fields.get(f"{arch}.context_length")) if arch else 0
        if context_limit <= 0:
            context_limit = _field_number(fields.get("context_length"))

        mmproj_path = None
        if not vision:
            mmproj_path = find_mmproj_sidecar(gguf_path)
            if mmproj_path and os.path.exists(mmproj_path):
                try:
                    mr = GGUFReader(mmproj_path)
                    arch_f = mr.fields.get("general.architecture")
                    arch = _field_text(arch_f).strip().lower() if arch_f else ""
                    if arch == "clip" or any(n.startswith("clip.") for n in mr.fields):
                        vision = True
                    else:
                        mmproj_path = None
                except Exception:
                    mmproj_path = None

                                                          
        template_str = ""
        tpl_f = fields.get("tokenizer.chat_template")
        if tpl_f is not None:
            template_str = _field_text(tpl_f)
        template_lower = template_str.lower()
        template_hit = any(
            kw in template_lower
            for kw in [
                "enable_thinking",
                "<think>",
                "thinking_mode",
                "reasoning_content",
                "think_budget",
                "<|channel|>thought",
                "<|think|>",
            ]
        )
        think_token_hit = False
        try:
            tf = fields.get("tokenizer.ggml.tokens")
            yf = fields.get("tokenizer.ggml.token_type")
            if tf is not None and yf is not None:
                toks = [t.tobytes().decode("utf-8", errors="ignore") if hasattr(t, "tobytes") else bytes(t).decode("utf-8", errors="ignore") for t in tf.parts]
                types = list(yf.data)
                think_token_hit = any("think" in t.lower() for t, y in zip(toks, types) if y in (3, 4, 5))
        except Exception:
            pass

        has_thinking = bool(template_hit or think_token_hit)
        if has_thinking:
            # Five rungs regardless of what the model can do. A 4k-context model
            # was offered `max` at 128k, and a 128k one was still topping out at
            # the same figure. Derived from the file, and dropped entirely when
            # too little of the window survives to make a choice out of it.
            levels = local_levels(context_limit) or list(DEFAULT_LEVELS)
            return _build_capability(
                os.path.basename(gguf_path),
                thinking=True,
                param_style="chat_template_kwargs",
                levels=levels,
                default_level="medium",
                thinking_tags=(("<think>", "</think>") if "<think>" in template_str
                               else ("<|think|>", "<|turn|>model")),
                vision=vision,
                image_param_style="mmproj" if vision else "none",
                mmproj_path=mmproj_path,
                confidence="high",
                # Read out of the model file, so it is worth keeping: re-reading
                # a multi-gigabyte GGUF on every launch is not.
                source="gguf",
                context_limit=context_limit,
            )
        return _build_capability(
            os.path.basename(gguf_path),
            thinking=False,
            vision=vision,
            image_param_style="mmproj" if vision else "none",
            mmproj_path=mmproj_path,
            confidence="high",
            source="gguf",
            context_limit=context_limit,
        )
    except Exception:
        return None


def inspect_gguf_chat_template(gguf_path: str) -> Optional[ModelCapability]:
    """The chat template's verdict on reasoning, or None if it says nothing.

    There is no `if "gemma" in path: return None` here any more. That line made
    this function unreachable for the one family it was written about, and it
    contradicted the point of asking a file: a Gemma 3 template can and does
    enable thinking. The template decides, the name does not.
    """
    cap = inspect_gguf_metadata(gguf_path)
    return cap if (cap and cap.thinking) else None


_MEM_CACHE: Dict[str, ModelCapability] = {}

# Only these survive to disk. A name guess is worth keeping for the current
# session and not one moment longer: it was the reason `big-pickle` was stuck
# on "cannot reason" forever, written down before anything had asked the model.
PERSISTED_SOURCES = frozenset({
    "gguf",              # read from the model file
    "provider_metadata", # the provider named its own levels
    "observed",          # it emitted a reasoning channel
    "api_error",         # the provider said it does not support it
    "registered",        # a plugin told us
    "catalog",           # models.dev, a community database
})

# Strongest first. Where two sources disagree the earlier one wins, and the
# order is the argument for itself:
#
#   * `observed` outranks everything, because it is not a claim - the model
#     streamed a reasoning channel and that happened.
#   * `provider_metadata` outranks the catalog: a provider that lists its own
#     capabilities knows them better than anyone else, including us.
#   * `catalog` outranks a name guess by a wide margin. It is one maintainer
#     group rather than 33 providers, so it is not an authority - but it is
#     still a real answer about a real model, and it is the only one available
#     for the ~26 providers whose `/models` returns nothing but an `id`.
#   * A name guess is last and is the only one that dies with the session.
SOURCE_ORDER: Tuple[str, ...] = (
    "observed",
    "gguf",
    "provider_metadata",
    "registered",
    "catalog",
    "api_error",
    "name_heuristic",
)


def source_rank(source: str) -> int:
    """Lower is stronger. Unknown sources rank below every known one."""
    try:
        return SOURCE_ORDER.index(source)
    except ValueError:
        return len(SOURCE_ORDER)


def stronger_source(a: str, b: str) -> bool:
    """Whether `a` should replace `b`."""
    return source_rank(a) < source_rank(b)


def register_model_capability(
    model_id: str,
    provider_id: str = "",
    thinking: bool = False,
    levels: Optional[List[str]] = None,
    param_style: str = "none",
    default_level: str = "",
    max_budget: int = 32768,
    thinking_tags: Tuple[str, str] = ("<think>", "</think>"),
    confidence: str = "high",
    context_limit: int = 0,
    local: bool = False,
) -> ModelCapability:
    """Record what is known about a model, from a source that actually knows.

    The generic fallback in `get_model_capability` guesses from the model name
    and then *caches the guess*, so `thinking=False` for an unrecognised name
    is permanent for the process and for the on-disk cache. That is fine for a
    name, and wrong for a provider that can simply be asked: the OpenCode
    server reports reasoning levels per model, and a provider that streams a
    reasoning channel proves it by doing so.

    This is the supported way to teach the app a fact that its heuristics
    cannot know. It writes through the same cache `get_model_capability`
    reads, so the input-bar thinking indicator and `/params` pick it up with
    no further wiring.

    `context_limit` and `local` are here because a provider plugin is often the
    only party that knows them. A server on this machine can report a model's
    window, and whether it is on this machine is a property of the plugin rather
    than something the core can read a provider id off. Without this, a local
    OpenCode model with a 1M window was shown - and compacted against - the
    core's flat 32,768 fallback.
    """
    if local and provider_id:
        declare_local_provider(provider_id)

    cache_key = f"{provider_id}:{model_id}" if provider_id else model_id
    cap = _build_capability(
        model_id,
        thinking=thinking,
        param_style=param_style,
        levels=levels,
        default_level=default_level,
        max_budget=max_budget,
        thinking_tags=thinking_tags,
        confidence=confidence,
        source="registered",
        context_limit=context_limit,
    )
    _persist(cache_key, cap)
    return cap


def _build_capability(
    model_id: str,
    thinking: bool,
    param_style: str = "none",
    levels: Any = None,
    default_level: str = "",
    max_budget: int = 32768,
    thinking_tags: Tuple[str, str] = ("<think>", "</think>"),
    vision: bool = False,
    image_param_style: str = "none",
    mmproj_path: Optional[str] = None,
    confidence: str = "low",
    source: str = "name_heuristic",
    infer_levels: bool = True,
    context_limit: int = 0,
) -> ModelCapability:
    """One place that normalises, so no caller can build a bad capability.

    The invariant that matters: a model without reasoning gets no ladder. Not an
    empty one to fill in later, none at all. That is what keeps Ctrl+T and the
    input-bar label out of the way for models that cannot use them, instead of
    offering "thinking: medium" to something that never emits a thought.

    `infer_levels=False` is for callers that already decided there is no ladder
    and must not have one invented underneath them. Two real cases: a model that
    thinks with no effort dial at all, and a model whose budget cannot fit two
    rungs. Without the flag both were handed `DEFAULT_LEVELS` here, which turned
    "we do not know" into "medium" - the exact confusion this whole module
    exists to remove.
    """
    names = normalize_levels(levels)
    if not thinking:
        names = []
    if thinking and not names and infer_levels:
        # Reasoning is on but nobody named the rungs. It is still a ladder -
        # a numeric budget template simply does not spell out its steps - so
        # offer the conservative three rather than nothing to cycle through.
        names = list(DEFAULT_LEVELS)

    lvl = normalize_level(default_level)
    if lvl not in names:
        # Middle of the ladder, leaning weak: a model that reasons harder than
        # asked is slow and a model that reasons less than asked answers worse.
        lvl = "medium" if "medium" in names else (names[len(names) // 2] if names else THINKING_OFF)
    return ModelCapability(
        model_id=model_id,
        thinking=bool(thinking),
        param_style=param_style,
        levels=names,
        default_level=lvl,
        max_budget=max_budget,
        thinking_tags=tuple(thinking_tags),
        vision=vision,
        image_param_style=image_param_style,
        mmproj_path=mmproj_path,
        confidence=confidence,
        source=source,
        context_limit=context_limit,
    )


def _persist(cache_key: str, cap: ModelCapability) -> None:
    """Keep it in memory always, on disk only when it is trustworthy."""
    _MEM_CACHE[cache_key] = cap
    if cap.source not in PERSISTED_SOURCES:
        return
    cache = _load_cache()
    cache[cache_key] = asdict(cap)
    _save_cache(cache)


def forget_uncached_capabilities() -> int:
    """Drop in-memory guesses so a better source can take over.

    Called when the model changes. A name guess is stable for one session and
    gone after this, which is what lets a later, correct answer replace it.
    Returns how many entries were dropped.
    """
    stale = [k for k, v in _MEM_CACHE.items() if v.source not in PERSISTED_SOURCES]
    for key in stale:
        _MEM_CACHE.pop(key, None)
    return len(stale)


# --------------------------------------------------------------- models.dev

# A toggle is on or off, not a dial. Three rungs is the honest maximum for a
# model that has no effort scale, and `extra`/`max` on such a model would be two
# more names for the same request.
_CATALOG_TOGGLE_LEVELS = ["low", "medium", "high"]


def catalog_max_budget(facts: Any) -> int:
    """What the top rung means in tokens, for a model described by models.dev.

    Drawn from `limit.output`, not `limit.context`: a thinking budget is spent
    from the output allowance, so a 200k-context model that can only emit 8k
    cannot think for 64k. Ten percent stays back for the answer itself.

    Falls back to the context window when there is no output figure, and to the
    top of our own ladder when the database has neither - the caller shows which
    of the three it used rather than pretending the number is certain.
    """
    output = int(getattr(facts, "output_limit", 0) or 0)
    if output > 0:
        return max(1024, min(BUDGET_MAP["max"], int(output * 0.9)))
    context = int(getattr(facts, "context_limit", 0) or 0)
    if context > 0:
        return max(1024, min(BUDGET_MAP["max"], int(context * 0.75)))
    return BUDGET_MAP["max"]


def catalog_levels(facts: Any) -> List[str]:
    """The rungs this model can actually reach.

    The budget ceiling is applied to the **budget** path only, and that
    distinction is the whole reason this is not one filter over one list. On the
    effort path no token count is ever sent - `max` and `extra` both travel as
    `high` - so a model that publishes `max` and can emit 64000 tokens has that
    rung available whatever its output limit is. Clipping it would have removed
    a level the provider said exists, on the strength of a number that is not
    used on that path.

    Three outcomes, and the last is the one worth naming:

    * The database published effort values - those are the rungs, literally.
    * It published a *style* but no values, as with `{type = "budget_tokens",
      min = 1024}` - a dial with nobody having written down its steps.
    * It published no options at all - no dial at all, and inventing rungs would
      be a lie. Empty; the model reasons on its own.
    """
    options = getattr(facts, "reasoning_options", None) or []
    if not options:
        return []

    style = facts.param_style

    # A toggle is on or off. It has no dial to have steps on, so three rungs is
    # as much as it can honestly mean and `extra`/`max` would be two more names
    # for the same request.
    if style == "chat_template_kwargs":
        return list(_CATALOG_TOGGLE_LEVELS)

    published = normalize_levels(getattr(facts, "reported_levels", []) or [])
    if not published:
        # A dial nobody named the steps of. Normalised, because THINKING_LEVELS is
        # stored strongest-first and everything downstream assumes weakest-first.
        published = normalize_levels(THINKING_LEVELS)

    if style != "budget_tokens":
        return published  # effort path: names, not token counts

    ceiling = catalog_max_budget(facts)
    fitting = [n for n in published if BUDGET_MAP.get(n, 0) <= ceiling]
    return fitting if len(fitting) >= 2 else []


def _capability_from_catalog(model_id: str, provider_id: str) -> Optional[ModelCapability]:
    """Build a capability from models.dev, or None when it has nothing.

    None is returned in three distinct cases and the caller treats them all the
    same way - fall through to whatever comes next - because from here they are
    indistinguishable and pretending otherwise would mean three branches for no
    gain: the provider is not in the database, the model is not in that
    provider's entry, or the database has nothing to say about reasoning.
    """
    try:
        from . import model_catalog
        facts = model_catalog.lookup(provider_id, model_id)
    except Exception:
        return None
    if facts is None:
        return None

    thinking = bool(facts.reasoning)
    levels = catalog_levels(facts) if thinking else []

    # A model running on this machine gets its ladder from its own window rather
    # than from the fixed Anthropic-shaped scale. Same reasoning as the budget:
    # `max` at 128k on a 32k window is a request the server can only answer by
    # ignoring it, and the middle rungs would sit where nothing can use them.
    if thinking and is_local_provider(provider_id):
        derived = local_levels(facts.context_limit, facts.output_limit)
        if derived:
            levels = derived

    # No dial published at all: the model thinks and there is nothing to choose.
    # That is a real state, not a failure - and it is exactly `big-pickle`. It
    # gets no label and no Ctrl+T, and the first reasoning channel on the wire
    # hands it the three-rung ladder through `note_reasoning_observed`.
    #
    # `infer_levels=False` because `levels` being empty here means "deliberately
    # none". Left to infer, the builder would hand it DEFAULT_LEVELS and we
    # would be publishing a control the model does not have.
    style = facts.param_style or "stream_only"
    if levels and style == "stream_only":
        style = "reasoning_effort"

    return _build_capability(
        model_id,
        thinking=thinking,
        param_style=style if thinking else "none",
        levels=levels,
        max_budget=catalog_max_budget(facts),
        vision=facts.vision,
        image_param_style="inline_base64" if facts.vision else "none",
        confidence="high" if facts.matched_by == "exact" else "medium",
        source="catalog",
        infer_levels=False,
        context_limit=facts.context_limit,
    )


def get_model_capability(
    model_id: str,
    provider_id: str = "",
    api_key: str = "",
    model_path: str = "",
    timeout: float = 3.0,
) -> ModelCapability:
    cache_key = f"{provider_id}:{model_id}" if provider_id else model_id

    # No blanket `gemma -> no thinking` rule. It used to sit here, before the
    # GGUF branch below ever ran, so a Gemma whose template does enable
    # thinking was recorded as incapable and `inspect_gguf_chat_template` could
    # never contradict it. The template is the authority; the name is not.

    if cache_key in _MEM_CACHE:
        return _MEM_CACHE[cache_key]

    cache = _load_cache()

    if cache_key in cache:
        item = cache[cache_key]
        # An entry with no `source` did not survive migration, so it can only be
        # a hand-edited file. Discarded rather than believed: this is the entry
        # that once froze a model on "cannot reason" with nothing to appeal to.
        src = item.get("source", "")
        if not src:
            cache.pop(cache_key, None)
            _save_cache(cache)
        elif "confidence" in item and "vision" in item:
            cap = _build_capability(
                item.get("model_id", model_id),
                thinking=item.get("thinking", False),
                param_style=item.get("param_style", "none"),
                levels=item.get("levels", []),
                default_level=item.get("default_level", ""),
                max_budget=item.get("max_budget", 32768),
                thinking_tags=tuple(item.get("thinking_tags", ("<think>", "</think>"))),
                vision=item.get("vision", False),
                image_param_style=item.get("image_param_style", "none"),
                mmproj_path=item.get("mmproj_path"),
                confidence=item.get("confidence", "high"),
                source=src,
                # Absent on entries written before the local budget existed, and
                # 0 is exactly the right answer for those: `budget_for` falls
                # back to the fixed ladder, which is what they were built with.
                context_limit=int(item.get("context_limit") or 0),
            )
            _MEM_CACHE[cache_key] = cap
            return cap

    if provider_id == "local_gguf" or (model_path and os.path.exists(model_path)) or model_id.endswith(".gguf"):
        actual_path = model_path
        if not actual_path or not os.path.exists(actual_path):
            # Was a six-entry candidate list including D:\\, E:\\ and
            # D:\\Swift\\models - the last belonging to an unrelated project.
            from .model_paths import resolve_model_file
            actual_path = resolve_model_file(model_id) or actual_path

        if actual_path and os.path.exists(actual_path):
            cap = inspect_gguf_metadata(actual_path)
            if cap:
                _persist(cache_key, cap)
                return cap

        mid_low = model_id.lower()
        has_thinking = any(k in mid_low for k in ["r1", "qwq", "reason", "think"])
        if has_thinking:
            cap = _build_capability(
                model_id,
                thinking=True,
                param_style="tag_based",
                levels=DEFAULT_LEVELS,
                default_level="medium",
                thinking_tags=("<think>", "</think>"),
                source="name_heuristic",
            )
        else:
            cap = _build_capability(
                model_id,
                thinking=False,
                source="name_heuristic",
            )
        _persist(cache_key, cap)
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
                        arch = m.get("architecture") or {}
                        modalities = arch.get("input_modalities") or []
                        has_vision = "image" in [str(x).lower() for x in modalities]
                        if "reasoning" in params or "include_reasoning" in params:
                            # The provider confirmed the model reasons. It did
                            # not publish effort values - `supported_parameters`
                            # is a list of names a request may carry, not a list
                            # of levels - so the ladder is somebody else's to
                            # answer.
                            #
                            # models.dev first: it outranks a name by a wide
                            # margin and knows the difference between a model
                            # that takes a numeric budget and one that takes a
                            # word. Only a name if models.dev is silent here.
                            from_catalog = _capability_from_catalog(model_id, provider_id)
                            if from_catalog is not None and from_catalog.levels:
                                cap = _build_capability(
                                    model_id,
                                    thinking=True,
                                    param_style=from_catalog.param_style,
                                    levels=from_catalog.levels,
                                    default_level="medium",
                                    max_budget=from_catalog.max_budget,
                                    vision=has_vision,
                                    image_param_style="inline_base64" if has_vision else "none",
                                    confidence=from_catalog.confidence,
                                    # Honest attribution: this is not the
                                    # provider's own ladder, so it must not be
                                    # filed as one. `provider_metadata` outranks
                                    # the catalog and is written to disk, so
                                    # filing a guess there would let it outlive
                                    # the models.dev data that corrected it.
                                    source="catalog",
                                )
                                _persist(cache_key, cap)
                                return cap

                            mid_low = model_id.lower()
                            # Last resort, and it stays cheap to be wrong: a name
                            # guess is never written to disk, so the next models.dev
                            # refresh can replace it.
                            if any(k in mid_low for k in ["o1", "o3", "o4"]):
                                levels = ["low", "medium", "high", "extra"] if (
                                    "xhigh" in mid_low or "pro" in mid_low
                                ) else ["low", "medium", "high"]
                                param_style = "reasoning_effort"
                            elif "gemma" in mid_low:
                                # A boolean switch, not a ladder: off is the
                                # disabled state, and on means a middle effort.
                                levels = DEFAULT_LEVELS
                                param_style = "chat_template_kwargs"
                            elif any(k in mid_low for k in ["claude-3-7", "claude-3.7", "fable"]):
                                # Anthropic takes a numeric budget, so the top
                                # rungs are real here rather than clamped away.
                                levels = ["low", "medium", "high", "extra", "max"]
                                param_style = "budget_tokens"
                            else:
                                levels = ["low", "medium", "high"]
                                param_style = "reasoning_effort"

                            cap = _build_capability(
                                model_id,
                                thinking=True,
                                param_style=param_style,
                                levels=levels,
                                default_level="medium",
                                vision=has_vision,
                                image_param_style="inline_base64" if has_vision else "none",
                                # Not "high". Nothing here was published by
                                # anyone; the name was read.
                                confidence="low",
                                source="name_heuristic",
                            )
                            _persist(cache_key, cap)
                            return cap
                        if has_vision:
                            cap = _build_capability(
                                model_id,
                                thinking=False,
                                vision=True,
                                image_param_style="inline_base64",
                                confidence="high",
                                source="provider_metadata",
                            )
                            _persist(cache_key, cap)
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
                    cap = _build_capability(
                        model_id,
                        thinking=True,
                        param_style=("chat_template_kwargs"
                                     if "enable_thinking" in template else "tag_based"),
                        levels=DEFAULT_LEVELS,
                        default_level="medium",
                        confidence="high",
                        source="provider_metadata",
                    )
                    _persist(cache_key, cap)
                    return cap
        except Exception:
            pass

    # models.dev. Placed after the branches above and before the name guesses,
    # which is the whole ordering argument: it loses to anything a provider said
    # about itself and wins over reading a name.
    cap = _capability_from_catalog(model_id, provider_id)
    if cap is not None:
        _persist(cache_key, cap)
        return cap

    mid = model_id.lower()

    # Name heuristics. Weak evidence, so they are never written to disk: a
    # guess must be free to be wrong today and replaced tomorrow.
    if "claude-3-7" in mid or "claude-3.7" in mid or "fable-5" in mid:
        cap = _build_capability(
            model_id,
            thinking=True,
            param_style="budget_tokens",
            levels=["low", "medium", "high", "extra", "max"],
            default_level="medium",
            max_budget=64000,
        )
        _persist(cache_key, cap)
        return cap

    if mid.startswith("o1") or mid.startswith("o3") or mid.startswith("o4") or "reasoning" in mid:
        cap = _build_capability(
            model_id,
            thinking=True,
            param_style="reasoning_effort",
            levels=(["low", "medium", "high", "extra"]
                    if ("xhigh" in mid or "pro" in mid) else ["low", "medium", "high"]),
            default_level="medium",
        )
        _persist(cache_key, cap)
        return cap

    if "r1" in mid or "deepseek-reasoner" in mid or "qwq" in mid or "qwen3" in mid:
        cap = _build_capability(
            model_id,
            thinking=True,
            param_style="reasoning_effort" if provider_id == "deepseek" else "tag_based",
            levels=["low", "medium", "high", "extra", "max"],
            default_level="medium",
        )
        _persist(cache_key, cap)
        return cap

    cap = _build_capability(
        model_id,
        thinking=False,
    )
    _persist(cache_key, cap)
    return cap


def note_reasoning_observed(model_id: str, provider_id: str = "") -> bool:
    """Record that a model emitted a reasoning channel, so it can reason.

    The strongest signal there is, and the only one no name heuristic reaches:
    the provider just streamed thinking. This is what unsticks a model that a
    name guess had already written down as incapable.

    It upgrades an existing answer and never replaces it. That distinction is
    the whole point: this used to rebuild the capability from scratch with the
    three-rung default, which threw away the six rungs the catalogue had
    published. The level the user had chosen then vanished from the ladder, and
    the next refresh quietly moved them off `max` and onto `medium` - in the
    middle of using the app, without a keypress. A model that reasons because we
    observed it is a fact about `thinking`; it says nothing about how finely the
    effort can be turned, which is what the rest of the record holds.

    Returns True when this changed the stored answer.
    """
    if not model_id:
        return False
    cache_key = f"{provider_id}:{model_id}" if provider_id else model_id
    existing = _MEM_CACHE.get(cache_key)

    if existing is not None and existing.thinking:
        # Already known to reason. Nothing to correct, and nothing to replace -
        # its ladder came from a source that knows more than this one does.
        return False

    if existing is not None:
        # It was recorded as not reasoning, which was wrong. Correct only that,
        # and keep the style, the levels and the budget a source had supplied.
        #
        # `infer_levels` stays on: if the entry carried no ladder, then nothing
        # ever claimed one, and a model that just proved it reasons has a dial
        # we had no reason to know about - so it gets the conservative three. If
        # it did carry one, the builder keeps it and inference never runs.
        cap = _build_capability(
            existing.model_id or model_id,
            thinking=True,
            param_style=existing.param_style if existing.param_style != "none" else "stream_only",
            levels=existing.levels,
            default_level=existing.default_level,
            max_budget=existing.max_budget,
            thinking_tags=existing.thinking_tags,
            vision=existing.vision,
            image_param_style=existing.image_param_style,
            mmproj_path=existing.mmproj_path,
            confidence="high",
            source="observed",
        )
        _persist(cache_key, cap)
        return True

    # Nothing on record at all. A model that emits reasoning with no known dial
    # gets the conservative three, so there is something to cycle.
    cap = _build_capability(
        model_id,
        thinking=True,
        param_style="stream_only",
        levels=DEFAULT_LEVELS,
        default_level="medium",
        confidence="high",
        source="observed",
    )
    _persist(cache_key, cap)
    return True


def clean_model_name(raw_name: str) -> str:
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

