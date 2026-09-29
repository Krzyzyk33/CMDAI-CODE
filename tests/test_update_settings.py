"""Groups I and J - update preferences, the settings window, and the auto check.

Automatic updates run a `git pull` nobody asked for, so the defaults matter as
much as the toggles: off unless enabled, and a check that is due only when the
interval has genuinely elapsed.
"""

import asyncio
import time

import pytest

from cmdai.core.settings import DEFAULT_CONFIG, SettingsManager
from cmdai.tui.app import CMDAICodeTUI
from cmdai.tui.modals.update_settings import (
    INTERVALS,
    UpdateSettingsModal,
    format_last_check,
    get_update_config,
    is_check_due,
    record_check,
    save_update_config,
)
from textual.widgets import OptionList


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """A throwaway SettingsManager, so tests never touch the real config.json.

    Patched into every module that imports `get_settings` by name. Missing one
    is not a harmless test bug: SettingsModal and LoaderModal resolve
    `get_settings` from their own module namespace, so an unpatched module
    reads and *writes* the user's real config - which is how a test run ended
    up flipping the real `active_loader`.
    """
    from cmdai.core.settings import SettingsManager

    root = tmp_path / "app"
    root.mkdir()
    mgr = SettingsManager(base_dir=str(root))
    for module in (
        "cmdai.tui.modals.update_settings",
        "cmdai.tui.modals.settings",
    ):
        monkeypatch.setattr(f"{module}.get_settings", lambda: mgr)
    return mgr


@pytest.fixture(autouse=True)
def _never_touch_the_real_config():
    """Backstop: the real singleton must never be used or saved.

    Catches a test that reaches for `get_settings()` through some other path -
    a new modal, a helper - which the per-module patches above would miss.
    """
    import cmdai.core.settings as S

    original = S._instance
    S._instance = None
    try:
        yield
    finally:
        S._instance = original


# ------------------------------------------------------------- the config block

def test_defaults_are_conservative(settings):
    """No automatic pulls and no surprise traffic until the user opts in."""
    cfg = get_update_config(settings)
    assert cfg["auto_update"] is False
    assert cfg["check_on_start"] is True
    assert cfg["interval_hours"] == 24
    assert cfg["last_check"] == 0.0


def test_a_config_without_the_block_still_yields_defaults(settings):
    """An older build's config.json has no `updates` key at all."""
    settings.config.pop("updates", None)
    assert get_update_config(settings)["auto_update"] is False


def test_a_malformed_block_falls_back_to_defaults(settings):
    settings.config["updates"] = "not a dict"
    cfg = get_update_config(settings)
    assert cfg["auto_update"] is False
    assert cfg["interval_hours"] == 24


def test_saving_one_setting_does_not_leak_into_the_defaults(settings, tmp_path):
    """Regression: the defaults are deep-copied, not shared.

    `_load_config` used `dict(DEFAULT_CONFIG)`, a shallow copy, so every
    SettingsManager received the same nested dict objects. Toggling automatic
    updates wrote straight into DEFAULT_CONFIG, which then leaked into every
    manager built afterwards in the process - a second installation would come
    up with automatic updates already on, and the file it writes would start
    from a value the user never chose.
    """
    save_update_config({"auto_update": True}, settings)
    assert DEFAULT_CONFIG["updates"]["auto_update"] is False

    from cmdai.core.settings import DEFAULT_CONFIG as fresh
    assert fresh["updates"]["auto_update"] is False

    other_root = tmp_path / "other"
    other_root.mkdir()
    other = SettingsManager(base_dir=str(other_root))
    assert other.config["updates"]["auto_update"] is False
    assert other.config["updates"] is not settings.config["updates"]


def test_values_round_trip_through_config_json(settings):
    save_update_config({"auto_update": True, "interval_hours": 6}, settings)
    reloaded = get_update_config(settings)
    assert reloaded["auto_update"] is True
    assert reloaded["interval_hours"] == 6
    # And it reached the disk, not just the in-memory config.
    with open(settings.config_path, encoding="utf-8") as f:
        on_disk = f.read()
    assert '"auto_update": true' in on_disk
    assert '"interval_hours": 6' in on_disk


# ------------------------------------------------------------------ the interval

def test_the_interval_cycles_through_every_option(settings):
    seen = []
    hour = 24
    for _ in range(len(INTERVALS)):
        pos = INTERVALS.index(hour)
        seen.append(hour)
        hour = INTERVALS[(pos + 1) % len(INTERVALS)]
    assert sorted(seen) == sorted(INTERVALS)
    assert hour == 24, "the cycle must come back around"


@pytest.mark.parametrize("now,last,expected", [
    (1000.0, 0.0, True),          # never checked -> due immediately
    (1000.0, 900.0, False),       # 100s ago, interval 1h
    (1000.0, 100.0, False),       # 900s ago, still under 1h
    (5000.0, 100.0, True),        # 4900s ago, past 1h
    (5000.0, 1000.0, True),       # exactly 4000s ago
])
def test_is_check_due_respects_the_interval(now, last, expected):
    cfg = {"check_on_start": True, "interval_hours": 1, "last_check": last}
    assert is_check_due(cfg, now=now) is expected


def test_is_check_due_is_false_when_the_check_is_off():
    cfg = {"check_on_start": False, "interval_hours": 1, "last_check": 0.0}
    assert is_check_due(cfg, now=1000.0) is False


def test_a_longer_interval_waits_longer(settings):
    save_update_config({"interval_hours": 24}, settings)
    cfg = dict(get_update_config(settings), last_check=time.time() - 3600.0)
    assert is_check_due(cfg) is False
    cfg["last_check"] = time.time() - 25 * 3600.0
    assert is_check_due(cfg) is True


def test_recording_a_check_starts_the_interval(settings):
    record_check(now=5000.0, settings=settings)
    assert get_update_config(settings)["last_check"] == 5000.0
    assert is_check_due(get_update_config(settings), now=5000.0) is False
    assert is_check_due(get_update_config(settings), now=5000.0 + 25 * 3600.0) is True


def test_a_zero_interval_is_treated_as_one_hour(settings):
    """A hand-edited 0 must not mean "check on every keystroke"."""
    cfg = {"check_on_start": True, "interval_hours": 0, "last_check": 0.0}
    assert is_check_due(cfg, now=1000.0) is True
    cfg = {"check_on_start": True, "interval_hours": 0, "last_check": 999.0}
    assert is_check_due(cfg, now=1000.0) is False


# ------------------------------------------------------------- last check label

def test_the_last_check_label_reads_naturally():
    now = 1_000_000.0
    assert format_last_check({"last_check": 0}, now=now) == "never"
    assert format_last_check({"last_check": now - 5}, now=now) == "just now"
    assert format_last_check({"last_check": now - 300}, now=now) == "5 min ago"
    assert format_last_check({"last_check": now - 7200}, now=now) == "2 h ago"
    assert format_last_check({"last_check": now - 3 * 86400}, now=now) == "3 d ago"


def test_a_future_last_check_does_not_render_a_negative_age():
    """Clock skew must not produce "-1 h ago"."""
    assert format_last_check({"last_check": 1_000_500.0}, now=1_000_000.0) == "just now"


# ------------------------------------------------------------- the settings window

@pytest.mark.asyncio
async def test_the_settings_window_lists_every_control(settings):
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(UpdateSettingsModal())
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, UpdateSettingsModal)
        assert screen.query_one("#modal-list").option_count == 6
        await pilot.press("escape")
        await pilot.pause()


def _rows(screen):
    ol = screen.query_one("#modal-list")
    return [ol.get_option_at_index(i).prompt for i in range(ol.option_count)]


def _visible(prompt: str) -> str:
    """The text a user actually sees, with Textual markup removed."""
    import re
    return re.sub(r"\[[^\]]*\]", "", str(prompt))


def test_no_value_is_swallowed_by_markup(settings, monkeypatch):
    """Regression: square brackets around values made them disappear.

    Textual parses `[...]` as markup, so `[ON]`, `[OFF]` and `[Run]` were
    dropped and the value column rendered empty - the window looked broken and
    gave no sign of what was set.
    """
    import re

    modal = UpdateSettingsModal()
    rows = list(_iter_prompts(modal))
    visible = [_visible(r) for r in rows]
    joined = " ".join(visible)
    assert "ON" in joined or "OFF" in joined
    for line in visible:
        assert "[" not in line, f"literal bracket survived: {line!r}"
    # The two switches read as states, the two actions as actions.
    assert any("ON" in v or "OFF" in v for v in visible)
    assert sum(1 for v in visible if "Run" in v) == 2
    # And the value is not glued to the label or to the next column.
    assert not any(re.search(r"(ON|OFF)\S", v) for v in visible), visible


def _iter_prompts(modal):
    """Prompts as compose_options would render them, without a live app."""
    cfg = modal.config
    saved = {}

    class _Fake:
        def __init__(self):
            self.items = []
            self.highlighted = None

        def clear_options(self):
            self.items = []

        def add_option(self, opt):
            self.items.append(opt)

    fake = _Fake()
    real_query = modal.query_one
    modal.query_one = lambda *a, **k: fake
    try:
        modal.rebuild_options()
    finally:
        modal.query_one = real_query
    saved["cfg"] = cfg
    return [o.prompt for o in fake.items]


def test_the_interval_row_is_not_marked_as_a_switch(settings):
    """It is a value, not a switch, so it must not carry an ON/OFF label."""
    modal = UpdateSettingsModal()
    rows = {i: _visible(p) for i, p in enumerate(_iter_prompts(modal))}
    assert "24 hours" in rows[2]
    assert "ON" not in rows[2] and "OFF" not in rows[2]
    assert "never" in rows[3]


def test_toggles_render_their_state(settings):
    """On and off both have to be visible, which is the whole point."""
    import re as _re
    from cmdai.tui.modals.settings import SettingsModal

    class _Fake:
        def __init__(self):
            self.items = []
            self.highlighted = None

        def clear_options(self):
            self.items = []

        def add_option(self, opt):
            self.items.append(opt)

    modal = SettingsModal()
    fake = _Fake()
    modal.query_one = lambda *a, **k: fake
    modal.rebuild_options()
    visible = [_visible(o.prompt) for o in fake.items]
    assert any("ON" in v or "OFF" in v for v in visible)
    assert all("[" not in v for v in visible), visible
    assert not any(_re.search(r"(ON|OFF)\S", v) for v in visible), visible
    assert any("Run" in v for v in visible)


@pytest.mark.asyncio
async def test_enter_actually_changes_a_setting(settings):
    """Regression: the window was dead to the keyboard.

    `rebuild_options` left `highlighted` as None, and OptionList ignores
    `enter` in that state - `action_select` returns early, so no
    OptionSelected was posted and the row never changed. The earlier tests
    called `action_activate()` directly and passed, which is exactly why the
    bug survived: nothing here touched a key.
    """
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(UpdateSettingsModal())
        await pilot.pause()
        ol = app.screen.query_one(OptionList)

        assert ol.highlighted == 0, "no row is live on open"
        before = get_update_config(settings)["auto_update"]

        await pilot.press("enter")
        await pilot.pause()
        assert get_update_config(settings)["auto_update"] is not before

        # The row itself has to show the new state, not just the config file.
        shown = _visible(_row(app.screen, 0))
        assert ("ON" in shown) is (not before)

        # And it toggles back.
        await pilot.press("enter")
        await pilot.pause()
        assert get_update_config(settings)["auto_update"] is before
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_arrows_move_the_cursor_and_enter_applies_to_that_row(settings):
    """The highlighted row is the one that changes, and the user can move it."""
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(UpdateSettingsModal())
        await pilot.pause()
        ol = app.screen.query_one(OptionList)

        await pilot.press("down")
        await pilot.pause()
        assert ol.highlighted == 1
        before = get_update_config(settings)["check_on_start"]
        await pilot.press("enter")
        await pilot.pause()
        assert get_update_config(settings)["check_on_start"] is not before
        # Automatic updates were left alone.
        assert get_update_config(settings)["auto_update"] is False
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_enter_on_check_now_runs_the_check(settings):
    """The action rows have to work from the keyboard too."""
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        actions = []
        app.push_screen(UpdateSettingsModal(), lambda a: actions.append(a))
        await pilot.pause()
        for _ in range(3):
            await pilot.press("down")
        await pilot.pause()
        assert app.screen.query_one(OptionList).highlighted == 3
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert actions == ["check"]
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_enter_works_in_the_general_settings_window(settings):
    """Same latent bug in /settings, which had the same missing cursor."""
    from cmdai.tui.modals.settings import SettingsModal

    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(SettingsModal())
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        assert ol.highlighted == 0

        before = settings.config["settings"].get("confirm_exit", False)
        await pilot.press("enter")
        await pilot.pause()
        assert settings.config["settings"].get("confirm_exit", False) is not before
        assert ("ON" in _visible(_row(app.screen, 0))) is (not before)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_enter_works_in_the_loader_window(settings):
    """The loader list had no default cursor either."""
    from cmdai.tui.modals.settings import LoaderModal

    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(LoaderModal())
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        assert ol.highlighted == 0
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert settings.config.get("active_loader") == "cuda"
        await pilot.press("escape")
        await pilot.pause()


def _row(screen, index: int) -> str:
    ol = screen.query_one("#modal-list")
    return ol.get_option_at_index(index).prompt


@pytest.mark.asyncio
async def test_toggling_persists_to_config(settings):
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(UpdateSettingsModal())
        await pilot.pause()
        screen = app.screen
        screen.query_one("#modal-list").highlighted = 0
        screen.action_activate()
        await pilot.pause()
        assert get_update_config(settings)["auto_update"] is True
        assert screen.config["auto_update"] is True
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_the_interval_row_cycles_and_saves(settings):
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(UpdateSettingsModal())
        await pilot.pause()
        screen = app.screen
        screen.query_one("#modal-list").highlighted = 2
        start = screen.config["interval_hours"]
        screen.action_activate()
        await pilot.pause()
        changed = get_update_config(settings)["interval_hours"]
        assert changed != start
        assert changed in INTERVALS
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_the_check_row_dismisses_with_an_action(settings):
    """"Check now" has to reach the app, and must record the attempt."""
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        actions = []
        app.push_screen(UpdateSettingsModal(), lambda a: actions.append(a))
        await pilot.pause()
        screen = app.screen
        screen.query_one("#modal-list").highlighted = 4
        screen.action_activate()
        await pilot.pause()
        assert actions == ["check"]
        assert get_update_config(settings)["last_check"] > 0
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_the_update_row_dismisses_with_an_action(settings):
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        actions = []
        app.push_screen(UpdateSettingsModal(), lambda a: actions.append(a))
        await pilot.pause()
        screen = app.screen
        screen.query_one("#modal-list").highlighted = 5
        screen.action_activate()
        await pilot.pause()
        assert actions == ["update"]
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_escape_dismisses_with_no_action(settings):
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        actions = []
        app.push_screen(UpdateSettingsModal(), lambda a: actions.append(a))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert actions == [""]


# ------------------------------------------------------------- the startup check

@pytest.mark.asyncio
async def test_the_startup_check_is_skipped_when_off(settings, monkeypatch):
    """No network traffic at all when the user turned the check off."""
    save_update_config({"check_on_start": False}, settings)
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        called = []
        monkeypatch.setattr(app, "_check_for_updates", lambda: called.append(True))
        app._maybe_auto_update_check()
        await pilot.pause()
        assert called == []


@pytest.mark.asyncio
async def test_the_startup_check_runs_when_due(settings, monkeypatch):
    save_update_config({"check_on_start": True, "last_check": 0}, settings)
    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        called = []
        monkeypatch.setattr(app, "_check_for_updates", lambda: called.append(True))
        app._maybe_auto_update_check()
        await pilot.pause()
        assert called == [True]


@pytest.mark.asyncio
async def test_the_check_reports_nothing_when_already_current(settings, monkeypatch):
    """An up-to-date installation must not produce a notification."""
    from cmdai.core import releases as RR
    bundled = RR.get_bundled_release()
    monkeypatch.setattr(RR, "get_releases", lambda refresh=True: ([
        dict(bundled, body="remote copy")], "github"))

    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        notes = []
        monkeypatch.setattr(app, "notify", lambda *a, **k: notes.append(a))
        app._check_for_updates()
        for _ in range(40):
            await asyncio.sleep(0.02)
            if notes:
                break
        await pilot.pause()
        assert notes, "the check said nothing at all"
        assert "up to date" in notes[0][0]


@pytest.mark.asyncio
async def test_the_check_announces_a_newer_release(settings, monkeypatch):
    from cmdai.core import releases as RR
    monkeypatch.setattr(RR, "get_releases", lambda refresh=True: ([
        {"tag": "v99.0.0-alpha", "name": "future", "body": "notes",
         "published_at": "2026-12-01"}], "github"))

    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        notes = []
        monkeypatch.setattr(app, "notify", lambda *a, **k: notes.append(a))
        app._check_for_updates()
        for _ in range(40):
            await asyncio.sleep(0.02)
            if notes:
                break
        await pilot.pause()
        assert notes, "a newer release was not announced"
        assert "v99.0.0-alpha" in notes[0][0]
        assert "/update" in notes[0][0]


@pytest.mark.asyncio
async def test_the_check_survives_no_network(settings, monkeypatch):
    """A failed fetch is silent, not an error toast."""
    from cmdai.core import releases as RR
    def _boom(refresh=True):
        raise RuntimeError("network is down")
    monkeypatch.setattr(RR, "get_releases", _boom)

    app = CMDAICodeTUI(workdir=".")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        notes = []
        monkeypatch.setattr(app, "notify", lambda *a, **k: notes.append(a))
        app._check_for_updates()
        for _ in range(20):
            await asyncio.sleep(0.02)
        await pilot.pause()
        assert notes == []
