import os
from typing import Dict, List, Tuple

from rich.text import Text
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Static

from .clickable import MouseActivatable

ERROR_BG = "#3a1418"
WARNING_BG = "#332a10"
ERROR_FG = "#f85149"
WARNING_FG = "#d29922"
FILE_FG = "#58a6ff"

CHEVRON_CLOSED = "\u25f8"
CHEVRON_OPEN = "\u25be"
WARN_SIGN = "\u203c"
CROSS = "x"
BANG = "!"

ENGINE_NOTE = "(tree-sitter + ruff)"

Item = Tuple[str, int, int, str, str]

def _counts(items: List[Item]) -> Tuple[int, int]:
    errors = sum(1 for i in items if i[3] == "error")
    return errors, len(items) - errors

class ProblemRow(MouseActivatable, Static):

    DEFAULT_CSS = """
    ProblemRow {
        width: 100%;
        height: 1;
        padding: 0 2;
        background: transparent;
        color: #c9d1d9;
    }

    ProblemRow:hover {
        background: #21262d;
        color: #ffffff;
    }
    """

    class Clicked(Message):
        def __init__(self, path: str, row: int, col: int) -> None:
            super().__init__()
            self.path = path
            self.row = row
            self.col = col

    def __init__(self, path: str, row: int, col: int, markup: Text, **kwargs):
        super().__init__(markup, **kwargs)
        self._path = path
        self._row = row
        self._col = col

    def on_activate(self) -> None:
        self.post_message(self.Clicked(self._path, self._row, self._col))

class ProblemHeader(MouseActivatable, Static):

    DEFAULT_CSS = """
    ProblemHeader {
        width: 100%;
        height: 1;
        padding: 0 1;
        color: #6e7681;
        background: transparent;
    }

    ProblemHeader:hover {
        background: #21262d;
        color: #e6edf3;
    }
    """

    class Clicked(Message):
        pass

    def on_activate(self) -> None:
        self.post_message(self.Clicked())

class FileGroupHeader(MouseActivatable, Static):

    DEFAULT_CSS = """
    FileGroupHeader {
        width: 100%;
        height: 1;
        padding: 0 1;
        background: transparent;
    }

    FileGroupHeader:hover {
        background: #21262d;
    }
    """

    class Toggle(Message):
        def __init__(self, path: str) -> None:
            super().__init__()
            self.path = path

    def __init__(self, path: str, name: str, errors: int, warnings: int,
                 collapsed: bool, **kwargs):
        super().__init__(self._build(path, name, errors, warnings, collapsed),
                         **kwargs)
        self._path = path

    @staticmethod
    def _build(path: str, name: str, errors: int, warnings: int,
               collapsed: bool) -> Text:
        text = Text()
        text.append(CHEVRON_CLOSED if collapsed else CHEVRON_OPEN,
                    style="#8b949e")
        text.append(" ")
        text.append(name, style=f"bold {FILE_FG}")

        pad = 2
        if errors:
            text.append(" " * pad)
            text.append(f"{CROSS} {errors}", style=ERROR_FG)
        if warnings:
            text.append("  ")
            text.append(f"{BANG} {warnings}", style=WARNING_FG)
        return text

    def on_activate(self) -> None:
        self.post_message(self.Toggle(self._path))

class ProblemsPane(VerticalScroll):

    DEFAULT_CSS = """
    ProblemsPane {
        width: 100%;
        height: 100%;
        background: #090d13;
        scrollbar-size-vertical: 1;
        scrollbar-color: #30363d;
        padding: 0 1;
    }
    """

    class JumpRequested(Message):
        def __init__(self, path: str, row: int, col: int) -> None:
            super().__init__()
            self.path = path
            self.row = row
            self.col = col

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._items: List[Item] = []
        self._engine_note: str = ""
        self._scan_note: str = ""

        self._filter = "all"

        self._collapsed: Dict[str, bool] = {}

        self._seeded = False

    def on_mount(self) -> None:
        self._seed()

    def _seed(self) -> None:

        if self._seeded:
            return
        self._seeded = True
        note = f"{self._engine_note} {ENGINE_NOTE}".strip()
        self.mount(Static(Text(f"Checking... {note}".strip()),
                          classes="problem-empty"))

    def set_engine_note(self, note: str) -> None:

        if note != self._engine_note:
            self._engine_note = note
            if self._seeded:
                self.rebuild()

    def set_scan_note(self, note: str) -> None:

        if note != self._scan_note:
            self._scan_note = note
            if self._seeded:
                self.rebuild()

    def set_problems(self, items) -> None:

        norm: List[Item] = []
        for it in items:
            if hasattr(it, "path"):
                norm.append((it.path, int(it.row), int(it.col),
                             str(it.severity), str(it.message)))
            else:
                norm.append(tuple(it))
        self._items = norm

        live = {i[0] for i in self._items}
        for gone in [p for p in self._collapsed if p not in live]:
            del self._collapsed[gone]
        self.rebuild()

    def clear(self) -> None:
        self._items = []
        self.rebuild()

    def count(self) -> Tuple[int, int]:

        return _counts(self._items)

    def total(self) -> int:
        return len(self._items)

    def is_collapsed(self, path: str) -> bool:
        return self._collapsed.get(path, False)

    def set_collapsed(self, path: str, collapsed: bool) -> None:
        if self._collapsed.get(path, False) == collapsed:
            return
        self._collapsed[path] = collapsed
        self.rebuild()

    def collapse_all(self, collapsed: bool = True) -> None:
        for path, _, _, _, _ in self._items:
            self._collapsed[path] = collapsed
        self.rebuild()

    def _visible(self) -> List[Item]:
        if self._filter == "errors":
            return [i for i in self._items if i[3] == "error"]
        return list(self._items)

    def _header_text(self) -> str:

        vis = self._visible()
        errors, warnings = self.count()
        if self._filter == "errors":
            shown = f"{len(vis)} shown of {errors + warnings}  [errors only]"
        else:
            shown = f"{len(vis)} in whole project  [click: errors only]"
        if self._scan_note:
            shown += "  " + self._scan_note
        return shown

    def on_problem_header_clicked(self, event: ProblemHeader.Clicked) -> None:
        event.stop()
        self._filter = "errors" if self._filter == "all" else "all"
        self.rebuild()

    def on_file_group_header_toggle(self, event: FileGroupHeader.Toggle) -> None:
        event.stop()
        self.set_collapsed(event.path, not self.is_collapsed(event.path))

    def on_problem_row_clicked(self, event: ProblemRow.Clicked) -> None:
        event.stop()
        self.post_message(self.JumpRequested(event.path, event.row, event.col))

    def _grouped(self) -> List[Tuple[str, List[Item]]]:

        order: List[str] = []
        groups: Dict[str, List[Item]] = {}
        for path, row, col, sev, msg in self._visible():
            if path not in groups:
                groups[path] = []
                order.append(path)
            groups[path].append((path, row, col, sev, msg))
        order.sort(key=lambda p: (
            0 if any(i[3] == "error" for i in groups[p]) else 1,
            -sum(1 for i in groups[p] if i[3] == "error"),
            os.path.basename(p).lower(),
        ))
        return [(p, groups[p]) for p in order]

    def rebuild(self) -> None:
        self._seeded = True
        self.remove_children()
        self.mount(ProblemHeader(Text(self._header_text(), style="#6e7681")))

        vis = self._visible()
        if not vis:
            if self._filter == "errors" and self._items:
                self.mount(Static(
                    Text("No errors - only warnings hidden  "
                         "(click the header to show them)",
                         style="#6e7681"),
                    classes="problem-empty"))
            else:
                self.mount(Static(
                    Text("No problems found  " + self._engine_note,
                         style="#6e7681"),
                    classes="problem-empty"))
            return

        for path, group in self._grouped():
            errors, warnings = _counts(group)
            name = os.path.basename(path) or path
            collapsed = self.is_collapsed(path)
            self.mount(FileGroupHeader(path, name, errors, warnings, collapsed))
            if collapsed:

                continue
            for _p, row, col, sev, msg in sorted(group, key=lambda i: (i[1], i[2])):
                label = Text()

                label.append(CROSS if sev == "error" else BANG,
                             style=ERROR_FG if sev == "error" else WARNING_FG)
                label.append(f"  {row + 1}:{col + 1}  ", style="#6e7681")
                label.append(msg,
                             style=ERROR_FG if sev == "error" else WARNING_FG)
                self.mount(ProblemRow(path, row, col, label))
