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
    UserMessageCard:hover {
        border: round #388bfd;
        background: #161b22;
    }
    """

    def __init__(self, prompt: str = "", turn_id: int = -1, **kwargs):
        classes = kwargs.pop("classes", "")
        classes = f"user-card {classes}".strip()
        super().__init__(classes=classes, **kwargs)
        self.prompt = prompt
        self.turn_id = turn_id
        self._header = Static("[bold #58a6ff]user[/] [dim #6e7681]· actions[/]", classes="card-label")
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


def get_lexer_for_path(path: str) -> str:
    """Return Pygments lexer name based on file extension."""
    if not path:
        return "text"
    import os
    clean_p = path.split(" (")[0].strip()
    ext = os.path.splitext(clean_p)[1].lower().lstrip(".")
    ext_map = {
        "html": "html", "htm": "html",
        "py": "python", "pyw": "python",
        "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
        "ts": "typescript", "tsx": "typescript",
        "css": "css", "scss": "scss", "sass": "sass", "less": "less",
        "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml",
        "md": "markdown", "markdown": "markdown",
        "sh": "bash", "bash": "bash", "zsh": "bash", "ps1": "powershell", "bat": "batch", "cmd": "batch",
        "rs": "rust", "go": "go", "cpp": "cpp", "c": "c", "h": "c", "hpp": "cpp",
        "java": "java", "kt": "kotlin", "cs": "csharp", "php": "php", "sql": "sql",
        "xml": "xml", "svg": "xml",
    }
    return ext_map.get(ext, ext or "text")


class LiveToolIndicator(Vertical):
    """Live tool indicator while a tool is being generated/streamed.
    Expandable on click to stream live tool content / arguments in real time."""

    DEFAULT_CSS = """
    LiveToolIndicator {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        display: none;
    }
    LiveToolIndicator .live-tool-details {
        width: 100%;
        height: auto;
        padding: 1 2;
        margin-top: 1;
        margin-bottom: 1;
        background: #0d1117;
        overflow-x: hidden;
        text-wrap: nowrap;
        text-overflow: clip;
        display: none;
    }
    """

    DOT_SPINNERS = ["●", "●", "○", "○"]

    def __init__(self, **kwargs):
        super().__init__(classes="live-tool-indicator", **kwargs)
        self.tool_name: str = ""
        self.target: str = ""
        self.live_text: str = ""
        self.is_expanded: bool = False
        self._spinner_idx: int = 0
        self.header_widget = Static("", classes="tool-header")
        self.details_widget = Static("", classes="live-tool-details")
        self._target_code: str = ""
        self._displayed_code: str = ""
        self._typewriter_timer = None
        self.start_time: Optional[float] = None

    def compose(self):
        yield self.header_widget
        yield self.details_widget

    def _extract_code(self, raw: str) -> str:
        code_to_show = raw.strip()
        if code_to_show.startswith(("{", "[")) and any(k.lower() in code_to_show.lower() for k in ('"content"', '"code"', '"new"', '"new_content"', '"codecontent"', '"replacementcontent"')):
            import re
            m = re.search(r'"(?:CodeContent|code_content|content|code|new|new_content|replacementContent|replacement_content)"\s*:\s*"((?:\\.|[^"\\])*)"?', code_to_show)
            if m:
                code_to_show = m.group(1).replace('\\n', '\n').replace('\\t', '    ').replace('\\"', '"').replace('\\\\', '\\')
        return code_to_show

    def _typewriter_tick(self) -> None:
        target_len = len(self._target_code)
        disp_len = len(self._displayed_code)
        if disp_len < target_len:
            remaining = target_len - disp_len
            if remaining > 1000:
                step = max(30, remaining // 8)
            elif remaining > 300:
                step = max(12, remaining // 6)
            elif remaining > 50:
                step = max(4, remaining // 4)
            else:
                step = min(remaining, 3)
            self._displayed_code = self._target_code[:disp_len + step]
            if self.is_expanded:
                self._render_details(self._displayed_code)
        else:
            if self._typewriter_timer is not None:
                try:
                    self._typewriter_timer.stop()
                except Exception:
                    pass
                self._typewriter_timer = None

    def update_state(self, tool_name: str, target: str = "", live_text: str = "", spinner_idx: int = 0) -> None:
        if self.start_time is None:
            self.start_time = time.time()
        self.tool_name = tool_name
        if not target and live_text:
            import re
            KEY_PAT = r'(?:\\?"?(?:TargetFile|target_file|targetfile|filePath|filepath|file_path|filename|file_name|dest|destination|file|path|cmd|command|commandLine|command_line|url|query)\\?"?)'
            m = re.search(KEY_PAT + r'\s*[:=]\s*(?:\\?["\']([^"\'\r\n}>]+)(?:\\?["\'])?|([^"\'\s,}>]+))', live_text, flags=re.IGNORECASE)
            if not m:
                m = re.search(r'<(?:TargetFile|target_file|targetfile|filePath|filepath|file_path|filename|file_name|file|path|dest)>([^<>\r\n]+)', live_text, flags=re.IGNORECASE)
            if m:
                raw_tgt = m.group(1) or (m.group(2) if len(m.groups()) > 1 else "")
                target = raw_tgt.replace('\\\\', '\\').replace('\\/', '/').strip('"\'\\')
            elif tool_name.lower() in ("write", "edit", "read"):
                first_line = live_text.strip().splitlines()[0].strip() if live_text.strip() else ""
                if ("." in first_line or "/" in first_line or "\\" in first_line) and len(first_line) < 100:
                    if not any(k in first_line for k in ("(", ")", ";", "=", "{", "}", "[", "]", ":", "<", ">", '"', "'", "import", "def", "class", "from", "//", "/*", "print", "console", "return", "function", "var", "let", "const")):
                        target = first_line
                elif "<!doctype html" in live_text.lower() or "<html" in live_text.lower():
                    target = "index.html"

        if target and tool_name.lower() in ("write", "edit", "read"):
            try:
                import os
                app = getattr(self, "app", None)
                wdir = getattr(app, "workdir", "") if app else ""
                if wdir and not os.path.isabs(target):
                    target = os.path.normpath(os.path.join(wdir, target))
            except Exception:
                pass

        # Never use (in folder) fallback
        if target and target.startswith("(in "):
            target = ""
        self.target = target
        if live_text:
            self.live_text = live_text
            self._target_code = self._extract_code(live_text)
            if not self._displayed_code:
                self._displayed_code = ""
            if self._typewriter_timer is None and self.is_expanded:
                try:
                    self._typewriter_timer = self.set_interval(0.025, self._typewriter_tick)
                except Exception:
                    pass
        self._spinner_idx = spinner_idx

        # Auto-expand expandable tools (write, edit) while generating; terminal stays collapsed
        if tool_name.lower() in ("write", "edit"):
            self.is_expanded = True
            self.details_widget.styles.display = "block"
            if self._typewriter_timer is None and self.live_text:
                try:
                    self._typewriter_timer = self.set_interval(0.025, self._typewriter_tick)
                except Exception:
                    pass
        else:
            self.is_expanded = False
            self.details_widget.styles.display = "none"

        self._update_display(update_details=True)

    def set_spinner_idx(self, idx: int) -> None:
        self._spinner_idx = idx
        self._update_display(update_details=False)

    def _update_display(self, update_details: bool = True) -> None:
        if not self.tool_name or (self.tool_name.lower() in ("write", "edit", "read") and not self.target):
            self.styles.display = "none"
            return
        self.styles.display = "block"
        frame = self.DOT_SPINNERS[self._spinner_idx % len(self.DOT_SPINNERS)]
        from ..agent.render import render_tool_header
        elapsed_s = (time.time() - self.start_time) if self.start_time else None
        t = render_tool_header(
            self.tool_name,
            self.target,
            is_expanded=self.is_expanded,
            is_running=True,
            spinner_frame=frame,
            elapsed_s=elapsed_s,
        )
        try:
            self.header_widget.update(t)
        except Exception:
            pass
        if update_details and self.is_expanded:
            self._render_details(self._displayed_code if self._displayed_code else self._target_code)

    def _render_details(self, code_to_show: Optional[str] = None) -> None:
        if code_to_show is None:
            code_to_show = self._displayed_code if self._displayed_code else self._target_code
        if not code_to_show and self.live_text:
            code_to_show = self._extract_code(self.live_text)
        if not code_to_show:
            txt = Text(no_wrap=True)
            txt.append("  (generating...)\n", style="dim #8b949e")
            try:
                self.details_widget.update(txt)
            except Exception:
                pass
            return

        lexer = get_lexer_for_path(self.target)
        try:
            from rich.syntax import Syntax
            syntax = Syntax(
                code_to_show,
                lexer,
                theme="github-dark",
                line_numbers=True,
                word_wrap=False,
                indent_guides=True,
            )
            self.details_widget.update(syntax)
        except Exception:
            lines = code_to_show.splitlines()
            txt = Text(no_wrap=True)
            for idx, line in enumerate(lines, 1):
                clean_l = str(line).replace("\t", "    ")
                txt.append(f"  {idx:>4}    ", style="#484f58")
                txt.append(f"{clean_l}\n", style="#e6edf3")
            try:
                self.details_widget.update(txt)
            except Exception:
                pass

    def on_click(self, event: events.Click) -> None:
        self.is_expanded = not self.is_expanded
        self.details_widget.styles.display = "block" if self.is_expanded else "none"
        if self.is_expanded and self._typewriter_timer is None and self._displayed_code != self._target_code:
            try:
                self._typewriter_timer = self.set_interval(0.025, self._typewriter_tick)
            except Exception:
                pass
        self._update_display(update_details=True)

    def clear(self) -> None:
        if self._typewriter_timer is not None:
            try:
                self._typewriter_timer.stop()
            except Exception:
                pass
            self._typewriter_timer = None
        self.tool_name = ""
        self.target = ""
        self.live_text = ""
        self._target_code = ""
        self._displayed_code = ""
        self.is_expanded = False
        self.start_time = None
        try:
            self.styles.display = "none"
            self.header_widget.update("")
            self.details_widget.update("")
            self.details_widget.styles.display = "none"
        except Exception:
            pass


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

    def __init__(self, model_name: str, initial_text: str = "", has_thinking: Optional[bool] = None, turn_id: int = -1, **kwargs):
        classes = kwargs.pop("classes", "")
        classes = f"assistant-card {classes}".strip()
        super().__init__(classes=classes, **kwargs)
        self.model_name = model_name
        self.raw_text = initial_text
        self.turn_id = turn_id
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
        # Textual's default Vertical sizing can claim all remaining space in a
        # scroll view.  Keep each turn content-sized so tool rows never get
        # pushed to the bottom of an otherwise empty screen.
        self.styles.height = "auto"
        for container in (self.loading_container, self.thinking_container, self.flow_container):
            container.styles.height = "auto"
            container.styles.min_height = 0
        self.footer_widget.styles.height = 1
        self.thinking_container.styles.display = "none"
        # An empty Vertical container can consume the remaining viewport in a
        # scrollable parent. It is shown only once it contains chat content.
        self.flow_container.styles.display = "none"
        self.body_widget.styles.display = "none"

        self.loading_block: Optional[LoadingBlock] = None
        self.thinking_block: Optional[ThinkingBlock] = None

        self.live_tool_widget = LiveToolIndicator()
        self.generating_tool_name: str = ""
        self.generating_tool_target: str = ""
        self.current_live_tool_text: str = ""
        self.tool_blocks: List[ToolBlock] = []
        self._pending_mounts: List[Any] = []
        self._last_finalized_text: str = ""

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
            # A card can start with no text yet (the subagent window builds one
            # from tool events), so the body widget is optional. Only yield in compose
            # if no pending mounts were already queued (which preserves chronological order on replay).
            if not self._pending_mounts and self.current_body_widget is not None:
                yield self.current_body_widget
        yield self.live_tool_widget
        yield self.footer_widget

    def on_click(self, event: events.Click) -> None:
        # Clicking model response does not show action menu (only clicking user question cards does)
        pass

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

    def set_generating_tool(self, tool_name: str, target: str = "", live_text: str = "") -> None:
        self.finish_thinking()
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()
        if not target and live_text:
            import re
            m = re.search(KEY_PAT + r'\s*[:=]\s*(?:\\?["\']([^"\'\r\n}>]+)(?:\\?["\'])?|([^"\'\s,}>]+))', live_text, flags=re.IGNORECASE)
            if not m:
                m = re.search(r'<(?:TargetFile|target_file|targetfile|filePath|filepath|file_path|filename|file_name|file|path|dest)>([^<>\r\n]+)', live_text, flags=re.IGNORECASE)
            if m:
                raw_tgt = m.group(1) or (m.group(2) if len(m.groups()) > 1 else "")
                target = raw_tgt.replace('\\\\', '\\').replace('\\/', '/').strip('"\'\\')
            elif tool_name.lower() in ("write", "edit", "read"):
                first_line = live_text.strip().splitlines()[0].strip() if live_text.strip() else ""
                if ("." in first_line or "/" in first_line or "\\" in first_line) and len(first_line) < 100:
                    if not any(k in first_line for k in ("(", ")", ";", "=", "{", "}", "[", "]", ":", "<", ">", '"', "'", "import", "def", "class", "from", "//", "/*", "print", "console", "return", "function", "var", "let", "const")):
                        target = first_line
                elif "<!doctype html" in live_text.lower() or "<html" in live_text.lower():
                    target = "index.html"
        if target and tool_name.lower() in ("write", "edit", "read"):
            try:
                import os
                app = getattr(self, "app", None)
                wdir = getattr(app, "workdir", "") if app else ""
                if wdir and not os.path.isabs(target):
                    target = os.path.normpath(os.path.join(wdir, target))
            except Exception:
                pass
        if getattr(self, "_tool_generation_start", None) is None:
            self._tool_generation_start = time.time()
        self.generating_tool_name = tool_name
        self.generating_tool_target = target
        if hasattr(self.live_tool_widget, "update_state"):
            self.live_tool_widget.update_state(tool_name, target, live_text, self._spinner_idx)
        self._update_live_tool_widget()

    def clear_generating_tool(self) -> None:
        self.generating_tool_name = ""
        self.generating_tool_target = ""
        self.current_live_tool_text = ""
        if hasattr(self.live_tool_widget, "clear"):
            self.live_tool_widget.clear()

    def _update_live_tool_widget(self) -> None:
        if not getattr(self, "generating_tool_name", ""):
            if hasattr(self.live_tool_widget, "clear"):
                self.live_tool_widget.clear()
            return
        if self.current_body_widget is not None and not getattr(self, "raw_text", "").strip():
            try:
                self.current_body_widget.styles.display = "none"
            except Exception:
                pass
        if hasattr(self.live_tool_widget, "set_spinner_idx"):
            self.live_tool_widget.set_spinner_idx(self._spinner_idx)

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
        self.finish_thinking()
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()
        self.flow_container.styles.display = "block"
        was_live_expanded = bool(getattr(self.live_tool_widget, "is_expanded", False))
        live_code = (
            getattr(self.live_tool_widget, "_displayed_code", "")
            or getattr(self.live_tool_widget, "_target_code", "")
            or getattr(self.live_tool_widget, "live_text", "")
        )
        gen_start = getattr(self, "_tool_generation_start", None)
        if not target and getattr(self, "generating_tool_target", "") and not self.generating_tool_target.startswith("("):
            target = self.generating_tool_target
        if target and target.startswith("(in "):
            target = ""
        if target and tool_name.lower() in ("write", "edit", "read"):
            try:
                import os
                app = getattr(self, "app", None)
                wdir = getattr(app, "workdir", "") if app else ""
                if wdir and not os.path.isabs(target):
                    target = os.path.normpath(os.path.join(wdir, target))
            except Exception:
                pass
        self.clear_generating_tool()
        self._tool_generation_start = None

        # Finish any previous lingering read/write/inspect tools so they don't spin forever
        for b in (self.flow_container.query(ToolBlock) or self.tool_blocks):
            if getattr(b, "tool_running", False):
                b_name = (getattr(b, "tool_name", "") or "").lower()
                if "background" in str(getattr(b, "target", "")).lower():
                    continue
                if b_name in ("read", "file_read", "web", "fetch", "webfetch", "ls", "grep", "search", "glob") or b_name == tool_name.lower():
                    b.finish({"result": "completed"})

        # Deduplicate consecutive identical tool blocks for the same target while still running
        all_blocks = list(self.flow_container.query(ToolBlock)) or self.tool_blocks
        if all_blocks and target:
            last_b = all_blocks[-1]
            if (
                (getattr(last_b, "tool_name", "") or "").lower() == tool_name.lower()
                and getattr(last_b, "tool_running", False)
            ):
                last_tgt = str(getattr(last_b, "target", "")).split(" (")[0].strip()
                clean_tgt = str(target).split(" (")[0].strip()
                if last_tgt and last_tgt == clean_tgt:
                    # Update existing block rather than creating duplicate row
                    if gen_start is not None and getattr(last_b, "start_time", None):
                        last_b.start_time = min(last_b.start_time, gen_start)
                    return last_b

        # Clean up any dummy placeholder blocks of the same tool (e.g. premature or duplicate)
        for b in list(self.flow_container.query(ToolBlock) or self.tool_blocks):
            b_name = (getattr(b, "tool_name", "") or "").lower()
            b_tgt = str(getattr(b, "target", "")).strip().lower()
            if b_name == tool_name.lower() and (b_tgt.startswith("<!doctype") or b_tgt in ("working", "running", "(no target)")):
                try:
                    b.remove()
                    if b in self.tool_blocks:
                        self.tool_blocks.remove(b)
                except Exception:
                    pass

        block = ToolBlock(tool_name, target)
        if gen_start is not None:
            block.start_time = gen_start
        if block.tool_name.lower() in ("write", "edit"):
            block.set_expanded(True)
            if live_code:
                block.seed_details(live_code)
        elif was_live_expanded and block.tool_name.lower() not in ("terminal", "command", "bash", "cmd", "sh", "run_command"):
            block.set_expanded(True)
        else:
            block.set_expanded(False)
        self.tool_blocks.append(block)

        if self.current_body_widget is not None and not getattr(self, "raw_text", "").strip():
            if self.current_body_widget in self.body_widgets:
                self.body_widgets.remove(self.current_body_widget)
            try:
                self.current_body_widget.styles.display = "none"
            except Exception:
                pass

        if getattr(self, "raw_text", "").strip():
            self._last_finalized_text = getattr(self, "raw_text", "").strip()
        self._pre_tool_body_widget = self.current_body_widget
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

    def note_server_tool(self, tool_name: str, phase: str, detail: str) -> None:
        """A tool the provider ran on its own server.

        Distinct from `add_tool`, which this app calls for tools it executed
        itself from a `<tool:name>` tag. Here the tool ran somewhere else and
        the text never named it, so the block is created from the event and
        then updated in place.

        The provider sends `(name, phase, detail)` with no call id, so
        consecutive calls of the *same* tool in one turn are matched in
        completion order. That is right for every provider that reports this
        way - a tool cannot run and finish twice in the same instant - and it is
        why the phases are collapsed here rather than matched on identity.

        A server-side subagent (`subagent`, and OpenCode's `task` tool, which
        its own client groups under "subagents") is not a tool block at all: it
        becomes a subagent marker, so a click opens the subagent window instead
        of expanding a row inline. Without this a server subagent rendered as a
        plain expandable block showing its goal text, while the local one next
        to it opened a window - same name, two behaviours.
        """
        self.finish_thinking()
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()

        name = str(tool_name or "tool")
        phase = str(phase or "running")
        detail = str(detail or "")

        if name.lower() == "question":
            self.clear_generating_tool()
            return

        if name.lower() in ("subagent", "task"):
            self._note_server_subagent(name, phase, detail)
            return

        is_execute = name.lower() in ("execute", "eval", "evaluate") or detail.strip().startswith('{"code":')
        if is_execute:
            import re
            raw_d = detail
            url_m = re.search(r"https?://[^\s'\"`\\]+", raw_d)
            if ("fetch" in raw_d.lower() or "http" in raw_d.lower()) and url_m:
                name = "webfetch"
                detail = url_m.group(0)
            elif any(k in raw_d.lower() for k in ("child_process", "exec(", "spawn(", "execsync", "bash", "cmd")):
                name = "terminal"
                cmd_m = re.search(r"(?:exec|spawn|execSync)\(\s*['\"]([^'\"]+)['\"]", raw_d)
                detail = cmd_m.group(1) if cmd_m else "command"
            else:
                name = "script"
                if detail.strip().startswith('{"code":') or "function " in detail or "const " in detail:
                    detail = ""
            if phase in ("done", "success"):
                phase = "failed"

        if phase == "generating":
            tgt = ""
            live_txt = detail
            if detail.strip().startswith("{") and any(k in detail for k in ("live_code", "target", "path", "TargetFile", "file")):
                try:
                    import json as _json
                    payload = _json.loads(detail)
                    tgt = payload.get("target") or payload.get("path") or payload.get("TargetFile") or payload.get("file") or ""
                    live_txt = payload.get("live_code") or payload.get("content") or payload.get("code") or detail
                except Exception:
                    pass
            elif len(detail) < 120 and "\n" not in detail:
                tgt = detail
            if not tgt and live_txt and name.lower() in ("write", "edit", "read"):
                import re
                KEY_PAT = r'(?:\\?"?(?:TargetFile|target_file|targetfile|filePath|filepath|file_path|filename|file_name|dest|destination|file|path)\\?"?)'
                m = re.search(KEY_PAT + r'\s*:\s*\\?"((?:\\.|[^"\r\n}>])+)', live_txt, flags=re.IGNORECASE)
                if not m:
                    m = re.search(KEY_PAT + r'\s*=\s*(?:\\?["\']?)([^"\'\\>\s]+)', live_txt, flags=re.IGNORECASE)
                if m:
                    tgt = m.group(1).replace('\\\\', '\\').replace('\\"', '"').replace('\\/', '/').strip('"\'\\')
                elif "<!doctype html" in live_txt.lower() or "<html" in live_txt.lower():
                    tgt = "index.html"
            self.set_generating_tool(name, tgt, live_text=live_txt)
            return

        if phase == "running":
            if name.lower() == "read" and detail:
                d_str = str(detail).strip()
                for prefix in ("read file ", "reading file ", "read ", "reading "):
                    if d_str.lower().startswith(prefix):
                        detail = d_str[len(prefix):].strip()
                        break
            block = self.add_tool(name, detail or "running")
            # Details are otherwise only filled in by `finish`, so expanding a
            # call that is still running showed an empty box. The target is the
            # one thing known at this point, and it is what the user clicked on
            # to find out.
            if detail:
                try:
                    block.seed_details(detail)
                except Exception:
                    pass
            return
        if phase == "progress":
            # The call is already on screen; update it rather than adding a row.
            # A long call publishes progress repeatedly, and treating each one
            # as a new call filled the chat with copies of the same tool.
            existing = [b for b in (self.flow_container.query(ToolBlock) or self.tool_blocks)
                        if getattr(b, "tool_name", None) == name
                        and getattr(b, "tool_running", False)]
            if existing:
                block = existing[-1]
                if detail and detail.strip().lower() not in ("working", "starting", "…", "..."):
                    if name.lower() in ("write", "edit") and ("\n" in detail or len(detail) > 80):
                        block.update_live_code(detail)
                    else:
                        block.target = detail
                        try:
                            block.update_header()
                        except Exception:
                            pass
                return
            # If no running call exists yet, do NOT add a new ToolBlock (which would create duplicates)!
            # Instead, update the live generating indicator:
            if hasattr(self, "live_tool_widget"):
                self.set_generating_tool(name, getattr(self, "generating_tool_target", ""), live_text=detail)
            return

        blocks = [b for b in self.flow_container.query(ToolBlock) or self.tool_blocks
                  if getattr(b, "tool_name", None) == name]
        block = None
        first_line_d = detail.splitlines()[0].strip().lower() if detail.strip() else ""
        if first_line_d:
            block = next((b for b in reversed(blocks)
                          if getattr(b, "tool_running", False)
                          and (first_line_d in str(b.target).lower() or str(b.target).lower() in first_line_d)), None)
        if block is None:
            block = next((b for b in reversed(blocks)
                          if getattr(b, "tool_running", False)), None)
        if block is None and first_line_d:
            block = next((b for b in reversed(blocks)
                          if first_line_d in str(b.target).lower() or str(b.target).lower() in first_line_d), None)
        if block is None and is_execute:
            running_candidates = [b for b in (self.flow_container.query(ToolBlock) or self.tool_blocks)
                                  if getattr(b, "tool_running", False)]
            if running_candidates:
                block = running_candidates[-1]
        if block is None:
            self.clear_generating_tool()
            if not detail.strip() or detail.strip().lower() in ("done", "ok"):
                return
            # An outcome with no start: still worth showing, or a failure would
            # be the only thing anyone ever sees.
            block = self.add_tool(name, detail.splitlines()[0])

        try:
            if phase == "done" and not is_execute:
                out_body = detail
                blk_tgt = str(getattr(block, "target", "") or "").split(" (")[0].strip()
                if blk_tgt and detail.startswith(blk_tgt + "\n"):
                    out_body = detail[len(blk_tgt) + 1:]
                elif blk_tgt and detail.strip() == blk_tgt:
                    out_body = ""
                res_dict: Dict[str, Any] = {"success": True, "result": out_body or detail}
                if name.lower() in ("command", "shell", "bash", "terminal", "sh", "cmd", "run_command", "exec"):
                    res_dict["stdout"] = out_body
                block.finish(res_dict)
            else:
                err_body = detail
                blk_tgt = str(getattr(block, "target", "") or "").split(" (")[0].strip()
                if blk_tgt and detail.startswith(blk_tgt + "\n"):
                    err_body = detail[len(blk_tgt) + 1:]
                block.finish({"error": err_body or detail or "failed"})
        except Exception:
            pass



    def _note_server_subagent(self, tool_name: str, phase: str, detail: str) -> None:
        """A subagent the provider ran on its own server, as a marker.

        Same contract as a locally spawned one: a click opens the subagent
        window, and the marker never expands inline. The server gives no
        transcript in this app's format, so the marker carries what the events
        did say - the goal it started with and one event per outcome, which is
        """
        self.finish_thinking()
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()

        blocks = [b for b in (self.flow_container.query(ToolBlock) or self.tool_blocks)
                  if getattr(b, "tool_name", None) in ("subagent", "task")
                  and getattr(b, "subagent_role", "")]
        block = next((b for b in reversed(blocks)
                      if getattr(b, "tool_running", False)), None)

        if phase in ("running", "progress") and block is None:
            block = self.add_tool("subagent", detail or "running")
            block.subagent_role = "Subagent"
            block.subagent_goal = detail
            block.subagent_status = "running"
            try:
                block.update_header()
            except Exception:
                pass
            return

        if block is None:
            # An outcome with no start. Still a marker, or a failure would open
            # nothing and the error would have nowhere to live.
            block = self.add_tool("subagent", detail or "done")
            block.subagent_role = "Subagent"
            block.subagent_goal = detail
            block.subagent_status = "running"

        if phase == "progress":
            # The live line, not a new row: progress arrives repeatedly and each
            # one opened another block before this existed.
            if detail:
                low = detail.strip().lower()
                if low in ("working", "starting", "…", "..."):
                    # A placeholder, not information. Overwriting the live line
                    # with it replaced the file being read with the word
                    # "Working" - which is what a whole window ended up showing.
                    return
                block.subagent_live_tool = detail
                # ... and a line in the window, or the window shows the live
                # line and nothing else. Placeholders ("working", "starting")
                # say nothing beyond "it runs", so only real details land here.
                low = detail.strip().lower()
                if low and low not in ("working", "starting", "…", "..."):
                    notes = block.subagent_notes
                    if not notes or notes[-1] != detail:
                        notes.append(detail)
                        del notes[:max(0, len(notes) - 30)]
                try:
                    block.update_header()
                except Exception:
                    pass
            return

        block.subagent_events.append({
            "tool": tool_name,
            "target": detail,
            "result": {"success": phase == "done", "content": detail},
            "ok": phase == "done",
        })
        block.subagent_status = "completed" if phase == "done" else "failed"
        try:
            if phase == "done":
                block.finish({"result": detail} if detail else {})
            else:
                block.finish({"error": detail or "failed"})
        except Exception:
            pass

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

    def finish_running_tools(self, default_result: Optional[Dict[str, Any]] = None) -> None:
        """Closes any orphaned or lingering tool blocks."""
        blocks = list(self.flow_container.query(ToolBlock)) or self.tool_blocks
        for b in blocks:
            if getattr(b, "tool_running", False):
                b_name = (getattr(b, "tool_name", "") or "").lower()
                if "background" in str(getattr(b, "target", "")).lower():
                    continue
                b.finish(default_result or {"result": "completed"})

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

    def update_pre_tool_content(self, text: str) -> None:
        """Update the text widget positioned above a currently-running server tool."""
        w = getattr(self, "_pre_tool_body_widget", None)
        if w is not None and text.strip():
            self._last_finalized_text = text.strip()
            try:
                w.styles.display = "block"
                w.update(Markdown(text))
            except Exception:
                try:
                    w.update(Text(text, style="white"))
                except Exception:
                    pass

    def update_last_body(self, text: str) -> None:
        last_final = getattr(self, "_last_finalized_text", "").strip()
        if last_final:
            clean_text = text.strip()
            if clean_text == last_final:
                return
            if clean_text.startswith(last_final):
                text = text[len(last_final):].lstrip("\r\n")
                if not text.strip():
                    return
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
        self.finish_thinking()
        self.finish_running_tools()
        if self.loading_block and not self.loading_block.is_finished:
            self.loading_block.finish()
        if not text.strip():
            return
        last_final = getattr(self, "_last_finalized_text", "").strip()
        if last_final:
            clean_text = text.strip()
            if clean_text == last_final:
                return
            if clean_text.startswith(last_final):
                text = text[len(last_final):].lstrip("\r\n")
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
        self.finish_thinking()
        self.clear_generating_tool()
        self.finish_running_tools()
        if getattr(self, "raw_text", "").strip():
            self._last_finalized_text = getattr(self, "raw_text", "").strip()
        self.current_body_widget = None
        self.raw_text = ""

    def _render_body(self, text: str) -> None:
        if not text.strip():
            if self.current_body_widget is not None:
                self.current_body_widget.update("")
            return

        last_final = getattr(self, "_last_finalized_text", "").strip()
        if last_final:
            clean_text = text.strip()
            if clean_text == last_final:
                if self.current_body_widget is not None:
                    try:
                        self.current_body_widget.styles.display = "none"
                        self.current_body_widget.update("")
                    except Exception:
                        pass
                return
            if clean_text.startswith(last_final):
                text = text[len(last_final):].lstrip("\r\n")
                if not text.strip():
                    if self.current_body_widget is not None:
                        try:
                            self.current_body_widget.styles.display = "none"
                            self.current_body_widget.update("")
                        except Exception:
                            pass
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

    def finish(
        self,
        elapsed: Optional[float] = None,
        tokens: int = 0,
        tok_s: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        self.is_generating = False
        self._stop_spinner()
        self.clear_generating_tool()
        self.finish_running_tools()
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
        self.update_footer(tokens, tok_s, tokens_in=tokens_in, tokens_out=tokens_out)

    def update_footer(
        self,
        tokens: int = 0,
        tok_s: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        t = Text()
        t.append("⌬  ", style="bold #58a6ff")
        t.append("Done", style="bold #e6edf3")
        if self.elapsed > 0.05:
            t.append(f"  ·  {self.elapsed:.1f}s", style="#8b949e")
        out_cnt = tokens_out if tokens_out > 0 else tokens
        if out_cnt > 0:
            t.append(f"  ·  {out_cnt} tokens", style="#8b949e")
            if tokens_in > 0:
                t.append(f" ({tokens_in} in / {out_cnt} out)", style="dim #8b949e")
        elif tokens_in > 0:
            t.append(f"  ·  {tokens_in} tokens in", style="#8b949e")
        if tok_s > 0:
            t.append(f"  ({tok_s:.1f} tok/s)", style="dim #8b949e")
        self.footer_widget.update(t)

    def abort(self) -> None:
        self.is_generating = False
        self._stop_spinner()
        self.clear_generating_tool()
        self.finish_running_tools({"error": "cancelled"})
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

    # The pair asked for, in all three places it appears: here, beside the model
    # name in the input bar, and in /models. It was briefly Braille, to dodge the
    # fact that both glyphs are East-Asian *ambiguous* width - rich lays them in
    # one cell, this terminal draws them across two. The dodging was wrong: the
    # smear came from the glyph's spare cell being overdrawn by *text*, so the
    # fix is a gap of spaces after the glyph, not a different alphabet.
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
        if not self.is_finished:
            self.update_header(live=True)
            if self._timer is None:
                self._timer = self.set_interval(0.1, self.tick)
        else:
            self.update_header(live=False)

    def _update_body(self) -> None:
        if not self.thinking_text.strip():
            return
        try:
            self.body_widget.update(Markdown(self.thinking_text))
        except Exception:
            self.body_widget.update(Text(self.thinking_text, style="#8b949e"))

    def append_chunk(self, chunk: str) -> None:
        if self.is_finished:
            return
        self.thinking_text += chunk
        self.elapsed = time.time() - self.start_time
        self.update_header(live=True)
        if self.is_expanded:
            self._update_body()

    def tick(self) -> None:
        if self.is_finished:
            if self._timer:
                self._timer.stop()
                self._timer = None
            return
        self.elapsed = time.time() - self.start_time
        self.update_header(live=True)

    def finish(self) -> None:
        if self.is_finished:
            return
        self.is_finished = True
        if self._timer:
            self._timer.stop()
            self._timer = None
        if not self.thinking_text.strip():
            self.styles.display = "none"
            return
        self.elapsed = max(0.001, time.time() - self.start_time)
        self.update_header(live=False)
        self._update_body()

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
            t.append(f"{glyph}  ", style="#58a6ff")
            t.append("Thinking", style="#8b949e")
            t.append(f"  {self.elapsed:.1f}s", style="dim #6e7681")
        else:
            # The star is a marker, not an emphasis. It was `bold #ffffff` while
            # the words beside it were grey, so the one glyph on the line was the
            # only thing on it lit up.
            glyph = "✻" if self.is_expanded else "⌬"
            t.append(f"{glyph}  ", style="#8b949e")
            if self.elapsed > 0.0:
                t.append(f"Thought for {max(0.1, self.elapsed):.1f}s", style="#8b949e")
            else:
                t.append("Thought", style="#8b949e")

        try:
            self.header_widget.update(t)
        except Exception:
            pass

    def set_expanded(self, expanded: bool) -> None:
        self.is_expanded = expanded
        self.update_header(live=not self.is_finished)
        if expanded:
            self._update_body()
        self.body_widget.styles.display = "block" if expanded else "none"

    def on_click(self, event: events.Click) -> None:
        # Expandable while the model is still thinking. This refused whenever
        # the block was unfinished, which is the whole time anyone would want to
        # read it - the reasoning is arriving exactly then, and waiting for the
        # finish meant waiting for the answer to arrive before reading why.
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
    NON_EXPANDABLE_TOOLS = {
        "read", "scratch", "glob", "search", "grep",
        "web", "webfetch", "fetch", "search_web", "web_search", "websearch", "searchweb",
        "google_search", "brave_search", "duckduckgo_search",
    }

    def is_tool_expandable(self) -> bool:
        if getattr(self, "subagent_role", ""):
            return False
        tl = (self.tool_name or "").lower().strip()
        if tl.startswith("tool:"):
            tl = tl[5:].strip()
        tl_clean = tl.replace("-", "").replace("_", "").replace(" ", "")
        if tl in self.NON_EXPANDABLE_TOOLS or tl_clean in (
            "web", "webfetch", "fetch", "searchweb", "websearch", "read", "scratch", "glob", "search", "grep", "googlesearch", "bravesearch"
        ):
            return False
        return True

    def update_live_code(self, code: str) -> None:
        """Format and update live code display with line numbers and syntax highlighting."""
        if not code:
            return
        lexer = get_lexer_for_path(self.target)
        try:
            from rich.syntax import Syntax
            syntax = Syntax(code, lexer, theme="github-dark", line_numbers=True, word_wrap=False)
            self._safe_update_details(syntax)
        except Exception:
            lines = code.splitlines()
            txt = Text(no_wrap=True)
            for idx, line in enumerate(lines, 1):
                num_str = f"  {idx:>4}    "
                clean_l = str(line).replace("\t", "    ")
                txt.append(num_str, style="#484f58")
                txt.append(f"{clean_l}\n", style="#e6edf3")
            self._safe_update_details(txt)
        if self.is_expanded:
            self.details_widget.styles.display = "block"

    def __init__(self, tool_name: str, target: str, **kwargs):
        super().__init__(classes="tool-block", **kwargs)
        import uuid as _uuid
        self.tool_uid = _uuid.uuid4().hex[:8]
        if tool_name.lower() in ("bugs", "tool_bugs", "debug", "scan_bugs") or "syntax error" in str(target).lower() or "scanning project" in str(target).lower():
            target = ""
        if target:
            if str(target).strip().startswith("(in "):
                target = ""
            tl = tool_name.lower()
            t_str = str(target).strip()
            if tl == "read":
                for prefix in ("read file ", "reading file ", "read ", "reading "):
                    if t_str.lower().startswith(prefix):
                        target = t_str[len(prefix):].strip()
                        break
            elif tl in ("edit", "write"):
                for prefix in (f"{tl} file ", f"{tl}ing file ", f"{tl} "):
                    if t_str.lower().startswith(prefix):
                        target = t_str[len(prefix):].strip()
                        break
        has_initial_err = False
        if tool_name.lower() in ("execute", "eval", "evaluate") or str(target).strip().startswith('{"code":'):
            import re
            raw_t = str(target)
            url_m = re.search(r"https?://[^\s'\"`\\]+", raw_t)
            if ("fetch" in raw_t.lower() or "http" in raw_t.lower()) and url_m:
                tool_name = "webfetch"
                target = url_m.group(0)
            elif any(k in raw_t.lower() for k in ("child_process", "exec(", "spawn(", "execsync", "bash", "cmd")):
                tool_name = "terminal"
                cmd_m = re.search(r"(?:exec|spawn|execSync)\(\s*['\"]([^'\"]+)['\"]", raw_t)
                target = cmd_m.group(1) if cmd_m else "command"
            else:
                tool_name = "script"
                target = ""
            has_initial_err = True
        self.tool_name = tool_name
        self.target = target
        self.is_expanded = False
        self.tool_running = True
        self.has_error = has_initial_err
        self.result_data: Optional[Dict[str, Any]] = None
        self.details_text: str = ""
        self.h_offset: int = 0
        self._original_details: Optional[Text] = None
        self._is_mouse_hovered: bool = False
        self._spinner_idx = 0
        self._spinner_timer = None
        self.start_time: float = time.time()
        self.elapsed_text: str = ""

        # Subagent-only state. A subagent block shows its role instead of the
        # literal tool name and keeps the live session data so a click can open
        # the subagent chat.
        self.subagent_role: str = ""
        self.subagent_status: str = "running"
        self.subagent_index: int = 1
        self.subagent_total: int = 1
        self.subagent_goal: str = ""
        self.subagent_system: str = ""
        self.subagent_history: List[Dict[str, Any]] = []
        self.subagent_findings: List[str] = []
        self.subagent_notes: List[str] = []
        self.subagent_tool_count: int = 0
        self.subagent_live_tool: str = ""
        self.subagent_tokens: int = 0
        self.subagent_elapsed: float = 0.0
        self.subagent_events: List[Dict[str, Any]] = []

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
            elapsed = time.time() - self.start_time
            tl = (self.tool_name or "").lower()
            if tl in ("read", "file_read", "ls", "grep", "search", "glob") and elapsed > 20.0:
                self.finish({"result": f"(Completed in {elapsed:.1f}s)"})
                return
            self._spinner_idx = (self._spinner_idx + 1) % len(self.SPINNERS)
            self.elapsed_text = f"{elapsed:.1f}s"
            self.update_header()
        self._spinner_timer = self.set_interval(0.18, _tick)

    def _stop_spinner(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def seed_details(self, text: str) -> None:
        """Give a still-running block something to show when it is clicked.

        `finish` fills the details in, and until then they are empty - so
        expanding a running tool rendered an empty box, which reads as "the
        click did nothing" rather than "this has no result yet".
        """
        text = str(text or "").strip()
        if not text:
            return
        tl = (self.tool_name or "").lower()
        if tl in ("write", "edit") and ("\n" in text or len(text) > 80):
            self.update_live_code(text)
            return
        if tl in ("terminal", "command", "shell", "bash", "sh", "cmd", "run_command", "exec"):
            # When terminal command is running, do not seed the raw command text as output!
            # The header already shows the command; the details box must display output.
            txt = Text(no_wrap=True)
            txt.append("  (running command...)\n", style="dim #8b949e")
            self._original_details = txt
            self.details_text = ""
            try:
                self.details_widget.update(self._original_details)
            except Exception:
                pass
            return
        self._original_details = Text(text, no_wrap=True)
        self.details_text = text
        try:
            self.details_widget.update(self._original_details)
        except Exception:
            pass

    def update_header(self) -> None:
        frame = self.SPINNERS[self._spinner_idx % len(self.SPINNERS)] if self.tool_running else ""
        display_name = self.subagent_role if self.subagent_role else ""
        target = self.target
        if self.subagent_role:
            # "<role>  <tool> <path>" while it works, "<role>  complete" when it
            # is done. Between tools there is no live tool, so the line is just
            # the role with its spinner - keeping "starting" or the previous
            # tool's path there made it look like it was looking at the wrong
            # file. self.target only becomes "complete" once finish_subagent
            # has run, which is why tool_running, not the status string, is
            # what decides: the orchestrator emits "completed" before the block
            # is closed out.
            if self.subagent_live_tool:
                target = self.subagent_live_tool
            elif self.tool_running:
                target = ""
            else:
                target = self.target
            target = ("  " + str(target).strip()) if str(target).strip() else ""
        is_exp = self.is_expanded if self.is_tool_expandable() else False
        text = render_tool_header(
            self.tool_name,
            target,
            is_expanded=is_exp,
            is_running=self.tool_running,
            spinner_frame=frame,
            is_error=self.has_error,
            display_name=display_name,
            elapsed_str=self.elapsed_text,
        )
        try:
            self.header_widget.update(text)
        except Exception:
            pass

    def set_subagent_progress(self, progress: Any) -> None:
        """Mirror a SubagentProgress event into the chat marker."""
        try:
            tool = str(getattr(progress, "current_tool", "") or "")
            arg = str(getattr(progress, "tool_arg", "") or "")
            if tool == "thinking":
                # Between tools. Clear the live line instead of leaving the
                # last one up: the subagent had already finished it, so a
                # spinner kept spinning on a tool that was already done and the
                # window claimed it was looking at the wrong file.
                self.subagent_live_tool = ""
            elif tool and tool not in ("loading model", "agent_note"):
                clean_arg = arg.strip()
                tl = tool.lower()
                if tl == "read":
                    for prefix in ("read file ", "reading file ", "read ", "reading "):
                        if clean_arg.lower().startswith(prefix):
                            clean_arg = clean_arg[len(prefix):].strip()
                            break
                elif tl in ("edit", "write"):
                    for prefix in (f"{tl} file ", f"{tl}ing file ", f"{tl} "):
                        if clean_arg.lower().startswith(prefix):
                            clean_arg = clean_arg[len(prefix):].strip()
                            break
                elif clean_arg.lower().startswith(f"{tl} "):
                    clean_arg = clean_arg[len(tl) + 1:].strip()
                self.subagent_live_tool = f"{tool} {clean_arg}".strip() if clean_arg else tool
            if tool == "agent_note":
                note = str(getattr(progress, "note", "") or "")
                if note and note not in self.subagent_notes:
                    self.subagent_notes.append(note)
            hist = getattr(progress, "history", None) or []
            self.subagent_history = list(hist)
            self.subagent_events = list(getattr(progress, "events", None) or [])
            finds = getattr(progress, "findings", None) or []
            self.subagent_findings = list(finds)
            status = str(getattr(progress, "status", "") or "")
            if status:
                self.subagent_status = status
            self.subagent_tool_count = sum(
                1 for m in self.subagent_history
                if m.get("role") == "user" and str(m.get("content", "")).startswith("[tool:")
            )
            self.update_header()
        except Exception:
            pass



    def finish_subagent(self, agent: Dict[str, Any]) -> None:
        """Close out one subagent marker from its SubagentResult payload."""
        try:
            self.tool_running = False
            self._stop_spinner()
            self.result_data = agent
            self.subagent_status = str(agent.get("status", "completed"))
            self.subagent_goal = str(agent.get("goal", "") or self.subagent_goal)
            self.subagent_system = str(agent.get("system_prompt", "") or self.subagent_system)
            hist = agent.get("history") or []
            if hist:
                self.subagent_history = list(hist)
            self.subagent_findings = list(agent.get("findings") or [])
            self.subagent_notes = list(agent.get("notes") or [])
            self.subagent_tool_count = int(agent.get("tool_count", 0) or 0)
            self.subagent_live_tool = ""
            self.subagent_events = list(agent.get("events") or [])
            self.has_error = str(agent.get("status", "")) == "failed"

            summary = str(agent.get("summary", "")).strip()
            if self.has_error:
                # Keep the reason visible; render_tool_header adds "[failed]".
                first = (str(agent.get("error", "") or summary).splitlines() or ["error"])[0]
                self.target = first.strip()[:70] or "error"
            else:
                self.target = "complete"
            self.subagent_tokens = int(agent.get("tokens", 0) or 0)
            # The orchestrator measures the real runtime; fall back to the local
            # clock only when it did not report one.
            reported = float(agent.get("elapsed", 0.0) or 0.0)
            if reported > 0:
                self.subagent_elapsed = reported
            else:
                started = getattr(self, "subagent_started", None)
                if started:
                    import time as _time
                    self.subagent_elapsed = max(0.0, _time.monotonic() - started)

            # No details body: a subagent marker never expands, so the chat
            # window is the only place the report, findings and notes appear.
            self._safe_update_details(Text(""))
            self.update_header()
            self.refresh(layout=True)
        except Exception:
            pass

    def _safe_update_details(self, content: Any) -> None:
        try:
            from rich.syntax import Syntax
            if isinstance(content, Text):
                self._original_details = content
                self.details_text = content.plain
            elif isinstance(content, Syntax):
                self._original_details = content
                self.details_text = getattr(content, "code", "")
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
        lines = (self.details_text or "").splitlines()
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
            from rich.syntax import Syntax
            if isinstance(self._original_details, Syntax) or self.h_offset <= 0:
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
        elapsed = time.time() - self.start_time
        self.elapsed_text = f"{elapsed:.1f}s"
        if self.tool_name.lower() in ("execute", "eval", "evaluate") or str(self.target).strip().startswith('{"code":'):
            import re
            raw_t = str(self.target)
            url_m = re.search(r"https?://[^\s'\"`\\]+", raw_t)
            if ("fetch" in raw_t.lower() or "http" in raw_t.lower()) and url_m:
                self.tool_name = "webfetch"
                self.target = url_m.group(0)
            elif any(k in raw_t.lower() for k in ("child_process", "exec(", "spawn(", "execsync", "bash", "cmd")):
                self.tool_name = "terminal"
                cmd_m = re.search(r"(?:exec|spawn|execSync)\(\s*['\"]([^'\"]+)['\"]", raw_t)
                self.target = cmd_m.group(1) if cmd_m else "command"
            else:
                self.tool_name = "script"
                self.target = ""
                self.has_error = True

        is_err = bool(
            result.get("error")
            or result.get("success") is False
            or (self.tool_name in ("command", "terminal") and result.get("exit_code", 0) != 0 and not result.get("success", True))
            or (getattr(self, "has_error", False) and result.get("success") is not True and self.tool_name not in ("terminal", "command", "webfetch"))
            or self.tool_name in ("script", "execute")
        )
        self.has_error = is_err
        self.update_header()

        if self.tool_name == "agent_note" and not is_err:
            # A note to the lead agent. Rendered exactly like any other tool:
            # one collapsed line, click to read, no special colour.
            note = str(result.get("note", "") or result.get("content", "")).strip()
            if note:
                self.target = note.splitlines()[0][:70]
            self.update_header()
            txt = Text(no_wrap=True)
            if note:
                for line in note.splitlines():
                    txt.append(f"  {line}\n", style="#e6edf3")
            else:
                txt.append("  (empty note)\n", style="dim #8b949e")
            self._safe_update_details(txt)
            return

        if is_err:
            err_msg = str(result.get("error") or result.get("stderr") or "Tool execution failed.")
            txt = Text(no_wrap=False)
            txt.append("  [!!] Execution Failed:\n", style="bold #f85149")
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

        if self.tool_name in ("edit", "write"):
            import os
            if not self.target or self.target.startswith("(in "):
                p = result.get("path") or result.get("file") or result.get("target") or result.get("dest")
                if p:
                    self.target = str(p)
            clean_tgt = self.target.split(" (")[0].strip()
            content = result.get("content") or result.get("new_content") or result.get("code") or result.get("file_content")
            if not content and clean_tgt:
                try:
                    if os.path.isfile(clean_tgt):
                        with open(clean_tgt, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                except Exception:
                    pass
            if content is not None:
                lines = content.splitlines()
                if "(" not in self.target and self.target:
                    self.target = f"{self.target} ({len(lines)} lines)"
                    self.update_header()
                lexer = get_lexer_for_path(clean_tgt)
                try:
                    from rich.syntax import Syntax
                    syntax = Syntax(content, lexer, theme="github-dark", line_numbers=True, word_wrap=False)
                    self._safe_update_details(syntax)
                except Exception:
                    txt = Text(no_wrap=True)
                    for idx, line in enumerate(lines, 1):
                        num_str = f"  {idx:>4}    "
                        clean_l = str(line).replace("\t", "    ")
                        txt.append(num_str, style="#484f58")
                        txt.append(f"{clean_l}\n", style="#e6edf3")
                    self._safe_update_details(txt)
            else:
                diff_entries = result.get("diff_entries", [])
                if diff_entries:
                    self._safe_update_details(render_edit_diff_opencode(diff_entries, target_file=self.target))
                else:
                    self._safe_update_details(Text(f"  {self.target}\n  (Code applied successfully)\n", style="#3fb950"))
        elif self.tool_name.lower() in ("command", "shell", "bash", "terminal", "sh", "cmd", "run_command", "exec"):
            stdout = result.get("stdout") or result.get("output") or result.get("result") or result.get("content") or ""
            stderr = result.get("stderr") or ""
            self._safe_update_details(render_command_output(str(stdout), str(stderr), cmd=self.target))
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
        elif self.tool_name.lower() in ("web", "fetch", "search_web", "webfetch"):
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
            elif "content" in result or "result" in result:
                raw_c = str(result.get("content") or result.get("result") or "")
                c_bytes = len(raw_c.encode("utf-8", errors="replace"))
                c_words = len(raw_c.split())
                c_kb = c_bytes / 1024.0
                status_code = result.get("status_code", 200)
                txt.append("  HTTP ", style="dim #8b949e")
                txt.append(f"{status_code} OK", style="bold #7ee787" if status_code == 200 else "bold #f85149")
                txt.append(f" · {c_kb:.1f} KB ({c_words} words)\n", style="#8b949e")
                title = str(result.get("title") or "").strip()
                if title:
                    txt.append(f"  {title}\n", style="bold #e6edf3")
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
        elif self.tool_name in ("vision", "mcp", "mcps"):
            txt = Text(no_wrap=True)
            if self.tool_name == "vision" and result.get("path"):
                txt.append(f"  {result['path']}\n", style="#e6edf3")
                if result.get("question"):
                    txt.append(f"  question: {result['question']}\n", style="#8b949e")
            else:
                servers = result.get("servers") or []
                opened = result.get("tools") or result.get("schema_text")
                if opened:
                    # `<tool:mcp open server="X" />` - the step that actually
                    # yields something to look at. It carries no `servers` key,
                    # and reading that absence as "no servers configured" said
                    # the opposite of what the call had just done.
                    names = result.get("tools") or []
                    label = "tool" if len(names) == 1 else "tools"
                    txt.append(f"  server: {self.target or result.get('server', '?')}\n",
                               style="bold #e6edf3")
                    txt.append(f"  {len(names)} {label} available\n", style="#8b949e")
                    for n in names:
                        txt.append(f"    {n}\n", style="#e6edf3")
                    if result.get("error"):
                        txt.append(f"  {result['error']}\n", style="#f85149")
                elif not servers:
                    # An empty list used to render nothing at all, so a broken
                    # MCP setup looked identical to a tool that had not run.
                    reason = result.get("note") or result.get("error") or "no servers configured"
                    txt.append(f"  {reason}\n", style="#f85149")
                else:
                    for s in servers:
                        if not isinstance(s, dict):
                            txt.append(f"  {s}\n", style="#8b949e")
                            continue
                        name = str(s.get("name", "?"))
                        # Connected servers are the useful case, so they read as
                        # normal content. Anything else is a dead end for the
                        # model and has to be impossible to miss.
                        if s.get("connected"):
                            count = int(s.get("tool_count", 0) or 0)
                            label = f"{count} tool" if count == 1 else f"{count} tools"
                            txt.append("  [ok] ", style="#3fb950")
                            txt.append(f"{name}", style="bold #e6edf3")
                            txt.append(f"  connected, {label}\n", style="#8b949e")
                        elif not s.get("enabled"):
                            txt.append("  [--] ", style="#f85149")
                            txt.append(f"{name}", style="bold #f85149")
                            txt.append("  not connected (switched off in /mcp)\n",
                                       style="#f85149")
                        else:
                            # ASCII only: the Windows console defaults to
                            # cp1250 and raises UnicodeEncodeError on a tick or
                            # a cross before anything renders.
                            txt.append("  [!!] ", style="#f85149")
                            txt.append(f"{name}", style="bold #f85149")
                            status = str(s.get("status") or "error")
                            txt.append(f"  not connected ({status})\n", style="#f85149")
                            why = str(s.get("reason") or "").strip()
                            if why:
                                txt.append(f"      {why}\n", style="dim #f85149")
                if result.get("note") and servers:
                    txt.append(f"  {result['note']}\n", style="dim #484f58")
            self._safe_update_details(txt)
        elif "content" not in result and ("text" in result or self.tool_name.startswith("mcp__")):
            # A qualified MCP tool call returns `text` (see `_execute_mcp_tool`),
            # not `content`. Only the `content` branches existed, so every real
            # MCP tool call rendered as an empty card - the same blank-body
            # symptom the listing had.
            lines = str(result.get("text", "")).splitlines() or [""]
            txt = Text(no_wrap=True)
            for l in lines[:200]:
                clean_l = str(l).replace("\t", "    ")
                txt.append(f"    {clean_l}\n", style="#e6edf3")
            if len(lines) > 200:
                txt.append(f"    ... {len(lines) - 200} more lines\n", style="dim #8b949e")
            self._safe_update_details(txt)
        elif "content" in result or "result" in result or "output" in result or "stdout" in result:
            val = result.get("content") or result.get("result") or result.get("output") or result.get("stdout") or ""
            lines = str(val).splitlines()
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

        if self.is_tool_expandable():
            self.set_expanded(False)

        try:
            self.refresh(layout=True)
            if self.app:
                self.app.refresh()
        except Exception:
            pass

    def set_expanded(self, expanded: bool) -> None:
        if not self.is_tool_expandable():
            return
        self.is_expanded = expanded
        if not expanded:
            self.h_offset = 0
            if self._original_details:
                self._apply_horizontal_shift()
        self.update_header()
        # Expanding something with no details produced a visible empty box, which
        # reads as a broken click rather than as "nothing here yet". A block
        show = expanded and (bool(self._original_details) or bool(self.tool_running))
        self.details_widget.styles.display = "block" if show else "none"
        if show and self._original_details:
            self._apply_horizontal_shift()

    def on_click(self, event: events.Click) -> None:
        if self.subagent_role:
            # A subagent click opens the subagent chat, even while it is still
            # running, so the user can watch it work. It never expands.
            opener = getattr(self.app, "_open_subagent_chat", None)
            if callable(opener):
                try:
                    opener(self)
                except Exception:
                    pass
            return
        if not self.is_tool_expandable():
            return
        if self.tool_running:
            # Expanding while the tool is still running used to be refused. That
            # is the moment the arguments matter most - a long command is
            # exactly what you want to read before it has finished, not after.
            # The details are already there; only the spinner is still going.
            self.set_expanded(not self.is_expanded)
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
        if not self._pasted_chunks:
            return strip
        from rich.segment import Segment
        from rich.style import Style
        from textual.strip import Strip

        badge_pattern = re.compile(r"(\[pasted \d+ lines(?: #\d+)?\])")
        highlight_style = Style(color="#ffffff", bgcolor="#0969da", bold=True)
        new_segments = []
        modified = False

        for seg in strip._segments:
            if seg.text and "[pasted " in seg.text:
                parts = badge_pattern.split(seg.text)
                for part in parts:
                    if not part:
                        continue
                    if badge_pattern.match(part):
                        new_segments.append(Segment(part, highlight_style))
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
        from .modals.help import COMMANDS_DOC
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
