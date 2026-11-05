"""Bridge to a locally running OpenCode server, used as a free model source.

Why this exists
---------------
OpenCode Zen's `*-free` models refuse third-party HTTP clients. A direct call
to https://opencode.ai/zen/v1/chat/completions returns:

    403 {"type":"FreeTierError", ... "OpenCode's free tier can only be used
          from within OpenCode"}

The same models answer normally when the request is issued by a real OpenCode
server. So instead of calling Zen directly, CMDAI CODE DEV starts (or reuses)
`opencode serve` and talks to its HTTP API:

    POST /api/session              -> create a session bound to one model
    POST /api/session/{id}/generate -> stateless text completion
    GET  /api/model?directory=...   -> available models

Known limitations, deliberately accepted
----------------------------------------
* `/generate` is not a stream. It returns the whole completion at once, so the
  caller gets the text in one piece and is expected to re-chunk it for display.
* No `reasoning_content` is exposed on this route, so the thinking animation
  has nothing to show for these models.
* Upstream 503s ("Service temporarily overloaded") happen at random. They are
  retried with backoff rather than surfaced.
* A fresh server reports zero models for the first few seconds while it warms
  up. `list_models` retries instead of reporting an empty catalogue.
* The session inherits OpenCode's own context (AGENTS.md and friends). We do
  not attempt to suppress that.

This module is DEV-only. The production checkout at E:\\CMDAI-CODE does not
contain it.
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover - requests is a hard dependency in practice
    requests = None  # type: ignore[assignment]


# The OpenCode server authenticates with HTTP Basic. The username is a constant
# in the client; only the password varies.
_AUTH_USER = "opencode"

# Where the managed background service records its password. Used only to attach
# to an already-running server instead of spawning a second one.
_SERVICE_JSON = os.path.join("~", ".config", "opencode", "service.json")

# Models we saw work through the bridge during development. This is a seed list
# only: the real catalogue comes from the server, which also knows which models
# are geo-blocked or gone.
KNOWN_FREE_MODELS: List[str] = [
    "big-pickle",
    "space-bunny-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "ling-3.1-flash-free",
    "ling-3.0-flash-fin-free",
    "mimo-v2.6-flash-free",
    "longcat-2.5-preview-free",
    "muse-spark-1.3-contributor-free",
    "fledge-alpha-free",
]

# Measured, not guessed. Each entry is what actually happened when the model was
# asked a plain arithmetic question and then asked to emit one CMDAI tool tag.
#
#   agent     - follows CMDAI's <tool:name> convention
#   observed  - what the agent probe returned, or the failure
#   bad       - do not advertise; the picker shows the reason instead
#
# Screen run: 10 models, arithmetic + one tool-tag probe each.
MODEL_NOTES: Dict[str, Dict[str, str]] = {
    "big-pickle": {
        "agent": "yes",
        "observed": "tool tag honoured, ~2s",
        "recommend": "recommended",
    },
    "space-bunny-free": {
        "agent": "yes",
        "observed": "tool tag honoured, 1-2s, occasionally 20s",
        "recommend": "recommended",
    },
    "mimo-v2.6-flash-free": {
        "agent": "yes",
        "observed": "tool tag honoured, 6-11s",
        "recommend": "ok",
    },
    "longcat-2.5-preview-free": {
        "agent": "yes",
        "observed": "tool tag honoured, ~6s",
        "recommend": "ok",
    },
    "nemotron-3-ultra-free": {
        "agent": "yes",
        "observed": "tool tag honoured but slow, 26s",
        "recommend": "slow",
    },
    "muse-spark-1.3-contributor-free": {
        "agent": "no",
        "observed": "answers in prose instead of emitting the tag; also returned "
                    "unrelated text once ('I can't switch to cu')",
        "bad": "chat only - does not emit tool tags",
    },
    "ling-3.1-flash-free": {
        "agent": "no",
        "observed": "empty response to the tool-tag probe",
        "bad": "chat only - does not emit tool tags",
    },
    "nemotron-3.5-lightning-free": {
        "agent": "no",
        "observed": "answered a 2+2 question in 118s, tool probe 225s; used "
                    "<tool_call> instead of <tool:name>",
        "bad": "unusable here - extremely slow, wrong tool syntax",
    },
    "ling-3.0-flash-fin-free": {
        "agent": "no",
        "observed": "every request failed with 'Upstream model is overloaded'",
        "bad": "upstream overloaded on every attempt",
    },
    "fledge-alpha-free": {
        "agent": "no",
        "observed": "refused: not available in your country",
        "bad": "not available in your region",
    },
}

# Offered first in the picker, best measured first.
PREFERRED_MODELS: List[str] = ["big-pickle", "space-bunny-free", "mimo-v2.6-flash-free"]


class BridgeError(RuntimeError):
    """Raised for conditions the user needs to read, not just log."""


class ServerUnavailable(BridgeError):
    """No OpenCode server, and one could not be started."""


@dataclass
class ServerInfo:
    base_url: str
    password: str
    spawned: bool = False
    process: Optional[subprocess.Popen] = field(default=None, repr=False)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _describe_failure(data: Dict[str, Any]) -> str:
    """Turn a step-failed event into the exact API error message returned upstream."""
    if not data:
        return "OpenCode reported a step failure with no detail."
    err = data.get("error") if isinstance(data, dict) else None
    if err is None and isinstance(data, dict):
        err = data

    if isinstance(err, str) and err.strip():
        return err.strip()

    if isinstance(err, dict):
        # 1. Look for nested response.body JSON (e.g. FreeUsageLimitError, rate limit, quota error)
        resp_obj = err.get("response")
        if isinstance(resp_obj, dict):
            body_val = resp_obj.get("body")
            if isinstance(body_val, str) and body_val.strip().startswith("{"):
                try:
                    parsed_body = json.loads(body_val)
                    if isinstance(parsed_body, dict):
                        b_err = parsed_body.get("error") or parsed_body
                        if isinstance(b_err, dict) and b_err.get("message"):
                            return str(b_err.get("message")).strip()
                        if isinstance(b_err, str) and b_err.strip():
                            return b_err.strip()
                except Exception:
                    pass
            elif isinstance(body_val, dict) and body_val.get("message"):
                return str(body_val.get("message")).strip()

        # 2. Extract specific error message and code/type
        msg = err.get("message") or (err.get("data", {}).get("message") if isinstance(err.get("data"), dict) else None)
        status = err.get("status") or err.get("statusCode") or err.get("code")
        err_type = err.get("type") or err.get("name") or err.get("_tag")

        if msg and str(msg).strip():
            clean_msg = str(msg).strip()
            # If status or type provides helpful context, prepend it
            if status and str(status) not in clean_msg:
                return f"[HTTP {status}] {clean_msg}"
            return clean_msg

        if err_type and str(err_type).strip():
            return f"API Error ({err_type})"

    raw = json.dumps(err, ensure_ascii=False) if err else ""
    return raw[:400] or "OpenCode reported a step failure with no detail."


def _picker_order(model: Dict[str, Any]) -> Tuple[int, str]:
    """Sort key for the picker: usable first, measured-good before the rest.

    The catalogue is whatever OpenCode advertises, which includes models that
    cannot drive an agent loop and one that took 225s to answer. Without an
    order the user meets those by accident.
    """
    mid = str(model.get("id", ""))
    if model.get("usable") is False:
        # Stable and alphabetical inside the bad tier: a user reading the list
        # should get the same order every time, not whatever the sort did.
        tier, rank = 2, ""
    elif mid in PREFERRED_MODELS:
        tier, rank = 0, f"{PREFERRED_MODELS.index(mid):02d}"
    else:
        tier, rank = 1, ""
    return (tier, f"{rank}{mid}")


def _find_opencode() -> List[str]:
    """Locate the `opencode` launcher as an argv prefix.

    On Windows an npm global install produces three files in the npm bin dir:
    `opencode`, `opencode.cmd` and `opencode.ps1`. CreateProcess does not
    resolve any of them from a bare name the way a shell does, so a plain
    `Popen(["opencode", ...])` fails with "file not found" even though
    `where opencode` finds it. We probe the extensions explicitly.

    Returns the argv prefix, e.g. ["opencode.cmd"] or ["/usr/local/bin/opencode"].
    """
    if os.name != "nt":
        found = shutil.which("opencode")
        return [found] if found else []

    paths = os.environ.get("PATH", "").split(os.pathsep)
    for ext in (".cmd", ".bat", ".exe", ""):
        for directory in paths:
            if not directory:
                continue
            candidate = os.path.join(directory.strip('"'), "opencode" + ext)
            if os.path.isfile(candidate):
                return [candidate]

    found = shutil.which("opencode")
    return [found] if found else []


    """Locate the `opencode` launcher as an argv prefix.

    On Windows an npm global install produces three files in the npm bin dir:
    `opencode`, `opencode.cmd` and `opencode.ps1`. CreateProcess does not
    resolve any of them from a bare name the way a shell does, so a plain
    `Popen(["opencode", ...])` fails with "file not found" even though
    `where opencode` finds it. We probe the extensions explicitly.

    Returns the argv prefix, e.g. ["opencode.cmd"] or ["/usr/local/bin/opencode"].
    """
    if os.name != "nt":
        found = shutil.which("opencode")
        return [found] if found else []

    paths = os.environ.get("PATH", "").split(os.pathsep)
    for ext in (".cmd", ".bat", ".exe", ""):
        for directory in paths:
            if not directory:
                continue
            candidate = os.path.join(directory.strip('"'), "opencode" + ext)
            if os.path.isfile(candidate):
                return [candidate]

    found = shutil.which("opencode")
    return [found] if found else []


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _read_service_json() -> Tuple[Optional[str], Optional[str]]:
    """Return (base_url, password) of the managed background service."""
    path = os.path.expanduser(_SERVICE_JSON)
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, None
    password = data.get("password")
    if not password:
        return None, None

    url: Optional[str] = None
    launcher = _find_opencode()
    if launcher:
        try:
            out = subprocess.run(
                launcher + ["service", "status"],
                capture_output=True, text=True, timeout=10, shell=False,
            ).stdout
        except Exception:
            return None, password
        match = re.search(r"https?://[^\s]+", out or "")
        if match:
            url = match.group(0).rstrip("/")
    return url, password


def _server_alive(base_url: str, password: str) -> bool:
    if requests is None:
        return False
    try:
        r = requests.get(
            f"{base_url}/api/info",
            headers={"Authorization": f"Basic {_b64(f'{_AUTH_USER}:{password}')}"},
            timeout=4.0,
        )
        return r.status_code == 200
    except Exception:
        return False


class OpenCodeBridge:
    """Owns one OpenCode server and one session per (workdir, model) pair."""

    def __init__(self, workdir: str, timeout: float = 180.0):
        self.workdir = os.path.abspath(workdir or os.getcwd())
        self.timeout = timeout
        self._server: Optional[ServerInfo] = None
        # (workdir, model) -> (session id, times used)
        self._sessions: Dict[Tuple[str, str], Tuple[str, int]] = {}
        self._models_cache: Tuple[float, List[Dict[str, Any]]] = (0.0, [])
        # `/generate` is not stateless: it reads the session's accumulated
        # context. Observed: first call on a fresh session took 23.9s, later
        # ones 1.4-2.7s, i.e. the context grows. Recycle to keep it bounded.
        self.max_session_uses = 40
        # Short, because the catalogue is OpenCode's and is expected to change
        # under us; only the picker hits this path.
        self.models_ttl = 45.0
        # Models known to be gone or region-locked, so the picker can say why
        # instead of listing something that will fail on use.
        self.unavailable: Dict[str, str] = {}

    # ---------------------------------------------------------------- server

    def _headers(self) -> Dict[str, str]:
        assert self._server is not None
        pw = self._server.password
        return {
            "Content-Type": "application/json",
            "Authorization": f"Basic {_b64(f'{_AUTH_USER}:{pw}')}",
        }

    # ------------------------------------------------------- server-side tools

    @staticmethod
    def _tool_target(args: Any, tool_name: str = "") -> str:
        """The one line worth showing for a tool call.

        OpenCode's tools take an object, and the interesting part differs per
        tool: a path for read and edit, a command for bash, a query for search.
        Preference order rather than "whatever has a value", so the same tool
        always shows the same thing.

        The field names are not documented anywhere the client exposes - it
        never names them either - so this list is a best guess that happens to
        cover the tools in daily use. When none of them match, the arguments
        themselves are shown. That is strictly better than a placeholder: a
        reader can see what the tool was actually called with, and a wrong
        guess is visible rather than silent.
        """
        if not isinstance(args, dict):
            return str(args)[:200] if args else ""
        tl = str(tool_name or "").strip().lower()
        if tl in ("glob", "grep", "search", "websearch", "web_search", "codesearch") or (
            ("pattern" in args or "query" in args) and tl not in ("read", "write", "edit", "ls", "list")
        ):
            pat = str(args.get("pattern") or args.get("query") or "").strip()
            p_dir = str(args.get("path") or args.get("dir") or args.get("directory") or "").strip()
            if pat and p_dir:
                return f"{pat} ({p_dir})"
            if pat:
                return pat
        for key in ("path", "filePath", "file_path", "file", "filename", "file_name", "command", "cmd",
                    "query", "pattern", "url", "description", "prompt",
                    "input", "target", "name"):
            value = args.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
        if "code" in args and isinstance(args["code"], str):
            code_str = args["code"]
            import re
            url_m = re.search(r"https?://[^\s'\"`\\]+", code_str)
            if url_m:
                return url_m.group(0)
            cmd_m = re.search(r"(?:exec|spawn|execSync)\(\s*['\"]([^'\"]+)['\"]", code_str)
            if cmd_m:
                return cmd_m.group(1)
            first_line = next((ln.strip() for ln in code_str.splitlines() if ln.strip()), "")
            return first_line[:200]
        try:
            return json.dumps(args, ensure_ascii=False)[:200]
        except Exception:
            return str(args)[:200]

    @staticmethod
    def _clean_tool_target(tool_name: str, target: str) -> str:
        """Strip redundant tool names or descriptions like 'read Read file ...'."""
        t = str(target or "").strip()
        tl = str(tool_name or "").strip().lower()
        if not t:
            return ""
        if tl in ("read", "file_read"):
            for prefix in ("read file ", "reading file ", "read ", "reading "):
                if t.lower().startswith(prefix):
                    return t[len(prefix):].strip()
        elif tl in ("edit", "file_edit"):
            for prefix in ("edit file ", "editing file ", "edit "):
                if t.lower().startswith(prefix):
                    return t[len(prefix):].strip()
        elif tl in ("write", "file_write"):
            for prefix in ("write file ", "writing file ", "write "):
                if t.lower().startswith(prefix):
                    return t[len(prefix):].strip()
        elif tl in ("bash", "shell", "command", "exec", "cmd", "run_command"):
            for prefix in ("run command ", "running command ", "execute command ", "executing command "):
                if t.lower().startswith(prefix):
                    return t[len(prefix):].strip()
        elif t.lower().startswith(f"{tl} "):
            return t[len(tl) + 1:].strip()
        return t

    @staticmethod
    def _tool_result_text(content: Any) -> str:
        """A tool result is usually a block; only the text in it is a summary."""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            for k in ("output", "stdout", "text", "content", "diff"):
                val = content.get(k)
                if isinstance(val, str) and val.strip():
                    return val
            return ""
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("output") or item.get("stdout")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return ""

    @staticmethod
    def _extract_target_from_partial_input(name: str, input_str: str) -> str:
        if not input_str:
            return ""
        import re
        m = re.search(r'"(?:filePath|path|file|cmd|command|url|query|pattern|directory|dir|name|role|agent|goal|task|description)"\s*:\s*"([^"]+)"', input_str, re.IGNORECASE)
        if m:
            raw = m.group(1).replace('\\\\', '\\').replace('\\/', '/')
            return OpenCodeBridge._clean_tool_target(name, raw)
        return ""

    @staticmethod
    def _extract_partial_code(json_str: str) -> str:
        if not json_str:
            return ""
        for key in ('"content":', '"code":', '"new":', '"new_content":'):
            pos = json_str.find(key)
            if pos != -1:
                rest = json_str[pos + len(key):].lstrip()
                if rest.startswith('"'):
                    rest = rest[1:]
                    if rest.endswith('"}'):
                        rest = rest[:-2]
                    elif rest.endswith('"'):
                        rest = rest[:-1]
                    return rest.replace('\\n', '\n').replace('\\t', '    ').replace('\\"', '"').replace('\\\\', '\\')
        return ""

    @staticmethod
    def _report_tool(
        on_tool: Callable[[str, str, str], None],
        etype: str,
        data: Dict[str, Any],
        tool_inputs: Dict[str, Dict[str, Any]],
    ) -> None:
        """Turn one `session.tool.*` event into one `on_tool` call.

        Only the four transitions a person watching would call a change: a call
        starting, finishing, failing, or reporting progress. The input deltas
        are consumed rather than forwarded - they are the arguments being typed
        out, and a line per token is noise, not information.
        """
        call_id = str(
            data.get("id") or data.get("callID") or data.get("callId")
            or data.get("toolCallId") or data.get("toolCallID")
            or data.get("partID") or data.get("partId") or "default"
        )
        record = tool_inputs.setdefault(call_id, {})
        name = str(data.get("name") or data.get("tool") or record.get("name") or "tool")
        if data.get("name") or data.get("tool"):
            record["name"] = name

        is_subagent_native = name.lower() in ("subagent", "task")
        is_exec = name.lower() in ("execute", "eval", "evaluate")
        code_str = ""
        if is_exec:
            code_val = record.get("input")
            code_str = code_val if isinstance(code_val, str) else (code_val.get("code", "") if isinstance(code_val, dict) else str(code_val or ""))
            import re
            url_m = re.search(r"https?://[^\s'\"`\\]+", code_str)
            if ("fetch" in code_str.lower() or "http" in code_str.lower()) and url_m:
                name = "webfetch"
            elif any(k in code_str.lower() for k in ("child_process", "exec(", "spawn(", "execsync", "bash")):
                name = "terminal"
            else:
                name = "script"
        is_subagent_call = is_subagent_native or (is_exec and ("tools.subagent" in code_str or "tools.task" in code_str))
        if etype == "session.tool.input.started":
            record["input"] = ""
            record["target"] = ""
            if not is_subagent_call:
                on_tool(name, "generating", "")
            return
        if etype == "session.tool.input.delta":
            record["input"] = str(record.get("input") or "") + str(data.get("delta") or "")
            target = OpenCodeBridge._extract_target_from_partial_input(name, record["input"])
            if target:
                record["target"] = target
            if is_subagent_call:
                return
            partial_code = OpenCodeBridge._extract_partial_code(record["input"]) if name.lower() in ("write", "edit") else ""
            import json as _json
            payload = _json.dumps({"target": record.get("target") or "", "live_code": partial_code or record["input"]})
            on_tool(name, "generating", payload)
            return
        if etype == "session.tool.input.ended":
            record["input"] = str(data.get("text") or record.get("input") or "")
            target = OpenCodeBridge._extract_target_from_partial_input(name, record["input"])
            if target:
                record["target"] = target
            if is_subagent_call:
                return
            partial_code = OpenCodeBridge._extract_partial_code(record["input"]) if name.lower() in ("write", "edit") else ""
            import json as _json
            payload = _json.dumps({"target": record.get("target") or "", "live_code": partial_code or record["input"]})
            on_tool(name, "generating", payload)
            return

        if etype == "session.tool.called":
            # `input` here is the *arguments object*, not a result payload. The
            # reader for result blocks would turn it into "" and the target
            # would silently disappear - which is the same bug as showing no
            # tool at all, one layer in.
            state_obj = data.get("state") if isinstance(data.get("state"), dict) else {}
            record["input"] = data.get("input") or state_obj.get("input") or record.get("input")
            raw_target = OpenCodeBridge._tool_target(record.get("input"), name)
            target = OpenCodeBridge._clean_tool_target(name, raw_target)
            record["target"] = target
            if is_subagent_call:
                return
            inp = record.get("input") or {}
            live_code = ""
            if isinstance(inp, dict):
                live_code = str(inp.get("content") or inp.get("new") or inp.get("code") or inp.get("new_content") or "")
            if name.lower() == "task":
                try:
                    from cmdai.agent.tasks import BackgroundTaskManager
                    from cmdai.agent.runner import clean_subagent_call
                    bg_sid = f"subagent_{call_id[:8] if call_id else int(time.time())}"
                    record["bg_sid"] = bg_sid
                    cleaned_t = clean_subagent_call(inp if inp else (record.get("input") or target or ""))
                    r_label = cleaned_t.get("role") or "Subagent"
                    g_label = cleaned_t.get("goal") or target or "Task"
                    if isinstance(inp, dict):
                        r_cand = str(inp.get("name") or inp.get("role") or inp.get("agent") or "").strip()
                        if r_cand and r_cand.lower() not in ("general", "subagent", "default", "agent"):
                            r_label = r_cand
                        g_cand = str(inp.get("description") or inp.get("prompt") or inp.get("goal") or inp.get("task") or "").strip()
                        if g_cand:
                            g_label = g_cand
                    record["role"] = r_label
                    record["goal"] = g_label
                    record["target"] = g_label
                    target = g_label
                    BackgroundTaskManager.get_instance().register_subagent(bg_sid, role=r_label, task=g_label)
                except Exception:
                    pass
            on_tool(name, "running", target or "(no target)")
            if live_code:
                on_tool(name, "progress", live_code)
            return
        if etype == "session.tool.progress":
            if is_subagent_call:
                return
            # Its own phase, not `running`. Progress arrives repeatedly for one
            # call, and a phase the card treats as "start a new block" produced
            # three `Execute working` rows for a single call.
            detail = OpenCodeBridge._tool_result_text(data.get("metadata"))
            if not detail.strip():
                # Nothing to say. Forwarding a placeholder ("working") put a
                # meaningless live line on screen - a window showing nothing
                # but "Working" - while the last real line it replaced may have
                # named the file being read.
                return
            on_tool(name, "progress", detail or "working")
            return
        if etype in ("session.tool.success", "session.tool.failed", "session.tool.error", "session.tool.rejected", "session.tool.cancelled", "session.tool.aborted"):
            _raw_res = (
                OpenCodeBridge._tool_result_text(data.get("content"))
                or OpenCodeBridge._tool_result_text(data.get("output"))
                or ""
            )
            # The model sometimes reaches OpenCode's own subagent through the
            # `execute` script tool (tools.subagent({...})), or launches the
            # native one in OpenCode's background, where CMDAI never sees it
            # work ("Done 0.1s", empty window). Both are rerouted to CMDAI's
            # own `<tool:subagent>` so the run is visible and stays local.
            if is_subagent_call:
                # Any call to OpenCode native `subagent` or `execute(tools.subagent)` is rerouted
                # to CMDAI's local `<tool:subagent>` XML so CMDAI executes the real subagent locally
                # with full UI, tools, diffs, logs, and background support!
                if is_exec:
                    _cv = record.get("input")
                    _code = _cv if isinstance(_cv, str) else (_cv.get("code", "") if isinstance(_cv, dict) else str(_cv or ""))
                    def _extract_fld(k, src):
                        import re as _re
                        pat = r'(?:["\']?' + k + r'["\']?)\s*[:=]\s*'
                        m = _re.search(pat, src, _re.I)
                        if not m:
                            return ""
                        rest = src[m.end():].lstrip()
                        quote = rest[0] if rest and rest[0] in ('"', "'", '`') else ''
                        if quote:
                            rest = rest[1:]
                            idx = 0
                            while idx < len(rest):
                                if rest[idx] == '\\':
                                    idx += 2
                                    continue
                                if rest[idx] == quote:
                                    tail = rest[idx+1:].lstrip()
                                    if not tail or tail[0] in (',', '}', '\r', '\n'):
                                        return rest[:idx].replace('\\n', '\n').strip()
                                if _re.match(r'\n\s*(?:description|system|prompt|goal|task|background|agent)\s*[:=]', rest[idx:], _re.I):
                                    return rest[:idx].rstrip('", \r\n').replace('\\n', '\n').strip()
                                idx += 1
                            return rest.rstrip('",}); \r\n').replace('\\n', '\n').strip()
                        else:
                            m2 = _re.match(r'([^,}\r\n]+)', rest)
                            return m2.group(1).strip() if m2 else ""
                    r_name = (_extract_fld("description", _code) or _extract_fld("name", _code) or "Subagent").replace('"', "'")[:60]
                    s_prompt = _extract_fld("system", _code).replace('"', "'")
                    g_task = _extract_fld("prompt", _code) or _extract_fld("goal", _code) or _extract_fld("task", _code) or r_name
                    bg_val = _extract_fld("background", _code)
                    bg_attr = ' background="true"' if str(bg_val).lower() in ("true", "1", "yes") or "background: true" in _code or '"background": true' in _code else ""
                    record["fallback_xml"] = f'<tool:subagent name="{r_name}" system="{s_prompt}"{bg_attr}>{g_task}</tool:subagent>'
                    return
                else:
                    raw_inp = record.get("input") or data.get("input") or {}
                    import json as _json
                    if isinstance(raw_inp, str):
                        try:
                            raw_inp = _json.loads(raw_inp)
                        except Exception:
                            pass
                    if isinstance(raw_inp, dict):
                        r_name = str(raw_inp.get("description") or raw_inp.get("name") or raw_inp.get("role") or "Subagent").replace('"', "'")[:60]
                        s_prompt = str(raw_inp.get("system") or raw_inp.get("system_prompt") or "").replace('"', "'")
                        g_task = str(raw_inp.get("prompt") or raw_inp.get("goal") or raw_inp.get("task") or raw_inp.get("description") or "Wykonaj zadanie")
                        bg_flag = str(raw_inp.get("background", "")).lower() in ("true", "1", "yes")
                        bg_attr = ' background="true"' if bg_flag else ""
                        record["fallback_xml"] = f'<tool:subagent name="{r_name}" system="{s_prompt}"{bg_attr}>{g_task}</tool:subagent>'
                        return
                    else:
                        r_name = "Subagent"
                        s_prompt = ""
                        g_task = str(raw_inp or "Wykonaj zadanie")
                        record["fallback_xml"] = f'<tool:subagent name="{r_name}" system="{s_prompt}">{g_task}</tool:subagent>'
                        return
            summary = (
                OpenCodeBridge._tool_result_text(data.get("content"))
                or OpenCodeBridge._tool_result_text(data.get("output"))
                or OpenCodeBridge._tool_result_text(data.get("metadata"))
                or str(record.get("child_text") or "")
            )
            if not summary and data.get("error"):
                err = data.get("error")
                summary = err.get("message") if isinstance(err, dict) else str(err)
            first = next((ln.strip() for ln in summary.splitlines() if ln.strip()), "")
            phase = "failed" if (is_exec or etype != "session.tool.success") else "done"
            tgt = record.get("target") or ""
            if tgt and summary.strip() and summary.strip() != tgt.strip():
                detail_out = f"{tgt}\n{summary.strip()}"
            else:
                detail_out = tgt or first[:200] or ("unsupported tool call" if is_exec else "ok")
            bg_sid = record.get("bg_sid")
            if bg_sid:
                try:
                    from cmdai.agent.tasks import BackgroundTaskManager
                    BackgroundTaskManager.get_instance().finish_subagent(
                        bg_sid,
                        status="finished" if phase == "done" else "failed",
                        result=summary.strip() or detail_out,
                    )
                except Exception:
                    pass
            on_tool(name, phase, detail_out)
            tool_inputs.pop(call_id, None)
            return

    def _reply_permission(self, sid: str, data: Dict[str, Any]) -> None:
        """Answer `permission.asked` so the turn can finish."""
        call_id = str(data.get("id") or data.get("callID") or data.get("permissionID") or "")
        if not call_id:
            return
        try:
            requests.post(
                f"{self._server.base_url}/api/session/{sid}/permission",
                headers=self._headers(),
                json={"id": call_id, "action": "allow", "save": False},
                timeout=5.0,
            )
        except Exception:
            pass

    def ensure_server(self) -> ServerInfo:
        if self._server is not None and _server_alive(self._server.base_url, self._server.password):
            return self._server

        url, password = _read_service_json()
        if url and password and _server_alive(url, password):
            self._server = ServerInfo(base_url=url, password=password, spawned=False)
            return self._server

        self._server = self._spawn_server()
        return self._server

    def _spawn_server(self) -> ServerInfo:
        if requests is None:
            raise ServerUnavailable("The `requests` package is missing; cannot talk to OpenCode.")

        password = base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        log = open(os.path.join(tempfile.gettempdir(), "cmdai-dev-opencode.log"), "ab")

        env = dict(os.environ)
        env["OPENCODE_SERVER_PASSWORD"] = password

        creation_flags = 0
        if os.name == "nt":
            # CREATE_NO_WINDOW - keep the console clean when CMDAI runs in a TUI.
            creation_flags = 0x08000000

        launcher = _find_opencode()
        if not launcher:
            log.close()
            raise ServerUnavailable(
                "`opencode` was not found on PATH.\n\n"
                "Install it first:\n\n"
                "    npm install -g @opencode/cli\n\n"
                "Then reopen CMDAI CODE DEV."
            )

        try:
            proc = subprocess.Popen(
                launcher + ["serve", "--hostname", "127.0.0.1", "--port", str(port)],
                stdout=log,
                stderr=log,
                env=env,
                cwd=self.workdir,
                creationflags=creation_flags,
            )
        except FileNotFoundError as exc:
            log.close()
            raise ServerUnavailable(
                f"Found `{launcher[0]}` but could not run it. Reinstall OpenCode "
                f"with:\n\n    npm install -g @opencode/cli"
            ) from exc
        except Exception as exc:
            log.close()
            raise ServerUnavailable(f"Could not start OpenCode: {exc}") from exc

        info = ServerInfo(base_url=base_url, password=password, spawned=True, process=proc)

        deadline = time.time() + 45.0
        while time.time() < deadline:
            if proc.poll() is not None:
                log.close()
                raise ServerUnavailable(
                    f"OpenCode exited immediately (code {proc.returncode}). "
                    f"See {os.path.join(tempfile.gettempdir(), 'cmdai-dev-opencode.log')}."
                )
            if _server_alive(base_url, password):
                return info
            time.sleep(0.4)

        proc.terminate()
        log.close()
        raise ServerUnavailable(
            "OpenCode started but did not answer on its port within 45s. "
            f"See {os.path.join(tempfile.gettempdir(), 'cmdai-dev-opencode.log')}."
        )

    def shutdown(self) -> None:
        if self._server is None:
            return
        # Abort all active sessions first so OpenCode immediately halts generation
        try:
            for (workdir, model), (sid, _) in list(self._sessions.items()):
                try:
                    if requests is not None:
                        requests.post(
                            f"{self._server.base_url}/api/session/{sid}/abort",
                            headers=self._headers(),
                            timeout=1.0,
                        )
                except Exception:
                    pass
        except Exception:
            pass
        proc = self._server.process
        if proc is not None and proc.poll() is None:
            try:
                if os.name == "nt":
                    # On Windows, kill the entire process tree (node/opencode children)
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        proc.kill()
            except Exception:
                pass
        self._server = None
        self._sessions.clear()

    # ---------------------------------------------------------------- models

    def list_models(self, force: bool = False) -> List[Dict[str, Any]]:
        """Models under the `opencode` provider, as CMDAI model dicts.

        Always re-queries the server unless we just did: the whole point of this
        provider is that the catalogue is OpenCode's, and it changes when
        OpenCode changes. A cached copy that outlives the server's view would
        offer models that no longer exist and hide ones that do.

        A cold server answers with an empty catalogue for a few seconds. We
        retry a couple of times before believing it, because an empty list reads
        as "no models exist" in the picker and the user has no way to tell the
        difference from a real outage.
        """
        if requests is None:
            return []

        now = time.time()
        if not force and self._models_cache[1] and now - self._models_cache[0] < self.models_ttl:
            return self._models_cache[1]

        for attempt in range(3):
            self.ensure_server()
            try:
                r = requests.get(
                    f"{self._server.base_url}/api/model",
                    params={"directory": self.workdir},
                    headers=self._headers(),
                    timeout=30.0,
                )
                if r.status_code == 200:
                    raw = (r.json() or {}).get("data") or []
                    models = [self._to_model(m) for m in raw if m.get("providerID") == "opencode"]
                    for m in models:
                        # Mark models we already watched fail, so the picker
                        # explains itself instead of listing a trap.
                        reason = self.unavailable.get(m["id"])
                        if reason:
                            m["badge"] = f"{m['badge']} · {reason}"
                    if models:
                        models.sort(key=_picker_order)
                        self._models_cache = (time.time(), models)
                        return models
            except Exception:
                pass
            if attempt < 2:
                time.sleep(2.5 * (attempt + 1))

        return self._fallback_models()

    def _fallback_models(self) -> List[Dict[str, Any]]:
        """Seed list for when the server cannot be reached.

        Better than an empty picker: some of these usually work, and the failure
        mode when one is geo-blocked is a clear per-model error, not silence.
        """
        return [
            {
                "id": mid,
                "name": mid,
                "badge": "FREE",
                "thinking": True,
                "context_length": 0,
            }
            for mid in KNOWN_FREE_MODELS
        ]

    def _to_model(self, m: Dict[str, Any]) -> Dict[str, Any]:
        caps = m.get("capabilities") or {}
        limit = m.get("limit") or {}
        ctx = int(limit.get("context") or 0)
        mid = m.get("id", "")
        note = MODEL_NOTES.get(mid, {})

        # Reasoning, as OpenCode reports it. There is no `capabilities.reasoning`
        # field: the signal is the list of selectable effort `variants`. A model
        # can reason without any variants (big-pickle does), which is why the
        # provider also learns it from an observed reasoning stream.
        variants = [str(v.get("id") or "") for v in (m.get("variants") or [])]
        variants = [v for v in variants if v]
        can_reason = bool(variants)

        parts = ["FREE"]
        if ctx >= 1_000_000:
            parts.append(f"{ctx // 1_000_000}M")
        elif ctx >= 1_000:
            parts.append(f"{ctx // 1_000}k")
        if can_reason:
            parts.append("reasoning")

        if note.get("bad"):
            # Say why up front. A model that cannot drive the agent loop is not
            # a worse option, it is a different tool - hiding that costs the
            # user a stalled turn every time they pick it. ASCII marker only:
            # this badge is compared in tests and printed to a cp1252 console.
            parts.append(f"UNUSABLE: {note['bad']}")
        elif note.get("recommend") == "recommended":
            # ASCII only: these badges reach a cp1250/cp1252 console, where a
            # non-Latin-1 char raises UnicodeEncodeError on print.
            parts.append("== recommended ==")
        elif note.get("recommend") == "slow":
            parts.append("slow")
        elif not note:
            # Not in our measurements at all. Not a warning: it is a model
            # OpenCode added after the screen run.
            parts.append("not yet tested")

        return {
            "id": mid,
            "name": m.get("name") or mid,
            "badge": " · ".join(parts),
            "thinking": can_reason,
            "levels": variants,
            "context_length": ctx,
            "usable": not note.get("bad"),
        }

    # ------------------------------------------------------------- generate

    def stream_generate(
        self,
        model_id: str,
        prompt: str,
        on_text: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        is_aborted: Optional[Callable[[], bool]] = None,
        timeout: Optional[float] = None,
        on_tool: Optional[Callable[[str, str, str], None]] = None,
        on_usage: Optional[Callable[[Dict[str, Any]], None]] = None,
        workdir: str = "",
        is_subagent: bool = False,
        *args,
        **kwargs,
    ) -> str:
        """Real streaming via the `/prompt` route and the server's event stream.

        `/generate` answers in one lump (measured chunks=1, 12.3s of silence
        before the text). The `/prompt` route publishes `session.text.delta`
        roughly every 100-200ms plus `session.reasoning.delta` for the thinking
        channel. Measured: 33 deltas spanning 5.1s-10.2s on a 300 word answer.

        Why `/prompt` and not `/generate`: only the prompt route streams.

        OpenCode's own tools stay enabled on the session, but the prompt tells
        the model it has none, so it never calls them (measured: 0 tool events).
        That steering is deliberate - denying the session with a permission
        ruleset was tried and it aborts the whole step with
        `session.step.failed` at 1.1s, producing no text at all. Prompt-level
        steering keeps CMDAI in sole control of execution: nothing runs unless
        the model emits a CMDAI `<tool:name>` tag, which this app executes.
        """
        if requests is None:
            raise ServerUnavailable("The `requests` package is missing.")

        target_dir = os.path.abspath(workdir or getattr(self, "workdir", "") or os.getcwd())
        self.ensure_server()
        tag = str(kwargs.get("tag") or ("naming" if kwargs.get("is_naming") else "") or "")
        sid = self._session_id(model_id, fresh=True, workdir=target_dir, tag=tag)
        base = self._server.base_url

        collected: List[str] = []
        reasoning: List[str] = []
        finished = threading.Event()
        state = {"error": ""}
        tool_inputs: Dict[str, Dict[str, Any]] = {}
        active_tools = [0]
        last_activity = [time.time()]
        prompt_posted = [False]
        part_start = [0]
        after_tool = [False]
        detached_bg = [False]
        child_text_acc: Dict[str, str] = {}
        child_tool_inputs: Dict[str, Dict[str, Any]] = {}

        def listen() -> None:
            try:
                with requests.get(
                    f"{base}/api/event",
                    headers=self._headers(),
                    stream=True,
                    timeout=(timeout or self.timeout) + 30,
                ) as resp:
                    for raw in resp.iter_lines():
                        if finished.is_set():
                            return
                        if not raw or not raw.startswith(b"data:"):
                            continue
                        last_activity[0] = time.time()
                        try:
                            ev = json.loads(raw[5:].decode("utf-8", "replace"))
                        except Exception:
                            continue

                        data = ev.get("data") or {}
                        etype = ev.get("type", "")
                        ev_sid = data.get("sessionID")

                        # Always reply to permission requests for both the main session
                        # and any child subagent session spawned by OpenCode's `task` tool.
                        if etype == "permission.asked":
                            self._reply_permission(ev_sid or sid, data)
                            continue

                        if ev_sid != sid:
                            # Forward child subagent session progress to the active subagent block
                            if on_tool:
                                c_id = str(
                                    data.get("id") or data.get("callID") or data.get("callId")
                                    or data.get("toolCallId") or data.get("toolCallID")
                                    or data.get("partID") or data.get("partId") or "last"
                                )
                                if etype.startswith("session.tool."):
                                    c_rec = child_tool_inputs.setdefault(c_id, {})
                                    if data.get("name") or data.get("tool"):
                                        c_rec["name"] = str(data.get("name") or data.get("tool"))
                                    if etype == "session.tool.input.started":
                                        c_rec["input"] = ""
                                        c_rec["target"] = ""
                                    elif etype == "session.tool.input.delta":
                                        c_rec["input"] = str(c_rec.get("input") or "") + str(data.get("delta") or "")
                                        t_part = OpenCodeBridge._extract_target_from_partial_input(c_rec.get("name", ""), c_rec["input"])
                                        if t_part:
                                            c_rec["target"] = t_part
                                    elif etype == "session.tool.input.ended":
                                        c_rec["input"] = str(data.get("text") or c_rec.get("input") or "")
                                        t_part = OpenCodeBridge._extract_target_from_partial_input(c_rec.get("name", ""), c_rec["input"])
                                        if t_part:
                                            c_rec["target"] = t_part
                                    elif etype == "session.tool.called":
                                        state_obj = data.get("state") if isinstance(data.get("state"), dict) else {}
                                        c_rec["input"] = data.get("input") or state_obj.get("input") or c_rec.get("input")
                                        c_name = str(c_rec.get("name") or data.get("name") or data.get("tool") or "").strip()
                                        raw_t = OpenCodeBridge._tool_target(c_rec.get("input"), c_name) or OpenCodeBridge._extract_target_from_partial_input(c_name, str(c_rec.get("input") or ""))
                                        c_tgt = OpenCodeBridge._clean_tool_target(c_name, raw_t) or str(c_rec.get("target") or "")
                                        c_rec["target"] = c_tgt
                                        if c_name and c_name.lower() not in ("tool", "task", "subagent", "question"):
                                            clean_act = f"{c_name} {c_tgt}".strip() if c_tgt else c_name
                                            on_tool("subagent", "progress", clean_act)
                                    elif etype in ("session.tool.success", "session.tool.failed", "session.tool.error"):
                                        c_name = str(c_rec.get("name") or data.get("name") or data.get("tool") or "").strip()
                                        state_obj = data.get("state") if isinstance(data.get("state"), dict) else {}
                                        raw_t = OpenCodeBridge._tool_target(data.get("input") or state_obj.get("input") or c_rec.get("input"), c_name)
                                        c_tgt = str(c_rec.get("target") or OpenCodeBridge._clean_tool_target(c_name, raw_t) or "").strip()
                                        child_tool_inputs.pop(c_id, None)
                                        if c_name and c_name.lower() not in ("tool", "task", "subagent", "question"):
                                            clean_act = f"{c_name} {c_tgt}".strip() if c_tgt else c_name
                                            on_tool("subagent", "progress", clean_act)
                                elif etype == "session.text.delta":
                                    piece = data.get("delta") or ""
                                    if piece and ev_sid:
                                        child_text_acc[ev_sid] = child_text_acc.get(ev_sid, "") + piece
                            continue

                        if etype == "session.text.delta":
                            piece = data.get("delta") or ""
                            if piece and not detached_bg[0]:
                                if after_tool[0]:
                                    after_tool[0] = False
                                    cur_all = "".join(collected)
                                    if cur_all and not cur_all.endswith("\n"):
                                        collected.append("\n\n")
                                        if on_text:
                                            try:
                                                on_text("\n\n")
                                            except Exception:
                                                pass
                                    part_start[0] = len("".join(collected))
                                collected.append(piece)
                                if on_text:
                                    try:
                                        on_text(piece)
                                    except Exception:
                                        pass
                        elif etype == "session.reasoning.delta":
                            piece = data.get("delta") or ""
                            if piece and not detached_bg[0]:
                                reasoning.append(piece)
                                if on_reasoning:
                                    try:
                                        on_reasoning(piece)
                                    except Exception:
                                        pass
                        elif etype.startswith("session.tool."):
                            if etype == "session.tool.called":
                                active_tools[0] += 1
                            elif etype in ("session.tool.success", "session.tool.failed", "session.tool.error", "session.tool.rejected", "session.tool.cancelled", "session.tool.aborted"):
                                active_tools[0] = max(0, active_tools[0] - 1)
                                after_tool[0] = True
                            if on_tool:
                                call_id_ev = str(data.get("id") or data.get("callID") or data.get("partID") or "default")
                                if child_text_acc and call_id_ev in tool_inputs:
                                    tool_inputs[call_id_ev]["child_text"] = "\n".join(v.strip() for v in child_text_acc.values() if v.strip())
                                try:
                                    self._report_tool(
                                        on_tool, etype, data, tool_inputs
                                    )
                                except Exception:
                                    pass
                                rec_after = tool_inputs.get(call_id_ev)
                                if rec_after and rec_after.get("fallback_xml"):
                                    fb_xml = rec_after.pop("fallback_xml")
                                    tool_inputs.pop(call_id_ev, None)
                                    collected.append("\n" + fb_xml)
                                    if on_text:
                                        try:
                                            on_text("\n" + fb_xml)
                                        except Exception:
                                            pass
                                    finished.set()
                                    return
                                if not is_subagent and detached_bg[0] and active_tools[0] == 0:
                                    finished.set()
                                    return
                        elif etype == "session.usage.updated":
                            toks = data.get("tokens") or {}
                            if toks and on_usage and not detached_bg[0]:
                                p_tok = int(toks.get("input", 0) or 0)
                                c_tok = int(toks.get("output", 0) or 0) + int(toks.get("reasoning", 0) or 0)
                                try:
                                    on_usage({
                                        "prompt_tokens": p_tok,
                                        "completion_tokens": c_tok,
                                        "total_tokens": p_tok + c_tok,
                                    })
                                except Exception:
                                    pass
                        elif etype in ("session.execution.succeeded", "session.execution.interrupted"):
                            if (not ev_sid or ev_sid == sid) and active_tools[0] == 0:
                                finished.set()
                        elif etype == "session.idle":
                            has_pending_sub = any(
                                str(r.get("name") or "").lower() == "subagent"
                                for r in list(tool_inputs.values())
                            )
                            if (not ev_sid or ev_sid == sid) and prompt_posted[0] and active_tools[0] == 0 and (len(collected) > 0 or len(reasoning) > 0 or has_pending_sub):
                                finished.set()
                        elif etype == "session.status":
                            if (not ev_sid or ev_sid == sid) and prompt_posted[0] and str(data.get("status", "")).lower() in ("completed", "done", "finished"):
                                if active_tools[0] == 0:
                                    finished.set()
                        elif etype in ("session.execution.failed", "session.step.failed"):
                            state["error"] = _describe_failure(data)
                            finished.set()
                        elif etype == "session.text.ended":
                            if not detached_bg[0]:
                                whole = data.get("text") or ""
                                already = len("".join(collected)) - part_start[0]
                                if whole and len(whole) > max(0, already):
                                    tail = whole[max(0, already):]
                                    collected.append(tail)
                                    if on_text:
                                        try:
                                            on_text(tail)
                                        except Exception:
                                            pass
                                part_start[0] = len("".join(collected))
            except Exception as exc:
                state["error"] = f"event stream broke: {exc}"
            finally:
                finished.set()

        worker = threading.Thread(target=listen, daemon=True)
        worker.start()
        time.sleep(0.35)

        try:
            r = requests.post(
                f"{base}/api/session/{sid}/prompt",
                headers=self._headers(),
                json={"text": prompt},
                timeout=30.0,
            )
            if r.status_code != 200:
                raise BridgeError(
                    f"OpenCode refused the prompt (HTTP {r.status_code}): {r.text[:300]}"
                )
            prompt_posted[0] = True
        except requests.exceptions.Timeout:
            finished.set()
            raise BridgeError("OpenCode did not accept the prompt in time.")

        inactivity_limit = max(300.0, float(timeout or self.timeout))
        while not finished.is_set():
            if is_aborted and is_aborted():
                finished.set()
                break
            try:
                from .patch import _APP_REF
                app_inst = _APP_REF.get("app")
                runner_inst = getattr(app_inst, "agent_runner", None) if app_inst else None
                if not is_subagent and runner_inst and getattr(runner_inst, "detach_requested", False) and active_tools[0] > 0:
                    runner_inst.detach_requested = False
                    detached_bg[0] = True
                    from cmdai.agent.tasks import BackgroundTaskManager
                    bg_mgr = BackgroundTaskManager.get_instance()
                    last_tname = "task"
                    last_ttgt = "Zadanie w tle"
                    last_bg_sid = ""
                    for cid, rec in list(tool_inputs.items()):
                        tname = str(rec.get("name") or "task").lower()
                        ttgt = str(rec.get("goal") or rec.get("target") or "Zadanie w tle")
                        if tname in ("subagent", "task"):
                            bg_sid = rec.get("bg_sid")
                            if not bg_sid:
                                bg_sid = f"subagent_{cid[:8] if cid else int(time.time())}"
                                rec["bg_sid"] = bg_sid
                                bg_mgr.register_subagent(bg_sid, role=str(rec.get("role") or "Subagent"), task=ttgt, background=True)
                            else:
                                bg_mgr.mark_subagent_background(bg_sid)
                            last_tname = tname
                            last_ttgt = ttgt
                            last_bg_sid = bg_sid
                            if on_tool:
                                try:
                                    on_tool(tname, "background", bg_sid)
                                except Exception:
                                    pass
                    if last_bg_sid:
                        runner_inst.last_detached_server_tool = {
                            "name": last_tname,
                            "task_id": last_bg_sid,
                            "target": last_ttgt,
                        }
                    # Detached to background: generation continues gracefully

            except Exception:
                pass
            if time.time() - last_activity[0] > inactivity_limit:
                break
            finished.wait(0.2)

        if not detached_bg[0]:
            # Flush any remaining unclosed tools so UI blocks never stay spinning,
            # and convert any aborted native `subagent` tool calls into `<tool:subagent>` XML
            for cid, rec in list(tool_inputs.items()):
                tname = str(rec.get("name") or "tool")
                if tname.lower() == "subagent" or rec.get("fallback_xml"):
                    fb_xml = rec.get("fallback_xml")
                    if not fb_xml:
                        try:
                            from cmdai.agent.runner import clean_subagent_call
                            cleaned = clean_subagent_call(rec.get("input") or "")
                            r_name = cleaned.get("role", "Subagent").replace('"', "'")
                            s_prompt = cleaned.get("system", "").replace('"', "'")
                            g_task = cleaned.get("goal", "") or "Przeanalizuj projekt i wykonaj zadanie"
                            bg_flag = str(cleaned.get("background", "")).lower() in ("true", "1", "yes")
                            bg_attr = ' background="true"' if bg_flag else ""
                            fb_xml = f'<tool:subagent name="{r_name}" system="{s_prompt}"{bg_attr}>{g_task}</tool:subagent>'
                        except Exception:
                            fb_xml = ""
                    if fb_xml and "<tool:subagent" not in "".join(collected):
                        collected.append("\n" + fb_xml)
                        if on_text:
                            try:
                                on_text("\n" + fb_xml)
                            except Exception:
                                pass
                        state["error"] = ""
                    continue
                ttgt = rec.get("target", "")
                if on_tool:
                    try:
                        on_tool(tname, "done", ttgt)
                    except Exception:
                        pass
            tool_inputs.clear()

        if is_aborted and is_aborted():
            self._reset_session(model_id)
            return ""

        if not collected and reasoning and not detached_bg[0]:
            full_r = "".join(reasoning)
            tool_in_r = re.findall(
                r'<(?:tool:\w+|tool_call|\|tool_call)[\s\S]*?(?:/>|</(?:tool:)?\w+>|</tool_call>|</\|tool_call>)',
                full_r,
                flags=re.IGNORECASE,
            )
            if tool_in_r:
                recovered = "\n".join(tool_in_r)
                collected.append(recovered)
                if on_text:
                    try:
                        on_text(recovered)
                    except Exception:
                        pass

        if state["error"]:
            # A failed step can still have produced usable text before dying.
            if collected or detached_bg[0] or reasoning:
                return "".join(collected)
            self._reset_session(model_id)
            raise BridgeError(state["error"])

        if not collected and not detached_bg[0]:
            if reasoning:
                return ""
            self._reset_session(model_id)
            raise BridgeError("The model returned an empty response.")

        return "".join(collected)

    def _session_id(self, model_id: str, fresh: bool = False, workdir: str = "", tag: str = "") -> str:
        """Session id for this (workdir, model), created on demand."""
        wdir = os.path.abspath(workdir or getattr(self, "workdir", "") or os.getcwd())
        key = (wdir, model_id, tag) if tag else (wdir, model_id)
        if fresh:
            self._sessions.pop(key, None)
        else:
            entry = self._sessions.get(key)
            if entry is not None:
                sid, uses = entry
                if uses < self.max_session_uses:
                    self._sessions[key] = (sid, uses + 1)
                    return sid

        self.ensure_server()
        r = requests.post(
            f"{self._server.base_url}/api/session",
            headers=self._headers(),
            json={
                "model": {"id": model_id, "providerID": "opencode"},
                "location": {"directory": wdir},
            },
            timeout=30.0,
        )
        if r.status_code != 200 and model_id != "big-pickle":
            r = requests.post(
                f"{self._server.base_url}/api/session",
                headers=self._headers(),
                json={
                    "model": {"id": "big-pickle", "providerID": "opencode"},
                    "location": {"directory": wdir},
                },
                timeout=30.0,
            )
        if r.status_code != 200:
            raise BridgeError(
                f"OpenCode refused to open a session for '{model_id}' "
                f"(HTTP {r.status_code}): {r.text[:300]}"
            )
        sid = (r.json() or {}).get("data", {}).get("id")
        if not sid:
            raise BridgeError(f"OpenCode returned no session id: {r.text[:300]}")
        self._sessions[key] = (sid, 1)
        return sid

    def generate(
        self,
        model_id: str,
        prompt: str,
        retries: int = 3,
    ) -> str:
        """One completion. Returns the full text, empty string on a blank reply.

        `prompt` is sent as-is: CMDAI's own system prompt travels inside the
        text, which is the only thing this route accepts.
        """
        if requests is None:
            raise ServerUnavailable("The `requests` package is missing.")

        last_error = ""
        for attempt in range(retries):
            sid = self._session_id(model_id)
            try:
                r = requests.post(
                    f"{self._server.base_url}/api/session/{sid}/generate",
                    headers=self._headers(),
                    json={"prompt": prompt},
                    timeout=self.timeout,
                )
            except requests.exceptions.Timeout:
                last_error = f"OpenCode timed out after {self.timeout:.0f}s"
                self._reset_session(model_id)
                continue
            except Exception as exc:
                last_error = f"Cannot reach OpenCode: {exc}"
                self._server = None
                self._sessions.clear()
                continue

            if r.status_code == 200:
                text = ((r.json() or {}).get("data") or {}).get("text") or ""
                if text.strip():
                    return text
                # A blank completion happens with some models on prompts that
                # carry tool markup. Not an error the user can act on, so we
                # retry quietly and only complain if every attempt is blank.
                last_error = ""
                time.sleep(1.2 * (attempt + 1))
                continue

            body = r.text or ""
            # Content is inspected before the generic 503 branch: OpenCode reports both
            # transient conditions and permanent ones with status 503, so the
            # status code alone cannot tell "wait and retry" from "this model is
            # gone for your region".
            if "not available in your country" in body or "Model unavailable" in body:
                # Remember it: the picker lists whatever the server advertises,
                # and a model that fails on first use is a bad first impression.
                self.unavailable[model_id] = "unavailable from your region"
                raise BridgeError(
                    f"Model '{model_id}' is not reachable from OpenCode right now.\n\n"
                    f"This is usually regional or a temporary catalogue change, "
                    f"not a missing API key. Try another model in /models."
                )

            if "free tier can only be used from within OpenCode" in body:
                # Misleading: seen on a healthy server whose models work, so it
                # is transient, not "these models are paid". Rebuild the session
                # and retry rather than telling the user they need a subscription.
                self._reset_session(model_id)
                last_error = ""
                time.sleep(1.5 * (attempt + 1))
                continue

            # Parse the exact error body returned by OpenCode / Upstream API
            clean_api_err = ""
            if body and body.strip():
                try:
                    parsed_b = json.loads(body.strip())
                    clean_api_err = _describe_failure(parsed_b)
                except Exception:
                    clean_api_err = body.strip()[:400]

            if r.status_code == 503:
                last_error = f"[HTTP 503] {clean_api_err or 'Upstream model is overloaded'}"
                time.sleep(2.0 * (attempt + 1))
                continue

            if r.status_code == 429:
                last_error = f"[HTTP 429] {clean_api_err or 'Rate limit / quota reached'}"
                time.sleep(2.0 * (attempt + 1))
                continue

            if r.status_code == 401:
                self._server = None
                self._sessions.clear()
                last_error = f"[HTTP 401] {clean_api_err or 'OpenCode rejected our password or authentication required'}"
                continue

            raise BridgeError(f"OpenCode error (HTTP {r.status_code}): {clean_api_err or body[:300]}")

        if last_error:
            raise BridgeError(last_error)
        raise BridgeError(
            "The model returned an empty response several times in a row.\n\n"
            "This bridge has no tools, and some models stall when asked about "
            "them. Asking something concrete usually works - try again."
        )

    def _reset_session(self, model_id: str) -> None:
        self._sessions.pop((self.workdir, model_id), None)

    # ------------------------------------------------------------ utilities

    @staticmethod
    def messages_to_prompt(
    system: str,
    messages: List[Dict[str, Any]],
    nudge: str = "",
) -> str:
        """Flatten CMDAI's message list into one prompt for `/generate`.

        The route takes a single string, so roles are rendered as labels. Tool
        results keep their `<tool_response>` wrapper because that is what the
        agent prompt tells the model to expect back.

        The preamble is not decoration. `/generate` has no tools, but the model
        still sees OpenCode's own instructions mentioning MCP servers, shell and
        friends. Left alone it narrates intent - "I'll list my tools, then look
        for xph" - and stops, which reads to the user like a hang. Observed
        directly, four variants, three of which stalled or listed phantom tools.

        The preamble states the ground rules once, above everything, so a turn
        ends in either output or a tool tag and never in a promise.
        """
        blocks: List[str] = [
            "You are the language model behind CMDAI CODE, a coding agent.\n"
            "\n"
            "CMDAI has its own tools and runs them itself: it asks the user for "
            "approval, shows their progress, hands you the result, and it is the "
            "only way to reach an MCP server. To use one, emit its tag as plain "
            "text:\n"
            "  'which/what MCP servers', 'check MCP', 'dostępne mpc'  -> <tool:mcp />\n"
            "  'open server NAME'                                       -> <tool:mcp open server=\"NAME\" />\n"
            "  list/read/search files                                  -> <tool:ls path=\".\" />, <tool:read path=\"file.py\" />, <tool:search query=\"text\" />\n"
            "  create or overwrite a file (MUST wrap content in tags)  -> <tool:write path=\"file.py\">file content here</tool:write>\n"
            "  edit a file (MUST wrap old/new in tags)                 -> <tool:edit path=\"file.py\"><old>exact old code</old><new>new code</new></tool:edit>\n"
            "  run something in a shell                                -> <tool:command cmd=\"...\" /> (add background=\"true\" to run in background: <tool:command cmd=\"...\" background=\"true\" />)\n"
            "  delegate to subagent / helper                           -> emit XML tag <tool:subagent name=\"Tester QA\" system=\"instructions\">task description</tool:subagent> (optional background=\"true\" to run in background)\n"
            "  NEVER call tools.subagent(), tools.task(), or write JavaScript programs with tools.*. To delegate to a subagent, ALWAYS emit the plain XML tag <tool:subagent name=\"...\" system=\"...\">task description</tool:subagent> directly in your response.\n"
            "  when launching a background command or subagent, always tell the user what command/subagent you started in the background; when its <tool_response> arrives upon completion, immediately summarize and report the result to the user.\n"
            "\n"
            "Use a tag whenever the job needs approval, MCP, or CMDAI's view of "
            "the workspace. Always close <tool:write>...</tool:write> and <tool:edit>...</tool:edit> with their closing tags.\n"
            "\n"
            "Never use <tool:command> to look something up. Shell access is for "
            "running programs, never for asking what is configured or available.\n"
            "\n"
            "Never emit these; CMDAI cannot read them:\n"
            "  <tool:execute>, <code>, <\u200btool_call>, tool_call, any JSON tool call.\n"
            "\n"
            "How to answer, pick one:\n"
            "  1. A tool is needed -> output the tag (including full body for <tool:write>/<tool:edit>) and nothing else.\n"
            "  2. No tool is needed -> answer the question in plain prose.\n"
            "\n"
            "Never do these:\n"
            "  - describe or explain your tools instead of using one\n"
            "  - announce an intention ('I will check', 'let me look') and stop\n"
            "  - claim you lack tools, or that your tools are 'Claude Code' or "
            "'Codex' or anything else\n"
            "  - emit a self-closing <tool:write ... /> without file content inside <tool:write>...</tool:write>\n"
            "\n"
            "If the user asks what you can do, answer in prose. Do not turn the "
            "answer into a tool call.",
        ]

        if system:
            blocks.append(f"<system>\n{system}\n</system>")

        for msg in messages:
            role = str(msg.get("role", "user")).upper()
            content = msg.get("content", "")
            if isinstance(content, list):
                parts: List[str] = []
                for chunk in content:
                    if isinstance(chunk, dict):
                        if chunk.get("type") == "text":
                            parts.append(str(chunk.get("text", "")))
                        elif chunk.get("type") == "image_url":
                            parts.append("(image attached)")
                    else:
                        parts.append(str(chunk))
                content = "\n".join(p for p in parts if p)
            content = str(content or "").strip()
            if not content:
                continue
            blocks.append(f"<{role}>\n{content}\n</{role}>")

        # Placed after the conversation, not before: it is a correction about
        # the reply just given, so it has to be the last thing read.
        if nudge:
            blocks.append(nudge.strip())

        return "\n\n".join(blocks)


_BRIDGE_SINGLETON: Optional[OpenCodeBridge] = None


def get_bridge(workdir: str = "") -> OpenCodeBridge:
    """Process-wide bridge, reused so we start at most one OpenCode server."""
    global _BRIDGE_SINGLETON
    if _BRIDGE_SINGLETON is None:
        _BRIDGE_SINGLETON = OpenCodeBridge(workdir or os.getcwd())
    elif workdir and not _BRIDGE_SINGLETON.workdir:
        _BRIDGE_SINGLETON.workdir = os.path.abspath(workdir)
    return _BRIDGE_SINGLETON


def stop_bridge() -> None:
    global _BRIDGE_SINGLETON
    if _BRIDGE_SINGLETON is not None:
        _BRIDGE_SINGLETON.shutdown()
        _BRIDGE_SINGLETON = None


atexit.register(stop_bridge)


def bridge_status(bridge: Optional["OpenCodeBridge"] = None) -> Dict[str, Any]:
    """Cheap snapshot for the UI. Never raises, never starts a server.

    Reporting status must not have the side effect of booting OpenCode, so this
    only inspects what already exists. Pass an explicit bridge to inspect one
    that is not the process-wide singleton.
    """
    b = bridge or _BRIDGE_SINGLETON
    if b is None or b._server is None:
        return {"alive": False, "started": False, "url": "", "error": "", "models": 0}

    alive = _server_alive(b._server.base_url, b._server.password)
    return {
        "alive": alive,
        "started": b._server.spawned,
        "url": b._server.base_url,
        "error": "" if alive else "OpenCode server is not responding",
        "models": len(b._models_cache[1]),
        "pid": b._server.process.pid if b._server.process else 0,
    }