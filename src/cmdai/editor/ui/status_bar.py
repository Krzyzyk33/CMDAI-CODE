import os
import subprocess
from textual import on

from .clickable import MouseActivatable
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Static
from rich.text import Text
from ..icons.registry import icons

class PanelToggle(MouseActivatable, Static):

    DEFAULT_CSS = """
    PanelToggle {
        width: auto;
        height: 1;
        color: #8b949e;
        background: transparent;
    }
    PanelToggle:hover {
        background: #21262d;
        color: #ffffff;
    }
    """

    class Clicked(Message):
        pass

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._open = False

    def set_open(self, open_: bool) -> None:
        self._open = open_
        self.update(Text.from_markup(
            f"[#8b949e]{'▾' if open_ else '▴'}[/]  PANEL"))

    def on_activate(self) -> None:
        self.post_message(self.Clicked())

class EditorStatusBar(Horizontal):

    DEFAULT_CSS = """EditorStatusBar {
        dock: bottom;
        height: 2;
        background: #0d1117;
        border-top: solid #21262d;
        padding: 0 1;
        color: #8b949e;
    }
    #status-panel-toggle {
        width: auto;
        margin-right: 2;
        color: #8b949e;
        background: transparent;
        text-style: none;
    }
    #status-panel-toggle:hover {
        color: #ffffff;
        background: #21262d;
    }
    #status-git {
        width: auto;
        margin-right: 2;
        background: #161b22;
        padding: 0 1;
        color: #bc8cff;
    }
    # Nazwa pliku jest wazniejsza niz pozycja kursora, wiec dostaje
    # gwarantowane `min-width`. Bez tego `width: 1fr` kurczylo sie do
    # zera przy szerokosci ~90 kolumn (zmierzono: `file=8` na 80
    # kolumnach, nazwa znikala z ekranu calkiem), bo `status-info` jest
    # `width: auto` i bierze swoja dlugosc w pierwszej kolejnosci.
    # `text-overflow: ellipsis` ucina dlugie nazwy zamiast je gubic.
    #status-file {
        width: 1fr;
        min-width: 12;
        text-overflow: ellipsis;
        color: #e6edf3;
    }
    #status-diag {
        width: auto;
        padding: 0 1;
        color: #8b949e;
        text-style: none;
    }
    # `padding-left` daje odstep od poprzedniego widgetu. Przy
    # szerokim ekranie `1fr` i tak go "wchlonie", a przy waskim
    # (`file` zablokowany na `min-width`) bez tego `main.py` i
    # `Ln 1, Col 1` kleilo sie do siebie bez spacji.
    #status-info {
        width: auto;
        padding: 0 1;
        color: #8b949e;
    }
"""

    def __init__(self, workdir: str, **kwargs):
        super().__init__(**kwargs)
        self.workdir = workdir
        self.branch = self._detect_branch()

    def _detect_branch(self) -> str:
        try:
            out = subprocess.check_output(
                ["git", "branch", "--show-current"],
                cwd=self.workdir,
                stderr=subprocess.DEVNULL,
                text=True
            ).strip()
            return out or "git"
        except Exception:
            return ""

    def compose(self):
        yield PanelToggle(id="status-panel-toggle")
        git_label = f"{icons.get_styled('git-branch')}{self.branch}" if self.branch else ""
        yield Static(Text.from_markup(git_label), id="status-git")
        yield Static("Ready", id="status-file")
        yield Static("", id="status-diag")
        yield Static("Ln 1, Col 1 · UTF-8", id="status-info")

    def on_mount(self) -> None:
        self.query_one("#status-panel-toggle", PanelToggle).set_open(False)

    @on(PanelToggle.Clicked)
    def _on_panel_toggle(self, event: PanelToggle.Clicked) -> None:
        event.stop()
        self.app.action_toggle_panel()

    def set_panel_open(self, open_: bool) -> None:

        self.query_one("#status-panel-toggle", PanelToggle).set_open(open_)

    def update_cursor(self, row: int, col: int, total_lines: int, lang: str = "text",
                      errors: int = 0, warnings: int = 0) -> None:
        lang_icon = icons.get_styled("code", with_space=True)
        info = (f"Ln {row}, Col {col}  [dim]•[/]  {total_lines} lines  "
                f"[dim]•[/]  {lang_icon}[#58a6ff]{lang.upper()}[/]")
        info += "  [dim]•[/]  UTF-8"
        self.query_one("#status-info", Static).update(Text.from_markup(info))

        diag = Text()
        if errors:
            diag.append(f"x {errors}", style="#f85149")
        if warnings:
            if errors:
                diag.append("  ")
            diag.append(f"! {warnings}", style="#d29922")
        self.query_one("#status-diag", Static).update(diag)

    def update_file(self, filepath: str, is_modified: bool = False) -> None:
        fname = os.path.basename(filepath) if filepath else "No file"
        icon_str = icons.get_file_icon(filepath) if filepath else ""
        if is_modified:
            status_text = f"{icon_str}[bold #e6edf3]{fname}[/] [bold #d29922]● modified[/]"
        else:
            status_text = f"{icon_str}[bold #e6edf3]{fname}[/] [#3fb950]✔[/]"
        self.query_one("#status-file", Static).update(Text.from_markup(status_text))
