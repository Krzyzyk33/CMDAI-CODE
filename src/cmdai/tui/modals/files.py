import os
from typing import List, Optional
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class FilePickerModal(ModalScreen[Optional[str]]):

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("enter", "select_file", "Select"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.all_files: List[str] = []
        self.filtered_files: List[str] = []
        self._scan_workspace_files()

    def _scan_workspace_files(self) -> None:
        ignore_dirs = {".git", ".venv", "__pycache__", "node_modules", ".idea", ".vscode", "dist", "build"}
        files_found = []
        for root, dirs, files in os.walk(self.workdir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
            for f in sorted(files):
                if f.startswith(".") or f.endswith((".pyc", ".gguf", ".bin", ".lock")):
                    continue
                full_p = os.path.join(root, f)
                rel_p = os.path.relpath(full_p, self.workdir).replace("\\", "/")
                files_found.append(rel_p)
                if len(files_found) >= 500:
                    break
            if len(files_found) >= 500:
                break
        self.all_files = sorted(files_found)
        self.filtered_files = list(self.all_files)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("[bold white]Workspace Files (Select to add to context)[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            yield Input(placeholder="Search files (e.g. app.py, models/)...", id="file-search-input")
            yield OptionList(id="file-picker-list")

            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Select[/] enter   [dim]Cancel[/] esc   [dim]↑/↓: navigate[/]", id="modal-footer-left")
                cnt = len(self.filtered_files)
                yield Static(f"[dim]{cnt} file{'s' if cnt != 1 else ''}[/]", id="modal-footer-right")

    def on_mount(self) -> None:
        self._refresh_list()
        self.query_one("#file-search-input", Input).focus()

    def _refresh_list(self) -> None:
        ol = self.query_one("#file-picker-list", OptionList)
        ol.clear_options()
        if not self.filtered_files:
            ol.add_option(Option("  [dim](no matching files)[/dim]"))
            return
        for idx, path in enumerate(self.filtered_files[:100]):
            ol.add_option(Option(f"  [b #58a6ff]•[/]  [white]{path}[/]", id=f"file_{idx}"))
        if self.filtered_files:
            ol.highlighted = 0

    @on(Input.Changed, "#file-search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        q = event.value.strip().lower()
        if q:
            self.filtered_files = [f for f in self.all_files if q in f.lower()]
        else:
            self.filtered_files = list(self.all_files)
        self._refresh_list()

    @on(Input.Submitted, "#file-search-input")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        self.action_select_file()

    @on(OptionList.OptionSelected, "#file-picker-list")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_select_file()

    def action_cursor_up(self) -> None:
        self.query_one("#file-picker-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#file-picker-list", OptionList).action_cursor_down()

    def action_select_file(self) -> None:
        ol = self.query_one("#file-picker-list", OptionList)
        idx = ol.highlighted
        if idx is not None and 0 <= idx < len(self.filtered_files):
            self.dismiss(self.filtered_files[idx])
        else:
            self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
