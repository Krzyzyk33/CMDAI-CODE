from ..tui.modals.git_modal import GitBranchesModal
from ..tui.modals.checkpoints import CheckpointsModal
import os
import sys
from typing import Optional
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Static, TextArea

EXT_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".rust": "rust",
    ".rs": "rust",
    ".go": "go",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sql": "sql",
    ".toml": "toml",
    ".xml": "xml",
}

EDITOR_CSS = """
Screen {
    background: #090d13;
    color: #c9d1d9;
    layers: base;
}

#editor-top-bar {
    dock: top;
    height: 1;
    background: #161b22;
    padding: 0 1;
    color: #8b949e;
}

#editor-brand {
    width: auto;
    margin-right: 2;
}

#editor-file-label {
    width: 1fr;
    color: #e6edf3;
    text-style: bold;
}


#editor-checkpoints-btn {
    width: auto;
    height: 1;
    background: transparent;
    color: #58a6ff;
    border: none;
    margin-right: 2;
}

#editor-checkpoints-btn:hover {
    color: #ffffff;
    background: #1f6feb;
}

#editor-git-btn {
    width: auto;
    height: 1;
    background: transparent;
    color: #3fb950;
    border: none;
    margin-right: 2;
}

#editor-git-btn:hover {
    color: #ffffff;
    background: #238636;
}


#editor-top-esc {
    width: auto;
    color: #6e7681;
}

#editor-main {
    width: 100%;
    height: 1fr;
}

#sidebar {
    width: 32;
    height: 100%;
    background: #0d1117;
    border-right: solid #21262d;
    padding: 0;
}

#sidebar.hidden {
    display: none;
}

#sidebar-title {
    height: 1;
    background: #161b22;
    color: #8b949e;
    text-style: bold;
    padding: 0 1;
}

DirectoryTree {
    background: #0d1117;
    color: #8b949e;
    padding: 0 1;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
    border: none;
}

DirectoryTree:focus {
    border: none;
}

DirectoryTree > .tree--cursor {
    background: #21262d;
    color: #58a6ff;
    text-style: bold;
}

#editor-pane {
    width: 1fr;
    height: 100%;
    background: #090d13;
}

TextArea {
    width: 100%;
    height: 1fr;
    background: #090d13;
    border: none;
    padding: 0 1;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

TextArea:focus {
    border: none;
}

"""


class CMDAICodeEditor(App[None]):
    """Swift-styled terminal code editor for CMDAI CODE."""

    CSS = EDITOR_CSS
    TITLE = "CMDAI CODE EDITOR"

    BINDINGS = [
        Binding("ctrl+s", "save_file", "Save"),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Explorer"),
        Binding("ctrl+q", "quit_editor", "Exit"),
        Binding("escape", "handle_escape", "Back/Exit"),
    ]

    def __init__(self, initial_path: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.target_path = os.path.abspath(initial_path)
        if os.path.isfile(self.target_path):
            self.root_dir = os.path.dirname(self.target_path)
            self.active_file: Optional[str] = self.target_path
        else:
            self.root_dir = self.target_path
            self.active_file: Optional[str] = None
        self.is_modified: bool = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="editor-top-bar"):
            yield Static("[bold white]CMDAI[/] [bold #58a6ff]EDITOR[/]", id="editor-brand")
            yield Static("No file selected", id="editor-file-label")
            yield Button(Text("[checkpoint]"), id="editor-checkpoints-btn")
            yield Button(Text("[git]"), id="editor-git-btn")
            yield Static("[dim]esc / ^Q exit[/]", id="editor-top-esc")

        with Horizontal(id="editor-main"):
            with Vertical(id="sidebar"):
                yield Static("  EXPLORER", id="sidebar-title")
                yield DirectoryTree(self.root_dir, id="file-tree")

            with Vertical(id="editor-pane"):
                yield TextArea("", id="code-area", show_line_numbers=True)

    def on_mount(self) -> None:
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleTitleW("CMDAI CODE Editor")
            except Exception:
                pass
        if self.active_file and os.path.isfile(self.active_file):
            self.load_file(self.active_file)
        else:
            for cand in ["README.md", "app.py", "main.py", "CMDAIPLAN.md"]:
                full = os.path.join(self.root_dir, cand)
                if os.path.isfile(full):
                    self.load_file(full)
                    break
        self.query_one("#code-area", TextArea).focus()

    def load_file(self, filepath: str) -> None:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            ext = os.path.splitext(filepath)[1].lower()
            lang = EXT_LANG_MAP.get(ext)

            ta = self.query_one("#code-area", TextArea)
            ta.text = content
            if lang:
                try:
                    ta.language = lang
                except Exception:
                    ta.language = None
            else:
                ta.language = None

            self.active_file = filepath
            self.is_modified = False
            rel = os.path.relpath(filepath, self.root_dir)
            self.query_one("#editor-file-label", Static).update(f"[b #58a6ff]{rel}[/]")
            lines = len(content.splitlines())
            self.query_one("#editor-bottom-status", Static).update(f"[dim]{lines} lines · UTF-8[/dim]")
        except Exception as e:
            self.notify(f"Error opening file: {e}", severity="error")

    @on(DirectoryTree.FileSelected, "#file-tree")
    def on_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        p = str(event.path)
        if os.path.isfile(p):
            self.load_file(p)
            self.query_one("#code-area", TextArea).focus()

    def action_save_file(self) -> None:
        if not self.active_file:
            self.notify("No active file to save", severity="warning")
            return

        ta = self.query_one("#code-area", TextArea)
        try:
            with open(self.active_file, "w", encoding="utf-8") as f:
                f.write(ta.text)
            self.is_modified = False
            rel = os.path.relpath(self.active_file, self.root_dir)
            self.notify(f"Saved: {rel}", timeout=2.0)
            lines = len(ta.text.splitlines())
            self.query_one("#editor-bottom-status", Static).update(f"[b #3fb950]Saved[/] · {lines} lines · UTF-8")
        except Exception as e:
            self.notify(f"Save failed: {e}", severity="error")

    def action_toggle_sidebar(self) -> None:
        sb = self.query_one("#sidebar")
        sb.toggle_class("hidden")

    def action_handle_escape(self) -> None:
        ta = self.query_one("#code-area", TextArea)
        if not ta.has_focus:
            ta.focus()
        else:
            self.exit()


    @on(Button.Pressed, "#editor-git-btn")
    def on_git_clicked(self) -> None:
        self.push_screen(GitBranchesModal(workdir=self.root_dir))

    @on(Button.Pressed, "#editor-checkpoints-btn")
    def on_checkpoints_clicked(self) -> None:
        self.push_screen(CheckpointsModal(workdir=self.root_dir))

    def action_quit_editor(self) -> None:

        self.exit()


def main():
    raw_path = sys.argv[1] if len(sys.argv) > 1 else "."
    abs_path = os.path.abspath(raw_path)
    app = CMDAICodeEditor(initial_path=abs_path)
    app.run()


if __name__ == "__main__":
    main()
