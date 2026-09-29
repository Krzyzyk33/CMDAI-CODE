import asyncio
import os
import sys
from typing import Dict, List, Optional
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import TextArea
from rich.text import Text

def _contains(region, point) -> bool:

    return (region.x <= point.x <= region.x + region.width
            and region.y <= point.y <= region.y + region.height)

def _first(root, selector: str):

    return next(iter(root.query(selector)), None)

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
src_dir = os.path.join(project_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from ..core.buffer import BufferManager
from ..core.diagnostics import linter, scan_project
from ..core.runner import RunProcess, run_command_for
from .top_bar import EditorTopBar, BrandMenuLabel, ToolIcon
from .tabs import EditorTabsBar
from .explorer import EditorExplorer, IconDirectoryTree
from .code_area import DiagnosticTextArea
from .status_bar import EditorStatusBar
from .dropdown_menu import InAppDropdownMenu
from .prompt_modal import PromptOverlay
from .terminal import EditorBottomTerminal
from .panel import BottomPanel, TAB_TERMINAL, TAB_PROBLEMS
from .problems import ProblemsPane
from .welcome import EmptyEditorWelcome

try:
    from cmdai.core.checkpoints import CheckpointManager
    from cmdai.tui.modals.git_modal import GitBranchesModal, GIT_MODAL_CSS
    from cmdai.tui.modals.checkpoints import CheckpointsModal, CHECKPOINTS_CSS

    class FullscreenGitModal(GitBranchesModal):
        CSS = GIT_MODAL_CSS + """GitBranchesModal {
            align: center middle;
            background: rgba(0, 0, 0, 0.70);
            color: #e6edf3;
        }

        #git-scroll {
            scrollbar-gutter: stable;
            min-height: 3;
        }
"""

    class FullscreenCheckpointsModal(CheckpointsModal):
        CSS = CHECKPOINTS_CSS + """CheckpointsModal {
            align: center middle;
            background: rgba(0, 0, 0, 0.70);
            color: #e6edf3;
        }
        #checkpoints-scroll {
            scrollbar-gutter: stable;
        }
        #checkpoints-info-hint {
            height: auto;
        }
        #btn-create-checkpoint {
            height: 3;
            min-width: 18;
            margin-left: 2;
            border: round #238636;
            background: #238636;
            color: #ffffff;
        }
        #btn-create-checkpoint:hover {
            background: #2ea043;
        }
        #checkpoint-desc-input {
            height: 3;
            border: round #30363d;
            background: #0d1117;
        }

        #diff-back-btn,
        #diff-revert-btn {
            height: 1;
            min-width: 0;
            border: none;
            background: transparent;
            padding: 0 1;
        }
"""

    GitBranchesModal = FullscreenGitModal
    CheckpointsModal = FullscreenCheckpointsModal
except Exception:
    GitBranchesModal = None
    CheckpointsModal = None
    CheckpointManager = None

EDITOR_CSS = """Screen {
    background: #090d13;
    color: #c9d1d9;
    layers: base overlay;
    margin: 0;
    padding: 0;
}
#editor-main {
    width: 100%;
    height: 1fr;
    margin: 0;
    padding: 0;
}
#editor-pane {
    width: 1fr;
    height: 100%;
    background: #090d13;
}

TextArea {
    width: 100%;
    height: 1fr;
    background: #090d13;
    border: none;
    padding: 0 0 0 1;
    overflow-x: auto;
    overflow-y: auto;
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 1;
    scrollbar-color: #484f58;
    scrollbar-background: #090d13;
    scrollbar-corner-color: #090d13;
}
TextArea:focus {
    border: none;
}
TextArea.hidden {
    display: none;
}
"""

class CMDAIEditorApp(App[None]):

    CSS = EDITOR_CSS

    BINDINGS = [
        Binding("ctrl+s", "save_file", "Save"),
        Binding("ctrl+shift+s", "save_as_dialog", "Save As"),
        Binding("ctrl+n", "new_file_dialog", "New File"),

        Binding("ctrl+w", "close_tab", "Close Tab", priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Explorer"),
        Binding("ctrl+l", "toggle_line_numbers", "Line Numbers"),
        Binding("ctrl+grave", "open_terminal", "Terminal"),

        Binding("ctrl+f5", "run_file", "Run File"),
        Binding("ctrl+shift+f5", "stop_run", "Stop Run"),
        Binding("ctrl+g", "open_git", "Git Branches"),
        Binding("ctrl+h", "open_checkpoints", "Checkpoints"),
        Binding("ctrl+q", "quit_editor", "Exit"),
        Binding("escape", "handle_escape", "Back/Unfocus"),
    ]

    def __init__(self, initial_path: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.target_path = os.path.abspath(initial_path)
        if os.path.isfile(self.target_path):
            self.root_dir = os.path.dirname(self.target_path)
            self.initial_file = self.target_path
        else:
            self.root_dir = self.target_path
            self.initial_file = None

        self.buffer_manager = BufferManager()
        self.show_line_nums = True

        self._lint_timer = None
        self.linting_enabled = True
        self._last_diags: list = []

        self._project_diags: list = []
        self._scan_task: Optional[asyncio.Task] = None
        self._scan_note: str = ""
        self._applied_diags: list = []
        self._applied_note: str = ""

        self._counts = (0, 0)

        self._runner: Optional[RunProcess] = None

        self._pending_spec = None

    def compose(self) -> ComposeResult:
        yield EditorTopBar(id="editor-top-bar")
        yield EditorTabsBar(id="editor-tabs")
        with Horizontal(id="editor-main"):
            yield EditorExplorer(self.root_dir, id="editor-explorer")
            with Vertical(id="editor-pane"):

                yield DiagnosticTextArea("", id="code-area",
                                         show_line_numbers=True,
                                         soft_wrap=False)
                yield EmptyEditorWelcome(id="empty-welcome", classes="hidden")

                yield BottomPanel(self.root_dir, id="bottom-panel",
                                  classes="hidden")
        yield EditorStatusBar(self.root_dir, id="status-bar")
        yield InAppDropdownMenu(id="inapp-dropdown")

        yield PromptOverlay(id="prompt-overlay")

    def on_mount(self) -> None:
        self.title = "CMDAI CODE Editor"
        ta = self.query_one("#code-area", TextArea)
        ta.theme = "vscode_dark"
        ta.show_line_numbers = self.show_line_nums

        if self.initial_file and os.path.exists(self.initial_file):
            self.open_file_path(self.initial_file)
        else:
            cand = os.path.join(self.root_dir, "main.py")
            if os.path.isfile(cand):
                self.open_file_path(cand)
            else:
                self._show_empty_state()
                self._refresh_tabs()
                self._refresh_status()

    def open_file_path(self, filepath: str) -> None:
        buf = self.buffer_manager.open_file(filepath)
        ta = self.query_one("#code-area", TextArea)
        welcome = self.query_one("#empty-welcome", EmptyEditorWelcome)
        welcome.add_class("hidden")
        ta.remove_class("hidden")

        ta.text = buf.content
        ta.language = buf.language
        ta.focus()

        self._refresh_tabs()
        self._refresh_status()

        self.call_after_refresh(self._after_file_opened)

    def _after_file_opened(self) -> None:

        self._refresh_run_button()
        self._refresh_diagnostics_now(full=True)

    def close_file_path(self, filepath: str) -> None:
        next_path = self.buffer_manager.close_buffer(filepath)
        if next_path:
            self.open_file_path(next_path)
        else:
            self._show_empty_state()
            self._refresh_tabs()
            self._refresh_status()

    def _show_empty_state(self) -> None:
        ta = self.query_one("#code-area", TextArea)
        ta.add_class("hidden")
        welcome = self.query_one("#empty-welcome", EmptyEditorWelcome)
        welcome.remove_class("hidden")

    def _refresh_tabs(self) -> None:
        tabs = self.query_one("#editor-tabs", EditorTabsBar)
        buf_list = self.buffer_manager.list_open_buffers()
        active_buf = self.buffer_manager.active_buffer
        active_path = active_buf.filepath if active_buf else ""
        tabs.update_tabs(buf_list, active_path)

    def _refresh_status(self, diagnostics=None) -> None:
        sb = self.query_one("#status-bar", EditorStatusBar)
        ta = self.query_one("#code-area", TextArea)
        buf = self.buffer_manager.active_buffer

        active_path = buf.filepath if buf else ""
        is_mod = buf.is_modified if buf else False
        lang = buf.language or "text" if buf else "text"

        if diagnostics is not None:
            errors, warnings = diagnostics
            self._counts = (errors, warnings)
        else:

            errors, warnings = self._counts

        sb.update_file(active_path, is_modified=is_mod)
        cursor_row, cursor_col = ta.cursor_location
        sb.update_cursor(cursor_row + 1, cursor_col + 1,
                         len(ta.text.splitlines()) or 1, lang=lang,
                         errors=errors, warnings=warnings)

    def _auto_create_checkpoint(self, prompt: str) -> None:
        if CheckpointManager:
            try:
                cm = CheckpointManager(self.root_dir)
                cm.create_checkpoint(prompt)
            except Exception:
                pass

    @on(EditorExplorer.Action)
    def on_explorer_action(self, message: EditorExplorer.Action) -> None:
        mapping = {
            "explorer_new_file": self.action_explorer_new_file,
            "explorer_refresh": self.action_explorer_refresh,
        }
        handler = mapping.get(message.action_id)
        if handler:
            handler()

    def _new_item_base_dir(self) -> str:

        try:
            tree = self.query_one("#explorer-tree", IconDirectoryTree)
            if tree.selected_dir is not None:
                return str(tree.selected_dir)
        except Exception:
            pass
        return self.root_dir

    def action_explorer_new_file(self) -> None:

        base = self._new_item_base_dir()

        def on_submit(name: str):
            name = name.strip().strip('"')
            if not name:
                return
            target = os.path.join(base, name)
            if os.path.exists(target):
                self.notify(f"Already exists: {name}", severity="warning")
                return

            wants_dir = name.endswith(("/", "\\"))
            try:
                if wants_dir:
                    os.makedirs(target, exist_ok=True)

                    clean_name = name.rstrip("/").rstrip("\\")
                    self.notify(f"Created folder: {clean_name}")
                else:
                    parent = os.path.dirname(target)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(target, "w", encoding="utf-8"):
                        pass
                    self.notify(f"Created file: {name}")
            except OSError as exc:
                self.notify(f"Cannot create: {exc}", severity="error")
                return

            self._reload_explorer()
            if not wants_dir:
                self.open_file_path(target)

        self.show_prompt(
            "New file or folder",
            on_submit,
            placeholder="app.py   |   src/  (folder)",
            target=base,
        )

    def action_explorer_refresh(self) -> None:
        self._reload_explorer()
        self.notify("Explorer refreshed", timeout=1.5)

    def _reload_explorer(self) -> None:
        tree = self.query_one("#explorer-tree", IconDirectoryTree)

        tree.reload()

    @on(IconDirectoryTree.FileSelected, "#explorer-tree")
    def on_tree_file_selected(self, event: IconDirectoryTree.FileSelected) -> None:
        p = str(event.path)
        if os.path.isfile(p):
            self.open_file_path(p)

    @on(EditorTabsBar.TabSelected)
    def on_tab_selected(self, message: EditorTabsBar.TabSelected) -> None:
        self.open_file_path(message.filepath)

        self.call_after_refresh(self._refresh_run_button)

    @on(EditorTabsBar.TabClosed)
    def on_tab_closed(self, message: EditorTabsBar.TabClosed) -> None:
        self.close_file_path(message.filepath)

    @on(EmptyEditorWelcome.ActionTriggered)
    def on_welcome_action_triggered(self, message: EmptyEditorWelcome.ActionTriggered) -> None:
        self.handle_menu_action(message.action_id)

    @on(TextArea.Changed, "#code-area")
    def on_textarea_changed(self, event: TextArea.Changed) -> None:
        buf = self.buffer_manager.active_buffer
        if buf:
            prev_mod = buf.is_modified
            buf.update_content(event.text_area.text)
            if buf.is_modified != prev_mod:
                self._refresh_tabs()
            self._refresh_status()
        self._schedule_diagnostics()

    def _schedule_diagnostics(self) -> None:
        if not getattr(self, "linting_enabled", True):
            return
        if self._lint_timer is not None:
            self._lint_timer.stop()
        self._lint_timer = self.set_timer(0.4, self._run_fast_lint)

    def _run_fast_lint(self) -> None:
        self._lint_timer = None
        buf = self.buffer_manager.active_buffer
        if buf is None:
            return
        self._apply_diagnostics(linter.check(buf.filepath, buf.content),
                                note=linter.note_for(buf.filepath))

    def _run_full_lint(self) -> None:

        buf = self.buffer_manager.active_buffer
        if buf is None:
            return
        self._apply_diagnostics(linter.check_full(buf.filepath, buf.content),
                                note=linter.note_for(buf.filepath))

    def _apply_diagnostics(self, diags, note: str = "") -> None:

        self._last_diags = list(diags)

        self._applied_diags = list(diags)
        self._applied_note = note
        merged = self._merge_project(list(diags))
        try:
            pane = self.query_one(ProblemsPane)
        except Exception:
            return

        pane.set_engine_note(f"{note} {linter.engine_note()}".strip())
        pane.set_scan_note(self._scan_note)
        pane.set_problems(merged)
        errors, warnings = pane.count()
        panel = self.query_one("#bottom-panel", BottomPanel)
        panel.set_problems_count(errors, warnings)
        self._push_diagnostics_to_views()
        self._refresh_status(diagnostics=(errors, warnings))

    def _push_diagnostics_to_views(self) -> None:

        try:
            ta = self.query_one("#code-area", DiagnosticTextArea)
        except Exception:
            ta = None
        markers: Dict[int, str] = {}
        if ta is not None:
            try:
                cur = self.buffer_manager.active_buffer
                cur_path = os.path.normcase(os.path.abspath(cur.filepath)) if (
                    cur and cur.filepath) else ""
            except Exception:
                cur_path = ""
            for d in self._last_diags:
                if cur_path and os.path.normcase(os.path.abspath(d.path)) != cur_path:
                    continue

                if markers.get(d.row) != "error":
                    markers[d.row] = d.severity
            ta.set_markers(markers)

        per_file: Dict[str, List[str]] = {}
        for d in self._project_diags:
            per_file.setdefault(os.path.normcase(os.path.abspath(d.path)),
                                []).append(d.severity)
        try:
            tree = self.query_one("#explorer-tree", IconDirectoryTree)
        except Exception:
            return
        tree.set_diagnostics(per_file)

    def _merge_project(self, active: List) -> List:

        if not self._project_diags:
            return active

        seen = set()
        out = []
        for d in list(active) + list(self._project_diags):
            try:
                key = (os.path.normcase(os.path.abspath(d.path)), d.row)
            except Exception:
                key = (d.path, d.row)
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
        out.sort(key=lambda d: (os.path.basename(d.path).lower(), d.row, d.col))
        return out
        return out

    def action_scan_project(self) -> None:

        if self._scan_task is not None and not self._scan_task.done():
            return
        root = self.root_dir
        pane = None
        try:
            pane = self.query_one(ProblemsPane)
        except Exception:
            pass
        if pane is not None:
            pane.set_scan_note("scanning project...")
        self._scan_task = asyncio.create_task(self._scan_project_async(root))

    async def _scan_project_async(self, root: str) -> None:
        try:
            scan = await asyncio.to_thread(scan_project, root, linter)
        except Exception as exc:
            self._scan_note = f"scan failed: {exc}"
            self._project_diags = []
            self.call_after_refresh(self._reapply_diagnostics)
            return
        self._project_diags = list(scan.diagnostics)
        bits = [f"{scan.checked} file{'' if scan.checked == 1 else 's'} checked"]
        if scan.note:
            bits.append(scan.note.lstrip("- "))
        self._scan_note = " - ".join(bits)
        self.call_after_refresh(self._reapply_diagnostics)

    def _reapply_diagnostics(self) -> None:

        self._apply_diagnostics(self._applied_diags, self._applied_note)

    def _maybe_scan_project(self) -> None:

        try:
            panel = self.query_one("#bottom-panel", BottomPanel)

            self.query_one(ProblemsPane)
        except Exception:
            return
        if not panel.is_open() or panel.active_tab != TAB_PROBLEMS:
            return
        if not self._project_diags and self._scan_task is None:
            self.action_scan_project()
            return
        if self._scan_task is None or self._scan_task.done():
            self.action_scan_project()

    def _refresh_diagnostics_now(self, full: bool = True) -> None:

        if self._lint_timer is not None:
            self._lint_timer.stop()
            self._lint_timer = None
        if full:
            self._run_full_lint()
        else:
            self._run_fast_lint()

    @on(ProblemsPane.JumpRequested)
    def on_problem_jump(self, message: ProblemsPane.JumpRequested) -> None:

        message.stop()
        row = max(0, message.row)
        col = max(0, message.col)
        try:
            self.open_file_path(message.path)
        except Exception:
            return
        self.call_after_refresh(self._focus_after_jump, row, col)

    def _focus_after_jump(self, row: int, col: int) -> None:

        ta = self.query_one("#code-area", TextArea)
        if ta.has_class("hidden"):
            return
        try:
            lines = ta.document.lines
        except Exception:
            lines = None
        if lines:
            row = max(0, min(row, len(lines) - 1))
            col = max(0, min(col, max(0, len(lines[row]) - 1)))
        try:

            ta.move_cursor((row, col))
            ta.scroll_cursor_visible(center=True)
            ta.focus()
        except Exception:
            return
        self._refresh_status()

    def _selected_text_args(self) -> List[str]:

        try:
            ta = self.query_one("#code-area", TextArea)
        except Exception:
            return []
        try:
            sel = ta.selection
            start, end = sel[0], sel[1]
            if start == end:
                return []

            lines = [ta.get_line(r).plain
                     for r in range(start[0], end[0] + 1)]
            if len(lines) == 1:
                text = lines[0][start[1]:end[1]]
            else:
                lines[0] = lines[0][start[1]:]
                lines[-1] = lines[-1][:end[1]]
                text = "\n".join(lines)
        except Exception:
            return []
        if not text.strip():
            return []
        args = [a for a in text.split() if a]
        return args[:20]

    def _current_run_spec(self):
        buf = self.buffer_manager.active_buffer
        if buf is None or not buf.filepath:
            return None
        return run_command_for(buf.filepath, self._selected_text_args())

    def action_run_file(self) -> None:

        if self._runner is not None and self._runner.is_running:
            self.action_stop_run()
            return
        buf = self.buffer_manager.active_buffer
        if buf is None or not buf.filepath:
            self.notify("No file to run", severity="warning")
            return
        spec = run_command_for(buf.filepath, self._selected_text_args())
        if not spec.ok:
            self.notify(f"Cannot run this file: {spec.reason}", severity="warning",
                         timeout=3.0)
            return

        if buf.is_modified:
            ta = self.query_one("#code-area", TextArea)
            buf.content = ta.text
            if not self.buffer_manager.save_file():
                self.notify("Cannot save before run - aborted", severity="error")
                return
            self._refresh_tabs()
        panel = self.query_one("#bottom-panel", BottomPanel)
        panel.open_panel()
        panel.show_tab(TAB_TERMINAL)
        term = self.query_one("#bottom-terminal", EditorBottomTerminal)
        term.write_run_header(spec.display)
        if self._runner is None:
            self._runner = RunProcess(on_line=self._on_run_line,
                                     on_done=self._on_run_done)

        self._pending_spec = spec
        self.call_later(self._start_run)
        self._refresh_run_button()

    async def _start_run(self) -> None:
        spec, self._pending_spec = getattr(self, "_pending_spec", None), None
        if spec is None or self._runner is None:
            return
        started = await self._runner.start(spec)
        if not started:
            self.notify("Could not start the process", severity="error")
            self._on_run_done(-1)
        else:
            self._refresh_run_button()

    def _on_run_line(self, line: str) -> None:
        try:
            self.query_one("#bottom-terminal", EditorBottomTerminal).write_output(line)
        except Exception:
            pass

    def _on_run_done(self, code: int) -> None:
        try:
            term = self.query_one("#bottom-terminal", EditorBottomTerminal)
            if code == 0:
                term.write_output(Text.from_markup("[#3fb950]exited with code 0[/]"))
            elif code is not None and code < 0:
                term.write_output(Text.from_markup("[#d29922]stopped[/]"))
            else:
                term.write_output(Text.from_markup(
                    f"[#f85149]exited with code {code}[/]"))
        except Exception:
            pass

        self._refresh_run_button()

    def action_stop_run(self) -> None:
        if self._runner is None or not self._runner.is_running:
            self.notify("Nothing is running")
            return
        self._runner.stop()
        self._refresh_run_button()

    def _refresh_run_button(self) -> None:

        try:
            bar = self.query_one(EditorTopBar)
        except Exception:
            return
        spec = self._current_run_spec()
        running = self._runner is not None and self._runner.is_running
        bar.refresh_run_button(runnable=bool(spec and spec.ok), running=running)

    @on(TextArea.SelectionChanged, "#code-area")
    def on_textarea_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self._refresh_status()

    def action_show_menu(self) -> None:
        menu = self.query_one("#inapp-dropdown", InAppDropdownMenu)
        menu.toggle()

    @on(InAppDropdownMenu.ActionSelected)
    def on_dropdown_action_selected(self, message: InAppDropdownMenu.ActionSelected) -> None:
        self.handle_menu_action(message.action_id)

    @on(InAppDropdownMenu.Dismissed)
    def on_dropdown_dismissed(self) -> None:
        ta = self.query_one("#code-area", TextArea)
        if not ta.has_class("hidden"):
            ta.focus()

    def handle_menu_action(self, action_id: Optional[str]) -> None:
        if not action_id:
            return

        actions = {
            "projects": self.action_projects,
            "new_file": self.action_new_file_dialog,
            "open_file": self.action_open_file_dialog,
            "save_file": self.action_save_file,
            "save_as": self.action_save_as_dialog,
            "close_tab": self.action_close_tab,
            "toggle_sidebar": self.action_toggle_sidebar,
            "toggle_line_numbers": self.action_toggle_line_numbers,

            "explorer_wider": self.action_explorer_wider,
            "explorer_narrower": self.action_explorer_narrower,
            "terminal_bigger": self.action_terminal_bigger,
            "terminal_smaller": self.action_terminal_smaller,
            "open_terminal": self.action_open_terminal,
            "git_branches": self.action_open_git,
            "checkpoints": self.action_open_checkpoints,
            "quit": self.action_quit_editor,
        }
        handler = actions.get(action_id)
        if handler:
            handler()

    def show_prompt(self, title: str, on_submit, placeholder: str = "",
                    default_value: str = "", target: str = "") -> None:

        self.query_one("#prompt-overlay", PromptOverlay).open(
            title, on_submit, placeholder=placeholder,
            default_value=default_value, target=target,
        )

    def action_new_file_dialog(self) -> None:

        self.action_explorer_new_file()

    @on(events.MouseDown)
    def on_mouse_down_dismiss(self, event: events.MouseDown) -> None:
        if len(self.screen_stack) != 1:
            return
        screen = self.screen
        if not isinstance(screen, Screen):
            return
        point = event.screen_offset

        overlay = _first(screen, "#prompt-overlay")
        if isinstance(overlay, PromptOverlay) and overlay.is_open():
            if not _contains(overlay.dialog_region(), point):
                event.stop()
                overlay._finish("")

        menu = _first(screen, "#inapp-dropdown")
        if menu is not None and menu.has_class("open"):
            if not _contains(menu.region, point):
                event.stop()
                menu.action_dismiss_menu()

    def action_open_file_dialog(self) -> None:

        self.notify("Open a file from the explorer tree", timeout=2.0)

    def action_save_as_dialog(self) -> None:
        buf = self.buffer_manager.active_buffer
        if not buf:
            self.notify("No active file to save", severity="warning")
            return

        def on_submit(new_name: str):
            if not new_name:
                return
            target = (new_name if os.path.isabs(new_name)
                      else os.path.join(self.root_dir, new_name))
            ta = self.query_one("#code-area", TextArea)
            if not self.buffer_manager.save_as(target, ta.text):
                self.notify(f"Cannot save as: {new_name}", severity="error")
                return
            rel = os.path.relpath(target, self.root_dir)
            self._auto_create_checkpoint(f"Zapis jako: {rel}")

            self._reload_explorer()
            self.notify(f"Saved as: {rel}")
            self._refresh_tabs()
            self._refresh_status()

        self.show_prompt("Save as", on_submit,
                         default_value=buf.filename,
                         target=self.root_dir)

    def action_save_file(self) -> None:
        buf = self.buffer_manager.active_buffer
        if not buf:
            self.notify("No active file to save", severity="warning")
            return

        ta = self.query_one("#code-area", TextArea)
        buf.content = ta.text
        if self.buffer_manager.save_file():
            rel = os.path.relpath(buf.filepath, self.root_dir)
            self._auto_create_checkpoint(f"Autozapis: {rel}")
            self.notify(f"Saved: {rel} (checkpoint created)", timeout=2.0)
            self._refresh_tabs()

            if self.buffer_manager.last_save_created_file:
                self._reload_explorer()

            self._refresh_diagnostics_now(full=True)
            errors_after = sum(1 for d in self._last_diags if d.severity == "error")
            if errors_after:
                panel = self.query_one("#bottom-panel", BottomPanel)
                if not panel.is_open():
                    panel.open_panel()
                panel.show_tab(TAB_PROBLEMS)

            self.call_after_refresh(self._maybe_scan_project)
            self._refresh_status()
        else:
            self.notify("Failed to save file", severity="error")

    def _confirm_discard(self, action, files: list, then: str) -> None:

        names = ", ".join(os.path.basename(p) for p in files[:4])
        if len(files) > 4:
            names += f", +{len(files) - 4}"

        def on_submit(answer: str) -> None:
            choice = (answer or "").strip().lower()
            if choice == "s":
                for path in files:
                    self._save_buffer_path(path)
                then()
            elif choice == "d":

                then()
            elif choice:
                self.notify("Unclear answer - type s, d, or leave it empty",
                            severity="warning", timeout=3.0)

        self.show_prompt(
            "Unsaved changes",
            on_submit,
            placeholder=f"[s] save  [d] discard  [Esc] cancel - {names}",
        )

    def _save_buffer_path(self, path: str) -> None:

        if path == (self.buffer_manager.active_path or ""):

            self.action_save_file()
            return
        if path not in self.buffer_manager.buffers:
            return
        if self.buffer_manager.save_file(path):
            self._refresh_tabs()
            self._refresh_status()

    def action_close_tab(self) -> None:
        buf = self.buffer_manager.active_buffer
        if not buf:
            return
        path = buf.filepath
        if buf.is_modified:
            self._confirm_discard(
                self._save_buffer_path, [path],
                then=lambda: self.close_file_path(path),
            )
        else:
            self.close_file_path(path)

    def action_toggle_sidebar(self) -> None:
        explorer = self.query_one("#editor-explorer", EditorExplorer)
        explorer.toggle_class("hidden")

    def action_explorer_wider(self) -> None:
        explorer = self.query_one("#editor-explorer", EditorExplorer)
        if explorer.has_class("hidden"):
            explorer.remove_class("hidden")
        explorer.widen(4)
        self._refresh_status()

    def action_explorer_narrower(self) -> None:
        explorer = self.query_one("#editor-explorer", EditorExplorer)
        explorer.narrow(4)
        self._refresh_status()

    def action_terminal_bigger(self) -> None:
        panel = self.query_one("#bottom-panel", BottomPanel)
        panel.open_panel()
        panel.grow(2)

    def action_terminal_smaller(self) -> None:
        panel = self.query_one("#bottom-panel", BottomPanel)
        panel.shrink(2)
        if panel.region.height <= BottomPanel.MIN_HEIGHT + 1:
            panel.close_panel()
            ta = self.query_one("#code-area", TextArea)
            if not ta.has_class("hidden"):
                ta.focus()

    def action_toggle_line_numbers(self) -> None:
        ta = self.query_one("#code-area", TextArea)
        self.show_line_nums = not self.show_line_nums
        ta.show_line_numbers = self.show_line_nums
        self.notify(f"Line numbers: {'ON' if self.show_line_nums else 'OFF'}")

    def action_open_terminal(self) -> None:
        panel = self.query_one("#bottom-panel", BottomPanel)
        if panel.is_open():
            panel.close_panel()
            ta = self.query_one("#code-area", TextArea)
            if not ta.has_class("hidden"):
                ta.focus()
        else:
            panel.open_panel()
            panel.show_tab(TAB_TERMINAL)
            term = self.query_one("#bottom-terminal", EditorBottomTerminal)
            term.focus_input()

    def action_toggle_panel(self) -> None:

        panel = self.query_one("#bottom-panel", BottomPanel)
        if panel.is_open():
            panel.close_panel()
            ta = self.query_one("#code-area", TextArea)
            if not ta.has_class("hidden"):
                ta.focus()
        else:
            panel.open_panel()
            active = panel.active_tab
            panel.show_tab(active)
            if active == TAB_TERMINAL:
                self.query_one("#bottom-terminal", EditorBottomTerminal).focus_input()
            else:
                self.query_one("#panel-problems-list").focus()
            self.call_after_refresh(self._maybe_scan_project)

    @on(BottomPanel.Opened)
    def _on_panel_opened(self, event: BottomPanel.Opened) -> None:
        event.stop()
        self.query_one("#status-bar", EditorStatusBar).set_panel_open(True)
        self._maybe_scan_project()

    @on(BottomPanel.Closed)
    def _on_panel_closed(self, event: BottomPanel.Closed) -> None:
        event.stop()
        self.query_one("#status-bar", EditorStatusBar).set_panel_open(False)

    @on(BottomPanel.TabChanged)
    def _on_panel_tab_changed(self, event: BottomPanel.TabChanged) -> None:

        if event.tab_name == TAB_PROBLEMS:
            self.call_after_refresh(self._maybe_scan_project)

    @on(EditorBottomTerminal.Closed)
    def on_bottom_terminal_closed(self) -> None:
        ta = self.query_one("#code-area", TextArea)
        if not ta.has_class("hidden"):
            ta.focus()

    def action_open_git(self) -> None:
        if GitBranchesModal:
            try:
                self.push_screen(GitBranchesModal(workdir=self.root_dir))
            except Exception as e:
                self.notify(f"Git error: {e}", severity="error")
        else:
            self.notify("Git module not available", severity="warning")

    def action_open_checkpoints(self) -> None:
        if CheckpointsModal:
            try:
                self.push_screen(CheckpointsModal(workdir=self.root_dir))
            except Exception as e:
                self.notify(f"Checkpoints error: {e}", severity="error")
        else:
            self.notify("Checkpoints module not available", severity="warning")

    def action_handle_escape(self) -> None:
        menu = self.query_one("#inapp-dropdown", InAppDropdownMenu)
        if menu.has_class("open"):
            menu.toggle()
            return
        if len(self.screen_stack) > 1:
            self.pop_screen()
            return
        term = self.query_one("#bottom-terminal", EditorBottomTerminal)
        if not term.has_class("hidden"):
            term.add_class("hidden")
            ta = self.query_one("#code-area", TextArea)
            if not ta.has_class("hidden"):
                ta.focus()
            return
        ta = self.query_one("#code-area", TextArea)
        if not ta.has_class("hidden") and not ta.has_focus:
            ta.focus()

    def action_quit_editor(self) -> None:
        dirty = [b.filepath for b in self.buffer_manager.list_open_buffers()
                 if b.is_modified]
        if dirty:

            self._confirm_discard(self._save_buffer_path, dirty,
                                  then=self.exit)
        else:
            self.exit()

    @on(BrandMenuLabel.Clicked)
    def on_brand_menu_clicked(self) -> None:
        self.action_show_menu()

    @on(ToolIcon.Clicked)
    def on_tool_icon_clicked(self, message: ToolIcon.Clicked) -> None:
        action_map = {
            "btn-run": self.action_run_file,
            "btn-terminal": self.action_open_terminal,
            "btn-git": self.action_open_git,
            "btn-checkpoints": self.action_open_checkpoints,
            "btn-sidebar": self.action_toggle_sidebar,
        }
        handler = action_map.get(message.action_id)
        if handler:
            handler()

    async def on_event(self, event) -> None:

        if isinstance(event, events.Key) and not isinstance(event, events.MouseEvent):
            if event.key not in ("escape", "tab", "shift+tab"):
                pole = self._prompt_input_if_open()
                if pole is not None and self.screen.focused is not pole:

                    self.screen.set_focus(pole)
        await super().on_event(event)

    def _prompt_input_if_open(self):

        try:
            overlay = _first(self.screen, "#prompt-overlay")
        except Exception:
            return None
        if not isinstance(overlay, PromptOverlay) or not overlay.is_open():
            return None
        try:
            return overlay.query_one("#prompt-input")
        except Exception:
            return None

    def action_projects(self) -> None:

        from ..core.workspace import projects_root
        from .project_picker import ProjectPicker

        try:
            projects_root().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.notify(f"Cannot create projects folder: {exc}",
                        severity="error")
            return

        def otworz(sciezka: Optional[str]) -> None:
            if not sciezka:
                return
            self._open_in_new_window(sciezka)

        self.push_screen(ProjectPicker(), otworz)

    def _open_in_new_window(self, cel: str) -> bool:

        import subprocess
        import sys as _sys

        editor_main = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "main.py",
        )
        if not os.path.isfile(editor_main):
            self.notify("Cannot find editor/main.py", severity="error")
            return False
        try:
            if os.name == "nt":
                subprocess.Popen([_sys.executable, editor_main, cel],
                                 creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                subprocess.Popen([_sys.executable, editor_main, cel],
                                 start_new_session=True)
        except OSError as exc:
            self.notify(f"Cannot open new window: {exc}", severity="error")
            return False
        return True
