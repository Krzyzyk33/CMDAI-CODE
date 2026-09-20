from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from ...core.providers import PROVIDERS_CATALOG
from ...core.settings import get_settings


class AddModelModal(ModalScreen[Optional[str]]):

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
        if self.provider_id == "ollama":
            placeholder = "e.g. llama3:latest or qwen2.5:7b"
        elif self.provider_id == "huggingface":
            placeholder = "e.g. meta-llama/Llama-3.3-70B-Instruct"
        else:
            placeholder = "Enter model name or identifier..."

        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"Add Model: {provider_name}", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            yield Static(f"[dim]Enter model identifier for [bold #58a6ff]{provider_name}[/]:[/dim]")

            yield Input(
                value="",
                placeholder=placeholder,
                id="model-name-input",
            )

            with Horizontal(id="modal-footer"):
                yield Static("[enter: Add & Select Model]  [esc: Cancel]")

    def on_mount(self) -> None:
        self.query_one("#model-name-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        inp = self.query_one("#model-name-input", Input)
        model_name = inp.value.strip()
        if model_name:
            self.dismiss(model_name)
        else:
            self.dismiss(None)

    @on(Input.Submitted, "#model-name-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()
