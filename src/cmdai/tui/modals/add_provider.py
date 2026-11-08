"""Modal dialog for adding custom OpenAI-compatible providers in /models."""

from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from ...core.providers import PROVIDERS_CATALOG, ProviderInfo
from ...core.settings import get_settings


class AddProviderModal(ModalScreen[Optional[str]]):
    """Modal for creating and configuring custom AI providers."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = get_settings()

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="add-provider-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Add Custom AI Provider", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")

            yield Static("[bold white]Provider Name[/bold white] [dim](e.g. Groq, Together AI, Local vLLM)[/dim]")
            yield Input(placeholder="e.g. Groq", id="prov-name")

            yield Static("[bold white]Base API URL[/bold white] [dim](OpenAI compatible endpoint ending in /v1)[/dim]")
            yield Input(placeholder="https://api.groq.com/openai/v1", id="prov-url")

            yield Static("[bold white]API Key[/bold white] [dim](optional if local/keyless)[/dim]")
            yield Input(placeholder="Paste API key here...", password=True, id="prov-key")

            yield Static("[bold white]Default Model ID[/bold white] [dim](optional initial model)[/dim]")
            yield Input(placeholder="e.g. llama-3.3-70b-versatile", id="prov-model")

            with Horizontal(id="modal-footer"):
                yield Static("enter on last field: Add Provider  ·  ctrl+s: Save  ·  esc: Cancel")

    def on_mount(self) -> None:
        self.query_one("#prov-name", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        name = self.query_one("#prov-name", Input).value.strip()
        url = self.query_one("#prov-url", Input).value.strip()
        key = self.query_one("#prov-key", Input).value.strip()
        model = self.query_one("#prov-model", Input).value.strip()

        if not name:
            self.notify("Provider Name is required.", severity="error")
            self.query_one("#prov-name", Input).focus()
            return
        if not url:
            self.notify("Base API URL is required.", severity="error")
            self.query_one("#prov-url", Input).focus()
            return

        pid = self.settings.add_custom_provider(
            name=name,
            base_url=url,
            api_key=key,
            default_model=model,
        )

        PROVIDERS_CATALOG[pid] = ProviderInfo(
            id=pid,
            name=name,
            base_url=url.rstrip("/"),
            key_url="",
            provider_type="cloud",
            default_models=[model] if model else [],
            supports_remote_fetch=True,
            models_endpoint="/models",
        )

        self.dismiss(pid)

    @on(Input.Submitted, "#prov-name")
    def on_name_submitted(self) -> None:
        self.query_one("#prov-url", Input).focus()

    @on(Input.Submitted, "#prov-url")
    def on_url_submitted(self) -> None:
        self.query_one("#prov-key", Input).focus()

    @on(Input.Submitted, "#prov-key")
    def on_key_submitted(self) -> None:
        self.query_one("#prov-model", Input).focus()

    @on(Input.Submitted, "#prov-model")
    def on_model_submitted(self) -> None:
        self.action_save()
