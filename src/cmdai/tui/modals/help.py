from typing import Any, Dict, List, Optional, Tuple
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

COMMANDS_DOC: Dict[str, str] = {
    "/help": "Browse commands by category — enter opens, enter runs",
    "/new": "Start a fresh conversation — previous stays saved in /sessions",
    "/models": "Open model picker and switch engines (Local + 23 API providers)",
    "/modelinfo": "Details of the currently loaded model: arch, context, thinking support",
    "/summarize": "Summarize conversation history into compact context handoff",
    "/agent": "Toggle CMDAI Code Agent mode (file tools, terminal loop) on/off",
    "/params": "View or set generation parameters: temp, top_p, max_tokens, reasoning level",
    "/sessions": "Browse, reopen or delete auto-saved conversations",
    "/system": "Pick, create or switch system prompt",
    "/settings": "Configure server port, auto-copy, exit confirmation, launcher",
    "/loader": "Select GGUF backend loader (CPU / CUDA / Vulkan)",
    "/stats": "Session statistics: tokens, response speeds, tool counts",
    "/diff": "Open git diff in a new terminal window",
    "/editor": "Open terminal code editor (VS Code style) in a new window",
    "/undo": "Safe rollback of files modified during the session",
    "/test": "Auto-detect and run test suite with live output",
    "/commit": "Open git commit in a new terminal window",
    "/review": "Run AI code review on current uncommitted changes",
    "/plan": "Minimalist task checklist and plan manager (CMDAIPLAN.md)",
    "/debug": "Fast project-wide AST scan for Python/JSON syntax errors (alias: /bugs)",
    "/index": "Scan and index project symbols into SQLite FTS5 database",
    "/context": "Token budget, context breakdown and interactive workspace file picker",
    "/export": "Export current session with thinking & tools to interactive HTML file",
    "/add": "Open Windows Explorer or pin files to conversation context",
    "/drop": "Remove pinned files from conversation context",
    "/cd": "Change current working directory (/cd <path>)",
    "/mode": "Toggle/cycle agent execution mode (auto / plan / ask)",
    "/thinking": "Cycle/set thinking reasoning level (Off / On / Low / Med / High) if supported",
    "/quit": "Quit CMDAI CODE",
}

HELP_CATEGORIES: List[Tuple[str, List[str]]] = [
    ("Chat & Sessions", ["/new", "/help", "/export", "/summarize", "/sessions", "/stats"]),
    ("Code Agent & Git", ["/editor", "/diff", "/commit", "/undo", "/test", "/review", "/plan", "/debug", "/index", "/agent", "/mode"]),
    ("Context & Files", ["/cd", "/add", "/drop", "/context"]),
    ("Model & Reasoning", ["/models", "/modelinfo", "/params", "/system", "/thinking"]),
    ("Configuration & Engine", ["/settings", "/loader", "/quit"]),
]


class HelpCommandsModal(ModalScreen[Optional[str]]):
    """Commands viewer for selected category or all commands with search (Swift style)."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "select_item", "Run"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, category: str = "", **kwargs):
        super().__init__(**kwargs)
        self.category = category
        self._all_commands: List[Tuple[str, str]] = []
        for cat_name, cmds in HELP_CATEGORIES:
            for c in cmds:
                self._all_commands.append((cat_name, c))
        self.filtered = list(self._all_commands)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                title = f"Help — {self.category}" if self.category else "All commands"
                yield Static(f"[bold white]{title}[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")
            yield Input(placeholder="Search commands...", id="modal-search")
            yield OptionList(id="modal-list")
            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Run[/] enter   [dim]Back[/] esc", id="modal-footer-left")
                yield Static("[dim]select command to execute[/]", id="modal-footer-right")

    def on_mount(self) -> None:
        self._refresh_options()
        self.set_timer(0.05, lambda: self.query_one("#modal-search", Input).focus())

    def _refresh_options(self, query: str = "") -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.clear_options()
        q = query.lower().strip()
        if self.category:
            pool = [(cat, c) for cat, c in self._all_commands if cat == self.category]
        else:
            pool = self._all_commands

        self.filtered = [
            (cat, c) for cat, c in pool
            if q in c.lower() or q in COMMANDS_DOC.get(c, "").lower()
        ]
        if not self.filtered:
            ol.add_option(Option("[dim](no matching commands)[/dim]", id="opt_none"))
        else:
            prev_cat = None
            for idx, (cat, c) in enumerate(self.filtered):
                if cat != prev_cat and not self.category:
                    ol.add_option(Option(f"[dim]{cat}[/]", id=f"cat_{cat}"))
                    prev_cat = cat
                desc = COMMANDS_DOC.get(c, "")
                ol.add_option(Option(f"  [b #58a6ff]{c:<12}[/]  [dim]{desc}[/]", id=f"hc_{c}"))
            ol.highlighted = 0

    @on(Input.Changed, "#modal-search")
    def on_search_changed(self, event: Input.Changed) -> None:
        self._refresh_options(event.value)

    @on(Input.Submitted, "#modal-search")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        self.action_select_item()

    @on(OptionList.OptionSelected, "#modal-list")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_select_item()

    def action_cursor_up(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_down()

    def action_back(self) -> None:
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_select_item(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            return
        try:
            opt = ol.get_option_at_index(idx)
        except Exception:
            return
        if opt and opt.id and opt.id.startswith("hc_"):
            self.dismiss(opt.id[3:])


class HelpModal(ModalScreen[Optional[str]]):
    """Categorized help browser modal matching Swift HelpModal."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("enter", "select_item", "Open"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("[bold white]Help[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")
            yield OptionList(id="modal-list")
            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Open[/] enter   [dim]Close[/] esc", id="modal-footer-left")
                yield Static("[dim]command categories[/]", id="modal-footer-right")

    def on_mount(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.clear_options()
        for cat_name, cmds in HELP_CATEGORIES:
            preview = "  ".join(cmds[:4])
            if len(cmds) > 4:
                preview += f"  ... ({len(cmds)} cmds)"
            ol.add_option(
                Option(f"  [b #58a6ff]{cat_name:<20}[/] [dim]{preview}[/]", id=f"cat_{cat_name}")
            )
        ol.highlighted = 0
        self.set_timer(0.05, lambda: ol.focus())

    @on(OptionList.OptionSelected, "#modal-list")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_select_item()

    def action_cursor_up(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_down()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_select_item(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is None or not (0 <= idx < len(HELP_CATEGORIES)):
            return
        cat_name = HELP_CATEGORIES[idx][0]
        self.app.push_screen(HelpCommandsModal(cat_name), self._on_command_chosen)

    def _on_command_chosen(self, cmd: Optional[str]) -> None:
        if cmd:
            self.dismiss(cmd)


class InfoModal(ModalScreen[None]):
    """Generic informative modal screen matching Swift InfoModal."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("enter", "dismiss_modal", "Close", show=False),
    ]

    def __init__(self, title: str, body: str, **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"[bold white]{self._title}[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")
            with VerticalScroll(id="modal-scroll"):
                yield Static(self._body, id="modal-body")
            with Horizontal(id="modal-footer"):
                yield Static("[dim]Close[/] esc", id="modal-footer-left")
                yield Static("", id="modal-footer-right")

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
