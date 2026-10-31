"""`/models`: one row per provider, then a screen of that provider's models.

This used to be an accordion. Every provider was a row, and expanding one pushed
its models into the same flat list underneath, which meant the arrows and the
up/down keys moved through two different kinds of thing and the search box mixed
providers with models in one result set. A provider and its models are two
questions, so they are two screens now.

Two decisions worth stating, because both undo something that looked deliberate:

* **No fabricated model lists.** Twenty-eight providers ship a hardcoded
  `default_models` list that used to be shown whenever a fetch failed, so the
  card looked like a catalogue and was not one. A card now carries a *state* -
  key required, no answer, loading, and how old the answer is - and never a list
  that nobody asked a server for.
* **A state is not a model.** `anthropic` has `supports_remote_fetch=False`, so
  its card used to be permanently empty. models.dev lists its models, so the
  card has a real list marked as coming from the catalogue rather than from an
  account.
"""

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ...core import model_catalog as MC
from ...core.providers import PROVIDERS_CATALOG, fetch_remote_models, has_reasoning
from ...core.settings import get_settings
from .add_model import AddModelModal
from .api_key import ApiKeyModal

# Providers that run on this machine and need no key.
LOCAL_PROVIDERS = ("local_gguf", "ollama", "lmstudio", "vllm")

# A card that can be opened without a key.
KEYLESS = ("openrouter",) + LOCAL_PROVIDERS

# Pinned to the top of the list and kept blue. It is the one provider that
# works with nothing configured and nothing purchased, and it was sorting
# between LM Studio and Ollama by name in the same dim grey as the ones that
# need a key and cannot answer.
TOP_PROVIDER = "local_gguf"

# The same pair as the thinking block and the input-bar marker. Ambiguous width,
# so the glyph is always followed by a space in its own style run - that is what
# keeps the spare cell from being overdrawn by the provider name beside it.
SPINNER_FRAMES = ["⌬", "✻"]
SPINNER_INTERVAL = 0.18


def _age_text(when: float) -> str:
    """How old an answer is, in words a person would use."""
    if not when:
        return "never"
    seconds = max(0, int(time.time() - when))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def _unusable_note(model: Dict[str, Any]) -> str:
    """The reason a model is marked unusable, if it is.

    Pulled out of the badge string rather than read from a field, because the
    badge is the only thing a plugin sets and its shape is not ours to fix.
    The prose after the marker is kept for `/modelinfo` and dropped here: one
    word is enough to stop a selection, a sentence is enough to make the list
    unreadable.
    """
    badge = str(model.get("badge") or "")
    for part in badge.split(" · "):
        if part.upper().startswith("UNUSABLE"):
            return part
    return ""


class ModelSearchInput(Input):
    """The search box, which hands the navigation keys back to the list.

    Written against `getattr` rather than `isinstance` so the same widget works
    in both screens. It used to check for one modal class by name, which meant
    the box silently stopped working the moment a second screen existed.
    """

    def on_key(self, event: events.Key) -> None:
        screen = self.screen
        if not hasattr(screen, "action_cursor_down"):
            return

        if event.key == "down":
            event.prevent_default()
            event.stop()
            screen.action_cursor_down()
        elif event.key == "up":
            event.prevent_default()
            event.stop()
            screen.action_cursor_up()
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            action = getattr(screen, "action_activate", None)
            if action:
                action()


# --------------------------------------------------------------------------
# screen 1: providers
# --------------------------------------------------------------------------

class ModelSelectModal(ModalScreen[Tuple[str, str]]):

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "open_provider", "Open"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("ctrl+d", "configure_key", "Set API Key"),
        Binding("ctrl+r", "refresh_all", "Refresh"),
    ]

    def __init__(self, current_provider: str = "", current_model: str = "", **kwargs):
        super().__init__(**kwargs)
        self.current_provider = current_provider
        self.current_model = current_model
        self.settings = get_settings()

        self.provider_models: Dict[str, List[Dict[str, Any]]] = {}
        self.fetching_providers: set = set()
        self.failed_providers: set = set()
        # When each provider's list last came back, for the "3 h ago" stamp.
        self.fetched_at: Dict[str, float] = {}
        self.filtered_items: List[str] = []
        self._spinner_idx = 0
        self._spinner_timer = None

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Models", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")

            yield OptionList(id="modal-list")

            with Horizontal(id="modal-footer"):
                yield Static("[enter: Open]  [ctrl+d: Key]  [ctrl+r: Refresh]")
                yield Static("", id="modal-footer-right")

    def on_mount(self) -> None:
        self.rebuild()
        self.query_one("#modal-list", OptionList).focus()
        self.refresh_all()
        # A spinner rather than a word, for the same reason the input bar has
        # one: 33 providers loading at once with the word "loading" on each of
        # them is a wall of text that says less than one moving dot per card.
        self._spinner_timer = self.set_interval(SPINNER_INTERVAL, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if not self.fetching_providers:
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(SPINNER_FRAMES)
        self.rebuild()

    def _spinner_for(self, pid: str) -> str:
        return SPINNER_FRAMES[self._spinner_idx]

    # ---------------------------------------------------------- fetching

    def refresh_all(self) -> None:
        for pid in PROVIDERS_CATALOG:
            key = self.settings.get_api_key(pid)
            if key or pid in KEYLESS:
                self.fetch_provider_models(pid, key)

    def _fetch_one(self, pid: str) -> None:
        """Fetch exactly one provider. `ctrl+r` on a row means this provider."""
        self.fetch_provider_models(pid, self.settings.get_api_key(pid))

    @work(thread=True)
    def fetch_provider_models(self, pid: str, api_key: str) -> None:
        self.fetching_providers.add(pid)
        self.failed_providers.discard(pid)
        self.app.call_from_thread(self.rebuild)

        models = fetch_remote_models(pid, api_key)

        custom_list = self.settings.get_custom_models(pid)
        known = {str(m.get("id")) for m in models}
        for cm in custom_list:
            if cm not in known:
                models.append({
                    "id": cm,
                    "name": cm,
                    "badge": "custom",
                    "thinking": has_reasoning(pid, cm) or False,
                })

        self.provider_models[pid] = models
        self.fetched_at[pid] = time.time()
        if not models:
            self.failed_providers.add(pid)
        self.fetching_providers.discard(pid)
        self.app.call_from_thread(self.rebuild)

    def refresh_catalog(self) -> None:
        """Pull models.dev again. Separate from the provider list on purpose.

        The provider says what this account can reach; the catalogue says what
        each model can do. Refreshing one has no reason to wait on the other.
        """
        self.fetch_catalog_from_thread()

    @work(thread=True)
    def fetch_catalog_from_thread(self) -> None:
        try:
            MC.refresh()
        except Exception:
            pass
        self.app.call_from_thread(self.rebuild)

    # ------------------------------------------------------------ drawing

    def _state_text(self, pid: str) -> Tuple[str, str]:
        """What the card says: plain text and a style, never markup.

        Returning a markup string was the bug that put
        `[dim #8b949e]key required[/]` on the screen as literal text. These
        cells are built with `rich.text.Text`, which does not parse markup -
        it appends it. So the state is text plus a style and the styling is
        applied by whoever appends it.
        """
        if pid in self.fetching_providers:
            return ("loading", "dim #58a6ff")

        count = len(self.provider_models.get(pid, []))
        key = self.settings.get_api_key(pid)
        from_catalog = count > 0 and all(
            m.get("from_catalog") for m in self.provider_models.get(pid, [])
        )

        if count:
            parts = [f"{count} models"]
            if from_catalog:
                parts.append("catalog")
            elif pid in LOCAL_PROVIDERS:
                parts.append("local")
            elif key:
                parts.append("key set")
            when = self.fetched_at.get(pid)
            if when and (time.time() - when) > 300:
                parts.append(_age_text(when))
            return ("  ".join(parts), "#7ee787")

        if pid == "huggingface":
            # It hosts weights, it does not serve them. Asking it for a model
            # list would return the catalogue of the hub, which is not what this
            # screen is choosing from.
            return ("add a model - no list to fetch", "dim #8b949e")
        if not key and pid not in KEYLESS:
            return ("key required", "dim #8b949e")
        if pid in self.failed_providers:
            return ("no answer", "#f0883e")
        return ("nothing yet", "dim #8b949e")

    def rebuild(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        prev = ol.highlighted
        ol.clear_options()
        self.filtered_items.clear()

        for pid, pinfo in self._ordered_providers():
            is_active = pid == self.current_provider
            loading = pid in self.fetching_providers

            text = Text()
            if loading:
                text.append(self._spinner_for(pid) + " ", style="#58a6ff")
            # The one provider that works with nothing configured stays blue and
            # stays on top. It was sorting between LM Studio and Ollama by name,
            # in the same grey as the ones that need a key and cannot answer.
            if pid == TOP_PROVIDER:
                text.append(f"{pinfo.name}", style="bold #58a6ff")
            else:
                text.append(f"{pinfo.name} ", style="bold white")

            if not loading:
                state, state_style = self._state_text(pid)
                text.append("  ")
                text.append(state, style=state_style)
            if is_active:
                text.append(" *", style="bold #58a6ff")

            ol.add_option(Option(text, id=f"prov_{pid}"))
            self.filtered_items.append(pid)

        if prev is not None and prev < len(self.filtered_items):
            ol.highlighted = prev
        elif self.filtered_items and ol.highlighted is None:
            ol.highlighted = 0

    def _ordered_providers(self):
        """Local GGUF first, everything else alphabetically by name.

        By name and not by whether a key is set: a list that reorders itself as
        keys are added is a list you cannot learn to navigate.
        """
        pinned = [(TOP_PROVIDER, PROVIDERS_CATALOG[TOP_PROVIDER])]
        rest = sorted(
            ((pid, info) for pid, info in PROVIDERS_CATALOG.items() if pid != TOP_PROVIDER),
            key=lambda kv: kv[1].name.lower(),
        )
        return pinned + rest

    # ----------------------------------------------------------- actions

    def _current(self) -> str:
        ol = self.query_one("#modal-list", OptionList)
        if ol.highlighted is None or ol.highlighted >= len(self.filtered_items):
            return ""
        return self.filtered_items[ol.highlighted]

    def action_cursor_up(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_down()

    def action_cancel(self) -> None:
        self.dismiss(("", ""))

    def action_open_provider(self) -> None:
        pid = self._current()
        if pid:
            self.app.push_screen(ModelListModal(pid, self.current_model), self._on_models_chosen)

    def _on_models_chosen(self, result: Optional[Tuple[str, str]]) -> None:
        """The list screen either picked a model or just came back."""
        if result and result[0] and result[1]:
            self.dismiss(result)

    def action_refresh_all(self) -> None:
        pid = self._current()
        if pid:
            self._fetch_one(pid)
        self.refresh_catalog()

    def action_configure_key(self) -> None:
        pid = self._current()
        if not pid:
            return
        if pid in LOCAL_PROVIDERS:
            self.notify(
                "Local providers need no key. They serve what is on this machine.",
                timeout=3.0,
            )
            return

        def on_key_saved(saved: bool) -> None:
            if saved:
                self._fetch_one(pid)
                self.rebuild()
                self.notify(f"API key for {PROVIDERS_CATALOG[pid].name} saved.", timeout=3.0)

        self.app.push_screen(ApiKeyModal(pid), on_key_saved)

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_open_provider()


# --------------------------------------------------------------------------
# screen 2: one provider's models
# --------------------------------------------------------------------------

class ModelListModal(ModalScreen[Optional[Tuple[str, str]]]):
    """The models one provider offers, with its own search box.

    Separate from the provider list rather than expanded inside it: a provider
    can serve hundreds of models, and mixing those into the same list as the 33
    providers meant the arrow keys walked through both and the search matched
    across both without saying so.
    """

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "activate", "Select"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("f5", "refresh", "Refresh"),
        Binding("ctrl+d", "configure_key", "Set API Key"),
        Binding("ctrl+a", "add_model", "Add Model"),
    ]

    def __init__(self, provider_id: str, current_model: str = "", **kwargs):
        super().__init__(**kwargs)
        self.provider_id = provider_id
        self.current_model = current_model
        self.settings = get_settings()
        self.info = PROVIDERS_CATALOG.get(provider_id)
        self.models: List[Dict[str, Any]] = []
        self.fetching = False
        self.fetched_at = 0.0
        self.failed = False
        self.filtered_items: List[Tuple[str, Dict[str, Any]]] = []
        self._spinner_idx = 0
        self._spinner_timer = None

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        name = self.info.name if self.info else self.provider_id
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(name, id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")

            yield ModelSearchInput(placeholder="Search this provider's models...",
                                   id="provider-search")

            yield OptionList(id="modal-list")

            with Horizontal(id="modal-footer"):
                yield Static("[enter: Select]  [ctrl+a: Add]  [F5: Refresh]  [esc: Back]")
                yield Static("", id="modal-footer-right")

    def on_mount(self) -> None:
        self.rebuild()
        self.query_one("#modal-list", OptionList).focus()
        self.fetch()
        self._spinner_timer = self.set_interval(SPINNER_INTERVAL, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if not self.fetching:
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(SPINNER_FRAMES)
        self.rebuild()

    # ---------------------------------------------------------- fetching

    def fetch(self) -> None:
        """Take what the parent already has, then go get it again.

        The parent has usually finished by the time this screen opens, so
        waiting would show an empty list for a second on every visit. Its result
        is a starting point, not the answer - the fetch still runs.
        """
        cached = self.app.screen
        inherited = getattr(cached, "provider_models", {}).get(self.provider_id)
        if inherited:
            self.models = list(inherited)
            self.rebuild()
        self.fetch_models_from_thread()

    @work(thread=True)
    def fetch_models_from_thread(self) -> None:
        self.fetching = True
        self.failed = False
        self.app.call_from_thread(self.rebuild)

        models = fetch_remote_models(self.provider_id, self.settings.get_api_key(self.provider_id))

        custom_list = self.settings.get_custom_models(self.provider_id)
        known = {str(m.get("id")) for m in models}
        for cm in custom_list:
            if cm not in known:
                models.append({
                    "id": cm,
                    "name": cm,
                    "badge": "custom",
                    "thinking": has_reasoning(self.provider_id, cm) or False,
                })

        self.models = models
        self.fetched_at = time.time()
        self.failed = not models
        self.fetching = False
        self.app.call_from_thread(self.rebuild)

        # Hand the result back so the card behind shows the same count.
        parent = self.app.screen
        if hasattr(parent, "provider_models"):
            parent.provider_models[self.provider_id] = models
            parent.fetched_at[self.provider_id] = self.fetched_at
            parent.rebuild()

    # ------------------------------------------------------------ drawing

    def _search_term(self) -> str:
        try:
            return self.query_one("#provider-search", ModelSearchInput).value.strip().lower()
        except Exception:
            return ""

    def _is_free(self, item: Dict[str, Any]) -> bool:
        i_id = str(item.get("id") or "").lower()
        i_name = str(item.get("name") or "").lower()
        i_badge = str(item.get("badge") or "").lower()
        return any("free" in s for s in (i_id, i_name, i_badge))

    def rebuild(self) -> None:
        term = self._search_term()
        ol = self.query_one("#modal-list", OptionList)
        prev = ol.highlighted
        ol.clear_options()
        self.filtered_items.clear()

        shown = [
            m for m in self.models
            if not term
            or term in str(m.get("id", "")).lower()
            or term in str(m.get("name", "")).lower()
        ]
        shown.sort(key=lambda m: (0 if self._is_free(m) else 1,
                                  str(m.get("id") or m.get("name") or "").lower()))

        if self.fetching:
            spin = Text()
            spin.append("  " + SPINNER_FRAMES[self._spinner_idx], style="#58a6ff")
            spin.append(" loading", style="dim #58a6ff")
            ol.add_option(Option(spin, id="row_loading", disabled=True))
            self.filtered_items.append(("hint", {}))
        elif not shown:
            ol.add_option(Option(Text(f"  {self._empty_hint()}", style="dim #8b949e"),
                                 id="row_empty", disabled=True))
            self.filtered_items.append(("hint", {}))
        else:
            for m in shown:
                self._add_model_row(ol, m)

        # Every provider offers Add. There is no separate mode for the ones that
        # publish no list - the button is the same button and the difference is
        # only whether anything came back before it.
        if self.provider_id != "local_gguf":
            ol.add_option(Option(Text("  + Add model...", style="bold #58a6ff"),
                                 id="row_add"))
            self.filtered_items.append(("add", {}))

        if prev is not None and prev < len(self.filtered_items):
            ol.highlighted = prev
        elif self.filtered_items and ol.highlighted is None:
            ol.highlighted = 0

    def _empty_hint(self) -> str:
        if self.provider_id == "huggingface":
            return "This provider publishes no list. Add the model by name below."
        if self.provider_id in LOCAL_PROVIDERS:
            return "Nothing found on this machine."
        if not self.settings.get_api_key(self.provider_id) and self.provider_id not in KEYLESS:
            return "No API key set. Ctrl+D adds one."
        if self.failed:
            when = _age_text(self.fetched_at) if self.fetched_at else ""
            return f"No answer from the provider{f' ({when})' if when else ''}."
        return "Nothing matches that search."

    def _add_model_row(self, ol: OptionList, m: Dict[str, Any]) -> None:
        """A row says the model's name and whether it is free. Nothing else.

        The current model is blue and nothing more. It carried a marker as well -
        `●`, then `*` - which was the second thing on the line saying what the
        colour already said, and it shifted the name one column right of where
        every other name sat.

        `UNUSABLE` is kept, because that one word is what stops a user picking a
        model that stalls every turn. Everything else the provider or plugin put
        in `badge` is dropped: it arrived as one to four lines per row - a
        context size, a recommendation, a sentence about why a model is unusable,
        a thinking-ladder listing - and made the name the smallest thing on
        screen.
        """
        mid = str(m.get("id") or m.get("name") or "")
        is_active = mid == self.current_model

        text = Text()
        text.append("  ")
        text.append(mid, style="bold #58a6ff" if is_active else "white")

        if self._is_free(m):
            text.append("  FREE", style="bold #7ee787")

        unusable = _unusable_note(m)
        if unusable:
            text.append("  UNUSABLE", style="bold #f85149")

        ol.add_option(Option(text, id=f"mod_{mid}"))
        self.filtered_items.append(("model", m))

    # ----------------------------------------------------------- actions

    def _current(self) -> Tuple[str, Dict[str, Any]]:
        ol = self.query_one("#modal-list", OptionList)
        if ol.highlighted is None or ol.highlighted >= len(self.filtered_items):
            return ("hint", {})
        return self.filtered_items[ol.highlighted]

    def action_cursor_up(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_down()

    def action_activate(self) -> None:
        kind, data = self._current()
        if kind == "model":
            self.dismiss((self.provider_id, str(data.get("id") or "")))
        elif kind == "add":
            self.action_add_model()

    def action_back(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self.fetch_models_from_thread()

    def action_configure_key(self) -> None:
        if self.provider_id in LOCAL_PROVIDERS:
            self.notify("Local providers need no key.", timeout=3.0)
            return

        def on_key_saved(saved: bool) -> None:
            if saved:
                self.fetch_models_from_thread()
                self.notify("API key saved.", timeout=3.0)

        self.app.push_screen(ApiKeyModal(self.provider_id), on_key_saved)

    def action_add_model(self) -> None:
        if self.provider_id == "local_gguf":
            self.notify("Local models are files in the models folder.", timeout=3.0)
            return

        def on_model_added(model_name: Optional[str]) -> None:
            if not model_name:
                return
            clean = model_name.strip()
            if not clean:
                return
            self.settings.add_custom_model(self.provider_id, clean)
            if not any(m.get("id") == clean for m in self.models):
                self.models.append({
                    "id": clean,
                    "name": clean,
                    "badge": "custom",
                    "thinking": has_reasoning(self.provider_id, clean) or False,
                    "levels": [],
                })
            self.rebuild()
            self.dismiss((self.provider_id, clean))

        self.app.push_screen(AddModelModal(self.provider_id), on_model_added)

    @on(Input.Changed, "#provider-search")
    def on_search_changed(self, event: Input.Changed) -> None:
        self.rebuild()

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_activate()
