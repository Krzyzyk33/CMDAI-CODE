"""Background Tasks & Subagents Modal for CMDAI CODE.

Displays running background terminal processes and subagents,
enabling log inspection, interactive subagent chat viewing, and task termination.
Styled with 100% visual parity to SessionsModal (wide dialog, clean columns, live search).
"""

from typing import Any, Dict, List, Optional
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ...agent.tasks import BackgroundTaskManager


class TasksSearchInput(Input):
    """Search input that forwards down arrow and enter to BackgroundTasksModal."""

    def on_key(self, event: events.Key) -> None:
        modal = self.screen
        if not isinstance(modal, BackgroundTasksModal):
            return
        if event.key == "down":
            event.prevent_default()
            event.stop()
            modal.action_focus_list()
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            modal.action_view_logs()


class TaskLogsModal(ModalScreen[None]):
    """Modal viewer for background task logs."""

    BINDINGS = [
        Binding("escape", "close", "Back", priority=True),
        Binding("enter", "close", "Back", show=False),
    ]

    def __init__(self, title: str, logs: str, **kwargs):
        super().__init__(**kwargs)
        self.task_title = title
        self.logs = logs or "(No log output recorded yet)"

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="sessions-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"[bold white]{self.task_title}[/]", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            with VerticalScroll(id="releases-preview"):
                t = Text()
                t.append(self.logs, style="#c9d1d9")
                yield Static(t, id="releases-body")
            with Horizontal(id="modal-footer"):
                yield Static("esc: Close", id="modal-footer-left")

    def action_close(self) -> None:
        self.dismiss(None)


class BackgroundTasksModal(ModalScreen[None]):
    """Modal displaying running background tasks and subagents matching sessions-modal style."""

    SPINNER_FRAMES = ["⌬", "✻"]
    SPINNER_COLORS = ["#f0883e"]

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", priority=True),
        Binding("enter", "view_logs", "Open"),
        Binding("space", "view_logs", "Open", show=False),
        Binding("l", "view_logs", "Open", show=False),
        Binding("k", "kill_selected", "Kill"),
        Binding("x", "kill_selected", "Kill", show=False),
        Binding("delete", "kill_selected", "Kill", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mgr = BackgroundTaskManager.get_instance()
        self._all_items: List[Dict[str, Any]] = []
        self._filtered_items: List[Dict[str, Any]] = []
        self.search_query: str = ""
        self._spin_idx: int = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="sessions-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Background Tasks & Subagents", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            yield TasksSearchInput(placeholder="Type to filter tasks & subagents...", id="modal-search")
            yield OptionList(id="modal-list")
            with Horizontal(id="modal-footer"):
                yield Static("enter: View / Chat  ·  k: Kill  ·  esc: Close")

    def on_mount(self) -> None:
        self.refresh_list()
        self.set_interval(0.35, self._tick_spinner)
        inp = self.query_one("#modal-search", TasksSearchInput)
        inp.focus()

    def _tick_spinner(self) -> None:
        self._spin_idx = (self._spin_idx + 1) % len(self.SPINNER_FRAMES)
        self.refresh_list(preserve_highlight=True)

    @on(Input.Changed, "#modal-search")
    def on_search_changed(self, event: Input.Changed) -> None:
        self.search_query = event.value.strip().lower()
        self.refresh_list(preserve_highlight=False)

    def refresh_list(self, preserve_highlight: bool = False) -> None:
        ol = self.query_one("#modal-list", OptionList)
        prev_idx = ol.highlighted if preserve_highlight else 0
        saved_scroll_y = getattr(ol, "scroll_y", 0) if preserve_highlight else 0

        # Collect active or background items
        self._all_items = []
        for t in self.mgr.list_tasks():
            self._all_items.append({"type": "process", "data": t})
        for s in self.mgr.list_subagents():
            if s.get("status") == "running" or s.get("background"):
                self._all_items.append({"type": "subagent", "data": s})

        # Apply search filter
        q = self.search_query
        if not q:
            self._filtered_items = list(self._all_items)
        else:
            filtered = []
            for it in self._all_items:
                d = it["data"]
                if it["type"] == "process":
                    haystack = f"{d.get('cmd', '')} {d.get('task_id', '')} {d.get('status', '')}".lower()
                else:
                    haystack = f"{d.get('role', '')} {d.get('task', '')} {d.get('subagent_id', '')} {d.get('status', '')}".lower()
                if q in haystack:
                    filtered.append(it)
            self._filtered_items = filtered

        ol.clear_options()

        if not self._filtered_items:
            ol.add_option(Option("  [dim]No background tasks or subagents running[/dim]"))
            return

        for idx, item in enumerate(self._filtered_items):
            icon = self.SPINNER_FRAMES[self._spin_idx % len(self.SPINNER_FRAMES)]
            color = "#f0883e"
            if item["type"] == "process":
                t = item["data"]
                elapsed = f"{t.get('elapsed', 0)}s"
                cmd_snip = str(t.get("cmd", "") or "").strip().replace("\n", " ")
                if len(cmd_snip) > 18:
                    cmd_snip = cmd_snip[:16] + ".."
                title_fmt = f"[bold white]Task: {cmd_snip:<18}[/]"
                time_fmt = f"[dim]{elapsed:<7}[/]"
                is_p_run = bool(t.get("running", False))
                if is_p_run:
                    badge = f"[b #f0883e]{icon}  [/]"
                    status_fmt = f"[#8b949e]running ({t.get('task_id', '')})[/]"
                else:
                    badge = "[b #7ee787]✓  [/]"
                    status_fmt = f"[#7ee787]completed ({t.get('task_id', '')})[/]"
                row = f"{badge}{title_fmt} {time_fmt} {status_fmt}".rstrip()
                t_row = Text.from_markup(row)
                t_row.no_wrap = True
                t_row.truncate(76, overflow="ellipsis")
                ol.add_option(Option(t_row))
            else:
                s = item["data"]
                elapsed = f"{s.get('elapsed', 0)}s"
                role_snip = str(s.get("role", "") or "Subagent").strip().replace("\n", " ")
                if len(role_snip) > 16:
                    role_snip = role_snip[:14] + ".."
                title_fmt = f"[bold white]Subagent: {role_snip:<16}[/]"
                time_fmt = f"[dim]{elapsed:<7}[/]"

                task_snip = str(s.get("task", "") or "").strip().replace("\n", " ")
                cur_t = s.get("current_tool")
                cur_tgt = str(s.get("tool_target") or "").strip().replace("\n", " ")
                st = str(s.get("status", "running")).lower()

                if st == "running":
                    badge = f"[b #f0883e]{icon}  [/]"
                    if cur_t:
                        tool_desc = f"{cur_t} {cur_tgt}".strip()
                        if len(tool_desc) > 20:
                            tool_desc = tool_desc[:18] + ".."
                        status_fmt = f"[#58a6ff]{tool_desc}[/]"
                    else:
                        if len(task_snip) > 20:
                            task_snip = task_snip[:18] + ".."
                        status_fmt = f"[#8b949e]{task_snip}[/]"
                elif st in ("finished", "completed"):
                    badge = "[b #7ee787]✓  [/]"
                    status_fmt = "[#7ee787]completed[/]"
                else:
                    badge = "[b #f85149]✗  [/]"
                    status_fmt = f"[#f85149]{st}[/]"

                row = f"{badge}{title_fmt} {time_fmt} {status_fmt}".rstrip()
                t_row = Text.from_markup(row)
                t_row.no_wrap = True
                t_row.truncate(76, overflow="ellipsis")
                ol.add_option(Option(t_row))

        if prev_idx is not None and 0 <= prev_idx < len(self._filtered_items):
            ol.highlighted = prev_idx
        elif len(self._filtered_items) > 0:
            ol.highlighted = 0

        if preserve_highlight and saved_scroll_y > 0:
            try:
                ol.scroll_to(y=saved_scroll_y, animate=False, immediate=True)
            except Exception:
                pass

    def _selected_item(self) -> Optional[Dict[str, Any]]:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is not None and 0 <= idx < len(self._filtered_items):
            return self._filtered_items[idx]
        return None

    def action_focus_list(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.focus()
        if ol.option_count > 0 and ol.highlighted is None:
            ol.highlighted = 0

    def action_cursor_up(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_down()

    def action_view_logs(self, item_override: Optional[Dict[str, Any]] = None) -> None:
        item = item_override or self._selected_item()
        if not item:
            return
        if item["type"] == "process":
            tid = item["data"]["task_id"]
            t = self.mgr.get_task(tid)
            logs = t.read_logs(max_lines=200) if t else "(Task not found)"
            title = f"Task Logs: {tid} ({item['data']['cmd']})"
            self.app.push_screen(TaskLogsModal(title, logs))
        else:
            sid = str(item["data"].get("subagent_id", "") or "")
            role_name = str(item["data"].get("role", "") or "")
            target_block = None
            try:
                from ..widgets import ToolBlock
                for b in self.app.query(ToolBlock):
                    b_sid = str(getattr(b, "subagent_id", "") or "")
                    b_role = str(getattr(b, "subagent_role", "") or getattr(b, "target", "") or "")
                    if (sid and b_sid == sid) or (role_name and b_role.lower() == role_name.lower()):
                        target_block = b
                        break
            except Exception:
                pass

            if not target_block:
                try:
                    card = getattr(self.app, "_current_assistant_card", None)
                    if card:
                        for b in getattr(card, "tool_blocks", []):
                            if (getattr(b, "subagent_id", "") and getattr(b, "subagent_id", "") == sid) or (
                                getattr(b, "subagent_role", "") and getattr(b, "subagent_role", "") == role_name
                            ):
                                target_block = b
                                break
                except Exception:
                    pass

            if target_block and hasattr(self.app, "_open_subagent_chat"):
                self.dismiss(None)
                self.app.call_after_refresh(self.app._open_subagent_chat, target_block)
            else:
                try:
                    from .subagent_chat import SubagentChatModal
                    s = self.mgr.get_subagent(sid) or item["data"]
                    res_val = s.get("result") or "(Subagent in progress or no result produced)"
                    self.dismiss(None)
                    self.app.push_screen(SubagentChatModal(
                        role=role_name or "Subagent",
                        goal=s.get("task") or "",
                        system_prompt="",
                        history=[],
                        findings=[],
                        agents=[{
                            "role": role_name or "Subagent",
                            "goal": s.get("task") or "",
                            "system_prompt": "",
                            "summary": str(res_val),
                            "history": [],
                            "findings": [],
                            "notes": [],
                            "events": [{"type": "text", "content": str(res_val)}],
                            "status": s.get("status") or "completed",
                            "elapsed": float(s.get("elapsed", 0) or 0),
                            "tokens": 0,
                            "error": "",
                        }],
                    ))
                except Exception:
                    s = self.mgr.get_subagent(sid) or item["data"]
                    result = s.get("result") or "(Subagent in progress or no result produced)"
                    title = f"Subagent: {role_name}"
                    self.app.push_screen(TaskLogsModal(title, str(result)))

    def action_kill_selected(self) -> None:
        item = self._selected_item()
        if not item:
            return
        if item["type"] == "process":
            tid = item["data"]["task_id"]
            self.mgr.kill_task(tid)
            self.refresh_list(preserve_highlight=True)
        else:
            sid = item["data"]["subagent_id"]
            self.mgr.cancel_subagent(sid)
            try:
                cs = self.app.query_one("#chat-scroll")
                for b in cs.query("ToolBlock"):
                    if getattr(b, "subagent_id", "") == sid:
                        b.tool_running = False
                        b._stop_spinner()
                        b.subagent_status = "cancelled"
                        b.subagent_live_tool = "Cancelled"
                        b.update_header()
            except Exception:
                pass
            self.refresh_list(preserve_highlight=True)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#modal-list")
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_index is not None and 0 <= event.option_index < len(self._filtered_items):
            self.action_view_logs(item_override=self._filtered_items[event.option_index])
        else:
            self.action_view_logs()
