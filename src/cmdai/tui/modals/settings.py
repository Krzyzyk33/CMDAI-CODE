from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core.launcher import install_global_launcher
from ...core.settings import get_settings


class SettingsModal(ModalScreen[bool]):
    """Settings modal for general options and global launcher install."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "toggle_option", "Toggle"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = get_settings()

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Application Settings", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            yield OptionList(id="modal-list")

            with Horizontal(id="modal-footer"):
                yield Static("[enter: Toggle / Run]  [esc: Close]")

    def on_mount(self) -> None:
        self.rebuild_options()

    def rebuild_options(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        prev = ol.highlighted
        ol.clear_options()

        conf_exit = self.settings.config.get("settings", {}).get("confirm_exit", False)
        auto_copy = self.settings.config.get("settings", {}).get("auto_copy_selection", True)
        port = self.settings.config.get("server", {}).get("port", 8080)
        active_loader = self.settings.config.get("active_loader", "cpu")

        ol.add_option(Option(f"  Confirm Exit:            {'[ON]' if conf_exit else '[OFF]'}"))
        ol.add_option(Option(f"  Auto-copy selection:     {'[ON]' if auto_copy else '[OFF]'}"))
        ol.add_option(Option(f"  Background API Port:     {port}"))
        ol.add_option(Option(f"  Active GGUF Loader:      {active_loader}"))
        ol.add_option(Option("  Install Global Launcher: [Run cmdai code installer]"))

        if prev is not None:
            ol.highlighted = prev

    def action_cancel(self) -> None:
        self.dismiss(True)

    def action_toggle_option(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            return

        settings_dict = self.settings.config.setdefault("settings", {})
        if idx == 0:
            settings_dict["confirm_exit"] = not settings_dict.get("confirm_exit", False)
            self.settings.save_config()
        elif idx == 1:
            settings_dict["auto_copy_selection"] = not settings_dict.get("auto_copy_selection", True)
            self.settings.save_config()
        elif idx == 2:
            ports = [8080, 11434, 8000, 5000]
            curr = self.settings.config.setdefault("server", {}).get("port", 8080)
            next_p = ports[(ports.index(curr) + 1) % len(ports)] if curr in ports else 8080
            self.settings.config["server"]["port"] = next_p
            self.settings.save_config()
        elif idx == 3:
            loaders = ["cpu", "vulkan", "cuda"]
            curr = self.settings.config.get("active_loader", "cpu")
            next_l = loaders[(loaders.index(curr) + 1) % len(loaders)] if curr in loaders else "cpu"
            self.settings.config["active_loader"] = next_l
            self.settings.save_config()
        elif idx == 4:
            install_global_launcher(silent=False)

        self.rebuild_options()

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_toggle_option()


class LoaderModal(ModalScreen[Optional[str]]):
    """Modal for switching local GGUF accelerator loader."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "select_loader", "Select"),
    ]

    LOADERS = ["cpu", "cuda", "vulkan"]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Select GGUF Engine Loader", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            yield OptionList(
                Option("  CPU (llama-cpp-python default)"),
                Option("  CUDA 12.4 (NVIDIA GPU Acceleration)"),
                Option("  Vulkan (Cross-vendor GPU Acceleration)"),
                id="modal-list",
            )

            with Horizontal(id="modal-footer"):
                yield Static("[enter: Select Loader]  [esc: Cancel]")

    def on_mount(self) -> None:
        curr = get_settings().config.get("active_loader", "cpu").lower()
        ol = self.query_one("#modal-list", OptionList)
        if curr in self.LOADERS:
            ol.highlighted = self.LOADERS.index(curr)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_select_loader(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        selected_loader = "cpu"
        if ol.highlighted is not None and ol.highlighted < len(self.LOADERS):
            selected_loader = self.LOADERS[ol.highlighted]
            get_settings().set("active_loader", selected_loader)
        self.dismiss(selected_loader)

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_select_loader()
