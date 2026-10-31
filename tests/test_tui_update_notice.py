"""Group K - the post-update notice inside the running app.

Drives the real Textual app headless. What matters here is not the rendering
but the state machine around it: a notice appears on the first launch after the
version moved, it carries the bundled release, and it does not come back on the
next launch. A regression that makes the popup sticky is the failure mode worth
guarding, because it is the one a user would notice immediately.
"""

import asyncio
import os

import pytest

from cmdai.core import releases as R
from cmdai.tui.app import CMDAICodeTUI
from cmdai.tui.modals.releases import UpdateNoticeModal

# on_mount arms the notice with set_timer(0.6, ...), so a pilot.pause() alone
# is not enough to let it fire.
NOTICE_DELAY = 0.9


@pytest.fixture()
def notice_cache(tmp_path, monkeypatch):
    """Keep the notice state out of the real checkout's cache/."""
    root = tmp_path / "notice"
    root.mkdir()
    monkeypatch.setattr(R, "_app_root", lambda: str(root))
    return root


def _run_app(workdir, seconds=NOTICE_DELAY):
    """Start the app, let timers run, yield the pilot."""
    return CMDAICodeTUI(workdir=str(workdir))


@pytest.mark.asyncio
async def test_a_pending_flag_opens_the_notice_on_the_next_launch(tmp_path, notice_cache):
    """The CLI updated, the app was closed, the app is opened: it shows up."""
    R.write_pending_notice("v3.0.2-alpha", "v3.0.3-alpha")

    app = _run_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert isinstance(app.screen, UpdateNoticeModal), "notice did not open"

        # Textual 8 exposes Static text as `.content`, not `.renderable`.
        title = str(app.screen.query_one("#modal-title").content)
        assert "v3.0.3-alpha" in title
        assert app.screen.version == "v3.0.3-alpha"
        assert app.screen.release["tag"] == R.get_bundled_release()["tag"]
        # The flag is consumed, so the next launch is quiet.
        assert R.read_pending_notice() is None
        await pilot.press("escape")
        await pilot.pause()

    assert R.read_pending_notice() is None


@pytest.mark.asyncio
async def test_the_second_launch_after_an_update_is_quiet(tmp_path, notice_cache, monkeypatch):
    """Shown once. Opening the app again must not bring it back.

    `get_display_version` is pinned because the real one reads the checkout's
    git tags, and a test that depends on which tags happen to exist is not a
    test.
    """
    R.write_pending_notice("v3.0.2-alpha", "v3.0.3-alpha")
    monkeypatch.setattr(R, "get_display_version", lambda: "v3.0.3-alpha")

    first = _run_app(tmp_path)
    async with first.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert isinstance(first.screen, UpdateNoticeModal)
        await pilot.press("escape")
        await pilot.pause()

    second = _run_app(tmp_path)
    async with second.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert not isinstance(second.screen, UpdateNoticeModal)


@pytest.mark.asyncio
async def test_a_checkout_newer_than_the_flag_is_still_noticed(tmp_path, notice_cache,
                                                               monkeypatch):
    """The flag is not the only truth: a newer local version still gets shown.

    The flag says what the update command reported. If the checkout has since
    moved past that - another pull, a tag switch - the app must notice that
    rather than trusting the older flag.
    """
    R.write_pending_notice("v3.0.2-alpha", "v3.0.3-alpha")
    monkeypatch.setattr(R, "get_display_version", lambda: "v3.0.4-alpha")

    first = _run_app(tmp_path)
    async with first.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert isinstance(first.screen, UpdateNoticeModal)
        # The flag named v3.0.3; the recorded baseline follows the checkout.
        assert R.read_last_seen_version() == "v3.0.3-alpha"
        await pilot.press("escape")
        await pilot.pause()

    second = _run_app(tmp_path)
    async with second.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert isinstance(second.screen, UpdateNoticeModal)
        assert second.screen.version == "v3.0.4-alpha"


@pytest.mark.asyncio
async def test_a_first_launch_with_no_history_stays_quiet(tmp_path, notice_cache):
    """A fresh install has nothing to announce, so nothing may pop up."""
    app = _run_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert not isinstance(app.screen, UpdateNoticeModal)


@pytest.mark.asyncio
async def test_a_version_moved_without_the_cli_is_still_noticed(tmp_path, notice_cache,
                                                                 monkeypatch):
    """The flag is the fast path; a hand-run `git pull` must also be caught.

    With no flag on disk the app compares the current version against the one
    recorded at the last launch, which is what covers updates the CLI did not
    perform.
    """
    R.mark_version_seen("v3.0.2-alpha")
    monkeypatch.setattr(R, "get_display_version", lambda: "v3.0.3-alpha")

    app = _run_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert isinstance(app.screen, UpdateNoticeModal)
        assert R.read_last_seen_version() == "v3.0.3-alpha"


@pytest.mark.asyncio
async def test_a_notice_opens_with_no_network(tmp_path, notice_cache, monkeypatch):
    """The content comes from the source copy, so nothing is fetched.

    Simulated by making any outbound request raise. A machine that has just
    updated is frequently offline or behind a proxy, and a notice that blocks
    on a timeout there is a notice the user never sees.
    """
    R.write_pending_notice("v3.0.2-alpha", "v3.0.3-alpha")

    import requests

    def _no_network(*a, **kw):
        raise requests.exceptions.ConnectionError("network is down")

    monkeypatch.setattr(requests, "get", _no_network)
    monkeypatch.setattr(requests, "post", _no_network)

    app = _run_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert isinstance(app.screen, UpdateNoticeModal), "notice needs the network"
        body = str(app.screen.query_one("#update-notice-body").content)
        assert body.strip(), "the notice rendered empty"
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_notes_from_an_older_release_are_labelled_not_misattributed(tmp_path,
                                                                         notice_cache,
                                                                         monkeypatch):
    """A checkout ahead of the bundled notes must not borrow their version.

    The bundled notes are regenerated at release time, so a checkout that moved
    past them - another pull, a tag switch - would otherwise get a notice
    headlined with a version it is not running. The headline stays truthful and
    the notes say which release they are for.
    """
    monkeypatch.setitem(R.BUNDLED_RELEASE, "tag", "v3.0.3-alpha")
    monkeypatch.setitem(R.BUNDLED_RELEASE, "body", "notes for three-oh-three")
    R.write_pending_notice("v3.0.3-alpha", "v3.0.4-alpha")

    app = _run_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert isinstance(app.screen, UpdateNoticeModal)
        title = str(app.screen.query_one("#modal-title").content)
        hint = str(app.screen.query_one("#update-notice-hint").content)
        assert "v3.0.4-alpha" in title
        assert "v3.0.3-alpha" not in title
        assert "v3.0.3-alpha" in hint
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_no_notes_label_when_the_versions_agree(tmp_path, notice_cache, monkeypatch):
    """Matching versions must not produce a redundant hint line."""
    monkeypatch.setitem(R.BUNDLED_RELEASE, "tag", "v3.0.3-alpha")
    R.write_pending_notice("v3.0.2-alpha", "v3.0.3-alpha")

    app = _run_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert isinstance(app.screen, UpdateNoticeModal)
        hint = str(app.screen.query_one("#update-notice-hint").content)
        assert hint == ""
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_a_corrupt_flag_does_not_break_startup(tmp_path, notice_cache):
    """A half-written flag must not stop the app or crash the notice path."""
    path = os.path.join(str(notice_cache), "cache", "pending_changelog.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"version": "v3.0.3-alpha"')  # truncated

    app = _run_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        # Treated as "no notice", and the app is fully usable.
        assert not isinstance(app.screen, UpdateNoticeModal)
        assert app.query_one("#hero-prompt-input") is not None
