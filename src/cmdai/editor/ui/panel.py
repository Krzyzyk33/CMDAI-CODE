import os

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Static

from .clickable import MouseActivatable
from .problems import ProblemsPane, WARN_SIGN
from .search_pane import SearchPane
from .terminal import EditorBottomTerminal

TAB_TERMINAL = "terminal"
TAB_PROBLEMS = "problems"
TAB_SEARCH = "search"

class PanelTab(MouseActivatable, Static):

    DEFAULT_CSS = """PanelTab {
        width: auto;
        height: 1;
        padding: 0 1;
        background: transparent;
        color: #8b949e;
        text-style: none;
    }
    PanelTab:hover {
        background: #21262d;
        color: #ffffff;
    }

    PanelTab.-active {
        background: #21262d;
        color: #ffffff;
        text-style: bold;
    }
"""

    class Clicked(Message):
        def __init__(self, tab_name: str) -> None:
            super().__init__()
            self.tab_name = tab_name

    def on_activate(self) -> None:
        self.post_message(self.Clicked(self.name or ""))

class BottomPanel(Vertical):

    MIN_HEIGHT = 6
    MAX_HEIGHT_FRACTION = 0.7

    DEFAULT_CSS = """BottomPanel {
        height: 12;
        dock: bottom;
        background: #0d1117;
        border-top: solid #30363d;
    }
    BottomPanel.hidden {
        display: none;
    }
    #panel-tabs {
        height: 1;
        background: #161b22;
        padding: 0 1;
    }
    #panel-tab-spacer {
        width: 1fr;
    }
    #panel-cwd {
        width: auto;
        max-width: 40;
        color: #6e7681;
        text-style: none;
    }
    #panel-content {
        width: 100%;
        height: 1fr;
    }

    #bottom-terminal,
    #panel-problems-list {
        width: 100%;
        height: 100%;
    }
"""

    class Opened(Message):
        pass

    class Closed(Message):
        pass

    class TabChanged(Message):

        def __init__(self, tab_name: str) -> None:
            super().__init__()
            self.tab_name = tab_name

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.active_tab: str = TAB_TERMINAL
        self.problems_errors: int = 0
        self.problems_warnings: int = 0
        self.search_files: int = 0
        self.search_hits: int = 0
        self._dragging = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="panel-tabs"):
            yield PanelTab("TERMINAL", id="tab-terminal", name=TAB_TERMINAL)
            yield PanelTab("PROBLEMS", id="tab-problems", name=TAB_PROBLEMS)
            yield PanelTab("SEARCH", id="tab-search", name=TAB_SEARCH)
            yield Static("", id="panel-tab-spacer")

            yield Static(self._short_cwd(self.workdir), id="panel-cwd")
        with Vertical(id="panel-content"):

            yield EditorBottomTerminal(self.workdir, id="bottom-terminal")
            yield ProblemsPane(id="panel-problems-list")
            yield SearchPane(id="panel-search-list")

    def on_mount(self) -> None:
        self._sync_tabs()

    def show_tab(self, name: str) -> None:
        if name not in (TAB_TERMINAL, TAB_PROBLEMS, TAB_SEARCH):
            return
        changed = name != self.active_tab
        self.active_tab = name

        term = self.query_one("#bottom-terminal", EditorBottomTerminal)
        problems = self.query_one("#panel-problems-list", ProblemsPane)
        search = self.query_one("#panel-search-list", SearchPane)
        # `display` przez `styles`, nie `.display = ...` - `Widget.display`
        # to wlasciwosc wywolujaca setter `styles.display`, wiec oba
        # sposoby sa rowne, ale `styles.display` ustawia JAWNIE
        # "block"/"none" zamiast zalezec od tego, co zostawil
        # poprzedni uklad. Tak samo robi `ProblemsPane`.
        term.styles.display = "block" if name == TAB_TERMINAL else "none"
        problems.styles.display = "block" if name == TAB_PROBLEMS else "none"
        search.styles.display = "block" if name == TAB_SEARCH else "none"
        if name == TAB_SEARCH:
            # Zakladka dopiero co stala sie widoczna - panel musi
            # wtedy odbudowac liste, bo `mount` na ukrytym widgetcie
            # konczy sie `MountError`.
            search.mount_queued()
        self._sync_tabs()
        if changed:
            self.post_message(self.TabChanged(name))

    @property
    def search_pane(self) -> SearchPane:
        return self.query_one("#panel-search-list", SearchPane)

    @on(PanelTab.Clicked)
    def _on_tab_clicked(self, event: PanelTab.Clicked) -> None:
        event.stop()
        self.show_tab(event.tab_name)

    def _sync_tabs(self) -> None:
        for zakladka, nazwa in (("tab-terminal", TAB_TERMINAL),
                                ("tab-problems", TAB_PROBLEMS),
                                ("tab-search", TAB_SEARCH)):
            self.query_one("#" + zakladka, PanelTab).set_class(
                self.active_tab == nazwa, "-active")
        self._render_problems_tab()
        self._render_search_tab()

    def set_problems_count(self, errors: int, warnings: int = 0) -> None:

        self.problems_errors = max(0, errors)
        self.problems_warnings = max(0, warnings)
        self._render_problems_tab()

    def set_search_count(self, files: int, hits: int) -> None:
        self.search_files = max(0, files)
        self.search_hits = max(0, hits)
        self._render_search_tab()

    def _render_problems_tab(self) -> None:

        e, w = self.problems_errors, self.problems_warnings
        total = e + w
        if not total:
            self.query_one("#tab-problems", PanelTab).update("PROBLEMS")
            return

        label = Text.from_markup("PROBLEMS ")
        label.append(WARN_SIGN + " ", style="#d29922")
        label.append(str(total), style="#ffffff")
        self.query_one("#tab-problems", PanelTab).update(label)

    def _render_search_tab(self) -> None:
        zakladka = self.query_one("#tab-search", PanelTab)
        if not self.search_files or not self.search_hits:
            zakladka.update("SEARCH")
            return
        label = Text.from_markup("SEARCH ")
        label.append(str(self.search_hits), style="#ffffff")
        zakladka.update(label)

    def _short_cwd(self, path: str) -> str:
        home = os.path.expanduser("~")
        if home and path.lower().startswith(home.lower()):
            path = "~" + path[len(home):]
        if len(path) <= 38:
            return path
        return "..." + path[-35:]

    def set_cwd(self, cwd: str) -> None:
        self.workdir = os.path.abspath(cwd)
        self.query_one("#panel-cwd", Static).update(self._short_cwd(self.workdir))

    @property
    def cwd(self) -> str:
        return self.workdir

    def on_mouse_down(self, event: events.MouseDown) -> None:

        if event.button != 1 or event.offset.y != 0:
            return
        self._dragging = True
        self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        screen_bottom = self.app.screen.size.height
        new_h = screen_bottom - event.screen_offset.y
        self.styles.height = self._clamped(new_h)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()

    def _clamped(self, height: int) -> int:

        screen_h = self.app.screen.size.height
        ceiling = max(self.MIN_HEIGHT, int(screen_h * self.MAX_HEIGHT_FRACTION))
        return max(self.MIN_HEIGHT, min(height, ceiling, screen_h - 8))

    def grow(self, delta: int = 2) -> None:
        self.styles.height = self._clamped(self.region.height + delta)

    def shrink(self, delta: int = 2) -> None:
        self.styles.height = self._clamped(self.region.height - delta)

    def open_panel(self) -> None:
        was_hidden = self.has_class("hidden")
        self.remove_class("hidden")
        if was_hidden:
            self.post_message(self.Opened())

    def close_panel(self) -> None:
        self.add_class("hidden")
        self.post_message(self.Closed())

    def is_open(self) -> bool:
        return not self.has_class("hidden")
