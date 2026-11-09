"""OpenCode-free provider logic, kept out of the CMDAI CODE checkout.

This module owns everything that turns a CMDAI message list into a call
against the local OpenCode server, plus the text handling that goes wrong in
practice: the model narrating instead of acting, foreign tool syntax, and the
absence of a real token stream on one of the two routes.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .bridge import BridgeError, OpenCodeBridge, get_bridge

# Reused from the app so we detect tool calls exactly where the agent loop does.
try:
    from cmdai.core.providers import TOOL_COMPLETE_PATTERNS
except Exception:  # pragma: no cover - the app is always present at runtime
    TOOL_COMPLETE_PATTERNS = re.compile(r"<tool:\w+[^>]*?/>", re.IGNORECASE)


# ---------------------------------------------------------------- text tools


def _is_tool_tag(token: str) -> bool:
    stripped = token.strip()
    return stripped.startswith("<tool:") or stripped.startswith("</tool:")


def _replay_as_stream(
    text: str,
    on_token: Optional[Callable[[str], None]] = None,
    is_aborted: Optional[Callable[[], bool]] = None,
    pace: float = 0.012,
) -> str:
    """Emit `text` through `on_token` in readable pieces, with a small pause.

    Only used on the non-streaming fallback route. Splitting on whitespace
    matters: fixed-width cuts landed mid-word and read as blocks of garbage.
    Reassembling the pieces reproduces the input exactly.
    """
    if not text:
        return ""

    tokens = re.findall(r"\S+\s*", text)
    if not tokens:
        if on_token:
            on_token(text)
        return text

    out: List[str] = []
    buf: List[str] = []
    size = 0
    target = 60

    def flush() -> None:
        nonlocal buf, size
        if not buf:
            return
        chunk = "".join(buf)
        out.append(chunk)
        buf = []
        size = 0
        if on_token:
            on_token(chunk)
        if pace and not (is_aborted and is_aborted()):
            time.sleep(pace)

    for tok in tokens:
        if is_aborted and is_aborted():
            break
        # Long unbroken runs (base64, minified JSON) would hold the buffer.
        if len(tok) > 160:
            flush()
            for i in range(0, len(tok), 160):
                part = tok[i:i + 160]
                out.append(part)
                if on_token:
                    on_token(part)
                if pace and not (is_aborted and is_aborted()):
                    time.sleep(pace)
            continue
        buf.append(tok)
        size += len(tok)
        if size >= target:
            flush()

    flush()
    return "".join(out)


_FOREIGN_TOOLS = re.compile(
    r"<(tool|tool_call|tool-call):\s*(execute|glob_tool|search_tool|bash|shell|"
    r"list_dir|read_file|write_file|patch|webfetch|todowrite|task)\b",
    re.IGNORECASE,
)


def neutralize_foreign_tool_syntax(text: str) -> str:
    """Rewrite OpenCode's own tool syntax so the app's parser ignores it.

    `/prompt` leaves OpenCode's tools attached to the session, and the model
    occasionally reaches for them:

        <tool:execute><code>await search(...)</code></tool:execute>
        <tool:glob>...</tool:glob>

    Both start with `<tool:` so the app accepts them and dispatches a tool that
    does not exist, and the turn dies on "Unknown tool". Renaming the tag keeps
    the text visible while making it inert.
    """
    if not text:
        return text
    return _FOREIGN_TOOLS.sub(
        lambda m: f"<{m.group(1)}-{m.group(2)}_unsupported:", text
    )


# The model sometimes refuses to use tags at all and claims it has "real Claude
# Code tools", or explains its tools instead of calling one. Measured on
# big-pickle: first attempt refused, second succeeded.
_REFUSAL_MARKERS = (
    "i don't have cm",
    "i do not have cm",
    "i have no cm",
    "claude code tool",
    "i have exactly two tools available",
    "tools are configured for me",
    "there are no cm",
    "i have no tools of my own",
    "the only tools i actually have",
    "let me just look up",
)


def looks_like_tool_refusal(text: str) -> bool:
    """True when the reply describes tools instead of emitting a tag."""
    if TOOL_COMPLETE_PATTERNS.search(text):
        return False
    low = text.lower()
    return any(m in low for m in _REFUSAL_MARKERS)


# ------------------------------------------------------------- capabilities


def publish_capabilities(models: List[Dict[str, Any]]) -> int:
    """Tell CMDAI which of these models can reason, and at what levels.

    The app decides whether to show a thinking level from
    `get_model_capability`, whose fallback guesses from the model name and then
    keeps that guess only for the session. `big-pickle` matches no keyword, so
    it was recorded as "cannot reason" and the label never appeared - while the
    model reasons on most turns.

    Two signals, and they are not the same thing:

      * `variants` on the server are selectable effort levels. A model with
        them is reasoning-capable and those levels are real.
      * Reasoning can also be on with no effort control at all, which is what
        big-pickle does. Only a stream proves that.

    The level names are the server's own (`xhigh` and friends) and are handed
    over untranslated: `register_model_capability` normalises them, so writing
    a second ladder here is how the two used to drift apart.

    Returns how many models were registered.
    """
    try:
        from cmdai.core.capabilities import register_model_capability
    except Exception:
        return 0

    registered = 0
    for m in models or []:
        mid = str(m.get("id", ""))
        has_thinking = bool(m.get("thinking"))
        has_vision = m.get("vision") if "vision" in m else None
        ctx_len = int(m.get("context_length") or 0)
        if not mid or (not has_thinking and not has_vision and ctx_len <= 0):
            continue
        levels = list(m.get("levels") or [])
        register_model_capability(
            mid,
            provider_id="opencode_local",
            thinking=has_thinking,
            levels=levels,
            param_style=("variant" if levels else "stream_only") if has_thinking else "none",
            # Left unset on purpose: the app picks the middle of the ladder,
            # and hardcoding it here is how "medium" ended up meaning two things.
            default_level="",
            vision=has_vision,
            # The window, which the server publishes per model as
            # `limit.context` and `_to_model` already carries as
            # `context_length`. Without handing it over, nothing in the app could
            # find the model - it is not on any network, so models.dev has never
            # heard of it - and `get_dynamic_context_limit` fell back to 32,768.
            # That number was not only what `/modelinfo` and `/context` showed;
            # it was what `should_summarize` multiplied by 0.97, so a 1M model
            # was being compacted at 3% of its window.
            context_limit=ctx_len,
            # Declared rather than inferred from the id. The server runs on this
            # machine, which is a fact about this plugin and not about a string.
            local=True,
        )
        registered += 1
    return registered


def note_reasoning_observed(model_id: str) -> bool:
    """Upgrade a model to "can reason" after its stream proved it.

    A provider that emits a reasoning channel cannot be argued out of it, and
    no name heuristic will ever know. This is the only signal that works for a
    model with no effort variants.

    Delegates to the core, which owns the bookkeeping and the cache. Returns
    True when the model was upgraded, so the caller can refresh the input bar.
    """
    if not model_id:
        return False
    try:
        from cmdai.core.capabilities import note_reasoning_observed as core_note
    except Exception:
        return False
    return core_note(model_id, "opencode_local")


# ---------------------------------------------------------------- the provider


def stream_completion(
    model_id: str,
    messages: List[Dict[str, Any]],
    generation_params: Optional[Dict[str, Any]] = None,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    is_aborted: Optional[Callable[[], bool]] = None,
    on_tool: Optional[Callable[[str, str, str], None]] = None,
    on_usage: Optional[Callable[[Dict[str, Any]], None]] = None,
    *args,
    **kwargs,
) -> Tuple[str, str]:
    """One agent turn against the local OpenCode server.

    Uses the streaming route first: the server publishes `session.text.delta`
    roughly every 100-200ms, so the chat fills in as the model writes. If that
    route is refused before producing anything, the non-streaming route is tried
    so the turn still lands.

    Returns the same `(text, thinking)` tuple as every other provider.
    """
    system = ""
    convo: List[Dict[str, Any]] = []
    for msg in messages:
        if str(msg.get("role", "user")).lower() == "system":
            system = str(msg.get("content", ""))
        else:
            convo.append(msg)

    if not system and not any(str(m.get("content", "")).strip() for m in convo):
        err = "Nothing to send: the prompt was empty."
        if on_token:
            on_token(err)
        return err, ""

    workdir = str((generation_params or {}).get("workdir") or "").strip()
    is_subagent = bool((generation_params or {}).get("is_subagent", False))
    if not workdir:
        from .patch import _APP_REF
        app = _APP_REF.get("app")
        if app:
            workdir = getattr(app, "workdir", "")
    if not workdir:
        workdir = os.getcwd()
    workdir = os.path.abspath(workdir)
    bridge = get_bridge(workdir)

    def _fail(message: str) -> Tuple[str, str]:
        err_msg = f"⚠ API Error: {message}" if not message.startswith("⚠") else message
        if on_token:
            on_token(err_msg)
        return err_msg, ""

    state = {"text": "", "used": False, "refused": False}

    def _on_text(piece: str) -> None:
        # Catch a refusal as it streams. A chunk already handed to the TUI
        # cannot be taken back, so the retry has to start before the bad reply
        # finishes rather than after.
        if looks_like_tool_refusal(state["text"] + piece):
            state["refused"] = True
            return
        state["used"] = True
        # Appended raw. Neutralising each delta separately was a real bug: a
        # tag split across two deltas matches nothing and reaches the parser
        # intact, which dispatches a tool that does not exist here. It is
        # neutralised once on the assembled text instead.
        state["text"] += piece
        if on_token:
            on_token(piece)

    def _aborted() -> bool:
        return bool(state["refused"]) or bool(is_aborted and is_aborted())

    reasoning_seen = [False]

    def _on_reasoning(piece: str) -> None:
        if not piece:
            return
        if not reasoning_seen[0]:
            reasoning_seen[0] = True
            # First reasoning delta is proof the model can reason, and the only
            # proof for a model whose name carries no keyword - big-pickle
            # being the case here, with no effort variants either.
            if note_reasoning_observed(model_id):
                try:
                    from .patch import refresh_thinking_ui
                    refresh_thinking_ui()
                except Exception:
                    pass
        if on_thinking:
            on_thinking(piece)

    nudge = ""
    for attempt in range(2):
        prompt = OpenCodeBridge.messages_to_prompt(system, convo, nudge=nudge)
        state.update(text="", used=False, refused=False)

        try:
            text = bridge.stream_generate(
                model_id,
                prompt,
                on_text=_on_text,
                on_reasoning=_on_reasoning,
                is_aborted=_aborted,
                on_tool=on_tool,
                on_usage=on_usage,
                workdir=workdir,
                is_subagent=is_subagent,
                is_naming=bool((generation_params or {}).get("is_naming", False)),
            )
        except BridgeError as exc:
            if attempt == 0 and not state["used"]:
                try:
                    fallback = bridge.generate(model_id, prompt)
                except BridgeError as exc2:
                    return _fail(f"OpenCode Bridge: {exc2}")
                except Exception as exc2:
                    return _fail(f"OpenCode Bridge failed: {exc2}")
                fallback = neutralize_foreign_tool_syntax(fallback)
                return (
                    _replay_as_stream(
                        fallback, on_token=on_token, is_aborted=is_aborted
                    ),
                    "",
                )
            return _fail(f"OpenCode Bridge: {exc}")
        except Exception as exc:
            return _fail(f"OpenCode Bridge failed: {exc}")

        if state["refused"]:
            # Nothing was shown: _on_text held the whole reply back.
            nudge = (
                "\n\nYour previous reply described tools instead of using one. "
                "Ignore that and answer now: if a tool is needed, output only its "
                "<tool:name ...> tag from the list above. Output nothing else."
            )
            continue
        break

    if is_aborted and is_aborted():
        return "", ""

    if state["used"]:
        # What is on screen is the raw text; what the agent parses is the
        # neutralised text. They differ only in foreign tool tags, which the
        # app cannot run either way, and the parser must never see one.
        full_res = neutralize_foreign_tool_syntax(state["text"])
        res_tuple = (full_res, "")
    else:
        full_res = neutralize_foreign_tool_syntax(text)
        res_tuple = (
            _replay_as_stream(
                full_res,
                on_token=on_token,
                is_aborted=is_aborted,
            ),
            "",
        )

    if on_usage:
        try:
            from cmdai.core.resource_limits import estimate_token_count
            p_tok = estimate_token_count(messages)
            c_tok = estimate_token_count([{"content": full_res}])
            on_usage({"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": p_tok + c_tok})
        except Exception:
            pass

    return res_tuple


def fetch_models(workdir: str = "") -> List[Dict[str, Any]]:
    return get_bridge(workdir).list_models()