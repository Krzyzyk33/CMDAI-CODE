import os
import subprocess
from typing import Dict, List, Optional
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class CommitModal(ModalScreen[Optional[str]]):

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("enter", "do_commit", "Commit"),
    ]

    def __init__(self, workdir: str = ".", suggested_msg: str = "", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.files_data: Dict[str, str] = {}
        self.file_list: List[str] = []
        self.file_status: Dict[str, str] = {}
        self._load_git_data()
        self.suggested_msg = suggested_msg or self._generate_suggested_message()

    def _load_git_data(self) -> None:
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

    def _generate_suggested_message(self) -> str:
        files = self.file_list
        if not files:
            return "chore: update files"
        first = files[0].split()[-1]
        base = os.path.basename(first)
        ext = os.path.splitext(base)[1].lstrip(".")
        scope = ext or "core"
        if len(files) == 1:
            return f"feat({scope}): update {base}"
        return f"feat({scope}): update {len(files)} files including {base}"

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
                yield Static("[bold white]Git Commit & Diff Review[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            with Horizontal(id="diff-split-container"):
                with Vertical(id="diff-files-sidebar"):
                    yield Static("  STAGED CHANGES", id="diff-sidebar-title")
                    ol = OptionList(id="diff-file-list")
                    if not self.file_list:
                        ol.add_option(Option("  [dim]No changes to commit[/]"))
                    else:
                        for idx, f in enumerate(self.file_list):
                            st = self.file_status.get(f, "M")
                            ol.add_option(Option(self._format_file_option(f, st), id=f"commit_opt_{idx}"))
                    yield ol

                with VerticalScroll(id="diff-preview-scroll"):
                    yield Static("", id="diff-preview-header")
                    yield Static("", id="diff-content-body")

            with Vertical(id="commit-box"):
                yield Static("[bold white]Commit Message:[/] [dim](press Enter to commit)[/dim]", id="commit-label")
                yield Input(value=self.suggested_msg, placeholder="e.g. feat: update files", id="commit-input")

            with Horizontal(id="modal-footer"):
                yield Static("[b #3fb950]Commit[/] enter in input   [b #58a6ff]Select file[/] ↑/↓   [dim]Cancel[/] esc", id="modal-footer-left")
                cnt = len(self.file_list)
                yield Static(f"[dim]{cnt} file{'s' if cnt != 1 else ''}[/]", id="modal-footer-right")

    def on_mount(self) -> None:
        ol = self.query_one("#diff-file-list", OptionList)
        if self.file_list:
            ol.highlighted = 0
            self._display_file_diff(self.file_list[0])
        else:
            body = self.query_one("#diff-content-body", Static)
            body.update("[dim]Workspace is clean. No uncommitted modifications found.[/dim]")
        self.query_one("#commit-input", Input).focus()

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

    @on(Input.Submitted, "#commit-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_do_commit()

    def action_do_commit(self) -> None:
        inp = self.query_one("#commit-input", Input)
        msg = inp.value.strip()
        if not msg:
            return

        try:
            subprocess.run(["git", "add", "-A"], cwd=self.workdir, check=True, timeout=10)
            res = subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if res.returncode == 0:
                self.dismiss(msg)
            else:
                self.dismiss(None)
        except Exception:
            self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
