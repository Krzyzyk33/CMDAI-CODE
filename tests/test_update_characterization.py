"""Group G - characterization of the current update flow.

These tests pin the behavior of `cmdai.cli.handle_update` exactly as it
behaves today, before any of it moves into `core/updater.py`. The point is to
make the move verifiable: when the logic is relocated, these same assertions
must stay green. Anything that fails here is a bug found before the refactor,
not a bug the refactor introduced.

The behavior being pinned is deliberately quirky in places - see the docstring
on each test for the "why this matters" note.
"""

import os

import pytest

from cmdai.cli import handle_update
from tests.harness import (
    FakeAnimator,
    FakeResult,
    FakeSubprocess,
    GitMissing,
    fake_app_root,
    install_fake_subprocess,
    patch_update_env,
)

# --------------------------------------------------------------------- git missing

def test_git_missing_returns_1_and_runs_no_steps(monkeypatch, tmp_path, capsys):
    """No git -> exit 1, a pointer at the clone URL, and not one step run.

    The check must stay ahead of the animation: a machine without git should
    fail instantly rather than spin a progress bar for a step that cannot run.
    """
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    patch_update_env(monkeypatch, anim, app_root=fake_app_root(tmp_path))

    with GitMissing():
        rc = handle_update()

    assert rc == 1
    out = capsys.readouterr().out
    assert "Git is not installed" in out
    assert "git clone" in out
    assert anim.labels() == []


# ------------------------------------------------------------------ step sequence

def test_steps_run_in_order_with_exact_parameters(monkeypatch, tmp_path):
    """Pull, then pip, then version refresh - with today's timings and chrome.

    The reporter receives (min_time, timeout, header, gap) per step. They are
    asserted because the TUI will drive the same runner with a progress widget
    and a step with the wrong timeout silently hangs for 5 minutes.
    """
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    patch_update_env(monkeypatch, anim, app_root=fake_app_root(tmp_path))

    assert handle_update() == 0

    assert anim.labels() == [
        "Pulling latest changes",
        "Synchronizing packages",
        "Refreshing version",
    ]
    assert anim.steps[0] == {
        "label": "Pulling latest changes",
        "min_time": 1.2,
        "timeout": 300.0,
        "header": "update cmdai code",
        "gap": 3,
    }
    assert anim.steps[1] == {
        "label": "Synchronizing packages",
        "min_time": 1.0,
        "timeout": 900.0,
        "header": "",
        "gap": 3,
    }
    assert anim.steps[2] == {
        "label": "Refreshing version",
        "min_time": 1.0,
        "timeout": 120.0,
        "header": "",
        "gap": 0,
    }


def test_pip_step_skipped_when_no_requirements_file(monkeypatch, tmp_path):
    """A checkout without requirements.txt must skip the pip step entirely.

    Not a no-op step: skipping avoids a failing `pip install` against a missing
    path, which today would surface as "Error: dependency sync failed".
    """
    anim = FakeAnimator()
    fake = install_fake_subprocess(monkeypatch, FakeSubprocess())
    root = fake_app_root(tmp_path, with_requirements=False)
    patch_update_env(monkeypatch, anim, app_root=root)

    assert handle_update() == 0
    assert "Synchronizing packages" not in anim.labels()
    assert not fake.ran("pip install")


def test_pull_uses_rebase_autostash(monkeypatch, tmp_path):
    """The pull flags are load-bearing: a dirty tree must not abort the update."""
    fake = install_fake_subprocess(monkeypatch, FakeSubprocess())
    patch_update_env(monkeypatch, FakeAnimator(), app_root=fake_app_root(tmp_path))

    handle_update()
    assert fake.argv_for("pull") == ["git", "pull", "--rebase", "--autostash"]


# ------------------------------------------------------------------------ failures

@pytest.mark.parametrize("stderr,expected_hint", [
    ("fatal: dubious ownership in directory", "safe.directory"),
    ("fatal: no tracking information for the current branch", "--set-upstream-to"),
    ("error: Your local changes to the following files would be overwritten", "git stash push"),
    ("error: would be overwritten by merge", "git stash push"),
])
def test_pull_failure_prints_the_matching_hint(monkeypatch, tmp_path, capsys,
                                               stderr, expected_hint):
    """Each pull failure mode gets its own actionable hint, then exit 1.

    Asserted per branch because the hint is chosen by substring match on the
    git output, and a rewritten matcher that drops one branch degrades to the
    generic "update failed" with nothing to act on.
    """
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess(
        fallback=FakeResult(0)
    ).on("pull", returncode=1, stderr=stderr))
    patch_update_env(monkeypatch, anim, app_root=fake_app_root(tmp_path))

    assert handle_update() == 1
    out = capsys.readouterr().out
    assert expected_hint in out
    assert "Error: update failed." in out


def test_pull_failure_without_recognized_text_prints_no_hint(monkeypatch, tmp_path, capsys):
    """An unrecognized pull failure still exits 1, but invents no advice."""
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess().on(
        "pull", returncode=1, stderr="fatal: something else entirely"))
    patch_update_env(monkeypatch, anim, app_root=fake_app_root(tmp_path))

    assert handle_update() == 1
    out = capsys.readouterr().out
    assert "Error: update failed." in out
    assert "Hint:" not in out


def test_pip_failure_is_reported_but_not_fatal(monkeypatch, tmp_path, capsys):
    """A broken dependency sync must NOT abort the update.

    This asymmetry is intentional in the current code and is the easiest thing
    to break while refactoring: pip failure prints, then execution falls
    through to the version refresh and the run still returns 0. The app has
    already been pulled at that point, so returning 1 would tell the user the
    update failed when the code did in fact land.
    """
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess().on_argv(
        "pip install",
        lambda argv: FakeResult(1, stdout="", stderr="ERROR: could not resolve"),
    ))
    patch_update_env(monkeypatch, anim, app_root=fake_app_root(tmp_path))

    assert handle_update() == 0
    out = capsys.readouterr().out
    assert "Error: dependency sync failed" in out
    assert "could not resolve" in out
    # The run carried on to the version step.
    assert "Refreshing version" in anim.labels()


def test_pip_failure_output_is_filtered_to_the_last_15_useful_lines(monkeypatch, tmp_path, capsys):
    """pip noise is dropped and only the last 15 meaningful lines are shown.

    Keeps the terminal readable after a failed sync. The filter drops four
    specific noise patterns; if any of them reappears the user reads
    "Ignoring invalid distribution" instead of the real error.
    """
    anim = FakeAnimator()
    noise = "\n".join([
        "Ignoring invalid distribution foo",
        "[notice] A new release of pip is available: 24.0",
        "To update, run:  python -m pip install --upgrade pip",
        "Collecting textual",
    ])
    real = "\n".join(f"ERROR line {i}" for i in range(30))
    install_fake_subprocess(monkeypatch, FakeSubprocess().on_argv(
        "pip install", lambda argv: FakeResult(1, stdout=noise, stderr=real)))
    patch_update_env(monkeypatch, anim, app_root=fake_app_root(tmp_path))

    handle_update()
    out = capsys.readouterr().out
    assert "Ignoring invalid distribution" not in out
    assert "A new release of pip is available" not in out
    assert "To update, run" not in out
    # Kept the newest 15 of the 30 real error lines, dropped the oldest.
    assert "ERROR line 29" in out
    assert "ERROR line 0" not in out
    assert out.count("ERROR line") == 15


# --------------------------------------------------------------------- footers

def test_footer_reports_a_real_version_change(monkeypatch, tmp_path):
    """Before and after differ -> the footer spells out the transition."""
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    patch_update_env(monkeypatch, anim,
                     versions=["v3.0.2-alpha", "v3.0.3-alpha"],
                     app_root=fake_app_root(tmp_path))

    assert handle_update() == 0
    assert anim.footers == ["Updated: v3.0.2-alpha -> v3.0.3-alpha"]


def test_footer_reports_already_up_to_date(monkeypatch, tmp_path):
    """Same version before and after -> "Already up to date"."""
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    patch_update_env(monkeypatch, anim, versions=["v3.0.3-alpha"],
                     app_root=fake_app_root(tmp_path))

    assert handle_update() == 0
    assert anim.footers == ["Already up to date (v3.0.3-alpha)"]


def test_footer_falls_back_to_generic_when_no_version_is_known(monkeypatch, tmp_path):
    """No version resolvable at all -> the generic success line, still exit 0."""
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess().on(
        "describe --tags", returncode=1))
    patch_update_env(monkeypatch, anim, versions=[""],
                     app_root=fake_app_root(tmp_path))

    assert handle_update() == 0
    assert anim.footers == ["CMDAI CODE has been successfully updated!"]


# ------------------------------------------------------------------ config safety

def test_config_json_is_never_written(monkeypatch, tmp_path):
    """A successful update must leave the user's config.json byte-identical.

    The whole point of the update path is that it does not touch personal
    config. The file is also the one holding API keys, so this is asserted on
    content, not just on "no exception".
    """
    anim = FakeAnimator()
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    root = fake_app_root(tmp_path, config='{"api_keys": {"openrouter": "sk-secret"}}')
    before = (root / "config.json").read_bytes()
    mtime_before = os.path.getmtime(root / "config.json")

    patch_update_env(monkeypatch, anim, app_root=root)
    assert handle_update() == 0

    assert (root / "config.json").read_bytes() == before
    assert os.path.getmtime(root / "config.json") == mtime_before


# ------------------------------------------------------------- safe.directory fix

def test_safe_directory_is_registered_when_missing(monkeypatch, tmp_path):
    """An unregistered checkout is added to global safe.directory."""
    fake = install_fake_subprocess(monkeypatch, FakeSubprocess().on(
        "get-all safe.directory", returncode=1))
    patch_update_env(monkeypatch, FakeAnimator(), app_root=fake_app_root(tmp_path))

    handle_update()
    added = [c["args"] for c in fake.calls if "--add" in c["args"]]
    assert len(added) == 1
    assert added[0][:4] == ["git", "config", "--global", "--add"]


def test_safe_directory_not_re_added_when_already_present(monkeypatch, tmp_path):
    """A checkout already listed must not be added a second time."""
    root = fake_app_root(tmp_path)
    existing = str(root).replace("\\", "/")
    fake = install_fake_subprocess(monkeypatch, FakeSubprocess().on(
        "get-all safe.directory", returncode=0, stdout=existing + "\n"))
    patch_update_env(monkeypatch, FakeAnimator(), app_root=root)

    handle_update()
    assert not any("--add" in c["args"] for c in fake.calls)


def test_safe_directory_retries_with_forward_slashes(monkeypatch, tmp_path):
    """If the backslash form is rejected, retry with the slash form.

    Git on Windows prefers the forward-slash spelling, so a repo on a
    filesystem that records no ownership needs the second call to land.
    """
    root = fake_app_root(tmp_path)

    def _add(argv):
        return FakeResult(0 if "/" in argv[-1] else 1)

    fake = install_fake_subprocess(monkeypatch, FakeSubprocess()
        .on("get-all safe.directory", returncode=1)
        .on_argv("--add safe.directory", _add))
    patch_update_env(monkeypatch, FakeAnimator(), app_root=root)

    handle_update()
    added = [c["args"] for c in fake.calls if "--add" in c["args"]]
    assert len(added) == 2
    assert "\\" in added[0][-1]
    assert "/" in added[1][-1] and "\\" not in added[1][-1]


# -------------------------------------------------------------------- upstream fix

def test_upstream_is_set_when_current_branch_has_none(monkeypatch, tmp_path):
    """No @{u} -> probe origin/<branch> first, then set it."""
    fake = install_fake_subprocess(monkeypatch, FakeSubprocess()
        .on("symbolic-full-name", returncode=1)
        .on("abbrev-ref HEAD", returncode=0, stdout="main\n")
        .on("refs/remotes/origin/main", returncode=0))
    patch_update_env(monkeypatch, FakeAnimator(), app_root=fake_app_root(tmp_path))

    handle_update()
    assert fake.argv_for("branch --set-upstream-to") == [
        "git", "branch", "--set-upstream-to", "origin/main"]


def test_upstream_falls_back_to_master_when_main_absent(monkeypatch, tmp_path):
    """A repo on master with no origin/main must still be wired up."""
    fake = install_fake_subprocess(monkeypatch, FakeSubprocess()
        .on("symbolic-full-name", returncode=1)
        .on("abbrev-ref HEAD", returncode=0, stdout="master\n")
        .on("refs/remotes/origin/main", returncode=128, stderr="unknown revision")
        .on("refs/remotes/origin/master", returncode=0))
    patch_update_env(monkeypatch, FakeAnimator(), app_root=fake_app_root(tmp_path))

    handle_update()
    assert fake.argv_for("branch --set-upstream-to") == [
        "git", "branch", "--set-upstream-to", "origin/master"]


def test_upstream_left_alone_when_already_tracking(monkeypatch, tmp_path):
    """A checkout that already tracks upstream must not be rewritten."""
    fake = install_fake_subprocess(monkeypatch, FakeSubprocess()
        .on("symbolic-full-name", returncode=0, stdout="origin/main\n"))
    patch_update_env(monkeypatch, FakeAnimator(), app_root=fake_app_root(tmp_path))

    handle_update()
    assert not fake.ran("branch --set-upstream-to")
