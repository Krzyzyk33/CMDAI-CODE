import os
from typing import List, Tuple
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class PlanModal(ModalScreen[None]):
    """Clean, minimalist project plan checklist modal (models-style) managing CMDAIPLAN.md."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("space", "toggle_task", "Toggle"),
        Binding("enter", "toggle_task", "Toggle"),
        Binding("delete", "delete_task", "Delete"),
        Binding("a", "focus_add", "Add"),
    ]

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.plan_path = os.path.join(self.workdir, "CMDAIPLAN.md")
        self.tasks: List[Tuple[bool, str]] = []
        self._load_plan()

    def _load_plan(self) -> None:
        self.tasks = []
        if os.path.exists(self.plan_path):
            try:
                with open(self.plan_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line_s = line.strip()
                        if line_s.startswith("- [ ]") or line_s.startswith("* [ ]"):
                            self.tasks.append((False, line_s[5:].strip()))
                        elif line_s.startswith("- [x]") or line_s.startswith("- [X]") or line_s.startswith("* [x]"):
                            self.tasks.append((True, line_s[5:].strip()))
            except Exception:
                pass

        if not self.tasks:
            self.tasks = [
                (False, "Explore project architecture and codebase"),
                (False, "Implement requested features and enhancements"),
                (False, "Run automated tests and verify build"),
                (False, "Review git diff and commit changes"),
                (False, "Configure terminal code editor in Swift style"),
                (False, "Test all slash commands and bindings"),
                (False, "Verify context inspector and memory limits"),
                (False, "Synchronize updates across E: and D: drives"),
            ]

    def _save_plan(self) -> None:
        try:
            lines = ["# CMDAIPLAN\n", "\n## Tasks\n"]
            for done, title in self.tasks:
                marker = "x" if done else " "
                lines.append(f"- [{marker}] {title}\n")
            with open(self.plan_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="plan-modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("[bold white]Project Plan[/]  [dim]CMDAIPLAN.md[/dim]", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            yield OptionList(id="plan-list")
            yield Input(placeholder="Add new task (Enter to save)...", id="plan-new-input")

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
                label = f"  [b #58a6ff]○[/]  [bold white]{title}[/]"
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
            self._save_plan()
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
            self._save_plan()
            self._refresh_list()

    def action_delete_task(self) -> None:
        ol = self.query_one("#plan-list", OptionList)
        idx = ol.highlighted
        if idx is not None and 0 <= idx < len(self.tasks):
            self.tasks.pop(idx)
            self._save_plan()
            self._refresh_list()
            if self.tasks:
                ol.highlighted = min(idx, len(self.tasks) - 1)

    def action_focus_add(self) -> None:
        self.query_one("#plan-new-input", Input).focus()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
