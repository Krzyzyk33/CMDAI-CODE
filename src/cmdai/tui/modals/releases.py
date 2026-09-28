from typing import Any, Dict, List, Optional
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core.releases import get_release_list, get_releases, offline_notice_text


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
        body = (self.release.get("body") or "").strip()
        if not body:
            # The entry is listed but its notes were never fetched, so there is
            # nothing to render. Say why instead of showing a blank page.
            body = offline_notice_text(str(tag))
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
        self.offline = False

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
        rels, source = get_releases(refresh=True)
        if not rels:
            # No network. The list still opens: the bundled release is on disk
            # in the source, and showing one usable entry beats showing an error.
            rels, source = get_releases(refresh=False)
        self.offline = source in ("none", "cache") and not rels
        self.releases = get_release_list(rels)

        ol = self.query_one("#modal-list", OptionList)
        ol.clear_options()
        if not self.releases:
            ol.add_option(Option("[dim](No releases >= v3.0-alpha found)[/dim]"))
            return
        for r in self.releases:
            pre = " [pre-release]" if r.get("prerelease") else ""
            mark = "" if r.get("has_notes") else " [dim](notes offline)[/dim]"
            ol.add_option(Option(
                f"  [b #58a6ff]{r['tag']}[/]  [dim]{r.get('published_at', '')}{pre}[/]  {r.get('name', '')[:50]}{mark}"
            ))
        ol.highlighted = 0
        ol.focus()
        if self.offline:
            self.query_one("#modal-footer-right", Static).update(
                "[dim]no internet - only the bundled release is shown[/dim]")

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
    """Post-update notice: what the newest release changed.

    Shown once, on the first launch after the version moved. The release is
    handed in already resolved rather than fetched here - this screen is built
    while the app is still coming up, and a network call on that path stalls
    startup for the length of the timeout and shows nothing at all when the
    machine is offline, which is common right after an update.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close", show=False),
    ]

    def __init__(self, version: str = "", release: Optional[Dict[str, Any]] = None, **kwargs):
        super().__init__(**kwargs)
        self.version = version
        self.release = dict(release or {})

    def compose(self) -> ComposeResult:
        # The headline is the version actually installed, not the version the
        # bundled notes describe. Those can differ whenever the checkout is
        # ahead of the last regenerated release, and a notice that claimed the
        # wrong version was the worse of the two.
        notes_tag = str(self.release.get("tag") or "")
        headline = self.version or notes_tag
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"[bold white]Updated to {headline}[/]", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            with VerticalScroll(id="releases-preview"):
                yield Static("", id="update-notice-hint")
                yield Static("", id="update-notice-body")

    def on_mount(self) -> None:
        notes_tag = str(self.release.get("tag") or "")
        try:
            # Name the release the notes came from, but only when it is not the
            # one in the headline - otherwise the line is just noise.
            if notes_tag and notes_tag != self.version:
                self.query_one("#update-notice-hint", Static).update(
                    f"[dim]Release notes: {notes_tag}[/dim]")
        except Exception:
            pass

        body_widget = self.query_one("#update-notice-body", Static)
        body = str(self.release.get("body") or "").strip()
        if not body:
            body = "_No release notes were bundled with this build._"
        try:
            from rich.markdown import Markdown
            body_widget.update(Markdown(body))
        except Exception:
            body_widget.update(body)

    def action_close(self) -> None:
        self.dismiss(None)
