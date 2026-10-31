"""Wiring between the updater and the post-update notice.

`cmdai code update` runs the updater and, when the version actually moved,
leaves a flag for the next launch. These tests sit on both sides of that: the
flag has to be written exactly when it is meaningful, and the app has to read
it, act on it once, and stay quiet otherwise.
"""

import pytest

from cmdai.cli import handle_update
from cmdai.core import releases as R
from tests.harness import (
    FakeAnimator,
    FakeResult,
    FakeSubprocess,
    install_fake_subprocess,
    patch_update_env,
)
from tests.test_update_characterization import fake_app_root


@pytest.fixture()
def notice_cache(tmp_path, monkeypatch):
    """Redirect the notice cache into a throwaway directory."""
    root = tmp_path / "notice"
    root.mkdir()
    monkeypatch.setattr(R, "_app_root", lambda: str(root))
    return root


# ------------------------------------------------------ the updater writes it

def test_a_version_change_leaves_a_notice(monkeypatch, tmp_path, notice_cache):
    """The whole feature: update lands, next launch has something to show."""
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    patch_update_env(monkeypatch, anim,
                     versions=["v3.0.2-alpha", "v3.0.3-alpha"],
                     app_root=fake_app_root(tmp_path))

    assert handle_update() == 0
    assert R.read_pending_notice() == {
        "previous": "v3.0.2-alpha",
        "version": "v3.0.3-alpha",
    }


def test_an_unchanged_version_leaves_no_notice(monkeypatch, tmp_path, notice_cache):
    """Pulling when already current must not nag the user on next launch."""
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    patch_update_env(monkeypatch, anim, versions=["v3.0.3-alpha"],
                     app_root=fake_app_root(tmp_path))

    assert handle_update() == 0
    assert R.read_pending_notice() is None


def test_a_failed_pull_leaves_no_notice(monkeypatch, tmp_path, notice_cache):
    """Nothing landed, so there is nothing to announce."""
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess().on(
        "pull", returncode=1, stderr="fatal: no tracking information"))
    patch_update_env(monkeypatch, anim,
                     versions=["v3.0.2-alpha", "v3.0.3-alpha"],
                     app_root=fake_app_root(tmp_path))

    assert handle_update() == 1
    assert R.read_pending_notice() is None


def test_the_version_is_read_from_the_checkout_being_updated(monkeypatch, tmp_path,
                                                              notice_cache):
    """The reported version must come from app_root, not from the code's own tree.

    Found by the end-to-end run against a real git remote: the updater pulled
    the clone from v3.0.5-alpha to v3.0.6-alpha, but the version it reported
    came from the directory holding the running package. Before and after were
    then identical, so the run concluded "Already up to date" and no notice was
    ever shown - the feature silently did nothing.
    """
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    root = fake_app_root(tmp_path)
    roots = patch_update_env(monkeypatch, anim,
                             versions=["v3.0.5-alpha", "v3.0.6-alpha"],
                             app_root=root)

    assert handle_update() == 0
    assert roots, "the version was never resolved"
    assert set(roots) == {str(root)}, f"read the version from {set(roots)}, not the updated checkout"
    assert R.read_pending_notice()["version"] == "v3.0.6-alpha"


def test_a_failed_dependency_sync_still_leaves_a_notice(monkeypatch, tmp_path, notice_cache):
    """pip failing does not mean the update did not land.

    The code is already pulled at that point, so the changelog is still worth
    showing. Confirms the flag is tied to the pull, not to the whole run.
    """
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess().on_argv(
        "pip install", lambda argv: FakeResult(1, stderr="ERROR: boom")))
    patch_update_env(monkeypatch, anim,
                     versions=["v3.0.2-alpha", "v3.0.3-alpha"],
                     app_root=fake_app_root(tmp_path))

    assert handle_update() == 0
    assert R.read_pending_notice()["version"] == "v3.0.3-alpha"


def test_a_missing_git_leaves_no_notice(monkeypatch, tmp_path, notice_cache):
    from tests.harness import GitMissing

    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    patch_update_env(monkeypatch, anim,
                     versions=["v3.0.2-alpha", "v3.0.3-alpha"],
                     app_root=fake_app_root(tmp_path))

    with GitMissing():
        assert handle_update() == 1
    assert R.read_pending_notice() is None


# ------------------------------------------------------ the app reads it once

def test_the_notice_resolves_to_the_bundled_release(notice_cache):
    """Whatever wrote the flag, the content comes from the source copy.

    This is what makes the notice work offline, which is the common case
    right after an update.
    """
    R.write_pending_notice("v3.0.2-alpha", "v3.0.3-alpha")
    pending = R.read_pending_notice()
    R.clear_pending_notice()

    release = R.get_bundled_release()
    assert pending["version"] == "v3.0.3-alpha"
    assert release["body"], "the bundled release must carry the notes"
    # And the flag is consumed, so a second launch stays quiet.
    assert R.read_pending_notice() is None


def test_two_launches_raise_the_notice_once(notice_cache):
    """First launch notices the change, the second does not.

    Modelled the way the app does it: read the flag, or fall back to comparing
    against the recorded version, then record the version seen.
    """
    R.mark_version_seen("v3.0.2-alpha")

    def launch():
        pending = R.read_pending_notice()
        if pending:
            R.clear_pending_notice()
            R.mark_version_seen(pending["version"])
            return pending["version"]
        change = R.detect_version_change("v3.0.3-alpha")
        return change["version"] if change else None

    assert launch() == "v3.0.3-alpha"
    assert launch() is None
    assert launch() is None


def test_a_notice_written_by_the_cli_is_seen_without_a_version_record(notice_cache):
    """The flag is authoritative even with no baseline on disk.

    A user who installed, updated via the CLI, and has never recorded a launch
    still gets the notice.
    """
    R.write_pending_notice("v3.0.2-alpha", "v3.0.3-alpha")
    assert R.read_last_seen_version() == ""

    pending = R.read_pending_notice()
    R.clear_pending_notice()
    R.mark_version_seen(pending["version"])
    assert pending["version"] == "v3.0.3-alpha"
    # And the next launch is quiet.
    assert R.detect_version_change("v3.0.3-alpha") is None
