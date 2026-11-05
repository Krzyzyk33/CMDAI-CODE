from typing import Any, Dict, List, Optional
from rich.text import Text
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

    SPINNER_FRAMES = ["⌬", "✻"]
    
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
        self._spin_idx = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="sessions-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Saved Conversations", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")

            yield SessionSearchInput(placeholder="Type to filter conversations...", id="modal-search")

            yield OptionList(id="modal-list")

            with Horizontal(id="modal-footer"):
                yield Static("enter: Open  ·  delete: Delete  ·  ctrl+del: Clear all  ·  esc: Close")

    def on_mount(self) -> None:
        self.all_sessions = self.session_manager.list_sessions(current_dir=self.workdir)
        self.filtered_sessions = list(self.all_sessions)
        self.refresh_list()
        self.set_interval(0.35, self._tick_spinner)
        inp = self.query_one("#modal-search", SessionSearchInput)
        inp.focus()

    def _tick_spinner(self) -> None:
        self._spin_idx = (self._spin_idx + 1) % len(self.SPINNER_FRAMES)
        self.refresh_list(preserve_highlight=True)

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
                or query in str(s.get("display_date", "")).lower()
                or query in str(s.get("last_written_at", "")).lower()
                or query in str(s.get("relative_time", "")).lower()
                or query in str(s.get("model_id", "")).lower()
                or query in str(s.get("directory", "")).lower()
                or query in str(s.get("dir_name", "")).lower()
            ]
        self.refresh_list()

    def refresh_list(self, preserve_highlight: bool = False) -> None:
        ol = self.query_one("#modal-list", OptionList)
        curr_idx = ol.highlighted if preserve_highlight else 0
        ol.clear_options()

        if not self.filtered_sessions:
            ol.add_option(Option("  [dim]No matching sessions found[/dim]"))
            return

        app = getattr(self, "app", None)
        curr_id = str(getattr(app, "current_session_id", "") or "")
        app_is_generating = bool(getattr(app, "is_generating", False))

        from ...agent.tasks import BackgroundTaskManager
        bg_mgr = BackgroundTaskManager.get_instance()
        all_subs = bg_mgr.list_subagents()
        all_tasks = bg_mgr.list_tasks()

        for s in self.filtered_sessions:
            try:
                sid = str(s.get("id") or "")
                is_active = bool(sid and sid == curr_id)

                has_running_bg = any(
                    sub.get("status") == "running" and str(sub.get("session_id") or "") == sid
                    for sub in all_subs
                ) or any(
                    tsk.get("running") and str(tsk.get("session_id") or "") == sid
                    for tsk in all_tasks
                )

                icon = self.SPINNER_FRAMES[self._spin_idx % len(self.SPINNER_FRAMES)]
                if has_running_bg:
                    # Constantly orange when running in background!
                    badge = f"[b #f0883e]{icon}  [/]"
                elif is_active and app_is_generating:
                    # Foreground generating is blue spinner
                    badge = f"[b #58a6ff]{icon}  [/]"
                elif is_active:
                    # Currently active session (idle): clean blue dot indicator
                    badge = "[b #58a6ff]●  [/]"
                else:
                    badge = "   "

                raw_title = str(s.get("title") or "Untitled").strip()
                if len(raw_title) > 38:
                    title_str = raw_title[:36] + ".."
                else:
                    title_str = raw_title
                title_fmt = f"[bold white]{title_str:<38}[/]"

                disp_date = str(s.get("display_date") or s.get("last_written_at") or "")
                if len(disp_date) > 16:
                    disp_date = disp_date[:16]
                time_fmt = f"[dim]{disp_date:<16}[/]"

                tok_str = str(s.get("tokens_str") or "")
                tok_fmt = f"[#7ee787]{tok_str:<8}[/]" if tok_str else "        "

                dir_name = s.get("dir_name") or ""
                dir_fmt = f"[dim]({dir_name})[/]" if dir_name else ""

                row = f"{badge}{title_fmt} {time_fmt} {tok_fmt} {dir_fmt}".rstrip()
                ol.add_option(Option(row))
            except Exception:
                pass
        if curr_idx is not None and curr_idx < len(self.filtered_sessions):
            ol.highlighted = curr_idx
        else:
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
        idx = getattr(event, "option_index", None)
        if idx is not None and 0 <= idx < len(self.filtered_sessions):
            self.dismiss(self.filtered_sessions[idx]["id"])
        else:
            self.action_load_session()
