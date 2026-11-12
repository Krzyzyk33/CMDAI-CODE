import datetime
import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static, TextArea

from ...agent.runner import AgentRunner
from ...agent.tools import AgentContext
from ...core.branding import (
    CMDAI_PARTS as _CMDAI_PARTS,
    CODE_PARTS as _CODE_PARTS,
    LOGO_HERO as _LOGO_HERO,
    get_hero_logo as _get_hero_logo,
)
from ...core.capabilities import (
    ModelCapability,
    clean_model_name,
    cycle_order,
    forget_uncached_capabilities,
    get_model_capability,
    normalize_level,
)
from ...core.engine import SimpleGGUFLoader
from ...core.providers import PROVIDERS_CATALOG, stream_chat_completion
from ...core.resource_limits import (
    build_summarization_payload,
    detect_conversation_language,
    estimate_model_memory,
    estimate_token_count,
    format_compacted_handoff,
    get_dynamic_context_limit,
    is_summarization_error_text,
    should_summarize,
)
from ...core.server import BackgroundHTTPServer
from ...core.sessions import SessionManager, transcript_from_messages
from ...core.settings import get_settings
from ...core.notifier import send_desktop_notification
from ...core.exporter import export_session_to_html
from ...core.releases import is_newer as _is_newer
from ...core.providers import TOOL_COMPLETE_PATTERNS, is_stream_tool_or_turn_complete
from ..modals.actions import ActionMenuModal
from ..modals.approval import EditInspectModal
from ..modals.ask import AskModal
from ..modals.commit import CommitModal
from ..modals.context import ContextInspectorModal
from ..modals.diff import DiffModal
from ..modals.files import FilePickerModal
from ..modals.plan import PlanModal
from ..modals.help import COMMANDS_DOC, HelpModal, InfoModal
from ..modals.models import ModelSelectModal
from ..modals.thinking import ThinkingModal
from ..modals.params import ParamsModal
from ..modals.prompts import SystemPromptModal
from ..modals.sessions import SessionsModal
from ..modals.settings import LoaderModal, SettingsModal
from ..modals.stats import StatsModal
from ..modals.releases import ReleasesModal, UpdateNoticeModal
from ..styles import SWIFT_CSS
from ..widgets import (
    ApprovalBar,
    ApprovalDiffPreview,
    AssistantTurnCard,
    AutoExpandingInput,
    ClickableMessage,
    HintRow,
    ThinkingBlock,
    TodoPreviewBar,
    ToolBlock,
    UserMessageCard,
    SessionTabsBar,
    TimelineRail,
    TimelineDot,
    copy_text_to_clipboard,
)
from ...mcp.client import sanitize_name

# Logo lives in core/branding.py so the CLI can print it without Textual.
CMDAI_PARTS = _CMDAI_PARTS
CODE_PARTS = _CODE_PARTS
get_hero_logo = _get_hero_logo
LOGO_HERO = _LOGO_HERO



class SessionsMixin:
    """Mixin for CMDAICodeTUI."""

    def _create_turn_checkpoint(self) -> None:
        """Snapshot the working tree as a checkpoint labelled with the prompt.

        Called when a turn finishes (chat / auto mode) and when the user
        accepts the changes of a code-mode turn.
        """
        try:
            from ...core.checkpoints import CheckpointManager
            prompt = ""
            for m in reversed(self.messages):
                if m.get("role") == "user":
                    prompt = str(m.get("content", ""))
                    break
            prompt = " ".join(prompt.split())[:80] or "Turn complete"
            mgr = CheckpointManager(self.workdir)
            cp = mgr.create_checkpoint(prompt)
            self._refresh_checkpoints_cache(cp)
        except Exception:
            pass


    def _refresh_checkpoints_cache(self, cp: Any) -> None:
        try:
            self._last_checkpoint = cp
            self.call_after_refresh(self._update_meta_bars)
        except Exception:
            pass


    def _flush_live_turn_to_session(self) -> None:
        """Write the turn being generated into the session before closing.

        The worker only records text once a step completes, so closing the
        app mid-answer saved nothing but the prompt. This takes what is on
        screen right now - thinking and the current text - and saves it.
        """
        if getattr(self, "_session_flushed_on_exit", False):
            return
        self._session_flushed_on_exit = True
        try:
            card = self._current_assistant_card
            if self.is_generating:
                think = "".join(getattr(self, "_live_step_thinking", []) or []).strip()
                if not think and card is not None:
                    tb = getattr(card, "thinking_block", None)
                    think = str(getattr(tb, "thinking_text", "") or "").strip() if tb else ""
                if think and not any(t.get("kind") == "thinking" and t.get("text") == think
                                     for t in self._transcript[-5:]):
                    self._transcript.append({"kind": "thinking", "text": think})
                live = "".join(getattr(self, "_live_step_chunks", []) or [])
                if not live.strip() and card is not None:
                    live = str(getattr(card, "raw_text", "") or "")
                if self.agent_runner:
                    live = self.agent_runner.strip_xml_tool_calls(live)
                live = live.strip()
                last = self._transcript[-1] if self._transcript else {}
                if live and not (last.get("kind") == "assistant" and last.get("text") == live):
                    self._record_assistant_text(live)
                    self.messages.append({"role": "assistant", "content": live})
            if self.messages or self._transcript:
                self._save_current_session()
        except Exception:
            pass


    def _record_user_turn(self, text: str, turn_id: int, time_str: str = "", timestamp: Optional[float] = None) -> None:
        now_ts = timestamp if timestamp is not None else time.time()
        t_str = time_str or time.strftime("%H:%M", time.localtime(now_ts))
        self._transcript.append({
            "kind": "user",
            "text": text,
            "turn_id": turn_id,
            "timestamp": now_ts,
            "time": t_str
        })


    def _record_assistant_text(self, text: str, target_transcript: Optional[List[Dict[str, Any]]] = None) -> None:
        import re
        clean = re.sub(r'</?(?:tool_response|tool_call|function_call)[^>]*>', '', str(text or ''), flags=re.IGNORECASE).strip()
        if not clean:
            return
        dest = target_transcript if target_transcript is not None else self._transcript
        dest.append({"kind": "assistant", "text": clean})


    def _record_tool(self, tool_name: str, target: str, result: Any, target_transcript: Optional[List[Dict[str, Any]]] = None) -> None:
        """Record one tool call so the replay can rebuild its card.

        Keeps the whole result, not a summary: the card renders command output,
        diffs and file contents out of it, and a trimmed copy would come back
        as an empty block.
        """
        dest = target_transcript if target_transcript is not None else self._transcript
        dest.append({
            "kind": "tool",
            "tool": tool_name,
            "target": target,
            "result": result if isinstance(result, dict) else {"success": True, "content": str(result)},
        })


    def _extract_live_tool_stream(self, fresh: str) -> Tuple[str, str, str]:
        """Extract active tool name, exact target (e.g. file path), and streaming code body from fresh text."""
        if not fresh:
            return "", "", ""

        _NORM = {
            "create_file": "write", "write_file": "write", "write_to_file": "write", "save_file": "write",
            "replace_file_content": "edit", "edit_file": "edit", "patch_file": "edit",
            "read_file": "read", "view_file": "read", "cat": "read",
            "run_command": "command", "execute_command": "command", "bash": "command", "terminal": "command", "sh": "command", "cmd": "command",
            "shell": "command", "exec": "command", "powershell": "command", "pwsh": "command", "run_shell_command": "command", "shell_command": "command",
            "web_search": "web", "search_web": "web", "fetch_web_page": "webfetch", "read_url_content": "webfetch",
        }

        KEY_PAT = (
            r'(?:\\?"?(?:TargetFile|target_file|targetfile|filePath|filepath|file_path|filename|file_name|dest|destination|file|path|cmd|command|commandLine|command_line|target|url|query|name|role|goal|task)\\?"?)'
        )

        # 1. XML tool tag streaming: find all tool tags and take the latest active one
        all_xml = list(re.finditer(r'<(?:tool:|\/tool_call:?|tool_call:?|\|tool_call>call:)([a-zA-Z0-9_-]+)([^>]*(?:>|$))', fresh, flags=re.IGNORECASE))
        if all_xml:
            xml_m = all_xml[-1]
            raw_n = xml_m.group(1).lower()
            tl_name = _NORM.get(raw_n, raw_n)
            tail = fresh[xml_m.start():]
            tl_args = xml_m.group(2) or ""

            tl_tgt = ""
            # Match path attributes with quotes or unquoted, handling Windows paths and streaming quotes
            tgt_m = re.search(
                KEY_PAT + r'\s*=\s*(?:\\?["\']([^"\'\r\n>]+)(?:\\?["\'])?|([^"\'\s>]+))',
                tail,
                flags=re.IGNORECASE,
            )
            if tgt_m:
                raw_tgt = tgt_m.group(1) or tgt_m.group(2) or ""
                tl_tgt = raw_tgt.replace('\\\\', '\\').replace('\\/', '/').strip('"\'\\')
            else:
                sub_m = re.search(r'<(?:TargetFile|target_file|targetfile|filePath|filepath|file_path|filename|file_name|file|path|dest)>([^<>\r\n]+)', tail, flags=re.IGNORECASE)
                if sub_m:
                    tl_tgt = sub_m.group(1).strip()
                else:
                    json_tgt_m = re.search(KEY_PAT + r'\s*:\s*(?:\\?["\']([^"\'\r\n}>]+)(?:\\?["\'])?|([^"\'\s,}>]+))', tail, flags=re.IGNORECASE)
                    if json_tgt_m:
                        raw_tgt = json_tgt_m.group(1) or json_tgt_m.group(2) or ""
                        tl_tgt = raw_tgt.replace('\\\\', '\\').replace('\\"', '"').replace('\\/', '/').strip('"\'\\')

            live_content = ""
            tag_end = fresh.find(">", xml_m.start())
            if tag_end != -1:
                live_content = fresh[tag_end + 1:]
                live_content = re.sub(r'</tool:[a-zA-Z0-9_-]+>$', '', live_content, flags=re.IGNORECASE)

            if tl_tgt and tl_name in ("write", "edit", "read"):
                try:
                    import os
                    wdir = getattr(self, "workdir", "")
                    if wdir and not os.path.isabs(tl_tgt):
                        tl_tgt = os.path.normpath(os.path.join(wdir, tl_tgt))
                except Exception:
                    pass

            return tl_name, tl_tgt, live_content or tl_args

        # 2. JSON tool streaming: single object or array (supports escaped and unescaped quotes)
        all_json = list(re.finditer(
            r'(?:\{|\[\s*\{)[^{}]*?\\?"(?:tool_name|name|tool|function)\\?"\s*:\s*\\?"([a-zA-Z0-9_-]+)\\?"',
            fresh,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        if all_json:
            json_m = all_json[-1]
            raw_n = json_m.group(1).lower()
            tl_name = _NORM.get(raw_n, raw_n)
            tail = fresh[json_m.start():]

            tgt_m = re.search(
                KEY_PAT + r'\s*[:=]\s*(?:\\?["\']([^"\'\r\n}>]+)(?:\\?["\'])?|([^"\'\s,}>]+))',
                tail,
                flags=re.IGNORECASE,
            )
            tl_tgt = ""
            if tgt_m:
                raw_tgt = tgt_m.group(1) or tgt_m.group(2) or ""
                tl_tgt = raw_tgt.replace('\\\\', '\\').replace('\\/', '/').strip('"\'\\')

            code_m = re.search(
                r'\\?"(?:CodeContent|code_content|code|replacementContent|replacement_content|new_content|new|content)\\?"\s*:\s*\\?"((?:\\.|[^"\\])*)',
                tail,
                flags=re.DOTALL,
            )
            live_content = ""
            if code_m:
                raw_c = code_m.group(1)
                live_content = raw_c.replace('\\n', '\n').replace('\\t', '    ').replace('\\"', '"').replace('\\\\', '\\')

            if tl_tgt and tl_name in ("write", "edit", "read"):
                try:
                    import os
                    wdir = getattr(self, "workdir", "")
                    if wdir and not os.path.isabs(tl_tgt):
                        tl_tgt = os.path.normpath(os.path.join(wdir, tl_tgt))
                except Exception:
                    pass

            return tl_name, tl_tgt, live_content

        return "", "", ""


    def _sync_transcript_from_ui(self) -> None:
        """Synchronize self._transcript directly from the mounted chat cards so every
        thinking block, text block, tool call, subagent, background badge, and Done
        footer stat is persisted accurately in exact visual order without duplicates."""
        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            children = list(chat_scroll.children)
        except Exception:
            return
        if not children:
            return
        rebuilt: List[Dict[str, Any]] = []
        for child in children:
            if isinstance(child, UserMessageCard):
                rebuilt.append({
                    "kind": "user",
                    "text": str(getattr(child, "prompt", "") or ""),
                    "turn_id": int(getattr(child, "turn_id", 0) or 0),
                })
            elif isinstance(child, AssistantTurnCard):
                tb = getattr(child, "thinking_block", None)
                if tb is not None and str(getattr(tb, "thinking_text", "") or "").strip():
                    rebuilt.append({
                        "kind": "thinking",
                        "text": str(tb.thinking_text).strip(),
                        "elapsed": float(getattr(tb, "elapsed", 0.0) or 0.0),
                    })
                flow_items = list(getattr(child.flow_container, "children", []) or [])
                if not flow_items and getattr(child, "_pending_mounts", None):
                    flow_items = list(child._pending_mounts)
                for fc in flow_items:
                    if isinstance(fc, ToolBlock):
                        t_name = str(getattr(fc, "tool_name", "") or "tool")
                        is_sub = bool(getattr(fc, "subagent_role", "")) or t_name.lower() in ("subagent", "task")
                        if is_sub:
                            res_d = fc.result_data if isinstance(fc.result_data, dict) else {}
                            role_s = str(getattr(fc, "subagent_role", "") or res_d.get("role") or "Subagent")
                            goal_s = str(getattr(fc, "subagent_goal", "") or res_d.get("goal") or "")
                            sys_s = str(getattr(fc, "subagent_system", "") or res_d.get("system_prompt") or "")
                            sub_id = str(getattr(fc, "subagent_id", "") or "")
                            agent_payload = {
                                "subagent_id": sub_id,
                                "role": role_s,
                                "status": str(getattr(fc, "subagent_status", "") or res_d.get("status") or ("running" if getattr(fc, "tool_running", False) else "completed")),
                                "goal": goal_s,
                                "system_prompt": sys_s,
                                "summary": str(res_d.get("summary") or res_d.get("content") or ""),
                                "history": list(getattr(fc, "subagent_history", None) or res_d.get("history") or []),
                                "events": list(getattr(fc, "subagent_events", None) or res_d.get("events") or []),
                                "notes": list(getattr(fc, "subagent_notes", None) or res_d.get("notes") or []),
                                "findings": list(getattr(fc, "subagent_findings", None) or res_d.get("findings") or []),
                                "tool_count": int(getattr(fc, "subagent_tool_count", 0) or len(getattr(fc, "subagent_events", None) or [])),
                                "elapsed": float(getattr(fc, "subagent_elapsed", 0.0) or res_d.get("elapsed") or 0.0),
                                "tokens": int(getattr(fc, "subagent_tokens", 0) or res_d.get("tokens") or 0),
                                "is_backgrounded": bool(getattr(fc, "is_backgrounded", False)),
                                "elapsed_text": str(getattr(fc, "elapsed_text", "") or ""),
                            }
                            rebuilt.append({
                                "kind": "subagent",
                                "subagent_id": sub_id,
                                "role": role_s,
                                "goal": goal_s,
                                "system": sys_s,
                                "is_backgrounded": bool(getattr(fc, "is_backgrounded", False)),
                                "elapsed_text": str(getattr(fc, "elapsed_text", "") or ""),
                                "agent": agent_payload,
                            })
                        else:
                            res_d = dict(fc.result_data) if isinstance(fc.result_data, dict) else {
                                "success": not bool(getattr(fc, "has_error", False)),
                                "content": str(getattr(fc, "details_text", "") or ""),
                            }
                            if getattr(fc, "is_backgrounded", False):
                                res_d["is_backgrounded"] = True
                            rebuilt.append({
                                "kind": "tool",
                                "tool": t_name,
                                "target": str(getattr(fc, "target", "") or ""),
                                "is_backgrounded": bool(getattr(fc, "is_backgrounded", False)),
                                "elapsed_text": str(getattr(fc, "elapsed_text", "") or ""),
                                "result": res_d,
                            })
                    else:
                        raw_md = str(getattr(fc, "_raw_markdown", "") or "").strip()
                        if raw_md:
                            if not (rebuilt and rebuilt[-1].get("kind") == "assistant" and rebuilt[-1].get("text") == raw_md):
                                rebuilt.append({"kind": "assistant", "text": raw_md})
                if not any(item.get("kind") == "assistant" for item in rebuilt[-max(1, len(flow_items)):]):
                    child_text = str(getattr(child, "raw_text", "") or getattr(child, "_last_finalized_text", "") or "").strip()
                    if child_text and not (rebuilt and rebuilt[-1].get("kind") == "assistant" and rebuilt[-1].get("text") == child_text):
                        rebuilt.append({"kind": "assistant", "text": child_text})
                if not getattr(child, "is_generating", False):
                    rebuilt.append({
                        "kind": "turn_done",
                        "elapsed": float(getattr(child, "elapsed", 0.0) or 0.0),
                        "tokens": int(getattr(child, "tokens", 0) or 0),
                        "tokens_in": int(getattr(child, "tokens_in", 0) or 0),
                        "tokens_out": int(getattr(child, "tokens_out", 0) or 0),
                        "tok_s": float(getattr(child, "tok_s", 0.0) or 0.0),
                    })
        if rebuilt:
            self._transcript = rebuilt


    def _save_session_data(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        transcript: Optional[List[Dict[str, Any]]] = None,
        title: Optional[str] = None,
        stats: Optional[Dict[str, Any]] = None,
        model_id: Optional[str] = None,
        workdir: Optional[str] = None,
        agent_mode: Optional[str] = None,
    ) -> None:
        """Save an isolated session's data safely without touching or reading active UI state."""
        if not session_id:
            return
        try:
            existing = self.session_manager.load_session(session_id) or {}
            s_title = str(title or existing.get("title") or "")
            if not s_title.strip():
                first_u = next((str(m.get("content", "")) for m in (messages or []) if isinstance(m, dict) and m.get("role") == "user"), "")
                from ...core.sessions import clean_title_from_prompt
                if first_u:
                    s_title = clean_title_from_prompt(first_u)
            s_stats = stats or existing.get("stats") or self.stats
            s_model = model_id or existing.get("model_id") or self.current_model_id
            s_workdir = workdir or existing.get("directory") or self.workdir
            s_mode = agent_mode or existing.get("mode") or self.agent_mode
            from ...core.sessions import reconcile_transcript
            final_transcript = reconcile_transcript(transcript or existing.get("transcript"), messages)
            self.session_manager.save_session(
                session_id=session_id,
                messages=messages,
                model_id=s_model,
                stats=s_stats,
                title=s_title,
                transcript=final_transcript,
                workdir=s_workdir,
                agent_mode=s_mode,
                named_by_model=bool(existing.get("named_by_model", False)),
                todo_list=existing.get("todo_list"),
                pending_approval=existing.get("pending_approval"),
            )
        except Exception:
            pass


    def _save_current_session(self, session_id: Optional[str] = None) -> None:
        try:
            target_id = session_id or self.current_session_id
            # Safety guard: never overwrite another session using active screen data
            if session_id and session_id != self.current_session_id:
                return
            if not self.messages and not self._transcript:
                return
            self._sync_transcript_from_ui()
            from ...core.sessions import clean_title_from_prompt, reconcile_transcript
            if self.messages:
                self._transcript = reconcile_transcript(self._transcript, self.messages)
            if not self.session_title.strip():
                first_u = next((str(m.get("content", "")) for m in self.messages if m.get("role") == "user"), "")
                if first_u:
                    self.session_title = clean_title_from_prompt(first_u)
                    self._update_terminal_title()
            self.session_manager.save_session(
                session_id=target_id,
                messages=self.messages,
                model_id=self.current_model_id,
                stats=self.stats,
                title=self.session_title,
                transcript=list(self._transcript),
                workdir=self.workdir,
                agent_mode=self.agent_mode,
                named_by_model=getattr(self, "_session_named_by_model", False),
                todo_list=getattr(self.agent_ctx, "todo_list", None),
                pending_approval=getattr(self, "_pending_approval_details", None) if getattr(self, "_post_turn_approval_active", False) else None,
            )
        except Exception:
            pass


    def _update_terminal_title(self) -> None:
        try:
            if not getattr(self, "in_chat_mode", False) or not getattr(self, "messages", []):
                title = "CMDAI CODE"
            else:
                name = self.session_title.strip() or "new session"
                title = f"CC ● {name}"

            self.title = title

            try:
                import sys
                sys.stdout.write(f"\x1b]0;{title}\x07")
                sys.stdout.write(f"\x1b]2;{title}\x07")
                sys.stdout.flush()
            except Exception:
                pass

            if os.name == "nt":
                import ctypes
                ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass


    def _maybe_auto_name_session(self) -> None:
        try:
            if getattr(self, "_session_named_by_model", False):
                if self.session_title.strip():
                    try:
                        self._update_terminal_title()
                    except Exception:
                        self.call_from_thread(self._update_terminal_title)
                return
            if self.current_provider == "local_gguf" and self.is_generating:
                return
            first_u = next((str(m.get("content", "")) for m in self.messages if m.get("role") == "user"), "")
            if not first_u:
                return
            self._name_session_worker(first_u[:500])
        except Exception:
            pass


    @work(thread=True)
    def _name_session_worker(self, user_text: str) -> None:
        try:
            from ...core.sessions import clean_title_from_prompt
            clean_u = re.sub(r'</?(?:tool_response|tool_call|function_call|\w+)[^>]*>', '', user_text, flags=re.IGNORECASE)
            clean_u = re.sub(r'@\S+', '', clean_u).strip()
            if not clean_u:
                clean_u = user_text

            title = ""
            naming = [
                {
                    "role": "user",
                    "content": (
                        "Task: Generate a concise, natural topic title (2 to 5 words) for a programming session that starts with this user request:\n"
                        f'\"{clean_u}\"\n\n'
                        "Rules:\n"
                        "- You have NO tools. Never emit XML tags, tool calls, or code blocks.\n"
                        "- Return ONLY the short plain text title (e.g. 'Ocena aplikacji', 'Code Review', 'Poprawa widoku').\n"
                        "- Use the same language as the user request."
                    ),
                }
            ]
            think = ""
            if self.current_provider == "local_gguf":
                if self.is_generating:
                    return
                local_naming = [
                    {
                        "role": "system",
                        "content": (
                            "Jesteś generatorem krótkich tytułów sesji czatu (2 do 4 słów). "
                            "NIGDY nie odpowiadaj użytkownikowi, nie witaj się i nie prowadź rozmowy. "
                            "Zwróć wyłącznie krótki tytuł określający temat wiadomości w tym samym języku co wiadomość użytkownika "
                            "(np. dla 'hej' zwróć 'Powitanie i rozmowa', dla 'napisz kalkulator' zwróć 'Kalkulator w Pythonie')."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f'Wygeneruj krótki tytuł sesji (2-4 słowa) dla wiadomości: "{clean_u}".\n'
                            "Zwróć TYLKO sam tytuł bez cudzysłowów, bez kropek i bez odpowiadania na wiadomość."
                        ),
                    },
                ]
                title, think = self.gguf_loader.stream_chat(
                    messages=local_naming,
                    temperature=0.1,
                    max_tokens=20,
                    model_filename=self.current_model_id,
                    is_aborted=lambda: self.is_generating or self.abort_requested,
                )
            else:
                from ...core.providers import stream_chat_completion
                title, think = stream_chat_completion(
                    provider_id=self.current_provider,
                    model_id=self.current_model_id,
                    messages=naming,
                    api_key=self.settings.get_api_key(self.current_provider),
                    generation_params={"temperature": 0.1, "max_tokens": 20, "reasoning_level": "off", "is_naming": True},
                    tools=[],
                )
            if not title and think:
                title = think
            if self.agent_runner:
                title = self.agent_runner.strip_xml_tool_calls(title)
            title = re.sub(r'</?(?:tool_response|tool_call|function_call|\w+)[^>]*>', '', title, flags=re.IGNORECASE)
            title = re.sub(r'<[^>]+>', '', title)
            title = re.sub(r'^(?:session\s+title:\s*|title:\s*|#+\s*|nazwa(?:\s+sesji)?:\s*|temat:\s*|tytuł(?:\s+sesji)?:\s*)', '', title, flags=re.IGNORECASE).strip()
            title = " ".join(str(title or "").strip().strip('"\'`').split())[:50]
            title = title.strip('"\'`').strip(" .:-_?!")

            bad_tokens = (
                "tool", "mock", "<tool", "</tool", "tool_call", "function_call", "tool_response",
                "error", "failed", "exception", "traceback", "api key", "json",
                "opencode bridge", "bridge", "http", "503", "502", "500", "401", "429", "403",
                "limit reached", "usage limit", "quota", "rate limit", "service unavailable",
                "how can i assist", "how can i help", "jak mogę pomóc", "w czym mogę pomóc",
                "jak moge pomoc", "hello, how", "cześć, w czym",
            )
            if not title or len(title) < 3 or any(b in title.lower() for b in bad_tokens):
                title = clean_title_from_prompt(user_text)

            if not title:
                return

            self.session_title = title
            self._session_named_by_model = True
            self.call_from_thread(self._apply_session_title, title)
            self.call_from_thread(
                self.notify,
                title,
                title="Session Renamed",
                severity="information",
                timeout=3.0,
            )
        except Exception:
            pass


    def _apply_session_title(self, title: str) -> None:
        try:
            self.session_title = title
            self._save_current_session()
            self._update_terminal_title()
            self._refresh_session_tabs()
        except Exception:
            pass


    def _capture_workspace_snapshot(self, turn_id: int) -> None:
        """Capture pre-turn file contents in workdir so reverting Turn N restores Turn N-1 state."""
        if turn_id <= 0:
            return
        try:
            from ...core.project_scope import is_unsafe_global_dir
            if is_unsafe_global_dir(self.workdir):
                return
        except Exception:
            pass
        if not hasattr(self, "_turn_snapshots"):
            self._turn_snapshots: Dict[int, Dict[str, Optional[bytes]]] = {}
        snap: Dict[str, Optional[bytes]] = {}
        ignored_dirs = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".cmdai_code_project", ".gemini"}
        count = 0
        try:
            for root, dirs, files in os.walk(self.workdir):
                dirs[:] = [d for d in dirs if d not in ignored_dirs]
                for fname in files:
                    full_p = os.path.normpath(os.path.join(root, fname))
                    try:
                        st = os.stat(full_p)
                        if st.st_size <= 1_500_000:
                            with open(full_p, "rb") as f:
                                snap[full_p] = f.read()
                            count += 1
                    except Exception:
                        pass
                    if count >= 500:
                        break
                if count >= 500:
                    break
        except Exception:
            pass
        self._turn_snapshots[int(turn_id)] = snap


    def _snapshot_file_before_modify(self, turn_id: int, path: str) -> None:
        """Ensure a specific file's pre-turn state is recorded before it is modified in `turn_id`."""
        if turn_id <= 0 or not path:
            return
        if not hasattr(self, "_turn_snapshots"):
            self._turn_snapshots = {}
        snap = self._turn_snapshots.setdefault(int(turn_id), {})
        full_p = os.path.normpath(path if os.path.isabs(path) else os.path.join(self.workdir, path))
        if full_p not in snap:
            try:
                if os.path.isfile(full_p):
                    with open(full_p, "rb") as f:
                        snap[full_p] = f.read()
                else:
                    snap[full_p] = None
            except Exception:
                snap[full_p] = None


    def _revert_files(self, file_list: Optional[List[str]] = None, from_turn: int = -1) -> List[str]:
        import subprocess
        reverted: List[str] = []
        snapshots: Dict[int, Dict[str, Optional[bytes]]] = getattr(self, "_turn_snapshots", {}) or {}

        if from_turn > 0 and from_turn in snapshots:
            baseline = snapshots[from_turn]
            # Collect all candidate paths currently in workdir or touched in >= from_turn
            ignored_dirs = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".cmdai_code_project", ".gemini"}
            current_files = set()
            try:
                for root, dirs, files in os.walk(self.workdir):
                    dirs[:] = [d for d in dirs if d not in ignored_dirs]
                    for fname in files:
                        current_files.add(os.path.normpath(os.path.join(root, fname)))
            except Exception:
                pass
            for t_id, t_snap in list(snapshots.items()):
                if t_id >= from_turn:
                    current_files.update(t_snap.keys())
            for f in (getattr(self, "_last_modified_files", None) or []):
                full_f = os.path.normpath(f if os.path.isabs(f) else os.path.join(self.workdir, f))
                current_files.add(full_f)

            # 1. Restore files that existed in baseline before `from_turn`
            for full_p, orig_bytes in baseline.items():
                rel = os.path.relpath(full_p, self.workdir)
                try:
                    if orig_bytes is None:
                        if os.path.exists(full_p):
                            os.remove(full_p)
                            reverted.append(rel)
                    else:
                        cur_bytes = None
                        if os.path.isfile(full_p):
                            with open(full_p, "rb") as f:
                                cur_bytes = f.read()
                        if cur_bytes != orig_bytes:
                            os.makedirs(os.path.dirname(full_p) or ".", exist_ok=True)
                            with open(full_p, "wb") as f:
                                f.write(orig_bytes)
                            reverted.append(rel)
                except Exception:
                    pass

            # 2. Remove files created during or after `from_turn` (not in baseline)
            for full_p in current_files:
                if full_p not in baseline and os.path.isfile(full_p):
                    rel = os.path.relpath(full_p, self.workdir)
                    try:
                        os.remove(full_p)
                        if rel not in reverted:
                            reverted.append(rel)
                    except Exception:
                        pass

            for t_id in [k for k in snapshots.keys() if k >= from_turn]:
                snapshots.pop(t_id, None)
            if hasattr(self, "_last_modified_files"):
                self._last_modified_files.clear()
            return reverted

        targets = set()
        if file_list:
            targets.update(file_list)
        if getattr(self, "_last_modified_files", None):
            targets.update(self._last_modified_files)
        if not targets:
            try:
                res = subprocess.run(["git", "status", "--porcelain"], cwd=self.workdir, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if line.strip():
                            targets.add(line[2:].strip().strip('"'))
            except Exception:
                pass

        for f in targets:
            full_p = os.path.join(self.workdir, f) if not os.path.isabs(f) else f
            rel = os.path.relpath(full_p, self.workdir)
            try:
                check_tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=self.workdir, capture_output=True)
                if check_tracked.returncode == 0:
                    res = subprocess.run(["git", "checkout", "HEAD", "--", rel], cwd=self.workdir, capture_output=True)
                    if res.returncode == 0:
                        reverted.append(rel)
                else:
                    if os.path.exists(full_p):
                        os.remove(full_p)
                        reverted.append(rel)
            except Exception:
                pass

        if hasattr(self, "_last_modified_files"):
            self._last_modified_files.clear()
        return reverted


    def _revert_turn_files(self) -> None:
        reverted = self._revert_files()
        if reverted:
            self.notify(f"Reverted {len(reverted)} file(s).", timeout=2.5)
        else:
            self.notify("No file changes to revert.", timeout=2.0)


    def _delete_turn(self, turn_id: int, keep_user: bool = True, notify: bool = True) -> None:
        try:
            # 1. Truncate self.messages from this turn onwards (keep user prompt if keep_user=True)
            idx = next((i for i, m in enumerate(self.messages) if m.get("role") == "user" and int(m.get("turn_id") or -1) == int(turn_id)), None)
            if idx is not None:
                if keep_user:
                    del self.messages[idx + 1:]
                else:
                    del self.messages[idx:]
            else:
                if keep_user:
                    self.messages = [m for m in self.messages if int(m.get("turn_id") or 0) <= int(turn_id) and m.get("role") == "user"]
                else:
                    self.messages = [m for m in self.messages if int(m.get("turn_id") or 0) < int(turn_id)]

            # 2. Truncate self._transcript from this turn onwards
            t_idx = next((i for i, t in enumerate(self._transcript) if isinstance(t, dict) and t.get("kind") == "user" and int(t.get("turn_id") or -1) == int(turn_id)), None)
            if t_idx is not None:
                if keep_user:
                    del self._transcript[t_idx + 1:]
                else:
                    del self._transcript[t_idx:]

            # 3. Save updated session
            try:
                self.session_manager.save_session(
                    session_id=self.current_session_id,
                    messages=self.messages,
                    model_id=self.current_model_id,
                    stats=self.stats,
                    title=self.session_title,
                    transcript=list(self._transcript),
                    workdir=self.workdir,
                    agent_mode=self.agent_mode,
                )
            except Exception:
                pass

            # 4. Remove UI widgets from chat_scroll from turn_id onwards
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            for child in list(chat_scroll.children):
                c_turn = int(getattr(child, "turn_id", -1))
                if c_turn == int(turn_id):
                    # If keeping user message, remove only the assistant card / tools, keep UserMessageCard
                    if keep_user and isinstance(child, UserMessageCard):
                        continue
                    try:
                        child.remove()
                    except Exception:
                        pass
                elif c_turn > int(turn_id):
                    try:
                        child.remove()
                    except Exception:
                        pass

            # Update turn counter
            self._turn_counter = int(turn_id) if keep_user else max(0, int(turn_id) - 1)
            self._current_assistant_card = None
            if notify:
                if keep_user:
                    self.notify(f"Reverted model response for turn {turn_id}.", timeout=2.0)
                else:
                    self.notify(f"Removed turn {turn_id}.", timeout=2.0)
        except Exception:
            pass


    def _replay_transcript(self, transcript: List[Dict[str, Any]], is_active_generating: bool = False) -> None:
        """Rebuild a session into the chat with the widgets it used before.

        Same cards, same tool blocks, same order - a reloaded session is
        supposed to be indistinguishable from the one that was saved. Grouping
        matters: an assistant turn is one card holding its prose and its tools,
        because that is how the live chat builds it.
        """
        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        card: Optional[AssistantTurnCard] = None
        marks: List[Any] = []
        self._turn_counter = 0
        turn_id = 0

        for item in transcript or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")

            if kind == "user":
                p_text = str(item.get("text", "") or "")
                if any(p_text.startswith(p) for p in (
                    "Kontynuuj na podstawie",
                    "Nie zatrzymuj się na samej",
                    "Wywołaj teraz subagenta",
                    "oraz przedstawić zwięzłe",
                )):
                    continue
                if card is not None and not getattr(card, "_replay_finished_done", False):
                    card.finish(getattr(card, "elapsed", None))
                card = None
                turn_id = int(item.get("turn_id") or 0)
                if turn_id <= 0:
                    self._turn_counter += 1
                    turn_id = self._turn_counter
                else:
                    self._turn_counter = max(self._turn_counter, turn_id)
                t_str = str(item.get("time") or "")
                if not t_str and item.get("timestamp"):
                    try:
                        t_str = time.strftime("%H:%M", time.localtime(float(item["timestamp"])))
                    except Exception:
                        t_str = ""
                if not t_str:
                    t_str = getattr(self, "_current_session_time_str", "") or ""
                if t_str and not item.get("time"):
                    item["time"] = t_str
                chat_scroll.mount(UserMessageCard(str(item.get("text", "")), turn_id=turn_id, time_str=t_str))
                continue

            if card is None:
                has_think = any(isinstance(t, dict) and t.get("kind") == "thinking" for t in transcript)
                card = AssistantTurnCard(self.current_model_id, has_thinking=True if has_think else None, turn_id=turn_id)
                chat_scroll.mount(card)
            elif getattr(card, "_replay_finished_done", False) and kind in ("thinking", "assistant", "tool", "subagent"):
                card.footer_widget.update("")
                card.footer_widget.styles.display = "none"
                card._replay_finished_done = False
                card.prepare_for_next_turn()

            if kind == "thinking":
                text = str(item.get("text", "") or "").strip()
                saved_el = float(item.get("elapsed", 0.0) or 0.0)
                if text:
                    try:
                        tb = card.thinking_block
                        if tb is not None and tb.thinking_text.strip():
                            tb.thinking_text += "\n\n" + text
                        else:
                            card.append_thinking(text)
                        if card.thinking_block is not None:
                            card.thinking_block.is_finished = False
                            card.thinking_block.finish(elapsed=saved_el if saved_el > 0.0 else None)
                            card.thinking_block.update_header(live=False)
                    except Exception:
                        pass
            elif kind == "assistant":
                text = str(item.get("text", "") or "")
                if not text.strip():
                    continue
                import re
                text = re.sub(r'</?(?:tool_response|tool_call|function_call)[^>]*>', '', text, flags=re.IGNORECASE).strip()
                if not text:
                    continue
                # Older sessions saved the model's raw tool syntax (JSON arrays,
                # <tool:...> tags) inside the text. Split it so it comes back as
                # tool blocks instead of a wall of JSON in the bubble.
                segments = None
                if self.agent_runner:
                    try:
                        segments = self.agent_runner.parse_chronological_segments(text)
                    except Exception:
                        segments = None
                if not segments:
                    card.append_text_block(text)
                    continue
                for seg_kind, seg_data in segments:
                    if seg_kind == "text":
                        if str(seg_data).strip():
                            card.append_text_block(str(seg_data))
                    elif seg_kind == "tool":
                        t_name, t_args = seg_data
                        t_args = t_args or {}
                        tgt = str(t_args.get("path") or t_args.get("file") or t_args.get("targetfile")
                                  or t_args.get("target_file") or t_args.get("filepath")
                                  or t_args.get("filename") or t_args.get("file_name")
                                  or t_args.get("dest") or t_args.get("destination")
                                  or t_args.get("target") or t_args.get("cmd")
                                  or t_args.get("command") or t_args.get("query")
                                  or t_args.get("url") or t_args.get("pattern") or "")
                        blk = card.add_tool(str(t_name), tgt)
                        if blk is not None:
                            blk_res = {"success": True, "path": tgt, "_replay_finished": True}
                            if t_args.get("content"):
                                blk_res["content"] = t_args.get("content")
                            blk.finish(blk_res)
            elif kind == "tool":
                tool_n = str(item.get("tool", "tool"))
                tool_t = str(item.get("target", ""))
                res_obj = item.get("result") or {"success": True}
                if tool_n.lower() in ("subagent", "task"):
                    from ...agent.runner import clean_subagent_call
                    cleaned_sub = clean_subagent_call(tool_t)
                    role_s = str(cleaned_sub.get("role") or "Subagent")
                    goal_s = str(cleaned_sub.get("goal") or tool_t or "")
                    block = card.add_tool("subagent", role_s)
                    if block is not None:
                        block.subagent_role = role_s
                        block.subagent_goal = goal_s
                        block.subagent_system = str(cleaned_sub.get("system") or "")
                        if item.get("is_backgrounded") or str(cleaned_sub.get("background", "")).lower() in ("true", "1", "yes"):
                            block.is_backgrounded = True

                        from ...agent.tasks import BackgroundTaskManager
                        bg_mgr = BackgroundTaskManager.get_instance()
                        sub_id = str(item.get("subagent_id") or "")
                        live_sub = bg_mgr.get_subagent(sub_id) if sub_id else None
                        if live_sub and str(live_sub.get("session_id") or "") != self.current_session_id:
                            live_sub = None
                        if not live_sub:
                            running_subs = [
                                s for s in bg_mgr.list_subagents()
                                if s.get("status") == "running" and str(s.get("session_id") or "") == self.current_session_id
                            ]
                            for s_info in running_subs:
                                if s_info.get("role", "").lower() == role_s.lower():
                                    live_sub = bg_mgr.get_subagent(s_info.get("subagent_id"))
                                    if live_sub:
                                        block.subagent_id = s_info.get("subagent_id")
                                        break
                            if not live_sub and len(running_subs) == 1:
                                live_sub = bg_mgr.get_subagent(running_subs[0].get("subagent_id"))
                                if live_sub:
                                    block.subagent_id = running_subs[0].get("subagent_id")

                        if live_sub and live_sub.get("status") == "running":
                            block.tool_running = True
                            block.is_backgrounded = True
                            block.start_time = float(live_sub.get("start_time") or time.time())
                            block.subagent_status = "running"
                            cur_t = live_sub.get("current_tool")
                            cur_tgt = live_sub.get("tool_target")
                            if cur_t:
                                block.subagent_live_tool = f"{cur_t} {cur_tgt}".strip()
                            block._start_spinner()
                            block.update_header()
                        else:
                            sum_txt = str(res_obj.get("content", "") if isinstance(res_obj, dict) else res_obj)
                            if sum_txt == tool_t:
                                sum_txt = ""
                            block.finish_subagent({
                                "role": role_s,
                                "status": "completed",
                                "goal": goal_s,
                                "summary": sum_txt,
                                "history": [{"role": "assistant", "content": sum_txt}] if sum_txt else [],
                                "events": [],
                            })
                            if item.get("elapsed_text"):
                                block.elapsed_text = str(item.get("elapsed_text"))
                            block.update_header()
                        marks.append(block)
                    continue
                if not tool_t and isinstance(res_obj, dict):
                    tool_t = str(res_obj.get("path") or res_obj.get("file") or res_obj.get("target") or "")
                block = card.add_tool(tool_n, tool_t)
                if block is not None:
                    res_copy = dict(res_obj) if isinstance(res_obj, dict) else {"success": True, "content": str(res_obj)}
                    res_copy["_replay_finished"] = True
                    if item.get("is_backgrounded") or res_copy.get("is_backgrounded") or res_copy.get("background"):
                        block.is_backgrounded = True
                        res_copy["is_backgrounded"] = True
                    if item.get("elapsed_text"):
                        block.elapsed_text = str(item.get("elapsed_text"))
                    block.finish(res_copy)
                    block.update_header()
            elif kind == "subagent":
                role = str(item.get("role", "Subagent") or "Subagent")
                block = card.add_tool("subagent", role)
                if block is None:
                    continue
                block.subagent_role = role
                block.subagent_goal = str(item.get("goal", ""))
                block.subagent_system = str(item.get("system", ""))
                agent = item.get("agent") or {}
                sub_id = str(item.get("subagent_id") or agent.get("subagent_id") or "")
                if sub_id:
                    block.subagent_id = sub_id
                if item.get("is_backgrounded") or agent.get("is_backgrounded"):
                    block.is_backgrounded = True

                from ...agent.tasks import BackgroundTaskManager
                bg_mgr = BackgroundTaskManager.get_instance()
                live_sub = bg_mgr.get_subagent(sub_id) if sub_id else None
                if live_sub and str(live_sub.get("session_id") or "") != self.current_session_id:
                    live_sub = None
                if not live_sub:
                    running_subs = [
                        s for s in bg_mgr.list_subagents()
                        if s.get("status") == "running" and str(s.get("session_id") or "") == self.current_session_id
                    ]
                    for s_info in running_subs:
                        if s_info.get("role", "").lower() == role.lower():
                            live_sub = bg_mgr.get_subagent(s_info.get("subagent_id"))
                            if live_sub:
                                block.subagent_id = s_info.get("subagent_id")
                                break
                    if not live_sub and len(running_subs) == 1:
                        live_sub = bg_mgr.get_subagent(running_subs[0].get("subagent_id"))
                        if live_sub:
                            block.subagent_id = running_subs[0].get("subagent_id")

                if live_sub and live_sub.get("status") == "running":
                    block.tool_running = True
                    block.is_backgrounded = True
                    block.start_time = float(live_sub.get("start_time") or time.time())
                    block.subagent_status = "running"
                    cur_t = live_sub.get("current_tool")
                    cur_tgt = live_sub.get("tool_target")
                    if cur_t:
                        block.subagent_live_tool = f"{cur_t} {cur_tgt}".strip()
                    block._start_spinner()
                    block.update_header()
                else:
                    block.finish_subagent(agent)
                    if item.get("elapsed_text") and not block.elapsed_text:
                        block.elapsed_text = str(item.get("elapsed_text"))
                    block.update_header()
                marks.append(block)
            elif kind == "turn_done":
                if card is not None:
                    card.finish(
                        elapsed=float(item.get("elapsed", 0.0) or 0.0),
                        tokens=int(item.get("tokens", 0) or 0),
                        tok_s=float(item.get("tok_s", 0.0) or 0.0),
                        tokens_in=int(item.get("tokens_in", 0) or 0),
                        tokens_out=int(item.get("tokens_out", 0) or 0),
                    )
                    card._replay_finished_done = True

        active_info: Dict[str, Any] = {}
        if is_active_generating and hasattr(self, "_active_generation_data"):
            cur_sid = str(self.current_session_id or "")
            active_info = dict(self._active_generation_data.get(cur_sid, {}) or {})

        if is_active_generating:
            active_tid = int(active_info.get("turn_id") or turn_id or self._turn_counter or 1)
            if card is None:
                active_model = str(active_info.get("model_id") or self.current_model_id)
                card = AssistantTurnCard(
                    active_model,
                    has_thinking=active_info.get("has_thinking"),
                    turn_id=active_tid,
                )
                chat_scroll.mount(card)
            elif getattr(card, "_replay_finished_done", False):
                if getattr(card, "turn_id", -1) == active_tid:
                    card.resume_turn()
                else:
                    active_model = str(active_info.get("model_id") or self.current_model_id)
                    card = AssistantTurnCard(
                        active_model,
                        has_thinking=active_info.get("has_thinking"),
                        turn_id=active_tid,
                    )
                    chat_scroll.mount(card)

            if card.body_widgets or card.tool_blocks or card.thinking_block is not None:
                card.prepare_for_next_turn()

            card._keep_generating_on_mount = True
            card.is_generating = True
            card._replay_finished_done = False
            card.turn_id = active_tid
            card.session_id = str(self.current_session_id or "")
            if active_info.get("started_at"):
                card.start_time = float(active_info["started_at"])

            loading_msg = str(active_info.get("loading_msg") or "")
            live_thinking = str(active_info.get("live_thinking") or "")
            live_text = str(active_info.get("live_text") or "")
            live_tool_gen = active_info.get("live_tool_gen")
            running_tool = active_info.get("running_tool")
            running_subs = list(active_info.get("running_subagents") or [])

            if loading_msg and not (live_thinking or live_text or live_tool_gen or running_tool or running_subs):
                card.start_loading(loading_msg)

            if live_thinking.strip():
                card.sync_thinking(live_thinking)
                t_start = float(active_info.get("thinking_started_at") or 0.0)
                if t_start > 0 and card.thinking_block is not None:
                    card.thinking_block.start_time = t_start
                if active_info.get("thinking_finished"):
                    card.finish_thinking()

            if live_text and live_text.strip():
                card.update_content(live_text)

            if isinstance(live_tool_gen, (tuple, list)) and len(live_tool_gen) == 3:
                card.set_generating_tool(str(live_tool_gen[0]), str(live_tool_gen[1]), str(live_tool_gen[2]))

            if isinstance(running_tool, dict) and running_tool.get("tool"):
                r_blk = card.add_tool(str(running_tool["tool"]), str(running_tool.get("target") or ""))
                if r_blk is not None:
                    if running_tool.get("started_at"):
                        r_blk.start_time = float(running_tool["started_at"])
                    sum_chunks = running_tool.get("summary_chunks")
                    if sum_chunks and hasattr(r_blk, "append_summary_chunk"):
                        r_blk.append_summary_chunk("".join(sum_chunks))

            if running_subs:
                from ...agent.tasks import BackgroundTaskManager
                bg_mgr = BackgroundTaskManager.get_instance()
                existing_sids = {str(getattr(tb, "subagent_id", "") or "") for tb in card.tool_blocks if getattr(tb, "subagent_id", "")}
                for sub_info in running_subs:
                    s_id = str(sub_info.get("subagent_id") or "")
                    if s_id and s_id in existing_sids:
                        continue
                    s_role = str(sub_info.get("role") or "Subagent")
                    s_blk = card.add_tool("subagent", s_role)
                    if s_blk is not None:
                        s_blk.subagent_id = s_id
                        s_blk.subagent_role = s_role
                        s_blk.subagent_goal = str(sub_info.get("goal") or "")
                        s_blk.subagent_system = str(sub_info.get("system") or "")
                        s_blk.subagent_index = int(sub_info.get("index") or 1)
                        s_blk.subagent_total = int(sub_info.get("total") or 1)
                        s_blk.tool_running = True
                        s_blk.subagent_status = "running"
                        if sub_info.get("started_at"):
                            s_blk.start_time = float(sub_info["started_at"])
                        live_sub = bg_mgr.get_subagent(s_id) if s_id else None
                        if live_sub:
                            cur_t = live_sub.get("current_tool")
                            cur_tgt = live_sub.get("tool_target")
                            if cur_t:
                                s_blk.subagent_live_tool = f"{cur_t} {cur_tgt}".strip()
                        s_blk._start_spinner()
                        s_blk.update_header()
                        marks.append(s_blk)

            card._start_spinner()
        elif card is not None and not getattr(card, "_replay_finished_done", False):
            card.finish(getattr(card, "elapsed", None))

        if card is not None:
            self._current_assistant_card = card
            if is_active_generating and hasattr(self, "_active_generation_data"):
                cur_sid = str(self.current_session_id or "")
                if cur_sid in self._active_generation_data:
                    self._active_generation_data[cur_sid]["card"] = card

        # Sibling position is only known once every marker exists.
        total = len(marks)
        for n, block in enumerate(marks, start=1):
            block.subagent_index = n
            block.subagent_total = total
            block.update_header()

        # Reconstruct todo list from transcript if present and not yet set
        if not getattr(getattr(self, "agent_ctx", None), "todo_list", None):
            restored_todos = []
            for item in transcript or []:
                if isinstance(item, dict) and item.get("kind") == "tool" and str(item.get("tool", "")).lower() in ("todo", "scratch", "tasks"):
                    tgt = str(item.get("target", "")).strip()
                    if tgt.lower().startswith("set:"):
                        raw = tgt[4:].strip()
                        parts = [p.strip() for p in raw.split("|") if p.strip()]
                        restored_todos = [{"text": p, "done": False} for p in parts]
                    elif tgt.lower().startswith("done:"):
                        idx_str = tgt[5:].strip()
                        try:
                            d_idx = int(idx_str) - 1
                            if 0 <= d_idx < len(restored_todos):
                                restored_todos[d_idx]["done"] = True
                        except Exception:
                            pass
            if restored_todos and hasattr(self, "agent_ctx"):
                self.agent_ctx.todo_list = restored_todos
                try:
                    self.query_one("#todo-preview-bar", TodoPreviewBar).update_steps([
                        t["text"] for t in restored_todos if not t.get("done")
                    ])
                except Exception:
                    pass

        # If in code mode and not active generating, check whether the last turn ended with file modifications
        if getattr(self, "agent_mode", "") == "code" and not is_active_generating and not getattr(self, "_post_turn_approval_active", False):
            last_turn_files = []
            for item in reversed(transcript or []):
                if not isinstance(item, dict):
                    continue
                if item.get("kind") == "user":
                    break
                if item.get("kind") == "tool" and str(item.get("tool", "")).lower() in ("write", "edit", "patch"):
                    fpath = str(item.get("target", "") or "")
                    res = item.get("result", {})
                    if not fpath and isinstance(res, dict):
                        fpath = str(res.get("path") or res.get("file") or "")
                    if fpath:
                        fclean = fpath.split("(")[0].strip()
                        if fclean and fclean not in last_turn_files:
                            last_turn_files.append(fclean)

            if last_turn_files:
                cnt = len(last_turn_files)
                summary_target = last_turn_files[0] if cnt == 1 else f"{cnt} files"
                details = {
                    "action": "edit",
                    "path": summary_target,
                    "file_count": cnt,
                    "diff_info": "",
                    "files": list(last_turn_files),
                    "lines_added": 0,
                    "lines_deleted": 0,
                }
                self._pending_approval_details = details
                self._post_turn_approval_active = True
                self._last_modified_files = list(last_turn_files)
                try:
                    bar = self.query_one("#approval-bar", ApprovalBar)
                    bar.show_request(details)
                except Exception:
                    pass

        chat_scroll.scroll_end(animate=False)
        self._refresh_timeline_rail()


    def _on_session_selected(self, session_id: str) -> None:
        if not session_id:
            return
        if session_id == self.current_session_id and self.in_chat_mode:
            return
        old_sid = str(self.current_session_id or "")
        old_is_empty = (not self.messages and not self._transcript)

        # If previous session was an empty new session, transform it into the selected session!
        if hasattr(self, "_open_session_ids"):
            if old_sid in self._open_session_ids and old_is_empty and old_sid != session_id:
                idx = self._open_session_ids.index(old_sid)
                if session_id in self._open_session_ids:
                    self._open_session_ids.remove(old_sid)
                else:
                    self._open_session_ids[idx] = session_id
            elif session_id not in self._open_session_ids:
                self._open_session_ids.append(session_id)
        if self.is_generating:
            try:
                old_active = getattr(self, "_active_generation_data", {}).get(old_sid, {})
                save_msgs = list(old_active.get("messages") or self.messages or [])
                save_tr = list(old_active.get("transcript") or self._transcript or [])
                save_stats = dict(old_active.get("stats") or self.stats or {})
                if old_sid and (save_msgs or save_tr):
                    self._save_session_data(
                        session_id=old_sid,
                        messages=save_msgs,
                        transcript=save_tr,
                        stats=save_stats,
                        model_id=old_active.get("model_id") or self.current_model_id,
                        workdir=old_active.get("workdir") or self.workdir,
                        agent_mode=old_active.get("agent_mode") or self.agent_mode,
                    )
            except Exception:
                pass
        else:
            try:
                if old_sid and (self.messages or self._transcript):
                    self._save_current_session(session_id=old_sid)
            except Exception:
                pass
        self._current_assistant_card = None

        try:
            data = self.session_manager.load_session(session_id)
        except Exception:
            data = None
        if not data:
            if session_id in getattr(self, "_open_session_ids", []):
                data = {
                    "session_id": session_id,
                    "title": "New session",
                    "messages": [],
                    "transcript": [],
                    "stats": {"tokens_in": 0, "tokens_out": 0, "tok_per_sec": 0.0, "tools_run": 0}
                }
            else:
                self.notify("That session could not be read.", severity="error", timeout=4.0)
                return

        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            for child in list(chat_scroll.children):
                try:
                    child.remove()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.query_one("#approval-bar", ApprovalBar).hide_request()
            self.query_one("#approval-diff-preview", ApprovalDiffPreview).hide_diff()
        except Exception:
            pass
        self._post_turn_approval_active = False
        self._pending_approval_details = None
        self._last_modified_files.clear()
        try:
            self.query_one("#todo-preview-bar", TodoPreviewBar).update_steps([])
        except Exception:
            pass
        if hasattr(self, "agent_ctx"):
            self.agent_ctx.todo_list = []

        self.current_session_id = session_id
        self._session_flushed_on_exit = False
        sess_created = str(data.get("created_at") or data.get("last_written_at") or "")
        sess_time = ""
        if sess_created:
            parts = sess_created.strip().split()
            for part in parts:
                if ":" in part:
                    sess_time = part[:5]
                    break
        if not sess_time and data.get("created_ts"):
            try:
                sess_time = time.strftime("%H:%M", time.localtime(float(data["created_ts"])))
            except Exception:
                sess_time = ""
        self._current_session_time_str = sess_time
        sess_dir = data.get("directory") or data.get("workdir")
        if sess_dir and os.path.isdir(str(sess_dir)):
            try:
                new_workdir = os.path.abspath(str(sess_dir))
                self.workdir = new_workdir
                os.chdir(new_workdir)
                if hasattr(self, "agent_ctx"):
                    self.agent_ctx.workdir = new_workdir
                    self.agent_ctx.current_cwd = new_workdir
                    self.agent_ctx.mode = self.agent_mode
                if hasattr(self, "agent_runner"):
                    self.agent_runner.workdir = new_workdir
                if hasattr(self, "session_manager"):
                    self.session_manager.project_dir = new_workdir
                if hasattr(self, "system_prompt") and "Current working directory:" in self.system_prompt:
                    import re as _re
                    self.system_prompt = _re.sub(
                        r"Current working directory: [^\n]+",
                        f"Current working directory: {self.workdir}",
                        self.system_prompt,
                    )
            except Exception:
                pass
        self.messages = list(data.get("messages", []) or [])
        self.session_title = str(data.get("title", "") or "")
        bad_titles = ("new session", "untitled", "untitled session", "session", "tool", "mock", "tool mock")
        if not self.session_title.strip() or self.session_title.strip().lower() in bad_titles or self.session_title.startswith("Session session_"):
            first_u = next((str(m.get("content", "")) for m in self.messages if isinstance(m, dict) and m.get("role") == "user"), "")
            from ...core.sessions import clean_title_from_prompt
            derived = clean_title_from_prompt(first_u)
            if derived:
                self.session_title = derived
        self._session_named_by_model = bool(data.get("named_by_model", False))
        self.stats = dict(data.get("stats") or {
            "tokens_in": 0,
            "tokens_out": 0,
            "tok_per_sec": 0.0,
            "tools_run": 0,
        })
        self._current_assistant_card = None

        is_active_gen = bool(session_id in getattr(self, "_active_generation_sessions", set()))
        active_info: Dict[str, Any] = {}
        if is_active_gen and hasattr(self, "_active_generation_data") and session_id in self._active_generation_data:
            active_info = self._active_generation_data[session_id]
            self.messages = list(active_info.get("messages") or list(data.get("messages", []) or []))
            self._transcript = list(active_info.get("transcript") or [])
            if isinstance(active_info.get("stats"), dict):
                self.stats = dict(active_info["stats"])
            if active_info.get("provider_id"):
                self.current_provider = str(active_info["provider_id"])
        else:
            from ...core.sessions import reconcile_transcript
            transcript = data.get("transcript")
            self._transcript = reconcile_transcript(transcript, self.messages)

        target_model = active_info.get("model_id") or data.get("model_id")
        if target_model:
            self.current_model_id = str(target_model)
            # Every other model change writes through to the config, so the
            # next launch starts where this one is. Loading a session used to
            # be the exception: the model changed on screen while the file kept
            # the old one, and the next launch silently reverted.
            self.settings.config["default_model"] = self.current_model_id
            self.settings.save_config()

        # Switch to chat view or hero view based on whether messages exist
        if self.messages or self._transcript or is_active_gen:
            self._switch_to_chat()
            try:
                self._replay_transcript(self._transcript, is_active_generating=is_active_gen)
            except Exception as ex:
                import logging
                logging.exception("Error replaying transcript: %s", ex)
            if data.get("todo_list") and hasattr(self, "agent_ctx"):
                self.agent_ctx.todo_list = list(data["todo_list"])
                try:
                    self.query_one("#todo-preview-bar", TodoPreviewBar).update_steps([
                        t["text"] if isinstance(t, dict) else str(t)
                        for t in self.agent_ctx.todo_list
                        if not (isinstance(t, dict) and t.get("done"))
                    ])
                except Exception:
                    pass
            pend = data.get("pending_approval")
            if pend and isinstance(pend, dict) and not is_active_gen:
                self._pending_approval_details = pend
                self._post_turn_approval_active = True
                if pend.get("files"):
                    self._last_modified_files = list(pend.get("files"))
                try:
                    bar = self.query_one("#approval-bar", ApprovalBar)
                    bar.show_request(pend)
                except Exception:
                    pass
        else:
            self._switch_to_hero()
        self.update_meta_bars()
        self._update_terminal_title()
        if hasattr(self, "_open_session_ids"):
            if session_id not in self._open_session_ids:
                self._open_session_ids.append(session_id)
        self._refresh_session_tabs()
        try:
            self.call_after_refresh(lambda: self.query_one("#prompt-input", AutoExpandingInput).focus())
        except Exception:
            pass

