import os
from pathlib import Path
from typing import Iterable, Optional
from textual import events, on
from textual.widgets import Button, DirectoryTree, Static, Tree
from textual.containers import Vertical
from textual.message import Message
from rich.text import Text
from ..icons.registry import icons
from .clickable import MouseActivatable
from .problems import ERROR_FG, WARNING_FG

class IconDirectoryTree(DirectoryTree):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_dir: Optional[Path] = None
        self._new_in: Optional[Path] = None

        self._diagnostics: dict = {}

    def set_diagnostics(self, per_file: dict) -> None:

        if per_file == self._diagnostics:
            return
        self._diagnostics = per_file

        self.refresh()

    def _severity_for(self, path) -> str:

        try:
            key = os.path.normcase(os.path.abspath(str(path)))
        except Exception:
            return ""
        sevs = self._diagnostics.get(key)
        if not sevs:
            return ""
        return "error" if "error" in sevs else "warning"

    @on(Tree.NodeHighlighted)
    def _track_highlighted(self, event: Tree.NodeHighlighted) -> None:
        data = getattr(event.node.data, "path", None)
        if data is not None and Path(data).is_dir():
            self.selected_dir = Path(data)

    @on(Button.Pressed)
    def _on_plus_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tree-new-file":
            event.stop()
            self.post_message(self.NewFileRequested(self._new_in))

    class NewFileRequested(Message):

        def __init__(self, directory) -> None:
            super().__init__()
            self.directory = directory

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        ignored = {".git", ".venv", "__pycache__", "node_modules", ".cmdai_code_project", ".pytest_cache", ".idea", ".vscode"}
        return [p for p in paths if p.name not in ignored and not p.name.endswith(".pyc")]

    def render_label(self, node, base_style=None, style=None, *args, **kwargs) -> Text:
        node_label = node._label.plain
        data = node.data
        if data is None:
            return Text(node_label)

        try:
            path = data.path
            is_dir = path.is_dir()
            is_open = node.is_expanded

            icon_markup = icons.get_file_icon(str(path), is_dir=is_dir, is_open=is_open)
            res = Text.from_markup(icon_markup)
            lbl = Text(node_label)
            if style:
                lbl.stylize(style)

            if not is_dir:
                severity = self._severity_for(path)
                if severity == "error":
                    lbl.stylize(f"bold {ERROR_FG}")
                elif severity == "warning":
                    lbl.stylize(f"bold {WARNING_FG}")
            res.append(lbl)
            return res
        except Exception:
            return Text(node_label)

class ExplorerPlus(MouseActivatable, Static):

    DEFAULT_CSS = """ExplorerPlus {
        dock: top;
        layer: overlay;
        align-horizontal: left;
        width: 1;
        min-width: 1;
        height: 1;
        margin-top: 0;
        margin-left: 0;
        padding: 0;
        background: transparent;
        color: #6e7681;
        text-align: center;
    }
    ExplorerPlus:hover {
        background: transparent;
        color: #ffffff;
    }
"""

    def on_activate(self) -> None:
        self.post_message(self.Pressed())

    class Pressed(Message):
        pass

class EditorExplorer(Vertical):

    MIN_WIDTH = 16
    MAX_WIDTH = 80
    DEFAULT_WIDTH = 32

    DEFAULT_CSS = """EditorExplorer {
        width: 32;
        height: 100%;
        background: #0d1117;
        border-right: solid #21262d;
        padding: 0 1;
    }
    EditorExplorer.hidden {
        display: none;
    }
    IconDirectoryTree {
        background: #0d1117;
        color: #8b949e;
        padding: 0 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: #30363d;
        border: none;
    }
    IconDirectoryTree:focus {
        border: none;
    }
    IconDirectoryTree > .tree--cursor {
        background: transparent;
        color: #c9d1d9;
        text-style: none;
    }
"""

    class Action(Message):

        def __init__(self, action_id: str):
            super().__init__()
            self.action_id = action_id

    def __init__(self, root_dir: str, **kwargs):
        super().__init__(**kwargs)
        self.root_dir = root_dir
        self._dragging = False

    def compose(self):

        yield IconDirectoryTree(self.root_dir, id="explorer-tree")
        plus = ExplorerPlus("+", id="explorer-new-file")
        plus.tooltip = "New file (name ending with / creates a folder)"
        yield plus

    @on(ExplorerPlus.Pressed)
    def on_plus_pressed(self, event: ExplorerPlus.Pressed) -> None:
        event.stop()
        self.post_message(self.Action("explorer_new_file"))

    @on(IconDirectoryTree.NewFileRequested)
    def on_new_file_requested(self, message: IconDirectoryTree.NewFileRequested) -> None:
        message.stop()
        if message.directory:
            self.query_one("#explorer-tree", IconDirectoryTree).selected_dir =\
                Path(message.directory)
        self.post_message(self.Action("explorer_new_file"))

    @on(Button.Pressed)
    def on_action_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "explorer-new-file": "explorer_new_file",
            "explorer-refresh": "explorer_refresh",
        }
        action_id = mapping.get(event.button.id or "")
        if action_id:
            event.stop()
            self.post_message(self.Action(action_id))

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return

        if event.offset.x < self.size.width - 1:
            return
        self._dragging = True
        self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        self.styles.width = self._clamped(event.screen_offset.x)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()

    def _clamped(self, width: int) -> int:

        screen_w = self.app.screen.size.width
        return max(self.MIN_WIDTH, min(width, self.MAX_WIDTH, screen_w - 30))

    def widen(self, delta: int = 4) -> None:
        self.styles.width = self._clamped(self.size.width + delta)

    def narrow(self, delta: int = 4) -> None:
        self.styles.width = self._clamped(self.size.width - delta)
