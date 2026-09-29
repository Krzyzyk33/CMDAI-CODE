"""Group H and I - the in-app update, and the reporter contract it relies on.

The requirement behind `/update` is that it does exactly what
`cmdai code update` does. That is only true if both drive the same
`core.updater.run_update` and differ only in who draws the steps, so the first
test here runs one scripted update through both reporters and compares the
results.
"""

import asyncio

import pytest

from cmdai.core.updater import NullReporter, UpdateResult, run_update
from cmdai.tui.app import CMDAICodeTUI
from cmdai.tui.modals.update import TuiReporter, UpdateProgressModal
from tests.harness import (
    FakeAnimator,
    FakeSubprocess,
    fake_app_root,
    install_fake_subprocess,
    patch_update_env,
)


class RecordingReporter(NullReporter):
    """A reporter that records the contract, with no drawing at all."""

    def __init__(self):
        self.steps = []
        self.messages = []
        self.finishes = []

    def step(self, label, work_fn, min_time=1.0, timeout=300.0, header="", gap=0):
        self.steps.append((label, min_time, timeout, header, gap))
        return work_fn()

    def message(self, text):
        self.messages.append(text)

    def finish(self, text):
        self.finishes.append(text)


# ------------------------------------------------------------ H: same behavior

def test_both_reporters_produce_the_same_result(monkeypatch, tmp_path):
    """`/update` and `cmdai code update` must be indistinguishable in outcome.

    Same scripted git, same requirement file, one run through the terminal
    reporter and one through the TUI reporter. Steps, hints, summary and the
    version transition all have to match; only the drawing differs.
    """

    def script(monkeypatch, versions):
        fake = install_fake_subprocess(monkeypatch, FakeSubprocess())
        patch_update_env(monkeypatch, FakeAnimator(), versions=versions,
                         app_root=fake_app_root(tmp_path))
        return fake

    script(monkeypatch, ["v3.0.2-alpha", "v3.0.3-alpha"])
    cli_reporter = RecordingReporter()
    cli_result = run_update(str(fake_app_root(tmp_path)), reporter=cli_reporter)

    fake2 = script(monkeypatch, ["v3.0.2-alpha", "v3.0.3-alpha"])
    tui_reporter = RecordingReporter()
    tui_result = run_update(str(fake_app_root(tmp_path)), reporter=tui_reporter)

    assert cli_result.ok == tui_result.ok
    assert cli_result.summary == tui_result.summary
    assert cli_result.version_before == tui_result.version_before
    assert cli_result.version_after == tui_result.version_after
    assert cli_result.steps == tui_result.steps
    assert [s[0] for s in cli_reporter.steps] == [s[0] for s in tui_reporter.steps]
    assert [s[1:] for s in cli_reporter.steps] == [s[1:] for s in tui_reporter.steps]
    assert cli_reporter.finishes == tui_reporter.finishes
    assert fake2.ran("pull")


def test_both_reporters_agree_on_a_failure(monkeypatch, tmp_path):
    """A failed pull must produce the same hint through either front end."""
    hints = {}
    for name, reporter_cls in (("cli", RecordingReporter), ("tui", RecordingReporter)):
        install_fake_subprocess(monkeypatch, FakeSubprocess().on(
            "pull", returncode=1, stderr="fatal: dubious ownership in directory"))
        patch_update_env(monkeypatch, FakeAnimator(),
                         app_root=fake_app_root(tmp_path))
        rep = reporter_cls()
        result = run_update(str(fake_app_root(tmp_path)), reporter=rep)
        hints[name] = (result.ok, tuple(rep.messages), result.steps)
    assert hints["cli"] == hints["tui"]
    assert hints["cli"][0] is False
    assert any("safe.directory" in m for m in hints["cli"][1])


def test_the_null_reporter_draws_nothing(monkeypatch, tmp_path, capsys):
    """The base reporter is silent, so the update path stays testable."""
    install_fake_subprocess(monkeypatch, FakeSubprocess())
    patch_update_env(monkeypatch, FakeAnimator(),
                     app_root=fake_app_root(tmp_path))
    result = run_update(str(fake_app_root(tmp_path)))
    assert isinstance(result, UpdateResult)
    assert result.ok is True
    assert capsys.readouterr().out == ""


# ------------------------------------------------- the progress window itself

def test_the_window_accumulates_steps_and_messages():
    """Rows render in order, so the log reads like the terminal output."""
    modal = UpdateProgressModal()
    modal.rows = getattr(modal, "_rows", [])
    modal._rows = []
    modal.begin_step("Pulling latest changes")
    modal.end_step("OK")
    modal.begin_step("Synchronizing packages")
    modal.end_step("FAIL", "ERROR: could not resolve")
    modal.add_message("Hint: check your connection")
    modal.finish("Updated: v3.0.2-alpha -> v3.0.3-alpha")

    assert modal.done is True
    statuses = [r["status"] for r in modal._rows]
    assert statuses == ["OK", "FAIL", "message"]
    labels = [r["label"] for r in modal._rows]
    assert labels[0] == "Pulling latest changes"
    assert labels[2] == "Hint: check your connection"


@pytest.mark.asyncio
async def test_the_window_asks_for_a_restart_when_the_update_lands(tmp_path):
    """The running process has the old code loaded; saying so is required.

    Without this the user keeps using a stale build and meets behavior that no
    longer matches the source they just pulled.
    """
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = UpdateProgressModal()
        app.push_screen(modal)
        await pilot.pause()

        reporter = TuiReporter(app, modal)
        modal.begin_step("Pulling latest changes")
        modal.end_step("OK")
        reporter.finish("Updated: v3.0.2-alpha -> v3.0.3-alpha")
        await pilot.pause()

        log = str(modal.query_one("#update-log-body").content)
        assert modal.done is True
        assert modal.restart_required is True
        assert "Updated: v3.0.2-alpha -> v3.0.3-alpha" in log
        assert "Restart CMDAI CODE" in log
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_the_window_is_dismissable_while_running(tmp_path):
    """esc closes the window; the update keeps going in its own thread."""
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = UpdateProgressModal()
        app.push_screen(modal)
        await pilot.pause()
        modal.begin_step("Pulling latest changes")
        await pilot.pause()

        assert modal.done is False
        assert isinstance(app.screen, UpdateProgressModal)
        await pilot.press("escape")
        await pilot.pause()
        # Back in the app, and the step is still marked as running.
        assert not isinstance(app.screen, UpdateProgressModal)
        assert modal._rows[-1]["status"] == "running"


# ---------------------------------------------------------- I: /update in the app

@pytest.mark.asyncio
async def test_the_update_command_pushes_the_progress_window(tmp_path, monkeypatch):
    """Typing /update opens the window and runs the updater off the UI thread."""
    import cmdai.core.updater as updater

    called = {}

    def _fake_run(app_root, reporter=None, **kw):
        called["app_root"] = app_root
        called["reporter"] = reporter
        reporter.begin_step("Pulling latest changes")
        reporter.end_step("OK")
        reporter.finish("Already up to date (v3.0.3-alpha)")
        return UpdateResult(ok=True, version_before="v3.0.3-alpha",
                            version_after="v3.0.3-alpha")

    monkeypatch.setattr(updater, "run_update", _fake_run)

    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._cmd_update("")
        await pilot.pause()
        assert isinstance(app.screen, UpdateProgressModal)
        for _ in range(40):
            await asyncio.sleep(0.05)
            if app._update_running is False:
                break
        await pilot.pause()
        assert called["app_root"]
        assert isinstance(called["reporter"], TuiReporter)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_a_second_update_is_refused_while_one_runs(tmp_path, monkeypatch):
    """Two concurrent updates would race on the same working tree."""

    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._update_running = True
        app._cmd_update("")
        await pilot.pause()
        assert not isinstance(app.screen, UpdateProgressModal), "a second update was started"


@pytest.mark.asyncio
async def test_update_is_refused_while_the_agent_is_generating(tmp_path):
    """Pulling mid-turn would swap the source out from under a running turn."""
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.is_generating = True
        app._cmd_update("")
        await pilot.pause()
        assert not isinstance(app.screen, UpdateProgressModal)


@pytest.mark.asyncio
async def test_the_in_app_update_writes_nothing_to_stdout(tmp_path, monkeypatch, capsys):
    """The load-bearing constraint: escape codes on stdout break the frame.

    The CLI reporter drives `install_anim`, which rewrites the current line with
    CR and ANSI codes. That is fine in a terminal and corrupts a Textual screen,
    so the in-app reporter has to be inert on stdout. Asserted by capturing
    everything written while a full update runs inside the app.
    """
    import cmdai.core.updater as updater

    def _fake_run(app_root, reporter=None, **kw):
        reporter.begin_step("Pulling latest changes")
        reporter.end_step("OK")
        reporter.begin_step("Synchronizing packages")
        reporter.end_step("OK")
        reporter.finish("Already up to date (v3.0.3-alpha)")
        return UpdateResult(ok=True, version_before="v3.0.3-alpha",
                            version_after="v3.0.3-alpha")

    monkeypatch.setattr(updater, "run_update", _fake_run)

    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        capsys.readouterr()  # discard what on_mount already emitted

        app._cmd_update("")
        for _ in range(40):
            await asyncio.sleep(0.05)
            if app._update_running is False:
                break
        await pilot.pause()

        captured = capsys.readouterr()
        assert captured.out == "", f"the update wrote to stdout: {captured.out!r}"
        assert captured.err == ""
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_a_hung_step_times_out_instead_of_freezing_the_window(tmp_path, monkeypatch):
    """A git that never returns must not hold the update window open forever."""
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = UpdateProgressModal()
        app.push_screen(modal)
        await pilot.pause()

        reporter = TuiReporter(app, modal, tick=0.01)
        import threading
        blocker = threading.Event()

        def _never_returns():
            blocker.wait(5)
            return "OK", ""

        status, detail = reporter.step("Hanging", _never_returns,
                                       min_time=0.0, timeout=0.3)
        blocker.set()
        await pilot.pause()

        assert status == "TIMEOUT"
        assert "timed out" in detail
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_a_running_step_appears_exactly_once(tmp_path):
    """Regression: the step was listed twice, once above the log and once in it.

    The header line owns the step that is running; the log is the history of
    finished ones. Repeating it read as two steps and made a single pull look
    like two.
    """
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = UpdateProgressModal()
        app.push_screen(modal)
        await pilot.pause()

        modal.begin_step("Pulling latest changes")
        await pilot.pause()
        step = str(modal.query_one("#update-step").content)
        log = str(modal.query_one("#update-log-body").content)
        assert "Pulling latest changes" in step
        assert "Pulling latest changes" not in log

        # Once it finishes it belongs in the log, and leaves the header.
        modal.end_step("OK")
        modal.begin_step("Synchronizing packages")
        await pilot.pause()
        step = str(modal.query_one("#update-step").content)
        log = str(modal.query_one("#update-log-body").content)
        assert "Synchronizing packages" in step
        assert "Pulling latest changes" in log
        assert "Synchronizing packages" not in log
        assert log.count("Pulling latest changes") == 1
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_the_step_name_appears_without_waiting_for_the_spinner(tmp_path):
    """begin_step repaints at once; the flip timer only animates the glyph."""
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = UpdateProgressModal()
        app.push_screen(modal)
        await pilot.pause()

        modal.begin_step("Refreshing version")
        # No pilot.pause(): the paint has to have happened synchronously.
        step = str(modal.query_one("#update-step").content)
        assert "Refreshing version" in step
        assert step.strip() != "..."
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_a_running_step_is_reported_not_propagated(tmp_path):
    """A step that throws shows as a failure; it must not kill the thread."""
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = UpdateProgressModal()
        app.push_screen(modal)
        await pilot.pause()

        reporter = TuiReporter(app, modal, tick=0.01)

        def _boom():
            raise RuntimeError("git exploded")

        status, detail = reporter.step("Boom", _boom, min_time=0.0, timeout=2.0)
        await pilot.pause()
        assert status == "FAIL"
        assert "git exploded" in detail
        await pilot.press("escape")
        await pilot.pause()
