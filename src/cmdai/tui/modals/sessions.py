from datetime import datetime, date
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


def format_day_header(dt_str: str) -> str:
    """Format date string YYYY-MM-DD into 'Friday, 11.11.2026'."""
    try:
        d = datetime.strptime(dt_str[:10], "%Y-%m-%d").date()
        today = date.today()
        day_name = d.strftime("%A")
        formatted_date = d.strftime("%d.%m.%Y")
        if d == today:
            return f"{day_name}, {formatted_date} (Today)"
        elif (today - d).days == 1:
            return f"{day_name}, {formatted_date} (Yesterday)"
        else:
            return f"{day_name}, {formatted_date}"
    except Exception:
        return dt_str[:10] if dt_str else "Saved Sessions"


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
        app = getattr(self, "app", None)
        active_gen = getattr(app, "_active_generation_sessions", set())
        from ...agent.tasks import BackgroundTaskManager
        bg_mgr = BackgroundTaskManager.get_instance()
        has_bg = any(bool(s.get("background")) and s.get("status") == "running" for s in bg_mgr.list_subagents()) or any(t.get("running") for t in bg_mgr.list_tasks())
        # If nothing is spinning, do not touch list or scroll position at all
        if not active_gen and not has_bg:
            return
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
        curr_sid = None
        saved_scroll_y = getattr(ol, "scroll_y", 0) if preserve_highlight else 0
        if preserve_highlight and ol.highlighted is not None:
            try:
                opt = ol.get_option_at_index(ol.highlighted)
                curr_sid = getattr(opt, "id", None)
            except Exception:
                curr_sid = None

        ol.clear_options()

        if not self.filtered_sessions:
            ol.add_option(Option("  [dim]No matching sessions found[/dim]", disabled=True))
            return

        app = getattr(self, "app", None)
        curr_id = str(getattr(app, "current_session_id", "") or "")

        from ...agent.tasks import BackgroundTaskManager
        bg_mgr = BackgroundTaskManager.get_instance()
        all_subs = bg_mgr.list_subagents()
        all_tasks = bg_mgr.list_tasks()

        last_day_key = None

        for s in self.filtered_sessions:
            try:
                sid = str(s.get("id") or "")
                is_active = bool(sid and sid == curr_id)

                has_running_bg = any(
                    bool(sub.get("background")) and sub.get("status") == "running" and str(sub.get("session_id") or "") == sid
                    for sub in all_subs
                ) or any(
                    tsk.get("running") and str(tsk.get("session_id") or "") == sid
                    for tsk in all_tasks
                )

                icon = self.SPINNER_FRAMES[self._spin_idx % len(self.SPINNER_FRAMES)]
                if has_running_bg:
                    badge = f"[b #f0883e]{icon}  [/]"
                elif bool(sid and sid in getattr(app, '_active_generation_sessions', set())):
                    badge = f"[b #58a6ff]{icon}  [/]"
                elif is_active:
                    badge = "[b #58a6ff]●  [/]"
                else:
                    badge = "   "

                disp_date = str(s.get("display_date") or s.get("last_written_at") or s.get("created_at") or "").strip()
                day_key = disp_date[:10] if len(disp_date) >= 10 else ""
                time_str = disp_date[11:16] if len(disp_date) >= 16 else (disp_date[-5:] if len(disp_date) >= 5 else "")

                # Insert day header separator when day changes
                if day_key and day_key != last_day_key:
                    if last_day_key is not None:
                        # Extra empty line between day groups as requested
                        ol.add_option(Option(Text(""), disabled=True))
                    header_title = format_day_header(day_key)
                    # Dashes only on the left side
                    sep_markup = f"[bold #58a6ff]  ── {header_title}[/]"
                    t_sep = Text.from_markup(sep_markup)
                    t_sep.no_wrap = True
                    t_sep.overflow = "ellipsis"
                    ol.add_option(Option(t_sep, disabled=True))
                    last_day_key = day_key
                elif not day_key and last_day_key is not None and last_day_key != "other":
                    ol.add_option(Option(Text(""), disabled=True))
                    t_sep = Text.from_markup("[bold #8b949e]  ── Other[/]")
                    t_sep.no_wrap = True
                    t_sep.overflow = "ellipsis"
                    ol.add_option(Option(t_sep, disabled=True))
                    last_day_key = "other"

                tok_str = str(s.get("tokens_str") or "").strip()
                dir_name = str(s.get("dir_name") or "").strip()

                # Right metadata
                parts_plain = []
                parts_markup = []
                if time_str:
                    parts_plain.append(time_str)
                    parts_markup.append(f"[dim]{time_str}[/]")
                if tok_str:
                    parts_plain.append(tok_str)
                    parts_markup.append(f"[#7ee787]{tok_str}[/]")
                if dir_name:
                    if len(dir_name) > 18:
                        d_disp = dir_name[:16] + ".."
                    else:
                        d_disp = dir_name
                    parts_plain.append(f"({d_disp})")
                    parts_markup.append(f"[dim]({d_disp})[/]")

                right_plain = "  ".join(parts_plain)
                right_markup = "  ".join(parts_markup)

                # Total target width is 90 so rows reach the right edge of modal
                badge_plain_len = 3
                max_title_len = max(20, 90 - badge_plain_len - len(right_plain) - 2)
                raw_title = str(s.get("title") or "Untitled").strip().replace("\n", " ")
                if len(raw_title) > max_title_len:
                    title_str = raw_title[:max_title_len - 2] + ".."
                else:
                    title_str = raw_title

                pad_len = max(2, 90 - badge_plain_len - len(title_str) - len(right_plain))
                spaces = " " * pad_len
                row = f"{badge}[bold white]{title_str}[/]{spaces}{right_markup}"
                t_row = Text.from_markup(row)
                t_row.no_wrap = True
                t_row.overflow = "ellipsis"
                ol.add_option(Option(t_row, id=sid))
            except Exception:
                pass

        if curr_sid:
            try:
                ol.highlighted = ol.get_option_index(curr_sid)
            except Exception:
                pass

        if ol.highlighted is None and ol.option_count > 0:
            for i in range(ol.option_count):
                opt = ol.get_option_at_index(i)
                if not getattr(opt, "disabled", False):
                    ol.highlighted = i
                    break

        if preserve_highlight and saved_scroll_y > 0:
            try:
                ol.scroll_to(y=saved_scroll_y, animate=False, immediate=True)
            except Exception:
                pass

    def action_focus_list(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.focus()
        if ol.option_count > 0 and ol.highlighted is None:
            for i in range(ol.option_count):
                opt = ol.get_option_at_index(i)
                if not getattr(opt, "disabled", False):
                    ol.highlighted = i
                    break

    def action_cancel(self) -> None:
        self.dismiss("")

    def action_load_session(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is not None and 0 <= idx < ol.option_count:
            try:
                opt = ol.get_option_at_index(idx)
                if opt and opt.id and not getattr(opt, "disabled", False):
                    self.dismiss(str(opt.id))
                    return
            except Exception:
                pass
        # Fallback to first non-disabled session
        if self.filtered_sessions:
            self.dismiss(str(self.filtered_sessions[0]["id"]))

    def action_delete_session(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is not None and 0 <= idx < ol.option_count:
            try:
                opt = ol.get_option_at_index(idx)
                if opt and opt.id and not getattr(opt, "disabled", False):
                    s_id = str(opt.id)
                    self.session_manager.delete_session(s_id)
                    self.all_sessions = self.session_manager.list_sessions(current_dir=self.workdir)
                    inp = self.query_one("#modal-search", SessionSearchInput)
                    self.on_search_changed(Input.Changed(inp, inp.value))
            except Exception:
                pass

    def action_clear_all(self) -> None:
        self.session_manager.clear_all_sessions()
        self.all_sessions = []
        self.filtered_sessions = []
        self.refresh_list()

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt = getattr(event, "option", None)
        opt_id = getattr(opt, "id", None) or getattr(event, "option_id", None)
        if opt_id and not getattr(opt, "disabled", False):
            self.dismiss(str(opt_id))
        else:
            self.action_load_session()
