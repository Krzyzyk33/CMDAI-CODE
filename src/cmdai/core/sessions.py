import json
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional


def clean_title_from_prompt(prompt: str) -> str:
    """Generate a clean OpenCode-style session title from user prompt."""
    if not prompt:
        return ""
    text = prompt.strip()
    # Strip tool response / call XML tags and markdown code blocks
    text = re.sub(r'</?(?:tool_response|tool_call|function_call|\w+)[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'^```[a-zA-Z0-9_-]*\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    # Strip @mentions cleanly so "@index.html inspect" -> "inspect"
    mention_clean = re.sub(r'@\S+', '', text).strip()
    if mention_clean:
        text = mention_clean
    else:
        text = re.sub(r'[@]', '', text).strip()

    # If the user prompt is a command prefix like "wywołaj subagenta aby/do/żeby <task>",
    # extract the actual task description rather than the prefix!
    sub_prefix = re.search(r"^(?:wywo[\u0142l]aj|uruchom|odpal|w[\u0142l][a\u0105]cz)\s+su?a?b\w*\s+(?:aby|\u017ceby|do|by)\s+(.+)", text, re.IGNORECASE)
    if sub_prefix:
        text = sub_prefix.group(1).strip()
    else:
        text = re.sub(r'^(?:prosz[eę]|m[oó]g[lł]by[sś]|we[zź]|wez|prosz[eę]\s+ci[eę]|hej|cze[sś][cć]|chc[eę]|potrzebuj[eę])\s+[,:]?\s*', '', text, flags=re.IGNORECASE).strip()

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    first_line = ""
    for line in lines:
        cleaned_line = re.sub(r'^[#>\-*•]+\s*', '', line).strip()
        cleaned_line = re.sub(r'^```[a-zA-Z0-9_-]*\s*', '', cleaned_line).strip()
        if cleaned_line and cleaned_line.lower() not in ("header", "title", "prompt", "task"):
            first_line = cleaned_line
            break
        elif cleaned_line and not first_line:
            first_line = cleaned_line
    if not first_line:
        first_line = text
    first_line = re.sub(r'https?://\S+', '', first_line)
    first_line = re.sub(r'[`*_~]', '', first_line)
    first_line = re.sub(r'\s+', ' ', first_line).strip()
    if len(first_line) > 46:
        truncated = first_line[:46]
        last_space = truncated.rfind(' ')
        if last_space > 20:
            first_line = truncated[:last_space]
        else:
            first_line = truncated
    first_line = first_line.rstrip(' -:;,./')
    if first_line:
        first_line = first_line[0].upper() + first_line[1:]
    return first_line or "New Session"


def format_relative_time(ts_val: Any) -> str:
    """Format timestamp into human-readable relative string like in OpenCode."""
    if not ts_val:
        return ""
    try:
        t = 0.0
        if isinstance(ts_val, (int, float)):
            t = float(ts_val)
        elif isinstance(ts_val, str):
            import datetime
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
                try:
                    dt = datetime.datetime.strptime(ts_val.split(".")[0], fmt)
                    t = dt.timestamp()
                    break
                except Exception:
                    pass
            else:
                return ts_val[:16]
        else:
            return str(ts_val)[:16]

        diff = time.time() - t
        if diff < 0:
            return "just now"
        if diff < 60:
            return "just now"
        if diff < 3600:
            m = int(diff // 60)
            return f"{m}m ago"
        if diff < 86400:
            h = int(diff // 3600)
            return f"{h}h ago"
        if diff < 172800:
            return "yesterday"
        days = int(diff // 86400)
        if days < 7:
            return f"{days}d ago"
        return time.strftime("%b %d", time.localtime(t))
    except Exception:
        return str(ts_val)[:16]


def format_tokens(n: Any) -> str:
    """Format token count into compact label e.g. 1.2M tok or 14.5k tok."""
    try:
        val = int(n or 0)
        if val <= 0:
            return ""
        if val >= 1_000_000:
            return f"{val / 1_000_000:.1f}M tok"
        if val >= 1_000:
            return f"{val / 1_000:.1f}k tok"
        return f"{val} tok"
    except Exception:
        return ""


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
            os.path.join(self.project_dir, "app", "sessions") if getattr(self, "project_dir", "") else "",
            r"E:\CMDAI-CODE\app\sessions",
            r"D:\CMDAI CODE\app\sessions",
            os.path.join(os.path.expanduser("~"), ".cmdai", "sessions"),
            os.path.join(os.path.expanduser("~"), ".cmdai_code", "sessions"),
            os.path.join(os.path.expanduser("~"), ".cmdai_code"),
            os.path.join(os.path.expanduser("~"), ".cmdai"),
        ]
        out: List[str] = []
        seen_d = set()
        for d in dirs:
            if d and os.path.exists(d):
                ad = os.path.normcase(os.path.abspath(d))
                if ad not in seen_d:
                    seen_d.add(ad)
                    out.append(d)
        return out

    def save_session(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        model_id: str,
        title: str = "",
        stats: Optional[Dict[str, Any]] = None,
        transcript: Optional[List[Dict[str, Any]]] = None,
        workdir: Optional[str] = None,
        agent_mode: Optional[str] = None,
        named_by_model: Optional[bool] = None,
    ) -> bool:
        if not session_id:
            session_id = f"session_{int(time.time())}"

        target_dir = self.repo_sessions_dir
        target_path = os.path.join(target_dir, f"{session_id}.json")
        temp_path = os.path.join(target_dir, f".{session_id}.tmp")

        now_ts = time.time()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))

        if messages:
            try:
                transcript = reconcile_transcript(transcript, messages)
            except Exception:
                pass

        # Preserve created_at from existing session file if present
        created_at = now_str
        existing = None
        if os.path.exists(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    if isinstance(existing, dict) and existing.get("created_at"):
                        created_at = existing.get("created_at")
            except Exception:
                pass

        # Calculate last_written_ts: timestamp of actual written content (messages or transcript).
        # Simply entering, opening or switching to a session must NOT count as writing!
        has_messages = bool(messages and len(messages) > 0)
        existing_msgs = (existing.get("messages") or []) if existing else []
        existing_written_ts = (existing.get("last_written_ts") or existing.get("updated_ts")) if existing else None
        existing_written_str = (existing.get("last_written_at") or existing.get("updated_at")) if existing else None

        if not has_messages:
            last_written_ts = 0.0
            last_written_str = ""
        else:
            if existing is None:
                last_written_ts = now_ts
                last_written_str = now_str
            elif len(messages) != len(existing_msgs) or messages != existing_msgs:
                last_written_ts = now_ts
                last_written_str = now_str
            else:
                last_written_ts = float(existing_written_ts) if existing_written_ts else now_ts
                last_written_str = existing_written_str or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_written_ts))

        # OpenCode-style prompt titling if title is generic or placeholder
        bad_titles = ("new session", "untitled", "untitled session", "session", "tool", "mock", "tool mock")
        if not title or title.strip().lower() in bad_titles or title.startswith("Session session_"):
            first_u = next((str(m.get("content", "")) for m in (messages or [])
                            if isinstance(m, dict) and m.get("role") == "user"), "")
            if not first_u and transcript:
                first_u = next((str(t.get("text", "")) for t in transcript
                                if isinstance(t, dict) and t.get("kind") == "user"), "")
            derived = clean_title_from_prompt(first_u)
            title = derived if derived else (title or f"Session {session_id}")

        tok_in = stats.get("tokens_in", 0) if stats else 0
        tok_out = stats.get("tokens_out", 0) if stats else 0

        data = {
            "id": session_id,
            "title": title,
            "created_at": created_at,
            "updated_at": last_written_str or created_at,
            "updated_ts": last_written_ts if last_written_ts > 0 else (existing.get("updated_ts") if existing else now_ts),
            "last_written_ts": last_written_ts,
            "last_written_at": last_written_str,
            "named_by_model": bool(named_by_model if named_by_model is not None else (existing.get("named_by_model", False) if existing else False)),
            "directory": os.path.abspath(workdir or self.project_dir),
            "model_id": model_id,
            "mode": agent_mode or "code",
            "stats": stats or {},
            "tokens": {
                "input": tok_in,
                "output": tok_out,
                "total": tok_in + tok_out,
                "tools": stats.get("tools_run", 0) if stats else 0,
            },
            "messages": messages,
        }
        if transcript:
            data["transcript"] = transcript

        try:
            os.makedirs(target_dir, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(temp_path, target_path)
            try:
                user_sdir = os.path.join(os.path.expanduser("~"), ".cmdai", "sessions")
                os.makedirs(user_sdir, exist_ok=True)
                mirror_path = os.path.join(user_sdir, f"{session_id}.json")
                if os.path.normcase(os.path.abspath(mirror_path)) != os.path.normcase(os.path.abspath(target_path)):
                    shutil.copy2(target_path, mirror_path)
            except Exception:
                pass
            return True
        except Exception:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            return False

    def list_sessions(self, current_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        sessions = []
        seen_ids = set()
        active_dir = os.path.abspath(current_dir or self.project_dir) if (current_dir or self.project_dir) else ""

        for s_dir in self._get_candidate_dirs():
            try:
                entries = sorted(os.listdir(s_dir), reverse=True)
            except Exception:
                continue
            for fname in entries:
                if not fname.endswith(".json") or fname.startswith("."):
                    continue
                if fname in ("state.json", "state_session.json") or fname.endswith("_state.json"):
                    continue
                if fname.startswith("session_") and fname.endswith("_history.json"):
                    sid = fname[len("session_"):-len("_history.json")]
                else:
                    sid = fname[:-5]
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
                try:
                    fpath = os.path.join(s_dir, fname)
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    mtime = os.path.getmtime(fpath)
                    if isinstance(data, list):
                        msg_count = len(data)
                        first_u = next((str(m.get("content", "")) for m in data if isinstance(m, dict) and m.get("role") == "user"), "")
                        title = clean_title_from_prompt(first_u) or sid.replace("_", " ").title()
                        created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
                        last_written_ts = mtime if msg_count > 0 else 0.0
                        last_written_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)) if msg_count > 0 else ""
                        updated_ts = mtime
                        directory = ""
                        model_id = ""
                        total_toks = 0
                    elif isinstance(data, dict):
                        msg_count = len(data.get("messages") or [])
                        title = data.get("title", "")
                        bad_titles = ("new session", "untitled", "untitled session", "session", "tool", "mock", "tool mock")
                        if not title or title.strip().lower() in bad_titles or title.startswith("Session session_") or "API key required" in str(title):
                            first_u = next((str(m.get("content", "")) for m in (data.get("messages") or [])
                                            if isinstance(m, dict) and m.get("role") == "user"), "")
                            derived = clean_title_from_prompt(first_u)
                            title = derived if derived else (title or sid)
                        created_at = data.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
                        last_written_ts = data.get("last_written_ts")
                        last_written_at = data.get("last_written_at")
                        if last_written_ts is None:
                            if msg_count > 0:
                                last_written_ts = data.get("updated_ts") or mtime
                                last_written_at = data.get("updated_at") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_written_ts))
                            else:
                                last_written_ts = 0.0
                                last_written_at = ""
                        updated_ts = float(last_written_ts) if last_written_ts else (data.get("updated_ts") or mtime)
                        directory = data.get("directory", "")
                        model_id = data.get("model_id", "")
                        tokens_dict = data.get("tokens") or {}
                        stats_dict = data.get("stats") or {}
                        total_toks = tokens_dict.get("total") or ((stats_dict.get("tokens_in", 0) or 0) + (stats_dict.get("tokens_out", 0) or 0))
                    else:
                        continue

                    if not directory:
                        if fpath.startswith(self.repo_sessions_dir):
                            directory = self.repo_root
                        else:
                            parent_dir = os.path.dirname(fpath)
                            if os.path.basename(parent_dir).lower() == "sessions":
                                p2 = os.path.dirname(parent_dir)
                                directory = os.path.dirname(p2) if os.path.basename(p2).lower() == "app" else p2
                            else:
                                directory = self.project_dir

                    dir_abs = os.path.abspath(directory) if directory else ""
                    is_current = bool(active_dir and dir_abs and active_dir.lower() == dir_abs.lower())
                    dir_name = os.path.basename(directory) if directory else ""

                    if last_written_at and len(last_written_at) >= 16:
                        display_date = last_written_at[:16]
                    elif last_written_ts and float(last_written_ts) > 0:
                        display_date = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(last_written_ts)))
                    else:
                        display_date = "—"

                    sessions.append({
                        "id": sid,
                        "title": title,
                        "created_at": created_at,
                        "updated_ts": updated_ts,
                        "last_written_ts": float(last_written_ts or 0),
                        "last_written_at": last_written_at or "",
                        "display_date": display_date,
                        "relative_time": format_relative_time(last_written_ts if float(last_written_ts or 0) > 0 else updated_ts),
                        "directory": directory,
                        "dir_name": dir_name,
                        "is_current_dir": is_current,
                        "model_id": model_id,
                        "tokens": total_toks,
                        "tokens_str": format_tokens(total_toks),
                        "message_count": msg_count,
                    })
                except Exception:
                    pass

        # Sorted chronologically by when something was actually written (newest content first)
        sessions.sort(key=lambda s: (
            -float(s.get("last_written_ts", 0) or 0),
            -float(s.get("updated_ts", 0) or 0),
        ))
        return sessions

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        sid = str(session_id or "").strip()
        bare_id = sid[len("session_"):] if sid.startswith("session_") else sid
        for s_dir in self._get_candidate_dirs():
            candidates = [
                os.path.join(s_dir, f"{sid}.json"),
                os.path.join(s_dir, f"{bare_id}.json"),
                os.path.join(s_dir, f"session_{bare_id}.json"),
                os.path.join(s_dir, f"session_{bare_id}_history.json"),
                os.path.join(s_dir, f"{bare_id}_history.json"),
                os.path.join(s_dir, f"{sid}_history.json"),
                os.path.join(s_dir, f"{sid}"),
            ]
            for path in candidates:
                if os.path.exists(path):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            raw = json.load(f)
                            if isinstance(raw, list):
                                title = sid.replace("_", " ").title()
                                first_u = next((str(m.get("content", ""))[:45].strip().replace("\n", " ")
                                                for m in raw if isinstance(m, dict) and m.get("role") == "user"), "")
                                if first_u:
                                    title = first_u
                                mtime = os.path.getmtime(path)
                                return {
                                    "id": sid,
                                    "title": title,
                                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
                                    "messages": raw,
                                    "stats": {},
                                }
                            elif isinstance(raw, dict):
                                try:
                                    raw["transcript"] = reconcile_transcript(raw.get("transcript"), raw.get("messages"))
                                except Exception:
                                    pass
                                return raw
                    except Exception:
                        pass
        return None

    def delete_session(self, session_id: str) -> bool:
        deleted = False
        sid = str(session_id or "").strip()
        bare_id = sid[len("session_"):] if sid.startswith("session_") else sid
        for s_dir in self._get_candidate_dirs():
            candidates = [
                os.path.join(s_dir, f"{sid}.json"),
                os.path.join(s_dir, f"{bare_id}.json"),
                os.path.join(s_dir, f"session_{bare_id}.json"),
                os.path.join(s_dir, f"session_{bare_id}_history.json"),
                os.path.join(s_dir, f"session_{bare_id}_state.json"),
                os.path.join(s_dir, f"{bare_id}_history.json"),
                os.path.join(s_dir, f"{sid}_history.json"),
                os.path.join(s_dir, f"{sid}"),
            ]
            for path in candidates:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        deleted = True
                    except Exception:
                        pass
        return deleted

    def clear_all_sessions(self) -> int:
        count = 0
        for s_dir in self._get_candidate_dirs():
            try:
                for fname in os.listdir(s_dir):
                    if fname.endswith(".json") and (fname.startswith("session_") or not fname.startswith("state")):
                        try:
                            os.remove(os.path.join(s_dir, fname))
                            count += 1
                        except Exception:
                            pass
            except Exception:
                pass
        return count

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


_TOOL_NAME_MAP = {
    "create_file": "write",
    "write_file": "write",
    "write_to_file": "write",
    "save_file": "write",
    "replace_file_content": "edit",
    "edit_file": "edit",
    "patch_file": "edit",
    "read_file": "read",
    "view_file": "read",
    "cat": "read",
    "list_directory": "ls",
    "list_dir": "ls",
    "run_command": "command",
    "execute_command": "command",
    "bash": "command",
    "sh": "command",
    "terminal": "command",
    "cmd": "command",
    "web_search": "web",
    "search_web": "web",
    "fetch_web_page": "webfetch",
    "read_url_content": "webfetch",
    "read_browser_page": "webfetch",
}


def _normalize_tool_name(name: str) -> str:
    n = name.lower().strip()
    return _TOOL_NAME_MAP.get(n, n)


def transcript_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best-effort display record for a session saved without one.

    Reconstructs user turns, tool cards (Write, Edit, Command, Read, etc.),
    and assistant text from any message format: OpenCode / OpenAI function calls,
    raw XML tool tags, Markdown JSON blocks, and tool result messages.
    """
    from ..agent.runner import _parse_only

    out: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []

    for m in (messages or []):
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).lower()
        content = str(m.get("content", "") or "")

        # 1. Role "tool": Tool response message from model execution
        if role == "tool":
            tool_name = _normalize_tool_name(m.get("name") or "")
            call_id = m.get("tool_call_id")
            idx = None
            if call_id:
                idx = next((i for i, c in enumerate(pending) if c.get("call_id") == call_id), None)
            if idx is None and tool_name:
                idx = next((i for i, c in enumerate(pending) if c["tool"] == tool_name), None)
            if idx is None and pending:
                idx = 0

            is_err = "error" in content.lower() and "failed" in content.lower()
            res = {"success": not is_err, "content": content}
            if idx is not None:
                call = pending.pop(idx)
                out.append(_subagent_event(call, res) if call["tool"] == "subagent"
                           else _tool_event(call["tool"], call["target"], res))
            else:
                out.append(_tool_event(tool_name or "tool", "", res))
            continue

        # 2. Role "user": can be real user input or legacy <tool_response>
        if role == "user":
            hit = _TOOL_RESPONSE_RE.match(content)
            if hit:
                name, payload = hit.group(1), hit.group(2).strip()
                norm = _normalize_tool_name(name)
                result = {"success": True, "legacy": True, "content": payload}
                idx = next((i for i, c in enumerate(pending) if c["tool"] == norm), None)
                if idx is None:
                    out.append(_tool_event(norm, "", result))
                else:
                    call = pending.pop(idx)
                    out.append(_subagent_event(call, result) if call["tool"] == "subagent"
                               else _tool_event(call["tool"], call["target"], result))
                continue

            for call in pending:
                empty = {"success": True, "content": ""}
                out.append(_subagent_event(call, empty) if call["tool"] == "subagent"
                           else _tool_event(call["tool"], call["target"], empty))
            pending.clear()

            out.append({
                "kind": "user",
                "text": content,
                "turn_id": int(m.get("turn_id") or 0)
            })
            continue

        # 3. Role "assistant" (or system/other):
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function") or tc
                raw_name = fn.get("name", "")
                t_name = _normalize_tool_name(raw_name)
                raw_args = fn.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {}
                pending.append({
                    "tool": t_name,
                    "args": raw_args,
                    "target": _target_for(t_name, raw_args),
                    "call_id": tc.get("id"),
                })

        clean_content = content
        json_call_re = re.compile(
            r'(?:```(?:json)?\s*)?(\[\s*\{.*?\}\s*\]|\{\s*"(?:tool_call_id|tool_name|name|function)"\s*:.*?\})(?:\s*```)?',
            re.DOTALL
        )
        for m_json in json_call_re.finditer(clean_content):
            chunk = m_json.group(1).strip()
            try:
                j_data = json.loads(chunk)
            except Exception:
                continue
            calls = j_data if isinstance(j_data, list) else [j_data]
            for j_obj in calls:
                if not isinstance(j_obj, dict):
                    continue
                raw_name = j_obj.get("tool_name") or j_obj.get("name") or ""
                if not raw_name and isinstance(j_obj.get("function"), dict):
                    raw_name = j_obj["function"].get("name", "")
                elif not raw_name and isinstance(j_obj.get("function"), str):
                    raw_name = j_obj["function"]
                if not raw_name:
                    continue
                t_name = _normalize_tool_name(str(raw_name))
                t_args = {}
                raw_args = j_obj.get("arguments") or j_obj.get("args") or j_obj.get("parameters")
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {}
                if isinstance(raw_args, dict):
                    t_args.update({str(k).lower(): str(v) for k, v in raw_args.items()})
                for k, v in j_obj.items():
                    k_low = str(k).lower()
                    if k_low not in ("tool_name", "name", "tool_call_id", "id", "type", "function", "arguments", "args", "parameters"):
                        if k_low not in t_args:
                            t_args[k_low] = str(v) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)
                if not any(c.get("tool") == t_name for c in pending):
                    pending.append({
                        "tool": t_name,
                        "args": t_args,
                        "target": _target_for(t_name, t_args),
                    })
            clean_content = clean_content.replace(m_json.group(0), "").strip()

        if "<|tool_call>" in clean_content:
            m_tc = re.search(r"<\|tool_call>call:(\w+)\{(.*?)\}", clean_content, re.DOTALL)
            if m_tc:
                raw_name = m_tc.group(1)
                t_name = _normalize_tool_name(raw_name)
                arg_str = m_tc.group(2)
                p_match = re.search(r'["\']?(?:path|filePath|file|cmd|command|query)["\']?\s*[:=]\s*["\'<|]*([^"\'<|,}\s]+)', arg_str)
                target = p_match.group(1).strip() if p_match else ""
                if not any(c.get("tool") == t_name for c in pending):
                    pending.append({
                        "tool": t_name,
                        "args": {"path": target},
                        "target": target,
                    })
                clean_content = re.sub(r"<\|tool_call>.*", "", clean_content, flags=re.DOTALL).strip()

        for kind, payload in _parse_only(clean_content):
            if kind == "text":
                if payload and payload.strip():
                    out.append({"kind": "assistant", "text": payload})
            elif payload:
                name, args = payload[0], dict(payload[1] or {})
                norm_name = _normalize_tool_name(name)
                pending.append({"tool": norm_name, "args": args,
                                "target": _target_for(norm_name, args)})

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
    goal = str(args.get("goal") or args.get("task") or args.get("description") or "").strip()
    sum_txt = str(result.get("content") or "").strip()
    return {
        "kind": "subagent",
        "role": role,
        "goal": goal,
        "system": str(args.get("system") or args.get("system_prompt")
                      or args.get("prompt") or "").strip(),
        "agent": {
            "role": role,
            "status": "completed",
            "goal": goal,
            "summary": sum_txt,
            "findings": [],
            "notes": [],
            "tool_count": 0,
            "history": [{"role": "assistant", "content": sum_txt}] if sum_txt else [],
            "events": [],
            "elapsed": float(result.get("elapsed", 0.0) or 0.0),
            "tokens": int(result.get("tokens", 0) or 0),
            "system_prompt": str(args.get("system") or ""),
        },
    }


def is_user_prompt_message(m: Dict[str, Any]) -> bool:
    if not isinstance(m, dict) or m.get("role") != "user":
        return False
    c = str(m.get("content", "") or "")
    return not bool(_TOOL_RESPONSE_RE.match(c))


def reconcile_transcript(transcript: Optional[List[Dict[str, Any]]], messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Ensure the transcript contains all conversation turns present in messages.
    
    If transcript is missing later turns, or if an assistant response was never recorded
    for an earlier turn, reconstructs the missing parts from messages while preserving
    existing thinking blocks, detailed tool events, and timing.
    """
    messages = list(messages or [])
    if not isinstance(transcript, list) or not transcript:
        return transcript_from_messages(messages)
    if not messages:
        return list(transcript)

    t_asst = sum(1 for t in transcript if isinstance(t, dict) and t.get("kind") in ("assistant", "tool", "subagent"))
    m_asst = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant" and str(m.get("content", "")).strip())
    if t_asst == 0 and m_asst > 0:
        return transcript_from_messages(messages)

    m_user_indices = [i for i, m in enumerate(messages) if is_user_prompt_message(m)]
    if not m_user_indices:
        return list(transcript)

    t_user_indices = [i for i, item in enumerate(transcript) if isinstance(item, dict) and item.get("kind") == "user"]

    repaired = list(transcript)
    if len(t_user_indices) < len(m_user_indices):
        first_missing_user_idx = m_user_indices[len(t_user_indices)]
        last_t_u_idx = t_user_indices[-1] if t_user_indices else 0
        last_turn_items = transcript[last_t_u_idx:]
        has_resp = any(isinstance(x, dict) and x.get("kind") in ("assistant", "tool", "subagent") for x in last_turn_items)
        if not has_resp:
            prev_m_idx = m_user_indices[len(t_user_indices) - 1]
            missing_messages = messages[prev_m_idx:]
            repaired = repaired[:last_t_u_idx]
            repaired.extend(transcript_from_messages(missing_messages))
        else:
            missing_messages = messages[first_missing_user_idx:]
            repaired.extend(transcript_from_messages(missing_messages))
    elif t_user_indices:
        last_t_u_idx = t_user_indices[-1]
        last_turn_items = transcript[last_t_u_idx:]
        has_resp = any(isinstance(x, dict) and x.get("kind") in ("assistant", "tool", "subagent") for x in last_turn_items)
        if not has_resp and m_user_indices:
            last_m_idx = m_user_indices[-1]
            m_after = messages[last_m_idx:]
            m_has_resp = any(m.get("role") == "assistant" and str(m.get("content", "")).strip() for m in m_after)
            if m_has_resp:
                repaired = repaired[:last_t_u_idx]
                repaired.extend(transcript_from_messages(m_after))

    # Ensure chronological order: if an assistant text announcing a background subagent
    # was placed before the subagent block, swap them so the subagent sits above the commentary.
    for i in range(len(repaired) - 1):
        item_a = repaired[i]
        item_b = repaired[i + 1]
        if isinstance(item_a, dict) and isinstance(item_b, dict):
            if item_a.get("kind") == "assistant" and item_b.get("kind") in ("subagent", "tool"):
                text_low = str(item_a.get("text", "")).lower()
                is_announcement = any(k in text_low for k in ("subagent", "subagenta", "uruchomiłem w tle", "uruchomilem w tle", "w tle"))
                if is_announcement or item_b.get("is_backgrounded"):
                    repaired[i], repaired[i + 1] = item_b, item_a

    return repaired
