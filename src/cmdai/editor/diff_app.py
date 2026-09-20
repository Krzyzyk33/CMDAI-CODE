from ..tui.modals.git_modal import GitBranchesModal
from ..tui.modals.checkpoints import CheckpointsModal
import os
import subprocess
import sys
from typing import Dict, List, Optional

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option


DIFF_CSS = """
Screen {
    background: #090d13;
    color: #c9d1d9;
}

#diff-top-bar {
    dock: top;
    height: 1;
    background: #161b22;
    padding: 0 1;
    color: #8b949e;
}

#diff-brand {
    width: auto;
    margin-right: 2;
}

#diff-title-label {
    width: 1fr;
    color: #6e7681;
}


#diff-checkpoints-btn {
    width: auto;
    height: 1;
    background: transparent;
    color: #58a6ff;
    border: none;
    margin-right: 2;
}

#diff-checkpoints-btn:hover {
    color: #ffffff;
    background: #1f6feb;
}

#diff-git-btn {
    width: auto;
    height: 1;
    background: transparent;
    color: #3fb950;
    border: none;
    margin-right: 2;
}

#diff-git-btn:hover {
    color: #ffffff;
    background: #238636;
}


#diff-top-esc {
    width: auto;
    color: #6e7681;
}

#diff-main {
    width: 100%;
    height: 1fr;
}

#sidebar {
    width: 38;
    height: 100%;
    background: #0d1117;
    border-right: solid #21262d;
    padding: 0;
}

#sidebar-title {
    height: 1;
    background: #161b22;
    color: #8b949e;
    text-style: bold;
    padding: 0 1;
}

#file-list {
    background: #0d1117;
    border: none !important;
    height: 1fr;
    padding: 0;
    margin: 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

#file-list > .option-list--option {
    color: #8b949e;
    background: transparent;
}

#file-list > .option-list--option-highlighted {
    background: #21262d !important;
    color: #58a6ff !important;
    text-style: bold;
}

#diff-pane {
    width: 1fr;
    height: 100%;
    background: #090d13;
    padding: 0 1;
}

#diff-scroll {
    width: 100%;
    height: 1fr;
    background: #090d13;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

#diff-body {
    width: 100%;
    height: auto;
}

#commit-bar {
    dock: bottom;
    height: auto;
    background: #161b22;
    border-top: solid #21262d;
    padding: 1 2;
}

#commit-info-row {
    height: 1;
    margin-bottom: 1;
}

#commit-active-file {
    width: 1fr;
    color: #e6edf3;
}

#commit-files-count {
    width: auto;
    color: #8b949e;
}

#commit-input-row {
    height: 3;
    align: left middle;
}

#commit-input {
    width: 1fr;
    background: #0d1117;
    color: #e6edf3;
    border: round #30363d;
    padding: 0 1;
    margin-right: 1;
}

#commit-input:focus {
    border: round #58a6ff;
    background: #090d13;
}

#commit-btn {
    width: auto;
    height: 3;
    background: #238636;
    color: #ffffff;
    border: none;
    padding: 0 3;
    text-style: bold;
}

#commit-btn:hover {
    background: #2ea043;
}

#commit-btn:focus {
    background: #2ea043;
    border: none;
}
"""


class CMDAIDiffApp(App[None]):

    CSS = DIFF_CSS
    TITLE = "CMDAI CODE DIFF VIEWER"

    BINDINGS = [
        Binding("ctrl+q", "quit_app", "Exit"),
        Binding("escape", "quit_app", "Exit"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.files_data: Dict[str, str] = {}
        self.file_list: List[str] = []
        self.file_status: Dict[str, str] = {}
        self._load_git_data()
        self.suggested_msg = self._generate_suggested_message()

    def _load_git_data(self) -> None:
        self.files_data.clear()
        self.file_list.clear()
        self.file_status.clear()
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
        with Horizontal(id="diff-top-bar"):
            yield Static("[bold white]CMDAI[/] [bold #58a6ff]DIFF[/]", id="diff-brand")
            yield Static(f"[dim]{self.workdir}[/dim]", id="diff-title-label")
            yield Button(Text("[checkpoint]"), id="diff-checkpoints-btn")
            yield Button(Text("[git]"), id="diff-git-btn")
            yield Static("[dim]esc exit[/]", id="diff-top-esc")

        with Horizontal(id="diff-main"):
            with Vertical(id="sidebar"):
                cnt = len(self.file_list)
                yield Static(f"  CHANGED FILES ({cnt})", id="sidebar-title")
                ol = OptionList(id="file-list")
                if not self.file_list:
                    ol.add_option(Option("  [dim]No changes detected[/]"))
                else:
                    for idx, f in enumerate(self.file_list):
                        st = self.file_status.get(f, "M")
                        ol.add_option(Option(self._format_file_option(f, st), id=f"opt_{idx}"))
                yield ol

            with Vertical(id="diff-pane"):
                with VerticalScroll(id="diff-scroll"):
                    yield Static("", id="diff-body")

        with Vertical(id="commit-bar"):
            with Horizontal(id="commit-info-row"):
                yield Static("", id="commit-active-file")
                yield Static(f"[dim]{cnt} changed files[/dim]", id="commit-files-count")
            with Horizontal(id="commit-input-row"):
                yield Input(
                    value=self.suggested_msg,
                    placeholder="Commit message... (click Commit Changes to save)",
                    id="commit-input",
                )
                yield Button("Commit Changes", id="commit-btn", variant="success")

    def on_mount(self) -> None:
        ol = self.query_one("#file-list", OptionList)
        if self.file_list:
            ol.highlighted = 0
            self._display_file_diff(self.file_list[0])
        else:
            self.query_one("#diff-body", Static).update("[dim]Workspace is clean. No uncommitted modifications found.[/dim]")
        ol.focus()

    @on(OptionList.OptionHighlighted, "#file-list")
    def on_file_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        idx = event.option_index
        if 0 <= idx < len(self.file_list):
            self._display_file_diff(self.file_list[idx])

    @on(OptionList.OptionSelected, "#file-list")
    def on_file_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if 0 <= idx < len(self.file_list):
            self._display_file_diff(self.file_list[idx])

    def _display_file_diff(self, filename: str) -> None:
        st = self.file_status.get(filename, "modified").strip()
        if "M" in st:
            st_badge = "[#58a6ff]Modified[/]"
        elif "A" in st or "?" in st:
            st_badge = "[#3fb950]Added[/]"
        elif "D" in st:
            st_badge = "[#f85149]Deleted[/]"
        else:
            st_badge = f"[dim]{st}[/dim]"

        info = self.query_one("#commit-active-file", Static)
        info.update(f"[bold #58a6ff]●[/] [bold white]{filename}[/]  {st_badge}")

        body = self.query_one("#diff-body", Static)
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

    @on(Button.Pressed, "#commit-btn")
    def on_commit_button_pressed(self, event: Button.Pressed) -> None:
        self.action_commit_changes()

    def action_commit_changes(self) -> None:
        inp = self.query_one("#commit-input", Input)
        msg = inp.value.strip()
        if not msg:
            self.notify("Please enter a commit message", severity="warning")
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
                self.notify(f"Committed: {msg}", timeout=3.0)
                self._load_git_data()
                self._refresh_ui_after_commit()
            else:
                err = res.stderr.strip() or res.stdout.strip()
                self.notify(f"Commit failed: {err}", severity="error")
        except Exception as e:
            self.notify(f"Commit error: {e}", severity="error")

    def _refresh_ui_after_commit(self) -> None:
        ol = self.query_one("#file-list", OptionList)
        ol.clear_options()
        cnt = len(self.file_list)
        self.query_one("#sidebar-title", Static).update(f"  CHANGED FILES ({cnt})")
        self.query_one("#commit-files-count", Static).update(f"[dim]{cnt} changed files[/dim]")
        if not self.file_list:
            ol.add_option(Option("  [dim]No changes detected (Clean)[/]"))
            self.query_one("#commit-active-file", Static).update("[b #3fb950]✔ Clean working tree[/]")
            self.query_one("#diff-body", Static).update("[dim]All changes committed successfully.[/dim]")
            self.query_one("#commit-input", Input).value = ""
        else:
            for idx, f in enumerate(self.file_list):
                st = self.file_status.get(f, "M")
                ol.add_option(Option(self._format_file_option(f, st), id=f"opt_{idx}"))
            ol.highlighted = 0
            self._display_file_diff(self.file_list[0])

    def action_cursor_up(self) -> None:
        self.query_one("#file-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#file-list", OptionList).action_cursor_down()


    @on(Button.Pressed, "#diff-git-btn")
    def on_git_clicked(self) -> None:
        self.push_screen(GitBranchesModal(workdir=self.workdir))

    @on(Button.Pressed, "#diff-checkpoints-btn")
    def on_checkpoints_clicked(self) -> None:
        self.push_screen(CheckpointsModal(workdir=self.workdir))

    def action_quit_app(self) -> None:

        self.exit()


def main():
    raw_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    abs_dir = os.path.abspath(raw_dir)
    app = CMDAIDiffApp(workdir=abs_dir)
    app.run()


if __name__ == "__main__":
    main()
