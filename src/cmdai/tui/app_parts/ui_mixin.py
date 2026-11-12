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



class UIMixin:
    """Mixin for CMDAICodeTUI."""

    def _refresh_hero_version(self) -> None:
        try:
            from ...core.releases import get_display_version
            ver = get_display_version()
            if ver and not ver.lower().startswith("v"):
                ver = f"v{ver}"
            self.query_one("#hero-logo", Static).update(get_hero_logo())
            try:
                self.query_one("#hero-version", Static).update(f"[#6e7681]{ver}[/]" if ver else "")
            except Exception:
                pass
        except Exception:
            pass


    @work(thread=True)
    def _refresh_hero_version_live(self) -> None:
        try:
            from ...core.releases import get_releases
            rels, src = get_releases(refresh=True)
            if rels:
                self.call_from_thread(self._refresh_hero_version)
        except Exception:
            pass


    def _refresh_capabilities(self) -> None:
        """Work out, on its own, whether this model reasons and how hard.

        The answer comes from the model file, the provider's own metadata or
        what it has been observed emitting - never from the name alone, which
        is why `forget_uncached_capabilities` runs first and throws the name
        guesses away between models.
        """
        try:
            forget_uncached_capabilities()
            cap = get_model_capability(self.current_model_id, self.current_provider)
            # Kept so Ctrl+T never touches the network: it used to call
            # get_model_capability from the key handler, and a cold cache meant
            # a 3s freeze on every press.
            self._cap = cap
            order = cycle_order(cap.levels) if (cap and cap.thinking) else []
            if not order:
                # No levels: the stored level is left alone. Overwriting it here
                # meant switching to a model without reasoning silently erased
                # the choice for every other model.
                return

            stored = normalize_level(
                self.settings.config.get("generation", {}).get("reasoning_level")
            )
            current = normalize_level(self.thinking_level)

            if stored and stored in order:
                self.thinking_level = stored
            elif current and current in order:
                self.thinking_level = current
            else:
                # Neither the stored nor the current level is on the ladder this
                # source produced. Show what will actually be sent, but do NOT
                # overwrite the stored choice.
                #
                # This is the second half of the bug where `max` turned into
                # `medium` while the app was being used: a source briefly
                # reported three rungs, the fallback was written to config, and
                # the user's level was gone for good. A ladder can be wrong for
                # one refresh; a silently rewritten preference is permanent.
                self.thinking_level = cap.default_level or order[0]
                return

            self.settings.config.setdefault("generation", {})["reasoning_level"] = self.thinking_level
        except Exception:
            self._cap = None


    def _tick_meta_spinner(self) -> None:
        bg_active = False
        has_notifs = False
        try:
            from ...agent.tasks import BackgroundTaskManager
            mgr = BackgroundTaskManager.get_instance()
            curr_sid = str(getattr(self, "current_session_id", "") or "")
            bg_active = mgr.has_active_background(session_id=curr_sid)
            has_notifs = mgr.has_finished_notifications(session_id=curr_sid)
        except Exception:
            pass

        prev_bg = getattr(self, "_last_bg_active", False)
        self._last_bg_active = bg_active

        try:
            for b in self.query(ToolBlock):
                if getattr(b, "tool_running", False):
                    b_sid = str(getattr(b, "subagent_id", "") or "")
                    b_r = str(getattr(b, "subagent_role", "") or "")
                    if b_sid or b_r:
                        s_info = mgr.get_subagent(b_sid) if b_sid else None
                        if not b_sid and not s_info and b_r:
                            s_info = next((s for s in mgr.list_subagents() if s.get("role", "").lower() == b_r.lower()), None)
                        if s_info and str(s_info.get("status", "")).lower() in ("finished", "completed", "failed"):
                            res_sum = str(s_info.get("result", "") or s_info.get("summary", "") or "")
                            b.finish_subagent({
                                "subagent_id": b_sid or s_info.get("subagent_id", ""),
                                "role": b_r or s_info.get("role", "Subagent"),
                                "status": "completed" if str(s_info.get("status")).lower() != "failed" else "failed",
                                "goal": s_info.get("task", ""),
                                "summary": res_sum,
                                "elapsed": float(s_info.get("elapsed", 0.0) or 0.0),
                            })
                            b.update_header()
        except Exception:
            pass

        if self.is_generating or bg_active:
            self._meta_spinner_idx = (self._meta_spinner_idx + 1) % len(self.SPINNER_FRAMES)
            self._update_meta_bars()
            if prev_bg != bg_active and self._current_assistant_card is not None:
                try:
                    self._current_assistant_card.update_header()
                except Exception:
                    pass
        else:
            if self._meta_spinner_idx != 0 or prev_bg != bg_active:
                self._meta_spinner_idx = 0
                self._update_meta_bars()
                if self._current_assistant_card is not None:
                    try:
                        self._current_assistant_card.update_header()
                    except Exception:
                        pass
            if self.in_chat_mode and has_notifs:
                if getattr(self, "_post_turn_approval_active", False):
                    # Do not auto-dismiss approval bar or start generation while user has pending file approval!
                    return
                self.start_generation("", resume_existing=True)


    def _get_meta_left_text(self) -> str:
        """The model name, with a marker that runs for the whole turn."""
        disp = clean_model_name(self.current_model_id)
        from ...agent.tasks import BackgroundTaskManager
        bg_mgr = BackgroundTaskManager.get_instance()
        curr_sid = str(getattr(self, "current_session_id", "") or "")
        has_bg = bg_mgr.has_active_background(session_id=curr_sid)
        if has_bg:
            icon = self.META_MARKERS[(self._meta_spinner_idx // 3) % len(self.META_MARKERS)]
            return f"[b #f0883e]{icon}  [/][bold white]{disp}[/]"
        elif self.is_generating:
            icon = self.META_MARKERS[(self._meta_spinner_idx // 3) % len(self.META_MARKERS)]
            return f"[b #58a6ff]{icon}  [/][bold white]{disp}[/]"
        return f"[b #58a6ff]⌬  [/][bold white]{disp}[/]"


    def _get_meta_mode_text(self) -> str:
        color = "#7ee787" if self.agent_mode == "auto" else ("#58a6ff" if self.agent_mode == "plan" else "#d29922")
        return f"[dim #6e7681]·[/] [b {color}]{self.agent_mode}[/]"


    def _get_meta_thinking_text(self) -> str:
        cap = getattr(self, "_cap", None)
        if cap is None or not cap.thinking:
            return ""

        level = normalize_level(self.thinking_level) or cap.default_level
        if level not in cycle_order(cap.levels):
            level = cap.default_level or "medium"
        return f"[dim #6e7681]·[/] [b #58a6ff]{level}[/]"


    def _update_meta_bars(self) -> None:
        left_markup = self._get_meta_left_text()
        mode_markup = self._get_meta_mode_text()
        thinking_markup = self._get_meta_thinking_text()

        try:
            self.query_one("#input-meta-left", Static).update(left_markup)
            self.query_one("#input-meta-mode", Static).update(mode_markup)
            think_w = self.query_one("#input-meta-thinking", Static)
            if thinking_markup:
                think_w.styles.display = "block"
                think_w.update(thinking_markup)
            else:
                think_w.styles.display = "none"
                think_w.update("")
            cancel_w = self.query_one("#input-meta-right", Static)
            curr_sid = str(getattr(self, "current_session_id", "") or "")
            from ...agent.tasks import BackgroundTaskManager
            has_bg = BackgroundTaskManager.get_instance().has_active_background(session_id=curr_sid)
            if self.is_generating or has_bg:
                cancel_w.styles.display = "block"
                cancel_w.update("[dim]esc: cancel[/dim]")
            else:
                cancel_w.styles.display = "none"
                cancel_w.update("")
        except Exception:
            pass

        try:
            rel_work = os.path.basename(self.workdir) or self.workdir
            self.query_one("#hero-meta-left", Static).update(left_markup)
            self.query_one("#hero-meta-mode", Static).update(mode_markup)
            hero_think = self.query_one("#hero-meta-thinking", Static)
            if thinking_markup:
                hero_think.styles.display = "block"
                hero_think.update(thinking_markup)
            else:
                hero_think.styles.display = "none"
                hero_think.update("")
            self.query_one("#hero-meta-right", Static).update(f"[dim]{rel_work}[/dim]")
        except Exception:
            pass


    def update_meta_bars(self) -> None:
        self._refresh_capabilities()
        self._update_meta_bars()


    def cycle_mode(self) -> None:
        modes = ["auto", "plan", "code"]
        curr_idx = modes.index(self.agent_mode) if self.agent_mode in modes else 0
        self.agent_mode = modes[(curr_idx + 1) % len(modes)]
        self.settings.config.setdefault("agent", {})["mode"] = self.agent_mode
        self.settings.save_config()
        self.agent_ctx.mode = self.agent_mode
        self._update_meta_bars()
        self.notify(f"Switched to {self.agent_mode.upper()} mode", title="Agent Mode", timeout=2.0)


    def action_cycle_mode(self) -> None:
        self.cycle_mode()


    def action_handle_tab_key(self) -> None:
        if getattr(self, "_hint_matches", None):
            self._hint_complete()
            return
        try:
            inp_id = "#hero-prompt-input" if not self.in_chat_mode else "#prompt-input"
            inp = self.query_one(inp_id, AutoExpandingInput)
            if not inp.text.strip():
                self.cycle_mode()
        except Exception:
            self.cycle_mode()


    def _get_editor_main_path(self) -> str:
        cmdai_pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        pkg_editor = os.path.join(cmdai_pkg, "editor", "main.py")
        if os.path.isfile(pkg_editor):
            return os.path.abspath(pkg_editor)
        repo_root = os.path.dirname(os.path.dirname(cmdai_pkg))
        root_editor = os.path.join(repo_root, "src", "cmdai", "editor", "main.py")
        if os.path.isfile(root_editor):
            return os.path.abspath(root_editor)
        return os.path.abspath(pkg_editor)


    def _open_diff_window(self, arg: str = "") -> None:
        try:
            from ...core.project_scope import is_unsafe_global_dir, resolve_diff_target_file
            recent_files = list(getattr(self, "_last_modified_files", None) or [])
            if not recent_files and getattr(self, "_pending_approval_details", None):
                pend_files = self._pending_approval_details.get("files", [])
                if pend_files:
                    recent_files = list(pend_files)
            target_file = resolve_diff_target_file(self.workdir, arg=arg, recent_files=recent_files)
            if not target_file:
                if is_unsafe_global_dir(self.workdir):
                    self.notify(
                        "Please specify a file: /diff <file>",
                        severity="warning",
                        timeout=4.0,
                    )
                else:
                    self.notify(
                        "No modified file for diff preview. Use: /diff <file>",
                        severity="information",
                        timeout=3.5,
                    )
                return

            editor_py = self._get_editor_main_path()
            self._spawn_terminal_window(
                editor_py,
                f"CMDAI CODE Editor Diff - {os.path.basename(target_file)}",
                "--diff",
                target_file,
            )
            self.notify(f"Opened diff in editor: {os.path.basename(target_file)}", timeout=2.5)
        except Exception as e:
            self.notify(f"Failed to launch editor diff: {e}", severity="error")


    @on(ApprovalBar.ActionSelected)
    def on_approval_action_selected(self, msg: ApprovalBar.ActionSelected) -> None:
        try:
            bar = self.query_one("#approval-bar", ApprovalBar)
        except Exception:
            bar = None

        if msg.action == "allow":
            if bar:
                bar.hide_request()
            if getattr(self, "_pending_approval_evt", None):
                self._pending_approval_res[0] = "Yes, proceed"
                self._pending_approval_evt.set()
            elif getattr(self, "_post_turn_approval_active", False):
                self._post_turn_approval_active = False
                self._last_modified_files.clear()
                # Code mode: the checkpoint lands only after the user accepts.
                self._create_turn_checkpoint()
                self.notify("Changes approved.", timeout=2.5)
        elif msg.action == "reject":
            if bar:
                bar.hide_request()
            if getattr(self, "_pending_approval_evt", None):
                self._pending_approval_res[0] = "No, skip"
                self._pending_approval_evt.set()
            elif getattr(self, "_post_turn_approval_active", False):
                self._post_turn_approval_active = False
                files_hint = msg.details.get("files", []) if msg.details else []
                self._revert_files(files_hint)
                self.notify("Changes rejected and reverted.", severity="warning", timeout=2.5)
        elif msg.action == "diff":
            self._open_diff_window()


    def _update_timeline_active_on_scroll(self, scroll: VerticalScroll) -> None:
        try:
            # If user explicitly clicked a dot, hold that active selection during animation and settling
            if getattr(self, "_timeline_nav_target_idx", None) is not None:
                if (time.time() - getattr(self, "_timeline_nav_time", 0.0) < 1.5) or getattr(scroll, "is_scrolling", False):
                    return
                self._timeline_nav_target_idx = None

            rail = self.query_one("#chat-timeline-rail", TimelineRail)
            if not rail or "hidden" in rail.classes or not rail._turns:
                return

            user_cards = [
                c for c in scroll.children
                if isinstance(c, UserMessageCard) and not getattr(c, "_pruning", False)
            ]
            if not user_cards:
                return

            total = len(user_cards)
            max_y = float(getattr(scroll, "max_scroll_y", 0) or 0)
            cur_y = float(getattr(scroll, "scroll_y", 0) or 0)

            if cur_y <= 1:
                active_idx = 0
            elif max_y > 5 and cur_y >= max_y - 1:
                active_idx = total - 1
            else:
                active_idx = 0
                best_dist = float("inf")
                for idx, c in enumerate(user_cards):
                    reg_y = getattr(getattr(c, "region", None), "y", None)
                    if reg_y is not None:
                        dist = abs(reg_y - cur_y)
                        if dist < best_dist:
                            best_dist = dist
                            active_idx = idx

            if getattr(self, "_active_view_turn_idx", None) != active_idx:
                self._active_view_turn_idx = active_idx
                rail.set_active_turn_idx(active_idx)
        except Exception:
            pass


    @on(TimelineRail.TurnSelected)
    def on_timeline_turn_selected(self, msg: TimelineRail.TurnSelected) -> None:
        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            target = None
            user_cards = [c for c in chat_scroll.children if isinstance(c, UserMessageCard) and not getattr(c, "_pruning", False)]
            if 0 <= msg.turn_idx < len(user_cards):
                target = user_cards[msg.turn_idx]
            else:
                for child in user_cards:
                    if int(getattr(child, "turn_id", -1)) == int(msg.turn_id):
                        target = child
                        break
            if target is not None:
                self._active_view_turn_idx = msg.turn_idx
                self._active_view_turn_id = getattr(target, "turn_id", msg.turn_id)
                self._timeline_nav_target_idx = msg.turn_idx
                self._timeline_nav_time = time.time()
                try:
                    rail = self.query_one("#chat-timeline-rail", TimelineRail)
                    rail.set_active_turn_idx(msg.turn_idx)
                except Exception:
                    pass
                chat_scroll.scroll_to_widget(target, top=True, animate=True)
        except Exception:
            pass


    @on(TimelineRail.ScrollRequested)
    def on_timeline_scroll_requested(self, msg: TimelineRail.ScrollRequested) -> None:
        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            if msg.direction == "up":
                chat_scroll.scroll_home(animate=True)
                if hasattr(self, "_active_view_turn_idx"):
                    self._active_view_turn_idx = 0
                    try:
                        self.query_one("#chat-timeline-rail", TimelineRail).set_active_turn_idx(0)
                    except Exception:
                        pass
            else:
                chat_scroll.scroll_end(animate=True)
                try:
                    rail = self.query_one("#chat-timeline-rail", TimelineRail)
                    if rail._turns:
                        last_idx = len(rail._turns) - 1
                        self._active_view_turn_idx = last_idx
                        rail.set_active_turn_idx(last_idx)
                except Exception:
                    pass
        except Exception:
            pass


    def _refresh_timeline_rail(self) -> None:
        try:
            rail = self.query_one("#chat-timeline-rail", TimelineRail)
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        except Exception:
            return

        turns = []
        nudge_prefixes = (
            "Kontynuuj na podstawie",
            "Nie zatrzymuj się na samej",
            "Wywołaj teraz subagenta",
            "oraz przedstawić zwięzłe",
        )
        if hasattr(self, "_transcript") and self._transcript:
            for item in self._transcript:
                if isinstance(item, dict) and item.get("kind") == "user":
                    prompt = str(item.get("text", "") or "")
                    if any(prompt.startswith(p) for p in nudge_prefixes):
                        continue
                    tid = int(item.get("turn_id", 0) or 0)
                    t_str = str(item.get("time") or "")
                    if not t_str and item.get("timestamp"):
                        try:
                            t_str = time.strftime("%H:%M", time.localtime(float(item["timestamp"])))
                        except Exception:
                            t_str = ""
                    if not t_str:
                        t_str = getattr(self, "_current_session_time_str", "") or ""
                    turns.append({"turn_id": tid, "text": prompt, "time": t_str})

        if not turns:
            for child in chat_scroll.children:
                if isinstance(child, UserMessageCard) and not getattr(child, "_pruning", False):
                    prompt = str(getattr(child, "prompt", "") or "")
                    if any(prompt.startswith(p) for p in nudge_prefixes):
                        continue
                    tid = int(getattr(child, "turn_id", 0) or 0)
                    t_str = str(getattr(child, "time_str", "") or "")
                    turns.append({"turn_id": tid, "text": prompt, "time": t_str})

        cur_idx = getattr(self, "_active_view_turn_idx", None)
        if cur_idx is None and turns:
            cur_idx = len(turns) - 1
        rail.update_turns(turns, active_turn_idx=cur_idx)


    @on(SessionTabsBar.TabSelected)
    def on_session_tab_selected(self, msg: SessionTabsBar.TabSelected) -> None:
        sid = str(msg.session_id or "").strip()
        if sid and sid != self.current_session_id:
            self._on_session_selected(sid)


    @on(SessionTabsBar.TabClosed)
    def on_session_tab_closed(self, msg: SessionTabsBar.TabClosed) -> None:
        sid = str(msg.session_id or "").strip()
        if sid in getattr(self, "_open_session_ids", []):
            self._open_session_ids.remove(sid)
        if sid == self.current_session_id:
            if getattr(self, "_open_session_ids", []):
                self._on_session_selected(self._open_session_ids[-1])
            else:
                self._cmd_new()
        else:
            self._refresh_session_tabs()


    @on(SessionTabsBar.NewTabRequested)
    def on_session_new_tab_requested(self, msg: SessionTabsBar.NewTabRequested) -> None:
        self._cmd_new()


    def _refresh_session_tabs(self) -> None:
        try:
            tabs_bar = self.query_one("#session-tabs-bar", SessionTabsBar)
        except Exception:
            return

        cur_sid = str(self.current_session_id or "").strip()
        if not hasattr(self, "_open_session_ids"):
            self._open_session_ids = [cur_sid] if cur_sid else []

        if cur_sid and cur_sid not in self._open_session_ids:
            self._open_session_ids.append(cur_sid)

        # Clean dead sessions that are no longer valid
        active_sids = getattr(self, "_active_generation_sessions", set())
        valid_open = []
        for sid in self._open_session_ids:
            if sid == cur_sid or sid in active_sids or self.session_manager.load_session(sid) is not None:
                valid_open.append(sid)
        self._open_session_ids = valid_open or ([cur_sid] if cur_sid else [])

        if len(self._open_session_ids) <= 1:
            tabs_bar.add_class("hidden")
            tabs_bar.display = False
            return

        tabs_bar.remove_class("hidden")
        tabs_bar.display = True

        tabs_data = []
        for sid in list(self._open_session_ids):
            if sid == cur_sid:
                t = self.session_title or "New session"
            else:
                try:
                    s_data = self.session_manager.load_session(sid) or {}
                    t = s_data.get("title") or "New session"
                except Exception:
                    t = "New session"
            tabs_data.append({"session_id": sid, "title": t})

        tabs_bar.update_tabs(tabs_data, active_id=cur_sid)


    def on_key(self, event: events.Key) -> None:
        key_name = (event.key or "").lower()
        if key_name in ("left", "right"):
            hovered_tool = None
            try:
                for tb in self.query(ToolBlock):
                    if getattr(tb, "is_expanded", False) and getattr(tb, "_is_mouse_hovered", False):
                        hovered_tool = tb
                        break
            except Exception:
                hovered_tool = None
            if hovered_tool is not None:
                delta = 6 if key_name == "right" else -6
                hovered_tool.shift_horizontal(delta)
                event.prevent_default()
                event.stop()
                return


    def _capability(self) -> Optional[ModelCapability]:
        """The detected capability for the current model.

        Prefers the one `_refresh_capabilities` already worked out, so the send
        path does not repeat a call that can take three seconds on a cold cache.
        Falls back to detecting, because a model changed without a refresh must
        still be handled correctly rather than against a stale answer.
        """
        cap = getattr(self, "_cap", None)
        if cap is not None and cap.model_id == self.current_model_id:
            return cap
        return get_model_capability(self.current_model_id, self.current_provider)


    def cycle_thinking(self) -> None:
        """Ctrl+T: low -> medium -> high -> extra -> max -> low.

        Whether the model reasons at all is detected, not chosen, so there is
        no `off` to cycle into and a model without reasoning does nothing at
        all here. No toast either: the input bar shows the level itself, so a
        toast on every press would be twenty interruptions over a session.
        """
        cap = self._capability()
        levels = cycle_order(cap.levels) if (cap and cap.thinking) else []
        if not levels:
            return  # this model does not reason; there is nothing to choose

        current = normalize_level(self.thinking_level)
        idx = levels.index(current) if current in levels else -1
        self.thinking_level = levels[(idx + 1) % len(levels)]
        self._thinking_invoked = True
        self.settings.config.setdefault("generation", {})["reasoning_level"] = self.thinking_level
        self.settings.save_config()
        self._update_meta_bars()


    def notify(
        self,
        message: Any = "",
        *,
        title: Optional[Any] = None,
        severity: str = "information",
        timeout: Optional[float] = None,
        markup: bool = True,
    ) -> None:
        """Application-wide toast notification styled like OpenCode:
        Line 1: Bold category/command title (cyan / amber / red)
        Line 2: Explanatory subtext
        """
        clean_msg = str(message or "")
        clean_title = str(title or "").strip()

        if not clean_title:
            lower_msg = clean_msg.lower()
            if "directory changed" in lower_msg or "current directory" in lower_msg or "directory not found" in lower_msg or "not a directory" in lower_msg:
                clean_title = "Working Directory"
                if "directory changed:" in lower_msg:
                    clean_msg = f"Changed to: {clean_msg.split(':', 1)[1].strip()}"
                elif "current directory:" in lower_msg:
                    clean_msg = clean_msg.split(":", 1)[1].strip()
            elif "mode set to" in lower_msg or "valid modes" in lower_msg or "switched to" in lower_msg:
                clean_title = "Agent Mode"
            elif "thinking set to" in lower_msg or "thinking levels" in lower_msg or "reason" in lower_msg:
                clean_title = "Thinking"
            elif "copied" in lower_msg or "clipboard" in lower_msg or "skopiowano" in lower_msg:
                clean_title = "Clipboard"
            elif "branch" in lower_msg or "committed" in lower_msg or lower_msg.startswith("git"):
                clean_title = "Git"
            elif "reverted" in lower_msg or "removed turn" in lower_msg:
                clean_title = "Revert"
            elif "context updated" in lower_msg:
                clean_title = "Context"
            elif "exported to" in lower_msg:
                clean_title = "Export"
            elif "generation" in lower_msg or "cancelled" in lower_msg:
                clean_title = "Generation"
            elif "session" in lower_msg:
                clean_title = "Session"
            elif "subagent" in lower_msg:
                clean_title = "Subagent"
            elif "update" in lower_msg:
                clean_title = "Update"
            elif severity == "error":
                clean_title = "Error"
            elif severity == "warning":
                clean_title = "Warning"
            else:
                clean_title = "Notice"

        if timeout is None:
            timeout = 3.0

        super().notify(
            clean_msg,
            title=clean_title,
            severity=severity,
            timeout=timeout,
            markup=markup,
        )


    def action_cycle_thinking(self) -> None:
        # Textual resolves a binding to `action_<name>`. Without this the key
        # was bound to a method it could not find and did nothing at all.
        self.cycle_thinking()


    @on(events.Click, "#input-meta-mode")
    @on(events.Click, "#hero-meta-mode")
    def on_mode_clicked(self, event: events.Click) -> None:
        self.cycle_mode()


    @on(events.Click, "#input-meta-thinking")
    @on(events.Click, "#hero-meta-thinking")
    def on_thinking_clicked(self, event: events.Click) -> None:
        """Clickable because `/models` is: a list you can read, not a key you
        have to know about. Ctrl+T still cycles, and is bound with priority so
        it works while the input has focus."""
        self._cmd_thinking("")


    def _chat_target_y(self, cs) -> float:
        try:
            t = getattr(cs, "scroll_target_y", None)
            if t is not None:
                return float(t)
        except Exception:
            pass
        try:
            return float(cs.scroll_y)
        except Exception:
            return 0.0


    def _chat_is_at_bottom(self, cs, target_y: Optional[float] = None) -> bool:
        try:
            y = float(target_y) if target_y is not None else self._chat_target_y(cs)
            return bool(y >= (float(cs.max_scroll_y) - 2)) or bool(
                getattr(cs, "is_vertical_scroll_end", False)
            )
        except Exception:
            return False


    def _mark_scrolled_up(self) -> None:
        self.auto_scroll_enabled = False
        self._last_user_scroll_time = time.time()


    def _refresh_follow_hint(self, cs) -> None:
        """Show the jump control iff the newest messages are out of view.

        One place deciding this, called after every manual scroll however it
        was made - wheel, keys, or clicks. The logic used to live scattered
        across the scroll handlers, and the wheel-up path simply forgot it, so
        the control never appeared for the most common way of scrolling.
        """
        try:
            self._set_follow_hint(not self._chat_is_at_bottom(cs))
        except Exception:
            pass


    def _mark_scrolled_down(self, cs, target_y: Optional[float] = None) -> None:
        try:
            if self._chat_is_at_bottom(cs, target_y):
                self.auto_scroll_enabled = True
                self._last_user_scroll_time = 0.0
                self._set_follow_hint(False)
            else:
                self.auto_scroll_enabled = False
                self._last_user_scroll_time = time.time()
        except Exception:
            pass


    def _set_follow_hint(self, visible: bool) -> None:
        if visible == getattr(self, "_follow_hint_visible", False):
            return
        self._follow_hint_visible = visible
        try:
            jump_bar = self.query_one("#jump-bar")
            jump_bar.styles.display = "block" if visible else "none"
            self.query_one("#jump-to-new", Static).update(
                "[b #58a6ff]more below[/] [b #58a6ff]↓[/]" if visible else ""
            )
        except Exception:
            pass


    def _scroll_chat_to_end_if_enabled(self, force: bool = False) -> None:
        try:
            cs = self.query_one("#chat-scroll", VerticalScroll)
            if force:
                self.auto_scroll_enabled = True
                self._last_user_scroll_time = 0.0
                self._last_auto_scroll_time = time.time()
                self._set_follow_hint(False)
                try:
                    cs.scroll_end(animate=False, immediate=True)
                except TypeError:
                    cs.scroll_end(animate=False)
                return

            if not getattr(self, "auto_scroll_enabled", True):
                return

            if getattr(cs, "is_vertical_scrollbar_grabbed", False):
                self.auto_scroll_enabled = False
                return

            # User is reading above: never yank the view while they scroll.
            # Down-to-bottom clears this lockout via _mark_scrolled_down.
            if time.time() - getattr(self, "_last_user_scroll_time", 0.0) < 2.0:
                self._set_follow_hint(True)
                return

            # Follow only when already near the bottom; otherwise leave the
            # user's reading position alone and just hint about new content.
            try:
                pos = self._chat_target_y(cs)
                if float(cs.max_scroll_y) > 0 and pos < (float(cs.max_scroll_y) - 6):
                    self._set_follow_hint(True)
                    return
            except Exception:
                pass

            # Throttle: token streams call this very often; 5x/s is enough.
            now = time.time()
            if now - getattr(self, "_last_auto_scroll_time", 0.0) < 0.2:
                return
            self._last_auto_scroll_time = now
            self._set_follow_hint(False)
            try:
                cs.scroll_end(animate=False, immediate=True)
            except TypeError:
                cs.scroll_end(animate=False)
        except Exception:
            pass


    def _chat_scroll_by(self, delta: float, is_up: bool) -> None:
        try:
            cs = self.query_one("#chat-scroll", VerticalScroll)
        except Exception:
            return
        if is_up:
            self._mark_scrolled_up()
        try:
            base = self._chat_target_y(cs)
            if is_up:
                target = max(0.0, base + delta)
            else:
                try:
                    target = min(float(cs.max_scroll_y), base + delta)
                except Exception:
                    target = base + delta
            try:
                cs.scroll_to(y=target, animate=False, immediate=True)
            except TypeError:
                cs.scroll_relative(y=delta, animate=False)
        except Exception:
            pass
        if not is_up:
            self._mark_scrolled_down(cs)
        else:
            try:
                self._refresh_follow_hint(cs)
            except Exception:
                pass


    def action_scroll_chat_up(self) -> None:
        try:
            cs = self.query_one("#chat-scroll", VerticalScroll)
            self._mark_scrolled_up()
            try:
                cs.scroll_page_up(animate=False)
            except TypeError:
                cs.scroll_page_up()
            # Page scroll is deferred like page-down: decide after it lands.
            self.call_after_refresh(self._refresh_follow_hint, cs)
        except Exception:
            pass


    def action_scroll_chat_down(self) -> None:
        try:
            cs = self.query_one("#chat-scroll", VerticalScroll)
            try:
                cs.scroll_page_down(animate=False)
            except TypeError:
                cs.scroll_page_down()
            # Page scroll is deferred: decide from the requested target.
            self.call_after_refresh(self._mark_scrolled_down, cs)
            self.call_after_refresh(self._refresh_follow_hint, cs)
        except Exception:
            pass


    def action_scroll_chat_top(self) -> None:
        """Always provide a reliable route back to the first message."""
        self._mark_scrolled_up()
        try:
            self.query_one("#chat-scroll", VerticalScroll).scroll_home(animate=False)
        except Exception:
            pass


    def action_scroll_chat_bottom(self) -> None:
        try:
            cs = self.query_one("#chat-scroll", VerticalScroll)
            try:
                cs.scroll_end(animate=False, immediate=True)
            except TypeError:
                cs.scroll_end(animate=False)
            self.auto_scroll_enabled = True
            self._last_user_scroll_time = 0.0
            self._set_follow_hint(False)
        except Exception:
            pass


    def action_scroll_chat_line_up(self) -> None:
        self._chat_scroll_by(-2, is_up=True)


    def action_scroll_chat_line_down(self) -> None:
        self._chat_scroll_by(2, is_up=False)


    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        # ChatScroll already handles + stops wheel events over the chat.
        # This is only a fallback for wheel events over the input dock.
        self._chat_scroll_by(-3, is_up=True)


    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        # Fallback for wheel events outside ChatScroll (e.g. over input).
        self._chat_scroll_by(3, is_up=False)


    def action_toggle_tools(self) -> None:
        try:
            blocks = list(self.query(ToolBlock))
            if not blocks:
                return
            any_expanded = any(b.is_expanded for b in blocks if not b.tool_running)
            for b in blocks:
                if not b.tool_running:
                    b.set_expanded(not any_expanded)
        except Exception:
            pass


    def action_send_to_background(self) -> None:
        if self.agent_runner:
            self.agent_runner.request_background()
        try:
            card = self._current_assistant_card
            if card is not None:
                blocks = list(card.flow_container.query(ToolBlock)) or list(card.tool_blocks)
                for b in reversed(blocks):
                    if getattr(b, "tool_running", False) and not getattr(b, "is_backgrounded", False):
                        tl = (getattr(b, "tool_name", "") or "").lower()
                        if getattr(b, "subagent_role", "") or tl in (
                            "command", "terminal", "shell", "bash", "cmd", "run_command", "sh", "exec", "subagent", "task"
                        ):
                            b.is_backgrounded = True
                            b.mark_backgrounded()
                            sid = getattr(b, "subagent_id", "")
                            if sid:
                                from ...agent.tasks import BackgroundTaskManager
                                BackgroundTaskManager.get_instance().mark_subagent_background(sid)
                            break
        except Exception:
            pass
        try:
            self.notify(
                "Task moved to background.\nType /bg to open task manager.",
                title="Background Task",
                severity="information",
                timeout=3.5,
            )
        except Exception:
            pass


    def action_quit_app(self) -> None:
        self._flush_live_turn_to_session()
        try:
            self.abort_requested = True
            if self._current_assistant_card:
                self._current_assistant_card.abort()
        except Exception:
            pass
        try:
            sys.stdout.write("\x1b]111\x07")
            sys.stdout.flush()
        except Exception:
            pass
        if self.http_server:
            self.http_server.stop()
        self.exit()


    def action_restart_app(self) -> None:
        """Saves current state and immediately restarts CMDAI CODE."""
        self._flush_live_turn_to_session()
        try:
            self.abort_requested = True
            if self._current_assistant_card:
                self._current_assistant_card.abort()
        except Exception:
            pass
        try:
            sys.stdout.write("\x1b]111\x07")
            sys.stdout.flush()
        except Exception:
            pass
        if self.http_server:
            try:
                self.http_server.stop()
            except Exception:
                pass
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv)
        self.exit(0)


    def action_handle_interrupt(self) -> None:
        try:
            if self.is_generating:
                self.abort_requested = True
                curr_sid = str(getattr(self, "current_session_id", "") or "")
                active_info = getattr(self, "_active_generation_data", {}).get(curr_sid)
                if isinstance(active_info, dict):
                    active_info["abort_requested"] = True
                if self._current_assistant_card:
                    self._current_assistant_card.abort_requested = True
                    try:
                        self._current_assistant_card.abort()
                    except Exception:
                        self.call_from_thread(self._current_assistant_card.abort)
                self.notify("Generation cancelled (Ctrl+C)", title="Cancelled", timeout=2.0)
                return

            try:
                focused = getattr(self, "focused", None)
                sel_text = getattr(focused, "selected_text", "") if focused else ""
                if sel_text:
                    if copy_text_to_clipboard(sel_text):
                        self.notify(f"Copied selection to clipboard ({len(sel_text)} chars)", title="Clipboard", timeout=2.0)
                        return
            except Exception:
                pass

            try:
                expanded_tools = [b for b in self.query(ToolBlock) if getattr(b, "is_expanded", False)]
                if expanded_tools:
                    target_tool = expanded_tools[-1]
                    content_to_copy = ""
                    if target_tool.tool_name == "edit" and target_tool.result_data and "diff_entries" in target_tool.result_data:
                        lines = [f"# Edit: {target_tool.target}"]
                        for entry in target_tool.result_data["diff_entries"]:
                            if len(entry) >= 3:
                                ln, t, c = entry[0], entry[1], entry[2]
                                prefix = "-" if t == "del" else ("+" if t == "add" else " ")
                                lines.append(f"{prefix} {ln:>4}: {c}")
                        content_to_copy = "\n".join(lines)
                    elif target_tool.result_data and ("content" in target_tool.result_data or "stdout" in target_tool.result_data):
                        content_to_copy = str(target_tool.result_data.get("content") or target_tool.result_data.get("stdout") or "")

                    if not content_to_copy and getattr(target_tool, "details_text", ""):
                        content_to_copy = target_tool.details_text

                    if not content_to_copy and target_tool.result_data:
                        try:
                            content_to_copy = json.dumps(target_tool.result_data, indent=2, ensure_ascii=False)
                        except Exception:
                            content_to_copy = str(target_tool.result_data)

                    if content_to_copy:
                        copy_text_to_clipboard(content_to_copy)
                        self.notify(f"Copied [{target_tool.tool_name}] to clipboard ({len(content_to_copy)} chars)", title="Clipboard", timeout=2.0)
                        return
            except Exception:
                pass

            try:
                expanded_thinking = [tb for tb in self.query(ThinkingBlock) if getattr(tb, "is_expanded", False)]
                if expanded_thinking:
                    content_to_copy = getattr(expanded_thinking[-1], "thinking_text", "")
                    if content_to_copy:
                        copy_text_to_clipboard(content_to_copy)
                        self.notify(f"Copied thoughts to clipboard ({len(content_to_copy)} chars)", title="Clipboard", timeout=2.0)
                        return
            except Exception:
                pass

            try:
                if len(self.screen_stack) > 1:
                    top = self.screen_stack[-1]
                    content = getattr(top, "diff_text", None) or getattr(top, "content", None)
                    if content:
                        copy_text_to_clipboard(str(content))
                        self.notify("Copied window content to clipboard", title="Clipboard", timeout=2.0)
                        return
                    self.action_handle_escape()
                    return
            except Exception:
                pass

            try:
                inp_id = "#prompt-input" if self.in_chat_mode else "#hero-prompt-input"
                inp = self.query_one(inp_id, AutoExpandingInput)
                if inp.text.strip():
                    copy_text_to_clipboard(inp.text)
                    self.notify(f"Copied input text to clipboard ({len(inp.text)} chars)", title="Clipboard", timeout=2.0)
                    return
            except Exception:
                pass

            try:
                assistant_msgs = [m["content"] for m in self.messages if m.get("role") == "assistant" and m.get("content")]
                if assistant_msgs:
                    copy_text_to_clipboard(assistant_msgs[-1])
                    self.notify("Copied last response to clipboard", title="Clipboard", timeout=2.0)
                else:
                    self.notify("Type /quit to exit the application", timeout=2.0)
            except Exception:
                pass
        except Exception:
            pass


    def action_handle_escape(self) -> None:
        if len(self.screen_stack) > 1:
            top = self.screen_stack[-1]
            if hasattr(top, "action_dismiss_modal"):
                top.action_dismiss_modal()
            elif hasattr(top, "action_cancel"):
                top.action_cancel()
            elif hasattr(top, "dismiss"):
                top.dismiss(None)
            else:
                self.pop_screen()
            return

        if getattr(self, "_pending_approval_evt", None):
            self.on_approval_action_selected(ApprovalBar.ActionSelected("reject", getattr(self, "_pending_approval_details", None)))
            return

        if bool(getattr(self, "_hint_matches", None)):
            self._hide_hints()
            return

        from ...agent.tasks import BackgroundTaskManager
        bg_mgr = BackgroundTaskManager.get_instance()
        has_active_bg = bg_mgr.has_active_background(session_id=self.current_session_id)

        if self.is_generating or has_active_bg:
            self.abort_requested = True
            curr_sid = str(getattr(self, "current_session_id", "") or "")
            active_info = getattr(self, "_active_generation_data", {}).get(curr_sid)
            if isinstance(active_info, dict):
                active_info["abort_requested"] = True
            self.is_generating = False
            self._last_escape_time = 0.0
            if self._current_assistant_card:
                self._current_assistant_card.abort_requested = True
                self._current_assistant_card.abort()
            num_cancelled = bg_mgr.cancel_all_for_session(self.current_session_id)
            try:
                cs = self.query_one("#chat-scroll", VerticalScroll)
                for b in cs.query(ToolBlock):
                    if getattr(b, "tool_running", False):
                        b.tool_running = False
                        b._stop_spinner()
                        if getattr(b, "subagent_role", ""):
                            b.subagent_status = "cancelled"
                            b.subagent_live_tool = "Cancelled"
                        b.update_header()
            except Exception:
                pass
            self._update_meta_bars()
            self._hide_hints()
            if num_cancelled > 0:
                self.notify(f"Generation and {num_cancelled} background task(s) cancelled.", severity="warning", timeout=2.5)
            else:
                self.notify("Generation cancelled.", severity="warning", timeout=2.0)
            try:
                self.set_focus(self.query_one("#prompt-input", AutoExpandingInput))
            except Exception:
                pass
            return

        self._last_escape_time = 0.0
        self._hide_hints()


    @on(events.Click, "#hero-view")
    @on(events.Click, "#hero-card")
    @on(events.Click, "#hero-input-box")
    def _on_hero_area_click(self, event: events.Click) -> None:
        try:
            self.set_focus(self.query_one("#hero-prompt-input", AutoExpandingInput))
        except Exception:
            pass


    @on(events.Click, "#bottom-dock")
    @on(events.Click, "#jump-to-new")
    def _on_jump_to_new(self, event: events.Click) -> None:
        """What `End` does, for the people who click instead of pressing it.

        The hint it replaces said "End aby wrocic" - the user was told the only
        way out was a key, while the cursor was sitting on the words describing
        it.
        """
        self.action_scroll_chat_bottom()


    @on(events.Click, "#input-card")
    def _on_chat_area_click(self, event: events.Click) -> None:
        try:
            self.set_focus(self.query_one("#prompt-input", AutoExpandingInput))
        except Exception:
            pass


    def _on_todo_updated(self, steps: List[str]) -> None:
        try:
            todo_bar = self.query_one("#todo-preview-bar", TodoPreviewBar)
            todo_bar.update_steps(steps)
        except Exception:
            pass


    def _on_tool_start(self, tool_name: str, target: str) -> None:
        self.stats["tools_run"] += 1
        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        if self._current_assistant_card:
            self.call_from_thread(self._current_assistant_card.add_tool, tool_name, target)
        else:
            block = ToolBlock(tool_name, target)
            self.call_from_thread(chat_scroll.mount, block)
        self.call_from_thread(self._scroll_chat_to_end_if_enabled)


    def _on_tool_finish(self, tool_name: str, target: str, result: Dict[str, Any]) -> None:
        if self._current_assistant_card:
            self.call_from_thread(self._current_assistant_card.finish_tool, result)
        else:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            blocks = chat_scroll.query(ToolBlock)
            if blocks:
                blocks[-1].finish(result)
        self.call_from_thread(self._scroll_chat_to_end_if_enabled)


    def _switch_to_chat(self) -> None:
        self.in_chat_mode = True
        try:
            hero_view = self.query_one("#hero-view", Vertical)
            chat_view = self.query_one("#chat-view", Vertical)
            hero_view.add_class("hidden")
            hero_view.styles.display = "none"
            chat_view.remove_class("hidden")
            chat_view.styles.display = "block"
            self.refresh(layout=True)
            self.query_one("#prompt-input", AutoExpandingInput).focus()
        except Exception:
            pass
        self._update_terminal_title()


    def _switch_to_hero(self) -> None:
        self.in_chat_mode = False
        try:
            chat_view = self.query_one("#chat-view", Vertical)
            hero_view = self.query_one("#hero-view", Vertical)
            chat_view.add_class("hidden")
            chat_view.styles.display = "none"
            hero_view.remove_class("hidden")
            hero_view.styles.display = "block"
            self.refresh(layout=True)
            self.query_one("#hero-prompt-input", AutoExpandingInput).focus()
        except Exception:
            pass
        self._update_terminal_title()


    def _switch_to_chat_view(self) -> None:
        self._switch_to_chat()


    def _add_msg(self, css_class: str, text: str) -> Static:
        container = self.query_one("#chat-scroll", VerticalScroll)
        w = Static(text, classes=css_class)
        container.mount(w)
        container.scroll_end(animate=False)
        return w


    def _feedback(self, text: str, title: str = "", severity: str = "information", timeout: float = 3.5) -> None:
        """Display system and command feedback strictly as a top-right Toast notification."""
        try:
            plain = re.sub(r"\[[^\]]*\]", "", text or "").strip()
            if not plain:
                return
            if len(plain) > 220:
                plain = plain[:217].rstrip() + "..."
            kwargs: Dict[str, Any] = {"severity": severity, "timeout": timeout}
            if title:
                kwargs["title"] = title
            self.notify(plain, **kwargs)
        except Exception:
            pass


    @on(AutoExpandingInput.Submitted, "#hero-prompt-input")
    def on_hero_input_submitted(self, event: AutoExpandingInput.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        self._handle_submit(prompt, from_hero=True)


    @on(AutoExpandingInput.Submitted, "#prompt-input")
    def on_chat_input_submitted(self, event: AutoExpandingInput.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        self._handle_submit(prompt, from_hero=False)


    @on(TextArea.Changed, "#hero-prompt-input")
    @on(TextArea.Changed, "#prompt-input")
    def on_input_changed(self, event: TextArea.Changed) -> None:
        if isinstance(event.text_area, AutoExpandingInput):
            event.text_area._update_auto_height()
            # A bare path typed into the prompt used to pin the file by itself,
            # mid-word, just because what you had typed so far happened to be
            # an existing file. Context is now attached explicitly with
            # @mention, completed by the hint dropdown.
            self._refresh_command_hints(event.text_area)


    def _refresh_command_hints(self, inp: AutoExpandingInput) -> None:
        self._hint_input_id = inp.id
        text = inp.text
        val = text.strip()
        mention = self._mention_query(text)
        if val.startswith("/"):
            self._hint_kind = "command"
            v = val.lower()
            matches = []
            for c, _desc in COMMANDS_DOC.items():
                cl = c.lower()
                if cl.startswith(v) or (len(v) > 1 and v[1:] in cl[1:]):
                    matches.append(c)
            self._hint_matches = matches
        elif mention is not None:
            self._hint_kind = "file"
            self._hint_matches = self._match_workspace_files(mention)
        else:
            self._hint_kind = "command"
            self._hint_matches = []
        self._hint_sel = 0 if self._hint_matches else -1
        self._hint_top = 0

        if self._hint_kind == "file":
            lines = [f"[b #58a6ff]{path}[/]  [dim]file[/]" for path in self._hint_matches]
        else:
            lines = [f"[b #58a6ff]{cmd}[/]  [dim]{COMMANDS_DOC.get(cmd, '')}[/]"
                     for cmd in self._hint_matches]
        container_map = {
            "hero-prompt-input": "#hero-cmd-hints",
            "prompt-input": "#cmd-hints",
        }
        active_hid = container_map.get(self._hint_input_id)
        other_hid = "#cmd-hints" if active_hid == "#hero-cmd-hints" else "#hero-cmd-hints"
        try:
            self.query_one(other_hid, VerticalScroll).styles.display = "none"
        except Exception:
            pass

        self._hint_rows = {}
        if active_hid:
            try:
                cont = self.query_one(active_hid, VerticalScroll)
                if lines:
                    cont.remove_children()
                    rows = [HintRow(line, hint_index=i, cmd=cmd)
                            for i, (line, cmd) in enumerate(zip(lines, self._hint_matches))]
                    cont.mount(*rows)
                    cont.styles.display = "block"
                    cont.scroll_to(y=0, animate=False)
                    self._hint_rows[active_hid] = rows
                else:
                    cont.styles.display = "none"
            except Exception:
                pass
        if lines:
            self._apply_hint_selection()


    def _apply_hint_selection(self) -> None:
        container_map = {
            "hero-prompt-input": "#hero-cmd-hints",
            "prompt-input": "#cmd-hints",
        }
        active_hid = container_map.get(self._hint_input_id)
        if active_hid is None or active_hid not in self._hint_rows:
            return
        rows = self._hint_rows[active_hid]
        try:
            cont = self.query_one(active_hid, VerticalScroll)
        except Exception:
            return
        for i, w in enumerate(rows):
            w.set_selected(i == self._hint_sel)

        top = self._hint_top
        vis = self.HINTS_VISIBLE_ROWS
        if 0 <= self._hint_sel < len(rows):
            if self._hint_sel < top:
                top = self._hint_sel
            elif self._hint_sel > top + vis - 1:
                top = self._hint_sel - vis + 1
        else:
            top = 0
        self._hint_top = top
        cont.scroll_to(y=top, animate=False)


    def _hide_hints(self) -> None:
        self._hint_matches = []
        self._hint_sel = -1
        self._hint_input_id = None
        self._hint_top = 0
        self._hint_rows = {}
        self._hint_kind = "command"
        for hid in ("#cmd-hints", "#hero-cmd-hints"):
            try:
                self.query_one(hid, VerticalScroll).styles.display = "none"
            except Exception:
                pass


    def _hint_move(self, delta: int) -> None:
        if not self._hint_matches:
            return
        n = len(self._hint_matches)
        if self._hint_sel < 0:
            self._hint_sel = 0 if delta > 0 else n - 1
        else:
            self._hint_sel = (self._hint_sel + delta) % n
        self._apply_hint_selection()


    @staticmethod
    def _mention_query(text: str) -> Optional[str]:
        """Text after the trailing `@`, or None when no mention is being typed.

        Only the last token counts: an `@` in the middle of a sentence is
        already part of the prompt, not something to complete.
        """
        if not text or text[-1].isspace():
            return None
        token = text.rsplit(None, 1)[-1]
        if token.startswith("@"):
            return token[1:]
        return None


    def _workspace_files(self) -> List[str]:
        """Project-relative posix paths, cached briefly.

        The cache is what keeps typing usable: without it every keystroke
        after `@` would walk the whole tree.
        """
        now = time.time()
        if self._file_cache and now - self._file_cache_at < 5.0:
            return self._file_cache
        found: List[str] = []
        try:
            for root, dirs, files in os.walk(self.workdir):
                dirs[:] = [d for d in dirs
                           if d not in self._MENTION_SKIP_DIRS and not d.startswith(".")]
                rel_root = os.path.relpath(root, self.workdir)
                for name in files:
                    rel = name if rel_root == "." else os.path.join(rel_root, name)
                    found.append(rel.replace("\\", "/"))
                if len(found) >= 4000:
                    break
        except Exception:
            pass
        found.sort()
        self._file_cache = found
        self._file_cache_at = now
        return found


    def _match_workspace_files(self, query: str, limit: int = 8) -> List[str]:
        q = query.lower().replace("\\", "/")
        files = self._workspace_files()
        if not q:
            hits = files[:limit]
        else:
            hits = [f for f in files if q in f.lower()]
            # Shorter paths first: `app.py` before `src/x/deep/app.py`.
            hits.sort(key=lambda f: (not f.lower().endswith(q), len(f)))
            hits = hits[:limit]
        return ["@" + f for f in hits]


    @staticmethod
    def _replace_mention(text: str, completion: str) -> str:
        """Swap the trailing `@token` for `completion`, keeping earlier text."""
        head, sep, tail = text.rpartition("@")
        if not sep or any(ch.isspace() for ch in tail):
            return text + completion + " "
        return head + completion + " "


    def _pin_mentions(self, prompt: str) -> None:
        """Attach every existing `@path` in the prompt to the context."""
        if not prompt or prompt[0] in ("/", "!", "$"):
            return
        seen: set = set()
        for mention in re.findall(r"(?<![@\w])@([^\s@]+)", prompt):
            path = mention.strip("'\"")
            if not path or path in seen:
                continue
            seen.add(path)
            full = path if os.path.isabs(path) else os.path.join(self.workdir, path)
            if os.path.isfile(full):
                self._add_file_to_context(path)


    def _hint_complete(self) -> None:
        if not self._hint_matches:
            return
        sel = self._hint_sel if self._hint_sel >= 0 else 0
        cmd = self._hint_matches[sel]
        try:
            inp = self.query_one(f"#{self._hint_input_id}", AutoExpandingInput)
            if self._hint_kind == "file":
                inp.text = self._replace_mention(inp.text, cmd)
            else:
                inp.text = cmd + " "
            inp._update_auto_height()
            lines = inp.text.split("\n")
            inp.cursor_location = (max(0, len(lines) - 1), len(lines[-1]) if lines else 0)
        except Exception:
            pass


    def _hint_execute(self) -> None:
        if not self._hint_matches or not (0 <= self._hint_sel < len(self._hint_matches)):
            return
        # A file mention completes in place; Enter submits on the next press.
        # Submitting `@ap` here would send a half-typed path to the model.
        if self._hint_kind == "file":
            self._hint_complete()
            return
        cmd = self._hint_matches[self._hint_sel]
        from_hero = self._hint_input_id == "hero-prompt-input"
        try:
            inp = self.query_one(f"#{self._hint_input_id}", AutoExpandingInput)
            inp.text = ""
            inp.styles.height = 2
            inp._update_auto_height()
        except Exception:
            pass
        self._hide_hints()
        self._handle_submit(cmd, from_hero=from_hero)


    def _hint_click(self, idx: int) -> None:
        if not (0 <= idx < len(self._hint_matches)):
            return
        self._hint_sel = idx
        self._apply_hint_selection()
        self._hint_execute()


    def _hint_select_and_execute(self, cmd: str) -> None:
        if self._hint_kind == "file":
            self._hint_complete()
            return
        from_hero = self._hint_input_id == "hero-prompt-input"
        self._hide_hints()
        try:
            inp = self.query_one(f"#{self._hint_input_id}", AutoExpandingInput)
            inp.text = ""
            inp.styles.height = 2
            inp._update_auto_height()
        except Exception:
            pass
        self._handle_submit(cmd, from_hero=from_hero)

