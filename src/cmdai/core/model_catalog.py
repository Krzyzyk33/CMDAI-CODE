"""Model facts from models.dev, cached on disk and read without the network.

The reason this exists: `PROVIDERS_CATALOG` has 33 providers and roughly 26 of
them answer `GET /models` with nothing but an `id`. There is no flag to ask
twice, so the app fell back on guessing from the model name - which is how
`big-pickle` sat on "cannot reason" for days while streaming a reasoning channel
the whole time.

models.dev is a community-maintained database (github.com/anomalyco/models.dev,
the same people who maintain opencode) that carries what those providers do not
publish: `reasoning`, and - the part that matters - `reasoning_options` with the
actual effort levels a model accepts.

Two rules shape everything here:

* **It is not an authority.** It ranks below anything the provider itself said
  and below anything seen on the wire. It is a very good third opinion, not the
  last word. See `capabilities.SOURCE_ORDER`.
* **Nothing on the interactive path may touch the network.** `load()` reads a
  file. `refresh()` is the only thing that makes a request, and a failed refresh
  must never overwrite what is already on disk - a stale answer beats no answer
  when the user is one keypress from needing it.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None


CATALOG_URL = "https://models.dev/catalog.json"

# Seven days. Long enough that nobody notices, short enough that a provider
# adding a model shows up within a week rather than at the next release.
CATALOG_TTL_SECONDS = 7 * 24 * 3600

# Bumped when the shape of what we keep changes, so an old file is re-fetched
# instead of read as if it were complete.
CATALOG_VERSION = 2

# Relative to the cache directory, which `_cache_dir` already resolves and
# creates. Not "cache/..." as well - that would nest a cache inside a cache.
_CATALOG_FILE = "models_dev_catalog.json"

# How long a single HTTP attempt may take. Generous for a big JSON file, short
# enough that startup does not appear to hang.
_HTTP_TIMEOUT = 20.0

# The fields worth keeping out of a catalog that is tens of megabytes. Pricing
# and benchmarks are interesting and unused, and carrying them would make every
# startup read a much larger file for nothing.
_KEPT_MODEL_FIELDS = (
    "reasoning",
    "reasoning_options",
    "limit",
    "modalities",
    "tool_call",
    "attachment",
    "interleaved",
    "canonical_model_id",
    "status",
    # Only the thinking line of it, but it has to survive the round trip - see
    # `_facts_from`, which reads `cost.reasoning` and used to read nothing,
    # because `cost` was dropped here.
    "cost",
)


# --------------------------------------------------------------- provider map

# Our provider ids against theirs. `None` is a decision, not a gap: it means
# "there is no entry, so do not ask and do not guess". `local_gguf` reads the
# model file, `opencode` gets its levels from the server's `variants`, and
# `huggingface` is not a serving provider - it hosts weights.
#
# Kept explicit rather than derived by lowercasing, because a wrong mapping is
# worse than a missing one: it attributes one lab's facts to another's model.
PROVIDER_ALIASES: Dict[str, Optional[str]] = {
    "local_gguf": None,
    "opencode": None,
    "opencode_zen": None,
    "opencode_local": None,
    "huggingface": None,
    "gemini": "google",
    "zai": "zhipuai",
    "github_models": "github-copilot",
    "bedrock": None,
    "vertex": None,
    "copilot": None,
    # Everything else is spelled the same on both sides. Listed so that this
    # table is the whole answer and not half of it.
    "openrouter": "openrouter",
    "openai": "openai",
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "groq": "groq",
    "cerebras": "cerebras",
    "xai": "xai",
    "mistral": "mistral",
    "together": "together",
    "perplexity": "perplexity",
    "cohere": "cohere",
    "fireworks": "fireworks",
    "sambanova": "sambanova",
    "ai21": "ai21",
    "deepinfra": "deepinfra",
    "replicate": "replicate",
    "cloudflare": "cloudflare",
    "lmstudio": None,
    "vllm": None,
    "ollama": None,
    "moonshot": "moonshotai",
    "nvidia": "nvidia",
    "nebius": "nebius",
    "minimax": "minimax",
}


def catalog_provider_id(provider_id: str) -> Optional[str]:
    """Our provider id -> theirs, or None when there is no entry.

    Membership, not truthiness. A `None` stored in the table means "there is no
    entry, so do not ask and do not guess", and it has to survive the lookup:
    `dict.get` cannot tell a stored None from an absent key, so checking
    `.get(...) is not None` silently turned every one of those decisions back
    into "try the same name after all" - which is how `local_gguf` and the four
    other deliberate Nones quietly started being asked about.
    """
    if not provider_id:
        return None
    if provider_id in PROVIDER_ALIASES:
        return PROVIDER_ALIASES[provider_id]
    # A provider added to the app after this table was written. Same spelling is
    # the overwhelmingly likely case, so try it - but the caller still checks
    # that the provider is actually present in the data before believing it.
    return provider_id.lower()


# -------------------------------------------------------------------- lookup

# How much extra a model id may carry past the family name for the dated-suffix
# step to accept it. `-20250929` and `-latest` fit; a different version does not.
_DATED_SUFFIX_MAX = 12


def _normalize(text: str) -> str:
    """`claude-3-7` -> `claude37`. Case, separators and punctuation dropped.

    Used for the fuzzy step only. Punctuation is removed rather than turned into
    separators because `claude-3-7` and `claude 3 7` have to land on the same
    key, and `qwen3-30b` must not collide with `qwen330b` by accident.
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


# Shortest name worth fuzzy-matching at all. Not a positional rule - a typo can
# be three characters in, as `claude`/`cloude` shows, so a prefix test would
# reject the very typos this step exists for. A length floor is the honest
# guard: short ids are too easy to collide by accident.
_FUZZY_MIN_LENGTH = 6


def _within_edit_budget(a: str, b: str, budget: int = 2) -> bool:
    """Levenshtein distance <= budget, where a typo counts and a version does not.

    The rule that makes this safe: a substitution is only allowed where both
    characters are **letters**. `claude-sonnet-4-5` and `cloude-sonnet-4-5` are
    the same model with one keystroke, and differ inside a word.
    `claude-sonnet-4-5` and `claude-sonnet-9-9` differ in a version number and
    are different models, however few edits apart they are. Counting both as two
    edits is what made this step match across major versions, which is worse
    than not matching at all.
    """
    if not a or not b:
        return False
    if min(len(a), len(b)) < _FUZZY_MIN_LENGTH:
        return False
    if abs(len(a) - len(b)) > budget:
        return False

    blocked = budget + 1  # the cost of a substitution we refuse to make
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            # Only a *disagreement* between digits is refused. Two matching
            # digits must cost nothing - charging them anything forces the
            # search around the whole numeric tail, which is how a one-keystroke
            # typo in `claudesonnet45` came out at distance three.
            if ca != cb and (ca.isdigit() or cb.isdigit()):
                sub = blocked
            else:
                sub = previous[j - 1] + (ca != cb)
            current.append(min(
                previous[j] + 1,     # deletion
                current[j - 1] + 1,  # insertion
                sub,
            ))
        previous = current
    return previous[len(b)] <= budget


@dataclass
class ModelFacts:
    """What one model can do, according to models.dev.

    `matched_by` is kept for the UI and for the tests: "the user typed
    `claude-3-7`, we found it by normalising" is worth being able to show and
    worth being able to assert.
    """

    model_id: str
    reasoning: Optional[bool] = None
    # `effort` -> the literal levels it accepts; `budget_tokens` -> numeric;
    # `toggle` -> on or off, which is not an effort dial.
    reasoning_options: List[Dict[str, Any]] = field(default_factory=list)
    param_style: str = ""
    context_limit: int = 0
    output_limit: int = 0
    vision: bool = False
    tool_call: bool = False
    reasoning_field: str = ""
    cost_per_mtok_reasoning: float = 0.0
    canonical_model_id: str = ""
    status: str = ""
    matched_by: str = ""

    @property
    def reported_levels(self) -> List[str]:
        """The levels it actually publishes, not the ones we would invent."""
        for opt in self.reasoning_options or []:
            if str(opt.get("type", "")).lower() != "effort":
                continue
            values = opt.get("values") or []
            if isinstance(values, list):
                return [str(v) for v in values if v]
        return []


# ------------------------------------------------------------------ storage

def _cache_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    path = os.path.join(project_root, "cache")
    os.makedirs(path, exist_ok=True)
    return path


def _catalog_path() -> str:
    return os.path.join(_cache_dir(), _CATALOG_FILE)


_MEM: Dict[str, Any] = {}
_FETCHED_AT: float = 0.0


def _meta_path() -> str:
    return _catalog_path() + ".meta.json"


def _slim(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the fields we read, so the cache stays small."""
    out: Dict[str, Any] = {}
    for provider_id, provider in (payload or {}).items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        kept: Dict[str, Any] = {}
        for model_id, model in models.items():
            if not isinstance(model, dict):
                continue
            entry = {k: model[k] for k in _KEPT_MODEL_FIELDS if k in model}
            if entry:
                kept[model_id] = entry
        if kept:
            out[provider_id] = {"models": kept}
    return out


def load() -> Dict[str, Any]:
    """The catalog from disk. Never a network call, never an exception.

    A file we cannot read, or one written by another version, is treated as no
    file at all: the app falls back to what it knew before, which is a guess
    from the model name, and says so.
    """
    global _MEM, _FETCHED_AT
    if _MEM:
        return _MEM

    path = _catalog_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        if not isinstance(blob, dict) or blob.get("__version__") != CATALOG_VERSION:
            return {}
        data = blob.get("providers")
        if not isinstance(data, dict):
            return {}
        _MEM = data
        _FETCHED_AT = float(blob.get("fetched_at") or 0.0)
        return _MEM
    except Exception:
        return {}


def fetched_at() -> float:
    """Unix time of the last successful fetch, 0 when there has been none."""
    load()
    return _FETCHED_AT


def is_stale() -> bool:
    """True when the cache is missing or older than the TTL."""
    load()
    if not _MEM or not _FETCHED_AT:
        return True
    return (time.time() - _FETCHED_AT) > CATALOG_TTL_SECONDS


def _write(data: Dict[str, Any], when: float) -> None:
    payload = {"__version__": CATALOG_VERSION, "fetched_at": when, "providers": data}
    try:
        with open(_catalog_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass
    try:
        with open(_meta_path(), "w", encoding="utf-8") as f:
            json.dump({"fetched_at": when, "count": len(data)}, f)
    except Exception:
        pass


def refresh(timeout: float = _HTTP_TIMEOUT) -> bool:
    """Fetch the catalog. Returns whether anything new landed.

    On any failure the file on disk is left exactly as it was. That is the
    whole point: an unreachable models.dev must not cost the user the answers
    they already had, and must never surface as an error.
    """
    global _MEM, _FETCHED_AT

    if requests is None:
        return False

    try:
        resp = requests.get(CATALOG_URL, timeout=timeout)
        if getattr(resp, "status_code", 0) != 200:
            return False
        payload = resp.json()
    except Exception:
        return False

    if not isinstance(payload, dict):
        return False

    slim = _slim(payload)
    if not slim:
        # A 200 with nothing usable is a failure, not an empty database.
        return False

    now = time.time()
    _write(slim, now)
    _MEM = slim
    _FETCHED_AT = now
    return True


# -------------------------------------------------------------------- facts

def _facts_from(model_id: str, model: Dict[str, Any], matched_by: str) -> ModelFacts:
    limits = model.get("limit") or {}
    modalities = model.get("modalities") or {}
    inputs = [str(x).lower() for x in (modalities.get("input") or [])]
    interleaved = model.get("interleaved")
    reasoning_field = ""
    if isinstance(interleaved, dict):
        reasoning_field = str(interleaved.get("field") or "")

    cost = model.get("cost") or {}
    try:
        reasoning_cost = float(cost.get("reasoning") or 0.0)
    except (TypeError, ValueError):
        reasoning_cost = 0.0

    options = model.get("reasoning_options") or []
    if not isinstance(options, list):
        options = []

    return ModelFacts(
        model_id=model_id,
        reasoning=model.get("reasoning") if isinstance(model.get("reasoning"), bool) else None,
        reasoning_options=options,
        param_style=param_style_for(options),
        context_limit=int(limits.get("context") or 0),
        output_limit=int(limits.get("output") or 0),
        vision="image" in inputs,
        tool_call=bool(model.get("tool_call")),
        reasoning_field=reasoning_field,
        cost_per_mtok_reasoning=reasoning_cost,
        canonical_model_id=str(model.get("canonical_model_id") or ""),
        status=str(model.get("status") or ""),
        matched_by=matched_by,
    )


def param_style_for(reasoning_options: List[Dict[str, Any]]) -> str:
    """Their vocabulary -> ours. No guessing from the family name.

    This is the whole reason for using models.dev. Their policy distinguishes a
    first-party lab from a relay and spells the difference out: GPT-style relays
    carry `effort` with the three standard values, DeepSeek-style switches are
    `toggle` with no dial at all. Deriving that from a name is exactly the guess
    work that produced four spellings of the same rung.
    """
    types = {str(o.get("type", "")).lower() for o in reasoning_options or []
             if isinstance(o, dict)}
    if "effort" in types:
        return "reasoning_effort"
    if "budget_tokens" in types:
        return "budget_tokens"
    if "toggle" in types:
        return "chat_template_kwargs"
    return ""


def _short(model_id: str) -> str:
    return model_id.split("/")[-1].lower() if model_id else ""


def lookup(provider_id: str, model_id: str) -> Optional[ModelFacts]:
    """Find one model. Five tries, first hit wins.

    The first four are exact, including the dated-suffix step that matches
    `claude-sonnet-4-5-20250929` against a catalog entry stored as
    `claude-sonnet-4-5`. Only the last guesses, and it guesses within an edit
    budget of two, because a user who typed `cloude-3-7` still meant Claude and
    a wrong answer about a model costs more than a missing one.
    """
    if not model_id:
        return None
    catalog = load()
    their_id = catalog_provider_id(provider_id)
    if not their_id:
        return None
    provider = catalog.get(their_id)
    if not isinstance(provider, dict):
        return None
    models = provider.get("models")
    if not isinstance(models, dict):
        return None

    # 1. exactly as written
    hit = models.get(model_id)
    if isinstance(hit, dict):
        return _facts_from(model_id, hit, "exact")

    # 2. the provider's own id, lowercased
    hit = models.get(model_id.lower())
    if isinstance(hit, dict):
        return _facts_from(model_id, hit, "lowercased")

    # 3. the short name, which is how a provider spells a model it resells
    short = _short(model_id)
    if short:
        candidates = [m for m in models if _short(m) == short]
        if len(candidates) == 1:
            return _facts_from(candidates[0], models[candidates[0]], "short-name")

    wanted = _normalize(model_id)
    if not wanted:
        return None

    # 4. a dated id against an undated entry. Anthropic ships
    # `claude-sonnet-4-5-20250929` while the catalog keeps the family name, and
    # a provider's own id is what the user pastes in. Exact on both sides, no
    # edit budget, and only when there is nothing to choose between.
    for candidate in models:
        norm = _normalize(candidate)
        if norm and (wanted.startswith(norm) or norm.startswith(wanted)) \
                and abs(len(wanted) - len(norm)) <= _DATED_SUFFIX_MAX:
            dated = [m for m in models if _normalize(m).startswith(norm)]
            if len(dated) == 1:
                return _facts_from(dated[0], models[dated[0]], "dated-suffix")
            break

    # 5. normalised, then within an edit budget of the normalised model id
    exact = [m for m in models if _normalize(m) == wanted]
    if len(exact) == 1:
        return _facts_from(exact[0], models[exact[0]], "normalized")
    for candidate in models:
        if _within_edit_budget(_normalize(candidate), wanted):
            return _facts_from(candidate, models[candidate], "fuzzy")

    return None


def lookup_unmatched(provider_id: str, model_id: str) -> bool:
    """True when we would have wanted an answer and there was none.

    Kept separate from `lookup` returning None, because "this provider is not in
    the database" and "this model is not in that provider's entry" are different
    facts and only the second one is worth acting on.
    """
    their_id = catalog_provider_id(provider_id)
    if not their_id:
        return False
    catalog = load()
    if their_id not in catalog:
        return False
    return lookup(provider_id, model_id) is None
