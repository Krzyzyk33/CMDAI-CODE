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



class WorkerMixin:
    """Mixin for CMDAICodeTUI."""

    def trigger_summarize(self, auto: bool = False) -> None:
        if not self.messages:
            return

        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        block = ToolBlock("summarizing", "")
        block.styles.padding = (0, 3)
        chat_scroll.mount(block)
        chat_scroll.scroll_end(animate=False)

        self._summarize_worker(block, auto)


    def _run_summarization_call(
        self,
        payload: List[Dict[str, str]],
        on_chunk: Optional[Any] = None,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> Tuple[str, str]:
        prov = provider_id or self.current_provider
        mod = model_id or self.current_model_id
        api_key = self.settings.get_api_key(prov)
        try:
            if prov == "local_gguf":
                resp, _ = self.gguf_loader.stream_chat(
                    messages=payload,
                    temperature=0.3,
                    max_tokens=1536,
                    model_filename=mod,
                    on_token=on_chunk,
                )
            else:
                resp, _ = stream_chat_completion(
                    provider_id=prov,
                    model_id=mod,
                    messages=payload,
                    api_key=api_key,
                    generation_params={"temperature": 0.3, "max_tokens": 1024, "reasoning_level": "off"},
                    on_token=on_chunk,
                )
            return str(resp or ""), ""
        except Exception as exc:
            return "", str(exc)


    @work(thread=True)
    def _summarize_worker(self, tool_block: ToolBlock, auto: bool = False) -> None:
        ctx_limit = self._dynamic_limit()
        if self.current_provider == "local_gguf":
            loaded_n_ctx = int(getattr(self.gguf_loader, "current_n_ctx", 0) or 0)
            if loaded_n_ctx > 0:
                ctx_limit = min(ctx_limit, loaded_n_ctx)
        payload = build_summarization_payload(
            self.messages,
            last_modified_files=getattr(self, "_last_modified_files", None),
            max_budget_tokens=min(2500, max(1200, ctx_limit // 2)),
        )

        def _on_sum_chunk(chunk: str) -> None:
            if chunk:
                try:
                    self.call_from_thread(tool_block.append_summary_chunk, chunk)
                except Exception:
                    pass

        summary_text, sum_err = self._run_summarization_call(payload, on_chunk=_on_sum_chunk)
        is_err = bool(sum_err) or is_summarization_error_text(summary_text)
        if is_err:
            err_detail = sum_err or (summary_text.strip()[:120] if summary_text else "Model returned empty summary.")
            try:
                self.call_from_thread(
                    self.notify,
                    f"Summarization fallback used: {err_detail}",
                    title="Summarize",
                    severity="warning",
                    timeout=5.0,
                )
            except Exception:
                pass

        handoff_content = format_compacted_handoff(
            summary_text if not is_err else "",
            last_modified_files=getattr(self, "_last_modified_files", None),
            lang=detect_conversation_language(self.messages),
        )

        init_goal = self.messages[0]["content"][:300] if self.messages else ""
        self.messages = [
            {"role": "user", "content": "Poprzednie ustalenia i cel projektu:\n" + init_goal},
            {"role": "assistant", "content": handoff_content.strip()},
        ]

        def _update_ui():
            try:
                sum_res = {"success": True, "summary": handoff_content.strip()}
                tool_block.finish(sum_res)
                self._record_tool("summarizing", "", sum_res)
                self._save_current_session()
                chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
                chat_scroll.scroll_end(animate=False)
            except Exception:
                pass

        self.call_from_thread(_update_ui)


    def _attach_pending_images(self, payload_messages: List[Dict[str, Any]]) -> None:
        pending = list(getattr(self.agent_ctx, "pending_images", []) or [])
        if not pending:
            return
        try:
            vcap = self._capability()
        except Exception:
            vcap = None
        if not vcap or not vcap.vision:
            self.agent_ctx.pending_images.clear()
            return
        if self.current_provider == "local_gguf":
                                                                                
                                                                               
            self.agent_ctx.pending_images.clear()
            for m in reversed(payload_messages):
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    m["content"] += "\n[vision: %d image(s) captured but the local build has no image projector - describe them from paths if needed]" % len(pending)
                    break
            return
        parts: List[Dict[str, Any]] = []
        for entry in pending:
            uri = self._image_to_data_uri(entry.get("path", ""))
            if not uri:
                continue
            q = entry.get("question", "")
            if q:
                parts.append({"type": "text", "text": f"[image question] {q}"})
            parts.append({"type": "image_url", "image_url": {"url": uri}})
        self.agent_ctx.pending_images.clear()
        if not parts:
            return
        for m in reversed(payload_messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    m["content"] = [{"type": "text", "text": content}, *parts]
                elif isinstance(content, list):
                    m["content"] = [*content, *parts]
                break


    @staticmethod
    def _image_to_data_uri(path: str) -> str:
        try:
            if not path or not os.path.exists(path):
                return ""
            if os.path.getsize(path) > 4_000_000:
                return ""
            import base64
            try:
                from PIL import Image
                import io
                img = Image.open(path).convert("RGB")
                img.thumbnail((1568, 1568))
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=80)
                return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
            except ImportError:
                import base64 as _b64
                with open(path, "rb") as f:
                    return "data:image/png;base64," + _b64.b64encode(f.read()).decode("ascii")
        except Exception:
            return ""
        return ""


    def start_generation(self, user_prompt: str, resume_existing: bool = False) -> None:
        if (
            not resume_existing
            and self.current_provider not in ("local_gguf", "ollama", "lmstudio", "vllm", "opencode_local", "local_opencode")
            and not self.settings.get_api_key(self.current_provider)
        ):
            from ..modals.api_key import ApiKeyModal

            def _after_key(saved: bool) -> None:
                if saved and self.settings.get_api_key(self.current_provider):
                    self.start_generation(user_prompt, resume_existing=False)
                else:
                    self.notify(
                        f"API key required for {self.current_provider}. Configure via /models -> Ctrl+D.",
                        timeout=4.0,
                    )

            self.notify("API key required — enter key & server URL to continue.", timeout=3.0)
            self.push_screen(ApiKeyModal(self.current_provider), _after_key)
            return

        self.is_generating = True
        self.abort_requested = False
        self.auto_scroll_enabled = True
        self._last_user_scroll_time = 0.0
        self._set_follow_hint(False)

        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)

        cap = self._capability()
        has_thinking_support = bool(cap and cap.thinking)

        if resume_existing:
            if self._current_assistant_card is None:
                try:
                    cards = [c for c in chat_scroll.children if isinstance(c, AssistantTurnCard)]
                    if cards:
                        self._current_assistant_card = cards[-1]
                except Exception:
                    pass

        initial_loading_msg = ""
        if resume_existing and self._current_assistant_card is not None:
            assistant_card = self._current_assistant_card
            assistant_card.resume_turn()
        else:
            assistant_card = AssistantTurnCard(self.current_model_id, has_thinking=has_thinking_support, turn_id=self._turn_counter)
            self._current_assistant_card = assistant_card
            if self.current_provider == "local_gguf":
                loaded_path = getattr(self.gguf_loader, "current_model_path", "") or ""
                active_loader = str(self.settings.config.get("active_loader", "cpu") or "cpu").strip().lower()
                loaded_req_loader = getattr(self.gguf_loader, "requested_loader", None)
                needs_load = (
                    self.gguf_loader.llm is None
                    or os.path.basename(loaded_path) != os.path.basename(self.current_model_id)
                    or (loaded_req_loader is not None and loaded_req_loader != active_loader)
                )
                if needs_load:
                    initial_loading_msg = f"Loading model ({active_loader})…"
                    assistant_card.start_loading(initial_loading_msg)
            _ = has_thinking_support
            chat_scroll.mount(assistant_card)

        self._scroll_chat_to_end_if_enabled(force=True)

        worker_sid = str(getattr(assistant_card, "session_id", "") or self.current_session_id)
        assistant_card.session_id = worker_sid
        self._active_generation_sessions.add(worker_sid)
        assistant_card._worker_messages = list(self.messages)
        assistant_card._worker_transcript = list(self._transcript)
        if not hasattr(self, "_active_generation_data"):
            self._active_generation_data = {}
        self._active_generation_data[worker_sid] = {
            "card": assistant_card,
            "turn_id": int(getattr(assistant_card, "turn_id", self._turn_counter) or self._turn_counter),
            "messages": assistant_card._worker_messages,
            "transcript": assistant_card._worker_transcript,
            "model_id": self.current_model_id,
            "provider_id": self.current_provider,
            "workdir": self.workdir,
            "agent_mode": self.agent_mode,
            "has_thinking": has_thinking_support,
            "started_at": float(getattr(assistant_card, "start_time", time.time()) or time.time()),
            "loading_msg": initial_loading_msg,
            "live_thinking": "",
            "thinking_started_at": 0.0,
            "thinking_finished": False,
            "live_text": "",
            "live_tool_gen": None,
            "running_tool": None,
            "running_subagents": [],
            "abort_requested": False,
            "stats": dict(self.stats or {"tokens_in": 0, "tokens_out": 0, "tok_per_sec": 0.0, "tools_run": 0}),
        }

        self._generation_worker(assistant_card)


    @work(thread=True)
    def _generation_worker(self, assistant_card: AssistantTurnCard) -> None:
        try:
            self._generation_worker_impl(assistant_card)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            try:
                worker_sid_err = getattr(assistant_card, "session_id", "")
                if self.current_session_id == worker_sid_err:
                    target_c = getattr(self, "_active_generation_data", {}).get(worker_sid_err, {}).get("card") or assistant_card
                    self.call_from_thread(target_c.update_content, f"**Execution error**: {exc}")
            except Exception:
                pass
        finally:
            worker_sid = getattr(assistant_card, "session_id", None) or getattr(self, "current_session_id", "")
            active_c = assistant_card
            live_info: Dict[str, Any] = {}
            is_still_active_worker = True
            if hasattr(self, "_active_generation_data") and worker_sid in self._active_generation_data:
                live_info = self._active_generation_data[worker_sid]
                reg_c = live_info.get("card")
                if reg_c is not None and getattr(reg_c, "turn_id", -1) != getattr(assistant_card, "turn_id", -2):
                    is_still_active_worker = False
                else:
                    active_c = reg_c or assistant_card
                    del self._active_generation_data[worker_sid]

            if is_still_active_worker and worker_sid and hasattr(self, "_active_generation_sessions"):
                self._active_generation_sessions.discard(str(worker_sid))
            if self.current_session_id == worker_sid:
                if getattr(active_c, "is_generating", False) and is_still_active_worker:
                    self.call_from_thread(active_c.finish, 0.0, 0, 0.0)
                if getattr(assistant_card, "is_generating", False) and assistant_card is not active_c:
                    self.call_from_thread(assistant_card.finish, 0.0, 0, 0.0)
            self.call_from_thread(self._update_meta_bars)
            if is_still_active_worker and self.current_session_id == worker_sid:
                self.abort_requested = False
            if self.current_session_id == worker_sid:
                self.call_from_thread(self._save_current_session)
                if is_still_active_worker:
                    self.call_from_thread(self._maybe_auto_name_session)
            else:
                self._save_session_data(
                    session_id=worker_sid,
                    messages=live_info.get("messages") or getattr(assistant_card, "_worker_messages", self.messages),
                    transcript=live_info.get("transcript") or getattr(assistant_card, "_worker_transcript", self._transcript),
                    stats=live_info.get("stats"),
                    model_id=live_info.get("model_id"),
                    workdir=live_info.get("workdir"),
                    agent_mode=live_info.get("agent_mode"),
                )


    def _generation_worker_impl(
        self,
        assistant_card: AssistantTurnCard,
    ) -> None:
        start_time = time.time()
        worker_session_id = str(getattr(assistant_card, "session_id", "") or self.current_session_id)
        assistant_card.session_id = worker_session_id
        if not hasattr(assistant_card, "_worker_messages"):
            assistant_card._worker_messages = list(self.messages)
        if not hasattr(assistant_card, "_worker_transcript"):
            assistant_card._worker_transcript = list(self._transcript)
        worker_messages = assistant_card._worker_messages
        worker_transcript = assistant_card._worker_transcript
        live_state = getattr(self, "_active_generation_data", {}).get(worker_session_id)
        if not isinstance(live_state, dict):
            live_state = {}
        worker_provider = str(live_state.get("provider_id") or self.current_provider)
        worker_model_id = str(live_state.get("model_id") or self.current_model_id)
        worker_workdir = str(live_state.get("workdir") or self.workdir)
        worker_agent_mode = str(live_state.get("agent_mode") or self.agent_mode)
        worker_stats = live_state.setdefault(
            "stats",
            dict(self.stats or {"tokens_in": 0, "tokens_out": 0, "tok_per_sec": 0.0, "tools_run": 0}),
        )

        def is_worker_aborted() -> bool:
            cur_c = live_state.get("card") or assistant_card
            return bool(
                live_state.get("abort_requested", False)
                or getattr(assistant_card, "abort_requested", False)
                or getattr(cur_c, "abort_requested", False)
                or (self.current_session_id == worker_session_id and self.abort_requested)
            )

        def _card() -> AssistantTurnCard:
            target_tid = int(live_state.get("turn_id") or getattr(assistant_card, "turn_id", -2) or -2)
            if (
                self.current_session_id == worker_session_id
                and self._current_assistant_card is not None
                and getattr(self._current_assistant_card, "turn_id", -1) == target_tid
            ):
                return self._current_assistant_card
            active_info = getattr(self, "_active_generation_data", {}).get(worker_session_id)
            if active_info and active_info.get("card") and getattr(active_info.get("card"), "turn_id", -1) == target_tid:
                return active_info["card"]
            return assistant_card

        def _call_card(method_name: str, *args, **kwargs) -> None:
            def _runner():
                if self.current_session_id != worker_session_id:
                    return
                c = _card()
                m = getattr(c, method_name, None)
                if m:
                    try:
                        m(*args, **kwargs)
                    except Exception:
                        pass
            self.call_from_thread(_runner)

        def _clear_live_step_state() -> None:
            live_state["live_thinking"] = ""
            live_state["thinking_started_at"] = 0.0
            live_state["thinking_finished"] = False
            live_state["live_text"] = ""
            live_state["live_tool_gen"] = None
            live_state["running_tool"] = None

        def _prepare_next_turn() -> None:
            _clear_live_step_state()
            _call_card("prepare_for_next_turn")

        def _add_message(msg: Dict[str, Any]) -> None:
            worker_messages.append(msg)
            if self.current_session_id == worker_session_id and worker_messages is not self.messages:
                if not (self.messages and self.messages[-1] == msg):
                    self.messages.append(msg)

        def _add_transcript(item: Dict[str, Any]) -> None:
            worker_transcript.append(item)
            if self.current_session_id == worker_session_id and worker_transcript is not self._transcript:
                self._transcript.append(item)

        def _rec_assistant(t_str: str) -> None:
            self._record_assistant_text(t_str, target_transcript=worker_transcript)
            if self.current_session_id == worker_session_id and worker_transcript is not self._transcript:
                self._record_assistant_text(t_str, target_transcript=self._transcript)

        def _rec_tool(t_name: str, t_target: str, t_res: Any) -> None:
            self._record_tool(t_name, t_target, t_res, target_transcript=worker_transcript)
            if self.current_session_id == worker_session_id and worker_transcript is not self._transcript:
                self._record_tool(t_name, t_target, t_res, target_transcript=self._transcript)

        current_turn_id = int(getattr(assistant_card, "turn_id", -1) or self._turn_counter or 0)
        if current_turn_id > 0:
            self._capture_workspace_snapshot(current_turn_id)
        if hasattr(self, "agent_ctx") and self.agent_ctx is not None:
            saved_todos = list(getattr(self.agent_ctx, "todo_list", []) or [])
            self.agent_ctx.workdir = worker_workdir
            self.agent_ctx.current_cwd = worker_workdir
            self.agent_ctx.session_id = worker_session_id
            self.agent_ctx.mode = worker_agent_mode
            if saved_todos:
                self.agent_ctx.todo_list = saved_todos
        if hasattr(self, "agent_runner") and self.agent_runner is not None:
            self.agent_runner.workdir = worker_workdir
        api_key = self.settings.get_api_key(worker_provider)
        gen_params = dict(self.settings.config.get("generation", {}) or {})
        gen_params["workdir"] = worker_workdir

        def _on_tool_start(tool_name: str, target: str) -> None:
            worker_stats["tools_run"] = worker_stats.get("tools_run", 0) + 1
            if self.current_session_id == worker_session_id:
                self.stats["tools_run"] = worker_stats["tools_run"]
            if tool_name.lower() in ("write", "edit") and target and current_turn_id > 0:
                self._snapshot_file_before_modify(current_turn_id, target.split(" (")[0].strip())
            if self._is_silent_tool_call(tool_name, target):
                # A bare <tool:mcp /> only asks which servers exist. It does
                # no work and its answer is available in the /mcp window, so a
                # card for it was noise in every single MCP turn. The model
                # still receives the full result.
                return
            live_state["live_tool_gen"] = None
            live_state["thinking_finished"] = True
            live_state["running_tool"] = {
                "tool": tool_name,
                "target": target,
                "started_at": time.time(),
                "summary_chunks": [],
            }
            _call_card("add_tool", tool_name, target)
            if self.current_session_id == worker_session_id:
                self.call_from_thread(self._scroll_chat_to_end_if_enabled)

        self._turn_diff_stats = {"added": 0, "deleted": 0}

        def _on_tool_finish(tool_name: str, target: str, res: Dict[str, Any]) -> None:
            if self._is_silent_tool_call(tool_name, target):
                # No card was started, so finish_tool must not run either: with
                # nothing running it falls back to the last block and would
                # paint this result onto the previous tool.
                _rec_tool(tool_name, target, res)
                self.call_from_thread(self.refresh)
                return
            if tool_name in ("write", "edit") and res.get("success"):
                p = res.get("path") or target
                if p:
                    self._last_modified_files.append(p)
                entries = res.get("diff_entries", [])
                adds = sum(1 for e in entries if len(e) > 1 and e[1] == "add")
                dels = sum(1 for e in entries if len(e) > 1 and e[1] == "del")
                if tool_name == "write" and not entries:
                    adds = len(str(res.get("content", "")).splitlines()) or 1
                self._turn_diff_stats["added"] += adds
                self._turn_diff_stats["deleted"] += dels
            _rec_tool(tool_name, target, res)
            live_state["running_tool"] = None
            _call_card("finish_tool", res)
            if self.current_session_id == worker_session_id:
                self.call_from_thread(self._scroll_chat_to_end_if_enabled)
            self.call_from_thread(self.refresh)

        if self.agent_runner:
            self.agent_runner.on_tool_start = _on_tool_start
            self.agent_runner.on_tool_finish = _on_tool_finish
            self.agent_runner.ask_handler = self._handle_agent_ask
            self.agent_runner.provider_id = worker_provider
            if hasattr(self.agent_runner, "ctx") and self.agent_runner.ctx is not None:
                self.agent_runner.ctx.on_before_file_modify = lambda p, _tid=current_turn_id: self._snapshot_file_before_modify(_tid, p)

        if worker_provider == "local_gguf":
            loaded_path = getattr(self.gguf_loader, "current_model_path", "") or ""
            active_loader = str(self.settings.config.get("active_loader", "cpu") or "cpu").strip().lower()
            loaded_req_loader = getattr(self.gguf_loader, "requested_loader", None)
            needs_load = (
                self.gguf_loader.llm is None
                or os.path.basename(loaded_path) != os.path.basename(worker_model_id)
                or (loaded_req_loader is not None and loaded_req_loader != active_loader)
            )
            if needs_load:
                load_start = time.time()
                target_n_ctx = min(32768, self._dynamic_limit())
                loaded = self.gguf_loader.load_model(worker_model_id, n_ctx=target_n_ctx, loader_type=active_loader)
                load_elapsed = time.time() - load_start
                live_state["loading_msg"] = ""
                if not loaded:
                    err_msg = f"Model '{worker_model_id}' not found or could not be loaded."
                    live_state["live_text"] = f"**Error**: {err_msg}"
                    _call_card("finish_loading", load_elapsed)
                    _call_card("update_content", f"**Error**: {err_msg}")
                    _call_card("finish", load_elapsed, 0, 0.0)
                    self._active_generation_sessions.discard(worker_session_id)
                    return
                _call_card("finish_loading", load_elapsed)
                load_warn = getattr(self.gguf_loader, "last_load_warning", "")
                if load_warn:
                    try:
                        self.call_from_thread(
                            self.notify,
                            load_warn,
                            title="GGUF Loader Fallback",
                            severity="warning",
                            timeout=5.5,
                        )
                    except Exception:
                        pass

        turn = 0
        total_tokens = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_gen_time = 0.0
        last_tool_failed = [False]
        had_api_err = False
        completed_subagent_roles: Dict[str, str] = {}
        tool_call_counts: Dict[str, int] = {}
        stop_after_subagent_repeat = [False]
        empty_after_tools_nudged = [False]
        max_agent_turns = 15 if worker_provider == "local_gguf" else 25

        while not is_worker_aborted() and not stop_after_subagent_repeat[0]:
            turn += 1
            if turn > max_agent_turns:
                break
            step_chunks: List[str] = []
            step_thinking: List[str] = []
            self._live_step_chunks = step_chunks
            self._live_step_thinking = step_thinking
            last_token_render = [0.0]
            step_tool_completed = [False]
            step_usage: Dict[str, Any] = {}
            first_token_time = [0.0]
            last_token_time = [0.0]

            def _on_step_usage(u: Dict[str, Any]) -> None:
                step_usage.update(u)

            def on_token(chunk: str) -> None:
                now_t = time.time()
                if not first_token_time[0]:
                    first_token_time[0] = now_t
                last_token_time[0] = now_t
                live_state["loading_msg"] = ""
                if live_state.get("live_thinking") and not live_state.get("thinking_finished"):
                    live_state["thinking_finished"] = True
                if self.current_session_id == worker_session_id:
                    tb = getattr(_card(), "thinking_block", None)
                    if tb and not tb.is_finished:
                        _call_card("finish_thinking")
                if is_worker_aborted() or step_tool_completed[0]:
                    return
                step_chunks.append(chunk)
                accumulated = "".join(step_chunks)

                if is_stream_tool_or_turn_complete(accumulated):
                    step_tool_completed[0] = True

                now = time.time()
                if server_tool_running[0]:
                    # Any text delta arriving while a server tool is running is the
                    # buffered tail of the sentence before the tool call.
                    pre_slice = accumulated[pre_tool_start[0]:]
                    clean_pre = self.agent_runner.strip_xml_tool_calls(pre_slice) if self.agent_runner else pre_slice
                    clean_pre = re.sub(r'</?(?:tool_response|tool_call|function_call)[^>]*>', '', clean_pre, flags=re.IGNORECASE).strip()
                    _call_card("update_pre_tool_content", clean_pre)
                    step_rendered[0] = len(accumulated)
                    return

                if server_tools_ran[0] > 0 and chunk.strip():
                    post_tool_text_seen[0] = True

                if now - last_token_render[0] > 0.03 or step_tool_completed[0]:
                    fresh = accumulated[step_rendered[0]:]
                    tl_name, tl_tgt, live_content = self._extract_live_tool_stream(fresh)
                    if tl_name and (tl_tgt or tl_name not in ("write", "edit", "read")):
                        live_state["live_tool_gen"] = (tl_name, tl_tgt, live_content)
                        _call_card("set_generating_tool", tl_name, tl_tgt, live_content)
                    else:
                        live_state["live_tool_gen"] = None
                        _call_card("clear_generating_tool")

                    clean_preview = self.agent_runner.strip_xml_tool_calls(fresh) if self.agent_runner else fresh
                    clean_preview = re.sub(r'</?(?:tool_response|tool_call|function_call)[^>]*>', '', clean_preview, flags=re.IGNORECASE)
                    clean_preview = re.sub(
                        r'(?:<(?:tool:|\/tool_call:?|tool_call:?|\|tool_call>call:)[a-zA-Z0-9_-]*.*$|```(?:json)?\s*\[?\s*\{.*$|<[a-zA-Z0-9_:|/-]*$)',
                        '',
                        clean_preview,
                        flags=re.DOTALL | re.IGNORECASE,
                    )
                    live_state["live_text"] = clean_preview
                    _call_card("update_content", clean_preview)
                    if self.current_session_id == worker_session_id:
                        self.call_from_thread(self._scroll_chat_to_end_if_enabled)

            last_thinking_render = [0.0]
            # How much of the turn has already been rendered into the current
            # text region. A server-side tool call moves the region on, so
            # anything below this point still needs writing; anything above it
            # would be printed twice.
            step_rendered = [0]
            pre_tool_start = [0]
            server_tool_running = [False]
            server_tools_ran = [0]
            post_tool_text_seen = [False]

            def _on_server_tool(name: str, phase: str, detail: str) -> None:
                """A tool the provider ran itself, mid-turn."""
                if str(name or "").lower() == "question":
                    return
                live_state["thinking_finished"] = True
                _call_card("finish_thinking")
                if phase in ("generating", "running"):
                    accumulated_now = "".join(step_chunks)
                    pending = accumulated_now[step_rendered[0]:]
                    if pending:
                        clean_pending = self.agent_runner.strip_xml_tool_calls(pending) if self.agent_runner else pending
                        clean_pending = re.sub(r'</?(?:tool_response|tool_call|function_call)[^>]*>', '', clean_pending, flags=re.IGNORECASE)
                        clean_pending = re.sub(
                            r'(?:<(?:tool:|\/tool_call:?|tool_call:?|\|tool_call>call:)[a-zA-Z0-9_-]+.*$|```(?:json)?\s*\[?\s*\{.*$)',
                            '',
                            clean_pending,
                            flags=re.DOTALL | re.IGNORECASE,
                        )
                        _call_card("update_content", clean_pending)
                        if clean_pending.strip():
                            _rec_assistant(clean_pending.strip())
                            live_state["live_text"] = ""
                    if not server_tool_running[0]:
                        pre_tool_start[0] = step_rendered[0]
                    step_rendered[0] = len(accumulated_now)
                    server_tool_running[0] = True
                _call_card("note_server_tool", name, phase, detail)
                if phase in ("done", "finished", "failed"):
                    server_tool_running[0] = False
                    server_tools_ran[0] += 1
                    post_tool_text_seen[0] = False
                    step_rendered[0] = len("".join(step_chunks))
                    pre_tool_start[0] = step_rendered[0]
                if phase in ("done", "finished"):
                    if name.lower() in ("write", "edit") and detail:
                        tgt_f = detail.splitlines()[0].split(" (")[0].strip()
                        if tgt_f and tgt_f not in ("(no target)", "ok", "done"):
                            self._last_modified_files.append(tgt_f)
                    _rec_tool(name, detail, {"success": True, "content": detail})

            def on_thinking(chunk: str) -> None:
                now_t = time.time()
                if not first_token_time[0]:
                    first_token_time[0] = now_t
                last_token_time[0] = now_t
                if is_worker_aborted():
                    return
                # No gate on the detected capability here, on purpose. A
                # reasoning chunk arriving is the proof; the cache it would be
                # checked against is the very thing that was wrong before. And
                # this runs per delta, so a `get_model_capability` call would be
                # a cache miss on every token for a model detected mid-stream.
                live_state["loading_msg"] = ""
                if not live_state.get("thinking_started_at"):
                    live_state["thinking_started_at"] = now_t
                live_state["thinking_finished"] = False
                step_thinking.append(chunk)
                full_think = "".join(step_thinking)
                live_state["live_thinking"] = full_think
                now = time.time()
                _call_card("sync_thinking", full_think)
                if now - last_thinking_render[0] > 0.05:
                    last_thinking_render[0] = now
                    if self.current_session_id == worker_session_id:
                        self.call_from_thread(self._scroll_chat_to_end_if_enabled)

            try:
                has_vision = self._model_supports_vision()
            except Exception:
                has_vision = False
            p_name = self.settings.config.get("active_system_prompt", "default")
            if p_name == "default":
                sys_content = self._build_base_system_prompt(vision=has_vision)
            else:
                sys_content = self.system_prompt
            agent_on = self.settings.config.get("agent", {}).get("enabled", True)
            if agent_on:
                from ...agent.runner import get_agent_system_prompt
                sys_content += get_agent_system_prompt(worker_workdir, worker_agent_mode, vision=has_vision)

            if getattr(self, "pinned_files", None):
                pinned_section = ["\n\nPINNED CONTEXT FILES (/add):"]
                for p_path, p_content in self.pinned_files.items():
                    pinned_section.append(f"--- File: {p_path} ---\n{p_content}\n--- End of {p_path} ---")
                sys_content += "\n".join(pinned_section)
            from ...agent.tasks import BackgroundTaskManager
            bg_notes = BackgroundTaskManager.get_instance().get_finished_notifications(session_id=worker_session_id)
            for note in bg_notes:
                _add_message({"role": "user", "content": note})

            ctx_limit = self._dynamic_limit()
            if worker_provider == "local_gguf":
                loaded_n_ctx = int(getattr(self.gguf_loader, "current_n_ctx", 0) or 0)
                if loaded_n_ctx > 0:
                    ctx_limit = min(ctx_limit, loaded_n_ctx)
            current_toks = estimate_token_count([{"role": "system", "content": sys_content}] + worker_messages)
            if should_summarize(current_toks, ctx_limit) and len(worker_messages) > 2:
                try:
                    live_state["live_tool_gen"] = None
                    live_state["thinking_finished"] = True
                    live_state["running_tool"] = {"tool": "summarizing", "target": "", "started_at": time.time(), "summary_chunks": []}
                    _call_card("add_tool", "summarizing", "")
                    if self.current_session_id == worker_session_id:
                        self.call_from_thread(self._scroll_chat_to_end_if_enabled)

                    compact_payload = build_summarization_payload(
                        worker_messages[:-1],
                        last_modified_files=getattr(self, "_last_modified_files", None),
                        max_budget_tokens=min(2500, max(1200, ctx_limit // 2)),
                    )
                    def _on_auto_sum_chunk(chunk: str) -> None:
                        if chunk:
                            rt = live_state.get("running_tool")
                            if isinstance(rt, dict) and rt.get("tool") == "summarizing":
                                rt.setdefault("summary_chunks", []).append(chunk)
                            _call_card("append_summary_chunk", chunk)

                    c_resp, _c_err = self._run_summarization_call(
                        compact_payload,
                        on_chunk=_on_auto_sum_chunk,
                        provider_id=worker_provider,
                        model_id=worker_model_id,
                    )
                    is_err = bool(_c_err) or is_summarization_error_text(c_resp)

                    handoff_content = format_compacted_handoff(
                        c_resp if not is_err else "",
                        last_modified_files=getattr(self, "_last_modified_files", None),
                        lang=detect_conversation_language(worker_messages),
                    )
                    init_goal = worker_messages[0]["content"][:300] if worker_messages else ""
                    last_msg = worker_messages[-1]
                    worker_messages[:] = [
                        {"role": "user", "content": "Poprzednie ustalenia i cel projektu:\n" + init_goal},
                        {"role": "assistant", "content": handoff_content.strip()},
                        last_msg,
                    ]
                    if self.current_session_id == worker_session_id:
                        self.messages = list(worker_messages)
                    sum_res = {"success": True, "summary": handoff_content.strip()}
                    live_state["running_tool"] = None
                    _call_card("finish_tool", sum_res)
                    _rec_tool("summarizing", "", sum_res)
                    if self.current_session_id == worker_session_id:
                        self.call_from_thread(self._scroll_chat_to_end_if_enabled)
                except Exception:
                    init_goal = worker_messages[0]["content"][:300] if worker_messages else ""
                    last_msg = worker_messages[-1] if worker_messages else {"role": "user", "content": ""}
                    handoff_content = format_compacted_handoff(
                        "",
                        last_modified_files=getattr(self, "_last_modified_files", None),
                        lang=detect_conversation_language(worker_messages),
                    )
                    worker_messages[:] = [
                        {"role": "user", "content": "Poprzednie ustalenia i cel projektu:\n" + init_goal},
                        {"role": "assistant", "content": handoff_content.strip()},
                        last_msg,
                    ]
                    if self.current_session_id == worker_session_id:
                        self.messages = list(worker_messages)
                    sum_res = {"success": True, "summary": handoff_content.strip()}
                    live_state["running_tool"] = None
                    _call_card("finish_tool", sum_res)
                    _rec_tool("summarizing", "", sum_res)

            payload_messages = [{"role": "system", "content": sys_content}]
            payload_messages.extend(worker_messages)
            self._attach_pending_images(payload_messages)

            if is_worker_aborted():
                break

            step_stream_start = time.time()
            try:
                if worker_provider == "local_gguf":
                    resp, think = self.gguf_loader.stream_chat(
                        messages=payload_messages,
                        temperature=gen_params.get("temperature", 0.3),
                        max_tokens=gen_params.get("max_tokens", 0),
                        on_token=on_token,
                        on_thinking=on_thinking,
                        model_filename=worker_model_id,
                        is_aborted=lambda: is_worker_aborted() or step_tool_completed[0],
                        on_usage=_on_step_usage,
                    )
                else:
                    from ...agent.tool_specs import get_native_tool_schemas
                    native_tools = (
                        get_native_tool_schemas()
                        if worker_provider not in ("local_gguf", "localllmapi")
                        else None
                    )
                    resp, think = stream_chat_completion(
                        provider_id=worker_provider,
                        model_id=worker_model_id,
                        messages=payload_messages,
                        api_key=api_key,
                        generation_params=gen_params,
                        tools=native_tools,
                        on_token=on_token,
                        on_thinking=on_thinking,
                        is_aborted=lambda: is_worker_aborted() or step_tool_completed[0],
                        # Only a server-side-tools provider ever calls this; the
                        # shipped ones ask the model for a text tag and execute it
                        # themselves, so the hook stays silent for them.
                        on_tool=_on_server_tool,
                        on_usage=_on_step_usage,
                    )
            except Exception as stream_exc:
                if not step_chunks:
                    err_text = f"⚠ API Error ({worker_provider}): {stream_exc}"
                    _clear_live_step_state()
                    _call_card("update_content", err_text)
                    _call_card("finish", 0.0, 0, 0.0)
                    _rec_assistant(err_text)
                    self.call_from_thread(self.notify, f"{err_text[:120]}...", severity="error", title="API Error", timeout=5.0)
                    had_api_err = True
                break
            step_stream_end = time.time()

            if first_token_time[0] > 0 and (len(step_chunks) + len(step_thinking)) > 1:
                end_t = last_token_time[0] if last_token_time[0] > first_token_time[0] else step_stream_end
                step_gen_duration = max(0.05, end_t - first_token_time[0])
            else:
                step_gen_duration = max(0.05, step_stream_end - step_stream_start)

            step_think_text = ("".join(step_thinking) or (think if isinstance(think, str) else "") or "").strip()
            tb = getattr(_card(), "thinking_block", None) if self.current_session_id == worker_session_id else None
            t_started = float(live_state.get("thinking_started_at") or 0.0)
            think_el = float(getattr(tb, "elapsed", 0.0) or 0.0) if tb else (max(0.05, time.time() - t_started) if t_started > 0 else 0.0)
            if step_think_text and not getattr(self, "_session_flushed_on_exit", False):
                _add_transcript({"kind": "thinking", "text": step_think_text, "elapsed": think_el})
            live_state["live_thinking"] = ""
            live_state["thinking_started_at"] = 0.0
            live_state["thinking_finished"] = False

            if is_worker_aborted():
                # Keep what was already generated: dropping it left a saved
                # session holding nothing but the user's prompt.
                partial = "".join(step_chunks) or resp or ""
                partial_clean = (self.agent_runner.strip_xml_tool_calls(partial)
                                 if self.agent_runner else partial).strip()
                if partial_clean and not getattr(self, "_session_flushed_on_exit", False):
                    _add_message({"role": "assistant", "content": partial_clean})
                    _rec_assistant(partial_clean)
                _clear_live_step_state()
                break

            _call_card("finish_thinking")

            step_text = "".join(step_chunks) or resp or ""
            s_low = step_text.lower().strip()
            is_api_err = bool(
                s_low.startswith("⚠ api error")
                or s_low.startswith("api error")
                or s_low.startswith("⚠ opencode local error")
                or s_low.startswith("opencode local error")
                or s_low.startswith("connection error:")
                or s_low.startswith("opencode bridge:")
                or "opencode bridge" in s_low
                or "[http 5" in s_low
                or "[http 4" in s_low
                or "free usage limit reached" in s_low
                or "usage limit reached" in s_low
                or "limit reached" in s_low
                or "api key required" in s_low
                or ("unauthorized" in s_low and ("401" in s_low or "api" in s_low or "key" in s_low))
                or ("rate limit" in s_low and ("429" in s_low or "exceeded" in s_low or "reached" in s_low))
                or "insufficient_quota" in s_low
                or "quota exceeded" in s_low
                or ("model not found" in s_low and "error" in s_low)
                or ("503" in s_low and "service" in s_low)
            )
            if is_api_err:
                self._active_generation_sessions.discard(worker_session_id)
                _clear_live_step_state()
                _call_card("finish_thinking")
                _call_card("clear_generating_tool")
                clean_err = step_text.strip()
                if not clean_err.startswith("⚠ API Error"):
                    clean_err = f"⚠ API Error: {clean_err}"
                _call_card("update_content", clean_err)
                _call_card("finish", 0.0, 0, 0.0)
                self.call_from_thread(self._update_meta_bars)
                self.call_from_thread(self.notify, f"{clean_err[:120]}...", severity="error", title="API Error", timeout=6.0)
                _rec_assistant(clean_err)
                had_api_err = True
                break

            step_p_tok = step_usage.get("prompt_tokens")
            if step_p_tok is None or int(step_p_tok) <= 0:
                step_p_tok = estimate_token_count(payload_messages)
            else:
                step_p_tok = int(step_p_tok)

            combined_gen = (step_text + ("\n" + step_think_text if step_think_text else "")).strip()
            est_c_tok = estimate_token_count([{"content": combined_gen}]) if combined_gen else 0
            raw_c_tok = int(step_usage.get("completion_tokens") or 0)
            step_c_tok = max(raw_c_tok, est_c_tok)

            total_prompt_tokens += step_p_tok
            total_completion_tokens += step_c_tok
            total_tokens = total_prompt_tokens + total_completion_tokens
            total_gen_time += step_gen_duration

            worker_stats["tokens_in"] = worker_stats.get("tokens_in", 0) + step_p_tok
            worker_stats["tokens_out"] = worker_stats.get("tokens_out", 0) + step_c_tok
            worker_stats["total_tokens"] = worker_stats["tokens_in"] + worker_stats["tokens_out"]
            if total_gen_time > 0:
                worker_stats["tok_per_sec"] = total_completion_tokens / total_gen_time
            if self.current_session_id == worker_session_id:
                self.stats.update(worker_stats)

            det_srv = getattr(self.agent_runner, "last_detached_server_tool", None) if self.agent_runner else None
            if det_srv:
                self.agent_runner.last_detached_server_tool = None
                live_state["live_tool_gen"] = None
                _call_card("clear_generating_tool")
                clean_pre_det = (self.agent_runner.strip_xml_tool_calls(step_text) if self.agent_runner else step_text).strip()
                if clean_pre_det:
                    _add_message({"role": "assistant", "content": clean_pre_det})
                    _rec_assistant(clean_pre_det)
                det_feedback = self.agent_runner.format_tool_result_for_llm("subagent", {
                    "success": True,
                    "background": True,
                    "task_id": det_srv.get("task_id", ""),
                    "roles": [det_srv.get("name", "Subagent")],
                })
                _add_message({"role": "user", "content": det_feedback})
                _prepare_next_turn()
                continue

            if not step_text.strip():
                if step_think_text and step_think_text.strip() and turn <= 4 and not is_worker_aborted():
                    # Check if the model emitted a tool tag inside reasoning/thinking
                    think_segments = self.agent_runner.parse_chronological_segments(step_think_text) if self.agent_runner else []
                    think_tools = [data for kind, data in think_segments if kind == "tool"]
                    if think_tools:
                        step_text = "\n".join(
                            m.group(0)
                            for m in re.finditer(
                                r'<(?:tool:\w+|tool_call|\|tool_call)[\s\S]*?(?:/>|</(?:tool:)?\w+>|</tool_call>|</\|tool_call>)',
                                step_think_text,
                                flags=re.IGNORECASE,
                            )
                        )
                    if not step_text.strip():
                        _add_message({
                            "role": "assistant",
                            "content": f"<think>\n{step_think_text.strip()}\n</think>",
                        })
                        _add_message({
                            "role": "user",
                            "content": (
                                "Zakończyłeś fazę myślenia (thinking), ale nie wygenerowałeś odpowiedzi ani wywołania narzędzia. "
                                "Przejdź od razu do działania i wyemituj odpowiedni tag narzędzia "
                                "(np. <tool:subagent name=\"...\" system=\"...\">...</tool:subagent>, <tool:ls ... />, <tool:read ... />) "
                                "lub udziel pełnej odpowiedzi."
                            ),
                        })
                        _prepare_next_turn()
                        continue
                if not step_text.strip():
                    if turn == 1 and not is_worker_aborted():
                        live_state["live_text"] = "*(Model returned an empty response. Check model connection or try another model in /models)*"
                        _call_card("update_content", "*(Model returned an empty response. Check model connection or try another model in /models)*")
                    elif (
                        turn > 1
                        and not empty_after_tools_nudged[0]
                        and not is_worker_aborted()
                        and not str(getattr(_card(), "raw_text", "") or live_state.get("live_text") or "").strip()
                    ):
                        empty_after_tools_nudged[0] = True
                        _add_message({
                            "role": "user",
                            "content": (
                                "Narzędzia zostały już wykonane. Napisz teraz pełną, szczegółową odpowiedź dla użytkownika "
                                "na podstawie powyższych wyników narzędzi (bez wywoływania kolejnych tagów <tool:...>, chyba że chcesz odczytać istniejący plik z listy ls)."
                            ),
                        })
                        _prepare_next_turn()
                        continue
                    break

            if any(kw in step_text.lower() for kw in ["exceed context window", "context_length_exceeded", "maximum context length", "too many tokens"]):
                init_msg = worker_messages[0] if worker_messages else {"role": "user", "content": ""}
                last_msg = worker_messages[-1] if len(worker_messages) > 1 else {"role": "user", "content": ""}
                handoff = format_compacted_handoff(
                    "",
                    last_modified_files=getattr(self, "_last_modified_files", None),
                    lang=detect_conversation_language(worker_messages),
                )
                worker_messages[:] = [
                    init_msg,
                    {"role": "assistant", "content": handoff},
                    last_msg,
                ]
                if self.current_session_id == worker_session_id:
                    self.messages = list(worker_messages)
                _call_card("update_last_body", "⚠️ Wykryto przekroczenie limitu kontekstu. Pamięć podręczna została pomyślnie skompresowana i zresetowana. Kontynuuję pracę...")
                _prepare_next_turn()
                continue

            segments = self.agent_runner.parse_chronological_segments(step_text) if self.agent_runner else [("text", step_text)]
            tool_calls = [data for kind, data in segments if kind == "tool"]

            if not tool_calls:
                live_state["live_tool_gen"] = None
                _call_card("clear_generating_tool")
                clean_step = self.agent_runner.strip_xml_tool_calls(step_text) if self.agent_runner else step_text
                if step_rendered[0] > 0:
                    raw_tail = step_text[step_rendered[0]:]
                    tail_text = (self.agent_runner.strip_xml_tool_calls(raw_tail) if self.agent_runner else raw_tail).strip()
                    tail_text = re.sub(r'</?(?:tool_response|tool_call|function_call)[^>]*>', '', tail_text, flags=re.IGNORECASE).strip()
                    if tail_text:
                        _call_card("update_last_body", tail_text)
                    elif server_tools_ran[0] == 0 and clean_step.strip():
                        _call_card("update_last_body", clean_step)
                else:
                    _call_card("update_last_body", clean_step)
                if clean_step.strip():
                    _add_message({"role": "assistant", "content": clean_step})
                    _rec_assistant(clean_step)
                    live_state["live_text"] = ""

                if last_tool_failed[0]:
                    last_tool_failed[0] = False
                    if turn < 8 and not is_worker_aborted():
                        _add_message({
                            "role": "user",
                            "content": "Poprzednie wywołanie narzędzia zakończyło się błędem. Popraw parametry narzędzia i kontynuuj zadanie aż do pełnego ukończenia.",
                        })
                        _prepare_next_turn()
                        continue

                # If the response was cut off abruptly mid-sentence, stopped right after
                # server-side tools without any concluding text, stopped right after thinking/unclosed XML,
                # or only stated an intent ("Sprawdzę najpierw..."), auto-continue rather than prematurely stopping:
                c_str = clean_step.strip()
                had_unclosed_tool_tag = bool(
                    not c_str
                    and step_text.strip()
                    and re.search(r'<(?:tool:|tool_call|\|tool_call)', step_text, flags=re.IGNORECASE)
                )
                stopped_empty_after_thinking = bool(
                    not c_str
                    and server_tools_ran[0] == 0
                    and (step_think_text and step_think_text.strip())
                )
                ends_abruptly = (
                    bool(c_str)
                    and len(c_str) > 30
                    and c_str[-1] not in ('.', '!', '?', '`', ':', '"', "'", ')', '}', ']', '>')
                    and not c_str.endswith("```")
                    and not c_str.endswith("\n")
                )
                stopped_after_server_tool = (
                    server_tools_ran[0] > 0 and not post_tool_text_seen[0]
                )
                c_low = c_str.lower().lstrip("*-•> \t")
                intent_verbs = (
                    "zakładam", "tworzę", "zaczynam", "rozpoczynam", "będę", "zaraz",
                    "najpierw", "przygotowuję", "planuję", "zrobię", "wdrażam", "dodaję",
                    "sprawdzę", "zobaczę", "przejrzę", "zbadam", "przeanalizuję", "zacznę",
                    "uruchamiam", "wywołuję", "deleguję", "przekazuję", "odpalam",
                    "let me check", "let me look", "let me inspect", "let me examine",
                    "i will check", "i'll check", "i will inspect", "i'll inspect",
                    "first, i'll", "first, let me", "i will create", "i'll create",
                    "i am going to", "i'm going to", "setting up", "creating a", "starting by",
                )
                intent_only = (
                    bool(c_str)
                    and len(c_str) <= 360
                    and c_str.count("\n") <= 4
                    and (
                        c_str.endswith(":")
                        or any(c_low.startswith(p) or f" {p} " in f" {c_low} " for p in intent_verbs)
                    )
                )

                last_user_prompt = ""
                _nudge_prefixes = (
                    "<tool_response",
                    "twoje poprzednie wywołanie",
                    "w trybie plan musisz",
                    "wywołaj teraz subagenta",
                    "nie zatrzymuj się na samej",
                    "kontynuuj na podstawie wykonanych",
                )
                for m in reversed(worker_messages):
                    if m.get("role") == "user":
                        cand_u = str(m.get("content", "")).strip()
                        cand_low = cand_u.lower()
                        if cand_u and not any(cand_low.startswith(p) for p in _nudge_prefixes):
                            last_user_prompt = cand_low
                            break

                action_triggers = (
                    "start", "zrób", "stwórz", "napisz", "dodaj", "todo", "plan",
                    "implement", "create", "make", "fix", "wywołaj", "uruchom",
                    "oceń", "przeanalizuj", "sprawdź", "subagent", "przetestuj",
                )
                has_action_trigger = bool(
                    last_user_prompt and any(t in last_user_prompt for t in action_triggers)
                )
                is_short_action_prompt = bool(
                    turn == 1
                    and len(c_str) < 180
                    and has_action_trigger
                    and not completed_subagent_roles
                )

                needs_plan_recording = (
                    self.agent_mode == "plan"
                    and has_action_trigger
                    and "subagent" not in last_user_prompt
                    and not (getattr(self.agent_ctx, "todo_list", None) or [])
                    and turn <= 2
                )

                if (
                    (
                        ends_abruptly
                        or stopped_after_server_tool
                        or ((had_unclosed_tool_tag or stopped_empty_after_thinking) and turn <= 4)
                        or (intent_only and turn <= 4)
                        or is_short_action_prompt
                        or needs_plan_recording
                    )
                    and turn < 15
                    and not self.abort_requested
                ):
                    if had_unclosed_tool_tag or stopped_empty_after_thinking:
                        if step_text.strip() and not clean_step.strip():
                            _add_message({"role": "assistant", "content": step_text.strip()})
                        nudge_content = (
                            "Twoje poprzednie wywołanie narzędzia zostało urwane lub nie zostało domknięte tagiem zamykającym. "
                            "Wyemituj teraz kompletny, domknięty tag narzędzia (np. <tool:subagent name=\"...\" system=\"...\">zadanie</tool:subagent>, "
                            "<tool:ls ... />, <tool:read ... />) i kontynuuj realizację zadania."
                        )
                    elif needs_plan_recording:
                        nudge_content = (
                            "W trybie PLAN musisz zarejestrować plan za pomocą <tool:todo action=\"set\" todos=\"krok 1|krok 2|...\" /> "
                            "oraz przedstawić zwięzłe podsumowanie. Wywołaj narzędzie <tool:todo> teraz."
                        )
                    elif intent_only or is_short_action_prompt:
                        if "subagent" in last_user_prompt:
                            nudge_content = (
                                "Wywołaj teraz subagenta za pomocą dokładnego tagu XML:\n"
                                "<tool:subagent name=\"Code Reviewer\" system=\"Inspect the project files using ls and read, and write a detailed evaluation report.\">Oceń aplikację w obecnym katalogu</tool:subagent>"
                            )
                        else:
                            nudge_content = (
                                "Nie zatrzymuj się na samej zapowiedzi ani opisie intencji. Przejdź natychmiast do działania "
                                "i wywołaj odpowiednie narzędzie (<tool:subagent>, <tool:todo>, <tool:read>, <tool:ls>, <tool:write>, <tool:command> itp.)."
                            )
                    else:
                        nudge_content = (
                            "Kontynuuj na podstawie wykonanych narzędzi i przedstaw pełną odpowiedź/plan "
                            "(nie zatrzymuj się na samym wstępie ani nie przerywaj po sprawdzeniu plików)."
                        )
                    _add_message({
                        "role": "user",
                        "content": nudge_content,
                        "internal": True,
                        "autonomous_nudge": True,
                    })
                    _prepare_next_turn()
                    continue

                try:
                    from ...agent.tasks import BackgroundTaskManager
                    bg_mgr_end = BackgroundTaskManager.get_instance()
                    if bg_mgr_end.has_finished_notifications(session_id=worker_session_id) and not is_worker_aborted():
                        _prepare_next_turn()
                        continue
                except Exception:
                    pass

                break
            else:
                live_state["live_tool_gen"] = None
                _call_card("clear_generating_tool")
                _add_message({"role": "assistant", "content": step_text})

                first_seg = segments[0] if segments else None
                first_text = first_seg[1] if (first_seg and first_seg[0] == "text") else ""
                if first_text:
                    _call_card("update_last_body", first_text)
                    _rec_assistant(first_text)
                    live_state["live_text"] = ""

                remaining_segments = segments[1:] if (first_seg and first_seg[0] == "text") else segments

                idx = 0
                while idx < len(remaining_segments):
                    if is_worker_aborted():
                        break
                    kind, data = remaining_segments[idx]
                    if kind == "text":
                        text_chunk = data
                        if first_text and text_chunk.strip() == first_text.strip():
                            idx += 1
                            continue
                        _call_card("append_text_block", text_chunk)
                        _rec_assistant(text_chunk)
                        live_state["live_text"] = ""
                        if self.current_session_id == worker_session_id:
                            self.call_from_thread(self._scroll_chat_to_end_if_enabled)
                        idx += 1
                    elif kind == "tool":
                        _call_card("finish_thinking")
                        tool_name, args = data
                        if tool_name == "subagent":
                            # A batch of N <tool:subagent> calls becomes N
                            # markers in the chat, one per subagent, each
                            # clickable into its own chat window.
                            batch = [args]
                            j = idx + 1
                            while (j < len(remaining_segments)
                                   and remaining_segments[j][0] == "tool"
                                   and remaining_segments[j][1][0] == "subagent"):
                                batch.append(remaining_segments[j][1][1])
                                j += 1

                            from ...agent.tasks import BackgroundTaskManager
                            from ...agent.runner import clean_subagent_call
                            bg_mgr = BackgroundTaskManager.get_instance()
                            if hasattr(self, "agent_runner"):
                                self.agent_runner.workdir = worker_workdir
                            if hasattr(self, "agent_ctx"):
                                self.agent_ctx.workdir = worker_workdir
                            batch = [clean_subagent_call(a) for a in batch]
                            if worker_provider == "local_gguf" and completed_subagent_roles:
                                prev_reports = [
                                    completed_subagent_roles.get(str(c.get("role") or "Subagent").lower(), "")
                                    for c in batch
                                ]
                                prev_report_text = "\n\n".join(r.strip() for r in prev_reports if r and r.strip())
                                if prev_report_text:
                                    live_state["live_tool_gen"] = None
                                    _call_card("clear_generating_tool")
                                    _call_card("append_text_block", prev_report_text)
                                    _add_message({"role": "assistant", "content": prev_report_text})
                                    _rec_assistant(prev_report_text)
                                    live_state["live_text"] = ""
                                    if self.current_session_id == worker_session_id:
                                        self.call_from_thread(self._scroll_chat_to_end_if_enabled)
                                    stop_after_subagent_repeat[0] = True
                                    break
                            by_role: Dict[str, List[Any]] = {}
                            by_sid: Dict[str, Any] = {}
                            for n, call_args in enumerate(batch, start=1):
                                worker_stats["tools_run"] = worker_stats.get("tools_run", 0) + 1
                                if self.current_session_id == worker_session_id:
                                    self.stats["tools_run"] = worker_stats["tools_run"]
                                r_name = call_args.get("role") or "Subagent"
                                g_text = call_args.get("goal") or ""
                                sid = f"subagent_{turn}_{n}"
                                call_args["_sid"] = sid
                                if not call_args.get("workdir"):
                                    call_args["workdir"] = worker_workdir
                                bg_mgr.register_subagent(sid, role=r_name, task=g_text, session_id=worker_session_id)
                                live_state.setdefault("running_subagents", []).append({
                                    "subagent_id": sid,
                                    "role": r_name,
                                    "goal": g_text,
                                    "system": call_args.get("system") or "",
                                    "index": n,
                                    "total": len(batch),
                                    "started_at": time.time(),
                                })
                                blk = self._new_subagent_block(
                                    _card(),
                                    role=r_name,
                                    goal=g_text,
                                    system=call_args.get("system") or "",
                                    index=n,
                                    total=len(batch),
                                    subagent_id=sid,
                                ) if self.current_session_id == worker_session_id else None
                                if blk is not None:
                                    blk.subagent_id = sid
                                    by_role.setdefault(blk.subagent_role, []).append(blk)
                                    by_sid[sid] = blk
                                else:
                                    by_role.setdefault(r_name, [])

                            def _on_sub_progress(prog: Any, _by_role: Dict[str, List[Any]] = by_role) -> None:
                                t_name = getattr(prog, "current_tool", "") or ""
                                t_arg = getattr(prog, "tool_arg", "") or ""
                                p_role = str(getattr(prog, "role", "") or "")
                                for s_ent in live_state.get("running_subagents") or []:
                                    if (s_ent.get("role") or "") == p_role and s_ent.get("subagent_id") and t_name:
                                        bg_mgr.update_subagent_tool(s_ent["subagent_id"], t_name, t_arg)
                                targets = []
                                for cand in _by_role.get(p_role, []):
                                    if getattr(cand, "subagent_id", "") and t_name:
                                        bg_mgr.update_subagent_tool(cand.subagent_id, t_name, t_arg)
                                    if getattr(cand, "is_mounted", False) and cand not in targets and getattr(cand, "tool_running", False):
                                        targets.append(cand)
                                if self.current_session_id == worker_session_id:
                                    for cand in getattr(_card(), "tool_blocks", []) or []:
                                        if (getattr(cand, "subagent_role", "") or "") == p_role and getattr(cand, "tool_running", False):
                                            if cand not in targets:
                                                targets.append(cand)
                                for tgt in targets:
                                    try:
                                        self.call_from_thread(tgt.set_subagent_progress, prog)
                                    except Exception:
                                        pass

                            def _sub_model_call(history: List[Dict[str, Any]], task: Any = None) -> str:
                                task_sid = (getattr(task, "context", {}) or {}).get("subagent_id", "") if task and isinstance(getattr(task, "context", None), dict) else ""
                                is_aborted_check = lambda: (
                                    self.abort_requested
                                    or getattr(task, "abort_requested", False)
                                    or (bool(task_sid) and bg_mgr.is_subagent_cancelled(task_sid))
                                )
                                if is_aborted_check():
                                    return ""
                                sub_params = dict(gen_params)
                                workdir_target = (
                                    (getattr(task, "context", {}) or {}).get("workdir")
                                    if task and isinstance(getattr(task, "context", None), dict)
                                    else getattr(self, "workdir", "") or os.getcwd()
                                )
                                sub_params["workdir"] = os.path.abspath(workdir_target)
                                sub_params["is_subagent"] = True
                                sub_provider, sub_model = self._subagent_target()
                                role = str(getattr(task, "role", "") or "Subagent") if task else "Subagent"
                                goal = str(getattr(task, "goal", "") or "") if task else ""
                                sys_prompt = str(getattr(task, "system_prompt", "") or "") if task else ""

                                sub_chunks: List[str] = []
                                sub_rendered = [0]
                                last_token_time = [0.0]

                                def _flush_pending_text_as_event() -> None:
                                    if task is None:
                                        return
                                    accumulated_now = "".join(sub_chunks)
                                    pending = accumulated_now[sub_rendered[0]:]
                                    if pending:
                                        from ...agent.runner import _strip_xml_tool_calls_clean
                                        clean_p = _strip_xml_tool_calls_clean(pending).strip()
                                        if clean_p:
                                            existing_evs = getattr(task, "events", None)
                                            if isinstance(existing_evs, list):
                                                existing_evs.append({"type": "text", "content": clean_p})
                                    sub_rendered[0] = len(accumulated_now)

                                sub_thinking_chunks: List[str] = []

                                def _on_sub_thinking(chunk: str) -> None:
                                    if is_aborted_check():
                                        return
                                    sub_thinking_chunks.append(chunk)
                                    full_think = "".join(sub_thinking_chunks)
                                    if task is not None:
                                        setattr(task, "thinking", full_think)
                                        existing_evs = getattr(task, "events", None)
                                        if isinstance(existing_evs, list):
                                            if existing_evs and existing_evs[-1].get("type") == "thinking":
                                                existing_evs[-1]["content"] = full_think
                                            else:
                                                existing_evs.append({"type": "thinking", "content": full_think})
                                    now_t = time.time()
                                    if now_t - last_token_time[0] >= 0.2:
                                        last_token_time[0] = now_t
                                        try:
                                            from ...agent.subagents.models import SubagentProgress
                                            _on_sub_progress(SubagentProgress(
                                                role=role, status="running",
                                                current_tool="Thinking",
                                                tool_arg="…",
                                                goal=goal,
                                                system_prompt=sys_prompt,
                                                history=list(history),
                                                events=list(getattr(task, "events", []) or []),
                                                thought=full_think,
                                            ))
                                        except Exception:
                                            pass

                                def _on_sub_token(chunk: str) -> None:
                                    if is_aborted_check():
                                        return
                                    sub_chunks.append(chunk)
                                    now_t = time.time()
                                    if now_t - last_token_time[0] >= 0.2:
                                        last_token_time[0] = now_t
                                        accumulated_now = "".join(sub_chunks)
                                        fresh = accumulated_now[sub_rendered[0]:]
                                        if task is not None:
                                            from ...agent.runner import _strip_xml_tool_calls_clean
                                            clean_fresh = _strip_xml_tool_calls_clean(fresh).strip()
                                            setattr(task, "live_text", clean_fresh)
                                        try:
                                            from ...agent.subagents.models import SubagentProgress
                                            _on_sub_progress(SubagentProgress(
                                                role=role, status="running",
                                                current_tool=getattr(task, "_live_tool_name", "") or "",
                                                tool_arg=getattr(task, "_live_tool_arg", "") or "",
                                                goal=goal,
                                                system_prompt=sys_prompt,
                                                history=list(history),
                                                events=list(getattr(task, "events", []) or []),
                                                thought=str(getattr(task, "live_text", "") or getattr(task, "thinking", "") or ""),
                                            ))
                                        except Exception:
                                            pass

                                def _hook_server_tool(name: str, phase: str, detail: str) -> None:
                                    """Server-side tools into this subagent's stream.

                                    Without this a model that calls native tools
                                    instead of `<tool:...>` tags ran invisibly:
                                    the marker said "starting" until the text
                                    arrived, and the report came with no tool
                                    blocks. Outcomes are recorded on the task so
                                    the result carries them; the live line goes
                                    through the same progress channel as a local
                                    tool, so the marker and its window move.
                                    """
                                    if task is None or name.lower() in ("question", "subagent", "spawn_subagent", "task"):
                                        return
                                    try:
                                        from ...agent.subagents.models import SubagentProgress
                                    except Exception:
                                        return

                                    raw_detail = str(detail or "").strip()
                                    first_line = raw_detail.splitlines()[0].strip() if raw_detail else ""
                                    body_content = raw_detail[len(first_line):].lstrip("\r\n") if raw_detail else ""
                                    clean_target = first_line
                                    if name.lower() == "read":
                                        for prefix in ("read file ", "reading file ", "read ", "reading "):
                                            if clean_target.lower().startswith(prefix):
                                                clean_target = clean_target[len(prefix):].strip()
                                                break

                                    if phase in ("generating", "running"):
                                        _flush_pending_text_as_event()
                                        setattr(task, "_live_tool_name", name)
                                        setattr(task, "_live_tool_arg", clean_target)
                                        setattr(task, "live_text", "")

                                    if phase not in ("running", "progress", "generating"):
                                        ok = (phase in ("done", "finished"))
                                        res_text = body_content or raw_detail
                                        outcome = {
                                            "tool": name,
                                            "target": clean_target,
                                            "result": (
                                                {"success": True, "content": res_text, "stdout": res_text, "result": res_text}
                                                if ok else
                                                {"success": False, "error": res_text or "failed"}
                                            ),
                                            "ok": ok,
                                        }
                                        try:
                                            existing_evs = getattr(task, "events", None)
                                            if isinstance(existing_evs, list):
                                                if existing_evs and existing_evs[-1].get("type") != "text" and str(existing_evs[-1].get("tool", "")).lower() == name.lower() and str(existing_evs[-1].get("target", "")) == clean_target:
                                                    existing_evs[-1] = outcome
                                                else:
                                                    existing_evs.append(outcome)
                                        except Exception:
                                            pass
                                        sub_rendered[0] = len("".join(sub_chunks))
                                        setattr(task, "_live_tool_name", "")
                                        setattr(task, "_live_tool_arg", "")

                                    try:
                                        is_finished_phase = phase not in ("running", "progress", "generating")
                                        _on_sub_progress(SubagentProgress(
                                            role=role, status="running",
                                            current_tool="" if is_finished_phase else name,
                                            tool_arg="" if is_finished_phase else clean_target,
                                            goal=goal,
                                            system_prompt=sys_prompt,
                                            history=list(history),
                                            events=list(getattr(task, "events", []) or []),
                                            thought=str(getattr(task, "live_text", "") or ""),
                                        ))
                                    except Exception:
                                        pass

                                if sub_provider == "local_gguf":
                                    txt, think_res = self.gguf_loader.stream_chat(
                                        messages=history,
                                        temperature=sub_params.get("temperature", 0.3),
                                        max_tokens=sub_params.get("max_tokens", 0),
                                        model_filename=sub_model,
                                        is_aborted=is_aborted_check,
                                        on_token=_on_sub_token,
                                        on_thinking=_on_sub_thinking,
                                    )
                                else:
                                    sub_key = (api_key if sub_provider == self.current_provider
                                               else self.settings.get_api_key(sub_provider))
                                    txt, think_res = stream_chat_completion(
                                        provider_id=sub_provider,
                                        model_id=sub_model,
                                        messages=history,
                                        api_key=sub_key,
                                        generation_params=sub_params,
                                        is_aborted=is_aborted_check,
                                        on_token=_on_sub_token,
                                        on_thinking=_on_sub_thinking,
                                        on_tool=_hook_server_tool,
                                    )
                                if think_res and task is not None:
                                    setattr(task, "thinking", think_res)
                                    existing_evs = getattr(task, "events", None)
                                    if isinstance(existing_evs, list):
                                        if existing_evs and existing_evs[-1].get("type") == "thinking":
                                            existing_evs[-1]["content"] = think_res
                                        else:
                                            existing_evs.append({"type": "thinking", "content": think_res})
                                _flush_pending_text_as_event()
                                if task is not None:
                                    setattr(task, "live_text", "")
                                return txt or ""

                            sub_res_box: List[Dict[str, Any]] = [{}]
                            is_local_sub = self._subagent_target()[0] in ("local_gguf", "local", "llama_cpp", "ollama")
                            want_bg = (not is_local_sub) and any(
                                str(c.get("background", "")).strip().lower() in ("true", "1", "yes")
                                for c in batch
                            )

                            def _run_sub_batch() -> None:
                                try:
                                    res_b = self.agent_runner.execute_subagents(
                                        batch,
                                        on_progress=_on_sub_progress,
                                        is_local=is_local_sub,
                                        model_call=_sub_model_call,
                                    )
                                except Exception as exc_b:
                                    res_b = {"success": False, "error": str(exc_b), "subagents": []}
                                sub_res_box[0] = res_b
                                matched_agents = _match_agents_to_calls(batch, res_b.get("subagents", []))
                                for call_args, agent in zip(batch, matched_agents):
                                    r_key = call_args.get("role") or agent.get("role", "")
                                    sid = call_args.get("_sid") or agent.get("subagent_id")
                                    res_sum = str(agent.get("summary", "") or "")
                                    if r_key and res_sum:
                                        completed_subagent_roles[str(r_key).lower()] = res_sum
                                    st = str(agent.get("status", "finished") or "finished")
                                    if sid:
                                        bg_mgr.finish_subagent(
                                            sid,
                                            status="finished" if st in ("completed", "finished") else "failed",
                                            result=res_sum,
                                        )
                                    finish_targets = []
                                    if sid and sid in by_sid and getattr(by_sid[sid], "is_mounted", False):
                                        finish_targets.append(by_sid[sid])
                                    for c in by_role.get(r_key, []):
                                        if getattr(c, "is_mounted", False) and c not in finish_targets and (not sid or getattr(c, "subagent_id", "") == sid):
                                            finish_targets.append(c)
                                    if self.current_session_id == worker_session_id:
                                        for c in getattr(_card(), "tool_blocks", []) or []:
                                            c_sid = getattr(c, "subagent_id", "")
                                            c_role = getattr(c, "subagent_role", "")
                                            if c not in finish_targets and ((sid and c_sid == sid) or (c_role == r_key and getattr(c, "tool_running", False))):
                                                finish_targets.append(c)
                                    if not finish_targets:
                                        for cands in by_role.values():
                                            for c in cands:
                                                if getattr(c, "is_mounted", False) and c not in finish_targets and getattr(c, "tool_running", False):
                                                    finish_targets.append(c)
                                    live_subs = live_state.get("running_subagents")
                                    if isinstance(live_subs, list) and sid:
                                        live_subs[:] = [s for s in live_subs if s.get("subagent_id") != sid]
                                    target_cand = finish_targets[0] if finish_targets else None
                                    was_bg = bool((target_cand and getattr(target_cand, "is_backgrounded", False)) or want_bg)
                                    for item in worker_transcript:
                                        if item.get("kind") == "subagent" and (
                                            str(item.get("agent", {}).get("subagent_id", "")) == str(sid)
                                            or (not sid and item.get("role") == r_key)
                                        ):
                                            item["agent"] = dict(agent)
                                            if was_bg:
                                                item["agent"]["is_backgrounded"] = True
                                                item["is_backgrounded"] = True
                                    if self.current_session_id == worker_session_id:
                                        for item in self._transcript:
                                            if item.get("kind") == "subagent" and (
                                                str(item.get("agent", {}).get("subagent_id", "")) == str(sid)
                                                or (not sid and item.get("role") == r_key)
                                            ):
                                                item["agent"] = dict(agent)
                                                if was_bg:
                                                    item["agent"]["is_backgrounded"] = True
                                                    item["is_backgrounded"] = True
                                    for tgt in finish_targets:
                                        try:
                                            self.call_from_thread(tgt.finish_subagent, agent)
                                        except Exception:
                                            pass
                                    if was_bg:
                                            try:
                                                self.call_from_thread(
                                                    self.notify,
                                                    f"Subagent '{r_key}' completed task in background.\nType /bg to view report.",
                                                    title="Subagent Completed",
                                                    severity="information",
                                                    timeout=4.0,
                                                )
                                            except Exception:
                                                pass
                                try:
                                    if self.current_session_id == worker_session_id:
                                        self.call_from_thread(self._save_current_session)
                                    else:
                                        self._save_session_data(
                                            session_id=worker_session_id,
                                            messages=worker_messages,
                                            transcript=worker_transcript,
                                            stats=worker_stats,
                                            model_id=worker_model_id,
                                            workdir=worker_workdir,
                                            agent_mode=worker_agent_mode,
                                        )
                                except Exception:
                                    pass

                            sub_thread = threading.Thread(target=_run_sub_batch, daemon=True)
                            sub_thread.start()

                            detached_to_bg = False
                            if want_bg:
                                detached_to_bg = True
                            else:
                                while sub_thread.is_alive():
                                    if is_worker_aborted():
                                        break
                                    if getattr(self.agent_runner, "detach_requested", False):
                                        self.agent_runner.detach_requested = False
                                        detached_to_bg = True
                                        break
                                    sub_thread.join(timeout=0.1)

                            if detached_to_bg:
                                sids = []
                                roles = []
                                for call_args in batch:
                                    sid = call_args.get("_sid", "")
                                    if sid:
                                        sids.append(sid)
                                        bg_mgr.mark_subagent_background(sid)
                                    roles.append(call_args.get("role") or "Subagent")
                                live_subs = live_state.get("running_subagents")
                                if isinstance(live_subs, list) and sids:
                                    live_subs[:] = [s for s in live_subs if s.get("subagent_id") not in sids]
                                first_sid = sids[0] if sids else ""
                                bg_targets = []
                                for b_list in by_role.values():
                                    for cand in b_list:
                                        if getattr(cand, "is_mounted", False):
                                            bg_targets.append(cand)
                                if self.current_session_id == worker_session_id:
                                    for cand in getattr(_card(), "tool_blocks", []) or []:
                                        if (getattr(cand, "subagent_id", "") in sids) and cand not in bg_targets:
                                            bg_targets.append(cand)
                                for cand in bg_targets:
                                    cand.is_backgrounded = True
                                    self.call_from_thread(cand.mark_backgrounded, first_sid)
                                sub_res = {
                                    "success": True,
                                    "background": True,
                                    "task_id": ", ".join(sids),
                                    "roles": roles,
                                    "subagents": [],
                                }
                                last_tool_failed[0] = False
                            else:
                                sub_res = sub_res_box[0] or {"success": False, "subagents": []}
                                last_tool_failed[0] = not bool(sub_res.get("success", True))

                            _add_message({
                                "role": "user",
                                "content": self.agent_runner.format_tool_result_for_llm("subagent", sub_res),
                            })
                            if detached_to_bg:
                                for call_args in batch:
                                    r_lbl = str(call_args.get("name") or call_args.get("role") or "Subagent").strip()[:60] or "Subagent"
                                    g_lbl = str(call_args.get("goal") or call_args.get("task") or call_args.get("description") or "").strip()
                                    s_lbl = str(call_args.get("system") or call_args.get("system_prompt") or call_args.get("prompt") or "").strip()
                                    _add_transcript({
                                        "kind": "subagent",
                                        "role": r_lbl,
                                        "goal": g_lbl,
                                        "system": s_lbl,
                                        "is_backgrounded": True,
                                        "agent": {
                                            "role": r_lbl,
                                            "goal": g_lbl,
                                            "system_prompt": s_lbl,
                                            "status": "running",
                                            "is_backgrounded": True,
                                        },
                                    })
                            else:
                                for call_args, agent in zip(batch, _match_agents_to_calls(
                                        batch, sub_res.get("subagents", []))):
                                    _add_transcript({
                                        "kind": "subagent",
                                        "role": str(call_args.get("name") or call_args.get("role") or "Subagent").strip()[:60] or "Subagent",
                                        "goal": str(call_args.get("goal") or call_args.get("task") or call_args.get("description") or "").strip(),
                                        "system": str(call_args.get("system") or call_args.get("system_prompt") or call_args.get("prompt") or "").strip(),
                                        "agent": agent,
                                    })
                            _call_card("clear_generating_tool")
                            self.call_from_thread(self.refresh)
                            # Always stop executing remaining speculative segments in this turn after
                            # spawning/running a subagent so the lead model receives its result first.
                            break
                        else:
                            call_sig = f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
                            tool_call_counts[call_sig] = tool_call_counts.get(call_sig, 0) + 1
                            if tool_call_counts[call_sig] >= 2:
                                if tool_call_counts[call_sig] >= 4:
                                    stop_after_subagent_repeat[0] = True
                                    idx += 1
                                    break
                                if tool_call_counts[call_sig] == 3:
                                    tool_feedback = (
                                        f'<tool_response name="{tool_name}">'
                                        f'STOP: Krok ({tool_name}) z tymi samymi parametrami został już powtórzony wielokrotnie. '
                                        f'NIE wywołuj więcej żadnych narzędzi (<tool:...>). Przejdź natychmiast do napisania pełnej odpowiedzi tekstowej dla użytkownika na podstawie dotychczas zebranych informacji.'
                                        f'</tool_response>'
                                    )
                                else:
                                    tool_feedback = (
                                        f'<tool_response name="{tool_name}">'
                                        f'Ten sam krok ({tool_name}) został już wykonany. Nie powtarzaj go — wybierz inny istniejący plik z listy ls lub przejdź od razu do pełnej odpowiedzi końcowej.'
                                        f'</tool_response>'
                                    )
                                _add_message({"role": "user", "content": tool_feedback})
                                idx += 1
                                continue
                            try:
                                res = self.agent_runner.execute_tool(tool_name, args)
                                last_tool_failed[0] = bool(res.get("error") or res.get("success") is False)
                                tool_feedback = self.agent_runner.format_tool_result_for_llm(tool_name, res)
                            except Exception as tool_exc:
                                res = {"success": False, "error": f"Tool execution exception: {tool_exc}"}
                                last_tool_failed[0] = True
                                tool_feedback = f'<tool_response name="{tool_name}">ERROR: {tool_exc}</tool_response>'
                            if (
                                self.current_provider == "local_gguf"
                                and not completed_subagent_roles
                                and turn <= 2
                            ):
                                _last_u = ""
                                for _m in reversed(worker_messages):
                                    if _m.get("role") == "user":
                                        _cu = str(_m.get("content", "")).strip().lower()
                                        if _cu and not _cu.startswith("<tool_response"):
                                            _last_u = _cu
                                            break
                                if "subagent" in _last_u:
                                    tool_feedback += (
                                        "\n\nUżytkownik poprosił o wywołanie subagenta. Wywołaj teraz subagenta dokładnym tagiem XML:\n"
                                        '<tool:subagent name="Code Reviewer" system="Inspect the project files using ls and read, and write a detailed evaluation report.">Oceń aplikację w obecnym katalogu</tool:subagent>'
                                    )
                            _add_message({"role": "user", "content": tool_feedback})
                            self.call_from_thread(self.refresh)
                            idx += 1

                if is_worker_aborted():
                    break

                _prepare_next_turn()
                # Also without announcing "Thinking" here. This line ran at the
                # top of every agent turn, including the ones where the model
                # answers straight away, so the word appeared before any
                # reasoning existed. The thinking block and the input-bar marker
                # are both driven by what actually arrives now.
                _call_card("clear_generating_tool")

        if (
            turn > 1
            and not is_worker_aborted()
            and not had_api_err
            and not completed_subagent_roles
            and not str(getattr(_card(), "raw_text", "") or live_state.get("live_text") or "").strip()
        ):
            try:
                _prepare_next_turn()
                synth_chunks: List[str] = []
                synth_prompt = (
                    "Na podstawie powyższych wyników wykonanych narzędzi napisz teraz bezpośrednią, wyczerpującą "
                    "odpowiedź końcową dla użytkownika. NIE używaj żadnych tagów XML ani narzędzi (<tool:...>) — "
                    "zwróć wyłącznie czysty tekst odpowiedzi (w formacie Markdown)."
                )
                synth_messages = [{"role": "system", "content": self.system_prompt}] + list(worker_messages) + [
                    {"role": "user", "content": synth_prompt}
                ]

                def _on_synth_token(ch: str) -> None:
                    if is_worker_aborted():
                        return
                    synth_chunks.append(ch)
                    acc_s = "".join(synth_chunks)
                    clean_s = self.agent_runner.strip_xml_tool_calls(acc_s) if self.agent_runner else acc_s
                    clean_s = re.sub(r'</?(?:tool_response|tool_call|function_call|tool:\w+)[^>]*>', '', clean_s, flags=re.IGNORECASE)
                    clean_s = re.sub(r'<[a-zA-Z0-9_:|/-]*$', '', clean_s)
                    live_state["live_text"] = clean_s
                    _call_card("update_content", clean_s)

                s_t0 = time.time()
                if worker_provider == "local_gguf":
                    s_resp, _ = self.gguf_loader.stream_chat(
                        messages=synth_messages,
                        temperature=gen_params.get("temperature", 0.3),
                        max_tokens=gen_params.get("max_tokens", 0),
                        on_token=_on_synth_token,
                        model_filename=worker_model_id,
                        is_aborted=is_worker_aborted,
                    )
                else:
                    s_resp, _ = stream_chat_completion(
                        provider_id=worker_provider,
                        model_id=worker_model_id,
                        messages=synth_messages,
                        api_key=api_key,
                        generation_params=gen_params,
                        tools=None,
                        on_token=_on_synth_token,
                        is_aborted=is_worker_aborted,
                    )
                s_dt = max(0.05, time.time() - s_t0)
                final_s = "".join(synth_chunks) or s_resp or ""
                final_s = (self.agent_runner.strip_xml_tool_calls(final_s) if self.agent_runner else final_s).strip()
                final_s = re.sub(r'</?(?:tool_response|tool_call|function_call|tool:\w+)[^>]*>', '', final_s, flags=re.IGNORECASE).strip()
                if final_s:
                    _call_card("update_last_body", final_s)
                    _add_message({"role": "assistant", "content": final_s})
                    _rec_assistant(final_s)
                    live_state["live_text"] = ""
                    s_tok = estimate_token_count([{"content": final_s}])
                    total_completion_tokens += s_tok
                    total_gen_time += s_dt
            except Exception:
                pass

        elapsed = time.time() - start_time
        tok_s = (total_completion_tokens / total_gen_time) if total_gen_time > 0 else 0.0
        worker_stats["tok_per_sec"] = tok_s
        if self.current_session_id == worker_session_id:
            self.stats["tok_per_sec"] = tok_s
        _clear_live_step_state()

        if is_worker_aborted():
            _call_card("clear_generating_tool")
            _call_card("abort")
        elif not had_api_err:
            _call_card("clear_generating_tool")
            _add_transcript({
                "kind": "turn_done",
                "elapsed": elapsed,
                "tokens": total_completion_tokens,
                "tokens_in": total_prompt_tokens,
                "tokens_out": total_completion_tokens,
                "tok_s": tok_s,
            })
            _call_card(
                "finish",
                elapsed,
                total_completion_tokens,
                tok_s,
                tokens_in=total_prompt_tokens,
                tokens_out=total_completion_tokens,
            )

            try:
                summary_msg = f"Turn complete · {total_completion_tokens} tokens ({total_prompt_tokens} in / {total_completion_tokens} out) ({tok_s:.1f} tok/s)"
                if getattr(self, "_last_modified_files", None):
                    unique_cnt = len(set(self._last_modified_files))
                    summary_msg += f" · Modified {unique_cnt} file(s)"
                send_desktop_notification("CMDAI CODE", summary_msg)
            except Exception:
                pass

        try:
            if self.current_session_id == worker_session_id:
                self._save_current_session()
            else:
                self._save_session_data(
                    session_id=worker_session_id,
                    messages=worker_messages,
                    transcript=worker_transcript,
                    stats=worker_stats,
                    model_id=worker_model_id,
                    workdir=worker_workdir,
                    agent_mode=worker_agent_mode,
                )
        except Exception:
            pass

        if worker_agent_mode == "code" and self._last_modified_files and self.current_session_id == worker_session_id:
            unique_files = list(dict.fromkeys(self._last_modified_files))
            cnt = len(unique_files)
            adds = getattr(self, "_turn_diff_stats", {}).get("added", 0)
            dels = getattr(self, "_turn_diff_stats", {}).get("deleted", 0)
            diff_lines_str = f"+{adds} -{dels} lines" if (adds or dels) else ""
            summary_target = unique_files[0] if cnt == 1 else f"{cnt} files"
            details = {
                "action": "edit",
                "path": summary_target,
                "file_count": cnt,
                "diff_info": diff_lines_str,
                "files": unique_files,
                "lines_added": adds,
                "lines_deleted": dels,
            }
            def _show_turn_approval():
                self._post_turn_approval_active = True
                try:
                    bar = self.query_one("#approval-bar", ApprovalBar)
                    bar.show_request(details)
                except Exception:
                    pass
            self.call_from_thread(_show_turn_approval)
        elif self.current_session_id == worker_session_id:
            # No pending approval: the turn is done, so snapshot it now.
            # In code mode with file changes the checkpoint waits for the user
            # to accept the changes (see on_approval_action_selected).
            self.call_from_thread(self._create_turn_checkpoint)

        # Checklist is maintained in self.agent_ctx.todo_list and synced in session state.

        if self.current_session_id == worker_session_id:
            self.abort_requested = False

