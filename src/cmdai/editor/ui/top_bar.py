import os

from textual.containers import Horizontal
from textual.widgets import Static
from textual.message import Message
from rich.text import Text
from ..icons.registry import icons
from .clickable import MouseActivatable
from .quick_open import QuickOpen

class BrandMenuLabel(MouseActivatable, Static):
    class Clicked(Message):
        pass

    def on_activate(self) -> None:
        self.post_message(self.Clicked())

class ToolIcon(MouseActivatable, Static):
    class Clicked(Message):
        def __init__(self, action_id: str):
            super().__init__()
            self.action_id = action_id

    def on_activate(self) -> None:
        self.post_message(self.Clicked(self.id or ""))

class EditorTopBar(Horizontal):

    def __init__(self, root_dir: str = ".", **kwargs) -> None:
        super().__init__(**kwargs)
        self.root_dir = os.path.abspath(root_dir)

    DEFAULT_CSS = """EditorTopBar {
        dock: top;
        height: 1;
        background: #161b22;
        padding: 0 1;
        color: #8b949e;
    }
    #btn-brand-menu {
        width: auto;
        height: 1;
        background: transparent;
        color: #e6edf3;
    }

    /* Trzy strefy: `1fr auto 1fr` - pola z obu stron maja ten sam
       udzial w szerokosci, wiec strefa srodkowa lezy DOKLADNIE na
       srodku ekranu, niezaleznie od tego jak szeroka jest marka
       po lewej i jak szeroki jest zbior ikon po prawej. Przy
       poprzednim ukladzie (`auto 1fr auto`) pole bylo przyklejone do
       lewej i rozciagalo sie do `max-width: 60`.
       To jest ten sam trik, ktory stosuje VS Code w pasku tytulu. */
    #top-left {
        width: 1fr;
        height: 1;
        content-align: left middle;
    }
    #quick-open {
        width: 46;
        height: 1;
    }
    #top-right {
        width: 1fr;
        height: 1;
        content-align: right middle;
    }
    #top-right-spacer {
        width: 1fr;
        height: 1;
    }
    /* `margin-left` na ikonie = odstep MIEDZY nimi. Ostatnia ikona
       konczy sie na prawej krawedzi obszaru tresci, ktora jest o
       `padding: 0 1` wezsze niz szerokosc ekranu - zmierzone:
       checkpoints `x=117, width=2`, koniec `119`, a `EditorTopBar`
       ma `padding-right: 1`, wiec to jest pozycja 118. */
    .tool-icon {
        width: auto;
        height: 1;
        min-width: 2;
        background: transparent;
        padding: 0;
        margin-left: 2;
    }
    .tool-icon:hover {
        background: #21262d;
    }

    .tool-icon.-dimmed {
        color: #484f58;
    }

    .tool-icon.-running {
        background: #21262d;
    }

    #btn-run {
        min-width: 1;
    }
"""

    def compose(self):
        with Horizontal(id="top-left"):
            yield BrandMenuLabel(
                Text.from_markup(
                    "[bold #58a6ff]⌬ CMDAI[/] [bold #ffffff]EDITOR[/]"),
                id="btn-brand-menu"
            )
        yield QuickOpen(self.root_dir, id="quick-open")
        with Horizontal(id="top-right"):
            yield Static("", id="top-right-spacer")
            run = ToolIcon(
                Text.from_markup(f"[#3fb950]{icons.get_raw('play')}[/]"),
                id="btn-run", classes="tool-icon")
            run.tooltip = "Run active file (Ctrl+F5)"
            yield run

            git = ToolIcon(
                Text.from_markup(f"[#bc8cff]{icons.get_raw('git-branch')}[/]"),
                id="btn-git", classes="tool-icon")
            git.tooltip = "Git branches"
            yield git

            chk = ToolIcon(
                Text.from_markup(f"[#d29922]{icons.get_raw('history')}[/]"),
                id="btn-checkpoints", classes="tool-icon")
            chk.tooltip = "Checkpoints"
            yield chk

    def refresh_run_button(self, runnable: bool, running: bool = False) -> None:

        try:
            btn = self.query_one("#btn-run", ToolIcon)
        except Exception:
            return
        name = "stop" if running else "play"
        color = "#f85149" if running else ("#6e7681" if not runnable else "#3fb950")
        btn.update(Text.from_markup(f"[{color}]{icons.get_raw(name)}[/]"))
        btn.tooltip = ("Stop (Ctrl+Shift+F5)" if running
                       else "Run active file (Ctrl+F5)")
        btn.set_class(not runnable, "-dimmed")
        btn.set_class(running, "-running")

    def refresh_labels(self):
        self.query_one("#btn-git", ToolIcon).update(
            Text.from_markup(f"[#bc8cff]{icons.get_raw('git-branch')}[/]"))
        self.query_one("#btn-checkpoints", ToolIcon).update(
            Text.from_markup(f"[#d29922]{icons.get_raw('history')}[/]"))
