"""Clean, minimalist project tasks / TODO checklist modal.

Manages the autonomous agent checklist in-memory and in session state.
Does NOT create or write CMDAIPLAN.md to disk.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class PlanModal(ModalScreen[None]):
    """Clean, minimalist project tasks / TODO checklist modal."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("space", "toggle_task", "Toggle"),
        Binding("enter", "toggle_task", "Toggle"),
        Binding("delete", "delete_task", "Delete"),
        Binding("a", "focus_add", "Add"),
    ]

    def __init__(
        self,
        tasks: Optional[List[Tuple[bool, str]]] = None,
        on_change: Optional[Callable[[List[Tuple[bool, str]]], None]] = None,
        workdir: str = ".",
        **kwargs
    ):
        super().__init__(**kwargs)
        self.workdir = workdir
        self.on_change = on_change
        self.tasks: List[Tuple[bool, str]] = list(tasks or [])
        if not self.tasks:
            self.tasks = [
                (False, "Explore project architecture and codebase"),
                (False, "Implement requested features and enhancements"),
                (False, "Run automated tests and verify build"),
                (False, "Review git diff and commit changes"),
            ]

    def _sync_change(self) -> None:
        if self.on_change:
            try:
                self.on_change(self.tasks)
            except Exception:
                pass

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="plan-modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("[bold white]Project Tasks[/]  [dim]TODO[/dim]", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")

            yield OptionList(id="plan-list")
            yield Input(
                placeholder="Add a task manually (Enter to save)...",
                id="plan-new-input",
                select_on_focus=False,
            )

            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Toggle[/] enter   [b #f85149]Delete[/] del   [dim]Add[/] a", id="modal-footer-left")
                done_cnt = sum(1 for d, _ in self.tasks if d)
                yield Static(f"[dim]{done_cnt}/{len(self.tasks)}[/dim]", id="modal-footer-right")

    def on_mount(self) -> None:
        self._refresh_list()
        self.query_one("#plan-list", OptionList).focus()

    def _refresh_list(self) -> None:
        ol = self.query_one("#plan-list", OptionList)
        prev_highlight = ol.highlighted
        ol.clear_options()

        for idx, (done, title) in enumerate(self.tasks):
            if done:
                label = f"  [b #3fb950]●[/]  [dim]{title}[/]"
            else:
                label = f"  [#58a6ff]○[/]  [#8b949e]{title}[/]"
            ol.add_option(Option(label, id=f"task_{idx}"))

        if prev_highlight is not None and 0 <= prev_highlight < len(self.tasks):
            ol.highlighted = prev_highlight
        elif len(self.tasks) > 0:
            ol.highlighted = 0

        try:
            done_cnt = sum(1 for d, _ in self.tasks if d)
            self.query_one("#modal-footer-right", Static).update(f"[dim]{done_cnt}/{len(self.tasks)}[/dim]")
        except Exception:
            pass

    @on(Input.Submitted, "#plan-new-input")
    def on_add_task(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        if val:
            self.tasks.append((False, val))
            self._sync_change()
            self._refresh_list()
            event.input.value = ""
            ol = self.query_one("#plan-list", OptionList)
            ol.highlighted = len(self.tasks) - 1
            ol.focus()

    @on(OptionList.OptionSelected, "#plan-list")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_toggle_task()

    def action_toggle_task(self) -> None:
        ol = self.query_one("#plan-list", OptionList)
        idx = ol.highlighted
        if idx is not None and 0 <= idx < len(self.tasks):
            done, title = self.tasks[idx]
            self.tasks[idx] = (not done, title)
            self._sync_change()
            self._refresh_list()

    def action_delete_task(self) -> None:
        ol = self.query_one("#plan-list", OptionList)
        idx = ol.highlighted
        if idx is not None and 0 <= idx < len(self.tasks):
            self.tasks.pop(idx)
            self._sync_change()
            self._refresh_list()
            if self.tasks:
                ol.highlighted = min(idx, len(self.tasks) - 1)

    def action_focus_add(self) -> None:
        try:
            self.query_one("#plan-new-input", Input).focus()
        except Exception:
            pass

    def action_dismiss_modal(self) -> None:
        self.dismiss(self.tasks)
