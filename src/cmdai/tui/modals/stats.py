from typing import Any, Dict
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class StatsModal(ModalScreen[None]):
    """Modal showing session usage and speed metrics."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    def __init__(self, stats_data: Dict[str, Any], **kwargs):
        super().__init__(**kwargs)
        self.stats = stats_data

    def compose(self) -> ComposeResult:
        tokens_in = self.stats.get("tokens_in", 0)
        tokens_out = self.stats.get("tokens_out", 0)
        tools_run = self.stats.get("tools_run", 0)
        tok_s = self.stats.get("tok_per_sec", 0.0)

        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Session Statistics", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            yield Static(
                f"[bold white]Total Tokens In:[/]     [#58a6ff]{tokens_in}[/]\n"
                f"[bold white]Total Tokens Out:[/]    [#58a6ff]{tokens_out}[/]\n"
                f"[bold white]Avg Speed:[/]           [#58a6ff]{tok_s:.1f} tok/s[/]\n"
                f"[bold white]Tool Calls Executed:[/] [#58a6ff]{tools_run}[/]\n"
            )

            with Horizontal(id="modal-footer"):
                yield Static("[enter/esc: Close]")

    def action_close(self) -> None:
        self.dismiss(None)
