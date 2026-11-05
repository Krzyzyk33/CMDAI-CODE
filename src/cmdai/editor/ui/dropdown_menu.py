from typing import List, Dict
from textual.containers import Vertical
from textual.widgets import OptionList
from textual.widgets.option_list import Option
from textual.message import Message
from textual.binding import Binding
from rich.text import Text
from ..icons.registry import icons

MENU_ITEMS: List[Dict[str, str]] = [

    {"id": "projects", "icon": "new-project", "label": "Projects"},
    {"separator": "true"},
    {"id": "new_file", "icon": "file-plus", "label": "New File"},
    {"id": "save_file", "icon": "device-floppy", "label": "Save"},
    {"id": "save_as", "icon": "device-floppy-save-as", "label": "Save As..."},
    {"id": "close_tab", "icon": "x", "label": "Close Tab"},
    {"separator": "true"},
    {"id": "toggle_sidebar", "icon": "layout-sidebar", "label": "Toggle Explorer"},
    {"id": "toggle_line_numbers", "icon": "numbers", "label": "Line Numbers"},
    {"separator": "true"},

    {"id": "open_terminal", "icon": "terminal-2", "label": "Panel"},
    {"id": "git_branches", "icon": "git-branch", "label": "Git Branches"},
    {"id": "checkpoints", "icon": "history", "label": "Checkpoints"},
    {"separator": "true"},
    {"id": "quit", "icon": "exit", "label": "Close Editor"},
]

class InAppDropdownMenu(Vertical):

    DEFAULT_CSS = """InAppDropdownMenu {
        layer: overlay;
        dock: top;
        margin-top: 1;
        margin-left: 1;
        width: 34;
        height: auto;
        background: #161b22;
        border: round #30363d;
        display: none;
        padding: 0 1;
    }
    InAppDropdownMenu.open {
        display: block;
    }
    #dropdown-options {
        background: #161b22;
        color: #c9d1d9;
        border: none;
        height: auto;
        scrollbar-size-vertical: 1;
        scrollbar-color: #30363d;
        padding: 0;
    }
    #dropdown-options:focus {
        border: none;
    }

    #dropdown-options > .option-list--option-highlighted {
        background: #1f6feb;
        color: #ffffff;
        text-style: none;
    }
    #dropdown-options > .option-list--option {
        background: #161b22;
    }
    .option-list--option:hover {
        background: #161b22;
    }
"""

    BINDINGS = [
        Binding("escape", "dismiss_menu", "Close"),
        Binding("up", "nav_up", "Up"),
        Binding("down", "nav_down", "Down"),
    ]

    class ActionSelected(Message):
        def __init__(self, action_id: str):
            super().__init__()
            self.action_id = action_id

    class Dismissed(Message):
        pass

    def compose(self):
        yield OptionList(id="dropdown-options")

    def on_mount(self):
        ol = self.query_one("#dropdown-options", OptionList)
        for item in MENU_ITEMS:
            if "separator" in item:
                ol.add_option(Option(Text.from_markup("[dim #30363d]─" * 30), disabled=True))
                continue

            lbl = item.get("label", "")
            icon_char = icons.get_raw(item["icon"])
            icon_color = icons.get_color(item["icon"])

            ol.add_option(
                Option(
                    Text.from_markup(
                        f" [{icon_color}]{icon_char}[/]  [#e6edf3]{lbl}[/] "
                    ),
                    id=item["id"],
                )
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        self.remove_class("open")
        self.post_message(self.ActionSelected(str(event.option.id)))

    def action_dismiss_menu(self):
        self.remove_class("open")
        self.post_message(self.Dismissed())

    def action_nav_up(self):
        ol = self.query_one("#dropdown-options", OptionList)
        ol.action_cursor_up()

    def action_nav_down(self):
        ol = self.query_one("#dropdown-options", OptionList)
        ol.action_cursor_down()

    def toggle(self):
        if self.has_class("open"):
            self.remove_class("open")
            self.post_message(self.Dismissed())
        else:
            self.add_class("open")
            ol = self.query_one("#dropdown-options", OptionList)
            ol.focus()
