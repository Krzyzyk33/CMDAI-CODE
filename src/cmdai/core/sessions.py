import json
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional


class SessionManager:

    def __init__(self, conversations_dir: Optional[str] = None, project_dir: Optional[str] = None):
        here = os.path.dirname(os.path.abspath(__file__))
        self.repo_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        self.repo_sessions_dir = os.path.join(self.repo_root, "app", "sessions")

        if project_dir:
            self.project_dir = os.path.abspath(project_dir)
        else:
            self.project_dir = os.path.abspath(os.getcwd())

        self.conversations_dir = self.repo_sessions_dir
        os.makedirs(self.conversations_dir, exist_ok=True)
        self._migrate_project_sessions()

    def _migrate_project_sessions(self) -> None:
        try:
            legacy = os.path.join(self.project_dir, "app", "sessions")
            if os.path.abspath(legacy) == os.path.abspath(self.repo_sessions_dir):
                return
            if not os.path.isdir(legacy):
                return
            for fname in os.listdir(legacy):
                if not fname.endswith(".json"):
                    continue
                src = os.path.join(legacy, fname)
                dst = os.path.join(self.repo_sessions_dir, fname)
                if not os.path.exists(dst):
                    try:
                        shutil.copy2(src, dst)
                    except Exception:
                        pass
        except Exception:
            pass

    def _get_candidate_dirs(self) -> List[str]:
        dirs = [
            self.repo_sessions_dir,
            os.path.join(os.path.expanduser("~"), ".cmdai", "sessions"),
            os.path.join(os.path.expanduser("~"), ".cmdai_code", "sessions"),
        ]
        return [d for d in dirs if d and os.path.exists(d)]

    def save_session(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        model_id: str,
        title: str = "",
        stats: Optional[Dict[str, Any]] = None,
        transcript: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        if not session_id:
            session_id = f"session_{int(time.time())}"

        data = {
            "id": session_id,
            "title": title or f"Session {session_id}",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model_id": model_id,
            "messages": messages,
            "stats": stats or {},
        }
        # The display record, kept beside the conversation rather than instead
        # of it. `messages` is what the model saw: it interleaves real user
        # turns with raw tool feedback and says nothing about how any of it was
        # rendered. Replaying from it alone shows tool output as if the user had
        # typed it, and cannot bring back the tool cards at all.
        if transcript:
            data["transcript"] = transcript
        try:
            os.makedirs(self.repo_sessions_dir, exist_ok=True)
            with open(os.path.join(self.repo_sessions_dir, f"{session_id}.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def list_sessions(self) -> List[Dict[str, Any]]:
        sessions = []
        seen_ids = set()
        for s_dir in self._get_candidate_dirs():
            try:
                entries = sorted(os.listdir(s_dir), reverse=True)
            except Exception:
                continue
            for fname in entries:
                if fname.endswith(".json"):
                    sid = fname[:-5]
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)
                    try:
                        with open(os.path.join(s_dir, fname), "r", encoding="utf-8") as f:
                            data = json.load(f)
                            sessions.append({
                                "id": data.get("id", sid),
                                "title": data.get("title", fname),
                                "created_at": data.get("created_at", ""),
                                "model_id": data.get("model_id", ""),
                                "message_count": len(data.get("messages", [])),
                            })
                    except Exception:
                        pass
        return sessions

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        for s_dir in self._get_candidate_dirs():
            path = os.path.join(s_dir, f"{session_id}.json")
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return None

    def delete_session(self, session_id: str) -> bool:
        deleted = False
        for s_dir in self._get_candidate_dirs():
            path = os.path.join(s_dir, f"{session_id}.json")
            if os.path.exists(path):
                try:
                    os.remove(path)
                    deleted = True
                except Exception:
                    pass
        return deleted

    def load_transcript(self, session_id: str) -> List[Dict[str, Any]]:
        """The display record for a session, rebuilt if it has none.

        Sessions saved before the transcript existed have to replay somehow,
        and the only source is `messages`. There, tool feedback is filed under
        role "user" with a `<tool_response name="...">` wrapper, which is what
        the model was told. Rendered as-is it puts raw tool JSON on screen
        under a user avatar, so those entries are converted back into tool
        events instead.
        """
        data = self.load_session(session_id)
        if not data:
            return []
        transcript = data.get("transcript")
        if isinstance(transcript, list) and transcript:
            return [t for t in transcript if isinstance(t, dict)]
        return transcript_from_messages(data.get("messages", []))


# The payload runs to the closing tag when there is one and to the end of the
# string when there is not - a turn cut off mid-response still has to parse.
# Taking everything to the end left the `</tool_response>` inside the result,
# and that text was then rendered inside the tool card.
_TOOL_RESPONSE_RE = re.compile(
    r"^\s*<tool_response\s+name=[\"']?([\w-]+)[\"']?[^>]*>(.*?)(?:</tool_response>|\Z)",
    re.DOTALL,
)

# Which argument each tool puts in its chat label, taken from the on_tool_start
# calls in agent/runner.py. A saved session has no record of these, so the
# label has to be recovered from the tool call's own arguments - a bare "Read"
# with no path is not what the original chat showed.
_TOOL_TARGET_ARG = {
    "read": "path",
    "edit": "path",
    "write": "path",
    "ls": "path",
    "glob": "pattern",
    "search": "query",
    "command": "cmd",
    "code_search": "query",
    "ask": "question",
    "screenshot": "target",
    "vision": "image_path",
    "web": "query",
    "todo": "note",
}


def _target_for(name: str, args: Dict[str, str]) -> str:
    key = _TOOL_TARGET_ARG.get(name)
    if key and str(args.get(key, "") or "").strip():
        return str(args[key]).strip()
    for fallback in ("path", "cmd", "query", "pattern", "text", "note"):
        if str(args.get(fallback, "") or "").strip():
            return str(args[fallback]).strip()
    return ""


def transcript_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best-effort display record for a session saved without one.

    A saved assistant turn is the raw model output: its prose and its tool calls
    sit in the same string. Replayed as one block of text it puts
    `<tool:ls path="." />` on screen as if the model had typed it, so the turn is
    re-parsed with the same parser the live chat uses. That splits prose from
    calls and recovers each call's target.

    A call's result lives in the *next* message, filed as a user turn with a
    `<tool_response>` wrapper. Calls are held until their result shows up so the
    halves can be rejoined - which is what puts the file name back on the Read
    card and the role back on the subagent marker.
    """
    from ..agent.runner import _parse_only

    out: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []

    for m in (messages or []):
        if not isinstance(m, dict):
            continue
        content = str(m.get("content", ""))
        if str(m.get("role", "")) == "user":
            hit = _TOOL_RESPONSE_RE.match(content)
            if not hit:
                out.append({"kind": "user", "text": content,
                            "turn_id": int(m.get("turn_id") or 0)})
                continue
            name, payload = hit.group(1), hit.group(2).strip()
            result = {"success": True, "legacy": True, "content": payload}
            idx = next((i for i, c in enumerate(pending) if c["tool"] == name), None)
            if idx is None:
                out.append(_tool_event(name, "", result))
                continue
            call = pending.pop(idx)
            out.append(_subagent_event(call, result) if call["tool"] == "subagent"
                       else _tool_event(call["tool"], call["target"], result))
            continue

        for kind, payload in _parse_only(content):
            if kind == "text":
                if payload:
                    out.append({"kind": "assistant", "text": payload})
            elif payload:
                name, args = payload[0], dict(payload[1] or {})
                pending.append({"tool": name, "args": args,
                                "target": _target_for(name, args)})

    # A turn can be cut short, leaving calls with no result. Emit them anyway so
    # the cards are not silently dropped.
    for call in pending:
        empty = {"success": True, "content": ""}
        out.append(_subagent_event(call, empty) if call["tool"] == "subagent"
                   else _tool_event(call["tool"], call["target"], empty))
    return out


def _tool_event(name: str, target: str, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"kind": "tool", "tool": name, "target": target, "result": result}


def _subagent_event(call: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """A subagent call, as a marker the chat can open.

    The saved result is the formatted report rather than the structured payload,
    so it becomes the summary. That is enough for the marker to carry its role,
    its task and something to read - the difference between a line reading
    "Subagent" and a marker that opens that subagent's own chat.
    """
    args = call.get("args") or {}
    role = str(args.get("name") or args.get("role") or "Subagent").strip()[:60] or "Subagent"
    return {
        "kind": "subagent",
        "role": role,
        "goal": str(args.get("goal") or args.get("task")
                    or args.get("description") or "").strip(),
        "system": str(args.get("system") or args.get("system_prompt")
                      or args.get("prompt") or "").strip(),
        "agent": {
            "role": role,
            "status": "completed",
            "summary": str(result.get("content") or "").strip(),
            "findings": [],
            "notes": [],
            "tool_count": 0,
            "history": [],
            "events": [],
            "elapsed": 0.0,
            "tokens": 0,
            "system_prompt": str(args.get("system") or ""),
        },
    }
