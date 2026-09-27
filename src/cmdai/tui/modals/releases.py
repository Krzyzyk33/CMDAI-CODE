from typing import Any, Dict, List, Optional
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core.releases import get_releases


class ReleaseNoteModal(ModalScreen[None]):
    """Full changelog of a single release, stacked on top of the list."""

    BINDINGS = [
        Binding("escape", "close", "Back", priority=True),
        Binding("enter", "close", "Back", show=False),
    ]

    def __init__(self, release: Dict[str, Any], **kwargs):
        super().__init__(**kwargs)
        self.release = release

    def compose(self) -> ComposeResult:
        tag = self.release.get("tag", "")
        name = self.release.get("name", "")
        date = self.release.get("published_at", "")
        pre = " [pre-release]" if self.release.get("prerelease") else ""
        body = (self.release.get("body") or "_No description._").strip()
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"[bold white]{tag}[/]", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            yield Static(f"[dim]{date}{pre} · {name}[/]", id="releases-hint")
            with VerticalScroll(id="releases-preview"):
                try:
                    from rich.markdown import Markdown
                    yield Static(Markdown(body), id="releases-body")
                except Exception:
                    yield Static(body, id="releases-body")

    def action_close(self) -> None:
        self.dismiss(None)


class ReleasesModal(ModalScreen[None]):
    """Changelog - every release listed newest first, click opens a stacked window."""

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
        with Vertical(id="modal-dialog", classes="releases-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Changelog", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            yield OptionList(id="modal-list")
            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Open[/] enter / click   [dim]opens a window with the full changelog[/dim]", id="modal-footer-left")
                yield Static("", id="modal-footer-right")

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
            ol.add_option(Option(
                f"  [b #58a6ff]{r['tag']}[/]  [dim]{r.get('published_at', '')}{pre}[/]  {r.get('name', '')[:50]}"
            ))
        ol.highlighted = 0
        ol.focus()

    def action_cursor_up(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_down()

    def action_open_window(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is not None and 0 <= idx < len(self.releases):
            self.app.push_screen(ReleaseNoteModal(self.releases[idx]))

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
                yield Static("[dim]esc[/]", id="modal-esc")
            with VerticalScroll(id="releases-preview"):
                yield Static("Loading latest release...", id="update-notice-body")

    def on_mount(self) -> None:
        try:
            from ...core.releases import get_releases
            rels, _src = get_releases(refresh=True)
            if not rels:
                rels, _src = get_releases(refresh=False)
            if rels:
                latest = rels[0]
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
