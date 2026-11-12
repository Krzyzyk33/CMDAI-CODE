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


def is_api_error_message(text: str) -> bool:
    t = str(text or "").strip()
    tl = t.lower()
    if tl.startswith(("opencode bridge:", "api error", "error:", "model error:", "provider error:", "**error", "**model connection error", "execution error", "connection error")):
        return True
    if any(k in tl for k in (
        "free usage limit reached",
        "upstream model is overloaded",
        "rate limit reached",
        "quota exceeded",
        "resource exhausted",
        "[http 401]",
        "[http 403]",
        "[http 429]",
        "[http 500]",
        "[http 502]",
        "[http 503]",
        "[http 504]",
    )):
        return True
    return False


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
