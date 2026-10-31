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
    should_summarize,
)
from ..core.server import BackgroundHTTPServer
from ..core.sessions import SessionManager, transcript_from_messages
from ..core.settings import get_settings
from ..core.notifier import send_desktop_notification
from ..core.exporter import export_session_to_html
from ..core.releases import is_newer as _is_newer
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
    copy_text_to_clipboard,
)
from ..mcp.client import sanitize_name

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

# Logo lives in core/branding.py so the CLI can print it without Textual.
CMDAI_PARTS = _CMDAI_PARTS
CODE_PARTS = _CODE_PARTS
get_hero_logo = _get_hero_logo
LOGO_HERO = _LOGO_HERO


class ChatScroll(VerticalScroll):

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


class CMDAICodeTUI(App):

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
        # What the chat showed, in order. `messages` alone cannot rebuild a
        # session: it is the model's view, holding raw tool feedback and no
        # record of the tool cards, so a replay from it loses every tool and
        # prints tool output as if the user had typed it.
        self._transcript: List[Dict[str, Any]] = []
        self._last_escape_time: float = 0.0

        self.current_provider: str = self.settings.config.get("default_provider", "openrouter")
        self.current_model_id: str = self.settings.config.get("default_model", "")
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
            "- <tool:mcp /> : List MCP servers. Then <tool:mcp open server=\"NAME\" /> reveals that server's tools; call them as <tool:mcp__NAME__TOOL>{\"arg\":\"v\"}</tool:mcp__NAME__TOOL>\n"
            "- <tool:screenshot target=\"active_window\" /> + <tool:vision image_path=\"file.png\" question=\"...\" /> : Only listed in the agent section below when the loaded model supports vision\n"
        )

        self.messages: List[Dict[str, Any]] = []
        self._turn_counter: int = 0
        self.in_chat_mode: bool = False
        self.is_generating: bool = False
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
                yield Static("", id="hero-version")
                with Vertical(id="hero-input-box"):
                    yield VerticalScroll(id="hero-cmd-hints")
                    yield AutoExpandingInput(id="hero-prompt-input")
                    with Horizontal(id="hero-meta-bar"):
                        yield Static("", id="hero-meta-left")
                        yield Static("", id="hero-meta-thinking", classes="meta-clickable")
                        yield Static("", id="hero-meta-mode", classes="meta-clickable")
                        yield Static("", id="hero-meta-spacer")
                        rel_work = os.path.basename(self.workdir) or self.workdir
                        yield Static(f"[dim]{rel_work}[/dim]", id="hero-meta-right")

        with Vertical(id="chat-view", classes="hidden"):
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
                        yield Static("", id="input-meta-thinking", classes="meta-clickable")
                        yield Static("", id="input-meta-mode", classes="meta-clickable")
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

    def _refresh_hero_version(self) -> None:
        try:
            from ..core.releases import get_display_version
            ver = get_display_version()
            if ver and not ver.lower().startswith("v"):
                ver = f"v{ver}"
            base = get_hero_logo()
            lines = base.split("\n")
            if ver and lines:
                lines[-1] = lines[-1] + f"  [#888888]{ver}[/]"
            self.query_one("#hero-logo", Static).update("\n".join(lines))
            try:
                self.query_one("#hero-version", Static).update("")
            except Exception:
                pass
        except Exception:
            pass

    @work(thread=True)
    def _refresh_hero_version_live(self) -> None:
        try:
            from ..core.releases import get_releases
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
        if self.is_generating:
            self._meta_spinner_idx = (self._meta_spinner_idx + 1) % len(self.SPINNER_FRAMES)
            self._update_meta_bars()
        elif self._meta_spinner_idx != 0:
            self._meta_spinner_idx = 0
            self._update_meta_bars()

    # The pair used in all three places: the thinking block, the marker beside the
    # model name, and the thinking level label. Both are ambiguous width - rich
    # lays them in one cell, this terminal draws two - so the two spaces after
    # the glyph are load-bearing: the spare cell eats a space instead of the
    # white model name, which is what used to leave the marker half white and
    # half blue.
    META_MARKERS = ["⌬", "✻"]
    THINKING_MARKERS = ["⌬", "✻"]

    def _get_meta_left_text(self) -> str:
        """The model name, with a marker that runs for the whole turn.

        It used to animate only while the thinking block was open, which stopped
        the moment the model produced its first word of text - so the marker
        froze exactly while the model was working, and the bar looked finished
        during the longest part of the turn. It now animates for as long as the
        app is generating.
        """
        disp = clean_model_name(self.current_model_id)
        if self.is_generating:
            icon = self.META_MARKERS[(self._meta_spinner_idx // 3) % len(self.META_MARKERS)]
            return f"[b #58a6ff]{icon}[/]  [bold white]{disp}[/]"
        return f"[b #58a6ff]⌬[/]  [bold white]{disp}[/]"

    def _get_meta_mode_text(self) -> str:
        color = "#7ee787" if self.agent_mode == "auto" else ("#58a6ff" if self.agent_mode == "plan" else "#d29922")
        return f"[dim]mode:[/] [b {color}]{self.agent_mode}[/]"

    def _get_meta_thinking_text(self) -> str:
        """`thinking: <level>` - the level, plainly, whenever there is one.

        A model that cannot reason gets no label at all. It never reads "off" -
        there is no off; whether a model reasons is detected, not chosen.

        A model that *can* reason always shows the level, before anything has
        been pressed. It was hidden until Ctrl+T or until the model first
        reasoned, on the reasoning that showing a level nobody had chosen
        announces a setting. But that is what the label is: the detected
        default, visible so that `Ctrl+T` has something to show it changed
        relative to. A feature you can only find by pressing a key is a feature
        nobody finds.

        No marker on this one. The animated marker belongs to the model name,
        which is the thing being worked on; two spinners in one bar is one too
        many, and the second one competed with the first for attention.

        It lives in the input bar, directly after the model name and before the
        mode, and it is clickable: `/models` opens a list of models, so this
        opens a list of levels. It was tried on its own line above the input,
        which is where it stopped reading as part of the same thing.
        """
        cap = getattr(self, "_cap", None)
        if cap is None or not cap.thinking:
            return ""

        level = normalize_level(self.thinking_level) or cap.default_level
        if level not in cycle_order(cap.levels):
            level = cap.default_level or "medium"
        return f"[dim]thinking:[/] [b #58a6ff]{level}[/]"

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

    def _get_editor_main_path(self) -> str:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        upgraded_main = os.path.join(repo_root, "editor", "main.py")
        if os.path.isfile(upgraded_main):
            return upgraded_main
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "editor", "editor_app.py")

    def _open_diff_window(self, arg: str = "") -> None:
        try:
            from ..core.project_scope import is_unsafe_global_dir, resolve_diff_target_file
            recent_files = list(getattr(self, "_last_modified_files", None) or [])
            target_file = resolve_diff_target_file(self.workdir, arg=arg, recent_files=recent_files)
            if not target_file:
                if is_unsafe_global_dir(self.workdir):
                    self.notify(
                        "Podaj konkretny plik: /diff <plik> (ochrona przed skanowaniem całego PC)",
                        severity="warning",
                        timeout=4.0,
                    )
                else:
                    self.notify(
                        "Brak zmienionego pliku do podglądu diff. Użyj: /diff <plik>",
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
            self.notify(f"Otwarto diff w edytorze: {os.path.basename(target_file)}", timeout=2.5)
        except Exception as e:
            self.notify(f"Failed to launch editor diff: {e}", severity="error")

    def _create_turn_checkpoint(self) -> None:
        """Snapshot the working tree as a checkpoint labelled with the prompt.

        Called when a turn finishes (chat / auto mode) and when the user
        accepts the changes of a code-mode turn.
        """
        try:
            from ..core.checkpoints import CheckpointManager
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

    def action_quit_app(self) -> None:
        self._flush_live_turn_to_session()
        try:
            self.abort_requested = True
            if self._current_assistant_card:
                self._current_assistant_card.abort()
        except Exception:
            pass
        try:
            from cmdai_opencode.bridge import stop_bridge
            stop_bridge()
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

    def on_unmount(self) -> None:
        self._flush_live_turn_to_session()
        try:
            self.abort_requested = True
            from cmdai_opencode.bridge import stop_bridge
            stop_bridge()
        except Exception:
            pass

    def action_handle_interrupt(self) -> None:
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
        self._switch_to_chat()

    def _add_msg(self, css_class: str, text: str) -> Static:
        container = self.query_one("#chat-scroll", VerticalScroll)
        w = Static(text, classes=css_class)
        container.mount(w)
        container.scroll_end(animate=False)
        return w

    def _feedback(self, text: str) -> None:
        if self.in_chat_mode:
            try:
                self._add_msg("system-msg", text)
                return
            except Exception:
                pass
        try:
            import re
            plain = re.sub(r"\[.*?\]", "", text).strip()
            self.notify(plain[:220], timeout=3.0)
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

    # ---------------------------------------------------------- @mentions

    # Walked on every keystroke after "@", so directories that never hold
    # source worth referencing are skipped rather than filtered afterwards.
    _MENTION_SKIP_DIRS = {
        ".git", ".hg", ".svn", "__pycache__", "node_modules", "venv", ".venv",
        "env", "dist", "build", "target", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", "models", "logs", "cache", ".freebuff",
    }

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


    def _handle_submit(self, prompt: str, from_hero: bool = False) -> None:
        prompt = prompt.strip()
        if not prompt:
            return

        # `@path` is the explicit way to put a file in context. Ordinary
        # prose that happens to look like a filename is left alone.
        self._pin_mentions(prompt)

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
                "/cd", "/changelog", "/update", "/updatesettings", "/mcp"
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
            self._turn_counter += 1
            user_msg = UserMessageCard(prompt, turn_id=self._turn_counter)
            chat_scroll.mount(user_msg)
            chat_scroll.scroll_end(animate=False)
        except Exception:
            pass

        self.messages.append({"role": "user", "content": prompt, "turn_id": self._turn_counter})
        self._record_user_turn(prompt, self._turn_counter)
        self._save_current_session()
        self.start_generation(prompt)

    def handle_submit(self, text: str) -> None:
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
            "/branches": self._cmd_branches,
            "/fork": self._cmd_fork,
            "/git": self._cmd_git,
            "/checkpoints": self._cmd_checkpoints,
            "/checkpoint": self._cmd_checkpoints,
            "/revert": self._cmd_revert,
            "/undo": self._cmd_revert,
            "/diff": self._cmd_diff,
            "/changes": self._cmd_diff,
            "/editor": self._cmd_editor,
            "/test": self._cmd_test,
            "/commit": self._cmd_commit,
            "/ci": self._cmd_commit,
            "/review": self._cmd_review,
            "/plan": self._cmd_plan,
            "/tasks": self._cmd_plan,
            "/implement": self._cmd_implement,
            "/wdroz": self._cmd_implement,
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
            "/changelog": self._cmd_releases,
            "/update": self._cmd_update,
            "/updatesettings": self._cmd_update_settings,
            "/mcp": self._cmd_mcp,
            "/modelinfo": self._cmd_modelinfo,
            "/loader": self._cmd_loader,
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
        """`/thinking` - pick a level, or set one by name.

        No argument opens the picker, which is the same shape as `/models`: a
        list you arrow through and take. A named level sets it directly, in any
        spelling, so `/thinking xHigh` and `/thinking max` are the same request
        as far as the model is concerned.
        """
        cap = self._capability()
        levels = cycle_order(cap.levels) if (cap and cap.thinking) else []
        if not levels:
            self.notify(
                f"{self.current_model_id} does not reason", severity="warning", timeout=3.0
            )
            return

        arg = (arg or "").strip()
        if not arg:
            def on_picked(level: Optional[str]) -> None:
                if level:
                    self._cmd_thinking(level)

            self.push_screen(
                ThinkingModal(self.thinking_level, self.current_model_id,
                              self.current_provider, cap=cap),
                on_picked,
            )
            return

        arg = normalize_level(arg)
        if arg in levels:
            self.thinking_level = arg
            self._thinking_invoked = True
            self.settings.config.setdefault("generation", {})["reasoning_level"] = arg
            self.settings.save_config()
            self._update_meta_bars()
            self.notify(f"Thinking set to: {arg}", timeout=2.0)
        else:
            self.notify(f"Thinking levels: {', '.join(levels)}", severity="warning", timeout=3.0)

    def _on_help_command(self, cmd: Optional[str]) -> None:
        if not cmd:
            return
        modal_cmds = ("/models", "/system", "/sessions", "/stats", "/modelinfo",
                      "/quit", "/new", "/params", "/help", "/loader", "/settings",
                      "/tools", "/status", "/diff", "/changes", "/commit", "/ci",
                      "/plan", "/tasks", "/context", "/ctx", "/export", "/add", "/mode", "/thinking",
                      "/changelog", "/update", "/updatesettings", "/mcp")
        if not self.in_chat_mode and cmd not in modal_cmds:
            self._switch_to_chat()
        self._dispatch_command(cmd)

    def _cmd_new(self, arg: str = "") -> None:
        if self.messages:
            self._save_current_session()
        self.messages.clear()
        self._transcript = []
        self.current_session_id = f"session_{int(time.time())}"
        self.session_title = ""
        self._update_terminal_title()
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

    def _record_user_turn(self, text: str, turn_id: int) -> None:
        self._transcript.append({"kind": "user", "text": text, "turn_id": turn_id})

    def _record_assistant_text(self, text: str) -> None:
        if not text:
            return
        self._transcript.append({"kind": "assistant", "text": text})

    def _record_tool(self, tool_name: str, target: str, result: Any) -> None:
        """Record one tool call so the replay can rebuild its card.

        Keeps the whole result, not a summary: the card renders command output,
        diffs and file contents out of it, and a trimmed copy would come back
        as an empty block.
        """
        self._transcript.append({
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
            "web_search": "web", "search_web": "web", "fetch_web_page": "webfetch", "read_url_content": "webfetch",
        }

        KEY_PAT = (
            r'(?:\\?"?(?:TargetFile|target_file|targetfile|filePath|filepath|file_path|filename|file_name|dest|destination|file|path|cmd|command|commandLine|command_line|target|url|query)\\?"?)'
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

    def _save_current_session(self) -> None:
        try:
            from ..core.sessions import clean_title_from_prompt
            if not self.session_title.strip():
                first_u = next((str(m.get("content", "")) for m in self.messages if m.get("role") == "user"), "")
                if first_u:
                    self.session_title = clean_title_from_prompt(first_u)
                    self._update_terminal_title()
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

    def _update_terminal_title(self) -> None:
        try:
            if os.name != "nt":
                return
            import ctypes
            name = self.session_title.strip() or "new session"
            ctypes.windll.kernel32.SetConsoleTitleW(f"CC \u25cf {name}")
        except Exception:
            pass

    def _maybe_auto_name_session(self) -> None:
        try:
            if self.session_title.strip() or len(self.messages) < 2 or self.is_generating:
                if self.session_title.strip():
                    self.call_from_thread(self._update_terminal_title)
                return
            first_u = next((m.get("content", "") for m in self.messages if m.get("role") == "user"), "")
            first_a = next((m.get("content", "") for m in self.messages if m.get("role") == "assistant" and m.get("content")), "")
            if not first_u:
                return
            self._name_session_worker(first_u[:500], first_a[:500])
        except Exception:
            pass

    @work(thread=True)
    def _name_session_worker(self, user_text: str, assistant_text: str) -> None:
        try:
            title = ""
            naming = [
                {"role": "system", "content": "You name chat sessions. Reply with a short title only, max 6 words, no quotes, no punctuation at the end."},
                {"role": "user", "content": f"User: {user_text}\nAssistant: {assistant_text}"},
            ]
            if self.current_provider == "local_gguf":
                title, _ = self.gguf_loader.stream_chat(messages=naming, temperature=0.3, max_tokens=32, model_filename=self.current_model_id)
            else:
                from ..core.providers import stream_chat_completion
                title, _ = stream_chat_completion(
                    provider_id=self.current_provider, model_id=self.current_model_id,
                    messages=naming, api_key=self.settings.get_api_key(self.current_provider),
                    generation_params={"temperature": 0.3, "max_tokens": 32},
                )
            title = " ".join(str(title or "").strip().strip("\"'").split())[:80]
            if not title or len(title) < 3 or "error" in title.lower()[:20]:
                return
            self.session_title = title
            self.call_from_thread(self._apply_session_title, title)
        except Exception:
            pass

    def _apply_session_title(self, title: str) -> None:
        try:
            self.session_title = title
            self._save_current_session()
            self._update_terminal_title()
        except Exception:
            pass

    def _cmd_models(self, arg: str = "") -> None:
        self.push_screen(
            ModelSelectModal(self.current_provider, self.current_model_id),
            self._on_model_selected,
        )

    def _cmd_sessions(self, arg: str = "") -> None:
        if self.is_generating:
            self._feedback("[yellow]Generation in progress — stop it first (Esc).[/]")
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
            self._feedback(f"Unknown parameter: [b]{name}[/] — one of: temperature, top_p, max_tokens, repetition_penalty")
            return
        key, cast, lo, hi = spec
        gen = self.settings.config.setdefault("generation", {})
        if len(parts) < 2 or not parts[1].strip():
            self._feedback(f"[b #58a6ff]•[/] {key}: {gen.get(key, 0.6)}")
            return
        val_str = parts[1].strip()
        try:
            val = cast(val_str)
        except ValueError:
            self._feedback(f"Invalid value for {key}: [b]{val_str}[/]")
            return
        if val < lo or val > hi:
            self._feedback(f"{key} must be between {lo} and {hi}")
            return
        old = gen.get(key, 0.6)
        gen[key] = val
        self.settings.save_config()
        self._feedback(f"[b #58a6ff]•[/] {key}: {old} → {val}")

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

    def _cmd_update_settings(self, arg: str = "") -> None:
        """/updatesettings -> automatic updates on, off, and how often."""
        from .modals.update_settings import UpdateSettingsModal

        self.push_screen(UpdateSettingsModal(), self._on_update_setting)

    def _on_update_setting(self, action: Optional[str]) -> None:
        if action == "check":
            self._check_for_updates()
        elif action == "update":
            self._cmd_update("")

    def _check_for_updates(self, silent_if_current: bool = False) -> None:
        """Look for a newer release and say so, without pulling.

        Runs on a thread because it reaches the network. Only reports when the
        installed release is behind what GitHub has, so a check on an up-to-date
        installation says nothing at all.
        """
        from ..core.releases import get_bundled_release, get_display_version, get_release_list, get_releases

        def _work() -> None:
            try:
                fetched, _src = get_releases(refresh=True)
                if not fetched:
                    return
                newest = get_release_list(fetched)
                if not newest:
                    return
                latest_tag = str(newest[0].get("tag", ""))
                current_tag = str(get_display_version() or get_bundled_release().get("tag", ""))
                if current_tag and not _is_newer(latest_tag, current_tag):
                    if not silent_if_current:
                        self.call_from_thread(self.notify, "CMDAI CODE is up to date.", timeout=2.5)
                    return
                self.call_from_thread(
                    self.notify,
                    f"CMDAI CODE {latest_tag} is available - run [b]/update[/b] to install it.",
                    timeout=6.0,
                )
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    def _maybe_auto_update_check(self) -> None:
        """Startup check, gated on the user's own settings."""
        try:
            from .modals.update_settings import is_check_due
            if is_check_due():
                self._check_for_updates(silent_if_current=True)
        except Exception:
            pass

    def _cmd_releases(self, arg: str = "") -> None:
        self.push_screen(ReleasesModal(), lambda _: None)

    def _cmd_update(self, arg: str = "") -> None:
        """/update -> the same update `cmdai code update` runs.

        Both go through core.updater.run_update, so they cannot drift apart.
        The only difference is who draws the steps: the terminal animator on the
        CLI, a progress window here. Nothing on this path may print - escape
        codes on stdout land inside the rendered frame.
        """
        from ..core.releases import write_pending_notice
        from ..core.updater import run_update
        from .modals.update import TuiReporter, UpdateProgressModal

        if getattr(self, "_update_running", False):
            self.notify("An update is already running.", severity="warning", timeout=3.0)
            return
        if self.is_generating:
            self.notify("Finish the current turn before updating.", severity="warning", timeout=3.0)
            return

        app_root = getattr(self.settings, "base_dir", None) or os.getcwd()
        modal = UpdateProgressModal()
        self._update_running = True
        self.push_screen(modal, lambda _: None)

        def _run() -> None:
            reporter = TuiReporter(self, modal)
            try:
                result = run_update(app_root, reporter=reporter,
                                    on_updated=write_pending_notice)
            except Exception as e:
                reporter.message(f"[#f85149]Update failed: {e}[/]")
                self.call_from_thread(self._finish_update, None, str(e))
                return
            self.call_from_thread(self._finish_update, result, "")

        threading.Thread(target=_run, daemon=True).start()

    def _finish_update(self, result: Any, error: str) -> None:
        """Report the outcome once the updater's thread is done."""
        self._update_running = False
        if error:
            self.notify(f"Update failed: {error}", severity="error", timeout=6.0)
            return
        if result is None or not getattr(result, "ok", False):
            self.notify("Update failed. See the window for details.",
                        severity="error", timeout=6.0)
            return
        if result.changed:
            self.notify(f"{result.summary} - restart CMDAI CODE to run the new code.",
                        timeout=8.0)
        else:
            self.notify(result.summary, timeout=4.0)

    def show_user_actions(self, turn_id: int) -> None:
        from .modals.actions import ActionMenuModal
        self.push_screen(ActionMenuModal(), lambda act: self._on_user_action(act, turn_id))

    def show_assistant_actions(self, turn_id: int) -> None:
        # Disabled: clicking model response does not show action menu (only user cards do)
        return

    def _restore_prompt_to_input(self, text: str) -> None:
        """Puts text back into the prompt input field and focuses it."""
        if not text:
            return
        try:
            target_input = None
            if self.in_chat_mode:
                try:
                    target_input = self.query_one("#prompt-input", AutoExpandingInput)
                except Exception:
                    pass
            if target_input is None:
                try:
                    target_input = self.query_one("#hero-prompt-input", AutoExpandingInput)
                except Exception:
                    try:
                        target_input = self.query_one("#prompt-input", AutoExpandingInput)
                    except Exception:
                        pass

            if target_input is not None:
                target_input.text = text
                lines = text.splitlines()
                last_row = max(0, len(lines) - 1)
                last_col = len(lines[-1]) if lines else 0
                target_input.cursor_location = (last_row, last_col)
                if hasattr(target_input, "_update_auto_height"):
                    target_input._update_auto_height()
                self.set_focus(target_input)
        except Exception:
            pass

    def _on_user_action(self, action: object, turn_id: int) -> None:
        if not action or int(turn_id) <= 0:
            return
        if action == "copy":
            try:
                text = next((str(m.get("content", "")) for m in self.messages if m.get("role") == "user" and int(m.get("turn_id") or -1) == int(turn_id)), "")
                if not text:
                    for t in self._transcript:
                        if isinstance(t, dict) and t.get("kind") == "user" and int(t.get("turn_id") or -1) == int(turn_id):
                            text = str(t.get("prompt", "") or t.get("content", "")).strip()
                            break
                if text and copy_text_to_clipboard(text):
                    self.notify("User message copied.", timeout=2.0)
            except Exception:
                pass
            return
        if action in ("revert", "delete"):
            if action == "revert":
                user_prompt = ""
                for m in self.messages:
                    if m.get("role") == "user" and int(m.get("turn_id") or -1) == int(turn_id):
                        user_prompt = str(m.get("content", "")).strip()
                        break
                if not user_prompt:
                    for t in self._transcript:
                        if isinstance(t, dict) and t.get("kind") == "user" and int(t.get("turn_id") or -1) == int(turn_id):
                            user_prompt = str(t.get("prompt", "") or t.get("content", "")).strip()
                            break
                if not user_prompt:
                    try:
                        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
                        for child in chat_scroll.children:
                            if isinstance(child, UserMessageCard) and int(getattr(child, "turn_id", -1)) == int(turn_id):
                                user_prompt = getattr(child, "prompt", "")
                                break
                    except Exception:
                        pass

                reverted = self._revert_files(from_turn=int(turn_id))
                self._delete_turn(int(turn_id), keep_user=False, notify=False)
                if user_prompt:
                    self._restore_prompt_to_input(user_prompt)
                files_info = f" ({len(reverted)} file(s))" if reverted else ""
                self.notify(f"Reverted turn {turn_id}{files_info} — prompt restored to input.", timeout=2.5)
            else:
                self._delete_turn(int(turn_id), keep_user=False)

    def _capture_workspace_snapshot(self, turn_id: int) -> None:
        """Capture pre-turn file contents in workdir so reverting Turn N restores Turn N-1 state."""
        if turn_id <= 0:
            return
        try:
            from ..core.project_scope import is_unsafe_global_dir
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

    def _maybe_show_update_notice(self) -> None:
        """Show what changed, once, on the first launch after the version moved.

        Two ways in. The flag written by `cmdai code update` is authoritative:
        it proves an update actually ran. The version comparison catches
        everything the flag misses - a `git pull` by hand, a tag switch, another
        installer - because it only compares against the last recorded launch.
        """
        try:
            from ..core.releases import (
                clear_pending_notice,
                detect_version_change,
                get_bundled_release,
                get_display_version,
                mark_version_seen,
                read_pending_notice,
            )

            pending = read_pending_notice()
            if pending:
                clear_pending_notice()
                version = pending.get("version", "")
                mark_version_seen(version)
            else:
                change = detect_version_change(get_display_version())
                if not change:
                    return
                version = change.get("version", "")

            release = get_bundled_release()
            if not release:
                # Nothing bundled and no way to say what changed. Stay quiet
                # rather than opening an empty window.
                return
            self.push_screen(UpdateNoticeModal(version=version, release=release))
        except Exception:
            pass

    def _model_supports_vision(self) -> bool:
        """Whether the model currently loaded can accept images."""
        try:
            return bool(getattr(self._capability(), "vision", False))
        except Exception:
            return False

    def _cmd_mcp(self, arg: str = "") -> None:
        parts = (arg or "").split()
        if self.mcp_client is None:
            self._feedback("[dim]MCP unavailable.[/dim]")
            return
        if len(parts) >= 2 and parts[0] == "restart":
            ok = self.mcp_client.restart(parts[1])
            self._feedback(f"[b #58a6ff]/mcp restart {parts[1]}:[/] {'connected' if ok else 'failed'}")
            return
        if len(parts) >= 2 and parts[0] == "logs":
            # The registry key is sanitized, so a server configured as
            # "my-server" is stored as "my_server". Looking up the raw
            # argument meant `/mcp logs my-server` always reported
            # "unknown server".
            key = sanitize_name(parts[1])
            srv = self.mcp_client.servers.get(key)
            if srv is None:
                known = ", ".join(self.mcp_client.server_names()) or "(none)"
                self._feedback(f"[b #58a6ff]/mcp logs:[/] [warn]no server named "
                               f"'{parts[1]}'[/]. Known: {known}")
                return
            logs = srv.stderr_log[-20:] or ["(stderr is empty)"]
            self._feedback("[b #58a6ff]/mcp logs:[/]\n" + "\n".join(logs))
            return
        # Bare /mcp opens a window. It is listed among the modal commands, so
        # printing a line of text here was the only entry in that list that
        # did not behave like the rest.
        from .modals.mcp_servers import MCPServerModal
        self.push_screen(MCPServerModal(self.mcp_client, self.mcp_client.config_file()))

    def _subagent_target(self) -> Tuple[str, str]:
        """Which provider and model a subagent runs on.

        Defaults to the chat's own, which is what a subagent used to get with no
        way to say otherwise. `subagent_provider` / `subagent_model` override it,
        because inheriting is not always safe: a subagent spawned from a chat on
        a local bridge inherits that bridge, and the result is a tool loop
        rather than a report - which shows up as an empty window with the task
        text and nothing above it, and nothing in the transcript to explain why.
        """
        cfg = self.settings.config or {}
        provider = str(cfg.get("subagent_provider") or "").strip()
        model = str(cfg.get("subagent_model") or "").strip()
        return (provider or self.current_provider,
                model or self.current_model_id)

    def _cmd_modelinfo(self, arg: str = "") -> None:
        self.push_screen(InfoModal("Model info", self._build_modelinfo_body()))

    def _cmd_loader(self, arg: str = "") -> None:
        arg = (arg or "").strip().lower()
        if arg in ("cpu", "cuda", "vulkan", "ryzen", "npu"):
            self.settings.config["active_loader"] = arg
            self.settings.save_config()
            self._feedback(f"⚙️ Active loader set to: [b #58a6ff]{arg}[/]")
        else:
            self.push_screen(LoaderModal(), self._on_loader_selected)

    def _find_model_file(self, model_id: str) -> str:
        try:
            from ..core.resource_limits import find_local_model_file
            m_dir = getattr(getattr(self, "gguf_loader", None), "models_dir", "models")
            return find_local_model_file(model_id, models_dir=m_dir, workdir=getattr(self, "workdir", ""))
        except Exception:
            return ""

    def _dynamic_limit(self) -> int:
        try:
            from ..core.resource_limits import get_dynamic_context_limit
            m_dir = getattr(getattr(self, "gguf_loader", None), "models_dir", "models")
            return get_dynamic_context_limit(
                self.current_model_id, self.current_provider,
                model_path=self._find_model_file(self.current_model_id) or None,
                models_dir=m_dir, workdir=getattr(self, "workdir", ""),
            )
        except Exception:
            return 8192

    def _build_modelinfo_body(self) -> str:
        import psutil
        from ..core.capabilities import budget_for, clamp_effort, normalize_level
        from ..core.resource_limits import (
            get_dynamic_context_limit,
            FALLBACK_CONTEXT_LIMIT,
        )

        vm = psutil.virtual_memory()
        total_ram_gb = vm.total / (1024**3)
        avail_ram_gb = vm.available / (1024**3)
        cache_budget_70 = max(0.5, (avail_ram_gb * 0.70))
        tokens_70_ram = int(cache_budget_70 * 128_000)

        dyn_limit = self._dynamic_limit()
        cap = self._capability()
        gen = self.settings.config.get("generation", {})
        active_reasoning = normalize_level(gen.get("reasoning_level")) or (
            cap.default_level if cap else ""
        )

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
            # `budget_for`, not `BUDGET_MAP[level]`. This line read the fixed
            # ladder directly, so a local model was shown `32,768` next to a
            # level whose request carried a sixth of that.
            budget = budget_for(
                self.current_provider, active_reasoning, cap.context_limit
            )
            lines += [
                f"  [b #58a6ff]•[/] [dim]thinking[/]     [b #3fb950]Supported[/]",
                f"  [b #58a6ff]•[/] [dim]levels[/]       {', '.join(cycle_order(cap.levels))}",
                f"  [b #58a6ff]•[/] [dim]active level[/] [b white]{active_reasoning}[/]",
                f"  [b #58a6ff]•[/] [dim]budget[/]       [b #bc8cff]{budget:,} tokens[/]",
                f"  [b #58a6ff]•[/] [dim]api effort[/]   {clamp_effort(active_reasoning)}",
                f"  [b #58a6ff]•[/] [dim]param style[/]  {cap.param_style}",
            ]
            if cap.param_style == "variant":
                # A variant is a name the server recognises. No token count is
                # sent with it, so printing a budget implies one is.
                lines.append(
                    "  [dim]• the level goes out as a variant name; "
                    "no token budget is sent[/]"
                )
        else:
            lines.append("  [dim]• thinking:     Not supported for this model[/dim]")

        lines.append("\n[bold white]Context & Hardware Safety (70% RAM Rule):[/]")
        # A bare number here is a claim. When nobody has published a window for
        # this model the number is the core's fallback, and the compaction
        # threshold is built on top of it - so the user is entitled to know which
        # of the two they are looking at.
        known = dyn_limit > 0 and dyn_limit != FALLBACK_CONTEXT_LIMIT
        safe = f"[b #58a6ff]{dyn_limit:,} tokens[/]" if known else (
            f"[b #58a6ff]{dyn_limit:,}[/] [dim]fallback - nobody published this window[/]"
        )
        lines += [
            f"  [b #58a6ff]•[/] [dim]safe context[/] {safe}",
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
        from .modals.tools import ToolsModal
        self.push_screen(ToolsModal())

    def _cmd_status(self, arg: str = "") -> None:
        self.push_screen(InfoModal("Workspace Status", self._build_status_body()))

    @staticmethod
    def _is_silent_tool_call(tool_name: str, target: str = "") -> bool:
        """True for tool calls that run but should not draw a card.

        Only one case: a bare `<tool:mcp />`, which asks which MCP servers
        exist. It does no work, it fires on nearly every MCP turn, and the
        answer is one keystroke away in the /mcp window. Drawing a card for it
        buried the real tool calls.

        Deliberately not silent:
          * `<tool:mcp open server="X" />` - returns that server's tool
            schemas, the step the model actually needs to see happen
          * `<tool:mcp__server__tool>` - a real call
        """
        name = str(tool_name or "").strip().lower()
        return name in ("mcp", "mcps") and not str(target or "").strip()

    def _build_status_body(self) -> str:
        agent_on = self.settings.config.get("agent", {}).get("enabled", True)
        cap = self._capability()
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

        # Whether this model reasons is detected, so the line is a fact about
        # the model rather than a preference. Ctrl+T walks what is listed here.
        if cap and cap.thinking:
            levels = cycle_order(cap.levels)
            lines.append(
                f"[dim]Thinking:[/]     [b #58a6ff]{self.thinking_level or cap.default_level}[/]"
                f" [dim]({'/'.join(levels)} - Ctrl+T)[/]"
            )
        else:
            lines.append("[dim]Thinking:[/]     [dim]not available for this model[/]")

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
            self._feedback("Usage: [b]/cmd <command>[/] (e.g. [dim]/cmd dir[/] or [dim]/cmd git status[/])")

    def _spawn_terminal_window(self, script_path: str, title: str, *args: str) -> None:
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

    def _git_window(self, view: str = "diff") -> None:
        """Open the git branches modal or single-file diff in the upgraded editor."""
        if view == "diff":
            self._open_diff_window()
            return
        from .modals.git_modal import GitBranchesModal
        self.push_screen(GitBranchesModal(workdir=self.workdir))

    def _new_subagent_block(self, card: Any, role: str, goal: str, system: str, index: int, total: int) -> Any:
        """Create one chat marker for a subagent and label it with its role."""
        def _create():
            try:
                card.finish_thinking()
            except Exception:
                pass
            return card.add_tool("subagent", "starting")

        try:
            if threading.current_thread() is threading.main_thread():
                block = _create()
            else:
                block = self.call_from_thread(_create)
        except Exception:
            return None
        if block is None:
            return None
        try:
            block.subagent_role = role
            block.subagent_goal = goal
            block.subagent_system = system
            block.subagent_index = index
            block.subagent_total = total
            block.subagent_live_tool = "starting"
            block.subagent_started = time.monotonic()
            block.update_header()
            if threading.current_thread() is threading.main_thread():
                self._scroll_chat_to_end_if_enabled()
            else:
                self.call_from_thread(self._scroll_chat_to_end_if_enabled)
        except Exception:
            pass
        return block

    def _open_subagent_chat(self, block: Any) -> None:
        """Push the subagent chat for one marker, with siblings for arrow nav."""
        try:
            from .modals.subagent_chat import SubagentChatModal
        except Exception:
            return
        try:
            card = self._current_assistant_card
            siblings = [b for b in getattr(card, "tool_blocks", []) if getattr(b, "subagent_role", "")]
            if not siblings:
                siblings = [block]
            try:
                idx = siblings.index(block) + 1
            except ValueError:
                idx = 1
            agents = [{
                "role": b.subagent_role,
                "goal": b.subagent_goal,
                "system_prompt": b.subagent_system,
                "summary": str((b.result_data or {}).get("summary", "") or ""),
                "history": list(b.subagent_history or []),
                "findings": list(b.subagent_findings or []),
                "notes": list(b.subagent_notes or []),
                "events": list(getattr(b, "subagent_events", []) or []),
                "status": b.subagent_status,
                "elapsed": float(getattr(b, "subagent_elapsed", 0.0) or 0.0),
                "tokens": int(getattr(b, "subagent_tokens", 0) or 0),
                "error": getattr(b, "error_msg", "") if getattr(b, "has_error", False) else "",
            } for b in siblings]
            running = bool(getattr(block, "tool_running", False))
            self.push_screen(SubagentChatModal(
                role=block.subagent_role or "Subagent",
                goal=block.subagent_goal,
                system_prompt=block.subagent_system,
                history=list(block.subagent_history or []),
                findings=list(block.subagent_findings or []),
                agent_index=idx,
                agent_total=len(agents),
                agents=agents,
                live_agent=block if running else None,
            ))
        except Exception:
            pass

    def _cmd_git(self, arg: str = "") -> None:
        """/git -> the git window: changed files, diff and the commit bar."""
        self._git_window("diff")

    def _cmd_branches(self, arg: str = "") -> None:
        """/branches -> the same window, straight into the branch list."""
        self._git_window("git")

    def _cmd_branch(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if not arg:
            self._cmd_branches()
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
        """/diff [file] -> open the upgraded editor in single-file diff mode."""
        self._open_diff_window(arg)

    def _on_diff_closed(self, action: Any) -> None:
        if action == "commit":
            self.push_screen(CommitModal(self.workdir), self._on_commit_done)

    def _cmd_editor(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        target_path = os.path.join(self.workdir, arg) if arg else self.workdir
        try:
            editor_py = self._get_editor_main_path()
            self._spawn_terminal_window(editor_py, "CMDAI CODE Editor", target_path)
        except Exception as e:
            self.notify(f"Failed to launch editor: {e}", severity="error")

    def _cmd_revert(self, arg: str = "") -> None:
        """/revert or /undo: revert files and remove user + model messages/cards from revert point onwards."""
        if self.is_generating:
            self.notify("Generation in progress — stop it first (Esc).", severity="warning", timeout=2.5)
            return

        arg = (arg or "").strip()
        target_turn = -1
        if arg.isdigit():
            target_turn = int(arg)
        else:
            # Revert the last user turn if no turn number is passed
            for m in reversed(self.messages):
                if m.get("role") == "user":
                    target_turn = int(m.get("turn_id") or -1)
                    break

        if target_turn > 0:
            user_prompt = ""
            for m in self.messages:
                if m.get("role") == "user" and int(m.get("turn_id") or -1) == int(target_turn):
                    user_prompt = str(m.get("content", "")).strip()
                    break
            reverted = self._revert_files(from_turn=target_turn)
            self._delete_turn(target_turn, keep_user=False, notify=False)
            if user_prompt:
                self._restore_prompt_to_input(user_prompt)
            if reverted:
                self.notify(f"Reverted turn {target_turn} and {len(reverted)} file(s).", timeout=2.5)
            else:
                self.notify(f"Reverted turn {target_turn} (no file modifications).", timeout=2.0)
        else:
            reverted = self._revert_files(from_turn=self._turn_counter if self._turn_counter > 0 else -1)
            if self._turn_counter > 0:
                user_prompt = ""
                for m in self.messages:
                    if m.get("role") == "user" and int(m.get("turn_id") or -1) == int(self._turn_counter):
                        user_prompt = str(m.get("content", "")).strip()
                        break
                self._delete_turn(self._turn_counter, keep_user=False, notify=False)
                if user_prompt:
                    self._restore_prompt_to_input(user_prompt)
            if reverted:
                self.notify(f"Reverted {len(reverted)} file(s).", timeout=2.5)
            else:
                self.notify("Nothing to revert.", timeout=2.0)

    def _revert_session_files(self, arg: str = "") -> None:
        self._cmd_revert(arg)

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
        self.push_screen(CommitModal(self.workdir, suggested_msg=arg), self._on_commit_done)

    def _on_commit_done(self, msg: Optional[str]) -> None:
        if msg:
            self._feedback(f"[b #3fb950]✔ Committed successfully:[/] [white]{msg}[/]")

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
        # The bar reads "text" and "done"; the file parser produced "task" and
        # "status", so the preview showed empty rows for a plan that was there.
        self._on_todo_updated([
            {"text": str(t.get("task", "")), "done": t.get("status") == "done"}
            for t in tasks
        ])

    def _plan_steps(self) -> List[Dict[str, Any]]:
        """The agent's checklist as `{"text", "done"}` rows."""
        rows: List[Dict[str, Any]] = []
        for step in (getattr(self.agent_ctx, "todo_list", None) or []):
            if isinstance(step, dict):
                rows.append({
                    "text": str(step.get("text", "")).strip(),
                    "done": bool(step.get("done")),
                })
            else:
                rows.append({"text": str(step).strip(), "done": False})
        return [r for r in rows if r["text"]]

    def _save_plan_to_file(self) -> int:
        """Write the agent's checklist to CMDAIPLAN.md - the file /plan reads.

        The format is the one PlanModal writes, so the modal and the agent
        agree. Without this /plan showed a fixed placeholder list that had
        nothing to do with the plan the model had just made.

        Returns how many steps were written.
        """
        steps = self._plan_steps()
        if not steps:
            return 0
        lines = ["# CMDAIPLAN\n", "\n## Tasks\n"]
        for step in steps:
            lines.append(f"- [{'x' if step['done'] else ' '}] {step['text']}\n")
        try:
            with open(os.path.join(self.workdir, "CMDAIPLAN.md"), "w", encoding="utf-8") as f:
                f.writelines(lines)
            return len(steps)
        except Exception:
            return 0

    def _persist_plan_after_turn(self) -> int:
        """Mirror the agent's checklist into CMDAIPLAN.md after a turn.

        In PLAN mode the turn just made the plan, so the file is always
        written. In a writing mode a turn may have finished steps, and the file
        is updated too - but only if it already exists, so an ad-hoc todo list
        never creates a plan file on its own. That keeps `/plan` and a resume
        after a restart honest about what is still left to do.

        Returns how many steps were written (0 when nothing was written).
        """
        if self.agent_mode != "plan" and not os.path.exists(
            os.path.join(self.workdir, "CMDAIPLAN.md")
        ):
            return 0
        return self._save_plan_to_file()

    def _load_plan_from_file(self) -> int:
        """Load CMDAIPLAN.md into the agent's checklist.

        The checklist lives in the agent context, so a restart empties it while
        the file survives. `/plan` then shows the plan and `/implement` has
        nothing to execute - this is the bridge between the two.
        """
        path = os.path.join(self.workdir, "CMDAIPLAN.md")
        if not os.path.exists(path):
            return 0
        rows: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    ls = line.strip()
                    if ls.startswith(("- [ ]", "* [ ]")):
                        rows.append({"text": ls[5:].strip(), "done": False})
                    elif ls[:5].lower() in ("- [x]", "* [x]"):
                        rows.append({"text": ls[5:].strip(), "done": True})
        except Exception:
            return 0
        rows = [r for r in rows if r["text"]]
        if not rows:
            return 0
        self.agent_ctx.todo_list = rows
        self._on_todo_updated(rows)
        return len(rows)

    def _cmd_implement(self, arg: str = "") -> None:
        """`/implement` - switch to code mode and execute the plan checklist.

        The opencode way round: plan first, build after. The checklist made in
        PLAN mode stays in the agent context, so the agent already knows what
        the work order is - it only needs a mode where writing is allowed.
        """
        if self.is_generating:
            self.notify(
                "Generation in progress - stop it first (Esc).",
                severity="warning",
                timeout=2.5,
            )
            return

        pending = [s for s in self._plan_steps() if not s.get("done")]
        if not pending:
            # A restart empties the context, not the file.
            self._load_plan_from_file()
            pending = [s for s in self._plan_steps() if not s.get("done")]
        if not pending:
            self.notify(
                "No plan to implement. Use /mode plan, let the agent make a plan, then /implement.",
                severity="warning",
                timeout=3.5,
            )
            return

        # The same switch /mode code does, so the choice survives a restart.
        self.agent_mode = "code"
        self.settings.config.setdefault("agent", {})["mode"] = self.agent_mode
        self.settings.save_config()
        self.agent_ctx.mode = self.agent_mode
        self._update_meta_bars()

        # The checklist lives in the context, not in the prompt, so the steps go
        # into the message itself. Without them a plan resumed from the file
        # would be invisible to the model - the todo tool only reports the list
        # back when it is called. Indices are the ones `tool_scratch` uses
        # (0-based), because that is what "done" takes.
        indexed = "\n".join(
            f"{i}. {s['text']}"
            for i, s in enumerate(self._plan_steps())
            if not s.get("done")
        )
        extra = f"\nDodatkowe wytyczne: {arg.strip()}" if arg.strip() else ""
        self._handle_submit(
            "Zaimplementuj plan z checklisty krok po kroku:\n"
            f"{indexed}\n"
            "Po ukończeniu każdego kroku oznacz go przez "
            "<tool:todo action=\"done\" index=\"N\" />, gdzie N to numer kroku "
            "z listy powyżej. Na końcu zweryfikuj wynik."
            f"{extra}",
            from_hero=False,
        )

    def _cmd_debug(self, arg: str = "") -> None:
        from ..agent.verifier import scan_project_bugs
        report = scan_project_bugs(self.workdir)
        scanned = report.get("scanned_files", 0)
        errors = report.get("errors", {})
        if not errors:
            self._feedback(f"[b #3fb950]✔ Debug Syntax Check Passed![/] Scanned [b]{scanned}[/] files with [b #3fb950]0 errors[/].")
        else:
            lines = [f"[b #f85149]⚠️ Syntax Errors Detected[/] ({len(errors)} file(s) with errors out of {scanned} scanned):\n"]
            for fpath, errs in errors.items():
                lines.append(f"[b white]• {fpath}[/]")
                for e in errs:
                    lines.append(f"  [#f85149]{e}[/]")
            self._feedback("\n".join(lines))

    def _cmd_index(self, arg: str = "") -> None:
        if not self.in_chat_mode:
            self._switch_to_chat()
        try:
            from ..indexer.ast_indexer import ProjectIndexer
            indexer = ProjectIndexer(self.workdir)
            stats = indexer.index_all()
        except Exception as e:
            self._add_msg("system-msg", f"[#f85149]Indexing failed:[/] {e}")

    def _cmd_context(self, arg: str = "") -> None:
        max_ctx = self._dynamic_limit()
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
                self._feedback(f"[b #3fb950]Session exported successfully:[/] [b white]{rel}[/]")
                self.notify(f"Exported to {rel}", timeout=3.5)
            send_desktop_notification("CMDAI CODE", f"Session exported: {rel}")
        except Exception as e:
            if self.is_running:
                self._feedback(f"[#f85149]Failed to export session:[/] {e}")

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
                self._feedback(f"[b #58a6ff]•[/] Opened Windows Explorer in: [b white]{target_dir}[/]")
            except Exception as e:
                self.notify(f"Failed to open Windows Explorer: {e}", severity="error")

    def _on_file_selected_for_add(self, rel_path: Optional[str]) -> None:
        if rel_path:
            self._add_file_to_context(rel_path)

    def _add_file_to_context(self, rel_path: str) -> None:
        full_p = os.path.join(self.workdir, rel_path) if not os.path.isabs(rel_path) else rel_path
        if not os.path.exists(full_p):
            self._feedback(f"[#f85149]File not found:[/] {rel_path}")
            return

        try:
            with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            clean_rel = os.path.relpath(full_p, self.workdir).replace("\\", "/")
            self.pinned_files[clean_rel] = content
            lines = len(content.splitlines())
            self._feedback(f"[b #3fb950]+ Pinned to context:[/] [b white]{clean_rel}[/] [dim]({lines} lines)[/dim]")
        except Exception as e:
            self._feedback(f"[#f85149]Error reading file:[/] {e}")

    def _cmd_drop(self, arg: str = "") -> None:
        arg = (arg or "").strip()

        if not self.pinned_files:
            self._feedback("[dim]No files currently pinned in context.[/dim]")
            return

        if not arg or arg.lower() == "all":
            cnt = len(self.pinned_files)
            self.pinned_files.clear()
            self._feedback(f"[b #f0883e]- Unpinned all {cnt} files from context.[/]")
            return

        matched = [k for k in self.pinned_files if arg.lower() in k.lower()]
        if matched:
            for k in matched:
                del self.pinned_files[k]
            self._feedback(f"[b #f0883e]- Unpinned from context:[/] {', '.join(matched)}")
        else:
            self._feedback(f"[dim]File not pinned:[/] {arg} (pinned: {', '.join(self.pinned_files.keys())})")

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

        try:
            from cmdai_opencode.bridge import get_bridge
            get_bridge(self.workdir)
        except Exception:
            pass

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
        self._dispatch_command(cmd_text)

    def execute_terminal_command(self, cmd_line: str) -> None:
        cmd_line = cmd_line.strip()
        if not cmd_line or self.is_generating:
            return

        if not self.in_chat_mode:
            self._switch_to_chat()
        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            self._turn_counter += 1
            user_msg = UserMessageCard(f"!{cmd_line}", turn_id=self._turn_counter)
            chat_scroll.mount(user_msg)
            card = AssistantTurnCard("Terminal", turn_id=self._turn_counter)
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
        if provider != "local_gguf":
            return
        found_path = self._find_model_file(model_id) or None
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
        """Ask the user something from a worker thread and wait for the answer.

        The wait is bounded. An unbounded `evt.wait()` here hangs the whole
        agent loop when the modal never reports back, and the app looks frozen
        with no error anywhere. Three ways that happens, all reproduced:

          * `call_from_thread` is a no-op because the event loop is busy
          * `push_screen` raises while the screen is being mounted
          * the screen mounts but its dismiss callback never fires

        In every case the tool call now fails loudly with a message the model
        can act on, instead of the app going silent forever. Returning "" is
        deliberate: the gate treats that as "declined", so the safe outcome
        (no tool runs) is also the one that keeps the turn moving.
        """
        evt = threading.Event()
        res = [""]
        failures: List[str] = []

        def _on_modal_dismiss(val: str) -> None:
            res[0] = "" if val is None else str(val)
            evt.set()

        def _open_modal() -> None:
            try:
                self.push_screen(AskModal(question, options=options), _on_modal_dismiss)
            except Exception as exc:
                # Without this the exception surfaces on the Textual side and
                # this thread waits for an event nobody will ever set.
                failures.append(f"could not open the prompt: {exc}")
                evt.set()

        try:
            self.call_from_thread(_open_modal)
        except Exception as exc:
            failures.append(f"could not reach the UI thread: {exc}")
            evt.set()

        if not evt.wait(timeout=self.ASK_TIMEOUT):
            failures.append(f"no answer within {self.ASK_TIMEOUT:.0f}s")

        if failures:
            detail = "; ".join(failures)
            message = (f"Agent question went unanswered ({detail}). "
                       f"Treated as declined.")
            # Guarded: reporting the failure uses the same channel that just
            # failed, so an unguarded notify here would raise inside the worker
            # and lose the one message that explains what went wrong. When the
            # channel is dead entirely, print instead - a line in the terminal
            # beats silence.
            try:
                self.call_from_thread(self.notify, message,
                                      title="Timeout", timeout=6.0)
            except Exception:
                try:
                    print(f"[CMDAI] {message}", file=sys.stderr)
                except Exception:
                    pass
        return res[0]

    def trigger_summarize(self, auto: bool = False) -> None:
        if not self.messages:
            return

        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        block = ToolBlock("summarizing", "")
        chat_scroll.mount(block)
        chat_scroll.scroll_end(animate=False)

        self._summarize_worker(block, auto)

    @work(thread=True)
    def _summarize_worker(self, tool_block: ToolBlock, auto: bool = False) -> None:
        ctx_limit = self._dynamic_limit()
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

    def _replay_transcript(self, transcript: List[Dict[str, Any]]) -> None:
        """Rebuild a session into the chat with the widgets it used before.

        Same cards, same tool blocks, same order - a reloaded session is
        supposed to be indistinguishable from the one that was saved. Grouping
        matters: an assistant turn is one card holding its prose and its tools,
        because that is how the live chat builds it.
        """
        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        card: Optional[AssistantTurnCard] = None
        marks: List[Any] = []

        for item in transcript or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")

            if kind == "user":
                if card is not None:
                    card.finish(0.0)
                card = None
                turn_id = int(item.get("turn_id") or 0)
                if turn_id <= 0:
                    self._turn_counter += 1
                    turn_id = self._turn_counter
                else:
                    self._turn_counter = max(self._turn_counter, turn_id)
                chat_scroll.mount(UserMessageCard(str(item.get("text", "")), turn_id=turn_id))
                continue

            if card is None:
                has_think = any(isinstance(t, dict) and t.get("kind") == "thinking" for t in transcript)
                card = AssistantTurnCard(self.current_model_id, has_thinking=True if has_think else None, turn_id=turn_id)
                chat_scroll.mount(card)

            if kind == "thinking":
                text = str(item.get("text", "") or "").strip()
                if text:
                    try:
                        tb = card.thinking_block
                        if tb is not None and tb.thinking_text.strip():
                            tb.thinking_text += "\n\n" + text
                        else:
                            card.append_thinking(text)
                        if card.thinking_block is not None:
                            card.thinking_block.is_finished = False
                            card.thinking_block.finish()
                            card.thinking_block.elapsed = 0.0
                    except Exception:
                        pass
            elif kind == "assistant":
                text = str(item.get("text", "") or "")
                if not text.strip():
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
                            blk_res = {"success": True, "path": tgt}
                            if t_args.get("content"):
                                blk_res["content"] = t_args.get("content")
                            blk.finish(blk_res)
            elif kind == "tool":
                tool_n = str(item.get("tool", "tool"))
                tool_t = str(item.get("target", ""))
                res_obj = item.get("result") or {"success": True}
                if not tool_t and isinstance(res_obj, dict):
                    tool_t = str(res_obj.get("path") or res_obj.get("file") or res_obj.get("target") or "")
                block = card.add_tool(tool_n, tool_t)
                if block is not None:
                    block.finish(res_obj)
            elif kind == "subagent":
                role = str(item.get("role", "Subagent") or "Subagent")
                block = card.add_tool("subagent", role)
                if block is None:
                    continue
                block.subagent_role = role
                block.subagent_goal = str(item.get("goal", ""))
                block.subagent_system = str(item.get("system", ""))
                agent = item.get("agent") or {}
                # finish_subagent carries the report, notes and history onto the
                # marker, which is what _open_subagent_chat reads. Setting the
                # role is enough for it to be picked up as a sibling too.
                block.finish_subagent(agent)
                block.update_header()
                marks.append(block)

        if card is not None:
            card.finish(0.0)

        # Sibling position is only known once every marker exists.
        total = len(marks)
        for n, block in enumerate(marks, start=1):
            block.subagent_index = n
            block.subagent_total = total
            block.update_header()

        chat_scroll.scroll_end(animate=False)

    def _on_session_selected(self, session_id: str) -> None:
        if not session_id:
            return
        data = self.session_manager.load_session(session_id)
        if not data:
            self.notify("That session could not be read.", severity="error", timeout=4.0)
            return

        if self.messages:
            self._save_current_session()
        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        chat_scroll.remove_children()

        self.current_session_id = session_id
        self.messages = list(data.get("messages", []) or [])
        self.session_title = str(data.get("title", "") or "")
        self.stats = dict(data.get("stats") or {
            "tokens_in": 0,
            "tokens_out": 0,
            "tok_per_sec": 0.0,
            "tools_run": 0,
        })
        self._current_assistant_card = None
        if data.get("model_id"):
            self.current_model_id = data.get("model_id")
            # Every other model change writes through to the config, so the
            # next launch starts where this one is. Loading a session used to
            # be the exception: the model changed on screen while the file kept
            # the old one, and the next launch silently reverted.
            self.settings.config["default_model"] = self.current_model_id
            self.settings.save_config()
            self.update_meta_bars()

        # A session saved before the transcript existed is rebuilt from the
        # conversation, so its tool calls still stop looking like things the
        # user typed.
        transcript = data.get("transcript")
        if not isinstance(transcript, list) or not transcript:
            transcript = transcript_from_messages(self.messages)
        else:
            # A transcript with no model output while the conversation does
            # have some is a session saved by an older build that never
            # recorded final answers. The messages are more complete then.
            has_output = any(isinstance(t, dict) and t.get("kind") in ("assistant", "tool", "subagent", "thinking")
                             for t in transcript)
            msg_output = any(m.get("role") == "assistant" and str(m.get("content", "")).strip()
                             for m in self.messages)
            t_asst = sum(1 for t in transcript if isinstance(t, dict) and t.get("kind") == "assistant")
            m_asst = sum(1 for m in self.messages if m.get("role") == "assistant" and str(m.get("content", "")).strip())
            if (not has_output and msg_output) or (m_asst > t_asst and t_asst == 0):
                transcript = transcript_from_messages(self.messages)
            else:
                # The final answer of each turn was never recorded by older
                # builds. If the conversation ends with one the transcript
                # lacks, put it back at the end.
                last_asst = next((str(m.get("content", "")).strip() for m in reversed(self.messages)
                                  if m.get("role") == "assistant"), "")
                if last_asst and self.messages and self.messages[-1].get("role") == "assistant":
                    known = {str(t.get("text", "")).strip() for t in transcript
                             if isinstance(t, dict) and t.get("kind") == "assistant"}
                    if last_asst not in known:
                        transcript = list(transcript) + [{"kind": "assistant", "text": last_asst}]
        self._transcript = [t for t in transcript if isinstance(t, dict)]

        # Switch to chat view and render replayed cards
        self._switch_to_chat()
        self._replay_transcript(self._transcript)
        self._update_terminal_title()

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

    def start_generation(self, user_prompt: str) -> None:
        self.is_generating = True
        self.abort_requested = False
        self.auto_scroll_enabled = True
        self._last_user_scroll_time = 0.0
        self._set_follow_hint(False)

        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)

        cap = self._capability()
        has_thinking_support = bool(cap and cap.thinking)

        assistant_card = AssistantTurnCard(self.current_model_id, has_thinking=has_thinking_support, turn_id=self._turn_counter)
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
        # Deliberately no `start_thinking()` here. It used to be called as soon as
        # the request went out, so the block appeared saying "Thinking 1.5s"
        # while the model had produced nothing at all and the app was merely
        # waiting for its first token - which reads as the model thinking and
        # makes a slow response look like a long deliberation. The block is
        # created by the first reasoning delta instead, so it appears exactly
        # when there is reasoning to show.
        #
        # A model with reasoning support that answers without any is a normal
        # outcome, not a failure, and now looks like one.
        _ = has_thinking_support

        chat_scroll.mount(assistant_card)
        self._scroll_chat_to_end_if_enabled(force=True)

        self._generation_worker(assistant_card)


    @work(thread=True)
    def _generation_worker(self, assistant_card: AssistantTurnCard) -> None:
        try:
            self._generation_worker_impl(assistant_card)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            try:
                self.call_from_thread(assistant_card.update_content, f"**Execution error**: {exc}")
            except Exception:
                pass
        finally:
            if getattr(assistant_card, "is_generating", False):
                self.call_from_thread(assistant_card.finish, 0.0, 0, 0.0)
            self.is_generating = False
            self.abort_requested = False
            self.call_from_thread(self._update_meta_bars)
            self.call_from_thread(self._maybe_auto_name_session)

    def _generation_worker_impl(
        self,
        assistant_card: AssistantTurnCard,
    ) -> None:
        start_time = time.time()
        current_turn_id = int(getattr(assistant_card, "turn_id", -1) or self._turn_counter or 0)
        if current_turn_id > 0:
            self._capture_workspace_snapshot(current_turn_id)
        api_key = self.settings.get_api_key(self.current_provider)
        gen_params = dict(self.settings.config.get("generation", {}) or {})
        gen_params["workdir"] = self.workdir

        def _on_tool_start(tool_name: str, target: str) -> None:
            self.stats["tools_run"] += 1
            if tool_name.lower() in ("write", "edit") and target and current_turn_id > 0:
                self._snapshot_file_before_modify(current_turn_id, target.split(" (")[0].strip())
            if self._is_silent_tool_call(tool_name, target):
                # A bare <tool:mcp /> only asks which servers exist. It does
                # no work and its answer is available in the /mcp window, so a
                # card for it was noise in every single MCP turn. The model
                # still receives the full result.
                return
            self.call_from_thread(assistant_card.add_tool, tool_name, target)
            self.call_from_thread(self._scroll_chat_to_end_if_enabled)

        self._turn_diff_stats = {"added": 0, "deleted": 0}

        def _on_tool_finish(tool_name: str, target: str, res: Dict[str, Any]) -> None:
            if self._is_silent_tool_call(tool_name, target):
                # No card was started, so finish_tool must not run either: with
                # nothing running it falls back to the last block and would
                # paint this result onto the previous tool.
                self._record_tool(tool_name, target, res)
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
            self._record_tool(tool_name, target, res)
            self.call_from_thread(assistant_card.finish_tool, res)
            self.call_from_thread(self._scroll_chat_to_end_if_enabled)
            self.call_from_thread(self.refresh)

        if self.agent_runner:
            self.agent_runner.on_tool_start = _on_tool_start
            self.agent_runner.on_tool_finish = _on_tool_finish
            self.agent_runner.ask_handler = self._handle_agent_ask
            self.agent_runner.provider_id = self.current_provider
            if hasattr(self.agent_runner, "ctx") and self.agent_runner.ctx is not None:
                self.agent_runner.ctx.on_before_file_modify = lambda p, _tid=current_turn_id: self._snapshot_file_before_modify(_tid, p)

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

        turn = 0
        total_tokens = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_gen_time = 0.0
        last_tool_failed = [False]

        while not self.abort_requested:
            turn += 1
            step_chunks: List[str] = []
            step_thinking: List[str] = []
            self._live_step_chunks = step_chunks
            self._live_step_thinking = step_thinking
            last_token_render = [0.0]
            step_tool_completed = [False]
            step_usage: Dict[str, Any] = {}
            first_token_time = [0.0]

            def _on_step_usage(u: Dict[str, Any]) -> None:
                step_usage.update(u)

            def on_token(chunk: str) -> None:
                if not first_token_time[0]:
                    first_token_time[0] = time.time()
                tb = getattr(assistant_card, "thinking_block", None)
                if tb and not tb.is_finished:
                    self.call_from_thread(assistant_card.finish_thinking)
                if self.abort_requested or step_tool_completed[0]:
                    return
                step_chunks.append(chunk)
                accumulated = "".join(step_chunks)

                if TOOL_COMPLETE_PATTERNS.search(accumulated):
                    step_tool_completed[0] = True

                now = time.time()
                if server_tool_running[0]:
                    # Any text delta arriving while a server tool is running is the
                    # buffered tail of the sentence before the tool call.
                    pre_slice = accumulated[pre_tool_start[0]:]
                    clean_pre = self.agent_runner.strip_xml_tool_calls(pre_slice) if self.agent_runner else pre_slice
                    self.call_from_thread(assistant_card.update_pre_tool_content, clean_pre)
                    step_rendered[0] = len(accumulated)
                    return

                if server_tools_ran[0] > 0 and chunk.strip():
                    post_tool_text_seen[0] = True

                if now - last_token_render[0] > 0.03 or step_tool_completed[0]:
                    fresh = accumulated[step_rendered[0]:]
                    tl_name, tl_tgt, live_content = self._extract_live_tool_stream(fresh)
                    if tl_name and (tl_tgt or tl_name not in ("write", "edit", "read")):
                        self.call_from_thread(assistant_card.set_generating_tool, tl_name, tl_tgt, live_content)
                    else:
                        self.call_from_thread(assistant_card.clear_generating_tool)

                    clean_preview = self.agent_runner.strip_xml_tool_calls(fresh) if self.agent_runner else fresh
                    clean_preview = re.sub(
                        r'(?:<(?:tool:|\/tool_call:?|tool_call:?|\|tool_call>call:)[a-zA-Z0-9_-]+.*$|```(?:json)?\s*\[?\s*\{.*$)',
                        '',
                        clean_preview,
                        flags=re.DOTALL | re.IGNORECASE,
                    )
                    self.call_from_thread(assistant_card.update_content, clean_preview)
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
                self.call_from_thread(assistant_card.finish_thinking)
                if phase in ("generating", "running"):
                    accumulated_now = "".join(step_chunks)
                    pending = accumulated_now[step_rendered[0]:]
                    if pending:
                        clean_pending = self.agent_runner.strip_xml_tool_calls(pending) if self.agent_runner else pending
                        clean_pending = re.sub(
                            r'(?:<(?:tool:|\/tool_call:?|tool_call:?|\|tool_call>call:)[a-zA-Z0-9_-]+.*$|```(?:json)?\s*\[?\s*\{.*$)',
                            '',
                            clean_pending,
                            flags=re.DOTALL | re.IGNORECASE,
                        )
                        self.call_from_thread(assistant_card.update_content, clean_pending)
                        if clean_pending.strip():
                            self._record_assistant_text(clean_pending.strip())
                    if not server_tool_running[0]:
                        pre_tool_start[0] = step_rendered[0]
                    step_rendered[0] = len(accumulated_now)
                    server_tool_running[0] = True
                self.call_from_thread(
                    assistant_card.note_server_tool, name, phase, detail
                )
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
                    self._record_tool(name, detail, {"success": True, "content": detail})

            def on_thinking(chunk: str) -> None:
                if not first_token_time[0]:
                    first_token_time[0] = time.time()
                if self.abort_requested:
                    return
                # No gate on the detected capability here, on purpose. A
                # reasoning chunk arriving is the proof; the cache it would be
                # checked against is the very thing that was wrong before. And
                # this runs per delta, so a `get_model_capability` call would be
                # a cache miss on every token for a model detected mid-stream.
                step_thinking.append(chunk)
                now = time.time()
                self.call_from_thread(assistant_card.append_thinking, chunk)
                if now - last_thinking_render[0] > 0.05:
                    last_thinking_render[0] = now
                    self.call_from_thread(self._scroll_chat_to_end_if_enabled)

            ctx_limit = self._dynamic_limit()
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
                try:
                    has_vision = self._model_supports_vision()
                except Exception:
                    has_vision = False
                sys_content += get_agent_system_prompt(self.workdir, self.agent_mode, vision=has_vision)

            if getattr(self, "pinned_files", None):
                pinned_section = ["\n\nPINNED CONTEXT FILES (/add):"]
                for p_path, p_content in self.pinned_files.items():
                    pinned_section.append(f"--- File: {p_path} ---\n{p_content}\n--- End of {p_path} ---")
                sys_content += "\n".join(pinned_section)
            from ..agent.tasks import BackgroundTaskManager
            bg_notes = BackgroundTaskManager.get_instance().get_finished_notifications()
            for note in bg_notes:
                self.messages.append({"role": "user", "content": note})

            payload_messages = [{"role": "system", "content": sys_content}]
            payload_messages.extend(self.messages)
            self._attach_pending_images(payload_messages)

            if self.abort_requested:
                break

            step_stream_start = time.time()
            try:
                if self.current_provider == "local_gguf":
                    resp, think = self.gguf_loader.stream_chat(
                        messages=payload_messages,
                        temperature=gen_params.get("temperature", 0.3),
                        max_tokens=gen_params.get("max_tokens", 0),
                        on_token=on_token,
                        on_thinking=on_thinking,
                        model_filename=self.current_model_id,
                        is_aborted=lambda: self.abort_requested or step_tool_completed[0],
                        on_usage=_on_step_usage,
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
                        # Only a server-side-tools provider ever calls this; the
                        # shipped ones ask the model for a text tag and execute it
                        # themselves, so the hook stays silent for them.
                        on_tool=_on_server_tool,
                        on_usage=_on_step_usage,
                    )
            except Exception as stream_exc:
                if not step_chunks:
                    err_text = f"**Model connection error ({self.current_provider})**: {stream_exc}"
                    self.call_from_thread(assistant_card.update_content, err_text)
                    self._record_assistant_text(err_text)
                break
            step_stream_end = time.time()

            if first_token_time[0] > 0 and len(step_chunks) > 1:
                step_gen_duration = max(0.01, step_stream_end - first_token_time[0])
            else:
                step_gen_duration = max(0.01, step_stream_end - step_stream_start)

            step_think_text = ("".join(step_thinking) or (think if isinstance(think, str) else "") or "").strip()
            if step_think_text and not getattr(self, "_session_flushed_on_exit", False):
                self._transcript.append({"kind": "thinking", "text": step_think_text})

            if self.abort_requested:
                # Keep what was already generated: dropping it left a saved
                # session holding nothing but the user's prompt.
                partial = "".join(step_chunks) or resp or ""
                partial_clean = (self.agent_runner.strip_xml_tool_calls(partial)
                                 if self.agent_runner else partial).strip()
                if partial_clean and not getattr(self, "_session_flushed_on_exit", False):
                    self.messages.append({"role": "assistant", "content": partial_clean})
                    self._record_assistant_text(partial_clean)
                break

            self.call_from_thread(assistant_card.finish_thinking)

            step_text = "".join(step_chunks) or resp or ""

            step_p_tok = step_usage.get("prompt_tokens")
            if step_p_tok is None or int(step_p_tok) <= 0:
                step_p_tok = estimate_token_count(payload_messages)
            else:
                step_p_tok = int(step_p_tok)

            step_c_tok = step_usage.get("completion_tokens")
            if step_c_tok is None or int(step_c_tok) <= 0:
                step_c_tok = estimate_token_count([{"content": step_text + ("\n" + step_think_text if step_think_text else "")}])
            else:
                step_c_tok = int(step_c_tok)

            total_prompt_tokens += step_p_tok
            total_completion_tokens += step_c_tok
            total_tokens = total_prompt_tokens + total_completion_tokens
            total_gen_time += step_gen_duration

            self.stats["tokens_in"] = self.stats.get("tokens_in", 0) + step_p_tok
            self.stats["tokens_out"] = self.stats.get("tokens_out", 0) + step_c_tok
            self.stats["total_tokens"] = self.stats["tokens_in"] + self.stats["tokens_out"]
            if total_gen_time > 0:
                self.stats["tok_per_sec"] = total_completion_tokens / total_gen_time

            if not step_text.strip():
                if turn == 1 and not self.abort_requested:
                    self.call_from_thread(assistant_card.update_content, "*(Model returned an empty response. Check model connection or try another model in /models)*")
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
                    if step_rendered[0] > 0:
                        raw_tail = step_text[step_rendered[0]:]
                        tail_text = (self.agent_runner.strip_xml_tool_calls(raw_tail) if self.agent_runner else raw_tail).strip()
                        self.call_from_thread(assistant_card.update_last_body, tail_text or "")
                    else:
                        self.call_from_thread(assistant_card.update_last_body, clean_step)
                    if clean_step.strip():
                        self.messages.append({"role": "assistant", "content": clean_step})
                        self._record_assistant_text(clean_step.strip())
                    self.messages.append({
                        "role": "user",
                        "content": "⚠️ The tool call failed and your modifications were not applied. Do NOT give up or stop! You must call <tool:read> to inspect the file and <tool:edit> or <tool:write> to complete the task.",
                    })
                    self.call_from_thread(assistant_card.prepare_for_next_turn)
                    continue

                self.call_from_thread(assistant_card.clear_generating_tool)
                clean_step = self.agent_runner.strip_xml_tool_calls(step_text) if self.agent_runner else step_text
                if step_rendered[0] > 0:
                    raw_tail = step_text[step_rendered[0]:]
                    tail_text = (self.agent_runner.strip_xml_tool_calls(raw_tail) if self.agent_runner else raw_tail).strip()
                    self.call_from_thread(assistant_card.update_last_body, tail_text or "")
                    if tail_text:
                        self._record_assistant_text(tail_text)
                else:
                    self.call_from_thread(assistant_card.update_last_body, clean_step)
                    if clean_step.strip():
                        self._record_assistant_text(clean_step.strip())
                self.messages.append({"role": "assistant", "content": clean_step})

                # If the response was cut off abruptly mid-sentence, stopped right after
                # server-side tools without any concluding text, or only stated an intent
                # ("Sprawdzę najpierw..."), auto-continue rather than prematurely stopping:
                c_str = clean_step.strip()
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
                intent_only = (
                    bool(c_str)
                    and len(c_str) <= 360
                    and c_str.count("\n") <= 4
                    and (
                        c_str.endswith(":")
                        or any(
                            c_low.startswith(p) or f" {p} " in f" {c_low} "
                            for p in (
                                "sprawdzę najpierw",
                                "najpierw sprawdzę",
                                "zobaczę najpierw",
                                "najpierw zobaczę",
                                "przejrzę najpierw",
                                "najpierw przejrzę",
                                "zbadam",
                                "przeanalizuję",
                                "zaraz sprawdzę",
                                "zacznę od",
                                "let me check",
                                "let me look",
                                "let me inspect",
                                "let me examine",
                                "i will check",
                                "i'll check",
                                "i will inspect",
                                "i'll inspect",
                                "first, i'll",
                                "first, let me",
                            )
                        )
                    )
                )
                if (
                    (ends_abruptly or stopped_after_server_tool or (intent_only and turn <= 4))
                    and turn < 15
                    and not self.abort_requested
                ):
                    self.messages.append({
                        "role": "user",
                        "content": (
                            "Kontynuuj na podstawie wykonanych narzędzi i przedstaw pełną odpowiedź/plan "
                            "(nie zatrzymuj się na samym wstępie ani nie przerywaj po sprawdzeniu plików)."
                        ),
                    })
                    self.call_from_thread(assistant_card.prepare_for_next_turn)
                    continue

                break
            else:
                self.call_from_thread(assistant_card.clear_generating_tool)
                self.messages.append({"role": "assistant", "content": step_text})

                first_seg = segments[0] if segments else None
                first_text = first_seg[1] if (first_seg and first_seg[0] == "text") else ""
                if first_text:
                    self.call_from_thread(assistant_card.update_last_body, first_text)
                    self._record_assistant_text(first_text)

                remaining_segments = segments[1:] if (first_seg and first_seg[0] == "text") else segments

                idx = 0
                while idx < len(remaining_segments):
                    if self.abort_requested:
                        break
                    kind, data = remaining_segments[idx]
                    if kind == "text":
                        text_chunk = data
                        if first_text and text_chunk.strip() == first_text.strip():
                            idx += 1
                            continue
                        self.call_from_thread(assistant_card.append_text_block, text_chunk)
                        self._record_assistant_text(text_chunk)
                        self.call_from_thread(self._scroll_chat_to_end_if_enabled)
                        idx += 1
                    elif kind == "tool":
                        self.call_from_thread(assistant_card.finish_thinking)
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

                            by_role: Dict[str, List[Any]] = {}
                            for n, call_args in enumerate(batch, start=1):
                                self.stats["tools_run"] += 1
                                blk = self._new_subagent_block(
                                    assistant_card,
                                    role=str(call_args.get("name") or call_args.get("role") or "Subagent").strip()[:60] or "Subagent",
                                    goal=str(call_args.get("goal") or call_args.get("task") or call_args.get("description") or "").strip(),
                                    system=str(call_args.get("system") or call_args.get("system_prompt") or call_args.get("prompt") or "").strip(),
                                    index=n,
                                    total=len(batch),
                                )
                                if blk is not None:
                                    by_role.setdefault(blk.subagent_role, []).append(blk)

                            def _on_sub_progress(prog: Any, _by_role: Dict[str, List[Any]] = by_role) -> None:
                                for cand in _by_role.get(getattr(prog, "role", ""), []):
                                    if getattr(cand, "tool_running", False):
                                        self.call_from_thread(cand.set_subagent_progress, prog)
                                        return

                            def _sub_model_call(history: List[Dict[str, Any]], task: Any = None) -> str:
                                if self.abort_requested:
                                    return ""
                                sub_params = dict(gen_params)
                                sub_provider, sub_model = self._subagent_target()

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
                                    if task is None:
                                        return
                                    try:
                                        from ..agent.subagents.models import SubagentProgress
                                    except Exception:
                                        return
                                    role = str(getattr(task, "role", "") or "Subagent")
                                    clean_detail = str(detail or "").strip()
                                    if name.lower() == "read":
                                        for prefix in ("read file ", "reading file ", "read ", "reading "):
                                            if clean_detail.lower().startswith(prefix):
                                                clean_detail = clean_detail[len(prefix):].strip()
                                                break
                                    if phase not in ("running", "progress"):
                                        ok = (phase == "done")
                                        outcome = {
                                            "tool": name, "target": clean_detail,
                                            "result": ({"success": True, "content": detail, "stdout": detail, "result": detail}
                                                       if ok else
                                                       {"success": False, "error": detail or "failed"}),
                                            "ok": ok,
                                        }

                                        try:
                                            task.events.append(outcome)
                                        except Exception:
                                            pass
                                    try:
                                        _on_sub_progress(SubagentProgress(
                                            role=role, status="running",
                                            current_tool=name, tool_arg=clean_detail,
                                            goal=str(getattr(task, "goal", "") or ""),
                                            system_prompt=str(getattr(task, "system_prompt", "") or ""),
                                            history=list(history),
                                            events=list(getattr(task, "events", []) or []),
                                        ))
                                    except Exception:
                                        pass

                                if sub_provider == "local_gguf":
                                    txt, _ = self.gguf_loader.stream_chat(
                                        messages=history,
                                        temperature=sub_params.get("temperature", 0.3),
                                        max_tokens=sub_params.get("max_tokens", 0),
                                        model_filename=sub_model,
                                        is_aborted=lambda: self.abort_requested,
                                    )
                                else:
                                    # The subagent's own key, not the chat's: a
                                    # chat may be signed in as one provider and
                                    # delegating to another.
                                    sub_key = (api_key if sub_provider == self.current_provider
                                               else self.settings.get_api_key(sub_provider))
                                    txt, _ = stream_chat_completion(
                                        provider_id=sub_provider,
                                        model_id=sub_model,
                                        messages=history,
                                        api_key=sub_key,
                                        generation_params=sub_params,
                                        is_aborted=lambda: self.abort_requested,
                                        on_tool=_hook_server_tool,
                                    )
                                return txt or ""

                            sub_res = self.agent_runner.execute_subagents(
                                batch,
                                on_progress=_on_sub_progress,
                                is_local=self._subagent_target()[0] in ("local_gguf", "local", "llama_cpp", "ollama"),
                                model_call=_sub_model_call,
                            )
                            last_tool_failed[0] = not bool(sub_res.get("success", True))
                            for agent in sub_res.get("subagents", []):
                                for cand in by_role.get(agent.get("role", ""), []):
                                    if getattr(cand, "tool_running", False):
                                        self.call_from_thread(cand.finish_subagent, agent)
                                        break
                            self.messages.append({
                                "role": "user",
                                "content": self.agent_runner.format_tool_result_for_llm("subagent", sub_res),
                            })
                            # One transcript entry per marker, each carrying its
                            # own subagent's result. Recording the whole batch on
                            # every marker would make a reloaded session show N
                            # markers that all open the same full report.
                            for call_args, agent in zip(batch, _match_agents_to_calls(
                                    batch, sub_res.get("subagents", []))):
                                self._transcript.append({
                                    "kind": "subagent",
                                    "role": str(call_args.get("name") or call_args.get("role") or "Subagent").strip()[:60] or "Subagent",
                                    "goal": str(call_args.get("goal") or call_args.get("task") or call_args.get("description") or "").strip(),
                                    "system": str(call_args.get("system") or call_args.get("system_prompt") or call_args.get("prompt") or "").strip(),
                                    "agent": agent,
                                })
                            self.call_from_thread(assistant_card.clear_generating_tool)
                            self.call_from_thread(self.refresh)
                            idx = j
                            continue
                        else:
                            try:
                                res = self.agent_runner.execute_tool(tool_name, args)
                                last_tool_failed[0] = bool(res.get("error") or res.get("success") is False)
                                tool_feedback = self.agent_runner.format_tool_result_for_llm(tool_name, res)
                            except Exception as tool_exc:
                                res = {"success": False, "error": f"Tool execution exception: {tool_exc}"}
                                last_tool_failed[0] = True
                                tool_feedback = f'<tool_response name="{tool_name}">ERROR: {tool_exc}</tool_response>'
                            self.messages.append({"role": "user", "content": tool_feedback})
                            self.call_from_thread(self.refresh)
                            idx += 1

                if self.abort_requested:
                    break

                self.call_from_thread(assistant_card.prepare_for_next_turn)
                # Also without announcing "Thinking" here. This line ran at the
                # top of every agent turn, including the ones where the model
                # answers straight away, so the word appeared before any
                # reasoning existed. The thinking block and the input-bar marker
                # are both driven by what actually arrives now.
                self.call_from_thread(assistant_card.clear_generating_tool)

        elapsed = time.time() - start_time
        tok_s = (total_completion_tokens / total_gen_time) if total_gen_time > 0 else 0.0
        self.stats["tok_per_sec"] = tok_s

        if self.abort_requested:
            self.call_from_thread(assistant_card.clear_generating_tool)
            self.call_from_thread(assistant_card.abort)
        else:
            self.call_from_thread(assistant_card.clear_generating_tool)
            self.call_from_thread(
                assistant_card.finish,
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
            self._save_current_session()
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
        else:
            # No pending approval: the turn is done, so snapshot it now.
            # In code mode with file changes the checkpoint waits for the user
            # to accept the changes (see on_approval_action_selected).
            self.call_from_thread(self._create_turn_checkpoint)

        # A plan is only useful if it survives the turn. The checklist stays in
        # the agent context; CMDAIPLAN.md is what /plan shows and what the next
        # session picks up.
        saved = self._persist_plan_after_turn()
        if saved:
            self.call_from_thread(
                self.notify,
                f"Plan zapisany: {saved} krok(i) w CMDAIPLAN.md (/plan)",
                timeout=2.5,
            )

        self.is_generating = False
        self.abort_requested = False
