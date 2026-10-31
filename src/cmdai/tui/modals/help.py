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
    "/models": "Open model picker and switch engines (Local + 33 API providers)",
    "/modelinfo": "Details of the currently loaded model: arch, context, thinking support",
    "/summarize": "Summarize conversation history into compact context handoff",
    "/params": "View or set generation parameters: temp, top_p, max_tokens, reasoning level",
    "/sessions": "Browse, reopen or delete auto-saved conversations",
    "/system": "Pick, create or switch system prompt",
    "/settings": "Configure server port, auto-copy, exit confirmation, launcher",
    "/loader": "Select GGUF backend loader (CPU / CUDA / Vulkan)",
    "/stats": "Session statistics: tokens, response speeds, tool counts",
    "/git": "Open the git window in a new terminal (changed files, diff, commit)",
    "/branches": "Open the branch list in a new terminal (checkout / fork)",
    "/diff": "Review working changes in a modal (read-only, opens commit)",
    "/editor": "Open terminal code editor (VS Code style) in a new window",
    "/test": "Auto-detect and run test suite with live output",
    "/commit": "Write a commit message and commit the staged changes in a modal",
    "/review": "Run AI code review on current uncommitted changes",
    "/plan": "Minimalist task checklist and plan manager (CMDAIPLAN.md)",
    "/implement": "Switch to code mode and execute the plan checklist (alias: /wdroz)",
    "/debug": "Fast project-wide AST scan for Python/JSON syntax errors (alias: /bugs)",
    "/index": "Scan and index project symbols into SQLite FTS5 database",
    "/context": "Token budget, context breakdown and interactive workspace file picker",
    "/export": "Export current session with thinking & tools to interactive HTML file",
    "/add": "Open Windows Explorer or pin files to conversation context",
    "/drop": "Remove pinned files from conversation context",
    "/cd": "Change current working directory (/cd <path>)",
    "/mode": "Toggle/cycle agent execution mode (auto / plan / ask)",
    "/thinking": "Show or set the thinking level. No argument cycles it, or name one: /thinking max",
    "/changelog": "Show GitHub releases changelog (>= v3.0-alpha)",
    "/update": "Pull the latest code - same as `cmdai code update`",
    "/updatesettings": "Automatic updates: on/off, check interval, check or update now",
    "/mcp": "MCP servers status (/mcp restart|logs <name>)",
    "/quit": "Quit CMDAI CODE",
}

SHORTCUTS_DOC: Dict[str, Dict[str, str]] = {
    "Ctrl+C": {
        "title": "Cancel / Interrupt",
        "desc": "Interrupt active model generation or terminate running process",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Ctrl+C[/]\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Interrupt Generation / Cancel Command[/]\n\n"
            "[bold white]Description:[/] Immediately aborts active model generation, stops live streamed tokens, "
            "or sends a cancellation interrupt to any running terminal command or background task."
        ),
    },
    "Escape": {
        "title": "Cancel / Abort / Close",
        "desc": "Abort generation (press twice), dismiss modal dialog, or unfocus",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Escape[/] (or [b #58a6ff]Esc Esc[/])\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Abort Generation / Dismiss Modal[/]\n\n"
            "[bold white]Description:[/] Press Escape to dismiss the current modal dialog or cancel autocomplete suggestions. "
            "Pressing Escape twice during generation stops the current turn safely without losing history."
        ),
    },
    "Shift+Tab": {
        "title": "Cycle Agent Mode",
        "desc": "Cycle execution mode between auto, plan, and ask",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Shift+Tab[/] (or [b #58a6ff]Backtab[/])\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Cycle Agent Execution Mode[/]\n\n"
            "[bold white]Description:[/] Cycles the autonomous mode:\n"
            "  • [b #7ee787]auto[/]: The agent executes file modifications and tool steps autonomously.\n"
            "  • [b #d29922]plan[/]: The agent creates and refines execution plans before applying code edits.\n"
            "  • [b #58a6ff]ask[/]: The agent requests interactive user approval before running write tools."
        ),
    },
    "Ctrl+T": {
        "title": "Cycle Thinking Level",
        "desc": "Cycle reasoning level: off, low, medium, high, max",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Ctrl+T[/]\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Cycle Reasoning / Thinking Effort[/]\n\n"
            "[bold white]Description:[/] For reasoning-capable models (e.g. Gemini 2.5, Claude 3.7, o1/o3, deepseek-r1), "
            "cycles the thinking effort between [dim]off[/], [b #58a6ff]low[/], [b #58a6ff]medium[/], [b #58a6ff]high[/], and [b #58a6ff]max[/]."
        ),
    },
    "Ctrl+B": {
        "title": "Send to Background",
        "desc": "Detach running command or task into background process",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Ctrl+B[/]\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Send Command to Background[/]\n\n"
            "[bold white]Description:[/] Detaches a long-running terminal command (e.g. dev servers, builds) to continue "
            "executing as an asynchronous background task so you can continue chatting."
        ),
    },
    "Enter": {
        "title": "Submit / Execute",
        "desc": "Send prompt message or execute selected slash command",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Enter[/]\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Submit Prompt or Execute Command[/]\n\n"
            "[bold white]Description:[/] Sends the typed message to the agent, executes the highlighted slash command, "
            "or confirms choices in active modals and dialogs."
        ),
    },
    "Shift+Enter": {
        "title": "Insert Newline",
        "desc": "Add a new line in the prompt editor without sending",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Shift+Enter[/]\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Insert Newline in Prompt[/]\n\n"
            "[bold white]Description:[/] Inserts a line break into the prompt box, allowing you to compose formatted, multi-line instructions."
        ),
    },
    "Up / Down": {
        "title": "Navigate Hints / History",
        "desc": "Browse autocomplete command hints or previous inputs",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Up / Down[/]\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Navigate Autocomplete Hints / History[/]\n\n"
            "[bold white]Description:[/] When slash command hints are shown, moves selection up/down. Otherwise navigates through prompt history."
        ),
    },
    "PageUp / PageDown": {
        "title": "Page Scroll Chat",
        "desc": "Scroll conversation viewport up or down by one full page",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]PageUp / PageDown[/]\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Page Scroll Chat[/]\n\n"
            "[bold white]Description:[/] Scrolls the conversation viewport up or down by a full screen page."
        ),
    },
    "Home / End": {
        "title": "Jump to Start / End of Chat",
        "desc": "Scroll directly to the beginning or latest message in chat",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Home / End[/]\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Jump to Beginning / End of History[/]\n\n"
            "[bold white]Description:[/] Home jumps to the very first turn of the conversation; End scrolls to the most recent turn and resumes auto-scroll."
        ),
    },
    "Ctrl+Up / Ctrl+Down": {
        "title": "Scroll Chat Line by Line",
        "desc": "Smooth scroll conversation history up or down one line (also Shift+Up/Down)",
        "detail": (
            "[bold white]Shortcut:[/] [b #58a6ff]Ctrl+Up / Ctrl+Down[/] (or [b #58a6ff]Shift+Up / Shift+Down[/])\n\n"
            "[bold white]Action:[/] [bold #e6edf3]Scroll Line by Line[/]\n\n"
            "[bold white]Description:[/] Nudges the chat viewport up or down by a single line for precise reading."
        ),
    },
}

HELP_CATEGORIES: List[Tuple[str, List[str]]] = [
    ("Chat & Sessions", ["/new", "/help", "/export", "/summarize", "/sessions", "/stats"]),
    ("Code Agent & Git", ["/editor", "/git", "/diff", "/commit", "/test", "/review", "/plan", "/implement", "/debug", "/index", "/mode"]),
    ("Context & Files", ["/cd", "/add", "/drop", "/context"]),
    ("Model & Reasoning", ["/models", "/modelinfo", "/params", "/system", "/thinking"]),
    ("Configuration & Engine", ["/settings", "/loader", "/update", "/updatesettings", "/changelog", "/mcp", "/quit"]),
    ("Keyboard Shortcuts", list(SHORTCUTS_DOC.keys())),
]


class HelpCommandsModal(ModalScreen[Optional[str]]):

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
                yield Static("[dim]esc[/]", id="modal-esc")
            is_sc = (self.category == "Keyboard Shortcuts")
            placeholder = "Search shortcuts..." if is_sc else "Search commands..."
            yield Input(placeholder=placeholder, id="modal-search")
            yield OptionList(id="modal-list")
            with Horizontal(id="modal-footer"):
                action_label = "View" if is_sc else "Run"
                desc_label = "select shortcut to view details" if is_sc else "select command to execute"
                yield Static(f"[b #58a6ff]{action_label}[/] enter", id="modal-footer-left")
                yield Static(f"[dim]{desc_label}[/]", id="modal-footer-right")

    def on_mount(self) -> None:
        self._refresh_options()
        self.set_timer(0.05, lambda: self.query_one("#modal-search", Input).focus())

    def _refresh_options(self, query: str = "") -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.clear_options()
        q = query.lower().strip()
        is_sc = (self.category == "Keyboard Shortcuts")
        if self.category:
            pool = [(cat, c) for cat, c in self._all_commands if cat == self.category]
        else:
            pool = self._all_commands

        if is_sc:
            self.filtered = [
                (cat, c) for cat, c in pool
                if q in c.lower() or q in SHORTCUTS_DOC.get(c, {}).get("title", "").lower()
                or q in SHORTCUTS_DOC.get(c, {}).get("desc", "").lower()
            ]
        else:
            self.filtered = [
                (cat, c) for cat, c in pool
                if q in c.lower() or q in COMMANDS_DOC.get(c, "").lower()
            ]

        if not self.filtered:
            ol.add_option(Option("[dim](no matching items)[/dim]", id="opt_none"))
        else:
            prev_cat = None
            for idx, (cat, c) in enumerate(self.filtered):
                if cat != prev_cat and not self.category:
                    ol.add_option(Option(f"[dim]{cat}[/]", id=f"cat_{cat}"))
                    prev_cat = cat
                if is_sc:
                    sc_data = SHORTCUTS_DOC.get(c, {})
                    desc = sc_data.get("desc", "")
                    ol.add_option(Option(f"  [b #58a6ff]{c:<22}[/]  [dim]{desc}[/]", id=f"sc_{c}"))
                else:
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
        if opt and opt.id:
            if opt.id.startswith("sc_"):
                sc_key = opt.id[3:]
                sc_info = SHORTCUTS_DOC.get(sc_key, {})
                title = f"Keyboard Shortcut: {sc_key}"
                detail_text = sc_info.get("detail", f"Shortcut: {sc_key}")
                self.app.push_screen(InfoModal(title=title, body=detail_text))
            elif opt.id.startswith("hc_"):
                self.dismiss(opt.id[3:])


class HelpModal(ModalScreen[Optional[str]]):

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
                yield Static("[dim]esc[/]", id="modal-esc")
            yield OptionList(id="modal-list")
            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Open[/] enter", id="modal-footer-left")
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
                yield Static("[dim]esc[/]", id="modal-esc")
            with VerticalScroll(id="modal-scroll"):
                yield Static(self._body, id="modal-body")

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
