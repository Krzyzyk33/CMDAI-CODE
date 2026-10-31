"""Single-file Diff View widget integrated into CMDAI CODE Editor.

Supports both Unified Diff and Side-by-Side Split Diff for a specific file,
with instant switching to live code editing (E / Ctrl+D) and file revert (R).
"""

import difflib
import os
from typing import Optional

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Static

from cmdai.core.project_scope import compute_single_file_diff


DIFF_VIEW_CSS = """
EditorFileDiffPane {
    width: 100%;
    height: 1fr;
    background: #090d13;
    color: #e6edf3;
}

EditorFileDiffPane.hidden {
    display: none;
}

#editor-diff-header {
    height: 3;
    width: 100%;
    background: #121721;
    border-bottom: tall #21262d;
    padding: 0 2;
    align: left middle;
}

#editor-diff-title {
    width: 1fr;
    height: 1;
    content-align: left middle;
}

.diff-action-btn {
    height: 1;
    min-width: 14;
    margin-left: 1;
    border: none;
    background: #1f2937;
    color: #c9d1d9;
    padding: 0 1;
}

.diff-action-btn:hover {
    background: #30363d;
    color: #ffffff;
}

#btn-diff-edit {
    background: #1f6feb;
    color: #ffffff;
}

#btn-diff-edit:hover {
    background: #388bfd;
}

#btn-diff-revert {
    background: #3d1a20;
    color: #ff7b72;
}

#btn-diff-revert:hover {
    background: #6e202c;
    color: #ffffff;
}

#editor-diff-scroll {
    width: 100%;
    height: 1fr;
    background: #090d13;
    padding: 1 2;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

#editor-diff-body {
    width: 100%;
    height: auto;
}
"""


class EditorFileDiffPane(Vertical):
    """In-editor single-file diff viewer (Unified + Side-by-Side modes)."""

    DEFAULT_CSS = DIFF_VIEW_CSS

    class SwitchToEdit(Message):
        def __init__(self, filepath: str) -> None:
            super().__init__()
            self.filepath = filepath

    class RevertRequested(Message):
        def __init__(self, filepath: str, original_text: str) -> None:
            super().__init__()
            self.filepath = filepath
            self.original_text = original_text

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.current_file: Optional[str] = None
        self.view_mode: str = "unified"  # "unified" or "split"
        self._last_diff_data: dict = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="editor-diff-header"):
            yield Static("", id="editor-diff-title")
            yield Button("Unified / Split (V)", id="btn-diff-mode", classes="diff-action-btn")
            yield Button("Revert File (R)", id="btn-diff-revert", classes="diff-action-btn")
            yield Button("Edit File (E)", id="btn-diff-edit", classes="diff-action-btn")
        with VerticalScroll(id="editor-diff-scroll"):
            yield Static("", id="editor-diff-body")

    def load_file_diff(self, filepath: str, baseline_text: Optional[str] = None) -> dict:
        self.current_file = os.path.abspath(filepath)
        data = compute_single_file_diff(
            self.current_file,
            workdir=self.workdir,
            baseline_text=baseline_text,
        )
        self._last_diff_data = data
        self._render_current()
        return data

    def toggle_view_mode(self) -> None:
        self.view_mode = "split" if self.view_mode == "unified" else "unified"
        self._render_current()

    def _render_current(self) -> None:
        data = self._last_diff_data
        if not data:
            return

        rel = data.get("relpath", "")
        status = data.get("status", "M")
        adds = data.get("added", 0)
        dels = data.get("deleted", 0)

        status_badge = {
            "M": "[bold #d29922]MODIFIED[/]",
            "A": "[bold #3fb950]NEW FILE[/]",
            "D": "[bold #f85149]DELETED[/]",
            "CLEAN": "[bold #58a6ff]CLEAN (HEAD)[/]",
        }.get(status, f"[bold #8b949e]{status}[/]")

        mode_label = "SPLIT VIEW" if self.view_mode == "split" else "UNIFIED"
        header_markup = (
            f"[bold #58a6ff]⌬ DIFF[/] [bold #e6edf3]{rel}[/]   "
            f"{status_badge}   "
            f"[bold #3fb950]+{adds}[/] [bold #f85149]-{dels}[/]   "
            f"[dim]│ {mode_label}[/]"
        )

        try:
            self.query_one("#editor-diff-title", Static).update(header_markup)
            body = self.query_one("#editor-diff-body", Static)
            if self.view_mode == "split":
                body.update(self._build_split_renderable(data))
            else:
                body.update(self._build_unified_renderable(data))
        except Exception:
            pass

    def _build_unified_renderable(self, data: dict) -> Text:
        out = Text()
        status = data.get("status", "M")
        diff_lines = data.get("diff_lines", [])

        if status == "CLEAN" or not diff_lines:
            out.append("✔ Brak niezatwierdzonych zmian względem HEAD — podgląd pliku:\n\n", style="bold #3fb950")
            curr = str(data.get("current_text", ""))
            for idx, line in enumerate(curr.splitlines(), start=1):
                out.append(f"{idx:4d} │ ", style="#484f58")
                out.append(f"{line}\n", style="#c9d1d9")
            return out

        old_ln = 0
        new_ln = 0
        for line in diff_lines:
            if line.startswith("---") or line.startswith("+++"):
                out.append(f"  {line}\n", style="bold #8b949e")
            elif line.startswith("@@"):
                # Parse hunk line numbers e.g. @@ -10,5 +10,7 @@
                try:
                    header_part = line.split("@@")[1].strip()
                    parts = header_part.split(" ")
                    old_start = abs(int(parts[0].split(",")[0]))
                    new_start = abs(int(parts[1].split(",")[0]))
                    old_ln = old_start
                    new_ln = new_start
                except Exception:
                    pass
                out.append(f"\n{line}\n", style="bold #58a6ff on #111b27")
            elif line.startswith("+"):
                out.append(f"{'':4s} {new_ln:4d} │ + ", style="bold #3fb950 on #0f291e")
                out.append(f"{line[1:]}\n", style="#e6edf3 on #0f291e")
                new_ln += 1
            elif line.startswith("-"):
                out.append(f"{old_ln:4d} {'':4s} │ - ", style="bold #f85149 on #2d1216")
                out.append(f"{line[1:]}\n", style="#ffdcd7 on #2d1216")
                old_ln += 1
            else:
                content = line[1:] if line.startswith(" ") else line
                out.append(f"{old_ln:4d} {new_ln:4d} │   ", style="#484f58")
                out.append(f"{content}\n", style="#8b949e")
                old_ln += 1
                new_ln += 1
        return out

    def _build_split_renderable(self, data: dict) -> Table:
        table = Table(
            show_header=True,
            header_style="bold #8b949e on #161b22",
            expand=True,
            box=None,
            padding=(0, 1),
        )
        table.add_column("#", width=5, style="#484f58", justify="right")
        table.add_column("ORIGINAL (HEAD)", ratio=1, overflow="fold")
        table.add_column("#", width=5, style="#484f58", justify="right")
        table.add_column("CURRENT (WORKING TREE)", ratio=1, overflow="fold")

        orig_lines = str(data.get("original_text", "")).splitlines()
        curr_lines = str(data.get("current_text", "")).splitlines()

        matcher = difflib.SequenceMatcher(None, orig_lines, curr_lines)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for o_idx, c_idx in zip(range(i1, i2), range(j1, j2)):
                    table.add_row(
                        str(o_idx + 1),
                        Text(orig_lines[o_idx], style="#8b949e"),
                        str(c_idx + 1),
                        Text(curr_lines[c_idx], style="#8b949e"),
                    )
            elif tag == "replace":
                max_len = max(i2 - i1, j2 - j1)
                for k in range(max_len):
                    o_idx = i1 + k if i1 + k < i2 else None
                    c_idx = j1 + k if j1 + k < j2 else None
                    o_num = str(o_idx + 1) if o_idx is not None else ""
                    c_num = str(c_idx + 1) if c_idx is not None else ""
                    o_txt = Text(f"- {orig_lines[o_idx]}", style="#ffdcd7 on #2d1216") if o_idx is not None else Text("")
                    c_txt = Text(f"+ {curr_lines[c_idx]}", style="#aff5b4 on #0f291e") if c_idx is not None else Text("")
                    table.add_row(o_num, o_txt, c_num, c_txt)
            elif tag == "delete":
                for o_idx in range(i1, i2):
                    table.add_row(
                        str(o_idx + 1),
                        Text(f"- {orig_lines[o_idx]}", style="#ffdcd7 on #2d1216"),
                        "",
                        Text(""),
                    )
            elif tag == "insert":
                for c_idx in range(j1, j2):
                    table.add_row(
                        "",
                        Text(""),
                        str(c_idx + 1),
                        Text(f"+ {curr_lines[c_idx]}", style="#aff5b4 on #0f291e"),
                    )
        return table

    @on(Button.Pressed, "#btn-diff-mode")
    def _on_mode_btn(self) -> None:
        self.toggle_view_mode()

    @on(Button.Pressed, "#btn-diff-edit")
    def _on_edit_btn(self) -> None:
        if self.current_file:
            self.post_message(self.SwitchToEdit(self.current_file))

    @on(Button.Pressed, "#btn-diff-revert")
    def _on_revert_btn(self) -> None:
        if self.current_file and self._last_diff_data:
            orig = str(self._last_diff_data.get("original_text", ""))
            self.post_message(self.RevertRequested(self.current_file, orig))
