from typing import Any, Dict, List, Optional, Tuple
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option
from ...core.settings import get_settings
from ...core.capabilities import get_model_capability

GEN_PARAM_SPECS: List[Tuple[str, str, Any, float, float]] = [
    ("temperature", "temp", float, 0.0, 2.0),
    ("top_p", "topp", float, 0.0, 1.0),
    ("max_tokens", "maxtok", int, 0, 32768),
    ("repetition_penalty", "rep", float, 1.0, 2.0),
]

REASONING_LEVELS = ["Off", "Low", "Medium", "High", "xHigh", "Max"]


class ParamEditScreen(ModalScreen[Optional[Dict[str, Any]]]):
    """Modal for typing and editing a specific generation parameter value (Swift style)."""

    BINDINGS = [
        Binding("escape", "cancel_edit", "Cancel"),
        Binding("enter", "save_value", "Save"),
    ]

    def __init__(self, key: str, value: Any, lo: float, hi: float, cast_type: Any, **kwargs):
        super().__init__(**kwargs)
        self._key = key
        self._current = value
        self._lo = lo
        self._hi = hi
        self._cast = cast_type

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"[bold white]Set {self._key}[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            yield Input(value=str(self._current), id="param-value")
            if self._key == "max_tokens":
                yield Static("[dim]0 = unlimited / default model maximum[/dim]")

            with Horizontal(id="modal-footer"):
                yield Static(
                    f"[dim]{self._lo:g} – {self._hi:g} · [/][b #58a6ff]Save[/] enter   [dim]Cancel[/] esc",
                    id="modal-footer-left",
                )
                yield Static("", id="modal-footer-right")

    def on_mount(self) -> None:
        self.query_one("#param-value", Input).focus()

    @on(Input.Submitted, "#param-value")
    def on_value_submitted(self, event: Input.Submitted) -> None:
        self.action_save_value()

    def action_cancel_edit(self) -> None:
        self.dismiss(None)

    def action_save_value(self) -> None:
        raw = self.query_one("#param-value", Input).value.strip()
        try:
            val = self._cast(raw)
        except ValueError:
            self.query_one("#modal-footer-left", Static).update(
                f"[b #f85149]Not a valid number: {raw}[/]"
            )
            return

        if val < self._lo or val > self._hi:
            self.query_one("#modal-footer-left", Static).update(
                f"[b #f85149]{self._key} must be between {self._lo:g} and {self._hi:g}[/]"
            )
            return

        self.dismiss({"key": self._key, "value": val})


class ParamsModal(ModalScreen[None]):
    """Generation parameters modal matching Swift ParamsModal with immediate editing and persistence."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("enter", "select_item", "Edit"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, current_model: str = "", current_provider: str = "", **kwargs):
        super().__init__(**kwargs)
        self.current_model = current_model
        self.current_provider = current_provider
        self.settings = get_settings()
        self.gen = self.settings.config.setdefault("generation", {})
        self._edit_idx: Optional[int] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("[bold white]Generation parameters[/]", id="modal-title")
                yield Static("[esc]", id="modal-esc")

            yield OptionList(id="modal-list")

            with Horizontal(id="modal-footer"):
                yield Static("[b #58a6ff]Edit[/] enter", id="modal-footer-left")
                yield Static("[dim]changes apply immediately[/]", id="modal-footer-right")

    def on_mount(self) -> None:
        self._refresh_options()
        self.query_one("#modal-list", OptionList).focus()

    def _refresh_options(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        ol.clear_options()

        for idx, spec in enumerate(GEN_PARAM_SPECS):
            key = spec[0]
            default_val = 0.6 if key == "temperature" else (0.95 if key == "top_p" else (2048 if key == "max_tokens" else 1.1))
            val = self.gen.get(key, default_val)
            if key == "max_tokens" and val == 0:
                display = "[dim]0 (unlimited)[/]"
            elif isinstance(val, float):
                display = f"[b white]{val:.2f}[/]"
            else:
                display = f"[b white]{val}[/]"
            ol.add_option(Option(f"  [b #58a6ff]{key:<22}[/]  {display}", id=f"par_{idx}"))

        cap = get_model_capability(self.current_model, self.current_provider)
        if cap and cap.thinking:
            r_val = self.gen.get("reasoning_level", cap.default_level or "Medium")
            ol.add_option(Option(f"  [b #58a6ff]{'reasoning_level':<22}[/]  [b #3fb950]{r_val}[/]", id="par_reasoning"))
        else:
            self.gen["reasoning_level"] = "Off"
            ol.add_option(Option(f"  [dim #8b949e]{'reasoning_level':<22}  Not supported (disabled)[/]", id="par_reasoning", disabled=True))

        if self._edit_idx is not None:
            ol.highlighted = max(0, min(self._edit_idx, len(GEN_PARAM_SPECS)))
        else:
            ol.highlighted = 0

    @on(OptionList.OptionSelected, "#modal-list")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_select_item()

    def action_cursor_up(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_down()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_select_item(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            return

        if idx < len(GEN_PARAM_SPECS):
            spec = GEN_PARAM_SPECS[idx]
            key, _alias, cast, lo, hi = spec
            self._edit_idx = idx
            current_val = self.gen.get(key, 0.6 if key == "temperature" else (0.95 if key == "top_p" else 2048))
            self.app.push_screen(ParamEditScreen(key, current_val, lo, hi, cast), self._on_param_edited)
        else:
            cap = get_model_capability(self.current_model, self.current_provider)
            if not cap or not cap.thinking:
                self.app.notify(
                    f"Thinking/reasoning is not supported by {self.current_model or 'this model'}",
                    severity="warning",
                    timeout=3.0,
                )
                return
            levels = cap.levels if cap and cap.levels else REASONING_LEVELS
            curr = self.gen.get("reasoning_level", cap.default_level or "Off")
            next_idx = (levels.index(curr) + 1) % len(levels) if curr in levels else 0
            self.gen["reasoning_level"] = levels[next_idx]
            self.settings.config["generation"] = self.gen
            self.settings.save_config()
            self._refresh_options()

    def _on_param_edited(self, result: Optional[Dict[str, Any]]) -> None:
        if result:
            key = result["key"]
            val = result["value"]
            self.gen[key] = val
            self.settings.config["generation"] = self.gen
            self.settings.save_config()
            self._refresh_options()
