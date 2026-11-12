from __future__ import annotations

import difflib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from rich.markdown import Markdown
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Static, TextArea

from ...agent.render import (
    render_command_output,
    render_edit_diff_opencode,
    render_edit_diff_side_by_side,
    render_tool_header,
)
from ...core.capabilities import clean_model_name


class SessionTabCloseBtn(Static):
    class Clicked(Message):
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    def __init__(self, session_id: str, **kwargs):
        super().__init__("×", classes="tab-close", **kwargs)
        self.session_id = session_id

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Clicked(self.session_id))


class SessionTabItem(Horizontal):
    class Selected(Message):
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    class Closed(Message):
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    def __init__(self, session_id: str, title: str, is_active: bool = False, **kwargs):
        active_cls = "active" if is_active else "inactive"
        super().__init__(classes=f"session-tab-item {active_cls}", **kwargs)
        self.session_id = session_id
        self.title_text = title or "New session"
        self.is_active = is_active
        self.is_hovered = False
        self._scroll_offset = 0
        self._pause_ticks = 0
        self._scroll_timer = None

    @property
    def visible_width(self) -> int:
        if self.size.width > 4:
            return max(6, self.size.width - 4)
        return 18

    def compose(self) -> ComposeResult:
        w = self.visible_width
        display_title = self.title_text
        if len(display_title) > w:
            display_title = display_title[: max(4, w - 1)] + "…"
        yield Static(display_title, classes="tab-title")
        yield SessionTabCloseBtn(self.session_id)

    def on_mount(self) -> None:
        self._update_static_title()

    def on_resize(self, event: events.Resize) -> None:
        if not self.is_hovered:
            self._update_static_title()

    def on_enter(self, event: events.Enter) -> None:
        self.is_hovered = True
        self.add_class("hovered")
        w = self.visible_width
        if len(self.title_text) > w:
            self._scroll_offset = 0
            self._pause_ticks = 2
            if self._scroll_timer is not None:
                self._scroll_timer.stop()
            self._scroll_timer = self.set_interval(0.12, self._tick_marquee)

    def on_leave(self, event: events.Leave) -> None:
        self.is_hovered = False
        self.remove_class("hovered")
        if self._scroll_timer is not None:
            self._scroll_timer.stop()
            self._scroll_timer = None
        self._scroll_offset = 0
        self._update_static_title()

    def _tick_marquee(self) -> None:
        if not self.is_hovered:
            return
        w = self.visible_width
        max_offset = len(self.title_text) - w
        if max_offset <= 0:
            return
        if self._pause_ticks > 0:
            self._pause_ticks -= 1
            return
        self._scroll_offset += 1
        if self._scroll_offset > max_offset + 3:
            self._scroll_offset = 0
            self._pause_ticks = 3
        offset = min(self._scroll_offset, max_offset)
        slice_text = self.title_text[offset : offset + w]
        try:
            self.query_one(".tab-title", Static).update(slice_text)
        except Exception:
            pass

    def _update_static_title(self) -> None:
        w = self.visible_width
        t = self.title_text or "New session"
        if len(t) > w:
            display = t[: max(4, w - 1)] + "…"
        else:
            display = t
        try:
            self.query_one(".tab-title", Static).update(display)
        except Exception:
            pass

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Selected(self.session_id))

    @on(SessionTabCloseBtn.Clicked)
    def on_close_clicked(self, msg: SessionTabCloseBtn.Clicked) -> None:
        msg.stop()
        self.post_message(self.Closed(self.session_id))


class SessionNewTabBtn(Static):
    class Requested(Message):
        pass

    def __init__(self, **kwargs):
        super().__init__("+", classes="session-new-tab-btn", **kwargs)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Requested())


class SessionTabsBar(Horizontal):
    DEFAULT_CSS = """
    SessionTabsBar {
        dock: top;
        height: 1;
        width: 100%;
        background: #000000;
        padding: 0;
        margin: 0;
        overflow-x: auto;
        scrollbar-size-horizontal: 0;
    }
    SessionTabsBar.hidden {
        display: none !important;
    }
    .session-tab-item {
        width: 1fr;
        max-width: 32;
        min-width: 10;
        height: 1;
        background: #000000;
        color: #8b949e;
        margin: 0;
        padding: 0 1;
        border: none;
    }
    .session-tab-item.active {
        background: #161b22;
        color: #ffffff;
        text-style: bold;
        border: none;
    }
    .session-tab-item:hover,
    .session-tab-item.hovered {
        color: #c9d1d9;
    }
    .session-tab-item.active:hover,
    .session-tab-item.active.hovered {
        background: #161b22;
        color: #ffffff;
    }
    .session-tab-item .tab-title {
        width: 1fr;
        height: 1;
        padding: 0;
    }
    .session-tab-item .tab-close {
        width: 1;
        height: 1;
        margin: 0 0 0 1;
        color: #6e7681;
        display: none;
    }
    .session-tab-item.active .tab-close,
    .session-tab-item:hover .tab-close,
    .session-tab-item.hovered .tab-close {
        display: block;
    }
    .session-tab-item .tab-close:hover {
        color: #f0f6fc;
        text-style: bold;
    }
    .session-new-tab-btn {
        width: 3;
        height: 1;
        background: #000000;
        color: #8b949e;
        padding: 0;
        margin: 0;
        text-align: center;
        content-align: center middle;
    }
    .session-new-tab-btn:hover {
        background: #161b22;
        color: #f0f6fc;
    }
    """

    class TabSelected(Message):
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    class TabClosed(Message):
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    class NewTabRequested(Message):
        pass

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sessions: List[Dict[str, Any]] = []
        self._active_id: str = ""

    def on_mount(self) -> None:
        self.rebuild_tabs()

    def update_tabs(self, sessions: List[Dict[str, Any]], active_id: str) -> None:
        self._sessions = list(sessions)
        self._active_id = str(active_id)
        if len(self._sessions) <= 1:
            self.add_class("hidden")
            self.display = False
        else:
            self.remove_class("hidden")
            self.display = True
            if self.is_mounted:
                self.rebuild_tabs()

    def rebuild_tabs(self) -> None:
        self.remove_children()
        if len(self._sessions) <= 1:
            self.add_class("hidden")
            self.display = False
            return
        self.remove_class("hidden")
        self.display = True
        for s in self._sessions:
            sid = str(s.get("session_id", ""))
            title = str(s.get("title", "") or "New session")
            is_active = (sid == self._active_id)
            self.mount(SessionTabItem(session_id=sid, title=title, is_active=is_active))
        self.mount(SessionNewTabBtn())

    @on(SessionTabItem.Selected)
    def _on_tab_selected(self, msg: SessionTabItem.Selected) -> None:
        msg.stop()
        self.post_message(self.TabSelected(msg.session_id))

    @on(SessionTabItem.Closed)
    def _on_tab_closed(self, msg: SessionTabItem.Closed) -> None:
        msg.stop()
        self.post_message(self.TabClosed(msg.session_id))

    @on(SessionNewTabBtn.Requested)
    def _on_new_requested(self, msg: SessionNewTabBtn.Requested) -> None:
        msg.stop()
        self.post_message(self.NewTabRequested())



class TimelineArrow(Static):
    class Clicked(Message):
        def __init__(self, direction: str):
            super().__init__()
            self.direction = direction

    def __init__(self, symbol: str, direction: str, **kwargs):
        super().__init__(symbol, classes=f"timeline-arrow timeline-arrow-{direction}", **kwargs)
        self.direction = direction

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Clicked(self.direction))


class TimelineDot(Static):
    class Selected(Message):
        def __init__(self, turn_idx: int, turn_id: int):
            super().__init__()
            self.turn_idx = turn_idx
            self.turn_id = turn_id

    def __init__(
        self,
        turn_idx: int,
        turn_id: int,
        prompt_text: str,
        index: int,
        total: int,
        time_str: str = "",
        is_active: bool = False,
        **kwargs
    ):
        active_cls = "active" if is_active else "inactive"
        super().__init__("●", classes=f"timeline-dot {active_cls}", **kwargs)
        self.turn_idx = turn_idx
        self.turn_id = turn_id
        self.prompt_text = prompt_text
        self.index = index
        self.total = total
        self.time_str = time_str
        self.is_active = is_active
        self._build_tooltip()

    def _build_tooltip(self) -> None:
        from rich.text import Text
        from rich.markup import escape
        
        clean_prompt = (self.prompt_text or "").strip().replace("\r", "")
        lines = [l for l in clean_prompt.split("\n") if l.strip()]
        if lines:
            first_line = lines[0][:40] + ("…" if len(lines[0]) > 40 or len(lines) > 1 else "")
        else:
            first_line = "User prompt"
            
        safe_prompt = escape(first_line)
        counter_text = f"{self.index} of {self.total}"
        if self.time_str:
            content = f"[bold #e6edf3]{safe_prompt}[/]\n\n[dim #8b949e]{counter_text}[/]       [dim #8b949e]{self.time_str}[/]"
        else:
            content = f"[bold #e6edf3]{safe_prompt}[/]\n\n[dim #8b949e]{counter_text}[/]"
        self.tooltip = Text.from_markup(content)

    def set_active(self, is_active: bool) -> None:
        self.is_active = is_active
        if is_active:
            self.add_class("active")
            self.remove_class("inactive")
        else:
            self.remove_class("active")
            self.add_class("inactive")

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Selected(self.turn_idx, self.turn_id))


class TimelineRail(Vertical):
    class TurnSelected(Message):
        def __init__(self, turn_idx: int, turn_id: int):
            super().__init__()
            self.turn_idx = turn_idx
            self.turn_id = turn_id

    class ScrollRequested(Message):
        def __init__(self, direction: str):
            super().__init__()
            self.direction = direction

    def __init__(self, **kwargs):
        super().__init__(classes="hidden", **kwargs)
        self._turns: List[Dict[str, Any]] = []
        self._active_turn_idx: Optional[int] = None

    def compose(self) -> ComposeResult:
        yield TimelineArrow("▲", direction="up", id="timeline-arrow-up")
        yield VerticalScroll(id="timeline-dots-container", classes="timeline-dots-scroll")
        yield TimelineArrow("▼", direction="down", id="timeline-arrow-down")

    def update_turns(self, turns: List[Dict[str, Any]], active_turn_idx: Optional[int] = None) -> None:
        self._turns = list(turns)
        total = len(self._turns)
        if active_turn_idx is not None and 0 <= active_turn_idx < total:
            self._active_turn_idx = active_turn_idx
        elif total > 0:
            self._active_turn_idx = total - 1
        else:
            self._active_turn_idx = None

        if total <= 1:
            self.add_class("hidden")
            self.display = False
        else:
            self.remove_class("hidden")
            self.display = True
            if self.is_mounted:
                self.rebuild_dots()

    def set_active_turn_idx(self, turn_idx: int) -> None:
        self._active_turn_idx = turn_idx
        try:
            container = self.query_one("#timeline-dots-container", VerticalScroll)
            for dot in container.children:
                if isinstance(dot, TimelineDot) and not getattr(dot, "_pruning", False):
                    dot.set_active(dot.turn_idx == turn_idx)
        except Exception:
            pass

    def rebuild_dots(self) -> None:
        try:
            container = self.query_one("#timeline-dots-container", VerticalScroll)
        except Exception:
            return

        total = len(self._turns)
        active_idx = self._active_turn_idx if self._active_turn_idx is not None else (total - 1 if total > 0 else None)

        dots = [d for d in container.children if isinstance(d, TimelineDot) and not getattr(d, "_pruning", False)]

        while len(dots) > total:
            excess = dots.pop()
            excess.remove()

        for idx in range(min(len(dots), total)):
            dot = dots[idx]
            t = self._turns[idx]
            dot.turn_idx = idx
            dot.turn_id = int(t.get("turn_id", idx + 1))
            dot.prompt_text = str(t.get("text", "") or t.get("prompt", "") or "")
            dot.time_str = str(t.get("time", "") or "")
            dot.index = idx + 1
            dot.total = total
            dot.set_active(idx == active_idx)
            dot._build_tooltip()

        new_dots = []
        for idx in range(len(dots), total):
            t = self._turns[idx]
            tid = int(t.get("turn_id", idx + 1))
            prompt = str(t.get("text", "") or t.get("prompt", "") or "")
            t_str = str(t.get("time", "") or "")
            is_active = (idx == active_idx)
            new_dots.append(TimelineDot(
                turn_idx=idx,
                turn_id=tid,
                prompt_text=prompt,
                index=idx + 1,
                total=total,
                time_str=t_str,
                is_active=is_active
            ))
        if new_dots:
            container.mount(*new_dots)

    @on(TimelineDot.Selected)
    def _on_dot_selected(self, msg: TimelineDot.Selected) -> None:
        msg.stop()
        self.set_active_turn_idx(msg.turn_idx)
        self.post_message(self.TurnSelected(msg.turn_idx, msg.turn_id))

    @on(TimelineArrow.Clicked)
    def _on_arrow_clicked(self, msg: TimelineArrow.Clicked) -> None:
        msg.stop()
        self.post_message(self.ScrollRequested(msg.direction))
