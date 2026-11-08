import difflib
import os
from typing import Any, Dict, List, Optional

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .diff import DiffModal


class EditInspectModal(ModalScreen[str]):

    BINDINGS = [
        Binding("escape", "reject", "Reject"),
        Binding("ctrl+c", "handle_interrupt", "Cancel", show=False),
        Binding("enter", "allow", "Allow"),
        Binding("y", "allow", "Allow", show=False),
        Binding("n", "reject", "Reject", show=False),
        Binding("d", "open_diff", "View Diff"),
    ]

    # NOTE: widget-level CSS (not DEFAULT_CSS) - a Button's own DEFAULT_CSS
    # would otherwise keep its `border: tall` and collapse a 1-row button.
    CSS = """
    EditInspectModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }
    #inspect-dialog {
        width: 76;
        max-width: 95%;
        height: auto;
        background: #0d1117;
        border: none;
        padding: 1 2;
    }
    #inspect-header {
        height: 1;
        width: 100%;
        margin-bottom: 1;
    }
    #inspect-title {
        width: 1fr;
        color: #e6edf3;
        text-style: bold;
    }
    #inspect-esc {
        width: auto;
        color: #6e7681;
    }
    #inspect-banner {
        height: auto;
        color: #e6edf3;
    }
    #inspect-action-bar {
        height: 1;
        width: 100%;
        margin-top: 1;
        align-horizontal: right;
        align-vertical: middle;
    }
    #inspect-action-right {
        width: auto;
        height: 1;
        align-vertical: middle;
    }
    .inspect-btn {
        height: 1;
        min-height: 1;
        width: auto;
        min-width: 8;
        border: none !important;
        padding: 0 2;
        text-style: bold;
        margin-right: 1;
    }
    #inspect-btn-allow { background: #238636; color: #ffffff; }
    #inspect-btn-allow:hover { background: #2ea043; }
    #inspect-btn-reject { background: #b62324; color: #ffffff; }
    #inspect-btn-reject:hover { background: #da3633; }
    #inspect-btn-diff { background: #21262d; color: #58a6ff; }
    #inspect-btn-diff:hover { background: #30363d; }
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
        files = self.details.get("files") or ([self.target] if self.target else [])
        cnt = self.file_count or len(files)
        with Vertical(id="inspect-dialog"):
            with Horizontal(id="inspect-header"):
                yield Static(f"[bold white]{self.action.title()} approval[/]", id="inspect-title")
                yield Static("[dim]esc[/]", id="inspect-esc")

            act_color = "#d29922" if self.action == "edit" else ("#3fb950" if self.action == "write" else "#58a6ff")
            target = f"{cnt} file{'s' if cnt != 1 else ''}" if cnt != 1 else self.target
            yield Static(
                f"[b {act_color}]● {self.action.upper()}[/]  [bold white]{target}[/]"
                f"  [dim]({self.diff_info or 'no changes'})[/dim]",
                id="inspect-banner",
            )

            with Horizontal(id="inspect-action-bar"):
                with Horizontal(id="inspect-action-right"):
                    yield Button("Allow", id="inspect-btn-allow", variant="success", classes="inspect-btn")
                    yield Button("Reject", id="inspect-btn-reject", variant="error", classes="inspect-btn")
                    yield Button("Diff", id="inspect-btn-diff", variant="primary", classes="inspect-btn")

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

        files = self.details.get("files") or ([self.target] if self.target else [])
        if len(files) == 1:
            self.app.push_screen(
                DiffModal(
                    workdir=self.workdir,
                    single_file=files[0],
                    single_diff=self.diff_text,
                    single_title=f"Diff: {files[0]}",
                ),
                _on_diff_dismiss,
            )
            return

        diffs = []
        for rel in files:
            diffs.append(self._file_diff(os.path.join(self.workdir, rel), rel))
        self.app.push_screen(
            DiffModal(workdir=self.workdir, single_file=files[0], single_diff=diffs, single_title="Diff"),
            _on_diff_dismiss,
        )

    def _file_diff(self, abs_path: str, rel: str) -> str:
        try:
            import difflib
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fp:
                new = fp.read()
        except Exception:
            return f"--- /dev/null\n+++ b/{rel}\n(could not read file)"
        old = self.details.get("old", "") if rel == self.target else ""
        if not old:
            return "".join(f"+{line}\n" for line in new.splitlines()[:400])
        return "".join(difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        ))

    def action_handle_interrupt(self) -> None:
        self.action_reject()

    def action_action_allow(self) -> None:
        self.action_allow()

    def action_action_reject(self) -> None:
        self.action_reject()

    def action_action_open_diff(self) -> None:
        self.action_open_diff()
