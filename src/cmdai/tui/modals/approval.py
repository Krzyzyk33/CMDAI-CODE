import difflib
import os
from typing import Any, Dict, List, Optional

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from .diff import DiffModal


class EditInspectModal(ModalScreen[str]):
    """Modal dialog displaying proposed file changes in Code mode, with file tree and Diff button in the bottom right."""

    BINDINGS = [
        Binding("escape", "reject", "Reject"),
        Binding("ctrl+c", "handle_interrupt", "Cancel", show=False),
        Binding("enter", "allow", "Allow"),
        Binding("y", "allow", "Allow", show=False),
        Binding("n", "reject", "Reject", show=False),
        Binding("d", "open_diff", "View Diff"),
    ]

    DEFAULT_CSS = """
    EditInspectModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }
    #inspect-dialog {
        width: 86;
        height: 28;
        background: #0d1117;
        border: solid #30363d;
        padding: 1 2;
    }
    #inspect-header {
        height: 3;
        width: 100%;
        border-bottom: solid #30363d;
        align-vertical: middle;
    }
    #inspect-title {
        width: 1fr;
        color: #e6edf3;
        text-style: bold;
    }
    #inspect-esc {
        width: auto;
        color: #8b949e;
    }
    #inspect-banner {
        height: auto;
        padding: 1 0;
        color: #e6edf3;
    }
    #inspect-file-list {
        height: 6;
        background: #161b22;
        border: solid #21262d;
        margin-bottom: 1;
    }
    #inspect-preview-scroll {
        height: 1fr;
        background: #090d13;
        border: solid #21262d;
        padding: 0 1;
    }
    #inspect-action-bar {
        height: 3;
        width: 100%;
        margin-top: 1;
        align-vertical: middle;
    }
    #inspect-action-left {
        width: 1fr;
        height: auto;
        align-vertical: middle;
    }
    #inspect-action-right {
        width: auto;
        height: auto;
        align-vertical: middle;
    }
    .inspect-btn {
        margin-right: 1;
    }
    """

    def __init__(self, details: Dict[str, Any], workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.details = details
        self.workdir = os.path.abspath(workdir)
        self.action = details.get("action", "edit")
        self.target = details.get("path") or details.get("cmd") or "action"
        self.diff_info = details.get("diff_info", "")
        self.file_count = details.get("file_count", 1)
        self.diff_text = self._build_diff_text()

    def _build_diff_text(self) -> str:
        old = self.details.get("old", "")
        new = self.details.get("new", "")
        content = self.details.get("content", "")

        if self.action == "edit" and (old or new):
            old_lines = old.splitlines(keepends=True)
            new_lines = new.splitlines(keepends=True)
            diff = list(difflib.unified_diff(
                old_lines, new_lines,
                fromfile=f"a/{self.target}",
                tofile=f"b/{self.target}",
            ))
            return "".join(diff) if diff else "(No differences found)"
        elif self.action == "write" and content:
            return f"--- /dev/null\n+++ b/{self.target}\n" + "".join([f"+{line}\n" for line in content.splitlines()[:200]])
        elif self.action == "command":
            return f"$ {self.target}"
        return "(Preview unavailable)"

    def compose(self) -> ComposeResult:
        with Vertical(id="inspect-dialog"):
            with Horizontal(id="inspect-header"):
                yield Static("[bold white]Execution Approval Request[/] [dim](Code Mode)[/dim]", id="inspect-title")
                yield Static("[esc]", id="inspect-esc")

            act_color = "#d29922" if self.action == "edit" else ("#3fb950" if self.action == "write" else "#58a6ff")
            banner_text = (
                f"[b {act_color}]● Tool: {self.action.upper()}[/]  "
                f"[bold white]{self.target}[/]  "
                f"[dim]({self.diff_info} · {self.file_count} file affected)[/dim]"
            )
            yield Static(banner_text, id="inspect-banner")

            ol = OptionList(id="inspect-file-list")
            base = os.path.basename(self.target)
            parent = os.path.dirname(self.target).replace("\\", "/")
            badge = "[b #d29922]M[/]" if self.action == "edit" else "[b #3fb950]+[/]"
            ol.add_option(Option(f" {badge} [bold white]{base}[/] [dim]({parent or '.'})  {self.diff_info}[/]"))
            yield ol

            with VerticalScroll(id="inspect-preview-scroll"):
                t = Text()
                for line in self.diff_text.splitlines():
                    if line.startswith("+"):
                        t.append(line + "\n", style="#7ee787")
                    elif line.startswith("-"):
                        t.append(line + "\n", style="#f85149")
                    elif line.startswith("@"):
                        t.append(line + "\n", style="#58a6ff")
                    else:
                        t.append(line + "\n", style="#8b949e")
                yield Static(t, id="inspect-preview-content")

            with Horizontal(id="inspect-action-bar"):
                with Horizontal(id="inspect-action-left"):
                    yield Button("Allow (y)", id="inspect-btn-allow", variant="success", classes="inspect-btn")
                    yield Button("Reject (n)", id="inspect-btn-reject", variant="error", classes="inspect-btn")
                with Horizontal(id="inspect-action-right"):
                    yield Button("Diff (d)", id="inspect-btn-diff", variant="primary", classes="inspect-btn")

    @on(Button.Pressed, "#inspect-btn-allow")
    def on_allow_clicked(self) -> None:
        self.action_allow()

    @on(Button.Pressed, "#inspect-btn-reject")
    def on_reject_clicked(self) -> None:
        self.action_reject()

    @on(Button.Pressed, "#inspect-btn-diff")
    def on_diff_clicked(self) -> None:
        self.action_open_diff()

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_reject(self) -> None:
        self.dismiss("reject")

    def action_open_diff(self) -> None:
        def _on_diff_dismiss(result: Optional[str]) -> None:
            if result in ("allow", "reject"):
                self.dismiss(result)

        self.app.push_screen(
            DiffModal(
                workdir=self.workdir,
                single_file=self.target,
                single_diff=self.diff_text,
                single_title=f"Diff: {self.target}",
            ),
            _on_diff_dismiss,
        )

    def action_handle_interrupt(self) -> None:
        self.action_reject()

    def action_action_allow(self) -> None:
        self.action_allow()

    def action_action_reject(self) -> None:
        self.action_reject()

    def action_action_open_diff(self) -> None:
        self.action_open_diff()
