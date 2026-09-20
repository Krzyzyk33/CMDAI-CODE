import os
import pathlib
from typing import Dict, List, Optional, Set
from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Static
from textual.widgets.tree import TreeNode

from ...core.capabilities import BUDGET_MAP, get_model_capability
from ...core.settings import get_settings


class ContextDirectoryTree(DirectoryTree):

    BINDINGS = [
        Binding("space", "toggle_pin", "Toggle Pin", priority=True),
    ]

    def __init__(self, path: pathlib.Path, pinned: Optional[Set[str]] = None, **kwargs):
        super().__init__(path, **kwargs)
        self.pinned: Set[str] = set(pinned or [])
        self.workdir_path = path.resolve()

    def filter_paths(self, paths: list[pathlib.Path]) -> list[pathlib.Path]:
        ignore_dirs = {
            ".git", ".venv", "venv", "__pycache__", "node_modules",
            ".idea", ".vscode", "dist", "build", ".pytest_cache", ".ruff_cache"
        }
        ignore_exts = {".pyc", ".gguf", ".bin", ".lock", ".sqlite", ".db", ".exe", ".dll"}
        result = []
        for p in paths:
            if p.name in ignore_dirs:
                continue
            if p.name.startswith(".") and not p.is_dir():
                continue
            if p.suffix.lower() in ignore_exts:
                continue
            result.append(p)
        return sorted(result, key=lambda p: (not p.is_dir(), p.name.lower()))

    def render_label(self, node: TreeNode, base_style: Style, style: Style) -> Text:
        text = super().render_label(node, base_style, style)
        if node.data and hasattr(node.data, "path") and node.data.path.is_file():
            try:
                rel = os.path.relpath(str(node.data.path), str(self.workdir_path)).replace("\\", "/")
                if rel in self.pinned:
                    prefix = Text("✓ ", style=Style(color="#3fb950", bold=True))
                    text.stylize(Style(color="#ffffff", bold=True))
                    return Text.assemble(prefix, text)
            except Exception:
                pass
        return text

    def action_toggle_pin(self) -> None:
        if self.cursor_node and self.cursor_node.data and hasattr(self.cursor_node.data, "path"):
            if self.cursor_node.data.path.is_file():
                if hasattr(self.screen, "_toggle_file_pin"):
                    self.screen._toggle_file_pin(self.cursor_node.data.path)


class ContextInspectorModal(ModalScreen[Optional[Dict[str, str]]]):

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("space", "toggle_pin", "Toggle Pin", show=False),
    ]

    def __init__(
        self,
        model_id: str = "",
        system_prompt: str = "",
        messages: list = None,
        pinned_files: Dict[str, str] = None,
        max_context: int = 8192,
        provider: str = "",
        workdir: str = ".",
        **kwargs
    ):
        super().__init__(**kwargs)
        self.model_id = model_id
        self.provider = provider
        self.system_prompt = system_prompt or ""
        self.messages = messages or []
        self.pinned_files = dict(pinned_files or {})
        self.max_context = max_context
        self.workdir = os.path.abspath(workdir)
        self.settings = get_settings()

    def _estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def _render_info_content(self) -> str:
        sys_tokens = self._estimate_tokens(self.system_prompt)
        pinned_tokens = sum(self._estimate_tokens(content) for content in self.pinned_files.values())
        msg_tokens = sum(self._estimate_tokens(str(m.get("content", ""))) for m in self.messages)
        total_used = sys_tokens + pinned_tokens + msg_tokens
        pct = min(100, int((total_used / max(1, self.max_context)) * 100))

        bar_len = 26
        fill_len = int(bar_len * (pct / 100.0))
        empty_len = bar_len - fill_len
        bar = f"[#58a6ff]{'█' * fill_len}[/][#30363d]{'░' * empty_len}[/]"

        cap = get_model_capability(self.model_id, provider_id=self.provider)
        gen = self.settings.config.get("generation", {})
        active_reasoning = gen.get("reasoning_level", "Medium")

        lines = [
            f"[dim]Model:[/]  [bold white]{self.model_id}[/] [dim]({self.provider or 'active'})[/dim]",
            f"[dim]Budget:[/] [bold #58a6ff]{total_used:,}[/] [dim]/ {self.max_context:,} tokens ({pct}%)[/dim]",
            f"{bar}\n",
            "[bold white]Token Breakdown[/]",
            f"  [b #58a6ff]•[/] System Prompt:    {sys_tokens:>6,} tokens",
            f"  [b #58a6ff]•[/] Pinned Files ({len(self.pinned_files)}): {pinned_tokens:>6,} tokens",
            f"  [b #58a6ff]•[/] Chat Turns ({len(self.messages)}):   {msg_tokens:>6,} tokens",
            f"  [b #58a6ff]•[/] Remaining:        {max(0, self.max_context - total_used):>6,} tokens\n",
            "[bold white]Thinking & Reasoning[/]",
        ]

        if cap and cap.thinking:
            budget = BUDGET_MAP.get(active_reasoning, 8192)
            lines.extend([
                f"  [b #3fb950]•[/] Status:          [b #3fb950]Supported[/]",
                f"  [b #58a6ff]•[/] Levels:          {', '.join(cap.levels)}",
                f"  [b #58a6ff]•[/] Active Level:    [b white]{active_reasoning}[/]",
                f"  [b #58a6ff]•[/] Thinking Budget: [b #bc8cff]{budget:,} tokens[/]",
            ])
        else:
            lines.append("  [dim]• Status:          Not supported for this model[/dim]")

        lines.append(f"\n[bold white]Pinned Files ({len(self.pinned_files)})[/]")
        if not self.pinned_files:
            lines.append("  [dim](Select files on the right to pin)[/dim]")
        else:
            for path, content in sorted(self.pinned_files.items()):
                c_tok = self._estimate_tokens(content)
                c_lines = len(content.splitlines())
                lines.append(f"  [b #3fb950]✓[/] [b white]{path}[/] [dim]({c_lines}L · {c_tok}t)[/dim]")

        return "\n".join(lines)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="context-modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("[bold white]Context Window Inspector & Workspace Picker[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            with Horizontal(id="context-split-container"):
                with VerticalScroll(id="context-info-sidebar"):
                    yield Static(self._render_info_content(), id="context-info-content")

                with Vertical(id="context-tree-container"):
                    yield Static("[bold white]Workspace Files[/] [dim]— Space/Enter to Pin or Unpin[/dim]", id="context-tree-header")
                    yield ContextDirectoryTree(
                        pathlib.Path(self.workdir),
                        pinned=set(self.pinned_files.keys()),
                        id="context-tree",
                    )

            with Horizontal(id="modal-footer"):
                yield Static(
                    "[b #58a6ff]Space / Enter[/] Pin/Unpin file   [dim]Esc: Save & Close[/dim]",
                    id="modal-footer-left",
                )
                sys_tokens = self._estimate_tokens(self.system_prompt)
                pinned_tokens = sum(self._estimate_tokens(content) for content in self.pinned_files.values())
                msg_tokens = sum(self._estimate_tokens(str(m.get("content", ""))) for m in self.messages)
                total_used = sys_tokens + pinned_tokens + msg_tokens
                pct = min(100, int((total_used / max(1, self.max_context)) * 100))
                yield Static(f"[dim]{total_used:,} / {self.max_context:,} tokens ({pct}%)[/dim]", id="modal-footer-right")

    def _update_display(self) -> None:
        try:
            content = self._render_info_content()
            self.query_one("#context-info-content", Static).update(content)

            sys_tokens = self._estimate_tokens(self.system_prompt)
            pinned_tokens = sum(self._estimate_tokens(c) for c in self.pinned_files.values())
            msg_tokens = sum(self._estimate_tokens(str(m.get("content", ""))) for m in self.messages)
            total_used = sys_tokens + pinned_tokens + msg_tokens
            pct = min(100, int((total_used / max(1, self.max_context)) * 100))

            self.query_one("#modal-footer-right", Static).update(
                f"[dim]{total_used:,} / {self.max_context:,} tokens ({pct}%)[/dim]"
            )
        except Exception:
            pass

    def _toggle_file_pin(self, file_path: pathlib.Path) -> None:
        try:
            rel = os.path.relpath(str(file_path), self.workdir).replace("\\", "/")
            if rel in self.pinned_files:
                del self.pinned_files[rel]
            else:
                content = ""
                try:
                    with open(str(file_path), "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(150_000)
                except Exception:
                    content = ""
                self.pinned_files[rel] = content

            tree = self.query_one(ContextDirectoryTree)
            tree.pinned = set(self.pinned_files.keys())
            tree.refresh()
            self._update_display()
        except Exception:
            pass

    @on(DirectoryTree.FileSelected)
    def on_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        event.stop()
        if event.path.is_file():
            self._toggle_file_pin(event.path)

    def action_toggle_pin(self) -> None:
        try:
            tree = self.query_one(ContextDirectoryTree)
            if tree.cursor_node and tree.cursor_node.data and hasattr(tree.cursor_node.data, "path"):
                if tree.cursor_node.data.path.is_file():
                    self._toggle_file_pin(tree.cursor_node.data.path)
        except Exception:
            pass

    def action_dismiss_modal(self) -> None:
        self.dismiss(self.pinned_files)

