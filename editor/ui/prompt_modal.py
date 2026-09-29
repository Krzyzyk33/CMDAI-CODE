from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.geometry import Spacing
from textual.widget import Widget
from textual.widgets import Button, Input, Static
from textual.message import Message

class PromptOverlay(Vertical):

    DEFAULT_CSS = """PromptOverlay {
        layer: overlay;
        width: auto;
        height: auto;
        background: transparent;
        display: none;
    }
    PromptOverlay.open {
        display: block;
    }
    #prompt-dialog {
        width: auto;
        height: auto;
        background: #161b22;
        border: round #30363d;
        padding: 0 2;
    }
    #prompt-head {
        width: 100%;
        height: 1;
    }
    #prompt-title {
        width: 1fr;
        color: #58a6ff;
        text-style: bold;
    }

    #prompt-esc {
        width: auto;
        color: #6e7681;
        text-align: right;
    }
    #prompt-target {
        width: 100%;
        height: 1;
        color: #6e7681;
    }

    #prompt-input {
        width: 52;
        height: 3;
        background: #0d1117;
        color: #e6edf3;
        border: round #21262d;
        padding: 0 1;
    }
    #prompt-input:focus {
        border: round #58a6ff;
    }

    #prompt-actions {
        width: 100%;
        height: 1;
        align-horizontal: center;
    }
    #prompt-ok {
        width: 12;
        height: 1;
        border: none;
        background: #238636;
        color: #ffffff;
    }
    #prompt-ok:hover {
        background: #2ea043;
    }
"""

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class Cancelled(Message):
        pass

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._on_submit = None

        self._focus_before: object | None = None

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def action_cancel(self) -> None:
        self._finish("")

    def compose(self):
        with Vertical(id="prompt-dialog"):
            with Horizontal(id="prompt-head"):
                yield Static("", id="prompt-title")
                yield Static("esc", id="prompt-esc")
            yield Static("", id="prompt-target")
            yield Input(id="prompt-input")
            with Horizontal(id="prompt-actions"):
                yield Button("create", id="prompt-ok")

    def open(self, title: str, on_submit, placeholder: str = "",
             default_value: str = "", target: str = "") -> None:
        self._on_submit = on_submit

        self._focus_before = self.screen.focused
        self.query_one("#prompt-title", Static).update(title)
        self.query_one("#prompt-target", Static).update(
            f"→ {target}" if target else ""
        )
        inp = self.query_one("#prompt-input", Input)
        inp.placeholder = placeholder
        inp.value = default_value
        self.add_class("open")
        self.call_after_refresh(self._reposition)

        inp.focus()
        inp.cursor_position = len(inp.value)
        self.call_after_refresh(self._grab_focus)

    def _grab_focus(self) -> None:

        try:
            inp = self.query_one("#prompt-input", Input)
        except Exception:
            return
        if not self.is_open():
            return
        if self.screen.focused is inp:
            return
        inp.focus()

    def _restore_focus(self) -> None:

        area = self.screen.query_one("#code-area", None)
        if area is not None and not area.has_class("hidden"):
            area.focus()
            self._focus_before = None
            return
        target = self._focus_before
        self._focus_before = None
        if isinstance(target, Widget) and target.is_attached:
            target.focus()

    def _reposition(self) -> None:

        dialog = self.query_one("#prompt-dialog")

        w, h = dialog.region.width, dialog.region.height
        screen_w, screen_h = self.screen.size.width, self.screen.size.height
        if not w or not h:
            return
        self.styles.margin = Spacing(
            max(0, (screen_h - h) // 2),
            0,
            0,
            max(0, (screen_w - w) // 2),
        )

    def is_open(self) -> bool:
        return self.has_class("open")

    def dialog_region(self):
        return self.query_one("#prompt-dialog").region

    def on_resize(self, event: Resize) -> None:

        if self.has_class("open"):
            self.call_after_refresh(self._reposition)

    def _finish(self, value: str) -> None:
        self.remove_class("open")
        cb, self._on_submit = self._on_submit, None
        if cb:
            cb(value)

        self.call_after_refresh(self._restore_focus)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._finish(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "prompt-ok":
            self._finish(self.query_one("#prompt-input", Input).value.strip())
        else:
            self._finish("")

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self._finish("")
