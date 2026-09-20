import os
import subprocess
from typing import Dict, List, Optional
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option


class DiffModal(ModalScreen[None]):

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("ctrl+c", "handle_interrupt", "Close", show=False),
        Binding("c", "open_commit", "Commit"),
        Binding("enter", "allow_or_commit", "Allow/Commit", show=False),
        Binding("y", "allow_or_commit", "Allow", show=False),
        Binding("n", "dismiss_modal", "Reject", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(
        self,
        workdir: str = ".",
        single_file: Optional[str] = None,
        single_diff: Optional[str] = None,
        single_title: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.single_title = single_title
        self.is_single_edit = single_file is not None
        self.files_data: Dict[str, str] = {}
        self.file_list: List[str] = []
        self.file_status: Dict[str, str] = {}

        if single_file:
            self.file_list = [single_file]
            self.files_data[single_file] = single_diff or "(No diff available)"
            self.file_status[single_file] = "M"
        else:
            self._load_git_diffs()

    def _load_git_diffs(self) -> None:
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if not line.strip():
                        continue
                    status = line[:2]
                    path = line[2:].strip().strip('"')
                    self.file_list.append(path)
                    self.file_status[path] = status

            diff_res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            raw_diff = diff_res.stdout
            if not raw_diff:
                diff_res2 = subprocess.run(
                    ["git", "diff"],
                    cwd=self.workdir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                )
                raw_diff = diff_res2.stdout

            current_file = None
            current_lines = []
            for line in raw_diff.splitlines():
                if line.startswith("diff --git"):
                    if current_file and current_lines:
                        self.files_data[current_file] = "\n".join(current_lines)
                    parts = line.split(" b/")
                    current_file = parts[-1] if len(parts) > 1 else "unknown"
                    current_lines = [line]
                else:
                    if current_file:
                        current_lines.append(line)
            if current_file and current_lines:
                self.files_data[current_file] = "\n".join(current_lines)

            for f in self.file_list:
                if f not in self.files_data:
                    full_p = os.path.join(self.workdir, f)
                    if os.path.isfile(full_p):
                        try:
                            with open(full_p, "r", encoding="utf-8", errors="replace") as fp:
                                content = fp.read()
                            lines = [f"+{l}" for l in content.splitlines()]
                            self.files_data[f] = "[Untracked new file]\n" + "\n".join(lines[:300])
                        except Exception:
                            self.files_data[f] = "(Unable to read file content)"
        except Exception:
            pass

    def _format_file_option(self, path: str, status: str) -> str:
        if "M" in status:
            badge = "[b #58a6ff]M[/]"
        elif "A" in status or "?" in status:
            badge = "[b #3fb950]+[/]"
        elif "D" in status:
            badge = "[b #f85149]-[/]"
        else:
            badge = "[dim]•[/]"

        base = os.path.basename(path)
        parent = os.path.dirname(path).replace("\\", "/")
        if parent:
            if len(parent) > 16:
                parent = "..." + parent[-13:]
            return f" {badge} [bold white]{base:<16}[/] [dim]{parent}[/]"
        return f" {badge} [bold white]{base}[/]"

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="diff-modal-dialog"):
            with Horizontal(id="modal-header"):
                title = self.single_title or "Working Changes (Git Diff)"
                yield Static(f"[bold white]{title}[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            with Horizontal(id="diff-split-container"):
                with Vertical(id="diff-files-sidebar"):
                    yield Static("  CHANGED FILES", id="diff-sidebar-title")
                    ol = OptionList(id="diff-file-list")
                    if not self.file_list:
                        ol.add_option(Option("  [dim]No changes detected[/]"))
                    else:
                        for idx, f in enumerate(self.file_list):
                            st = self.file_status.get(f, "M")
                            ol.add_option(Option(self._format_file_option(f, st), id=f"diff_opt_{idx}"))
                    yield ol

                with VerticalScroll(id="diff-preview-scroll"):
                    yield Static("", id="diff-preview-header")
                    yield Static("", id="diff-content-body")

            with Horizontal(id="diff-action-bar"):
                if self.is_single_edit:
                    yield Static("[bold #7ee787]●[/] [dim]Allow this edit?[/dim]", id="diff-action-hint")
                    yield Button("Allow (y)", id="diff-commit-btn", variant="success")
                    yield Button("Reject (n)", id="diff-reject-btn", variant="error")
                else:
                    yield Static("[bold #58a6ff]●[/] [dim]Ready to commit changes?[/dim]", id="diff-action-hint")
                    yield Button("Commit Changes (c)", id="diff-commit-btn", variant="success")

            with Horizontal(id="modal-footer"):
                if self.is_single_edit:
                    yield Static("[b #3fb950]Allow[/] y/Enter   [b #f85149]Reject[/] n/Esc   [b #58a6ff]Select file[/] ↑/↓", id="modal-footer-left")
                else:
                    yield Static("[b #3fb950]Commit[/] c / click button   [b #58a6ff]Select file[/] ↑/↓   [dim]Close[/] esc", id="modal-footer-left")
                cnt = len(self.file_list)
                yield Static(f"[dim]{cnt} changed file{'s' if cnt != 1 else ''}[/]", id="modal-footer-right")

    def on_mount(self) -> None:
        ol = self.query_one("#diff-file-list", OptionList)
        if self.file_list:
            ol.highlighted = 0
            self._display_file_diff(self.file_list[0])
        else:
            body = self.query_one("#diff-content-body", Static)
            body.update("[dim]Workspace is clean. No uncommitted modifications found.[/dim]")
        ol.focus()

    @on(OptionList.OptionHighlighted, "#diff-file-list")
    def on_file_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        idx = event.option_index
        if 0 <= idx < len(self.file_list):
            self._display_file_diff(self.file_list[idx])

    @on(OptionList.OptionSelected, "#diff-file-list")
    def on_file_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if 0 <= idx < len(self.file_list):
            self._display_file_diff(self.file_list[idx])

    def _display_file_diff(self, filename: str) -> None:
        header = self.query_one("#diff-preview-header", Static)
        body = self.query_one("#diff-content-body", Static)
        st = self.file_status.get(filename, "modified").strip()
        header.update(f"[b #58a6ff]Diff:[/] [bold white]{filename}[/]  [dim]({st})[/dim]")
        diff_text = self.files_data.get(filename, "No diff available for this file.")

        t = Text()
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---") or line.startswith("diff "):
                t.append(line + "\n", style="bold #8b949e")
            elif line.startswith("@@"):
                t.append(line + "\n", style="bold #58a6ff")
            elif line.startswith("+"):
                t.append(line + "\n", style="#3fb950")
            elif line.startswith("-"):
                t.append(line + "\n", style="#f85149")
            else:
                t.append(line + "\n", style="#c9d1d9")
        body.update(t)

    @on(Button.Pressed, "#diff-commit-btn")
    def on_commit_button_pressed(self, event: Button.Pressed) -> None:
        self.action_allow_or_commit()

    @on(Button.Pressed, "#diff-reject-btn")
    def on_reject_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("reject" if self.is_single_edit else None)

    def action_allow_or_commit(self) -> None:
        if self.is_single_edit:
            self.dismiss("allow")
        else:
            self.dismiss("commit")

    def action_open_commit(self) -> None:
        self.action_allow_or_commit()

    def action_cursor_up(self) -> None:
        self.query_one("#diff-file-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#diff-file-list", OptionList).action_cursor_down()

    def action_dismiss_modal(self) -> None:
        self.dismiss("reject" if self.is_single_edit else None)

    def action_handle_interrupt(self) -> None:
        self.action_dismiss_modal()

    def action_action_allow_or_commit(self) -> None:
        self.action_allow_or_commit()

    def action_action_dismiss_modal(self) -> None:
        self.action_dismiss_modal()
