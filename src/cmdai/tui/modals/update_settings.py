"""Update preferences and the window that edits them.

Automatic updates mean a `git pull` the user did not ask for, so the default
is off and every part of it is visible in one place: what is on, how often the
check happens, when it last ran, and the two actions.

`last_check` is stored rather than inferred, because "have we checked recently"
has to survive a restart to be worth anything.
"""

import time
from typing import Any, Dict, Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core.settings import get_settings

# Offered in this order; enter cycles to the next one.
INTERVALS = [1, 6, 24, 168]
INTERVAL_LABELS = {1: "1 hour", 6: "6 hours", 24: "24 hours", 168: "7 days"}


def get_update_config(settings=None) -> Dict[str, Any]:
    """The updates block from config, with defaults filled in.

    Read through `setdefault` on a copy so a config.json written by an older
    build, or one with no `updates` key at all, yields usable values.
    """
    cfg = (settings or get_settings()).config
    block = cfg.get("updates") or {}
    if not isinstance(block, dict):
        block = {}
    return {
        "auto_update": bool(block.get("auto_update", False)),
        "check_on_start": bool(block.get("check_on_start", True)),
        "interval_hours": int(block.get("interval_hours", 24) or 24),
        "last_check": float(block.get("last_check", 0) or 0),
    }


def save_update_config(values: Dict[str, Any], settings=None) -> bool:
    mgr = settings or get_settings()
    mgr.config.setdefault("updates", {}).update(values)
    return mgr.save_config()


def record_check(now: Optional[float] = None, settings=None) -> bool:
    """Note that a check just happened, so the interval starts from here."""
    return save_update_config({"last_check": float(now if now is not None else time.time())},
                              settings=settings)


def is_check_due(config: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> bool:
    """True when a startup check is enabled and the interval has elapsed.

    A `last_check` of zero means "never checked", so the first launch after
    enabling it checks immediately rather than waiting a full interval.
    """
    cfg = config if config is not None else get_update_config()
    if not cfg.get("check_on_start"):
        return False
    now = float(now if now is not None else time.time())
    last = float(cfg.get("last_check", 0) or 0)
    if last <= 0:
        return True
    hours = max(1, int(cfg.get("interval_hours", 24) or 24))
    return (now - last) >= hours * 3600.0


def format_last_check(config: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> str:
    """Human-readable age of the last check, for the read-only row."""
    cfg = config if config is not None else get_update_config()
    last = float(cfg.get("last_check", 0) or 0)
    if last <= 0:
        return "never"
    now = float(now if now is not None else time.time())
    delta = max(0, int(now - last))
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60} min ago"
    if delta < 86400:
        return f"{delta // 3600} h ago"
    return f"{delta // 86400} d ago"


class UpdateSettingsModal(ModalScreen[str]):
    """Toggles for automatic updates, plus the two run actions.

    Same shape as SettingsModal: an OptionList where enter acts on the
    highlighted row. `dismiss` carries the action back to the app so it can run
    the updater on the app's terms, not from inside the modal.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "activate", "Toggle"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config = get_update_config()

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="update-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Update Settings", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            yield OptionList(id="modal-list")
            with Horizontal(id="modal-footer"):
                yield Static("[enter: Toggle / Run]")
                yield Static("", id="modal-footer-right")

    def on_mount(self) -> None:
        self.rebuild_options()
        self.query_one("#modal-footer-right", Static).update(
            "[dim]a pull rewrites the working tree[/dim]")
        self.query_one("#modal-list", OptionList).focus()

    def rebuild_options(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        prev = ol.highlighted
        ol.clear_options()
        cfg = self.config
        hours = cfg["interval_hours"]
        hours_label = INTERVAL_LABELS.get(hours, f"{hours} hours")
        # No square brackets around the values: Textual reads those as markup
        # and drops them, which left the whole value column blank. The trailing
        # space is what the brackets used to supply visually - without it the
        # value runs straight into the next column.
        on = "[b #3fb950]ON[/] "
        off = "[dim]OFF[/] "
        ol.add_option(Option(
            f"  Automatic updates:      {on if cfg['auto_update'] else off}"
            "[dim]pull without asking[/dim]"))
        ol.add_option(Option(
            f"  Check on startup:       {on if cfg['check_on_start'] else off}"
            "[dim]offer updates at launch[/dim]"))
        # The interval and the last check are read-only values, not switches,
        # so they carry no ON/OFF marker.
        ol.add_option(Option(f"  Check interval:         [b #58a6ff]{hours_label}[/]"))
        ol.add_option(Option(f"  Last check:             [dim]{format_last_check(cfg)}[/dim]"))
        ol.add_option(Option("  Check now:              [b #58a6ff]Run[/]"))
        ol.add_option(Option("  Update now:             [b #58a6ff]Run[/]"))
        # OptionList ignores `enter` while `highlighted` is None: action_select
        # returns early, no OptionSelected is posted, and the row cannot be
        # changed at all. Default the cursor so the first row is live on open.
        ol.highlighted = prev if prev is not None else 0

    def action_cancel(self) -> None:
        self.dismiss("")

    def action_activate(self) -> None:
        ol = self.query_one("#modal-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            return

        if idx == 0:
            self.config["auto_update"] = not self.config["auto_update"]
            save_update_config(self.config)
        elif idx == 1:
            self.config["check_on_start"] = not self.config["check_on_start"]
            save_update_config(self.config)
        elif idx == 2:
            hours = self.config["interval_hours"]
            if hours in INTERVALS:
                nxt = INTERVALS[(INTERVALS.index(hours) + 1) % len(INTERVALS)]
            else:
                # A value written by an older build, or hand-edited config.
                nxt = INTERVALS[0]
            self.config["interval_hours"] = nxt
            save_update_config(self.config)
        elif idx == 4:
            record_check()
            self.dismiss("check")
            return
        elif idx == 5:
            record_check()
            self.dismiss("update")
            return
        self.rebuild_options()

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter on a row.

        The focused OptionList consumes `enter` before the screen binding sees
        it, so this is the path that actually runs; the binding is only reached
        if the list ever lets the key through.
        """
        self.action_activate()
