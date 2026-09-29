from textual.containers import Vertical
from textual.widgets import Static
from textual.message import Message
from rich.text import Text

FALLBACK_TEXT = "CMDAI EDITOR"

try:
    from .logo_variants import LOGO, LOGO_MAX
except Exception:
    LOGO = FALLBACK_TEXT
    LOGO_MAX = len(FALLBACK_TEXT)

def pick_logo(available_width: int = 0) -> str:

    return LOGO

class EmptyEditorWelcome(Vertical):

    DEFAULT_CSS = """EmptyEditorWelcome {
        width: 100%;
        height: 1fr;
        align: center middle;
        background: #090d13;
        padding: 1 2;
    }
    EmptyEditorWelcome.hidden {
        display: none;
    }

    #welcome-ascii {
        width: 100%;
        color: #ffffff;
        text-align: center;
        margin-bottom: 1;
        overflow-x: hidden;
        overflow-y: hidden;
    }
    #welcome-subtext {
        width: 100%;
        color: #8b949e;
        text-align: center;
    }
"""

    class ActionTriggered(Message):
        def __init__(self, action_id: str):
            super().__init__()
            self.action_id = action_id

    GRADIENT = ["#1f6feb", "#3d8bfd", "#58a6ff", "#79b8ff", "#a5d6ff", "#e6edf3"]

    def compose(self):

        yield Static("", id="welcome-ascii")
        yield Static(
            Text.from_markup("[dim #c9d1d9]Advanced terminal code editor[/]"),
            id="welcome-subtext",
        )

    def on_mount(self) -> None:
        self.call_after_refresh(self._refresh_logo)

    def on_resize(self, event) -> None:
        if self.has_class("hidden"):
            return
        self.call_after_refresh(self._refresh_logo)

    def _refresh_logo(self) -> None:

        try:
            static = self.query_one("#welcome-ascii", Static)
        except Exception:
            return
        ascii_art = pick_logo()
        if static.content and str(static.content) == ascii_art:
            return
        static.update(self._colorize(ascii_art))

    def _colorize(self, ascii_art: str) -> Text:

        text = Text()
        lines = ascii_art.rstrip("\n").split("\n")
        total = max((len(l.rstrip()) for l in lines), default=1) or 1
        stops = len(self.GRADIENT)
        for row, line in enumerate(lines):
            if row:
                text.append("\n")
            for col, ch in enumerate(line):
                pos = (col / total) * (stops - 1)
                idx = min(stops - 1, int(pos))
                text.append(ch, style=f"bold {self.GRADIENT[idx]}")
        return text
