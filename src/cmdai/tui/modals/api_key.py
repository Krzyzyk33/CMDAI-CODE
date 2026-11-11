from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ...core.providers import PROVIDERS_CATALOG
from ...core.settings import get_settings


class ApiKeyModal(ModalScreen[bool]):

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "save", "Save"),
    ]

    def __init__(self, provider_id: str, **kwargs):
        super().__init__(**kwargs)
        self.provider_id = provider_id
        self.info = PROVIDERS_CATALOG.get(provider_id)
        self.settings = get_settings()

    @property
    def _has_editable_url(self) -> bool:
        return (
            self.provider_id == "localllmapi"
            or self.provider_id in self.settings.get_custom_providers()
        )

    def compose(self) -> ComposeResult:
        provider_name = self.info.name if self.info else self.provider_id
        key_url = self.info.key_url if self.info else ""
        current_key = self.settings.get_api_key(self.provider_id)

        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"Configure API Key: {provider_name}", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")

            if key_url:
                yield Static(f"[dim]Get API key at: [bold #58a6ff]{key_url}[/][/dim]\n")

            yield Input(
                value=current_key,
                password=True,
                placeholder="Enter API Key here...",
                id="api-key-input",
            )

            if self._has_editable_url:
                default_url = self.info.base_url if self.info else "http://127.0.0.1:1000/v1"
                current_url = self.settings.get_provider_base_url(self.provider_id, default_url)
                yield Static("\n[dim]Server URL (localhost or LAN IP, e.g. http://192.168.1.50:1000/v1):[/dim]")
                yield Input(
                    value=current_url,
                    placeholder=default_url,
                    id="api-url-input",
                )

            with Horizontal(id="modal-footer"):
                yield Static("[enter: Save & Fetch Models]")

    def on_mount(self) -> None:
        self.query_one("#api-key-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_save(self) -> None:
        inp = self.query_one("#api-key-input", Input)
        new_key = inp.value.strip()
        self.settings.set_api_key(self.provider_id, new_key)
        if self._has_editable_url:
            try:
                url_inp = self.query_one("#api-url-input", Input)
                self.settings.set_provider_base_url(self.provider_id, url_inp.value.strip())
            except Exception:
                pass
            try:
                from ...core.resource_limits import _LIVE_CONTEXT_LIMITS
                prefix = f"{self.provider_id}:"
                for k in list(_LIVE_CONTEXT_LIMITS.keys()):
                    if k.startswith(prefix):
                        _LIVE_CONTEXT_LIMITS.pop(k, None)
            except Exception:
                pass
        if self.provider_id == "opencode":
            self.settings.set_api_key("opencode_zen", new_key)
        self.dismiss(True)

    @on(Input.Submitted, "#api-key-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()

    @on(Input.Submitted, "#api-url-input")
    def on_url_submitted(self, event: Input.Submitted) -> None:
        self.action_save()
