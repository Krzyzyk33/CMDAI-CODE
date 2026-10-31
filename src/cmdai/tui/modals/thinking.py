"""`/thinking`: pick a thinking level, the same shape as `/models`.

Two screens have the same shape - a list of things you choose from, an arrow
key, enter to take - and building one on top of the other is what stopped this
from being a new idea. The difference is what is in the rows: a model name, or an
effort level, and a model that cannot reason has nothing to pick.

The picker shows the level and what it costs. `medium` and `low` differ by a few
seconds and nothing else visible; `max` on a model that bills reasoning tokens
separately costs real money per turn. Showing the number next to the number you
are about to choose is the whole reason this is a screen rather than a key.
"""

from typing import Any, Dict, List, Optional

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ...core.capabilities import (
    BUDGET_MAP,
    budget_for,
    cycle_order,
    effort_budget_field,
    get_model_capability,
    is_local_provider,
    normalize_level,
)


class ThinkingSearchInput(Input):
    """Arrows and enter belong to the list; the typing belongs here."""

    def on_key(self, event) -> None:
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
            screen.action_select()


class ThinkingModal(ModalScreen[Optional[str]]):
    """Choose a thinking level. Returns the canonical name, or None."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "select", "Select"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, current_level: str = "", model_id: str = "",
                 provider_id: str = "", cap=None, **kwargs):
        super().__init__(**kwargs)
        self.model_id = model_id
        self.provider_id = provider_id
        self.current_level = normalize_level(current_level)
        self.filtered_items: List[str] = []

        # The caller passes what it already detected. Looking it up again here
        # would mean a second probe - and, worse, a second answer: a modal that
        # disagrees with the bar behind it is worse than no modal at all.
        if cap is None:
            cap = get_model_capability(model_id, provider_id=provider_id)
        self.cap = cap
        self.levels = cycle_order(cap.levels) if (cap and cap.thinking) else []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Thinking level", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")

            if not self.levels:
                yield Static(
                    f"[dim]{self.model_id or 'This model'} does not reason, "
                    f"so there is no level to choose.[/]",
                    id="thinking-empty",
                )
            else:
                yield ThinkingSearchInput(
                    placeholder="Type to filter levels, or pick one...",
                    id="provider-search",
                )
                yield OptionList(id="modal-list")

            with Horizontal(id="modal-footer"):
                yield Static(self._wire_note())
                yield Static("[enter: Select]  [Ctrl+T: next level]", id="modal-footer-right")

    def _budget_for(self, level: str) -> int:
        """The number that will actually be sent for this level.

        Not `BUDGET_MAP[level]`. A local model thinks out of its own window, so
        its budgets are a share of the context it reported - and printing the
        fixed ladder next to those is how you get a picker that says 131,072
        beside a level that goes out as 24,576.

        Same function the request path uses, so the two cannot disagree.
        """
        context = self.cap.context_limit if self.cap else 0
        return budget_for(self.provider_id, level, context)

    def _wire_note(self) -> str:
        """What this model actually receives, in one line.

        A number next to `max` is only worth printing if the number goes
        somewhere. Anthropic takes a budget, so the ladder is real there. A
        model that only accepts an effort *name* gets `high` for `high`, `extra`
        and `max` alike, and saying so is more use than three rows of figures
        that never leave the process.
        """
        if not self.levels:
            return ""
        style = self.cap.param_style if self.cap else ""

        # A local model's figures are a share of its own window, and that is a
        # different question from which field they land in. Saying so is the
        # difference between "12,288 tokens" and a number you cannot account for.
        context = self.cap.context_limit if self.cap else 0
        if is_local_provider(self.provider_id) and context > 0:
            max_b = self._budget_for("max")
            return f"[dim]reasoning tokens capped at {max_b:,} (model context: {context:,})[/]"

        if style == "budget_tokens":
            return "[dim]sends: thinking.budget_tokens - standard Anthropic Claude ladder[/]"
        if style == "chat_template_kwargs":
            return "[dim]sends: thinking_budget - standard reasoning ladder[/]"
        if style == "reasoning_effort":
            field = effort_budget_field(self.provider_id)
            if field == "__nested_reasoning__":
                return "[dim]sends: reasoning.effort + reasoning.max_tokens[/]"
            if field:
                return f"[dim]sends: reasoning_effort + {field}[/]"
            return ('[dim]sends: reasoning_effort (OpenAI format: low / medium / high)[/]')
        return "[dim]this model reasons on its own; no level is sent[/]"

    def on_mount(self) -> None:
        if self.levels:
            self.rebuild()
            self.query_one("#modal-list", OptionList).focus()

    def _search_term(self) -> str:
        try:
            return self.query_one("#provider-search", ThinkingSearchInput).value.strip().lower()
        except Exception:
            return ""

    def rebuild(self) -> None:
        from ...core.capabilities import REASONING_EFFORT_MAP
        term = self._search_term()
        ol = self.query_one("#modal-list", OptionList)
        prev = ol.highlighted
        ol.clear_options()
        self.filtered_items.clear()
        style = self.cap.param_style if self.cap else ""

        for name in self.levels:
            if term and term not in name:
                continue
            text = Text()
            text.append("  ")
            if name == self.current_level:
                text.append(name, style="bold #58a6ff")
            else:
                text.append(name, style="white")
            budget = self._budget_for(name)
            if style == "reasoning_effort":
                effort = REASONING_EFFORT_MAP.get(name, name)
                text.append(f"   effort: {effort}", style="dim #6e7681")
                if budget:
                    text.append(f" ({budget:,} tokens)", style="dim #484f58")
            elif budget:
                text.append(f"   {budget:,} tokens", style="dim #6e7681")
            if name == self.current_level:
                text.append("   current", style="dim #58a6ff")
            ol.add_option(Option(text, id=f"lvl_{name}"))
            self.filtered_items.append(name)

        if not self.filtered_items:
            ol.add_option(Option(Text("  nothing matches", style="dim #8b949e"),
                                 id="none", disabled=True))
            self.filtered_items.append("")

        if prev is not None and prev < len(self.filtered_items):
            ol.highlighted = prev
        elif self.filtered_items:
            ol.highlighted = 0

    # ------------------------------------------------------------- actions

    def _current(self) -> str:
        ol = self.query_one("#modal-list", OptionList)
        if ol.highlighted is None or ol.highlighted >= len(self.filtered_items):
            return ""
        return self.filtered_items[ol.highlighted]

    def action_cursor_up(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#modal-list", OptionList).action_cursor_down()

    def action_select(self) -> None:
        name = self._current()
        self.dismiss(name or None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Changed, "#provider-search")
    def on_search_changed(self, event: Input.Changed) -> None:
        self.rebuild()

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_select()