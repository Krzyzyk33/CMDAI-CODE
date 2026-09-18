from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ...core.providers import PROVIDERS_CATALOG
from ...core.settings import get_settings


class ApiKeyModal(ModalScreen[bool]):
    """Modal dialog for entering and saving API keys."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "save", "Save"),
    ]

    def __init__(self, provider_id: str, **kwargs):
        super().__init__(**kwargs)
        self.provider_id = provider_id
        self.info = PROVIDERS_CATALOG.get(provider_id)
        self.settings = get_settings()

    def compose(self) -> ComposeResult:
        provider_name = self.info.name if self.info else self.provider_id
        key_url = self.info.key_url if self.info else ""
        current_key = self.settings.get_api_key(self.provider_id)

        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"Configure API Key: {provider_name}", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            if key_url:
                yield Static(f"[dim]Get API key at: [bold #58a6ff]{key_url}[/][/dim]\n")

            yield Input(
                value=current_key,
                password=True,
                placeholder="Enter API Key here...",
                id="api-key-input",
            )

            with Horizontal(id="modal-footer"):
                yield Static("[enter: Save & Fetch Models]  [esc: Cancel]")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_save(self) -> None:
        inp = self.query_one("#api-key-input", Input)
        new_key = inp.value.strip()
        self.settings.set_api_key(self.provider_id, new_key)
        self.dismiss(True)
