"""Agent tools reference: a flat list (like /help) that opens a stacked window.

The list stays free of raw invocations - clicking a tool opens a second
window of the same size with the tool name, its syntax and an example.
"""

from typing import Optional

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core.tools_catalog import TOOL_DOCS, ToolDoc


class ToolNoteModal(ModalScreen[None]):
    """Tool name plus the way the model calls it."""

    BINDINGS = [
        Binding("escape", "close", "Back", priority=True),
        Binding("enter", "close", "Back", show=False),
    ]

    def __init__(self, doc: ToolDoc, **kwargs):
        super().__init__(**kwargs)
        self.doc = doc

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="tool-note-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"[bold #58a6ff]{self.doc.name}[/]", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            yield Static(f"[dim]{self.doc.summary}[/]", id="releases-hint")
            with VerticalScroll(id="releases-preview"):
                t = Text()
                t.append("Syntax\n", style="bold #8b949e")
                t.append(f"  {self.doc.syntax}\n\n", style="#7ee787")
                t.append("Example\n", style="bold #8b949e")
                t.append(f"  {self.doc.example}\n", style="#c9d1d9")
                yield Static(t, id="releases-body")

    def action_close(self) -> None:
        self.dismiss(None)


class ToolsModal(ModalScreen[None]):
    """One line per tool, exactly like the help window."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "open_tool", "Open"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="tools-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Agent Tools", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            yield OptionList(id="tools-list")
            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Open[/] enter   [dim]tool call syntax[/dim]", id="modal-footer-left")
                yield Static("", id="modal-footer-right")

    def on_mount(self) -> None:
        ol = self.query_one("#tools-list", OptionList)
        for doc in TOOL_DOCS:
            summary = doc.summary if len(doc.summary) <= 46 else doc.summary[:45].rstrip() + "…"
            ol.add_option(Option(f"  [b #58a6ff]{doc.name:<9}[/][dim]{summary}[/]"))
        ol.highlighted = 0
        ol.focus()

    def _index(self) -> Optional[int]:
        return self.query_one("#tools-list", OptionList).highlighted

    def action_cursor_up(self) -> None:
        self.query_one("#tools-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#tools-list", OptionList).action_cursor_down()

    def action_open_tool(self) -> None:
        idx = self._index()
        if idx is not None and 0 <= idx < len(TOOL_DOCS):
            self.app.push_screen(ToolNoteModal(TOOL_DOCS[idx]))

    def action_close(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#tools-list")
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_open_tool()
