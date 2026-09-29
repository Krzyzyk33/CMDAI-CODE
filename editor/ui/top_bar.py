from textual.containers import Horizontal
from textual.widgets import Static
from textual.message import Message
from rich.text import Text
from ..icons.registry import icons
from .clickable import MouseActivatable

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
        margin-right: 1;
    }
    #top-spacer {
        width: 1fr;
    }
    .tool-icon {
        width: auto;
        height: 1;
        min-width: 2;
        background: transparent;
        padding: 0;
        margin-left: 2;
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
        yield BrandMenuLabel(
            Text.from_markup("[bold #58a6ff]⌬ CMDAI[/] [bold #ffffff]EDITOR[/]"),
            id="btn-brand-menu"
        )
        yield Static("", id="top-spacer")

        run = ToolIcon(Text.from_markup(f"[#3fb950]{icons.get_raw('play')}[/]"),
                       id="btn-run", classes="tool-icon")
        run.tooltip = "Run active file (Ctrl+F5)"
        yield run

        git = ToolIcon(Text.from_markup(f"[#bc8cff]{icons.get_raw('git-branch')}[/]"),
                       id="btn-git", classes="tool-icon")
        git.tooltip = "Git branches"
        yield git

        chk = ToolIcon(Text.from_markup(f"[#d29922]{icons.get_raw('history')}[/]"),
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
