import os
from typing import Dict, List, Optional
from .syntax import detect_language

class EditorBuffer:

    def __init__(self, filepath: str, content: str = ""):
        self.filepath = os.path.abspath(filepath)
        self.filename = os.path.basename(filepath)
        self.content = content
        self.saved_content = content
        self.is_modified = False
        self.language = detect_language(filepath)
        self.cursor_row = 0
        self.cursor_col = 0

        self.is_new = False

    def update_content(self, new_content: str) -> None:
        self.content = new_content
        self.is_modified = (self.content != self.saved_content)

    def mark_saved(self) -> None:
        self.saved_content = self.content
        self.is_modified = False

    @property
    def line_count(self) -> int:
        return len(self.content.splitlines()) or 1

class BufferManager:

    def __init__(self):
        self.buffers: Dict[str, EditorBuffer] = {}
        self.active_path: Optional[str] = None

        self.last_save_created_file: bool = False

    @property
    def active_buffer(self) -> Optional[EditorBuffer]:
        if self.active_path and self.active_path in self.buffers:
            return self.buffers[self.active_path]
        return None

    def open_file(self, filepath: str) -> EditorBuffer:
        norm = os.path.abspath(filepath)
        if norm in self.buffers:
            self.active_path = norm
            return self.buffers[norm]

        content = ""
        if os.path.isfile(norm):
            try:
                with open(norm, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                with open(norm, "r", encoding="latin-1", errors="replace") as f:
                    content = f.read()

        buf = EditorBuffer(norm, content)
        buf.is_new = not os.path.isfile(norm)
        self.buffers[norm] = buf
        self.active_path = norm
        return buf

    def save_file(self, filepath: Optional[str] = None) -> bool:
        target = filepath or self.active_path
        if not target:
            return False
        norm = os.path.abspath(target)
        if norm not in self.buffers:
            return False

        buf = self.buffers[norm]
        parent = os.path.dirname(norm)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        with open(norm, "w", encoding="utf-8") as f:
            f.write(buf.content)

        self.last_save_created_file = buf.is_new
        buf.is_new = False
        buf.mark_saved()
        return True

    def save_as(self, new_path: str, content: str) -> bool:

        old_key = self.active_path
        norm = os.path.abspath(new_path)
        if old_key is not None and old_key in self.buffers:
            buf = self.buffers.pop(old_key)
        else:
            buf = EditorBuffer(norm, content)

        buf.filepath = norm
        buf.filename = os.path.basename(norm)
        buf.content = content

        self.last_save_created_file = True
        buf.is_new = False

        parent = os.path.dirname(norm)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        with open(norm, "w", encoding="utf-8") as f:
            f.write(content)

        self.buffers[norm] = buf
        self.active_path = norm
        buf.mark_saved()
        return True

    def close_buffer(self, filepath: str) -> Optional[str]:
        norm = os.path.abspath(filepath)
        if norm in self.buffers:
            del self.buffers[norm]

        if self.active_path == norm:
            if self.buffers:
                self.active_path = list(self.buffers.keys())[-1]
            else:
                self.active_path = None
        return self.active_path

    def list_open_buffers(self) -> List[EditorBuffer]:
        return list(self.buffers.values())
