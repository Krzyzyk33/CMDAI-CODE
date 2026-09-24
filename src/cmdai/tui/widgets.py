from __future__ import annotations

import difflib
import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from rich.markdown import Markdown
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Static, TextArea

from ..agent.render import (
    render_command_output,
    render_edit_diff_opencode,
    render_edit_diff_side_by_side,
    render_tool_header,
)
from ..core.capabilities import clean_model_name


class UserMessageCard(Vertical):
    """User message card with in-card label and prompt text (Swift style). Click opens copy/revert/delete."""

    DEFAULT_CSS = """
    UserMessageCard {
        width: 100%;
        height: auto;
    }
    """

    def __init__(self, prompt: str = "", turn_id: int = -1, **kwargs):
        classes = kwargs.pop("classes", "")
        classes = f"user-card {classes}".strip()
        super().__init__(classes=classes, **kwargs)
        self.prompt = prompt
        self.turn_id = turn_id
        self._header = Static("[bold #58a6ff]user[/]", classes="card-label")
        self._body = Static(Text(prompt, style="white"), classes="card-body")

    def compose(self):
        yield self._header
        yield self._body

    def update_content(self, text: str) -> None:
        self.prompt = text
        self._body.update(Text(text, style="white"))

    def on_click(self, event: events.Click) -> None:
        try:
            app = getattr(self, "app", None)
            if app and hasattr(app, "show_user_actions"):
                app.show_user_actions(self.turn_id)
        except Exception:
            pass


class LoadingBlock(Vertical):
    """Model loading block with ⌬ / ✻ glyph and braille animation before thinking starts."""

    DEFAULT_CSS = """
    LoadingBlock {
        width: 100%;
        height: auto;
        margin: 0;
    }
    """

    SPINNERS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "Loading model…", **kwargs):
        super().__init__(classes="loading-block", **kwargs)
        self.message = message
        self.start_time = time.time()
        self.elapsed: float = 0.0
        self.is_finished: bool = False
        self._timer = None
        self.header_widget = Static("", classes="loading-header")

    def compose(self):
        yield self.header_widget

    def on_mount(self) -> None:
        self.update_header(live=True)
        self._timer = self.set_interval(0.08, self.tick)

    def tick(self) -> None:
        if not self.is_finished:
            self.elapsed = time.time() - self.start_time
            self.update_header(live=True)

    def finish(self, elapsed: Optional[float] = None) -> None:
        self.is_finished = True
        if self._timer:
            self._timer.stop()
            self._timer = None
        if elapsed is not None and elapsed > 0:
            self.elapsed = elapsed
        else:
            self.elapsed = time.time() - self.start_time
        self.update_header(live=False)

    def update_header(self, live: bool = False) -> None:
        t = Text()
        if live:
            glyph = ["⌬", "✻"][int(self.elapsed / 0.35) % 2]
            t.append(f"{glyph}  ", style="bold #58a6ff")
            t.append(self.message, style="#8b949e")
            t.append(f"  {self.elapsed:.1f}s", style="dim #8b949e")
        else:
            t.append("⌬  ", style="#8b949e")
            t.append(f"Loaded model in {self.elapsed:.1f}s", style="#8b949e")
        self.header_widget.update(t)


class AssistantTurnCard(Vertical):
    """Unified assistant turn card containing model header, loading block, thinking block,
    tool blocks, markdown response, and completion footer (Swift style)."""

    SPINNERS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    DOT_SPINNERS = ["●", "●", "○", "○"]

    DEFAULT_CSS = """
    AssistantTurnCard {
        width: 100%;
        height: auto;
    }
    """

    def __init__(self, model_name: str, initial_text: str = "", has_thinking: Optional[bool] = None, **kwargs):
        classes = kwargs.pop("classes", "")
        classes = f"assistant-card {classes}".strip()
        super().__init__(classes=classes, **kwargs)
        self.model_name = model_name
        self.raw_text = initial_text
        if has_thinking is None:
            try:
                from ..core.capabilities import get_model_capability
                cap = get_model_capability(self.model_name)
                self.has_thinking = bool(cap and cap.thinking)
            except Exception:
                self.has_thinking = False
        else:
            self.has_thinking = bool(has_thinking)
        self.start_time = time.time()
        self.elapsed: float = 0.0
        self.is_generating: bool = True
        self.current_status: str = "generating"

        self.header_widget = Static("", classes="card-header")
        self.loading_container = Vertical(classes="card-loading")
        self.thinking_container = Vertical(classes="card-thinking")
        self.flow_container = Vertical(classes="card-flow")
        self.tools_container = self.flow_container
        self.body_widget = Static("", classes="card-body")
        self.current_body_widget: Static = self.body_widget
        self.body_widgets: List[Static] = [self.body_widget]
        self.footer_widget = Static("", classes="card-footer")
        self.styles.height = "auto"
        for container in (self.loading_container, self.thinking_container, self.flow_container):
            container.styles.height = "auto"
            container.styles.min_height = 0
        self.footer_widget.styles.height = 1
        self.thinking_container.styles.display = "none"
        self.flow_container.styles.display = "none"
        self.body_widget.styles.display = "none"

        self.loading_block: Optional[LoadingBlock] = None
        self.thinking_block: Optional[ThinkingBlock] = None

        self.live_tool_widget = Static("", classes="live-tool-indicator")
        self.generating_tool_name: str = ""
        self.generating_tool_target: str = ""
        self.current_live_tool_text: str = ""
        self.tool_blocks: List[ToolBlock] = []
        self._pending_mounts: List[Any] = []

        self._spinner_idx = 0
        self._spinner_timer = None

    def compose(self):
        yield self.header_widget
        with self.loading_container:
            if self.loading_block:
                yield self.loading_block
        with self.thinking_container:
            if self.thinking_block:
                yield self.thinking_block
        with self.flow_container:
            yield self.current_body_widget
        yield self.live_tool_widget
        yield self.footer_widget

    def on_mount(self) -> None:
        self.update_header()
        if hasattr(self, "_pending_mounts"):
            for item in self._pending_mounts:
                try:
                    self.flow_container.mount(item)
                except Exception:
                    pass
            self._pending_mounts.clear()
            try:
                self.flow_container.refresh(layout=True)
                self.refresh(layout=True)
            except Exception:
                pass
        if self.raw_text:
            self._render_body(self.raw_text)
            self.finish(0.0)
        else:
            self._start_spinner()

    def _start_spinner(self) -> None:
        self._spinner_idx = 0
        def _tick():
            self._spinner_idx = (self._spinner_idx + 1) % len(self.SPINNERS)
            self.elapsed = time.time() - self.start_time
            if self.loading_block and not self.loading_block.is_finished:
                self.loading_block.tick()
            if self.thinking_block and not self.thinking_block.is_finished:
                self.thinking_block.tick()
            if getattr(self, "generating_tool_name", ""):
                self._update_live_tool_widget()
        self._spinner_timer = self.set_interval(0.18, _tick)

    def _stop_spinner(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def set_generating_tool(self, tool_name: str, target: str = "") -> None:
        self.generating_tool_name = tool_name
        self.generating_tool_target = target
        self._update_live_tool_widget()

    def clear_generating_tool(self) -> None:
        self.generating_tool_name = ""
        self.generating_tool_target = ""
        self.current_live_tool_text = ""
        try:
            self.live_tool_widget.styles.display = "none"
            self.live_tool_widget.update("")
        except Exception:
            pass

    def _update_live_tool_widget(self) -> None:
        if not getattr(self, "generating_tool_name", ""):
            self.current_live_tool_text = ""
            try:
                self.live_tool_widget.styles.display = "none"
                self.live_tool_widget.update("")
            except Exception:
                pass
            return
        if self.current_body_widget is not None and not getattr(self, "raw_text", "").strip():
            try:
                self.current_body_widget.styles.display = "none"
            except Exception:
                pass
        from ..agent.render import render_tool_header
        frame = self.DOT_SPINNERS[self._spinner_idx % len(self.DOT_SPINNERS)]
        t = render_tool_header(
            self.generating_tool_name,
            self.generating_tool_target,
            is_running=True,
            spinner_frame=frame,
        )
        self.current_live_tool_text = t.plain
        try:
            self.live_tool_widget.styles.display = "block"
            self.live_tool_widget.update(t)
        except Exception:
            pass

    def update_header(self) -> None:
        model_display = clean_model_name(self.model_name).lstrip("⌬").strip()
        t = Text()
        t.append("⌬  ", style="bold #58a6ff")
        t.append(model_display, style="bold #e6edf3")
        t.justify = "center"
        self.header_widget.update(t)

    def start_loading(self, message: str = "Loading model…") -> LoadingBlock:
        if self.loading_block is None:
            self.loading_block = LoadingBlock(message)
            if self.is_mounted and self.loading_container.is_mounted:
                self.loading_container.mount(self.loading_block)
            else:
                self.call_after_refresh(lambda: self.loading_container.mount(self.loading_block) if self.loading_container.is_mounted else None)
        return self.loading_block

    def finish_loading(self, elapsed: Optional[float] = None) -> None:
        if self.loading_block:
            self.loading_block.finish(elapsed)

    def start_thinking(self, message: str = "Thinking…") -> Optional[ThinkingBlock]:
        if not self.has_thinking:
            return None
        self.thinking_container.styles.display = "block"
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()
        if self.thinking_block is None:
            self.thinking_block = ThinkingBlock()
            if self.is_mounted and self.thinking_container.is_mounted:
                self.thinking_container.mount(self.thinking_block)
            else:
                self.call_after_refresh(lambda: self.thinking_container.mount(self.thinking_block) if self.thinking_container.is_mounted else None)
        return self.thinking_block

    def append_thinking(self, chunk: str) -> None:
        if not self.has_thinking:
            return
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()
        if self.thinking_block is None:
            self.start_thinking()
        if self.thinking_block:
            self.thinking_block.append_chunk(chunk)

    def finish_thinking(self) -> None:
        if self.thinking_block:
            if not self.thinking_block.thinking_text.strip():
                try:
                    self.thinking_block.remove()
                except Exception:
                    pass
                self.thinking_block = None
            else:
                self.thinking_block.finish()


    def add_tool(self, tool_name: str, target: str) -> ToolBlock:
        self.flow_container.styles.display = "block"
        self.clear_generating_tool()
        block = ToolBlock(tool_name, target)
        self.tool_blocks.append(block)

        if self.current_body_widget is not None and not getattr(self, "raw_text", "").strip():
            if self.current_body_widget in self.body_widgets:
                self.body_widgets.remove(self.current_body_widget)
            try:
                self.current_body_widget.styles.display = "none"
            except Exception:
                pass

        self.current_body_widget = None
        self.raw_text = ""

        def _mount_tool():
            try:
                self.flow_container.mount(block)
            except Exception:
                try:
                    self.mount(block)
                except Exception:
                    pass
            try:
                self.flow_container.refresh(layout=True)
                self.refresh(layout=True)
                if self.app:
                    self.app.refresh()
            except Exception:
                pass

        if self.is_mounted and self.flow_container.is_mounted:
            _mount_tool()
        else:
            if not hasattr(self, "_pending_mounts"):
                self._pending_mounts = []
            self._pending_mounts.append(block)
            self.call_after_refresh(_mount_tool)
            try:
                self.refresh(layout=True)
            except Exception:
                pass
        return block

    def finish_tool(self, result: Dict[str, Any]) -> None:
        self.clear_generating_tool()
        blocks = list(self.flow_container.query(ToolBlock)) or self.tool_blocks
        for b in reversed(blocks):
            if getattr(b, "tool_running", False):
                b.finish(result)
                break
        else:
            if blocks:
                blocks[-1].finish(result)

        try:
            self.flow_container.refresh(layout=True)
            self.refresh(layout=True)
            if self.app:
                self.app.refresh()
        except Exception:
            pass

    def append_token(self, chunk: str) -> None:
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()
        if self.thinking_block and not self.thinking_block.is_finished:
            self.thinking_block.finish()
        self.raw_text += chunk
        self._render_body(self.raw_text)

    def update_content(self, text: str) -> None:
        self.raw_text = text
        self._render_body(text)

    def update_last_body(self, text: str) -> None:
        if not text.strip():
            if self.current_body_widget is not None:
                try:
                    self.current_body_widget.styles.display = "none"
                    self.current_body_widget.update("")
                except Exception:
                    pass
            self.raw_text = ""
            return
        self.update_content(text)

    def append_text_block(self, text: str) -> None:
        """Appends a new chronological text commentary block into flow_container."""
        if not text.strip():
            return
        self.flow_container.styles.display = "block"
        new_body = Static("", classes="card-body")
        self.current_body_widget = new_body
        self.body_widgets.append(new_body)
        self.raw_text = text

        def _mount_body():
            try:
                self.flow_container.mount(new_body)
            except Exception:
                try:
                    self.mount(new_body)
                except Exception:
                    pass
            try:
                self.flow_container.refresh(layout=True)
                self.refresh(layout=True)
                if self.app:
                    self.app.refresh()
            except Exception:
                pass

        if self.is_mounted and self.flow_container.is_mounted:
            _mount_body()
        else:
            if not hasattr(self, "_pending_mounts"):
                self._pending_mounts = []
            self._pending_mounts.append(new_body)
            self.call_after_refresh(_mount_body)
            try:
                self.refresh(layout=True)
            except Exception:
                pass

        try:
            new_body.styles.display = "block"
            new_body.update(Markdown(text))
        except Exception:
            new_body.update(Text(text, style="white"))

    def prepare_for_next_turn(self) -> None:
        """Resets active body pointer so the next autonomous turn appends below preceding tools/text."""
        self.clear_generating_tool()
        self.current_body_widget = None
        self.raw_text = ""

    def _render_body(self, text: str) -> None:
        if not text.strip():
            if self.current_body_widget is not None:
                self.current_body_widget.update("")
            return

        self.flow_container.styles.display = "block"

        if self.current_body_widget is None:
            new_body = Static("", classes="card-body")
            self.current_body_widget = new_body
            self.body_widgets.append(new_body)

            def _mount_body():
                try:
                    if self.flow_container.is_mounted:
                        self.flow_container.mount(new_body)
                    self.flow_container.refresh(layout=True)
                    self.refresh(layout=True)
                    if self.app:
                        self.app.refresh()
                except Exception:
                    pass

            if self.is_mounted and self.flow_container.is_mounted:
                _mount_body()
            else:
                if not hasattr(self, "_pending_mounts"):
                    self._pending_mounts = []
                self._pending_mounts.append(new_body)
                self.call_after_refresh(_mount_body)
                try:
                    self.refresh(layout=True)
                except Exception:
                    pass

        target_w = self.current_body_widget
        try:
            target_w.styles.display = "block"
            target_w.update(Markdown(text))
        except Exception:
            target_w.update(Text(text, style="white"))

    def finish(self, elapsed: Optional[float] = None, tokens: int = 0, tok_s: float = 0.0) -> None:
        self.is_generating = False
        self._stop_spinner()
        self.clear_generating_tool()
        if elapsed is not None and elapsed > 0:
            self.elapsed = elapsed
        else:
            self.elapsed = time.time() - self.start_time
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()
        if self.thinking_block and not self.thinking_block.is_finished:
            self.thinking_block.finish()
        self.current_status = ""
        self.update_header()
        self.update_footer(tokens, tok_s)

    def update_footer(self, tokens: int = 0, tok_s: float = 0.0) -> None:
        t = Text()
        t.append("⌬  ", style="bold #58a6ff")
        t.append("Done", style="bold #e6edf3")
        t.append(f"  ·  {self.elapsed:.1f}s", style="#8b949e")
        if tokens > 0:
            t.append(f"  ·  {tokens} tokens", style="#8b949e")
        if tok_s > 0:
            t.append(f"  ({tok_s:.1f} tok/s)", style="dim #8b949e")
        self.footer_widget.update(t)

    def abort(self) -> None:
        self.is_generating = False
        self._stop_spinner()
        self.clear_generating_tool()
        self.elapsed = time.time() - self.start_time
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()
        if self.thinking_block and not self.thinking_block.is_finished:
            self.thinking_block.finish()
        self.current_status = ""
        self.update_header()
        t = Text()
        t.append("⌬  ", style="bold #8b949e")
        t.append("Cancelled (Esc Esc)", style="#8b949e")
        t.append(f"  ·  {self.elapsed:.1f}s", style="dim #8b949e")
        self.footer_widget.update(t)


class ClickableMessage(Vertical):
    """Backwards-compatible wrapper delegating to UserMessageCard or AssistantTurnCard."""

    def __init__(self, raw_text: str = "", is_user: bool = False, label: str = "", **kwargs):
        super().__init__(**kwargs)
        self.raw_text = raw_text
        self.is_user = is_user
        self.label_text = label
        if is_user:
            self._label_widget = Static("[bold #8b949e]user[/]", classes="card-label")
            self._body_widget = Static(Text(raw_text, style="white"), classes="card-body")
        else:
            model_name = label or "Assistant"
            self._label_widget = Static(model_name, classes="card-label") if label else None
            self._body_widget = Static("", classes="card-body")

    def compose(self):
        if self._label_widget:
            yield self._label_widget
        yield self._body_widget

    def on_mount(self) -> None:
        if self.raw_text:
            self.update_content(self.raw_text)

    def update_content(self, text: str) -> None:
        self.raw_text = text
        if self.is_user:
            self._body_widget.update(Text(text, style="white"))
        else:
            try:
                self._body_widget.update(Markdown(text))
            except Exception:
                self._body_widget.update(Text(text, style="white"))



def copy_text_to_clipboard(text: str) -> bool:
    """Copies text to clipboard safely using pyperclip or Windows clip.exe."""
    if not text:
        return False
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        pass
    try:
        import subprocess
        cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True, creationflags=cflags)
        p.communicate(input=text.encode("utf-16le"), timeout=2.0)
        return True
    except Exception:
        pass
    return False


def subprocess_run_copy(text: str) -> None:
    copy_text_to_clipboard(text)


class ThinkingBlock(Vertical):
    """Thinking block with Swift-identical 2-phase animation, ⌬ / ✻ glyphs, and compact elapsed time."""

    DEFAULT_CSS = """
    ThinkingBlock {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
    }
    """

    SPINNERS = ["⌬", "✻"]

    def __init__(self, **kwargs):
        super().__init__(classes="thinking-block", **kwargs)
        self.thinking_text = ""
        self.start_time = time.time()
        self.elapsed: float = 0.0
        self.is_finished: bool = False
        self.is_expanded: bool = False
        self._timer = None

        self.header_widget = Static("", classes="thinking-header")
        self.body_widget = Static("", classes="thinking-body")

    def compose(self):
        yield self.header_widget
        yield self.body_widget

    def on_mount(self) -> None:
        self.update_header(live=True)
        self._timer = self.set_interval(0.1, self.tick)

    def append_chunk(self, chunk: str) -> None:
        self.thinking_text += chunk
        self.elapsed = time.time() - self.start_time
        self.update_header(live=True)

    def tick(self) -> None:
        if not self.is_finished:
            self.elapsed = time.time() - self.start_time
            self.update_header(live=True)

    def finish(self) -> None:
        self.is_finished = True
        if self._timer:
            self._timer.stop()
            self._timer = None
        if not self.thinking_text.strip():
            self.styles.display = "none"
            return
        self.elapsed = time.time() - self.start_time
        self.update_header(live=False)
        try:
            self.body_widget.update(Markdown(self.thinking_text))
        except Exception:
            self.body_widget.update(Text(self.thinking_text, style="#8b949e"))

    def update_header(self, live: bool = False) -> None:
        if not live and not self.thinking_text.strip():
            try:
                self.header_widget.update("")
            except Exception:
                pass
            return
        t = Text()
        if live:
            glyph = self.SPINNERS[int(self.elapsed / 0.35) % len(self.SPINNERS)]
            t.append(f"{glyph}  ", style="bold #ffffff")
            t.append("Thinking...", style="bold #ffffff")
            t.append(f"  {self.elapsed:.1f}s", style="dim #8b949e")
        elif self.is_expanded:
            t.append("✻  ", style="bold #ffffff")
            t.append(f"Thought for {self.elapsed:.1f}s", style="#8b949e")
        else:
            t.append("⌬  ", style="#8b949e")
            t.append(f"Thought for {self.elapsed:.1f}s", style="#8b949e")

        try:
            self.header_widget.update(t)
        except Exception:
            pass

    def set_expanded(self, expanded: bool) -> None:
        self.is_expanded = expanded
        self.update_header(live=not self.is_finished)
        self.body_widget.styles.display = "block" if expanded else "none"

    def on_click(self, event: events.Click) -> None:
        if not self.is_finished:
            return
        self.set_expanded(not self.is_expanded)


class ToolBlock(Vertical):
    """Collapsible tool block rendered as 1 single gray line with ● vs ○ and animated spinner when running."""

    DEFAULT_CSS = """
    ToolBlock {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
    }
    ToolBlock .tool-details {
        width: 100%;
        height: auto;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow-x: hidden;
    }
    """

    SPINNERS = ["●", "●", "○", "○"]

    def __init__(self, tool_name: str, target: str, **kwargs):
        super().__init__(classes="tool-block", **kwargs)
        import uuid as _uuid
        self.tool_uid = _uuid.uuid4().hex[:8]
        if tool_name.lower() in ("bugs", "tool_bugs", "debug", "scan_bugs") or "syntax error" in str(target).lower() or "scanning project" in str(target).lower():
            target = ""
        self.tool_name = tool_name
        self.target = target
        self.is_expanded = False
        self.tool_running = True
        self.has_error = False
        self.result_data: Optional[Dict[str, Any]] = None
        self.details_text: str = ""
        self.h_offset: int = 0
        self._original_details: Optional[Text] = None
        self._is_mouse_hovered: bool = False
        self._spinner_idx = 0
        self._spinner_timer = None

        self.header_widget = Static("", classes="tool-header")
        self.details_widget = Static("", classes="tool-details")
        self.details_widget.styles.display = "none"
        self.details_widget.styles.text_wrap = "nowrap"
        self.details_widget.styles.text_overflow = "clip"
        self.details_widget.styles.overflow_x = "hidden"

    def compose(self):
        yield self.header_widget
        yield self.details_widget

    def on_mount(self) -> None:
        self.update_header()
        if self.tool_running:
            self._start_spinner()

    def on_enter(self, event: events.Enter) -> None:
        self._is_mouse_hovered = True

    def on_leave(self, event: events.Leave) -> None:
        self._is_mouse_hovered = False

    def on_mouse_scroll_left(self, event: events.MouseScrollLeft) -> None:
        if self.is_expanded:
            self.shift_horizontal(-6)
            event.stop()

    def on_mouse_scroll_right(self, event: events.MouseScrollRight) -> None:
        if self.is_expanded:
            self.shift_horizontal(6)
            event.stop()

    def _start_spinner(self) -> None:
        def _tick():
            if not self.tool_running:
                self._stop_spinner()
                return
            self._spinner_idx = (self._spinner_idx + 1) % len(self.SPINNERS)
            self.update_header()
        self._spinner_timer = self.set_interval(0.18, _tick)

    def _stop_spinner(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def update_header(self) -> None:
        frame = self.SPINNERS[self._spinner_idx % len(self.SPINNERS)] if self.tool_running else ""
        text = render_tool_header(
            self.tool_name,
            self.target,
            is_expanded=self.is_expanded,
            is_running=self.tool_running,
            spinner_frame=frame,
            is_error=self.has_error,
        )
        try:
            self.header_widget.update(text)
        except Exception:
            pass

    def _safe_update_details(self, content: Any) -> None:
        try:
            if isinstance(content, Text):
                self._original_details = content
                self.details_text = content.plain
            elif hasattr(content, "plain"):
                self._original_details = Text(str(content.plain), no_wrap=True)
                self.details_text = content.plain
            else:
                s = str(content)
                self._original_details = Text(s, no_wrap=True)
                self.details_text = s
            self._apply_horizontal_shift()
        except Exception:
            pass

    def shift_horizontal(self, delta: int) -> None:
        if not self.is_expanded or not self._original_details:
            return
        lines = self._original_details.plain.splitlines()
        max_len = max((len(l) for l in lines), default=0)
        visible_w = 0
        try:
            visible_w = self.details_widget.size.width
        except Exception:
            pass
        if visible_w <= 0:
            try:
                visible_w = self.app.size.width
            except Exception:
                pass
        if visible_w <= 0:
            visible_w = 50

        avail_cols = max(10, visible_w - 6)
        max_shift = max(0, (max_len + 1) - avail_cols)
        new_offset = max(0, min(max_shift, self.h_offset + delta))
        if new_offset != self.h_offset:
            self.h_offset = new_offset
            self._apply_horizontal_shift()

    def _apply_horizontal_shift(self) -> None:
        if not self._original_details:
            return
        try:
            if self.h_offset <= 0:
                self.details_widget.update(self._original_details)
                return

            orig_lines = self._original_details.split("\n")
            shifted = Text(no_wrap=True)
            for idx, line in enumerate(orig_lines):
                if len(line) > self.h_offset:
                    shifted.append_text(line[self.h_offset:])
                else:
                    shifted.append("")
                if idx < len(orig_lines) - 1:
                    shifted.append("\n")
            self.details_widget.update(shifted)
        except Exception:
            pass

    def finish(self, result: Dict[str, Any]) -> None:
        self.tool_running = False
        self._stop_spinner()
        self.result_data = result
        is_err = bool(
            result.get("error")
            or result.get("success") is False
            or (self.tool_name == "command" and result.get("exit_code", 0) != 0 and not result.get("success", True))
        )
        self.has_error = is_err
        self.update_header()

        if is_err:
            err_msg = str(result.get("error") or result.get("stderr") or "Tool execution failed.")
            txt = Text(no_wrap=False)
            txt.append("  ● Execution Failed:\n", style="bold #f85149")
            if result.get("missing"):
                txt.append(f"    Missing: {result['missing']}\n", style="bold #e6edf3")
            if result.get("example"):
                txt.append(f"    Example: {result['example']}\n", style="#58a6ff")
            if self.target:
                txt.append(f"    Target:  {self.target}\n", style="bold #e6edf3")
            txt.append(f"    Reason:  {err_msg}\n", style="#ff7b72")
            if result.get("stdout"):
                txt.append("\n  ● Standard Output:\n", style="bold #8b949e")
                for l in str(result["stdout"]).splitlines()[:20]:
                    txt.append(f"    {l}\n", style="#8b949e")
            if result.get("missing") and not self.target:
                self.target = f"missing '{result['missing']}'"
            self.update_header()
            self._safe_update_details(txt)
            try:
                self.refresh(layout=True)
                if self.app:
                    self.app.refresh()
            except Exception:
                pass
            return

        if self.tool_name == "edit":
            diff_entries = result.get("diff_entries", [])
            adds = sum(1 for e in diff_entries if len(e) >= 2 and e[1] == "add")
            dels = sum(1 for e in diff_entries if len(e) >= 2 and e[1] == "del")
            if (adds or dels) and "(" not in self.target:
                self.target = f"{self.target} (+{adds} -{dels})"
                self.update_header()
            if diff_entries:
                self._safe_update_details(render_edit_diff_opencode(diff_entries, target_file=self.target))
            else:
                self._safe_update_details(Text(f"  {self.target}\n  (Edit applied successfully)\n", style="#3fb950"))
        elif self.tool_name in ("summarizing", "summarize", "compact", "context_compact"):
            summary_txt = str(result.get("summary") or result.get("content") or "Context compacted successfully.").strip()
            self.target = "context compacted"
            self.update_header()
            txt = Text(no_wrap=False)
            txt.append("  ⌬ Context Compaction & Handoff:\n", style="bold #58a6ff")
            for line in summary_txt.splitlines()[:30]:
                txt.append(f"    {line}\n", style="#c9d1d9")
            if len(summary_txt.splitlines()) > 30:
                txt.append("    ... [remaining summary truncated]\n", style="dim #8b949e")
            self._safe_update_details(txt)
        elif self.tool_name == "command":
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            self._safe_update_details(render_command_output(stdout, stderr, cmd=self.target))
        elif self.tool_name == "write":
            content = result.get("content", "")
            lines = content.splitlines() if content else []
            if "(" not in self.target:
                self.target = f"{self.target} ({len(lines)} lines)"
                self.update_header()
            txt = Text(no_wrap=True)
            for idx, line in enumerate(lines, 1):
                num_str = f"  {idx:>4}    "
                clean_l = str(line).replace("\t", "    ")
                txt.append(num_str, style="#484f58")
                txt.append(f"{clean_l}\n", style="#e6edf3")
            self._safe_update_details(txt)
        elif self.tool_name == "read" and "content" in result:
            lines = str(result["content"]).splitlines()
            if "(" not in self.target:
                self.target = f"{self.target} ({len(lines)} lines)"
                self.update_header()
            start_l = 1
            if "line_range" in result and "-" in str(result["line_range"]):
                try:
                    start_l = int(str(result["line_range"]).split("-")[0])
                except Exception:
                    start_l = 1
            txt = Text(no_wrap=True)
            for idx, l in enumerate(lines, start_l):
                num_str = f"  {idx:>4}    "
                clean_l = str(l).replace("\t", "    ")
                txt.append(num_str, style="#484f58")
                txt.append(f"{clean_l}\n", style="#e6edf3")
            self._safe_update_details(txt)
        elif self.tool_name in ("web", "fetch", "search_web"):
            results = result.get("results", [])
            txt = Text(no_wrap=True)
            if results and isinstance(results, list):
                if "(" not in self.target:
                    self.target = f"{self.target} ({len(results)} results)"
                    self.update_header()
                import textwrap
                for i, r in enumerate(results, 1):
                    title = str(r.get("title", "Result")).strip()
                    url = str(r.get("url", "")).strip()
                    snippet = str(r.get("snippet", "")).strip()

                    txt.append(f"  {i}. ", style="bold #58a6ff")
                    txt.append(f"{title}\n", style="bold #e6edf3")

                    if url:
                        display_url = url if len(url) <= 85 else url[:82] + "..."
                        txt.append(f"     {display_url}\n", style="dim #58a6ff")

                    if snippet:
                        wrapped_lines = textwrap.wrap(snippet, width=82)
                        for line in wrapped_lines:
                            txt.append(f"     {line}\n", style="#8b949e")

                    if i < len(results):
                        txt.append("\n")
            elif "content" in result:
                content = str(result["content"]).strip()
                lines = content.splitlines()
                for l in lines[:40]:
                    txt.append(f"  {l}\n", style="#8b949e")
                if len(lines) > 40:
                    txt.append(f"  ... [{len(lines) - 40} lines truncated]\n", style="dim #484f58")
            elif "error" in result:
                txt.append(f"  Web error: {result['error']}\n", style="#f85149")
            self._safe_update_details(txt)
        elif self.tool_name in ("bugs", "tool_bugs"):
            scanned = result.get("scanned_files", 0)
            errors = result.get("errors", {})
            txt = Text(no_wrap=False)
            if not errors:
                txt.append(f"  Syntax check passed: 0 errors across {scanned} files\n", style="#8b949e")
            else:
                txt.append(f"  Syntax errors in {len(errors)} file(s) (of {scanned} scanned):\n", style="bold #e6edf3")
                for fpath, errs in errors.items():
                    txt.append(f"    • {fpath}\n", style="#8b949e")
                    for e in errs:
                        txt.append(f"      {e}\n", style="#8b949e")
            self._safe_update_details(txt)
        elif self.tool_name in ("code_search", "codesearch", "symbol_search"):
            symbols = result.get("symbols", [])
            txt = Text(no_wrap=True)
            if symbols:
                if "(" not in self.target:
                    self.target = f"{self.target} ({len(symbols)} symbols)"
                    self.update_header()
                for s in symbols:
                    name = s.get("name", "")
                    sig = s.get("signature", name)
                    fp = s.get("filepath", "")
                    line = s.get("start_line", 1)
                    stype = s.get("symbol_type", "symbol")
                    txt.append(f"  {fp}:{line} ", style="#58a6ff")
                    txt.append(f"[{stype}] ", style="dim #8b949e")
                    txt.append(f"{sig}\n", style="bold #e6edf3")
            else:
                txt.append(f"  No symbols found for query: {self.target}\n", style="dim #8b949e")
            self._safe_update_details(txt)
        elif self.tool_name == "screenshot":
            txt = Text(no_wrap=True)
            if result.get("path"):
                if "(" not in self.target:
                    self.target = f"{self.target} ({(result.get('size_bytes', 0) // 1024)} KB)"
                    self.update_header()
                txt.append(f"  {result['path']}\n", style="#e6edf3")
                txt.append("  (open the file to view; call <tool:vision> to analyze it)\n", style="dim #8b949e")
            else:
                txt.append(f"  Screenshot failed: {result.get('error', 'unknown')}\n", style="#f85149")
            self._safe_update_details(txt)
        elif self.tool_name in ("vision", "mcps"):
            txt = Text(no_wrap=True)
            if self.tool_name == "vision" and result.get("path"):
                txt.append(f"  {result['path']}\n", style="#e6edf3")
                if result.get("question"):
                    txt.append(f"  question: {result['question']}\n", style="#8b949e")
            else:
                for s in result.get("servers", [result.get("note", "")]):
                    if s:
                        txt.append(f"  {s}\n", style="#8b949e")
                if result.get("note") and result.get("servers"):
                    txt.append(f"  {result['note']}\n", style="dim #484f58")
            self._safe_update_details(txt)
        elif "content" in result:
            lines = str(result["content"]).splitlines()
            txt = Text(no_wrap=True)
            for l in lines:
                clean_l = str(l).replace("\t", "    ")
                txt.append(f"    {clean_l}\n", style="#e6edf3")
            self._safe_update_details(txt)
        elif "error" in result:
            self._safe_update_details(Text(f"    Error: {result['error']}\n", style="#f85149"))
        elif "entries" in result:
            entries = result.get("entries", [])
            dirs = [e for e in entries if e.get("type") == "dir"]
            files = [e for e in entries if e.get("type") != "dir"]
            all_items = dirs + files
            total = len(all_items)
            if "(" not in self.target:
                self.target = f"{self.target} ({total} items)"
                self.update_header()
            txt = Text(no_wrap=True)
            for idx, e in enumerate(all_items):
                name = e.get("name", "")
                if e.get("type") == "dir":
                    txt.append(f"  {name}/\n", style="bold #58a6ff")
                    children = e.get("children", [])
                    for child in children:
                        cname = child.get("name", "")
                        c_is_dir = child.get("is_dir", False)
                        txt.append(f"      {cname}\n", style="bold #58a6ff" if c_is_dir else "#8b949e")
                else:
                    txt.append(f"  {name}\n", style="#e6edf3")
            self._safe_update_details(txt)
        elif "matches" in result:
            matches = result.get("matches", [])
            if "(" not in self.target:
                self.target = f"{self.target} ({len(matches)} matches)"
                self.update_header()
            txt = Text(no_wrap=True)
            if not matches:
                clean_tgt = self.target.split(" (")[0].strip()
                txt.append(f"  No files matching: {clean_tgt}\n", style="dim #8b949e")
            else:
                for m in matches:
                    txt.append(f"    {m}\n", style="#8b949e")
            self._safe_update_details(txt)
        elif "results" in result:
            results = result.get("results", [])
            if "(" not in self.target:
                self.target = f"{self.target} ({len(results)} matches)"
                self.update_header()
            txt = Text(no_wrap=True)
            if not results:
                clean_tgt = self.target.split(" (")[0].strip()
                txt.append(f"  No matches found for query: {clean_tgt}\n", style="dim #8b949e")
            else:
                by_file = {}
                for r in results:
                    f = r.get("file", "")
                    by_file.setdefault(f, []).append(r)
                for filename, matches in by_file.items():
                    txt.append(f"  {filename} ", style="bold #58a6ff")
                    txt.append(f"({len(matches)} matches)\n", style="#8b949e")
                    for m in matches:
                        line_num = m.get("line", "")
                        content = m.get("content", "")
                        clean_c = str(content).replace("\t", "    ")
                        txt.append(f"    {line_num:>4}    ", style="#484f58")
                        txt.append(f"{clean_c}\n", style="#e6edf3")
            self._safe_update_details(txt)
        elif self.tool_name in ("scratch", "todo", "tasks"):
            txt = Text(no_wrap=True)
            steps = result.get("visible_steps", [])
            if steps:
                for idx, s in enumerate(steps, 1):
                    is_done = False
                    if isinstance(s, dict):
                        is_done = s.get("done", False)
                        step_text = s.get("text", "")
                    else:
                        step_text = str(s)
                        is_done = (idx == 1)

                    if is_done:
                        txt.append("  ●  ", style="bold #7ee787")
                        txt.append(f"{step_text}\n", style="bold #e6edf3")
                    else:
                        txt.append("  ○  ", style="#8b949e")
                        txt.append(f"{step_text}\n", style="#8b949e")
            else:
                note = result.get("note", self.target)
                if note and note not in ("(TODO)", "plan", "test.py modification completed"):
                    txt.append(f"  ●  {note}\n", style="bold #7ee787")
                else:
                    txt.append("  ●  Task completed successfully\n", style="bold #7ee787")
            self._safe_update_details(txt)
        elif self.tool_name in ("summarizing", "summarize", "compact", "context_compact"):
            txt = Text(no_wrap=False)
            summary_content = result.get("summary") or result.get("content") or self.target or ""
            clean_summary = str(summary_content).strip()
            clean_summary = re.sub(r"^#+\s*CONTEXT COMPACTION STATE HANDOFF\s*", "", clean_summary, flags=re.IGNORECASE).strip()
            clean_summary = re.sub(r"^●?\s*Context Compaction[^\n]*\n*", "", clean_summary, flags=re.IGNORECASE).strip()
            clean_summary = re.sub(r"^●?\s*CONTEXT COMPACTION[^\n]*\n*", "", clean_summary, flags=re.IGNORECASE).strip()

            txt.append("  ● Conversation Summary:\n\n", style="bold #58a6ff")
            for l in clean_summary.splitlines():
                line = l.rstrip()
                if not line:
                    txt.append("\n")
                else:
                    txt.append(f"  {line}\n", style="#e6edf3")
            self._safe_update_details(txt)

        elif self.tool_name == "ask":
            txt = Text()
            qa_list = result.get("qa_list") or result.get("answers")
            if qa_list and isinstance(qa_list, list):
                for idx, qa in enumerate(qa_list, 1):
                    q = qa.get("question", "")
                    a = qa.get("answer", "")
                    txt.append(f"  {idx}. {q}\n", style="bold #e6edf3")
                    txt.append(f"     ↳ {a}\n", style="bold #7ee787")
            else:
                q = result.get("question", self.target)
                ans = result.get("answer", "")
                txt.append(f"  {q}\n", style="#e6edf3")
                if ans:
                    txt.append(f"  ↳  {ans}\n", style="bold #7ee787")
            self._safe_update_details(txt)

        try:
            self.refresh(layout=True)
            if self.app:
                self.app.refresh()
        except Exception:
            pass

    def set_expanded(self, expanded: bool) -> None:
        self.is_expanded = expanded
        if not expanded:
            self.h_offset = 0
            if self._original_details:
                self._apply_horizontal_shift()
        self.update_header()
        self.details_widget.styles.display = "block" if expanded else "none"

    def on_click(self, event: events.Click) -> None:
        if self.tool_running:
            return
        if self.tool_name == "screenshot" and self.result_data and self.result_data.get("path"):
            import os as _os
            import subprocess as _sp
            p = self.result_data["path"]
            try:
                if _os.name == "nt":
                    _os.startfile(p)
                else:
                    _sp.Popen(["xdg-open", p])
                return
            except Exception:
                pass
        w = getattr(event, "widget", None)
        if w is not None:
            if w == self.details_widget:
                return
            ancestors = getattr(w, "ancestors", [])
            if self.details_widget in ancestors:
                return
        self.set_expanded(not self.is_expanded)


class TodoPreviewBar(Static):
    """TODO preview bar positioned directly above #input-card, showing max 3 items with scroll."""

    DEFAULT_CSS = """
    TodoPreviewBar {
        width: 100%;
        height: auto;
        max-height: 6;
        background: #161b22;
        border: none;
        border-bottom: solid #21262d;
        padding: 0 2 0 2;
        margin: 0;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("id", "todo-preview-bar")
        super().__init__(**kwargs)
        self.steps: List[Any] = []

    def update_steps(self, steps: List[Any]) -> None:
        self.steps = steps
        if not self.steps:
            self.styles.display = "none"
            self.update("")
            return

        self.styles.display = "block"
        t = Text()
        t.append("\n")
        for idx, s in enumerate(self.steps, 1):
            if isinstance(s, dict):
                is_done = s.get("done", False)
                step_text = s.get("text", "")
            else:
                step_text = str(s)
                is_done = (idx == 1)

            if is_done:
                t.append("  ● ", style="bold #7ee787")
                t.append(f"{step_text}", style="bold #e6edf3")
            else:
                t.append("  ○ ", style="#8b949e")
                t.append(f"{step_text}", style="#8b949e")

            if idx < len(self.steps):
                t.append("\n")

        self.update(t)


from dataclasses import dataclass
from textual.binding import Binding
from textual.message import Message


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
    def action_copy(self) -> None:
        sel = self.selected_text
        if sel:
            copy_text_to_clipboard(sel)
            app = getattr(self, "app", None)
            if app:
                app.notify(f"Skopiowano zaznaczenie ({len(sel)} zn.)", title="Clipboard", timeout=1.5)
        elif self.text.strip():
            copy_text_to_clipboard(self.text)
            app = getattr(self, "app", None)
            if app:
                app.notify("Skopiowano tekst wpisu", title="Clipboard", timeout=1.5)

    def action_submit(self) -> None:
        app = getattr(self, "app", None)
        hints_active = (
            app is not None
            and getattr(app, "_hint_input_id", None) == self.id
            and bool(getattr(app, "_hint_matches", None))
            and getattr(app, "_hint_sel", -1) >= 0
            and self.text.lstrip().startswith("/")
        )
        if hints_active and hasattr(app, "_hint_execute"):
            app._hint_execute()
            return

        text = self.text.strip()
        if text:
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
            and self.text.lstrip().startswith("/")
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
        self.styles.height = max(2, min(8, total_lines + 1))


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
        from .modals.help import COMMANDS_DOC
        desc = COMMANDS_DOC.get(self.cmd, "")
        if sel:
            self.update(f"[b #ffffff]{self.cmd}[/]  [#f0f6fc]{desc}[/]")
        else:
            self.update(f"[b #58a6ff]{self.cmd}[/]  [dim]{desc}[/]")

    def on_click(self, event: events.Click) -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "_hint_select_and_execute"):
            app._hint_select_and_execute(self.cmd)


def build_diff_text_from_details(details: Dict[str, Any]) -> str:
    """Builds clean unified diff text for a proposed tool action."""
    action = details.get("action", "edit")
    target = details.get("path") or details.get("cmd") or "action"
    old = details.get("old", "")
    new = details.get("new", "")
    content = details.get("content", "")

    if action == "edit" and (old or new):
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{target}",
            tofile=f"b/{target}",
        ))
        return "".join(diff) if diff else "(No content differences)"
    elif action == "write" and content:
        lines = content.splitlines()[:150]
        preview = "".join([f"+{line}\n" for line in lines])
        if len(content.splitlines()) > 150:
            preview += f"... [{len(content.splitlines()) - 150} lines truncated]\n"
        return f"--- /dev/null\n+++ b/{target}\n" + preview
    elif action == "command":
        cmd = details.get("cmd") or target
        return f"$ {cmd}\n\n# Terminal shell command execution requested by agent.\n# Press Allow (y) to run or Reject (n) to skip."
    return "(Preview unavailable)"


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
