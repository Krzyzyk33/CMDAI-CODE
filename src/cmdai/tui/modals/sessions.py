from typing import Any, Dict, List, Optional
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ...core.capabilities import clean_model_name
from ...core.sessions import SessionManager


class SessionSearchInput(Input):
    """Search input that forwards down arrow and enter to SessionsModal."""

    def on_key(self, event: events.Key) -> None:
        modal = self.screen
        if not isinstance(modal, SessionsModal):
            return
        if event.key == "down":
            event.prevent_default()
            event.stop()
            modal.action_focus_list()
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            modal.action_load_session()


class SessionsModal(ModalScreen[str]):
    """Modal for browsing, reopening, and deleting saved sessions with live search."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "load_session", "Open"),
        Binding("delete", "delete_session", "Delete"),
        Binding("ctrl+delete", "clear_all", "Clear all"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, workdir: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.workdir = workdir
        self.session_manager = SessionManager(project_dir=workdir)
        self.all_sessions: List[Dict[str, Any]] = []
        self.filtered_sessions: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="sessions-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Saved Conversations", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")

            yield SessionSearchInput(placeholder="Type to filter conversations... [Enter to open]", id="modal-search")

            yield OptionList(id="modal-list")

            with Horizontal(id="modal-footer"):
                yield Static("[dim]● current workspace[/dim]   [enter: Open]   [delete: Delete]   [ctrl+del: Clear all]")

    def on_mount(self) -> None:
        self.all_sessions = self.session_manager.list_sessions(current_dir=self.workdir)
        self.filtered_sessions = list(self.all_sessions)
        self.refresh_list()
        inp = self.query_one("#modal-search", SessionSearchInput)
        inp.focus()

    @on(Input.Changed, "#modal-search")
    def on_search_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        if not query:
            self.filtered_sessions = list(self.all_sessions)
        else:
            self.filtered_sessions = [
                s for s in self.all_sessions
                if query in str(s.get("title", "")).lower()
                or query in str(s.get("id", "")).lower()
                or query in str(s.get("created_at", "")).lower()
                or query in str(s.get("relative_time", "")).lower()
                or query in str(s.get("model_id", "")).lower()
                or query in str(s.get("directory", "")).lower()
                or query in str(s.get("dir_name", "")).lower()
            ]
        self.refresh_list()

    def refresh_list(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.clear_options()

        if not self.filtered_sessions:
            ol.add_option(Option("  [dim]No matching sessions found[/dim]"))
            return

        for s in self.filtered_sessions:
            is_curr = s.get("is_current_dir", False)
            badge = "[b #58a6ff]●[/] " if is_curr else "  "

            raw_title = str(s.get("title") or "Untitled").strip()
            if len(raw_title) > 28:
                title_str = raw_title[:26] + ".."
            else:
                title_str = raw_title
            title_fmt = f"[bold white]{title_str:<28}[/]"

            rel_time = str(s.get("relative_time") or "")
            time_fmt = f"[dim]{rel_time:<8}[/]"

            tok_str = str(s.get("tokens_str") or "")
            tok_fmt = f"[#7ee787]{tok_str:<8}[/]" if tok_str else "        "

            raw_model = s.get("model_id") or ""
            model_short = clean_model_name(raw_model) if raw_model else ""
            if len(model_short) > 11:
                model_short = model_short[:9] + ".."
            model_fmt = f"[dim]{model_short:<11}[/]" if model_short else "           "

            dir_name = s.get("dir_name") or ""
            if not is_curr and dir_name:
                if len(dir_name) > 12:
                    dir_name = dir_name[:10] + ".."
                dir_fmt = f"[dim]({dir_name})[/]"
            else:
                dir_fmt = ""

            row = f"{badge}{title_fmt} {time_fmt} {tok_fmt} {model_fmt} {dir_fmt}".rstrip()
            ol.add_option(Option(row))
        ol.highlighted = 0

    def action_focus_list(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.focus()
        if ol.option_count > 0 and ol.highlighted is None:
            ol.highlighted = 0

    def action_cancel(self) -> None:
        self.dismiss("")

    def action_load_session(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is not None and idx < len(self.filtered_sessions):
            self.dismiss(self.filtered_sessions[idx]["id"])

    def action_delete_session(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is not None and idx < len(self.filtered_sessions):
            s_id = self.filtered_sessions[idx]["id"]
            self.session_manager.delete_session(s_id)
            self.all_sessions = self.session_manager.list_sessions(current_dir=self.workdir)
            inp = self.query_one("#modal-search", SessionSearchInput)
            self.on_search_changed(Input.Changed(inp, inp.value))

    def action_clear_all(self) -> None:
        self.session_manager.clear_all_sessions()
        self.all_sessions = []
        self.filtered_sessions = []
        self.refresh_list()

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_load_session()
