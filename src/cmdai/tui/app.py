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

from ..agent.runner import AgentRunner
from ..agent.tools import AgentContext
from ..core.branding import (
    CMDAI_PARTS as _CMDAI_PARTS,
    CODE_PARTS as _CODE_PARTS,
    LOGO_HERO as _LOGO_HERO,
    get_hero_logo as _get_hero_logo,
)
from ..core.capabilities import (
    ModelCapability,
    clean_model_name,
    cycle_order,
    forget_uncached_capabilities,
    get_model_capability,
    normalize_level,
)
from ..core.engine import SimpleGGUFLoader
from ..core.providers import PROVIDERS_CATALOG, stream_chat_completion
from ..core.resource_limits import (
    build_summarization_payload,
    detect_conversation_language,
    estimate_model_memory,
    estimate_token_count,
    format_compacted_handoff,
    get_dynamic_context_limit,
    is_summarization_error_text,
    should_summarize,
)
from ..core.server import BackgroundHTTPServer
from ..core.sessions import SessionManager, transcript_from_messages
from ..core.settings import get_settings
from ..core.notifier import send_desktop_notification
from ..core.exporter import export_session_to_html
from ..core.releases import is_newer as _is_newer
from ..core.providers import TOOL_COMPLETE_PATTERNS, is_stream_tool_or_turn_complete
from .modals.actions import ActionMenuModal
from .modals.approval import EditInspectModal
from .modals.ask import AskModal
from .modals.commit import CommitModal
from .modals.context import ContextInspectorModal
from .modals.diff import DiffModal
from .modals.files import FilePickerModal
from .modals.plan import PlanModal
from .modals.help import COMMANDS_DOC, HelpModal, InfoModal
from .modals.models import ModelSelectModal
from .modals.thinking import ThinkingModal
from .modals.params import ParamsModal
from .modals.prompts import SystemPromptModal
from .modals.sessions import SessionsModal
from .modals.settings import LoaderModal, SettingsModal
from .modals.stats import StatsModal
from .modals.releases import ReleasesModal, UpdateNoticeModal
from .styles import SWIFT_CSS
from .widgets import (
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
from ..mcp.client import sanitize_name

# Logo lives in core/branding.py so the CLI can print it without Textual.
CMDAI_PARTS = _CMDAI_PARTS
CODE_PARTS = _CODE_PARTS
get_hero_logo = _get_hero_logo
LOGO_HERO = _LOGO_HERO

from .app_parts.commands_mixin import CommandsMixin
from .app_parts.sessions_mixin import SessionsMixin
from .app_parts.ui_mixin import UIMixin
from .app_parts.worker_mixin import WorkerMixin

from textual.screen import Screen
from textual.widget import Widget
from textual.widgets._tooltip import Tooltip
from textual.geometry import Offset

_orig_screen_handle_tooltip_timer = getattr(Screen, "_handle_tooltip_timer", None)
if _orig_screen_handle_tooltip_timer is not None:
    def _patched_screen_handle_tooltip_timer(self, widget: Widget) -> None:
        _orig_screen_handle_tooltip_timer(self, widget)
        try:
            tooltip = self.get_child_by_type(Tooltip)
            if tooltip.display:
                is_timeline = False
                for node in widget.ancestors_with_self:
                    if getattr(node, "id", None) == "chat-timeline-rail" or "timeline-dot" in getattr(node, "classes", set()):
                        is_timeline = True
                        break
                if is_timeline:
                    tooltip.add_class("-timeline-tooltip")
                    dot_y = widget.region.y if getattr(widget, "region", None) else self.app.mouse_position.y
                    tooltip.absolute_offset = Offset(3, dot_y)
                else:
                    tooltip.remove_class("-timeline-tooltip")
        except Exception:
            pass

    Screen._handle_tooltip_timer = _patched_screen_handle_tooltip_timer

_orig_screen_clear_tooltip = getattr(Screen, "_clear_tooltip", None)
if _orig_screen_clear_tooltip is not None:
    def _patched_screen_clear_tooltip(self) -> None:
        _orig_screen_clear_tooltip(self)
        try:
            tooltip = self.get_child_by_type(Tooltip)
            tooltip.remove_class("-timeline-tooltip")
        except Exception:
            pass

    Screen._clear_tooltip = _patched_screen_clear_tooltip


class ChatScroll(VerticalScroll):

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        app = getattr(self, "app", None)
        if app is not None and hasattr(app, "_update_timeline_active_on_scroll"):
            app._update_timeline_active_on_scroll(self)

    def _scroll_target_y(self) -> float:
        try:
            t = getattr(self, "scroll_target_y", None)
            if t is not None:
                return float(t)
        except Exception:
            pass
        try:
            return float(self.scroll_y)
        except Exception:
            return 0.0

    def _is_at_bottom(self, target_y: Optional[float] = None) -> bool:
        try:
            y = float(target_y) if target_y is not None else self._scroll_target_y()
            return bool(y >= (float(self.max_scroll_y) - 2)) or bool(
                getattr(self, "is_vertical_scroll_end", False)
            )
        except Exception:
            return False

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            app.auto_scroll_enabled = False
            app._last_user_scroll_time = time.time()
            app._timeline_nav_target_idx = None
        try:
            target = max(0.0, self._scroll_target_y() - 3)
            self.scroll_to(y=target, animate=False, immediate=True)
        except Exception:
            try:
                self.scroll_relative(y=-3, animate=False, immediate=True)
            except TypeError:
                self.scroll_relative(y=-3, animate=False)
        # Scrolling up is the moment the control is for: the newest messages
        # are now out of view, and nothing else showed it. The wheel-up handler
        # used to only switch off auto-scroll, so the control never appeared
        # for the most common way of scrolling.
        try:
            if app is not None:
                app._refresh_follow_hint(self)
        except Exception:
            pass
        event.prevent_default()
        event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        try:
            target = min(float(self.max_scroll_y), self._scroll_target_y() + 3)
            self.scroll_to(y=target, animate=False, immediate=True)
        except Exception:
            try:
                self.scroll_relative(y=3, animate=False, immediate=True)
            except TypeError:
                self.scroll_relative(y=3, animate=False)
        app = getattr(self, "app", None)
        if app is not None:
            app._timeline_nav_target_idx = None
            try:
                if self._is_at_bottom():
                    app.auto_scroll_enabled = True
                    app._last_user_scroll_time = 0.0
                    try:
                        app._set_follow_hint(False)
                    except Exception:
                        pass
                else:
                    # User is manually working back down: stay manual for a
                    # moment so streaming does not yank the view away.
                    app.auto_scroll_enabled = False
                    app._last_user_scroll_time = time.time()
                    try:
                        app._refresh_follow_hint(self)
                    except Exception:
                        pass
            except Exception:
                pass
        event.prevent_default()
        event.stop()


def _match_agents_to_calls(calls: List[Dict[str, Any]],
                           agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pair each <tool:subagent> call with the result it produced.

    Matched by role, first come first served, which is the same rule the live
    chat uses to hand a progress event to the marker that is still running. A
    call with no matching result gets an empty dict rather than a neighbour's
    report: showing the wrong subagent's output would be worse than none.
    """
    remaining = list(agents or [])
    paired: List[Dict[str, Any]] = []
    for call in (calls or []):
        role = str((call or {}).get("name") or (call or {}).get("role") or "").strip()
        match = {}
        for i, agent in enumerate(remaining):
            if str(agent.get("role", "")).strip() == role:
                match = agent
                del remaining[i]
                break
        paired.append(match)
    return paired




class CMDAICodeTUI(UIMixin, SessionsMixin, CommandsMixin, WorkerMixin, App):
    CSS = SWIFT_CSS
    HINTS_VISIBLE_ROWS: int = 5

    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    BINDINGS = [
        Binding("ctrl+c", "handle_interrupt", "Cancel/Interrupt", priority=True),
        Binding("escape", "handle_escape", "Cancel/Abort", priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle Mode", show=True, priority=True),
        Binding("backtab", "cycle_mode", "Cycle Mode", show=False, priority=True),
        Binding("pageup", "scroll_chat_up", "Scroll chat up", show=False, priority=True),
        Binding("pagedown", "scroll_chat_down", "Scroll chat down", show=False, priority=True),
        Binding("home", "scroll_chat_top", "Scroll to chat start", show=False, priority=True),
        Binding("end", "scroll_chat_bottom", "Scroll to chat end", show=False, priority=True),
        Binding("shift+up", "scroll_chat_line_up", "Scroll line up", show=False, priority=True),
        Binding("shift+down", "scroll_chat_line_down", "Scroll line down", show=False, priority=True),
        Binding("ctrl+up", "scroll_chat_line_up", "Scroll line up", show=False, priority=True),
        Binding("ctrl+down", "scroll_chat_line_down", "Scroll line down", show=False, priority=True),
        Binding("ctrl+t", "cycle_thinking", "Thinking", show=True, priority=True),
        Binding("ctrl+shift+t", "toggle_tools", "Toggle Tools", show=False, priority=True),
        Binding("ctrl+b", "send_to_background", "Send to Background", show=False, priority=True),
    ]

    # An agent question blocks the worker thread that asked it, so it needs a
    # ceiling. Long enough that reading a tool description and deciding is not
    # cut short, short enough that a lost modal surfaces as an error instead of
    # an app that never responds again.
    ASK_TIMEOUT = 180.0
    META_MARKERS = ["⌬", "✻"]
    THINKING_MARKERS = ["⌬", "✻"]
    _MENTION_SKIP_DIRS = {
        ".git", ".hg", ".svn", "__pycache__", "node_modules", "venv", ".venv",
        "env", "dist", "build", "target", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", "models", "logs", "cache", ".freebuff",
    }


    @property
    def is_generating(self) -> bool:
        curr_sid = str(getattr(self, "current_session_id", "") or "")
        return bool(curr_sid and curr_sid in getattr(self, "_active_generation_sessions", set()))


    @is_generating.setter
    def is_generating(self, val: bool) -> None:
        curr_sid = str(getattr(self, "current_session_id", "") or "")
        if not hasattr(self, "_active_generation_sessions"):
            self._active_generation_sessions = set()
        if val and curr_sid:
            self._active_generation_sessions.add(curr_sid)
        elif not val and curr_sid:
            self._active_generation_sessions.discard(curr_sid)


    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.settings = get_settings()
        self.workdir = os.path.abspath(workdir)
        try:
            os.chdir(self.workdir)
        except Exception:
            pass
        self.session_manager = SessionManager(project_dir=self.workdir)
        self.current_session_id: str = f"session_{int(time.time())}"
        self.session_title: str = ""
        self._session_named_by_model: bool = False
        self._open_session_ids: List[str] = [self.current_session_id]
        # What the chat showed, in order. `messages` alone cannot rebuild a
        # session: it is the model's view, holding raw tool feedback and no
        # record of the tool cards, so a replay from it loses every tool and
        # prints tool output as if the user had typed it.
        self._transcript: List[Dict[str, Any]] = []
        self._last_escape_time: float = 0.0

        self.current_provider: str = self.settings.config.get("default_provider", "openrouter")
        if self.current_provider in ("opencode_local", "local_opencode"):
            self.current_provider = "local_gguf"
        self.current_model_id: str = self.settings.config.get("default_model", "")
        if self.current_provider == "local_gguf" and (not self.current_model_id or self.current_model_id == "default"):
            from ..core.providers import fetch_remote_models
            local_models = fetch_remote_models("local_gguf")
            if local_models:
                coder_model = next((m["id"] for m in local_models if "coder" in m["id"].lower()), local_models[0]["id"])
                self.current_model_id = coder_model

        if not self.current_model_id:
            info = PROVIDERS_CATALOG.get(self.current_provider)
            self.current_model_id = info.default_models[0] if info and info.default_models else "default"

        self.agent_mode: str = self.settings.config.get("agent", {}).get("mode", "auto")
        init_cap = get_model_capability(self.current_model_id, self.current_provider)
        # Kept from the first read so Ctrl+T never re-probes the network.
        self._cap: Optional[ModelCapability] = init_cap
        self.thinking_level = ""
        if init_cap and init_cap.thinking:
            order = cycle_order(init_cap.levels)
            stored = normalize_level(
                self.settings.config.get("generation", {}).get("reasoning_level")
            )
            self.thinking_level = (
                stored if stored in order else (init_cap.default_level or order[0])
            )
        # Otherwise: detected as a model that does not reason, and the stored
        # level is deliberately left alone so switching back to a reasoning
        # model restores the choice.
        self._meta_spinner_idx: int = 0
        # Whether the user has put thinking into play on this model. The label
        # stays hidden until then: a model that *can* reason is not a statement
        # that it *is* reasoning.
        self._thinking_invoked: bool = False
        self._pending_approval_evt: Optional[threading.Event] = None
        self._pending_approval_res: Optional[List[str]] = None
        self._pending_approval_details: Optional[Dict[str, Any]] = None
        self._meta_spinner_timer = None

        self.system_prompt: str = self._build_base_system_prompt()

        self.messages: List[Dict[str, Any]] = []
        self._turn_counter: int = 0
        self.in_chat_mode: bool = False
        self._active_generation_sessions: set = set()
        self.abort_requested: bool = False
        # Set while /update's worker thread is pulling. Guards against a second
        # update racing the same working tree.
        self._update_running: bool = False
        self.auto_scroll_enabled: bool = True
        self._last_user_scroll_time: float = 0.0
        self._last_auto_scroll_time: float = 0.0
        self._follow_hint_visible: bool = False

        self._hint_matches: List[str] = []
        self._hint_sel: int = -1
        self._hint_input_id: Optional[str] = None
        self._hint_rows: Dict[str, List[HintRow]] = {}
        self._hint_top: int = 0
        # "command" for the slash dropdown, "file" for @mentions. They share
        # one list and one selection index, so completion has to know which
        # kind it is completing.
        self._hint_kind: str = "command"
        self._file_cache: List[str] = []
        self._file_cache_at: float = 0.0

        self.agent_ctx = AgentContext(workdir=self.workdir)
        self.agent_ctx.mode = self.agent_mode
        self.pinned_files: Dict[str, str] = {}
        self._last_modified_files: List[str] = []
        try:
            from ..mcp.client import MCPClient, sanitize_name
            self.mcp_client = MCPClient()
            # Writable, so /mcp can offer the Add form. The model has no
            # equivalent: it can use servers you approved and cannot
            # register new ones.
            self.mcp_client.add_server = self.mcp_client._write_server
        except Exception:
            self.mcp_client = None
        self.agent_runner = AgentRunner(
            ctx=self.agent_ctx,
            on_todo_updated=self._on_todo_updated,
            mcp_client=self.mcp_client,
            # Lets the runner attach images returned by MCP tools when the
            # loaded model can actually see them, and say so when it cannot.
            vision_capable=self._model_supports_vision,
        )
        self._current_assistant_card: Optional[AssistantTurnCard] = None

        self.gguf_loader = SimpleGGUFLoader()
        self.http_server: Optional[BackgroundHTTPServer] = None

        self.stats = {
            "tokens_in": 0,
            "tokens_out": 0,
            "tok_per_sec": 0.0,
            "tools_run": 0,
        }


    def compose(self) -> ComposeResult:
        yield SessionTabsBar(id="session-tabs-bar")
        with Vertical(id="hero-view"):
            with Vertical(id="hero-card"):
                yield Static(LOGO_HERO, id="hero-logo")
                with Vertical(id="hero-input-box"):
                    yield VerticalScroll(id="hero-cmd-hints")
                    yield AutoExpandingInput(id="hero-prompt-input")
                    with Horizontal(id="hero-meta-bar"):
                        yield Static("", id="hero-meta-left")
                        yield Static("", id="hero-meta-mode", classes="meta-clickable")
                        yield Static("", id="hero-meta-thinking", classes="meta-clickable")
                        yield Static("", id="hero-meta-spacer")
                        rel_work = os.path.basename(self.workdir) or self.workdir
                        yield Static(f"[dim]{rel_work}[/dim]", id="hero-meta-right")
            yield Static("", id="hero-version")

        with Vertical(id="chat-view", classes="hidden"):
            yield TimelineRail(id="chat-timeline-rail")
            yield ChatScroll(id="chat-scroll")
            with Vertical(id="bottom-dock"):
                # Scrolled up, there is something below you. It used to be
                # a Polish sentence wedged into the meta row telling you to
                # press End: "v nowe - End aby wrocic". Four words in a
                # language the rest of the UI does not use, describing a
                # single click that was not wired to anything.
                #
                # Its own row, on the right, above the input card rather than
                # inside it: the card is gray and this sits on the black
                # margin, where it cannot be mistaken for input chrome.
                # Clicking it does what End did.
                with Horizontal(id="jump-bar"):
                    yield Static("", id="jump-spacer")
                    yield Static("", id="jump-to-new")
                with Vertical(id="input-card"):
                    yield TodoPreviewBar(id="todo-preview-bar")
                    yield ApprovalBar(id="approval-bar")
                    yield VerticalScroll(id="cmd-hints")
                    yield AutoExpandingInput(id="prompt-input")
                    with Horizontal(id="input-meta-bar"):
                        yield Static("", id="input-meta-left")
                        yield Static("", id="input-meta-mode", classes="meta-clickable")
                        yield Static("", id="input-meta-thinking", classes="meta-clickable")
                        yield Static("", id="input-meta-spacer")
                        yield Static("", id="input-meta-right")


    def on_mount(self) -> None:
        try:
            sys.stdout.write("\x1b]11;#000000\x07")
            sys.stdout.flush()
        except Exception:
            pass

        try:
            import signal
            def _sigint_handler(s, f):
                try:
                    self.call_soon(self.action_handle_interrupt)
                except Exception:
                    try:
                        self.call_from_thread(self.action_handle_interrupt)
                    except Exception:
                        pass
            signal.signal(signal.SIGINT, _sigint_handler)
        except Exception:
            pass

        self._refresh_capabilities()
        self._update_meta_bars()
        self._meta_spinner_timer = self.set_interval(0.08, self._tick_meta_spinner)

        self.agent_runner = AgentRunner(
            self.agent_ctx,
            on_tool_start=self._on_tool_start,
            on_tool_finish=self._on_tool_finish,
            on_todo_updated=self._on_todo_updated,
            ask_handler=self._handle_agent_ask,
            mcp_client=self.mcp_client,
        )
        try:
            if self.mcp_client is not None:
                self.mcp_client.connect_autostart(getattr(self.settings, "base_dir", os.getcwd()))
        except Exception:
            pass

        port = self.settings.config.get("server", {}).get("port", 8080)
        self.http_server = BackgroundHTTPServer(port=port, app_ref=self)
        self.http_server.start()

        hero_inp = self.query_one("#hero-prompt-input", AutoExpandingInput)
        self.set_focus(hero_inp)
        self._update_terminal_title()
        self.set_timer(0.6, self._maybe_show_update_notice)
        self.set_timer(3.0, self._maybe_auto_update_check)
        self._refresh_hero_version()
        self._refresh_hero_version_live()
        self._refresh_session_tabs()


    def on_unmount(self) -> None:
        self._flush_live_turn_to_session()
        try:
            self.abort_requested = True
        except Exception:
            pass

    @on(ApprovalBar.ActionSelected)
    def on_approval_action_selected(self, msg: ApprovalBar.ActionSelected) -> None:
        super().on_approval_action_selected(msg)

    @on(TimelineRail.TurnSelected)
    def on_timeline_turn_selected(self, msg: TimelineRail.TurnSelected) -> None:
        super().on_timeline_turn_selected(msg)

    @on(TimelineRail.ScrollRequested)
    def on_timeline_scroll_requested(self, msg: TimelineRail.ScrollRequested) -> None:
        super().on_timeline_scroll_requested(msg)

    @on(SessionTabsBar.TabSelected)
    def on_session_tab_selected(self, msg: SessionTabsBar.TabSelected) -> None:
        super().on_session_tab_selected(msg)

    @on(SessionTabsBar.TabClosed)
    def on_session_tab_closed(self, msg: SessionTabsBar.TabClosed) -> None:
        super().on_session_tab_closed(msg)

    @on(SessionTabsBar.NewTabRequested)
    def on_session_new_tab_requested(self, msg: SessionTabsBar.NewTabRequested) -> None:
        super().on_session_new_tab_requested(msg)

    @on(events.Click, "#input-meta-mode")
    @on(events.Click, "#hero-meta-mode")
    def on_mode_clicked(self, event: events.Click) -> None:
        super().on_mode_clicked(event)

    @on(events.Click, "#input-meta-thinking")
    @on(events.Click, "#hero-meta-thinking")
    def on_thinking_clicked(self, event: events.Click) -> None:
        super().on_thinking_clicked(event)

    @on(events.Click, "#hero-view")
    @on(events.Click, "#hero-card")
    @on(events.Click, "#hero-input-box")
    def _on_hero_area_click(self, event: events.Click) -> None:
        super()._on_hero_area_click(event)

    @on(events.Click, "#bottom-dock")
    @on(events.Click, "#jump-to-new")
    def _on_jump_to_new(self, event: events.Click) -> None:
        super()._on_jump_to_new(event)

    @on(events.Click, "#input-card")
    def _on_chat_area_click(self, event: events.Click) -> None:
        super()._on_chat_area_click(event)

    @on(AutoExpandingInput.Submitted, "#hero-prompt-input")
    def on_hero_input_submitted(self, event: AutoExpandingInput.Submitted) -> None:
        super().on_hero_input_submitted(event)

    @on(AutoExpandingInput.Submitted, "#prompt-input")
    def on_chat_input_submitted(self, event: AutoExpandingInput.Submitted) -> None:
        super().on_chat_input_submitted(event)

    @on(TextArea.Changed, "#hero-prompt-input")
    @on(TextArea.Changed, "#prompt-input")
    def on_input_changed(self, event: TextArea.Changed) -> None:
        super().on_input_changed(event)


