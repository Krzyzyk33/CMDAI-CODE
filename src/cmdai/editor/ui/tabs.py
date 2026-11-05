import os
from typing import List, Optional
from textual import events
from textual.containers import Horizontal
from textual.widgets import Static
from textual.message import Message
from textual.app import ComposeResult
from rich.text import Text
from ..icons.registry import icons
from ..core.buffer import EditorBuffer

class TabSelectBtn(Static):

    DEFAULT_CSS = """
    TabSelectBtn {
        width: auto;
        height: 1;
        padding: 0 1;
        color: #8b949e;
    }

    TabSelectBtn:hover {
        color: #c9d1d9;
    }
    """

    def __init__(self, filepath: str, label_markup: str, **kwargs):
        super().__init__(Text.from_markup(label_markup), **kwargs)
        self.filepath = filepath

class TabCloseBtn(Static):
    DEFAULT_CSS = """
    TabCloseBtn {
        width: 3;
        height: 1;
        padding: 0;
        content-align: center middle;
        color: #6e7681;
    }

    TabCloseBtn:hover {
        color: #f85149;
        text-style: bold;
    }
    """

    def __init__(self, filepath: str, **kwargs):
        super().__init__("✕", **kwargs)
        self.filepath = filepath

class EditorTabItem(Horizontal):
    def __init__(self, filepath: str, label_markup: str, is_active: bool, **kwargs):
        cls = f"editor-tab-item {'active' if is_active else ''}"
        super().__init__(classes=cls, **kwargs)
        self.filepath = filepath
        self.label_markup = label_markup

    def compose(self) -> ComposeResult:
        yield TabSelectBtn(self.filepath, self.label_markup, classes="tab-select-btn")
        yield TabCloseBtn(self.filepath, classes="tab-close-btn")

class EditorTabsBar(Horizontal):

    DEFAULT_CSS = """EditorTabsBar {
        height: 2;
        background: #0d1117;
        border-bottom: solid #21262d;
        overflow-x: auto;
        scrollbar-size-horizontal: 0;
        padding: 0 1;
    }

    EditorTabsBar.hidden {
        display: none;
    }
    .editor-tab-item {
        width: auto;
        height: 1;
        background: #161b22;
        color: #8b949e;
        border-right: solid #21262d;
        padding: 0;
    }

    .editor-tab-item.active {
        background: #21262d;
        color: #f0f6fc;
    }
    .editor-tab-item.active .tab-select-btn {
        color: #f0f6fc;
        text-style: bold;
    }
    .editor-tab-item:hover {
        background: #21262d;
    }
"""

    class TabSelected(Message):
        def __init__(self, filepath: str):
            super().__init__()
            self.filepath = filepath

    class TabClosed(Message):
        def __init__(self, filepath: str):
            super().__init__()
            self.filepath = filepath

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._buffers: List[EditorBuffer] = []
        self._active_path: Optional[str] = None

    def on_mount(self) -> None:
        self.rebuild_ui()

    def update_tabs(self, buffers: List[EditorBuffer], active_path: Optional[str]) -> None:
        self._buffers = list(buffers)
        self._active_path = active_path
        if self.is_mounted:
            self.rebuild_ui()

    def rebuild_ui(self) -> None:
        for child in list(self.children):
            child.remove()

        self.set_class(not self._buffers, "hidden")

        for buf in self._buffers:
            is_active = (buf.filepath == self._active_path)
            file_icon = icons.get_file_icon(buf.filepath)
            fname = os.path.basename(buf.filepath) or buf.filepath
            mod_marker = " ●" if buf.is_modified else ""
            label_markup = f"{file_icon}{fname}{mod_marker}"

            self.mount(EditorTabItem(buf.filepath, label_markup, is_active))

    def on_mouse_down(self, event: events.MouseDown) -> None:

        if event.button != 1:
            return
        x = event.screen_offset.x
        y = event.screen_offset.y
        for item in self.query(EditorTabItem):
            region = item.region
            if not (region.x <= x < region.x + region.width):
                continue
            if not (region.y <= y < region.y + region.height):
                continue
            close = item.query_one(TabCloseBtn)
            if x >= close.region.x:
                self.post_message(self.TabClosed(item.filepath))
            else:
                self.post_message(self.TabSelected(item.filepath))
            return
