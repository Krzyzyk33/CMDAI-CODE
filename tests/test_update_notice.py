"""Groups C and D - the post-update notice flag and version-change detection.

The notice has to survive three awkward moments: the machine that just updated
is often the one that cannot reach GitHub, the app may be started twice in a
row, and a user on a feature branch should not get a changelog popup for it.
These tests pin all three.
"""

import json
import os

import pytest

from cmdai.core import releases as R


@pytest.fixture()
def cache_root(tmp_path, monkeypatch):
    """Point every cache path at a throwaway directory.

    `_app_root` is derived from __file__, so it has to be redirected for the
    tests to stay off the real checkout's cache/.
    """
    root = tmp_path / "app"
    root.mkdir()
    monkeypatch.setattr(R, "_app_root", lambda: str(root))
    return root


# --------------------------------------------------------- C: the notice flag

def test_notice_flag_roundtrips(cache_root):
    """What the updater writes is exactly what the app will read back."""
    assert R.write_pending_notice("v3.0.2-alpha", "v3.0.3-alpha") is True
    assert R.read_pending_notice() == {
        "previous": "v3.0.2-alpha",
        "version": "v3.0.3-alpha",
    }


def test_notice_flag_lives_under_cache(cache_root):
    """It must not land at the repo root, where it would dirty the checkout."""
    R.write_pending_notice("v3.0.2-alpha", "v3.0.3-alpha")
    written = os.path.join(str(cache_root), "cache", "pending_changelog.json")
    assert os.path.isfile(written)
    with open(written, encoding="utf-8") as f:
        assert json.load(f)["version"] == "v3.0.3-alpha"


def test_reading_a_missing_notice_is_not_an_error(cache_root):
    assert R.read_pending_notice() is None


def test_clearing_removes_the_notice(cache_root):
    R.write_pending_notice("a", "b")
    R.clear_pending_notice()
    assert R.read_pending_notice() is None
    # Clearing again is a no-op, not a crash: the app clears on every launch.
    R.clear_pending_notice()


def test_a_corrupt_notice_is_ignored(cache_root):
    """A truncated write must not stop the app from starting."""
    path = os.path.join(str(cache_root), "cache", "pending_changelog.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json at all")
    assert R.read_pending_notice() is None


def test_a_notice_without_a_version_is_ignored(cache_root):
    path = os.path.join(str(cache_root), "cache", "pending_changelog.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"previous": "v3.0.2-alpha"}, f)
    assert R.read_pending_notice() is None


# ------------------------------------------------------- D: change detection

def test_first_run_stays_quiet_but_records_the_version(cache_root):
    """A fresh clone has no history, so there is nothing to report."""
    assert R.detect_version_change("v3.0.3-alpha") is None
    assert R.read_last_seen_version() == "v3.0.3-alpha"


def test_a_version_that_moved_forward_raises_a_notice(cache_root):
    R.mark_version_seen("v3.0.2-alpha")
    assert R.detect_version_change("v3.0.3-alpha") == {
        "previous": "v3.0.2-alpha",
        "version": "v3.0.3-alpha",
    }
    assert R.read_last_seen_version() == "v3.0.3-alpha"


def test_the_same_version_twice_raises_it_only_once(cache_root):
    """This is the "show it once" guarantee, and it is a state test.

    Opening the app again after dismissing the notice must not bring it back,
    which is why the version is recorded inside detect_version_change rather
    than by the caller.
    """
    R.mark_version_seen("v3.0.2-alpha")
    assert R.detect_version_change("v3.0.3-alpha") is not None
    assert R.detect_version_change("v3.0.3-alpha") is None
    assert R.detect_version_change("v3.0.3-alpha") is None


def test_an_unknown_current_version_stays_quiet(cache_root):
    """No resolvable version must not wipe the recorded one or fake a notice."""
    R.mark_version_seen("v3.0.2-alpha")
    assert R.detect_version_change("") is None
    assert R.read_last_seen_version() == "v3.0.2-alpha"


def test_a_downgrade_stays_quiet_but_is_recorded(cache_root):
    """Switching to an older tag or a feature branch is not a changelog event."""
    R.mark_version_seen("v3.0.3-alpha")
    assert R.detect_version_change("v3.0.2-alpha") is None
    # And it moved the baseline, so coming back is a forward move again.
    assert R.detect_version_change("v3.0.3-alpha") == {
        "previous": "v3.0.2-alpha",
        "version": "v3.0.3-alpha",
    }


def test_a_corrupt_last_seen_is_treated_as_a_first_run(cache_root):
    """A damaged baseline must not produce a notice for a bogus transition."""
    path = os.path.join(str(cache_root), "cache", "last_seen_version.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("[]")
    assert R.detect_version_change("v3.0.3-alpha") is None
    assert R.read_last_seen_version() == "v3.0.3-alpha"


def test_an_unresolvable_version_does_not_wipe_the_baseline(cache_root, monkeypatch):
    """A launch with no resolvable version must leave the baseline intact.

    Tags can be missing and the release cache can be cold, so a "" here is a
    gap in knowledge, not a rollback. Overwriting the baseline would cost the
    user the notice for the next real update.
    """
    R.mark_version_seen("v3.0.2-alpha")
    assert R.detect_version_change("") is None
    assert R.read_last_seen_version() == "v3.0.2-alpha"
    # And the next resolvable launch still sees the real transition.
    assert R.detect_version_change("v3.0.3-alpha") == {
        "previous": "v3.0.2-alpha",
        "version": "v3.0.3-alpha",
    }


def test_writes_are_best_effort_when_the_cache_is_unwritable(tmp_path, monkeypatch):
    """A cache that cannot be created must not stop the app from starting.

    Pointed at a path whose parent is a regular file, so every write raises and
    each helper has to swallow it.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("i am a file", encoding="utf-8")
    monkeypatch.setattr(R, "_app_root", lambda: str(blocker / "app"))

    assert R.write_pending_notice("a", "b") is False
    assert R.read_pending_notice() is None
    assert R.read_last_seen_version() == ""
    assert R.detect_version_change("v3.0.3-alpha") is None
    R.clear_pending_notice()
