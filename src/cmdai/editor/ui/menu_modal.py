from typing import List, Dict, Optional
from textual.screen import ModalScreen
from textual.containers import Vertical
from textual.widgets import OptionList
from textual.widgets.option_list import Option
from textual.binding import Binding
from rich.text import Text
from ..icons.registry import icons

MENU_ITEMS: List[Dict[str, str]] = [
    {"id": "new_file", "icon": "file-plus", "label": "New File"},
    {"id": "open_file", "icon": "folder-open", "label": "Open File..."},
    {"id": "save_file", "icon": "device-floppy", "label": "Save"},
    {"id": "save_as", "icon": "device-floppy-save-as", "label": "Save As..."},
    {"id": "close_tab", "icon": "x", "label": "Close Tab"},
    {"separator": "true"},
    {"id": "toggle_sidebar", "icon": "layout-sidebar", "label": "Toggle Explorer"},
    {"id": "toggle_line_numbers", "icon": "numbers", "label": "Line Numbers"},
    {"id": "cycle_icon_mode", "icon": "palette", "label": "Icon Style"},
    {"separator": "true"},
    {"id": "open_terminal", "icon": "terminal-2", "label": "Panel"},
    {"id": "git_branches", "icon": "git-branch", "label": "Git Branches"},
    {"id": "checkpoints", "icon": "history", "label": "Checkpoints"},
    {"separator": "true"},
    {"id": "quit", "icon": "exit", "label": "Close Editor"},
]

class VSCodeMenuModal(ModalScreen[Optional[str]]):

    DEFAULT_CSS = """
    VSCodeMenuModal {
        align: left top;
        background: transparent;
        padding-top: 1;
        padding-left: 0;
    }
    #menu-palette-container {
        width: 38;
        height: auto;
        max-height: 20;
        background: #161b22;
        border: none;
        padding: 0;
    }
    #menu-options {
        background: #161b22;
        color: #c9d1d9;
        border: none;
        height: auto;
        max-height: 18;
        scrollbar-size-vertical: 1;
        scrollbar-color: #30363d;
    }
    #menu-options:focus {
        border: none;
    }
    #menu-options > .option-list--option-highlighted {
        background: #1f6feb;
        color: #ffffff;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("up", "nav_up", "Up"),
        Binding("down", "nav_down", "Down"),
    ]

    def compose(self):
        with Vertical(id="menu-palette-container"):
            yield OptionList(id="menu-options")

    def on_mount(self):
        ol = self.query_one("#menu-options", OptionList)
        for item in MENU_ITEMS:
            if "separator" in item:
                ol.add_option(Option(Text.from_markup("[dim #21262d]──────────────────────────────────[/]"), disabled=True))
                continue

            lbl = item.get("label", "")
            sc = item.get("shortcut", "")
            icon_char = icons.get_styled(item["icon"], with_space=True)
            spacer = " " * max(1, 20 - len(lbl))
            sc_badge = f"[dim #8b949e]{sc}[/]"
            formatted = f" {icon_char}[#e6edf3]{lbl}[/]{spacer}{sc_badge} "
            ol.add_option(Option(Text.from_markup(formatted), id=item["id"]))

        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        self.dismiss(str(event.option.id))

    def action_dismiss_modal(self):
        self.dismiss(None)

    def action_nav_up(self):
        ol = self.query_one("#menu-options", OptionList)
        ol.action_cursor_up()

    def action_nav_down(self):
        ol = self.query_one("#menu-options", OptionList)
        ol.action_cursor_down()
