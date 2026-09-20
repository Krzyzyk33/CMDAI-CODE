import threading
from typing import Any, Dict, List, Optional, Tuple

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ...core.providers import PROVIDERS_CATALOG, fetch_remote_models
from ...core.settings import get_settings
from .add_model import AddModelModal
from .api_key import ApiKeyModal


class ModalSearchInput(Input):

    def on_key(self, event: events.Key) -> None:
        modal = self.screen
        if not isinstance(modal, ModelSelectModal):
            return

        if event.key == "down":
            event.prevent_default()
            event.stop()
            modal.action_cursor_down()
        elif event.key == "up":
            event.prevent_default()
            event.stop()
            modal.action_cursor_up()
        elif event.key == "right":
            event.prevent_default()
            event.stop()
            modal.action_expand_node()
        elif event.key == "left":
            event.prevent_default()
            event.stop()
            modal.action_collapse_node()
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            modal.action_select_or_toggle()


class ModelSelectModal(ModalScreen[Tuple[str, str]]):

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "select_or_toggle", "Select/Toggle"),
        Binding("right", "expand_node", "Expand"),
        Binding("left", "collapse_node", "Collapse"),
        Binding("up", "cursor_up", "Up"),
        Binding("down", "cursor_down", "Down"),
        Binding("ctrl+d", "configure_key", "Set API Key"),
        Binding("ctrl+a", "add_custom_model_shortcut", "Add Model"),
        Binding("slash", "focus_search", "Search"),
    ]

    def __init__(self, current_provider: str = "", current_model: str = "", **kwargs):
        super().__init__(**kwargs)
        self.current_provider = current_provider
        self.current_model = current_model
        self.settings = get_settings()

        self.expanded_providers: set = set()
        self.provider_models: Dict[str, List[Dict[str, Any]]] = {}
        self.fetching_providers: set = set()
        self.filtered_items: List[Tuple[str, str, Dict[str, Any]]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Models — Providers Tree", id="modal-title")
                yield Static("[esc: Close]", id="modal-esc")

            yield ModalSearchInput(placeholder="Type to filter models... [Press / to search]", id="modal-search")

            yield OptionList(id="modal-list")

            with Horizontal(id="modal-footer"):
                yield Static("[↑/↓: Move]  [→/←: Expand/Collapse]  [enter: Select/Add]  [ctrl+d: Key]")

    def on_mount(self) -> None:
        self.rebuild_tree()
        ol = self.query_one("#modal-list", OptionList)
        ol.focus()
        self.refresh_all_models()

    def refresh_all_models(self) -> None:
        for pid in PROVIDERS_CATALOG:
            key = self.settings.get_api_key(pid)
            if key or pid in ("openrouter", "local_gguf", "ollama", "lmstudio", "vllm"):
                self.fetch_provider_models(pid, key)

    @work(thread=True)
    def fetch_provider_models(self, pid: str, api_key: str) -> None:
        self.fetching_providers.add(pid)
        self.app.call_from_thread(self.rebuild_tree)
        models = fetch_remote_models(pid, api_key)
        if not models and pid in PROVIDERS_CATALOG:
            pinfo = PROVIDERS_CATALOG[pid]
            if pinfo.default_models:
                models = [
                    {
                        "id": m,
                        "name": m,
                        "badge": "free" if "free" in m.lower() else "",
                        "thinking": any(k in m.lower() for k in ("r1", "o1", "o3", "claude-3-7", "thinking")),
                    }
                    for m in pinfo.default_models
                ]
        custom_list = self.settings.get_custom_models(pid)
        for cm in custom_list:
            if not any(m.get("id") == cm for m in models):
                models.append({
                    "id": cm,
                    "name": cm,
                    "badge": "custom",
                    "thinking": any(k in cm.lower() for k in ("r1", "o1", "o3", "claude-3-7", "thinking")),
                })
        self.provider_models[pid] = models
        self.fetching_providers.discard(pid)
        self.app.call_from_thread(self.rebuild_tree)

    def rebuild_tree(self) -> None:
        search_term = ""
        try:
            inp = self.query_one("#modal-search", ModalSearchInput)
            search_term = inp.value.strip().lower()
        except Exception:
            pass

        ol = self.query_one("#modal-list", OptionList)
        prev_idx = ol.highlighted

        ol.clear_options()
        self.filtered_items.clear()

        catalog_items = sorted(PROVIDERS_CATALOG.items(), key=lambda x: 0 if x[0] == "local_gguf" else 1)
        for pid, pinfo in catalog_items:
            models = list(self.provider_models.get(pid, []))
            if not models and pinfo.default_models:
                models = [
                    {
                        "id": m,
                        "name": m,
                        "badge": "free" if "free" in m.lower() else "",
                        "thinking": any(k in m.lower() for k in ("r1", "o1", "o3", "claude-3-7", "thinking")),
                    }
                    for m in pinfo.default_models
                ]
            custom_list = self.settings.get_custom_models(pid)
            for cm in custom_list:
                if not any(m.get("id") == cm for m in models):
                    models.append({
                        "id": cm,
                        "name": cm,
                        "badge": "custom",
                        "thinking": any(k in cm.lower() for k in ("r1", "o1", "o3", "claude-3-7", "thinking")),
                    })
            api_key = self.settings.get_api_key(pid)
            is_fetching = pid in self.fetching_providers

            provider_matches = search_term in pinfo.name.lower() or search_term in pid.lower()
            matching_models = [
                m for m in models
                if search_term in str(m.get("id", "")).lower() or search_term in str(m.get("name", "")).lower()
            ] if search_term else models

            if search_term and not (provider_matches or matching_models):
                continue

            is_expanded = (pid in self.expanded_providers) or (bool(search_term) and bool(matching_models))

            arrow = "▾" if is_expanded else "▸"
            header_text = Text()
            header_text.append(f"{arrow} ", style="bold #58a6ff")
            header_text.append(f"{pinfo.name} ", style="bold white")

            if is_fetching:
                header_text.append("[⟳ fetching models...]", style="dim #58a6ff")
            elif api_key:
                header_text.append(f"[{len(models)} models · Key set]", style="#7ee787")
            elif pid in ("local_gguf", "ollama", "lmstudio", "vllm"):
                header_text.append(f"[{len(models)} models · Local]", style="#58a6ff")
            elif pid == "openrouter":
                header_text.append(f"[{len(models)} models · Free models available]", style="#58a6ff")
            else:
                header_text.append("[Key required · Ctrl+D to add]", style="dim #8b949e")

            ol.add_option(Option(header_text, id=f"prov_{pid}"))
            self.filtered_items.append(("provider", pid, {"name": pinfo.name}))

            if is_expanded:
                models_to_show = matching_models if search_term else models
                if models_to_show:
                    def _is_free_model(item: Dict[str, Any]) -> bool:
                        i_id = str(item.get("id") or "").lower()
                        i_name = str(item.get("name") or "").lower()
                        i_badge = str(item.get("badge") or "").lower()
                        return "free" in i_id or ":free" in i_id or "free" in i_name or "free" in i_badge

                    models_to_show = sorted(
                        models_to_show,
                        key=lambda x: (0 if _is_free_model(x) else 1, str(x.get("id") or x.get("name") or "").lower())
                    )
                if not models_to_show:
                    if pid == "local_gguf":
                        empty_text = Text("    [No .gguf models found in models/ folder. Press Enter to refresh]", style="dim #8b949e")
                        ol.add_option(Option(empty_text, id=f"empty_{pid}"))
                        self.filtered_items.append(("hint", pid, {"is_local": True}))
                    elif not api_key and pid not in ("ollama", "lmstudio", "vllm", "openrouter"):
                        empty_text = Text("    [No API Key configured. Press Ctrl+D to add key]", style="dim #8b949e")
                        ol.add_option(Option(empty_text, id=f"empty_{pid}"))
                        self.filtered_items.append(("hint", pid, {}))
                    elif pid == "ollama":
                        empty_text = Text("    [No local Ollama models detected. Run 'ollama pull <name>' or add below]", style="dim #8b949e")
                        ol.add_option(Option(empty_text, id=f"empty_ollama_{pid}"))
                        self.filtered_items.append(("hint", pid, {"is_local": True}))
                    elif is_fetching:
                        empty_text = Text("    [Fetching live model catalog...]", style="dim #58a6ff")
                        ol.add_option(Option(empty_text, id=f"fetching_{pid}"))
                        self.filtered_items.append(("hint", pid, {"is_local": True}))
                    else:
                        empty_text = Text("    [No models currently available. Add model below]", style="dim #8b949e")
                        ol.add_option(Option(empty_text, id=f"none_{pid}"))
                        self.filtered_items.append(("hint", pid, {"is_local": pid in ("ollama", "lmstudio", "vllm")}))
                else:
                    for m in models_to_show:
                        mid = str(m.get("id") or m.get("name") or "")
                        is_active = (pid == self.current_provider and mid == self.current_model)
                        is_free = _is_free_model(m)
                        m_text = Text()
                        m_text.append("    ")
                        if is_active:
                            m_text.append("● ", style="bold #58a6ff")
                            m_text.append(f"{mid} ", style="bold #58a6ff")
                        else:
                            m_text.append("  ", style="#8b949e")
                            m_text.append(f"{mid} ", style="white")

                        if is_free:
                            m_text.append("[FREE] ", style="bold #7ee787")

                        badge = m.get("badge")
                        if badge:
                            cleaned_badge = " · ".join(part for part in badge.split(" · ") if part.upper() != "FREE")
                            if cleaned_badge:
                                m_text.append(f"[{cleaned_badge}]", style="dim #8b949e")

                        ol.add_option(Option(m_text, id=f"mod_{pid}_{mid}"))
                        self.filtered_items.append(("model", pid, m))

                if pid != "local_gguf":
                    add_text = Text("    + Add model...", style="bold #58a6ff")
                    ol.add_option(Option(add_text, id=f"add_mod_{pid}"))
                    self.filtered_items.append(("add_model", pid, {}))

        if prev_idx is not None and prev_idx < len(self.filtered_items):
            ol.highlighted = prev_idx
        elif self.filtered_items and ol.highlighted is None:
            ol.highlighted = 0

    @on(Input.Changed, "#modal-search")
    def on_search_changed(self, event: Input.Changed) -> None:
        self.rebuild_tree()

    def action_focus_search(self) -> None:
        self.query_one("#modal-search", ModalSearchInput).focus()

    def action_cursor_up(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.action_cursor_up()

    def action_cursor_down(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.action_cursor_down()

    def action_expand_node(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        if ol.highlighted is None or ol.highlighted >= len(self.filtered_items):
            return
        item_type, pid, data = self.filtered_items[ol.highlighted]
        if item_type == "provider":
            self.expanded_providers.add(pid)
            self.rebuild_tree()

    def action_collapse_node(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        if ol.highlighted is None or ol.highlighted >= len(self.filtered_items):
            return
        item_type, pid, data = self.filtered_items[ol.highlighted]
        if item_type == "provider":
            self.expanded_providers.discard(pid)
            self.rebuild_tree()

    def action_cancel(self) -> None:
        self.dismiss(("", ""))

    def action_refresh_models(self) -> None:
        self.refresh_all_models()

    def action_configure_key(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        if ol.highlighted is None or ol.highlighted >= len(self.filtered_items):
            return
        item_type, pid, data = self.filtered_items[ol.highlighted]

        if pid in ("local_gguf", "ollama", "lmstudio", "vllm"):
            if pid == "local_gguf":
                self.notify("Local GGUF scans models directly from models/ folder. No API key needed.", timeout=3.0)
            else:
                self.notify(f"{pid.upper()} runs locally. No API key needed.", timeout=3.0)
            return

        def on_key_saved(saved: bool) -> None:
            if saved:
                key = self.settings.get_api_key(pid)
                self.fetch_provider_models(pid, key)
                self.rebuild_tree()
                self.notify(f"API key for {pid} saved successfully!", timeout=3.0)

        self.app.push_screen(ApiKeyModal(pid), on_key_saved)

    def action_select_or_toggle(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        if ol.highlighted is None or ol.highlighted >= len(self.filtered_items):
            return

        item_type, pid, data = self.filtered_items[ol.highlighted]
        if item_type == "provider":
            if pid in self.expanded_providers:
                self.expanded_providers.discard(pid)
            else:
                self.expanded_providers.add(pid)
            self.rebuild_tree()
        elif item_type == "model":
            mid = str(data.get("id") or data.get("name") or "")
            if pid in ("opencode", "opencode_zen"):
                key = self.settings.get_api_key(pid)
                if not key:
                    self.notify("OpenCode Zen requires an API key. Press Ctrl+D to configure.", timeout=4.0)
                    self.action_configure_key()
                    return
            elif pid == "openrouter":
                key = self.settings.get_api_key(pid)
                if not key:
                    self.notify("Notice: OpenRouter free models require a free key from openrouter.ai/keys. Configure via Ctrl+D or config.json.", timeout=4.0)
            self.dismiss((pid, mid))
        elif item_type == "add_model":
            self.action_add_custom_model(pid)
        elif item_type == "hint":
            if data.get("is_local") or pid in ("local_gguf", "ollama", "lmstudio", "vllm"):
                self.refresh_all_models()
            else:
                self.action_configure_key()

    def action_add_custom_model_shortcut(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        if ol.highlighted is not None and ol.highlighted < len(self.filtered_items):
            _, pid, _ = self.filtered_items[ol.highlighted]
            if pid and pid != "local_gguf":
                self.action_add_custom_model(pid)

    def action_add_custom_model(self, pid: Optional[str] = None) -> None:
        if not pid:
            ol = self.query_one("#modal-list", OptionList)
            if ol.highlighted is not None and ol.highlighted < len(self.filtered_items):
                _, pid, _ = self.filtered_items[ol.highlighted]
        if not pid or pid == "local_gguf":
            return

        def on_model_added(model_name: Optional[str]) -> None:
            if model_name:
                clean_name = model_name.strip()
                if clean_name:
                    self.settings.add_custom_model(pid, clean_name)
                    models = self.provider_models.setdefault(pid, [])
                    if not any(m.get("id") == clean_name for m in models):
                        models.append({
                            "id": clean_name,
                            "name": clean_name,
                            "badge": "custom",
                            "thinking": any(k in clean_name.lower() for k in ("r1", "o1", "o3", "claude-3-7", "thinking")),
                        })
                    self.expanded_providers.add(pid)
                    self.rebuild_tree()
                    self.dismiss((pid, clean_name))

        self.app.push_screen(AddModelModal(pid), on_model_added)

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_select_or_toggle()
