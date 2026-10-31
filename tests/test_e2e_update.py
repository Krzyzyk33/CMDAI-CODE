"""End-to-end: a real git remote, a real update, the real app.

Every other test in the suite fakes `subprocess`. This one builds a bare repo,
clones it, tags two releases, runs the actual `handle_update` against it, and
launches the actual Textual app to confirm the notice appears.

It exists because it is the only layer that can catch the updater reading its
version from the wrong checkout. That bug was invisible to the fakes: the fake
returned whatever the test told it to, so "the version came from app_root" was
never really exercised. Against real git, the updater pulled the clone forward
and still reported the version of the directory holding the running code,
concluded "Already up to date", and showed nothing.
"""

import asyncio
import os
import subprocess

import pytest

from cmdai.core import releases as R
from cmdai.tui.app import CMDAICodeTUI
from cmdai.tui.modals.releases import UpdateNoticeModal

GIT_ENV = {
    "GIT_AUTHOR_NAME": "cmDAI tests",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "cmDAI tests",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
}

NOTICE_DELAY = 0.9


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _git_available(), reason="git is not installed")


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, timeout=60,
        env={**os.environ, **GIT_ENV},
    )


@pytest.fixture()
def remote_with_release(tmp_path):
    """A bare remote plus a clone, tagged v3.0.5-alpha."""
    work = tmp_path / "work"
    work.mkdir()
    remote = work / "remote.git"
    seed = work / "seed"
    clone = work / "clone"
    # Created up front: subprocess validates cwd before git gets a chance to
    # make the directory itself.
    remote.mkdir()
    seed.mkdir()

    _git(["init", "--bare", "-b", "main"], str(remote))
    _git(["init", "-b", "main"], str(seed))
    (seed / "f.txt").write_text("v1\n", encoding="utf-8")
    _git(["add", "-A"], str(seed))
    _git(["commit", "-m", "first"], str(seed))
    _git(["tag", "v3.0.5-alpha"], str(seed))
    _git(["remote", "add", "origin", str(remote)], str(seed))
    _git(["push", "origin", "main", "--tags"], str(seed))
    _git(["clone", str(remote), str(clone)], str(work))

    return {"work": work, "remote": remote, "seed": seed, "clone": clone}


def _publish_next_release(remote_with_release, tag):
    """Commit and tag a new release, then push it."""
    seed = remote_with_release["seed"]
    (seed / "f.txt").write_text("v2\n", encoding="utf-8")
    _git(["commit", "-am", "second"], str(seed))
    _git(["tag", tag], str(seed))
    _git(["push", "origin", "main", "--tags"], str(seed))
    return tag


def _describe(path):
    return (_git(["describe", "--tags", "--abbrev=0"], str(path)).stdout or "").strip()


def test_update_writes_a_flag_the_app_can_act_on(remote_with_release, tmp_path, monkeypatch):
    """The whole chain, on real git: pull moves, flag written, version correct.

    The `assert _describe(clone) == tag` before the flag check is the load
    bearing part: without it a run that pulled nothing could still pass by
    reporting a version change it did not cause.
    """
    import cmdai.cli as cli

    tag = _publish_next_release(remote_with_release, "v3.0.6-alpha")
    assert _describe(remote_with_release["clone"]) == "v3.0.5-alpha"

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(R, "_app_root", lambda: str(cache))
    monkeypatch.setattr(cli, "APP_ROOT", str(remote_with_release["clone"]))

    assert R.read_pending_notice() is None
    assert cli.handle_update() == 0

    assert _describe(remote_with_release["clone"]) == tag, "the pull did not land"
    assert R.read_pending_notice() == {"previous": "v3.0.5-alpha", "version": tag}


def test_an_up_to_date_clone_writes_no_flag(remote_with_release, tmp_path, monkeypatch):
    """Nothing new on the remote means nothing to announce."""
    import cmdai.cli as cli

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(R, "_app_root", lambda: str(cache))
    monkeypatch.setattr(cli, "APP_ROOT", str(remote_with_release["clone"]))

    assert cli.handle_update() == 0
    assert R.read_pending_notice() is None


def test_a_broken_remote_writes_no_flag(remote_with_release, tmp_path, monkeypatch):
    """A failed pull must not leave a notice announcing an update that never happened."""
    import cmdai.cli as cli

    # Repoint origin at a path that does not exist rather than deleting the
    # remote. On Windows a deleted bare repo keeps object files locked and
    # pytest's tmp_path cleanup then fails on them - a teardown failure that has
    # nothing to do with what is being asserted.
    clone = remote_with_release["clone"]
    _git(["remote", "set-url", "origin", str(remote_with_release["work"] / "gone.git")], str(clone))

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(R, "_app_root", lambda: str(cache))
    monkeypatch.setattr(cli, "APP_ROOT", str(clone))

    assert cli.handle_update() == 1
    assert R.read_pending_notice() is None


@pytest.mark.asyncio
async def test_the_app_shows_the_notice_after_a_real_update(remote_with_release, tmp_path,
                                                             monkeypatch):
    """Launch the real app against a real updated clone."""
    import cmdai.cli as cli

    tag = _publish_next_release(remote_with_release, "v3.0.6-alpha")
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(R, "_app_root", lambda: str(cache))
    monkeypatch.setattr(cli, "APP_ROOT", str(remote_with_release["clone"]))
    assert cli.handle_update() == 0
    assert R.read_pending_notice()["version"] == tag

    app = CMDAICodeTUI(workdir=str(remote_with_release["clone"]))
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(NOTICE_DELAY)
        await pilot.pause()
        assert isinstance(app.screen, UpdateNoticeModal), "no notice after a real update"
        assert tag in str(app.screen.query_one("#modal-title").content)
        assert R.read_pending_notice() is None, "the flag was not consumed"
        await pilot.press("escape")
        await pilot.pause()
