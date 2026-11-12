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

from .helpers import build_diff_text_from_details

class AutoExpandingInput(TextArea):
    """Auto-expanding multi-line input matching CMDAI behavior with Submitted message and hint navigation."""

    DEFAULT_CSS = """
    AutoExpandingInput {
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }
    """

    BINDINGS = [
        Binding("enter", "submit", "Submit", show=False, priority=True),
        Binding("return", "submit", "Submit", show=False, priority=True),
        Binding("ctrl+m", "submit", "Submit", show=False, priority=True),
        Binding("ctrl+j", "submit", "Submit", show=False, priority=True),
        Binding("numpad_enter", "submit", "Submit", show=False, priority=True),
        Binding("shift+enter", "insert_newline", "New line", show=False, priority=True),
        Binding("shift+return", "insert_newline", "New line", show=False, priority=True),
    ]

    @dataclass
    class Submitted(Message):
        text_area: "TextArea"
        value: str

        @property
        def control(self) -> "TextArea":
            return self.text_area

    _SHARED_HISTORY: List[str] = []
    _HISTORY_FILE: str = os.path.join(os.path.expanduser("~"), ".cmdai_prompt_history.json")

    @classmethod
    def _load_history(cls) -> None:
        if not cls._SHARED_HISTORY and os.path.exists(cls._HISTORY_FILE):
            try:
                import json
                with open(cls._HISTORY_FILE, "r", encoding="utf-8") as f:
                    cls._SHARED_HISTORY = json.load(f)[-100:]
            except Exception:
                pass

    @classmethod
    def _save_history(cls) -> None:
        try:
            import json
            with open(cls._HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(cls._SHARED_HISTORY[-100:], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def __init__(self, **kwargs):
        super().__init__(
            soft_wrap=True,
            show_line_numbers=False,
            highlight_cursor_line=False,
            **kwargs,
        )
        self._load_history()
        self._history_idx: int = -1
        self._pasted_chunks: Dict[str, str] = {}

    def _handle_multiline_paste(self, text: str) -> None:
        lines = text.splitlines()
        count = len(lines)
        tag = f"[pasted {count} lines]"
        idx = 2
        while tag in self._pasted_chunks and self._pasted_chunks[tag] != text:
            tag = f"[pasted {count} lines #{idx}]"
            idx += 1
        self._pasted_chunks[tag] = text
        self.insert(tag)
        self._update_auto_height()

    def render_line(self, y: int):
        strip = super().render_line(y)
        from rich.segment import Segment
        from rich.style import Style
        from textual.strip import Strip

        badge_pattern = re.compile(r"(\[pasted \d+ lines(?: #\d+)?\])")
        mention_pattern = re.compile(r"(@[a-zA-Z0-9_\-\./\\]+)")
        badge_style = Style(color="#ffffff", bgcolor="#0969da", bold=True)
        mention_style = Style(color="#58a6ff", bold=True)

        new_segments = []
        modified = False

        for seg in strip._segments:
            text = seg.text or ""
            if not text:
                new_segments.append(seg)
                continue

            if "[pasted " in text:
                parts = badge_pattern.split(text)
                for part in parts:
                    if not part:
                        continue
                    if badge_pattern.match(part):
                        new_segments.append(Segment(part, badge_style))
                        modified = True
                    else:
                        if "@" in part:
                            m_parts = mention_pattern.split(part)
                            for mp in m_parts:
                                if not mp:
                                    continue
                                if mention_pattern.match(mp):
                                    new_segments.append(Segment(mp, mention_style))
                                    modified = True
                                else:
                                    new_segments.append(Segment(mp, seg.style))
                        else:
                            new_segments.append(Segment(part, seg.style))
            elif "@" in text:
                parts = mention_pattern.split(text)
                for part in parts:
                    if not part:
                        continue
                    if mention_pattern.match(part):
                        new_segments.append(Segment(part, mention_style))
                        modified = True
                    else:
                        new_segments.append(Segment(part, seg.style))
            else:
                new_segments.append(seg)

        if modified:
            return Strip(new_segments, strip.cell_length)
        return strip

    def on_paste(self, event: events.Paste) -> None:
        pasted = event.text or ""
        lines = pasted.splitlines()
        if len(lines) >= 4 or (len(lines) >= 3 and len(pasted) > 80):
            event.prevent_default()
            event.stop()
            self._handle_multiline_paste(pasted)

    def action_copy(self) -> None:
        sel = self.selected_text
        if sel:
            copy_text_to_clipboard(sel)
            app = getattr(self, "app", None)
            if app:
                app.notify(f"Copied selection to clipboard ({len(sel)} chars)", title="Clipboard", timeout=2.0)
        elif self.text.strip():
            copy_text_to_clipboard(self.text)
            app = getattr(self, "app", None)
            if app:
                app.notify("Copied input text to clipboard", title="Clipboard", timeout=2.0)

    def action_submit(self) -> None:
        app = getattr(self, "app", None)
        hints_active = (
            app is not None
            and getattr(app, "_hint_input_id", None) == self.id
            and bool(getattr(app, "_hint_matches", None))
            and getattr(app, "_hint_sel", -1) >= 0
            # Slash commands are recognised by the text, @mentions by the kind
            # the dropdown was built for: the prompt around an @path is free
            # text, so it cannot be matched by a prefix.
            and (self.text.lstrip().startswith("/")
                 or getattr(app, "_hint_kind", "command") == "file")
        )
        if hints_active and hasattr(app, "_hint_execute"):
            app._hint_execute()
            return

        text = self.text.strip()
        if text:
            for tag, full_content in list(self._pasted_chunks.items()):
                if tag in text:
                    text = text.replace(tag, full_content)
            self._pasted_chunks.clear()

            if not self._SHARED_HISTORY or self._SHARED_HISTORY[-1] != text:
                self._SHARED_HISTORY.append(text)
                self._save_history()
            self._history_idx = -1
            self._temp_draft = ""

            self.text = ""
            self.cursor_location = (0, 0)
            self._update_auto_height()
            if app and hasattr(app, "handle_submit"):
                app.handle_submit(text)
            else:
                self.post_message(self.Submitted(self, text))

    def action_insert_newline(self) -> None:
        self.insert("\n")
        self._update_auto_height()

    def on_key(self, event: events.Key) -> None:
        app = getattr(self, "app", None)
        hints_active = (
            app is not None
            and getattr(app, "_hint_input_id", None) == self.id
            and getattr(app, "_hint_matches", None)
            and (self.text.lstrip().startswith("/")
                 or getattr(app, "_hint_kind", "command") == "file")
        )

        key_name = (event.key or "").lower()
        is_enter = (
            key_name in ("enter", "return", "ctrl+m", "ctrl+j", "numpad_enter", "kp_enter")
            or getattr(event, "character", None) in ("\r", "\n")
        )
        is_shift = (
            getattr(event, "shift", False)
            or "shift" in key_name
            or key_name in ("shift+enter", "shift+return")
        )

        if key_name == "backspace" and self.selection.is_empty:
            row, col = self.cursor_location
            try:
                line = self.document.get_line(row)
            except Exception:
                line = ""
            if "@" in line:
                import re
                for match in re.finditer(r"@[a-zA-Z0-9_\-\./\\]+", line):
                    m_start, m_end = match.start(), match.end()
                    if (m_start < col <= m_end) or (col == m_end + 1 and m_end < len(line) and line[m_end] == " "):
                        del_end = m_end + 1 if (m_end < len(line) and line[m_end] == " ") else m_end
                        event.prevent_default()
                        event.stop()
                        self.delete((row, m_start), (row, del_end))
                        self.cursor_location = (row, m_start)
                        self._update_auto_height()
                        if app and hasattr(app, "_hint_on_input_changed"):
                            app._hint_on_input_changed(self)
                        return

        if key_name in ("shift+tab", "backtab"):
            event.prevent_default()
            event.stop()
            if app and hasattr(app, "cycle_mode"):
                app.cycle_mode()
            return

        if key_name == "tab" and not hints_active and not self.text.strip():
            event.prevent_default()
            event.stop()
            if app and hasattr(app, "cycle_mode"):
                app.cycle_mode()
            return

        if not hints_active:
            if key_name == "up":
                curr_row, _ = self.cursor_location
                if curr_row == 0 and self._SHARED_HISTORY:
                    event.prevent_default()
                    event.stop()
                    if self._history_idx == -1:
                        self._temp_draft = self.text
                        self._history_idx = len(self._SHARED_HISTORY)
                    if self._history_idx > 0:
                        self._history_idx -= 1
                        hist_text = self._SHARED_HISTORY[self._history_idx]
                        self.text = hist_text
                        lines = hist_text.splitlines()
                        self.cursor_location = (max(0, len(lines) - 1), len(lines[-1]) if lines else 0)
                        self._update_auto_height()
                    return

            elif key_name == "down":
                if self._history_idx != -1:
                    event.prevent_default()
                    event.stop()
                    self._history_idx += 1
                    if self._history_idx >= len(self._SHARED_HISTORY):
                        self._history_idx = -1
                        self.text = self._temp_draft
                        lines = self._temp_draft.splitlines()
                        self.cursor_location = (max(0, len(lines) - 1), len(lines[-1]) if lines else 0)
                    else:
                        hist_text = self._SHARED_HISTORY[self._history_idx]
                        self.text = hist_text
                        lines = hist_text.splitlines()
                        self.cursor_location = (max(0, len(lines) - 1), len(lines[-1]) if lines else 0)
                    self._update_auto_height()
                    return

        if hints_active:
            if key_name == "down":
                event.prevent_default()
                event.stop()
                if hasattr(app, "_hint_move"):
                    app._hint_move(1)
                return
            if key_name == "up":
                event.prevent_default()
                event.stop()
                if hasattr(app, "_hint_move"):
                    app._hint_move(-1)
                return
            if key_name == "tab":
                event.prevent_default()
                event.stop()
                if hasattr(app, "_hint_complete"):
                    app._hint_complete()
                return
            if is_enter and getattr(app, "_hint_sel", -1) >= 0:

                event.prevent_default()
                event.stop()
                if hasattr(app, "_hint_execute"):
                    app._hint_execute()
                return

        if key_name == "ctrl+c":
            event.prevent_default()
            event.stop()
            if app and hasattr(app, "action_handle_interrupt"):
                app.action_handle_interrupt()
            return

        if key_name == "ctrl+v":
            try:
                import pyperclip
                clip = pyperclip.paste() or ""
                lines = clip.splitlines()
                if len(lines) >= 4 or (len(lines) >= 3 and len(clip) > 80):
                    event.prevent_default()
                    event.stop()
                    self._handle_multiline_paste(clip)
                    return
            except Exception:
                pass

        if key_name in ("left", "right") and app is not None:
            hovered_tool = None
            try:
                for tb in app.query(ToolBlock):
                    if getattr(tb, "is_expanded", False) and getattr(tb, "_is_mouse_hovered", False):
                        hovered_tool = tb
                        break
            except Exception:
                hovered_tool = None
            if hovered_tool is not None:
                delta = 6 if key_name == "right" else -6
                hovered_tool.shift_horizontal(delta)
                event.prevent_default()
                event.stop()
                return

        if key_name == "escape":
            event.prevent_default()
            event.stop()
            if app and hasattr(app, "action_handle_escape"):
                app.action_handle_escape()
            return

        if is_enter and not is_shift:
            event.prevent_default()
            event.stop()
            self.action_submit()
        elif is_shift and is_enter:
            event.prevent_default()
            event.stop()
            self.action_insert_newline()

    def _update_auto_height(self) -> None:
        width = self.size.width if self.size.width > 15 else 80
        total_lines = 0
        for line in self.text.split("\n"):
            total_lines += max(1, (len(line) // max(1, width - 4)) + 1)
        self.styles.height = max(3, min(8, total_lines + 2))


class HintRow(Static):
    """Row in the slash command hint dropdown matching CMDAI."""

    def __init__(self, markup: str = "", *, hint_index: int = 0, cmd: str = "", **kwargs):
        classes = kwargs.pop("classes", "")
        classes = f"cmd-hint {classes}".strip()
        super().__init__(markup, classes=classes, **kwargs)
        self.hint_index = hint_index
        self.cmd = cmd

    def set_selected(self, sel: bool) -> None:
        self.set_class(sel, "selected")
        from ..modals.help import COMMANDS_DOC
        # A file mention is not in COMMANDS_DOC; without the fallback its row
        # would render with an empty description.
        desc = COMMANDS_DOC.get(self.cmd, "file" if self.cmd.startswith("@") else "")
        if sel:
            self.update(f"[b #ffffff]{self.cmd}[/]  [#f0f6fc]{desc}[/]")
        else:
            self.update(f"[b #58a6ff]{self.cmd}[/]  [dim]{desc}[/]")

    def on_click(self, event: events.Click) -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "_hint_select_and_execute"):
            app._hint_select_and_execute(self.cmd)


class ApprovalBar(Horizontal):
    """Codex-style approval bar displayed in 1 clean line without borders inside #input-card."""

    DEFAULT_CSS = """
    ApprovalBar {
        width: 100%;
        height: auto;
        background: #161b22;
        border: none;
        border-bottom: solid #21262d;
        padding: 0 1;
        margin: 0;
        display: none;
        align-vertical: middle;
    }
    #approval-info {
        width: 1fr;
        height: 1;
        overflow: hidden;
        content-align: left middle;
        padding: 0;
        color: #e6edf3;
    }
    #approval-info:hover {
        text-style: underline;
    }
    #approval-actions {
        width: 22;
        height: 1;
        align-vertical: middle;
    }
    .approval-btn {
        width: auto;
        margin-left: 1;
        min-width: 0;
        height: 1;
        border: none !important;
        padding: 0 1;
        content-align: center middle;
        text-style: bold;
    }
    #approval-btn-approve, .btn-approve {
        background: #238636;
        color: #ffffff;
    }
    #approval-btn-approve:hover, .btn-approve:hover {
        background: #2ea043;
    }
    #approval-btn-reject, .btn-reject {
        background: #b62324;
        color: #ffffff;
    }
    #approval-btn-reject:hover, .btn-reject:hover {
        background: #da3633;
    }
    """

    class ActionSelected(events.Message):
        def __init__(self, action: str, details: Optional[Dict[str, Any]] = None) -> None:
            super().__init__()
            self.action = action
            self.details = details

    def __init__(self, **kwargs):
        classes = kwargs.pop("classes", "")
        classes = f"approval-bar {classes}".strip()
        super().__init__(classes=classes, **kwargs)
        self.pending_details: Optional[Dict[str, Any]] = None
        self.label_widget = Static("", id="approval-info")

    def compose(self):
        yield self.label_widget
        with Horizontal(id="approval-actions"):
            yield Static("Approve", id="approval-btn-approve", classes="approval-btn btn-approve")
            yield Static("Remove", id="approval-btn-reject", classes="approval-btn btn-reject")

    def show_request(self, details: Dict[str, Any]) -> None:
        self.pending_details = details
        action = details.get("action", "edit")
        target = details.get("path") or details.get("cmd") or "action"
        diff_info = details.get("diff_info", "")
        file_count = details.get("file_count", 1)
        files = details.get("files", [])
        if not file_count and files:
            file_count = len(files)

        lines_added = details.get("lines_added", 0)
        lines_deleted = details.get("lines_deleted", 0)

        stats_parts = []
        if lines_added or lines_deleted:
            stats_parts.append(f"+{lines_added} -{lines_deleted} lines")
        elif diff_info and ("lines" in diff_info or "+" in diff_info):
            stats_parts.append(diff_info)

        if file_count > 1:
            stats_parts.append(f"{file_count} files")
        elif file_count == 1:
            stats_parts.append("1 file")

        stats_str = " · ".join(stats_parts)
        inspect_hint = f"({stats_str} · click to inspect)" if stats_str else "(click to inspect)"

        if action == "command":
            cmd = details.get("cmd") or target
            t = f"[b #58a6ff]● RUN:[/] [bold white]{cmd}[/] [dim #8b949e](run command)[/]"
        elif action == "write":
            disp_target = f"{file_count} files" if file_count > 1 else target
            t = f"[b #3fb950]● CREATE:[/] [bold white]{disp_target}[/] [dim]{inspect_hint}[/dim]"
        else:
            disp_target = f"{file_count} files" if file_count > 1 else target
            t = f"[b #d29922]● EDIT:[/] [bold white]{disp_target}[/] [dim]{inspect_hint}[/dim]"

        self.label_widget.update(t)
        self.styles.display = "block"

    def hide_request(self) -> None:
        self.pending_details = None
        self.styles.display = "none"

    @on(events.Click, "#approval-btn-approve")
    def on_approve_click(self) -> None:
        self.post_message(self.ActionSelected("allow", self.pending_details))

    @on(events.Click, "#approval-btn-reject")
    def on_reject_click(self) -> None:
        self.post_message(self.ActionSelected("reject", self.pending_details))

    @on(events.Click, "#approval-info")
    def on_info_click(self) -> None:
        self.post_message(self.ActionSelected("diff", self.pending_details))


class ApprovalDiffPreview(VerticalScroll):
    """Clean inline diff viewer displayed directly in place of the input."""

    DEFAULT_CSS = """
    ApprovalDiffPreview {
        width: 100%;
        height: auto;
        max-height: 12;
        min-height: 2;
        background: #0d1117;
        border: none;
        border-top: solid #21262d;
        padding: 0 2;
        margin: 0;
        display: none;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }
    #approval-diff-text {
        width: 100%;
        height: auto;
        color: #e6edf3;
    }
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("id", "approval-diff-preview")
        super().__init__(**kwargs)
        self.diff_widget = Static("", id="approval-diff-text")

    def compose(self) -> ComposeResult:
        yield self.diff_widget

    def show_diff(self, diff_text: str) -> None:
        t = Text()
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                t.append(line + "\n", style="bold #8b949e")
            elif line.startswith("+"):
                t.append(line + "\n", style="#7ee787")
            elif line.startswith("-"):
                t.append(line + "\n", style="#f85149")
            elif line.startswith("@@"):
                t.append(line + "\n", style="bold #58a6ff")
            else:
                t.append(line + "\n", style="#8b949e")
        self.diff_widget.update(t)
        self.styles.display = "block"
        self.scroll_to(y=0, animate=False)

    def hide_diff(self) -> None:
        self.styles.display = "none"
        self.diff_widget.update("")
