from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class ActionMenuModal(ModalScreen[str]):

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "select_action", "Execute"),
    ]

    ACTIONS = [
        ("copy", "Copy only this user message"),
        ("revert", "Remove user + response and revert files"),
        ("delete", "Remove user + response only"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Message Actions", id="modal-title")
                yield Static("[esc]", id="modal-esc")
            yield OptionList(
                Option("  Copy user message"),
                Option("  Revert turn + files"),
                Option("  Delete turn"),
                id="modal-list",
            )
            with Horizontal(id="modal-footer"):
                yield Static("[enter: Execute action]  [esc: Close]")

    def action_cancel(self) -> None:
        self.dismiss("")

    def action_select_action(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        if ol.highlighted is not None and ol.highlighted < len(self.ACTIONS):
            self.dismiss(self.ACTIONS[ol.highlighted][0])

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_select_action()
