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
from ..core.capabilities import clean_model_name, get_model_capability
from ..core.engine import SimpleGGUFLoader
from ..core.providers import PROVIDERS_CATALOG, stream_chat_completion
from ..core.resource_limits import (
    build_summarization_payload,
    estimate_model_memory,
    estimate_token_count,
    format_compacted_handoff,
    get_dynamic_context_limit,
    should_summarize,
)
from ..core.server import BackgroundHTTPServer
from ..core.sessions import SessionManager
from ..core.settings import get_settings
from ..core.notifier import send_desktop_notification
from ..core.exporter import export_session_to_html
from .modals.actions import ActionMenuModal
from .modals.api_key import ApiKeyModal
from .modals.approval import EditInspectModal
from .modals.ask import AskModal
from .modals.commit import CommitModal
from .modals.context import ContextInspectorModal
from .modals.diff import DiffModal
from .modals.files import FilePickerModal
from .modals.plan import PlanModal
from .modals.help import COMMANDS_DOC, HelpModal, InfoModal
from .modals.models import ModelSelectModal
from .modals.params import ParamsModal
from .modals.prompts import SystemPromptModal
from .modals.sessions import SessionsModal
from .modals.settings import LoaderModal, SettingsModal
from .modals.stats import StatsModal
from .styles import SWIFT_CSS
from .widgets import (
    ApprovalBar,
    AssistantTurnCard,
    AutoExpandingInput,
    ClickableMessage,
    HintRow,
    ThinkingBlock,
    TodoPreviewBar,
    ToolBlock,
    SubagentBlock,
    UserMessageCard,
    copy_text_to_clipboard,
)

TOOL_COMPLETE_PATTERNS = re.compile(
    r'(?:'
    r'<tool:\w+[^>]*?/>'
    r'|</tool:\w+>'
    r'|<tool_call>[^>]*?/>'
    r'|</tool_call>'
    r'|<\|tool_call>[^>]*?/>'
    r'|<tool:(?:ls|read|glob|search|code_search|bugs)[^>]*?>'
    r')',
    re.IGNORECASE
)

CMDAI_PARTS = [
    " ██████╗███╗   ███╗██████╗  █████╗ ██╗",
    "██╔════╝████╗ ████║██╔══██╗██╔══██╗██║",
    "██║     ██╔████╔██║██║  ██║███████║██║",
    "██║     ██║╚██╔╝██║██║  ██║██╔══██║██║",
    "╚██████╗██║ ╚═╝ ██║██████╔╝██║  ██║██║",
    " ╚═════╝╚═╝     ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝",
]

CODE_PARTS = [
    " ██████╗ ██████╗ ██████╗ ███████╗",
    "██╔════╝██╔═══██╗██╔══██╗██╔════╝",
    "██║     ██║   ██║██║  ██║█████╗  ",
    "██║     ██║   ██║██║  ██║██╔══╝  ",
    "╚██████╗╚██████╔╝██████╔╝███████╗",
    " ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
]


def get_hero_logo(d: Optional[datetime.date] = None) -> str:
    """Return daily rotating ANSI logo matching original CMDAI CODE repo:
    cycle 0: whole logo white
    cycle 1: CMDAI gray, CODE white
    cycle 2: CMDAI white, CODE gray
    cycle 3: both gray
    Then cycles back to whole logo white.
    """
    if d is None:
        d = datetime.date.today()
    day_of_year = d.timetuple().tm_yday
    cycle = day_of_year % 4
    WHITE = "#ffffff"
    GRAY = "#888888"
    GAP = "   "

    if cycle == 0:
        cmdai_color, code_color = WHITE, WHITE
    elif cycle == 1:
        cmdai_color, code_color = GRAY, WHITE
    elif cycle == 2:
        cmdai_color, code_color = WHITE, GRAY
    else:
        cmdai_color, code_color = GRAY, GRAY

    lines = []
    for c_line, cd_line in zip(CMDAI_PARTS, CODE_PARTS):
        lines.append(f"[{cmdai_color}]{c_line}[/]{GAP}[{code_color}]{cd_line}[/]")
    return "\n".join(lines)


LOGO_HERO = get_hero_logo()


class ChatScroll(VerticalScroll):
    """Chat container that immediately pauses auto-scroll upon any scroll-up event, eliminating bounce."""

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            app.auto_scroll_enabled = False
            app._last_user_scroll_time = time.time()
        self.scroll_relative(y=-3, animate=False)
        event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.scroll_relative(y=3, animate=False)
        app = getattr(self, "app", None)
        if app is not None and (self.scroll_y >= (self.max_scroll_y - 2) or getattr(self, "is_vertical_scroll_end", False)):
            app.auto_scroll_enabled = True
        event.stop()


class CMDAICodeTUI(App):
    """Swift-identical Textual TUI for CMDAI CODE."""

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
        Binding("shift+up", "scroll_chat_line_up", "Scroll line up", show=False, priority=True),
        Binding("shift+down", "scroll_chat_line_down", "Scroll line down", show=False, priority=True),
        Binding("ctrl+up", "scroll_chat_line_up", "Scroll line up", show=False, priority=True),
        Binding("ctrl+down", "scroll_chat_line_down", "Scroll line down", show=False, priority=True),
        Binding("ctrl+t", "toggle_tools", "Toggle Tools", show=True, priority=True),
    ]

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.settings = get_settings()
        self.workdir = os.path.abspath(workdir)
        self.session_manager = SessionManager(project_dir=self.workdir)
        self.current_session_id: str = f"session_{int(time.time())}"
        self._last_escape_time: float = 0.0

        self.current_provider: str = self.settings.config.get("default_provider", "openrouter")
        self.current_model_id: str = self.settings.config.get("default_model", "")
        if not self.current_model_id:
            info = PROVIDERS_CATALOG.get(self.current_provider)
            self.current_model_id = info.default_models[0] if info and info.default_models else "default"

        self.agent_mode: str = self.settings.config.get("agent", {}).get("mode", "auto")
        init_cap = get_model_capability(self.current_model_id, self.current_provider)
        if init_cap and init_cap.thinking:
            self.thinking_level = self.settings.config.get("generation", {}).get("reasoning_level", init_cap.default_level or "Medium")
            self._show_thinking = (self.thinking_level != "Off")
        else:
            self.thinking_level = "Off"
            self.settings.config.setdefault("generation", {})["reasoning_level"] = "Off"
            self._show_thinking = False
        self._meta_spinner_idx: int = 0
        self._pending_approval_evt: Optional[threading.Event] = None
        self._pending_approval_res: Optional[List[str]] = None
        self._pending_approval_details: Optional[Dict[str, Any]] = None
        self._meta_spinner_timer = None

        self.system_prompt: str = (
            "You are CMDAI CODE, an autonomous terminal AI programming agent running on Windows.\n"
            f"Current working directory: {self.workdir}\n\n"
            "CRITICAL INSTRUCTIONS FOR CODE AGENT BEHAVIOR:\n"
            "1. You are an AUTONOMOUS CODE AGENT. NEVER tell the user to inspect files, run commands, or check directory structure themselves. YOU MUST DO IT YOURSELF using the XML tools immediately.\n"
            "2. When the user asks you to check, build, edit, or debug a project, DO NOT ask permission or tell the user to prepare the project — immediately run <tool:ls path=\".\" /> or <tool:read path=\"file\" /> to inspect it.\n"
            "3. Answer in the same language as the user (if the user asks in Polish, respond in Polish).\n"
            "4. When you emit a tool call, the system will execute it and return the result inside <tool_response name=\"...\">...</tool_response>. You will then see the output and continue working until the task is complete.\n"
            "5. If a tool call fails (e.g. <tool:edit> fails with \"don't match\"), DO NOT GIVE UP or stop! You MUST immediately inspect the file using <tool:read>, locate the exact content, and retry <tool:edit> or use <tool:write>. The turn is only complete when all changes are properly applied.\n\n"
            "AVAILABLE XML TOOLS:\n"
            "- <tool:ls path=\".\" /> : List files in directory\n"
            "- <tool:read path=\"file.py\" lines=\"1-50\" /> : Read file contents\n"
            "- <tool:glob pattern=\"*.py\" dir=\".\" /> : Find files matching glob\n"
            "- <tool:search query=\"text\" path=\".\" /> : Search text across files\n"
            "- <tool:write path=\"file.py\">content</tool:write> : Create or overwrite file\n"
            "- <tool:edit path=\"file.py\"><old>exact_code</old><new>replacement_code</new></tool:edit> : Edit code\n"
            "- <tool:command>shell_command</tool:command> : Run terminal command\n"
            "- <tool:web>url_or_query</tool:web> : Web fetch or search\n"
            "- <tool:scratch action=\"add|done|clear\">step note</tool:scratch> : Update TODO queue\n"
            "- <tool:ask question=\"Question to user\" options=\"Option 1|Option 2|Option 3\" /> : Prompt user for choice/confirmation\n"
        )

        self.messages: List[Dict[str, Any]] = []
        self.in_chat_mode: bool = False
        self.is_generating: bool = False
        self.abort_requested: bool = False
        self.auto_scroll_enabled: bool = True
        self._last_user_scroll_time: float = 0.0

        self._hint_matches: List[str] = []
        self._hint_sel: int = -1
        self._hint_input_id: Optional[str] = None
        self._hint_rows: Dict[str, List[HintRow]] = {}
        self._hint_top: int = 0

        self.agent_ctx = AgentContext(workdir=self.workdir)
        self.agent_ctx.mode = self.agent_mode
        self.pinned_files: Dict[str, str] = {}
        self._last_modified_files: List[str] = []
        self.agent_runner = AgentRunner(
            ctx=self.agent_ctx,
            on_todo_updated=self._on_todo_updated,
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
    
    def _on_todo_updated(self, todos: List[str]) -> None:
        try:
            preview_bar = self.query_one(TodoPreviewBar)
            self.call_from_thread(preview_bar.update_todos, todos)
        except Exception:
            pass

    def compose(self) -> ComposeResult:
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

        with Vertical(id="chat-view", classes="hidden"):
            yield ChatScroll(id="chat-scroll")
            with Vertical(id="bottom-dock"):
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
                        yield Static("[dim]esc: cancel[/dim]", id="input-meta-right")

    def on_mount(self) -> None:
        try:
            sys.stdout.write("\x1b]11;#000000\x07")
            sys.stdout.flush()
        except Exception:
            pass

        try:
            import signal
            signal.signal(signal.SIGINT, lambda s, f: self.call_from_thread(self.action_handle_interrupt))
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
        )

        port = self.settings.config.get("server", {}).get("port", 8080)
        self.http_server = BackgroundHTTPServer(port=port, app_ref=self)
        self.http_server.start()

        hero_inp = self.query_one("#hero-prompt-input", AutoExpandingInput)
        self.set_focus(hero_inp)

    def _refresh_capabilities(self) -> None:
        try:
            cap = get_model_capability(self.current_model_id, self.current_provider)
            if cap and cap.thinking:
                cfg_lvl = self.settings.config.get("generation", {}).get("reasoning_level")
                if cfg_lvl and (not cap.levels or cfg_lvl in cap.levels or cfg_lvl == "Off"):
                    self.thinking_level = cfg_lvl
                elif not self.thinking_level or (cap.levels and self.thinking_level not in cap.levels):
                    self.thinking_level = cap.default_level or (cap.levels[0] if cap.levels else "Medium")
                self._show_thinking = (self.thinking_level != "Off")
                self.settings.config.setdefault("generation", {})["reasoning_level"] = self.thinking_level
            else:
                self._show_thinking = False
                self.thinking_level = "Off"
                self.settings.config.setdefault("generation", {})["reasoning_level"] = "Off"
        except Exception:
            self._show_thinking = False
            self.thinking_level = "Off"
            self.settings.config.setdefault("generation", {})["reasoning_level"] = "Off"

    def _tick_meta_spinner(self) -> None:
        if self.is_generating:
            self._meta_spinner_idx = (self._meta_spinner_idx + 1) % len(self.SPINNER_FRAMES)
            self._update_meta_bars()
        elif self._meta_spinner_idx != 0:
            self._meta_spinner_idx = 0
            self._update_meta_bars()

    def _get_meta_left_text(self) -> str:
        disp = clean_model_name(self.current_model_id)
        is_thinking = False
        if self.is_generating and getattr(self, "_current_assistant_card", None):
            tb = getattr(self._current_assistant_card, "thinking_block", None)
            if tb and not tb.is_finished:
                is_thinking = True

        if is_thinking:
            icon = ["⌬", "✻"][self._meta_spinner_idx % 2]
            return f"[bold white]{icon}[/]  [bold white]{disp}[/] [dim white](thinking...)[/]"
        elif self.is_generating:
            icon = self.SPINNER_FRAMES[self._meta_spinner_idx]
            return f"[b #58a6ff]{icon}[/]  [bold white]{disp}[/] [dim #58a6ff](generating...)[/]"
        else:
            return f"[b #58a6ff]⌬[/]  [bold white]{disp}[/] [dim]({self.current_provider})[/dim]"

    def _get_meta_mode_text(self) -> str:
        color = "#7ee787" if self.agent_mode == "auto" else ("#58a6ff" if self.agent_mode == "plan" else "#d29922")
        return f"[dim]mode:[/] [b {color}]{self.agent_mode}[/]"

    def _get_meta_thinking_text(self) -> str:
        if not self._show_thinking:
            return ""
        color = "#8b949e" if self.thinking_level == "Off" else "#58a6ff"
        return f"[dim]thinking:[/] [b {color}]{self.thinking_level}[/]"

    def _update_meta_bars(self) -> None:
        left_markup = self._get_meta_left_text()
        mode_markup = self._get_meta_mode_text()
        thinking_markup = self._get_meta_thinking_text()

        try:
            self.query_one("#input-meta-left", Static).update(left_markup)
            self.query_one("#input-meta-mode", Static).update(mode_markup)
            think_w = self.query_one("#input-meta-thinking", Static)
            if self._show_thinking:
                think_w.styles.display = "block"
                think_w.update(thinking_markup)
            else:
                think_w.styles.display = "none"
                think_w.update("")
        except Exception:
            pass

        try:
            rel_work = os.path.basename(self.workdir) or self.workdir
            self.query_one("#hero-meta-left", Static).update(left_markup)
            self.query_one("#hero-meta-mode", Static).update(mode_markup)
            hero_think = self.query_one("#hero-meta-thinking", Static)
            if self._show_thinking:
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
        self.notify(f"Mode set to: {self.agent_mode.upper()}", timeout=2.0)

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

    def _open_diff_window(self) -> None:
        try:
            diff_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "editor", "diff_app.py")
            self._spawn_terminal_window(diff_py, "CMDAI CODE Diff", self.workdir)
        except Exception as e:
            self.notify(f"Failed to launch diff: {e}", severity="error")

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
                self.notify("Changes approved.", timeout=2.5)
        elif msg.action == "reject":
            if bar:
                bar.hide_request()
            if getattr(self, "_pending_approval_evt", None):
                self._pending_approval_res[0] = "No, skip"
                self._pending_approval_evt.set()
            elif getattr(self, "_post_turn_approval_active", False):
                self._post_turn_approval_active = False
                self._cmd_undo()
                self._last_modified_files.clear()
                self.notify("Changes rejected and reverted.", severity="warning", timeout=2.5)
        elif msg.action == "diff":
            self._open_diff_window()

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

    def _handle_agent_ask(
        self,
        question: str,
        options: List[str],
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Called from agent runner thread in Code mode or on ask tool."""
        evt = threading.Event()
        res: List[str] = [options[0] if options else "Yes, proceed"]

        self._pending_approval_evt = evt
        self._pending_approval_res = res
        self._pending_approval_details = details

        def _show_ui():
            try:
                bar = self.query_one("#approval-bar", ApprovalBar)
                if details:
                    bar.show_request(details)
                else:
                    bar.show_request({
                        "action": "ask",
                        "path": question,
                        "diff_info": " | ".join(options),
                        "file_count": 1,
                    })
                self._scroll_chat_to_end_if_enabled()
            except Exception:
                pass

        self.call_from_thread(_show_ui)

        while not evt.is_set():
            if self.abort_requested:
                res[0] = "No, skip"
                break
            evt.wait(timeout=0.1)

        def _cleanup_ui():
            try:
                bar = self.query_one("#approval-bar", ApprovalBar)
                bar.hide_request()
            except Exception:
                pass
            try:
                diff_box = self.query_one("#approval-diff-preview", ApprovalDiffPreview)
                diff_box.hide_diff()
            except Exception:
                pass
            try:
                inp = self.query_one("#prompt-input", AutoExpandingInput)
                inp.styles.display = "block"
                inp.focus()
            except Exception:
                pass

        self.call_from_thread(_cleanup_ui)
        self._pending_approval_evt = None
        self._pending_approval_res = None
        self._pending_approval_details = None

        return res[0]

    def cycle_thinking(self) -> None:
        cap = get_model_capability(self.current_model_id, self.current_provider)
        if not cap or not cap.thinking or not cap.levels:
            self.thinking_level = "Off"
            self._show_thinking = False
            self.settings.config.setdefault("generation", {})["reasoning_level"] = "Off"
            self.settings.save_config()
            self._update_meta_bars()
            self.notify(f"Thinking is not supported for {self.current_model_id or 'this model'}", severity="warning", timeout=3.0)
            return
        levels = cap.levels
        curr_idx = levels.index(self.thinking_level) if self.thinking_level in levels else 0
        self.thinking_level = levels[(curr_idx + 1) % len(levels)]
        self._show_thinking = (self.thinking_level != "Off")
        self.settings.config.setdefault("generation", {})["reasoning_level"] = self.thinking_level
        self.settings.save_config()
        self._update_meta_bars()
        self.notify(f"Thinking set to: {self.thinking_level}", timeout=2.0)

    @on(events.Click, "#input-meta-mode")
    @on(events.Click, "#hero-meta-mode")
    def on_mode_clicked(self, event: events.Click) -> None:
        self.cycle_mode()

    @on(events.Click, "#input-meta-thinking")
    @on(events.Click, "#hero-meta-thinking")
    def on_thinking_clicked(self, event: events.Click) -> None:
        self.cycle_thinking()

    def _scroll_chat_to_end_if_enabled(self, force: bool = False) -> None:
        try:
            cs = self.query_one("#chat-scroll", VerticalScroll)
            if force:
                self.auto_scroll_enabled = True
                self._last_user_scroll_time = 0.0
                cs.scroll_end(animate=False)
                return

            if not getattr(self, "auto_scroll_enabled", True):
                return

            if time.time() - getattr(self, "_last_user_scroll_time", 0.0) < 2.0:
                return

            if getattr(cs, "is_vertical_scrollbar_grabbed", False):
                self.auto_scroll_enabled = False
                return

            if cs.max_scroll_y > 0 and cs.scroll_y < (cs.max_scroll_y - 2):
                self.auto_scroll_enabled = False
                return

            cs.scroll_end(animate=False)
        except Exception:
            pass

    def action_scroll_chat_up(self) -> None:
        self.auto_scroll_enabled = False
        self._last_user_scroll_time = time.time()
        try:
            self.query_one("#chat-scroll", VerticalScroll).scroll_page_up(animate=False)
        except Exception:
            pass

    def action_scroll_chat_down(self) -> None:
        try:
            cs = self.query_one("#chat-scroll", VerticalScroll)
            cs.scroll_page_down(animate=False)
            if cs.scroll_y >= (cs.max_scroll_y - 2) or getattr(cs, "is_vertical_scroll_end", False):
                self.auto_scroll_enabled = True
        except Exception:
            pass

    def action_scroll_chat_line_up(self) -> None:
        self.auto_scroll_enabled = False
        self._last_user_scroll_time = time.time()
        try:
            self.query_one("#chat-scroll", VerticalScroll).scroll_relative(y=-2, animate=False)
        except Exception:
            pass

    def action_scroll_chat_line_down(self) -> None:
        try:
            cs = self.query_one("#chat-scroll", VerticalScroll)
            cs.scroll_relative(y=2, animate=False)
            if cs.scroll_y >= (cs.max_scroll_y - 2) or getattr(cs, "is_vertical_scroll_end", False):
                self.auto_scroll_enabled = True
        except Exception:
            pass

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.auto_scroll_enabled = False
        self._last_user_scroll_time = time.time()
        try:
            self.query_one("#chat-scroll", VerticalScroll).scroll_relative(y=-3, animate=False)
        except Exception:
            pass

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        try:
            cs = self.query_one("#chat-scroll", VerticalScroll)
            cs.scroll_relative(y=3, animate=False)
            if cs.scroll_y >= (cs.max_scroll_y - 2) or getattr(cs, "is_vertical_scroll_end", False):
                self.auto_scroll_enabled = True
        except Exception:
            pass

    def action_toggle_tools(self) -> None:
        """Toggle tool blocks expansion/collapse via Ctrl+T."""
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

    def action_quit_app(self) -> None:
        try:
            sys.stdout.write("\x1b]111\x07")
            sys.stdout.flush()
        except Exception:
            pass
        if self.http_server:
            self.http_server.stop()
        self.exit()

    def action_handle_interrupt(self) -> None:
        """Handles Ctrl+C safely without crashing the app:
        - If generating: safely cancel generation.
        - If text is selected in focused widget: copy selection to clipboard.
        - If any ToolBlock is expanded: copy expanded tool details/diff/code to clipboard.
        - If any ThinkingBlock is expanded: copy thinking to clipboard.
        - If a modal is open: dismiss or copy its content.
        - If prompt input has text: copy prompt text to clipboard (preserving input).
        - If prompt is empty: copy latest assistant message to clipboard.
        """
        try:
            if self.is_generating:
                self.abort_requested = True
                if self._current_assistant_card:
                    self.call_from_thread(self._current_assistant_card.abort)
                self.notify("Przerwano generowanie (Ctrl+C)", title="Cancel", timeout=2.0)
                return

            try:
                focused = getattr(self, "focused", None)
                sel_text = getattr(focused, "selected_text", "") if focused else ""
                if sel_text:
                    if copy_text_to_clipboard(sel_text):
                        self.notify(f"Skopiowano zaznaczenie do schowka ({len(sel_text)} zn.)", title="Clipboard", timeout=2.0)
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
                        self.notify(f"Skopiowano [{target_tool.tool_name}] do schowka ({len(content_to_copy)} zn.)", title="Clipboard", timeout=2.0)
                        return
            except Exception:
                pass

            try:
                expanded_thinking = [tb for tb in self.query(ThinkingBlock) if getattr(tb, "is_expanded", False)]
                if expanded_thinking:
                    content_to_copy = getattr(expanded_thinking[-1], "thinking_text", "")
                    if content_to_copy:
                        copy_text_to_clipboard(content_to_copy)
                        self.notify(f"Skopiowano myślenie do schowka ({len(content_to_copy)} zn.)", title="Clipboard", timeout=2.0)
                        return
            except Exception:
                pass

            try:
                if len(self.screen_stack) > 1:
                    top = self.screen_stack[-1]
                    content = getattr(top, "diff_text", None) or getattr(top, "content", None)
                    if content:
                        copy_text_to_clipboard(str(content))
                        self.notify("Skopiowano zawartość okna do schowka", title="Clipboard", timeout=2.0)
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
                    self.notify(f"Skopiowano tekst wpisu do schowka ({len(inp.text)} zn.)", title="Clipboard", timeout=2.0)
                    return
            except Exception:
                pass

            try:
                assistant_msgs = [m["content"] for m in self.messages if m.get("role") == "assistant" and m.get("content")]
                if assistant_msgs:
                    copy_text_to_clipboard(assistant_msgs[-1])
                    self.notify("Skopiowano ostatnią odpowiedź do schowka", title="Clipboard", timeout=2.0)
                else:
                    self.notify("Wpisz /quit aby wyjść z aplikacji", timeout=2.0)
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

        if self.is_generating:
            now = time.time()
            if now - self._last_escape_time < 1.5:
                self.abort_requested = True
                self.is_generating = False
                self._last_escape_time = 0.0
                if self._current_assistant_card:
                    self._current_assistant_card.abort()
                self._update_meta_bars()
                self._hide_hints()
                self.notify("Generation cancelled.", severity="warning", timeout=2.0)
                try:
                    self.set_focus(self.query_one("#prompt-input", AutoExpandingInput))
                except Exception:
                    pass
            else:
                self._last_escape_time = now
                self.notify("Press Esc again to cancel generation", timeout=1.5)
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

    def _switch_to_chat_view(self) -> None:
        """Alias for backwards compatibility."""
        self._switch_to_chat()

    def _add_msg(self, css_class: str, text: str) -> Static:
        container = self.query_one("#chat-scroll", VerticalScroll)
        w = Static(text, classes=css_class)
        container.mount(w)
        container.scroll_end(animate=False)
        return w


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
            txt = event.text_area.text
            stripped = txt.strip().strip("'\"")
            if (os.path.isabs(stripped) or (self.workdir and os.path.exists(os.path.join(self.workdir, stripped)))) and os.path.isfile(os.path.abspath(stripped if os.path.isabs(stripped) else os.path.join(self.workdir, stripped))):
                self._add_file_to_context(stripped)

            self._refresh_command_hints(event.text_area)

    def _refresh_command_hints(self, inp: AutoExpandingInput) -> None:
        self._hint_input_id = inp.id
        val = inp.text.strip()
        if val.startswith("/"):
            v = val.lower()
            matches = []
            for c, _desc in COMMANDS_DOC.items():
                cl = c.lower()
                if cl.startswith(v) or (len(v) > 1 and v[1:] in cl[1:]):
                    matches.append(c)
            self._hint_matches = matches
        else:
            self._hint_matches = []
        self._hint_sel = 0 if self._hint_matches else -1
        self._hint_top = 0

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

    def _hint_complete(self) -> None:
        if not self._hint_matches:
            return
        sel = self._hint_sel if self._hint_sel >= 0 else 0
        cmd = self._hint_matches[sel]
        try:
            inp = self.query_one(f"#{self._hint_input_id}", AutoExpandingInput)
            inp.text = cmd + " "
            inp._update_auto_height()
        except Exception:
            pass

    def _hint_execute(self) -> None:
        if not self._hint_matches or not (0 <= self._hint_sel < len(self._hint_matches)):
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


    def _handle_submit(self, prompt: str, from_hero: bool = False) -> None:
        prompt = prompt.strip()
        if not prompt:
            return

        clean_p = prompt.strip().strip('"').strip("'")
        full_p = clean_p if os.path.isabs(clean_p) else os.path.join(self.workdir, clean_p)
        if "\n" not in clean_p and len(clean_p) < 300 and os.path.isfile(full_p):
            ext = os.path.splitext(clean_p)[1].lower()
            if ext in (".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".c", ".cpp", ".h", ".rs", ".go", ".java", ".sh", ".bat"):
                self._add_file_to_context(clean_p)
                return

        if prompt.startswith("!") or prompt.startswith("$ ") or prompt.startswith("/cmd ") or prompt.startswith("/run "):
            if prompt.startswith("!"):
                cmd = prompt[1:].strip()
            elif prompt.startswith("$ "):
                cmd = prompt[2:].strip()
            elif prompt.startswith("/cmd "):
                cmd = prompt[5:].strip()
            else:
                cmd = prompt[5:].strip()
            self._hide_hints()
            self.execute_terminal_command(cmd)
            return

        if prompt.startswith("/"):
            self._hide_hints()
            cmd = prompt.split(maxsplit=1)[0].lower()
            modal_cmds = (
                "/models", "/system", "/sessions", "/stats", "/modelinfo",
                "/settings", "/quit", "/new", "/params", "/help", "/loader",
                "/clear", "/reset", "/exit", "/tools", "/status",
                "/diff", "/changes", "/commit", "/ci", "/plan", "/tasks",
                "/context", "/ctx", "/export", "/add", "/mode", "/thinking",
                "/cd"
            )
            if cmd in modal_cmds:
                pass
            elif not self.in_chat_mode:
                self._switch_to_chat()
            if self._dispatch_command(prompt):
                return

        if self.is_generating:
            return

        self._hide_hints()
        if from_hero or not self.in_chat_mode:
            self._switch_to_chat()

        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            user_msg = UserMessageCard(prompt)
            chat_scroll.mount(user_msg)
            chat_scroll.scroll_end(animate=False)
        except Exception:
            pass

        in_toks = max(1, len(prompt.split()) * 4 // 3)
        self.stats["tokens_in"] += in_toks

        self.messages.append({"role": "user", "content": prompt})
        try:
            self.session_manager.save_session(
                session_id=self.current_session_id,
                messages=self.messages,
                model_id=self.current_model_id,
                stats=self.stats,
            )
        except Exception:
            pass
        self.start_generation(prompt)

    def handle_submit(self, text: str) -> None:
        """Compatibility wrapper."""
        self._handle_submit(text, from_hero=(not self.in_chat_mode))

    def _dispatch_command(self, raw: str) -> bool:
        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "/help": self._cmd_help,
            "/mode": self._cmd_mode,
            "/thinking": self._cmd_thinking,
            "/new": self._cmd_new,
            "/clear": self._cmd_new,
            "/reset": self._cmd_new,
                        "/branch": self._cmd_branch,
            "/branches": self._cmd_branch,
            "/fork": self._cmd_fork,
            "/git": self._cmd_git,
            "/checkpoints": self._cmd_checkpoints,
            "/checkpoint": self._cmd_checkpoints,
            "/diff": self._cmd_diff,
            "/changes": self._cmd_diff,
            "/editor": self._cmd_editor,
            "/undo": self._cmd_undo,
            "/rollback": self._cmd_undo,
            "/test": self._cmd_test,
            "/commit": self._cmd_commit,
            "/ci": self._cmd_commit,
            "/review": self._cmd_review,
            "/plan": self._cmd_plan,
            "/tasks": self._cmd_plan,
            "/debug": self._cmd_debug,
            "/bugs": self._cmd_debug,
            "/index": self._cmd_index,
            "/context": self._cmd_context,
            "/ctx": self._cmd_context,
            "/export": self._cmd_export,
            "/add": self._cmd_add,
            "/drop": self._cmd_drop,
            "/cd": self._cmd_cd,
            "/models": self._cmd_models,
            "/agent": self._cmd_agent,
            "/tools": self._cmd_tools,
            "/status": self._cmd_status,
            "/init": self._cmd_init,
            "/cmd": self._cmd_run,
            "/run": self._cmd_run,
            "/sessions": self._cmd_sessions,
            "/system": self._cmd_system,
            "/params": self._cmd_params,
            "/settings": self._cmd_settings,
            "/summarize": self._cmd_summarize,
            "/compact": self._cmd_summarize,
            "/stats": self._cmd_stats,
            "/modelinfo": self._cmd_modelinfo,
            "/loader": self._cmd_loader,
            "/api-key": self._cmd_apikey,
            "/key": self._cmd_apikey,
            "/quit": self._cmd_quit,
            "/exit": self._cmd_quit,
        }
        handler = handlers.get(cmd)
        if handler is None:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"Unknown command: [b]{cmd}[/] — type [b]/help[/] for the list")
            return True
        handler(arg)
        return True

    def _cmd_apikey(self, arg: str = "") -> None:
        provider = arg.strip().lower() or self.current_provider
        self.push_screen(ApiKeyModal(provider), lambda _: None)

    def _cmd_quit(self, arg: str = "") -> None:
        self.action_quit_app()

    def _cmd_help(self, arg: str = "") -> None:
        self.push_screen(HelpModal(), self._on_help_command)

    def _cmd_mode(self, arg: str = "") -> None:
        arg = (arg or "").strip().lower()
        if not arg:
            self.cycle_mode()
            return
        if arg == "ask":
            arg = "code"
        if arg in ("auto", "plan", "code"):
            self.agent_mode = arg
            self.settings.config.setdefault("agent", {})["mode"] = self.agent_mode
            self.settings.save_config()
            self.agent_ctx.mode = self.agent_mode
            self._update_meta_bars()
            self.notify(f"Mode set to: {self.agent_mode.upper()}", timeout=2.0)
        else:
            self.notify("Valid modes: auto, plan, code", severity="warning", timeout=3.0)

    def _cmd_thinking(self, arg: str = "") -> None:
        cap = get_model_capability(self.current_model_id, self.current_provider)
        if not cap or not cap.thinking or not cap.levels:
            self.thinking_level = "Off"
            self._show_thinking = False
            self.settings.config.setdefault("generation", {})["reasoning_level"] = "Off"
            self.settings.save_config()
            self._update_meta_bars()
            self.notify(f"Thinking is not supported for {self.current_model_id} according to its template", severity="warning", timeout=3.0)
            return
        arg = (arg or "").strip()
        if not arg:
            self.cycle_thinking()
            return
        matched = next((lvl for lvl in cap.levels if lvl.lower() == arg.lower()), None)
        if matched:
            self.thinking_level = matched
            self._show_thinking = (self.thinking_level != "Off")
            self.settings.config.setdefault("generation", {})["reasoning_level"] = self.thinking_level
            self.settings.save_config()
            self._update_meta_bars()
            self.notify(f"Thinking set to: {self.thinking_level}", timeout=2.0)
        else:
            self.notify(f"Valid thinking levels: {', '.join(cap.levels)}", severity="warning", timeout=3.0)

    def _on_help_command(self, cmd: Optional[str]) -> None:
        if not cmd:
            return
        modal_cmds = ("/models", "/system", "/sessions", "/stats", "/modelinfo",
                      "/quit", "/new", "/params", "/help", "/loader", "/settings",
                      "/tools", "/status", "/diff", "/changes", "/commit", "/ci",
                      "/plan", "/tasks", "/context", "/ctx", "/export", "/add", "/mode", "/thinking")
        if not self.in_chat_mode and cmd not in modal_cmds:
            self._switch_to_chat()
        self._dispatch_command(cmd)

    def _cmd_new(self, arg: str = "") -> None:
        if self.messages:
            self.session_manager.save_session(
                session_id=self.current_session_id,
                messages=self.messages,
                model_id=self.current_model_id,
                stats=self.stats,
            )
        self.messages.clear()
        self.current_session_id = f"session_{int(time.time())}"
        self.stats = {
            "tokens_in": 0,
            "tokens_out": 0,
            "tok_per_sec": 0.0,
            "tools_run": 0,
        }
        self._current_assistant_card = None
        try:
            self.query_one("#chat-scroll", VerticalScroll).remove_children()
        except Exception:
            pass
        self._switch_to_hero()

    def reset_session(self) -> None:
        self._cmd_new()

    def _cmd_models(self, arg: str = "") -> None:
        self.push_screen(
            ModelSelectModal(self.current_provider, self.current_model_id),
            self._on_model_selected,
        )

    def _cmd_agent(self, arg: str = "") -> None:
        agent_enabled = self.settings.config.get("agent", {}).get("enabled", True)
        new_val = not agent_enabled
        self.settings.config.setdefault("agent", {})["enabled"] = new_val
        self.settings.save_config()
        if not self.in_chat_mode:
            self._switch_to_chat()
        self._add_msg("system-msg", f"[b #58a6ff]/agent {'ON' if new_val else 'OFF'}[/]")

    def _cmd_sessions(self, arg: str = "") -> None:
        if self.is_generating:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", "[yellow]Generation in progress — stop it first (Esc).[/]")
            return
        self.push_screen(SessionsModal(workdir=self.workdir), self._on_session_selected)

    def _cmd_system(self, arg: str = "") -> None:
        self.push_screen(SystemPromptModal(), self._on_prompt_selected)

    def _cmd_params(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if not arg:
            self.push_screen(ParamsModal(self.current_model_id, self.current_provider), lambda _: None)
            return
        parts = arg.split(maxsplit=1)
        name = parts[0].lower()
        param_specs = {
            "temperature": ("temperature", float, 0.0, 2.0),
            "temp": ("temperature", float, 0.0, 2.0),
            "top_p": ("top_p", float, 0.0, 1.0),
            "top-p": ("top_p", float, 0.0, 1.0),
            "max_tokens": ("max_tokens", int, 1, 32768),
            "tokens": ("max_tokens", int, 1, 32768),
            "repetition_penalty": ("repetition_penalty", float, 1.0, 2.0),
            "rep": ("repetition_penalty", float, 1.0, 2.0),
        }
        spec = param_specs.get(name)
        if spec is None:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"Unknown parameter: [b]{name}[/] — one of: temperature, top_p, max_tokens, repetition_penalty")
            return
        key, cast, lo, hi = spec
        gen = self.settings.config.setdefault("generation", {})
        if len(parts) < 2 or not parts[1].strip():
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"[b #58a6ff]•[/] {key}: {gen.get(key, 0.6)}")
            return
        val_str = parts[1].strip()
        try:
            val = cast(val_str)
        except ValueError:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"Invalid value for {key}: [b]{val_str}[/]")
            return
        if val < lo or val > hi:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"{key} must be between {lo} and {hi}")
            return
        old = gen.get(key, 0.6)
        gen[key] = val
        self.settings.save_config()
        if not self.in_chat_mode:
            self._switch_to_chat()
        self._add_msg("system-msg", f"[b #58a6ff]•[/] {key}: {old} → {val}")

    def _cmd_settings(self, arg: str = "") -> None:
        self.push_screen(SettingsModal(), lambda _: None)

    def _cmd_summarize(self, arg: str = "") -> None:
        if self.is_generating:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", "[yellow]Generation in progress — stop it first (Esc).[/]")
            return
        if not self.messages:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", "[yellow]Nothing to summarize.[/]")
            return
        if not self.in_chat_mode:
            self._switch_to_chat()
        self.trigger_summarize()

    def _cmd_stats(self, arg: str = "") -> None:
        self.push_screen(StatsModal(self.stats), lambda _: None)

    def _cmd_modelinfo(self, arg: str = "") -> None:
        self.push_screen(InfoModal("Model info", self._build_modelinfo_body()))

    def _cmd_loader(self, arg: str = "") -> None:
        arg = (arg or "").strip().lower()
        if arg in ("cpu", "cuda", "vulkan", "ryzen", "npu"):
            self.settings.config["active_loader"] = arg
            self.settings.save_config()
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"⚙️ Active loader set to: [b #58a6ff]{arg}[/]")
        else:
            self.push_screen(LoaderModal(), self._on_loader_selected)

    def _build_modelinfo_body(self) -> str:
        import psutil
        from ..core.capabilities import BUDGET_MAP, get_model_capability
        from ..core.resource_limits import get_dynamic_context_limit

        vm = psutil.virtual_memory()
        total_ram_gb = vm.total / (1024**3)
        avail_ram_gb = vm.available / (1024**3)
        cache_budget_70 = max(0.5, (avail_ram_gb * 0.70))
        tokens_70_ram = int(cache_budget_70 * 128_000)

        dyn_limit = get_dynamic_context_limit(self.current_model_id, self.current_provider)
        cap = get_model_capability(self.current_model_id, provider_id=self.current_provider)
        gen = self.settings.config.get("generation", {})
        active_reasoning = gen.get("reasoning_level", "Medium")

        lines = [
            f"[dim]name[/]         [b white]{self.current_model_id}[/]",
            f"[dim]provider[/]     {self.current_provider}",
            f"[dim]loader[/]       {self.settings.config.get('active_loader', 'cpu')}",
            f"[dim]code agent[/]   {'[b #3fb950]ON[/]' if self.settings.config.get('agent', {}).get('enabled', True) else '[dim]OFF[/]'}",
        ]

        gguf = getattr(self, "gguf_loader", None)
        if gguf and getattr(gguf, "current_model_path", None):
            lines += [
                f"[dim]engine[/]       GGUF Engine ({self.settings.config.get('active_loader', 'cpu')})",
                f"[dim]file path[/]    {gguf.current_model_path}",
            ]

        lines.append("\n[bold white]Thinking & Reasoning Support:[/]")
        if cap and cap.thinking:
            budget = BUDGET_MAP.get(active_reasoning, 8192)
            lines += [
                f"  [b #58a6ff]•[/] [dim]thinking[/]     [b #3fb950]Supported[/]",
                f"  [b #58a6ff]•[/] [dim]levels[/]       {', '.join(cap.levels)}",
                f"  [b #58a6ff]•[/] [dim]active level[/] [b white]{active_reasoning}[/]",
                f"  [b #58a6ff]•[/] [dim]budget[/]       [b #bc8cff]{budget:,} tokens[/]",
                f"  [b #58a6ff]•[/] [dim]param style[/]  {cap.param_style}",
            ]
        else:
            lines.append("  [dim]• thinking:     Not supported for this model[/dim]")

        lines.append("\n[bold white]Context & Hardware Safety (70% RAM Rule):[/]")
        lines += [
            f"  [b #58a6ff]•[/] [dim]safe context[/] [b #58a6ff]{dyn_limit:,} tokens[/]",
            f"  [b #58a6ff]•[/] [dim]system ram[/]   {total_ram_gb:.1f} GB total · {avail_ram_gb:.1f} GB free",
            f"  [b #58a6ff]•[/] [dim]70% cache[/]    {cache_budget_70:.1f} GB safe headroom (~{tokens_70_ram:,} tokens max)",
        ]

        lines.append("\n[bold white]Generation Parameters:[/]")
        lines.append(
            f"  [b #58a6ff]•[/] temp {gen.get('temperature', 0.6)} · "
            f"top_p {gen.get('top_p', 0.95)} · "
            f"max_tokens {gen.get('max_tokens', 2048)}"
        )
        return "\n".join(lines)

    def _cmd_tools(self, arg: str = "") -> None:
        self.push_screen(InfoModal("Agent Tools", self._build_tools_body()))

    def _build_tools_body(self) -> str:
        lines = [
            "[b #58a6ff]CMDAI CODE XML Agent Tools[/]\n",
            "[b white]• read[/]       Read full file or line ranges",
            "  [dim]<read><path>src/main.py</path><lines>1-50</lines></read>[/]",
            "[b white]• edit[/]       Accurate in-file replacement with diff preview",
            "  [dim]<edit><path>file.py</path><old>old_code</old><new>new_code</new></edit>[/]",
            "[b white]• write[/]      Create new file or overwrite file with content",
            "  [dim]<write><path>src/new.py</path><content>...</content></write>[/]",
            "[b white]• ls[/]         List files and directories in workspace directory",
            "  [dim]<ls><path>.</path></ls>[/]",
            "[b white]• glob[/]       Find files matching pattern (e.g. **/*.py)",
            "  [dim]<glob><pattern>**/*.py</pattern></glob>[/]",
            "[b white]• search[/]     Grep regex search inside files",
            "  [dim]<search><pattern>def test</pattern><path>tests</path></search>[/]",
            "[b white]• command[/]    Execute shell terminal command in workspace",
            "  [dim]<command>python -m pytest</command>[/]",
            "[b white]• fetch[/]      Fetch web content via HTTP GET",
            "  [dim]<fetch><url>https://api.github.com</url></fetch>[/]",
            "[b white]• todo[/]       Manage project task checklist (add/list/done/remove)",
            "  [dim]<todo><action>add</action><task>Implement feature</task></todo>[/]",
        ]
        return "\n".join(lines)

    def _cmd_status(self, arg: str = "") -> None:
        self.push_screen(InfoModal("Workspace Status", self._build_status_body()))

    def _build_status_body(self) -> str:
        agent_on = self.settings.config.get("agent", {}).get("enabled", True)
        lines = [
            f"[dim]Workspace:[/]    [b white]{self.workdir}[/]",
            f"[dim]Active Model:[/] [b #58a6ff]{self.current_model_id}[/]",
            f"[dim]Provider:[/]     {self.current_provider}",
            f"[dim]Loader:[/]       {self.settings.config.get('active_loader', 'cpu')}",
            f"[dim]Code Agent:[/]   {'[b #3fb950]ENABLED[/]' if agent_on else '[dim]DISABLED[/]'}",
            f"[dim]Current View:[/] {'Chat view' if self.in_chat_mode else 'Hero view'}",
            f"[dim]History:[/]      {len(self.messages)} turns",
            f"[dim]Generating:[/]   {'[b #f0883e]IN PROGRESS[/]' if self.is_generating else '[dim]IDLE[/]'}",
        ]
        if hasattr(self, "stats"):
            lines.append(f"[dim]Total Tokens:[/] {self.stats.get('total_tokens', 0)}")
            lines.append(f"[dim]Tool Calls:[/]   {self.stats.get('tool_calls', 0)}")
        return "\n".join(lines)

    def _cmd_init(self, arg: str = "") -> None:
        if self.is_generating:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", "[yellow]Generation in progress — wait or press Esc first.[/]")
            return
        if not self.in_chat_mode:
            self._switch_to_chat()
        target = f" ({arg})" if arg else ""
        prompt = f"Inspect project structure{target} and initialize CMDAIPLAN.md with architecture overview, file hierarchy, and next steps."
        self._handle_submit(prompt, from_hero=False)

    def _cmd_run(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if arg:
            self.execute_terminal_command(arg)
        else:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", "Usage: [b]/cmd <command>[/] (e.g. [dim]/cmd dir[/] or [dim]/cmd git status[/])")

    def _spawn_terminal_window(self, script_path: str, title: str, *args: str) -> None:
        """Spawn an independent terminal app in a new dedicated console window."""
        import subprocess
        clean_args = [os.path.normpath(str(a)).rstrip("\\") for a in args if str(a).strip()]
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        subprocess.Popen(
            [sys.executable, script_path, *clean_args],
            creationflags=flags,
            cwd=self.workdir,
        )

    def _cmd_checkpoints(self, arg: str = "") -> None:
        from .modals.checkpoints import CheckpointsModal
        self.push_screen(CheckpointsModal(workdir=self.workdir))

    def _cmd_git(self, arg: str = "") -> None:
        from .modals.git_modal import GitBranchesModal
        self.push_screen(GitBranchesModal(workdir=self.workdir))

    def _cmd_branch(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if not arg:
            self._cmd_git()
            return
        import subprocess
        try:
            res = subprocess.run(["git", "checkout", "-b", arg], cwd=self.workdir, capture_output=True, text=True)
            if res.returncode == 0:
                self.notify(f"Created and checked out branch: {arg}", severity="information")
            else:
                self.notify(f"Git: {res.stderr.strip() or res.stdout.strip()}", severity="warning")
        except Exception as e:
            self.notify(f"Failed to create branch: {e}", severity="error")

    def _cmd_fork(self, arg: str = "") -> None:
        self._cmd_branch(arg)

    def _cmd_diff(self, arg: str = "") -> None:
        self._open_diff_window()

    def _on_diff_closed(self, action: Any) -> None:
        if action == "commit":
            self.push_screen(CommitModal(self.workdir), self._on_commit_done)

    def _cmd_editor(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        target_path = os.path.join(self.workdir, arg) if arg else self.workdir
        try:
            editor_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "editor", "editor_app.py")
            self._spawn_terminal_window(editor_py, "CMDAI CODE Editor", target_path)
        except Exception as e:
            self.notify(f"Failed to launch editor: {e}", severity="error")

    def _cmd_undo(self, arg: str = "") -> None:
        if self.is_generating:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", "[yellow]Generation in progress — stop it first (Esc).[/]")
            return

        reverted_files = []
        if hasattr(self, "_last_modified_files") and self._last_modified_files:
            import subprocess
            for f in set(self._last_modified_files):
                full_p = os.path.join(self.workdir, f) if not os.path.isabs(f) else f
                if os.path.exists(full_p):
                    rel = os.path.relpath(full_p, self.workdir)
                    res = subprocess.run(["git", "checkout", "--", rel], cwd=self.workdir, capture_output=True)
                    if res.returncode == 0:
                        reverted_files.append(rel)
            self._last_modified_files.clear()

        if not self.in_chat_mode:
            self._switch_to_chat()
        if reverted_files:
            self._add_msg("system-msg", f"[b #f0883e]< Reverted changes:[/] {', '.join(reverted_files)}")
        else:
            self._add_msg("system-msg", "[dim]Nothing to revert (no agent file edits recorded in this session).[/dim]")

    def _cmd_test(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if arg:
            test_cmd = arg
        elif os.path.exists(os.path.join(self.workdir, "pytest.ini")) or os.path.exists(os.path.join(self.workdir, "tests")):
            test_cmd = "python -m pytest"
        elif os.path.exists(os.path.join(self.workdir, "package.json")):
            test_cmd = "npm test"
        elif os.path.exists(os.path.join(self.workdir, "Cargo.toml")):
            test_cmd = "cargo test"
        elif os.path.exists(os.path.join(self.workdir, "go.mod")):
            test_cmd = "go test ./..."
        else:
            test_cmd = "python -m unittest discover"

        if not self.in_chat_mode:
            self._switch_to_chat()
        self._add_msg("system-msg", f"[b #58a6ff]> Running test suite:[/] [dim]{test_cmd}[/]")
        self.execute_terminal_command(test_cmd)

    def _cmd_commit(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if arg.lower() == "modal":
            self.push_screen(CommitModal(self.workdir, suggested_msg=arg), self._on_commit_done)
            return

        try:
            diff_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "editor", "diff_app.py")
            self._spawn_terminal_window(diff_py, "CMDAI CODE - Git Commit & Diff", self.workdir)
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", "[b #58a6ff]•[/] Opened CMDAI CODE Diff & Commit in a new terminal window.")
        except Exception as e:
            self.notify(f"Failed to open commit window: {e}", severity="error")

    def _on_commit_done(self, msg: Optional[str]) -> None:
        if msg:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"[b #3fb950]✔ Committed successfully:[/] [white]{msg}[/]")

    def _cmd_review(self, arg: str = "") -> None:
        if self.is_generating:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", "[yellow]Generation in progress — wait or press Esc first.[/]")
            return
        if not self.in_chat_mode:
            self._switch_to_chat()
        target = f" ({arg})" if arg else ""
        prompt = f"Perform a comprehensive code review of recent changes (git diff){target}: check security, potential bugs, performance, and code quality."
        self._handle_submit(prompt, from_hero=False)

    def _cmd_plan(self, arg: str = "") -> None:
        self.push_screen(PlanModal(self.workdir), self._on_plan_closed)

    def _on_plan_closed(self, _: Any) -> None:
        plan_p = os.path.join(self.workdir, "CMDAIPLAN.md")
        tasks = []
        if os.path.exists(plan_p):
            try:
                with open(plan_p, "r", encoding="utf-8", errors="replace") as f:
                    for l in f:
                        ls = l.strip()
                        if ls.startswith("- [ ]") or ls.startswith("* [ ]"):
                            tasks.append({"task": ls[5:].strip(), "status": "pending"})
                        elif ls.startswith("- [x]") or ls.startswith("- [X]") or ls.startswith("* [x]"):
                            tasks.append({"task": ls[5:].strip(), "status": "done"})
            except Exception:
                pass
        self._on_todo_updated(tasks)

    def _cmd_debug(self, arg: str = "") -> None:
        if not self.in_chat_mode:
            self._switch_to_chat()
        from ..agent.verifier import scan_project_bugs
        report = scan_project_bugs(self.workdir)
        scanned = report.get("scanned_files", 0)
        errors = report.get("errors", {})
        if not errors:
            self._add_msg("system-msg", f"[b #3fb950]✔ Debug Syntax Check Passed![/] Scanned [b]{scanned}[/] files with [b #3fb950]0 errors[/].")
        else:
            lines = [f"[b #f85149]⚠️ Syntax Errors Detected[/] ({len(errors)} file(s) with errors out of {scanned} scanned):\n"]
            for fpath, errs in errors.items():
                lines.append(f"[b white]• {fpath}[/]")
                for e in errs:
                    lines.append(f"  [#f85149]{e}[/]")
            self._add_msg("system-msg", "\n".join(lines))

    def _cmd_index(self, arg: str = "") -> None:
        if not self.in_chat_mode:
            self._switch_to_chat()
        try:
            from ..indexer.ast_indexer import ProjectIndexer
            indexer = ProjectIndexer(self.workdir)
            stats = indexer.index_all()
            self._add_msg(
                "system-msg",
                f"[b #58a6ff]⌬ AST Symbol Index Updated[/]: [b white]{stats['indexed_files']}[/] files indexed, "
                f"[b #3fb950]{stats['total_symbols']}[/] symbols saved to SQLite FTS5 database."
            )
        except Exception as e:
            self._add_msg("system-msg", f"[#f85149]Indexing failed:[/] {e}")

    def _cmd_context(self, arg: str = "") -> None:
        max_ctx = get_dynamic_context_limit(self.current_model_id, self.current_provider)
        def _on_context_close(updated_pinned: Optional[Dict[str, str]] = None) -> None:
            if updated_pinned is not None:
                self.pinned_files = updated_pinned
                cnt = len(self.pinned_files)
                self.notify(f"Context updated ({cnt} file{'s' if cnt != 1 else ''} pinned)", timeout=2.5)
        self.push_screen(
            ContextInspectorModal(
                model_id=self.current_model_id,
                system_prompt=self.system_prompt,
                messages=self.messages,
                pinned_files=dict(self.pinned_files),
                max_context=max_ctx,
                provider=self.current_provider,
                workdir=self.workdir,
            ),
            _on_context_close,
        )

    def _cmd_export(self, arg: str = "") -> None:
        target_path = arg.strip() if arg else ""
        if not target_path:
            exports_dir = os.path.join(self.workdir, "exports")
            os.makedirs(exports_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            target_path = os.path.join(exports_dir, f"session_{ts}.html")
        elif not os.path.isabs(target_path):
            target_path = os.path.join(self.workdir, target_path)

        try:
            out_file = export_session_to_html(
                session_id=self.current_session_id or "default",
                messages=self.messages,
                stats=self.stats,
                model_id=self.current_model_id,
                workdir=self.workdir,
                output_path=target_path,
            )
            rel = os.path.relpath(out_file, self.workdir)
            if self.is_running:
                if not self.in_chat_mode:
                    self._switch_to_chat()
                self._add_msg("system-msg", f"[b #3fb950]Session exported successfully:[/] [b white]{rel}[/]")
                self.notify(f"Exported to {rel}", timeout=3.5)
            send_desktop_notification("CMDAI CODE", f"Session exported: {rel}")
        except Exception as e:
            if self.is_running:
                if not self.in_chat_mode:
                    self._switch_to_chat()
                self._add_msg("system-msg", f"[#f85149]Failed to export session:[/] {e}")

    def _cmd_add(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if arg and os.path.isfile(os.path.join(self.workdir, arg)):
            self._add_file_to_context(arg)
        else:
            try:
                target_dir = os.path.normpath(self.workdir)
                if arg and os.path.isdir(os.path.join(self.workdir, arg)):
                    target_dir = os.path.normpath(os.path.join(self.workdir, arg))
                os.startfile(target_dir)
                if not self.in_chat_mode:
                    self._switch_to_chat()
                self._add_msg("system-msg", f"[b #58a6ff]•[/] Opened Windows Explorer in: [b white]{target_dir}[/]")
            except Exception as e:
                self.notify(f"Failed to open Windows Explorer: {e}", severity="error")

    def _on_file_selected_for_add(self, rel_path: Optional[str]) -> None:
        if rel_path:
            self._add_file_to_context(rel_path)

    def _add_file_to_context(self, rel_path: str) -> None:
        full_p = os.path.join(self.workdir, rel_path) if not os.path.isabs(rel_path) else rel_path
        if not os.path.exists(full_p):
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"[#f85149]File not found:[/] {rel_path}")
            return

        try:
            with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            clean_rel = os.path.relpath(full_p, self.workdir).replace("\\", "/")
            self.pinned_files[clean_rel] = content
            lines = len(content.splitlines())
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"[b #3fb950]+ Pinned to context:[/] [b white]{clean_rel}[/] [dim]({lines} lines)[/dim]")
        except Exception as e:
            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"[#f85149]Error reading file:[/] {e}")

    def _cmd_drop(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if not self.in_chat_mode:
            self._switch_to_chat()

        if not self.pinned_files:
            self._add_msg("system-msg", "[dim]No files currently pinned in context.[/dim]")
            return

        if not arg or arg.lower() == "all":
            cnt = len(self.pinned_files)
            self.pinned_files.clear()
            self._add_msg("system-msg", f"[b #f0883e]- Unpinned all {cnt} files from context.[/]")
            return

        matched = [k for k in self.pinned_files if arg.lower() in k.lower()]
        if matched:
            for k in matched:
                del self.pinned_files[k]
            self._add_msg("system-msg", f"[b #f0883e]- Unpinned from context:[/] {', '.join(matched)}")
        else:
            self._add_msg("system-msg", f"[dim]File not pinned:[/] {arg} (pinned: {', '.join(self.pinned_files.keys())})")

    def _cmd_cd(self, arg: str = "") -> None:
        target = (arg or "").strip()
        if not target:
            msg = f"Current directory: [bold #58a6ff]{self.workdir}[/]\nUsage: [dim]/cd <path>[/]"
            if self.in_chat_mode:
                self._add_msg("system-msg", msg)
            self.notify(f"Current directory: {os.path.basename(self.workdir) or self.workdir}")
            return

        if (target.startswith('"') and target.endswith('"')) or (target.startswith("'") and target.endswith("'")):
            target = target[1:-1].strip()

        target = os.path.expanduser(target)
        target = os.path.expandvars(target)

        if not os.path.isabs(target):
            new_path = os.path.abspath(os.path.join(self.workdir, target))
        else:
            new_path = os.path.abspath(target)

        if not os.path.exists(new_path):
            msg = f"[bold #f85149]cd: no such file or directory:[/] {target}"
            if self.in_chat_mode:
                self._add_msg("system-msg", msg)
            self.notify(f"Directory not found: {target}", severity="error")
            return

        if not os.path.isdir(new_path):
            msg = f"[bold #f85149]cd: not a directory:[/] {target}"
            if self.in_chat_mode:
                self._add_msg("system-msg", msg)
            self.notify(f"Not a directory: {target}", severity="error")
            return

        self.workdir = new_path
        try:
            os.chdir(new_path)
        except Exception:
            pass

        self.session_manager = SessionManager(project_dir=self.workdir)

        if getattr(self, "agent_ctx", None):
            self.agent_ctx.workdir = self.workdir

        if hasattr(self, "system_prompt") and "Current working directory:" in self.system_prompt:
            self.system_prompt = re.sub(
                r"Current working directory: [^\n]+",
                lambda _: f"Current working directory: {self.workdir}",
                self.system_prompt,
            )

        self._update_meta_bars()

        if self.in_chat_mode:
            self._add_msg("system-msg", f"Changed directory to: [bold #58a6ff]{self.workdir}[/]")
        self.notify(f"Directory changed: {os.path.basename(self.workdir) or self.workdir}")

    def execute_command(self, cmd_text: str) -> None:
        """Compatibility wrapper for command execution."""
        self._dispatch_command(cmd_text)

    def execute_terminal_command(self, cmd_line: str) -> None:
        cmd_line = cmd_line.strip()
        if not cmd_line or self.is_generating:
            return

        if not self.in_chat_mode:
            self._switch_to_chat()
        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            user_msg = UserMessageCard(f"!{cmd_line}")
            chat_scroll.mount(user_msg)
            card = AssistantTurnCard("Terminal")
            chat_scroll.mount(card)
            chat_scroll.scroll_end(animate=False)
            self._run_terminal_worker(card, cmd_line)
        except Exception:
            pass

    @work(thread=True)
    def _run_terminal_worker(self, card: AssistantTurnCard, cmd_line: str) -> None:
        from ..agent.tools import tool_command
        self.call_from_thread(card.add_tool, "command", cmd_line)
        res = tool_command(self.agent_ctx, cmd_line)
        self.call_from_thread(card.finish_tool, res)
        self.call_from_thread(card.finish, 0.1, 0, 0.0)

    def _on_loader_selected(self, loader: Optional[str]) -> None:
        if loader:
            self.settings.config["active_loader"] = loader
            self.settings.save_config()
            if self.gguf_loader.llm is not None:
                self.gguf_loader.unload()

    def _on_model_selected(self, result: Tuple[str, str]) -> None:
        if result and result[0] and result[1]:
            old_model = self.current_model_id
            self.current_provider, self.current_model_id = result
            self.settings.config["default_provider"] = self.current_provider
            self.settings.config["default_model"] = self.current_model_id
            self.settings.save_config()
            self.update_meta_bars()
            if old_model != self.current_model_id and self.gguf_loader.llm is not None:
                self.gguf_loader.unload()
            self._check_and_warn_model_memory(self.current_provider, self.current_model_id)

    def _check_and_warn_model_memory(self, provider: str, model_id: str) -> None:
        """Inspects model memory demands against host RAM/VRAM and posts an alert card if unsafe."""
        if provider != "local_gguf":
            return
        m_dir = getattr(self.gguf_loader, "models_dir", "models")
        candidates = [
            os.path.join(m_dir, model_id),
            os.path.join(self.workdir, "models", model_id),
            os.path.join("D:/CMDAI CODE/models", model_id),
            os.path.join("E:/CMDAI CODE/models", model_id),
            model_id,
        ]
        found_path = None
        for c in candidates:
            if c and os.path.exists(c) and os.path.isfile(c):
                found_path = c
                break
        if not found_path:
            return

        try:
            mem = estimate_model_memory(found_path)
            req = mem.get("total_required_gb", 0)
            avail = mem.get("system_available_gb", 0)
            total = mem.get("system_total_gb", 0)
            fits = mem.get("fits_comfortably", True)

            if not fits or req > avail:
                warn_msg = (
                    f"[b #f85149]⚠️ OSTRZEŻENIE O PAMIĘCI RAM/VRAM[/]\n"
                    f"Wybrany model: [b white]{model_id}[/] ([dim]{mem['weights_gb']} GB[/])\n"
                    f"Wymagana pamięć: [b #f0883e]~{req} GB[/] (wagi + KV cache)\n"
                    f"Dostępna pamięć RAM w systemie: [b white]{avail} GB[/] / {total} GB\n\n"
                    f"[yellow]Uwaga: Uruchomienie tego modelu może spowodować silne spowolnienie systemu lub zacięcie (thrashing swapu).[/]\n"
                    f"[dim]Zalecenie: Wybierz mniejszą kwantyzację (Q4_0/Q4_K_M) lub mniejszy model (np. Gemma 4 E4B, Qwen 2.5 0.5B).[/]"
                )
                if not self.in_chat_mode:
                    self._switch_to_chat()
                self._add_msg("system-msg", warn_msg)
        except Exception:
            pass


    def _handle_agent_ask(self, question: str, options: List[str]) -> str:
        """Invoked synchronously from worker thread by AgentRunner when agent emits <tool:ask>.
        Displays interactive AskModal in the same single TUI window, waiting for user response.
        """
        evt = threading.Event()
        res = [""]

        def _on_modal_dismiss(val: str) -> None:
            res[0] = val
            evt.set()

        def _open_modal() -> None:
            self.push_screen(AskModal(question, options=options), _on_modal_dismiss)

        self.call_from_thread(_open_modal)
        evt.wait()
        return res[0]

    def trigger_summarize(self, auto: bool = False) -> None:
        """Summarizes conversation history using the active model into a single context handoff turn."""
        if not self.messages:
            return

        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        block = ToolBlock("summarizing", "")
        chat_scroll.mount(block)
        chat_scroll.scroll_end(animate=False)

        self._summarize_worker(block, auto)

    @work(thread=True)
    def _summarize_worker(self, tool_block: ToolBlock, auto: bool = False) -> None:
        ctx_limit = get_dynamic_context_limit(self.current_model_id, self.current_provider)
        payload = build_summarization_payload(
            self.messages,
            last_modified_files=getattr(self, "_last_modified_files", None),
            max_budget_tokens=min(2500, max(1200, ctx_limit // 2)),
        )
        api_key = self.settings.get_api_key(self.current_provider)

        summary_text = ""
        try:
            if self.current_provider == "local_gguf":
                resp, _ = self.gguf_loader.stream_chat(
                    messages=payload,
                    temperature=0.3,
                    max_tokens=1024,
                    model_filename=self.current_model_id,
                )
                summary_text = resp
            else:
                resp, _ = stream_chat_completion(
                    provider_id=self.current_provider,
                    model_id=self.current_model_id,
                    messages=payload,
                    api_key=api_key,
                    generation_params={"temperature": 0.3, "max_tokens": 1024},
                )
                summary_text = resp
        except Exception:
            summary_text = ""

        is_err = not summary_text or any(err_kw in str(summary_text).lower() for err_kw in [
            "error:", "exceed context window", "status code", "rate limit", "token limit", "api error", "400 bad request"
        ])

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
            tool_block.finish({"success": True, "summary": handoff_content.strip()})
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            chat_scroll.scroll_end(animate=False)

        self.call_from_thread(_update_ui)

    def _on_session_selected(self, session_id: str) -> None:
        if not session_id:
            return
        data = self.session_manager.load_session(session_id)
        if data:
            self.reset_session()
            self.current_session_id = session_id
            self.messages = data.get("messages", [])
            self.stats = data.get("stats") or {
                "tokens_in": 0,
                "tokens_out": 0,
                "tok_per_sec": 0.0,
                "tools_run": 0,
            }
            if data.get("model_id"):
                self.current_model_id = data.get("model_id")
                self.update_meta_bars()
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            for m in self.messages:
                is_u = m.get("role") == "user"
                content = m.get("content", "")
                if is_u:
                    chat_scroll.mount(UserMessageCard(content))
                else:
                    card = AssistantTurnCard(self.current_model_id, initial_text=content)
                    chat_scroll.mount(card)

            chat_scroll.scroll_end(animate=False)

    def _on_prompt_selected(self, result: Any) -> None:
        if not result:
            return
        if isinstance(result, dict):
            p_text = result.get("text") or result.get("prompt", "")
            p_name = result.get("name", "Custom")
        else:
            p_text = str(result)
            p_name = "Custom"

        if p_text:
            if "<tool:" not in p_text:
                self.system_prompt = (
                    f"{p_text}\n\n"
                    f"Current working directory: {self.workdir}\n\n"
                    "CRITICAL INSTRUCTIONS FOR CODE AGENT BEHAVIOR:\n"
                    "1. You are an AUTONOMOUS CODE AGENT. NEVER tell the user to inspect files, run commands, or check directory structure themselves. YOU MUST DO IT YOURSELF using the XML tools immediately.\n"
                    "2. When the user asks you to check, build, edit, or debug a project, DO NOT ask permission or tell the user to prepare the project — immediately run <tool:ls path=\".\" /> or <tool:read path=\"file\" /> to inspect it.\n"
                    "3. Answer in the same language as the user (if the user asks in Polish, respond in Polish).\n"
                    "4. When you emit a tool call, the system will execute it and return the result inside <tool_response name=\"...\">...</tool_response>. You will then see the output and continue working until the task is complete.\n"
                    "5. If a tool call fails (e.g. <tool:edit> fails with \"don't match\"), DO NOT GIVE UP or stop! You MUST immediately inspect the file using <tool:read>, locate the exact content, and retry <tool:edit> or use <tool:write>. The turn is only complete when all changes are properly applied.\n\n"
                    "AVAILABLE XML TOOLS:\n"
                    "- <tool:ls path=\".\" /> : List files in directory\n"
                    "- <tool:read path=\"file.py\" lines=\"1-50\" /> : Read file contents\n"
                    "- <tool:glob pattern=\"*.py\" dir=\".\" /> : Find files matching glob\n"
                    "- <tool:search query=\"text\" path=\".\" /> : Search text across files\n"
                    "- <tool:write path=\"file.py\">content</tool:write> : Create or overwrite file\n"
                    "- <tool:edit path=\"file.py\"><old>exact_code</old><new>replacement_code</new></tool:edit> : Edit code\n"
                    "- <tool:command>shell_command</tool:command> : Run terminal command\n"
                    "- <tool:web>url_or_query</tool:web> : Web fetch or search\n"
                    "- <tool:scratch action=\"add|done|clear\">step note</tool:scratch> : Update TODO queue\n"
                    "- <tool:ask question=\"Question to user\" options=\"Option 1|Option 2|Option 3\" /> : Prompt user for choice/confirmation\n"
                )
            else:
                self.system_prompt = p_text

            self.settings.config["active_system_prompt"] = p_name
            self.settings.save_config()

            if not self.in_chat_mode:
                self._switch_to_chat()
            self._add_msg("system-msg", f"[b #3fb950]• Active system prompt:[/] [b white]{p_name}[/]")


    def start_generation(self, user_prompt: str) -> None:
        self.is_generating = True
        self.abort_requested = False
        self.auto_scroll_enabled = True

        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)

        cap = get_model_capability(self.current_model_id, self.current_provider)
        has_thinking_support = bool(cap and cap.thinking and self.thinking_level != "Off" and self._show_thinking)

        assistant_card = AssistantTurnCard(self.current_model_id, has_thinking=has_thinking_support)
        self._current_assistant_card = assistant_card

        if self.current_provider == "local_gguf":
            loaded_path = getattr(self.gguf_loader, "current_model_path", "") or ""
            needs_load = (
                self.gguf_loader.llm is None
                or os.path.basename(loaded_path) != os.path.basename(self.current_model_id)
            )
            if needs_load:
                active_loader = self.settings.config.get("active_loader", "cpu")
                assistant_card.start_loading(f"Loading model ({active_loader})…")
            elif has_thinking_support:
                assistant_card.start_thinking()
        elif has_thinking_support:
            assistant_card.start_thinking()

        chat_scroll.mount(assistant_card)
        self._scroll_chat_to_end_if_enabled(force=True)

        self._generation_worker(assistant_card)


    @work(thread=True)
    def _generation_worker(self, assistant_card: AssistantTurnCard) -> None:
        try:
            self._generation_worker_impl(assistant_card)
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            self.is_generating = False
            self.abort_requested = False
            self.call_from_thread(self._update_meta_bars)

    def _generation_worker_impl(
        self,
        assistant_card: AssistantTurnCard,
    ) -> None:
        start_time = time.time()
        api_key = self.settings.get_api_key(self.current_provider)
        gen_params = self.settings.config.get("generation", {})

        def _on_tool_start(tool_name: str, target: str) -> None:
            self.stats["tools_run"] += 1
            self.call_from_thread(assistant_card.add_tool, tool_name, target)
            self.call_from_thread(self._scroll_chat_to_end_if_enabled)

        self._turn_diff_stats = {"added": 0, "deleted": 0}

        def _on_tool_finish(tool_name: str, target: str, res: Dict[str, Any]) -> None:
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
            self.call_from_thread(assistant_card.finish_tool, res)
            self.call_from_thread(self._scroll_chat_to_end_if_enabled)
            self.call_from_thread(self.refresh)

        if self.agent_runner:
            self.agent_runner.on_tool_start = _on_tool_start
            self.agent_runner.on_tool_finish = _on_tool_finish
            self.agent_runner.ask_handler = self._handle_agent_ask

        if self.current_provider == "local_gguf":
            loaded_path = getattr(self.gguf_loader, "current_model_path", "") or ""
            needs_load = (
                self.gguf_loader.llm is None
                or os.path.basename(loaded_path) != os.path.basename(self.current_model_id)
            )
            if needs_load:
                load_start = time.time()
                loaded = self.gguf_loader.load_model(self.current_model_id)
                load_elapsed = time.time() - load_start
                if not loaded:
                    err_msg = f"Model '{self.current_model_id}' not found or could not be loaded."
                    self.call_from_thread(assistant_card.finish_loading, load_elapsed)
                    self.call_from_thread(assistant_card.update_content, f"**Error**: {err_msg}")
                    self.call_from_thread(assistant_card.finish, load_elapsed, 0, 0.0)
                    self.is_generating = False
                    return
                self.call_from_thread(assistant_card.finish_loading, load_elapsed)
                cap = get_model_capability(self.current_model_id, self.current_provider)
                if cap and cap.thinking and self.thinking_level != "Off" and self._show_thinking:
                    self.call_from_thread(assistant_card.start_thinking)

        turn = 0
        total_tokens = 0
        last_tool_failed = [False]

        while not self.abort_requested:
            turn += 1
            step_chunks: List[str] = []
            step_thinking: List[str] = []
            last_token_render = [0.0]
            step_tool_completed = [False]

            def on_token(chunk: str) -> None:
                if self.abort_requested or step_tool_completed[0]:
                    return
                tb = getattr(assistant_card, "thinking_block", None)
                if tb and not tb.is_finished:
                    self.call_from_thread(assistant_card.finish_thinking)
                step_chunks.append(chunk)
                accumulated = "".join(step_chunks)

                if TOOL_COMPLETE_PATTERNS.search(accumulated):
                    step_tool_completed[0] = True

                now = time.time()
                if now - last_token_render[0] > 0.03 or step_tool_completed[0]:
                    last_token_render[0] = now

                    tool_matches = list(re.finditer(
                        r'(?:<tool:|call:|<tool_call>call:|<\|tool_call>call:)(\w+)(?:[^>]*?(?:path|target|cmd|query|file|url|note|action)=["\']?([^"\'\s>]+)["\']?)?(?:>([^\n<]{1,60}))?',
                        accumulated,
                        flags=re.IGNORECASE,
                    ))
                    if tool_matches:
                        last_call = tool_matches[-1]
                        gen_name = last_call.group(1)
                        gen_tgt = last_call.group(2) or last_call.group(3) or ""
                        self.call_from_thread(assistant_card.set_generating_tool, gen_name, gen_tgt.strip())
                    else:
                        self.call_from_thread(assistant_card.clear_generating_tool)

                    clean_preview = self.agent_runner.strip_xml_tool_calls(accumulated) if self.agent_runner else accumulated
                    clean_preview = re.sub(r'(?:<tool:\w*[^>]*|<\|tool_call[^>]*|<tool_call[^>]*)$', '', clean_preview, flags=re.IGNORECASE)
                    self.call_from_thread(assistant_card.update_content, clean_preview)
                    self.call_from_thread(self._scroll_chat_to_end_if_enabled)

            last_thinking_render = [0.0]

            def on_thinking(chunk: str) -> None:
                if self.abort_requested:
                    return
                cap = get_model_capability(self.current_model_id, self.current_provider)
                if not cap or not cap.thinking or self.thinking_level == "Off" or not self._show_thinking:
                    return
                step_thinking.append(chunk)
                now = time.time()
                self.call_from_thread(assistant_card.append_thinking, chunk)
                if now - last_thinking_render[0] > 0.05:
                    last_thinking_render[0] = now
                    self.call_from_thread(self._scroll_chat_to_end_if_enabled)

            ctx_limit = get_dynamic_context_limit(self.current_model_id, self.current_provider)
            current_toks = estimate_token_count(self.messages)
            if should_summarize(current_toks, ctx_limit) and len(self.messages) > 1:
                try:
                    self.call_from_thread(assistant_card.add_tool, "summarizing", "")
                    self.call_from_thread(self._scroll_chat_to_end_if_enabled)

                    compact_payload = build_summarization_payload(
                        self.messages[:-1],
                        last_modified_files=getattr(self, "_last_modified_files", None),
                        max_budget_tokens=min(2500, max(1200, ctx_limit // 2)),
                    )
                    c_resp = ""
                    try:
                        if self.current_provider == "local_gguf":
                            c_resp, _ = self.gguf_loader.stream_chat(
                                messages=compact_payload,
                                temperature=0.3,
                                max_tokens=1024,
                                model_filename=self.current_model_id,
                            )
                        else:
                            c_resp, _ = stream_chat_completion(
                                provider_id=self.current_provider,
                                model_id=self.current_model_id,
                                messages=compact_payload,
                                api_key=api_key,
                                generation_params={"temperature": 0.3, "max_tokens": 1024},
                            )
                    except Exception:
                        c_resp = ""

                    is_err = not c_resp or any(err_kw in str(c_resp).lower() for err_kw in [
                        "error:", "exceed context window", "status code", "rate limit", "token limit", "api error", "400 bad request"
                    ])

                    handoff_content = format_compacted_handoff(
                        c_resp if not is_err else "",
                        last_modified_files=getattr(self, "_last_modified_files", None),
                        lang=detect_conversation_language(self.messages),
                    )
                    init_goal = self.messages[0]["content"][:300] if self.messages else ""
                    last_msg = self.messages[-1]
                    self.messages = [
                        {"role": "user", "content": "Poprzednie ustalenia i cel projektu:\n" + init_goal},
                        {"role": "assistant", "content": handoff_content.strip()},
                        last_msg,
                    ]
                    self.call_from_thread(assistant_card.finish_tool, {"success": True, "summary": handoff_content.strip()})
                    self.call_from_thread(self._scroll_chat_to_end_if_enabled)
                except Exception:
                    init_goal = self.messages[0]["content"][:300] if self.messages else ""
                    last_msg = self.messages[-1] if self.messages else {"role": "user", "content": ""}
                    handoff_content = format_compacted_handoff(
                        "",
                        last_modified_files=getattr(self, "_last_modified_files", None),
                        lang=detect_conversation_language(self.messages),
                    )
                    self.messages = [
                        {"role": "user", "content": "Poprzednie ustalenia i cel projektu:\n" + init_goal},
                        {"role": "assistant", "content": handoff_content.strip()},
                        last_msg,
                    ]
                    self.call_from_thread(assistant_card.finish_tool, {"success": True, "summary": handoff_content.strip()})

            sys_content = self.system_prompt
            agent_on = self.settings.config.get("agent", {}).get("enabled", True)
            if agent_on:
                from ..agent.runner import get_agent_system_prompt
                sys_content += get_agent_system_prompt(self.workdir, self.agent_mode)

            if getattr(self, "pinned_files", None):
                pinned_section = ["\n\nPINNED CONTEXT FILES (/add):"]
                for p_path, p_content in self.pinned_files.items():
                    pinned_section.append(f"--- File: {p_path} ---\n{p_content}\n--- End of {p_path} ---")
                sys_content += "\n".join(pinned_section)
            payload_messages = [{"role": "system", "content": sys_content}]
            payload_messages.extend(self.messages)

            if self.abort_requested:
                break

            if self.current_provider == "local_gguf":
                resp, think = self.gguf_loader.stream_chat(
                    messages=payload_messages,
                    temperature=gen_params.get("temperature", 0.6),
                    max_tokens=gen_params.get("max_tokens", 2048),
                    on_token=on_token,
                    on_thinking=on_thinking,
                    model_filename=self.current_model_id,
                    is_aborted=lambda: self.abort_requested or step_tool_completed[0],
                )
            else:
                resp, think = stream_chat_completion(
                    provider_id=self.current_provider,
                    model_id=self.current_model_id,
                    messages=payload_messages,
                    api_key=api_key,
                    generation_params=gen_params,
                    on_token=on_token,
                    on_thinking=on_thinking,
                    is_aborted=lambda: self.abort_requested or step_tool_completed[0],
                )

            if self.abort_requested:
                break

            self.call_from_thread(assistant_card.finish_thinking)

            step_text = "".join(step_chunks) or resp or ""
            toks = len(step_text.split())
            total_tokens += toks
            self.stats["tokens_out"] += toks

            if not step_text.strip():
                break

            if any(kw in step_text.lower() for kw in ["exceed context window", "context_length_exceeded", "maximum context length", "too many tokens"]):
                init_msg = self.messages[0] if self.messages else {"role": "user", "content": ""}
                last_msg = self.messages[-1] if len(self.messages) > 1 else {"role": "user", "content": ""}
                handoff = format_compacted_handoff(
                    "",
                    last_modified_files=getattr(self, "_last_modified_files", None),
                    lang=detect_conversation_language(self.messages),
                )
                self.messages = [
                    init_msg,
                    {"role": "assistant", "content": handoff},
                    last_msg,
                ]
                self.call_from_thread(assistant_card.update_last_body, "⚠️ Wykryto przekroczenie limitu kontekstu. Pamięć podręczna została pomyślnie skompresowana i zresetowana. Kontynuuję pracę...")
                self.call_from_thread(assistant_card.prepare_for_next_turn)
                continue

            segments = self.agent_runner.parse_chronological_segments(step_text) if self.agent_runner else [("text", step_text)]
            tool_calls = [data for kind, data in segments if kind == "tool"]

            if not tool_calls:
                if last_tool_failed[0]:
                    last_tool_failed[0] = False
                    self.call_from_thread(assistant_card.clear_generating_tool)
                    clean_step = self.agent_runner.strip_xml_tool_calls(step_text) if self.agent_runner else step_text
                    self.call_from_thread(assistant_card.update_last_body, clean_step)
                    if clean_step.strip():
                        self.messages.append({"role": "assistant", "content": clean_step})
                    self.messages.append({
                        "role": "user",
                        "content": "⚠️ The tool call failed and your modifications were not applied. Do NOT give up or stop! You must call <tool:read> to inspect the file and <tool:edit> or <tool:write> to complete the task.",
                    })
                    self.call_from_thread(assistant_card.prepare_for_next_turn)
                    continue

                self.call_from_thread(assistant_card.clear_generating_tool)
                clean_step = self.agent_runner.strip_xml_tool_calls(step_text) if self.agent_runner else step_text
                self.call_from_thread(assistant_card.update_last_body, clean_step)
                self.messages.append({"role": "assistant", "content": clean_step})
                break
            else:
                self.call_from_thread(assistant_card.clear_generating_tool)
                self.messages.append({"role": "assistant", "content": step_text})

                first_seg = segments[0] if segments else None
                first_text = first_seg[1] if (first_seg and first_seg[0] == "text") else ""
                if first_text:
                    self.call_from_thread(assistant_card.update_last_body, first_text)

                remaining_segments = segments[1:] if (first_seg and first_seg[0] == "text") else segments

                for kind, data in remaining_segments:
                    if self.abort_requested:
                        break
                    if kind == "text":
                        text_chunk = data
                        self.call_from_thread(assistant_card.append_text_block, text_chunk)
                        self.call_from_thread(self._scroll_chat_to_end_if_enabled)
                    elif kind == "tool":
                        tool_name, args = data
                        res = self.agent_runner.execute_tool(tool_name, args)
                        last_tool_failed[0] = bool(res.get("error") or res.get("success") is False)
                        tool_feedback = self.agent_runner.format_tool_result_for_llm(tool_name, res)
                        self.messages.append({"role": "user", "content": tool_feedback})
                        self.call_from_thread(self.refresh)

                if self.abort_requested:
                    break

                self.call_from_thread(assistant_card.prepare_for_next_turn)
                cap = get_model_capability(self.current_model_id, self.current_provider)
                has_thinking = bool(cap and cap.thinking and self.thinking_level != "Off" and self._show_thinking)
                if has_thinking:
                    self.call_from_thread(assistant_card.set_generating_tool, "Thinking", "…")
                else:
                    self.call_from_thread(assistant_card.clear_generating_tool)

        elapsed = time.time() - start_time
        tok_s = (total_tokens / elapsed) if elapsed > 0 else 0.0
        self.stats["tok_per_sec"] = tok_s

        if self.abort_requested:
            self.call_from_thread(assistant_card.clear_generating_tool)
            self.call_from_thread(assistant_card.abort)
        else:
            self.call_from_thread(assistant_card.clear_generating_tool)
            self.call_from_thread(assistant_card.finish, elapsed, total_tokens, tok_s)

            try:
                summary_msg = f"Turn complete · {total_tokens} tokens ({tok_s:.1f} tok/s)"
                if getattr(self, "_last_modified_files", None):
                    unique_cnt = len(set(self._last_modified_files))
                    summary_msg += f" · Modified {unique_cnt} file(s)"
                send_desktop_notification("CMDAI CODE", summary_msg)
            except Exception:
                pass

        try:
            self.session_manager.save_session(
                session_id=self.current_session_id,
                messages=self.messages,
                model_id=self.current_model_id,
                stats=self.stats,
            )
        except Exception:
            pass

        if self.agent_mode == "code" and self._last_modified_files:
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

        self.is_generating = False
        self.abort_requested = False


