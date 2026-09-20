from typing import Any, Dict, List, Optional
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core.releases import get_releases


class ReleasesModal(ModalScreen[None]):
    """Changelog - release menu on top, markdown preview below. All English."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "open_window", "Open"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.releases: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Changelog", id="modal-title")
                yield Static("[esc]", id="modal-esc")
            yield Static("Select a release to preview.", id="releases-hint")
            yield OptionList(id="modal-list")
            with VerticalScroll(id="releases-preview"):
                yield Static("Select a release.", id="releases-body")
            with Horizontal(id="modal-footer"):
                yield Static("[enter: preview]  [esc: close]")

    def on_mount(self) -> None:
        self.releases, _src = get_releases(refresh=True)
        if not self.releases:
            self.releases, _src = get_releases(refresh=False)
        ol = self.query_one("#modal-list", OptionList)
        ol.clear_options()
        if not self.releases:
            ol.add_option(Option("[dim](No releases >= v3.0-alpha found)[/dim]"))
            return
        for r in self.releases:
            pre = " [pre-release]" if r.get("prerelease") else ""
            ol.add_option(Option(f"  [b #58a6ff]{r['tag']}[/]  [dim]{r.get('published_at', '')}{pre}[/]  {r.get('name', '')[:50]}"))
        ol.highlighted = 0
        self._preview(0)
        ol.focus()

    def _preview(self, idx: int) -> None:
        if not (0 <= idx < len(self.releases)):
            return
        r = self.releases[idx]
        body = (r.get("body") or "_No description._").strip()
        try:
            from rich.markdown import Markdown
            self.query_one("#releases-body", Static).update(Markdown(f"# {r['tag']} - {r.get('name', '')}\n\n*{r.get('published_at', '')}*\n\n{body}"))
        except Exception:
            self.query_one("#releases-body", Static).update(f"{r['tag']}\n\n{body}")

    def action_cursor_up(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.action_cursor_up()
        self._preview(ol.highlighted or 0)

    def action_cursor_down(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.action_cursor_down()
        self._preview(ol.highlighted or 0)

    def action_open_window(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is not None:
            self._preview(idx)

    def action_close(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_open_window()


class UpdateNoticeModal(ModalScreen[None]):
    """Simplest centered header shown once after update. Content always comes
    from the newest GitHub release (>= v3.0-alpha). No search, no selection."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close", show=False),
    ]

    def __init__(self, version: str = "", notes: str = "", **kwargs):
        super().__init__(**kwargs)
        self.version = version
        self.notes = notes

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Changelog", id="modal-title")
                yield Static("[esc]", id="modal-esc")
            with VerticalScroll(id="releases-preview"):
                yield Static("Loading latest release...", id="update-notice-body")
            with Horizontal(id="modal-footer"):
                yield Static("[esc: close]")
                yield Static("", id="modal-footer-right")

    def on_mount(self) -> None:
        try:
            from ...core.releases import get_releases
            rels, _src = get_releases(refresh=True)
            if not rels:
                rels, _src = get_releases(refresh=False)
            if rels:
                latest = rels[-1]
                self.version = latest.get("tag", self.version)
                body = (latest.get("body") or "_No description._").strip()
                self.notes = f"{latest.get('name', '')}\n\n{body}"
        except Exception:
            pass
        try:
            from rich.markdown import Markdown
            self.query_one("#update-notice-body", Static).update(
                Markdown(f"# Updated to {self.version}\n\n{self.notes}"))
        except Exception:
            self.query_one("#update-notice-body", Static).update(
                f"Updated to {self.version}\n\n{self.notes}")

    def action_close(self) -> None:
        self.dismiss(None)
