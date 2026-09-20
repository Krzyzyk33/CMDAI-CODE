import os
from typing import Dict, List, Optional
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from ...core.settings import get_settings

DEFAULT_SYSTEM_PROMPTS = [
    {
        "name": "Code Agent (Standard)",
        "text": "You are CMDAI CODE, an autonomous terminal AI programming agent running on Windows. Help the user analyze, write, debug, and test code accurately.",
    },
    {
        "name": "Senior Software Architect",
        "text": "You are a Senior Principal Software Engineer. Focus on modular system design, clean architecture, performance, security, and enterprise engineering best practices.",
    },
    {
        "name": "Code Reviewer & Security Analyst",
        "text": "You are an expert security and code reviewer. Analyze code for bugs, logic errors, edge cases, vulnerabilities, and optimization opportunities.",
    },
    {
        "name": "Concise Minimalist",
        "text": "Answer with extreme brevity and precision. Output code directly with minimal prose and zero unnecessary filler.",
    },
]


class SystemPromptEditScreen(ModalScreen[Optional[Dict[str, str]]]):
    """Editor modal for creating a new system prompt (Swift style)."""

    BINDINGS = [
        Binding("escape", "cancel_edit", "Cancel"),
        Binding("ctrl+s", "save_prompt", "Save"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("[bold white]New system prompt[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")
            yield Input(placeholder="Name", id="sp-name")
            yield TextArea(id="sp-text")
            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Save[/] ctrl+s   [dim]Cancel[/] esc", id="modal-footer-left")
                yield Static("", id="modal-footer-right")

    def on_mount(self) -> None:
        self.query_one("#sp-name", Input).focus()

    def action_cancel_edit(self) -> None:
        self.dismiss(None)

    def action_save_prompt(self) -> None:
        name = self.query_one("#sp-name", Input).value.strip()
        text = self.query_one("#sp-text", TextArea).text.strip()
        if not name or not text:
            self.query_one("#modal-footer-left", Static).update("[b #f85149]Name and text are required[/]")
            return
        self.dismiss({"name": name, "text": text})


class SystemPromptModal(ModalScreen[Optional[Dict[str, str]]]):
    """System prompt selector and manager matching Swift SystemPromptModal."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("enter", "select_item", "Select"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, active_name: str = "", **kwargs):
        super().__init__(**kwargs)
        self.active_name = active_name or "Code Agent (Standard)"
        self.settings = get_settings()
        stored = self.settings.config.get("system_prompts", [])
        if not stored:
            stored = list(DEFAULT_SYSTEM_PROMPTS)
            self.settings.config["system_prompts"] = stored
            self.settings.save_config()
        self.prompts: List[Dict[str, str]] = list(stored)
        self.filtered: List[Dict[str, str]] = list(self.prompts)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("[bold white]System prompt[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")
            yield Input(placeholder="Search prompts...", id="modal-search")
            yield OptionList(id="modal-list")
            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Select[/] enter   [dim]Cancel[/] esc", id="modal-footer-left")
                yield Static("[dim]ctrl+s in editor saves[/]", id="modal-footer-right")

    def on_mount(self) -> None:
        self._refresh_options()
        self.set_timer(0.05, lambda: self.query_one("#modal-search", Input).focus())

    def _refresh_options(self, query: str = "") -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.clear_options()

        q = query.lower().strip()
        self.filtered = [
            p for p in self.prompts
            if q in p["name"].lower() or q in p.get("text", "").lower()
        ]

        ol.add_option(Option("[b #58a6ff][+][/]  [bold white]New prompt…[/]", id="opt_new"))
        for idx, p in enumerate(self.filtered):
            bullet = "[b #3fb950]●[/] " if p["name"] == self.active_name else "  "
            preview = p.get("text", "")[:40].replace("\n", " ")
            label = f"{bullet}[b white]{p['name']:<24.24}[/]  [dim]{preview}...[/dim]"
            ol.add_option(Option(label, id=f"opt_{idx}"))

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

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_select_item(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            self.dismiss(None)
            return
        if idx == 0:
            self.app.push_screen(SystemPromptEditScreen(), self._on_edit_done)
        elif 1 <= idx <= len(self.filtered):
            self.dismiss(self.filtered[idx - 1])
        else:
            self.dismiss(None)

    def _on_edit_done(self, result: Optional[Dict[str, str]]) -> None:
        if result:
            self.prompts.insert(0, result)
            self.settings.config["system_prompts"] = self.prompts
            self.settings.save_config()
            self.active_name = result["name"]
            self.dismiss(result)
