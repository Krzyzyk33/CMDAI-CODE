"""Progress window for the in-app `/update`.

Draws what `cmdai code update` draws in a terminal, but as widgets. The updater
itself is the same code in both places - `core.updater.run_update` - so the two
entry points cannot drift apart; only the reporter differs.

Nothing in here may print. Writing to stdout from a Textual screen injects
escape codes into the rendered frame.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll, Center
from textual.screen import ModalScreen
from textual.widgets import Static, Button

GLYPHS = ["⌬", "✻"]
FLIP = 0.35


def render_installer_banner(percent: int) -> str:
    """Renders the CC logo with a block progress bar for Textual / Rich UI."""
    pct = max(0, min(100, int(percent)))
    total_blocks = 16
    filled = int((pct * total_blocks) / 100)
    empty = total_blocks - filled
    bar_str = f"[#58a6ff]{'▓' * filled}[/][#484f58]{'░' * empty}[/]  [bold #ffffff]{pct:>3}%[/]"
    return (
        f" [bold #ffffff]█████╗    █████╗[/]\n"
        f"[bold #ffffff]██╔═══╝   ██╔═══╝[/]\n"
        f"[bold #ffffff]██║       ██║[/]        {bar_str}\n"
        f"[bold #ffffff]╚█████╗   ╚█████╗[/]\n"
        f" [bold #ffffff]╚════╝    ╚════╝[/]"
    )


class UpdateProgressModal(ModalScreen[bool]):
    """Steps of an update, with a running line and a finished summary.

    Dismissable throughout: a user who started an update and changed their
    mind closes the window, the update keeps running in its thread, and the
    result is reported through the app's own notify path.
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.done = False
        self.restart_required = False
        self.summary = ""
        self._started = time.monotonic()
        self._rows: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="update-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Update CMDAI CODE", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            with Center(id="update-banner-container"):
                yield Static(render_installer_banner(10), id="update-banner")
            yield Static("Preparing...", id="update-step")
            with VerticalScroll(id="update-log"):
                yield Static("", id="update-log-body")
            with Horizontal(id="update-actions"):
                yield Button("Restart Now", id="btn-restart-now", variant="primary")
                yield Button("Later", id="btn-restart-later")
            with Horizontal(id="modal-footer"):
                yield Static("[dim]esc closes the window - the update keeps running[/]",
                            id="modal-footer-left")
                yield Static("", id="modal-footer-right")

    # ------------------------------------------------------------- reporting

    def begin_step(self, label: str) -> None:
        """A step started. Called from the updater's worker thread."""
        self._rows.append({"label": label, "status": "running", "detail": ""})
        # Repaint now rather than waiting for the next flip. The timer only
        # rewrites the glyph, so without this the window sits on "..." for up to
        # a third of a second after the work already started.
        self.refresh_rows()

    def end_step(self, status: str, detail: str = "") -> None:
        if self._rows:
            self._rows[-1]["status"] = status
            self._rows[-1]["detail"] = detail
        self.refresh_rows()

    def add_message(self, text: str) -> None:
        """A hint or error line. Same rows, so it lands under the step it
        belongs to instead of in a separate log."""
        self._rows.append({"label": text, "status": "message", "detail": ""})
        self.refresh_rows()

    def finish(self, summary: str) -> None:
        self.done = True
        self.summary = summary
        self.refresh_rows()

    # -------------------------------------------------------------- rendering

    def _current_row(self) -> Optional[str]:
        for row in reversed(self._rows):
            if row["status"] == "running":
                return row["label"]
        return None

    def refresh_rows(self) -> None:
        """Repaint the step line and the log. Safe to call from any thread
        through `call_from_thread`."""
        current = self._current_row()
        elapsed = time.monotonic() - self._started
        glyph = GLYPHS[int(elapsed / FLIP) % 2] if current else "⌬"

        if self.done:
            step_text = f"[b #3fb950]✔  {self.summary or 'Update completed!'}[/]"
            percent = 100
            try:
                actions = self.query_one("#update-actions")
                if not actions.has_class("visible"):
                    actions.add_class("visible")
                    self.query_one("#btn-restart-now", Button).focus()
            except Exception:
                pass
            try:
                self.query_one("#modal-footer-left", Static).update(
                    "[dim]Enter restarts now · esc closes window[/]"
                )
            except Exception:
                pass
        else:
            step_text = f"{glyph}  {current}" if current else f"{glyph}  Preparing update..."
            percent = 15
            labels = [r["label"].lower() for r in self._rows]
            if any("refresh" in l or "version" in l for l in labels):
                percent = 90
            elif any("package" in l or "synchroniz" in l or "pip" in l for l in labels):
                percent = 75
            elif any("pull" in l or "download" in l for l in labels):
                percent = 45
            elif len(self._rows) > 0:
                percent = 30

        try:
            self.query_one("#update-banner", Static).update(render_installer_banner(percent))
        except Exception:
            pass

        try:
            self.query_one("#update-step", Static).update(step_text)
        except Exception:
            return

        lines: List[str] = []
        for row in self._rows:
            status = row["status"]
            # The running step already has its own line above the log. Listing
            # it here too showed the same text twice, which read as two steps.
            if status == "running":
                continue
            if status == "OK":
                lines.append(f"[#3fb950]✓[/]  {row['label']}")
            elif status == "FAIL":
                lines.append(f"[#f85149]✕[/]  {row['label']}")
            else:
                lines.append(f"[dim]{row['label']}[/dim]")
            detail = str(row.get("detail") or "").strip()
            if detail and status == "FAIL":
                for line in detail.splitlines():
                    lines.append(f"    [dim]{line}[/dim]")
        if self.done and self.summary:
            if lines:
                lines.append("")
            lines.append(f"[b #3fb950]{self.summary}[/]")
            if self.restart_required:
                lines.append("")
                lines.append("[dim #8b949e]Restart CMDAI CODE to run the updated version:[/dim]")
        try:
            self.query_one("#update-log-body", Static).update("\n".join(lines))
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-restart-now":
            self.action_restart_now()
        elif event.button.id == "btn-restart-later":
            self.dismiss(self.done)

    def action_restart_now(self) -> None:
        """Restarts CMDAI CODE immediately."""
        self.dismiss(True)
        if hasattr(self.app, "action_restart_app"):
            self.app.action_restart_app()
        elif hasattr(self.app, "restart_app"):
            self.app.restart_app()
        else:
            import subprocess
            subprocess.Popen([sys.executable] + sys.argv)
            self.app.exit(0)

    def on_mount(self) -> None:
        self.refresh_rows()
        self._timer = self.set_interval(FLIP, self.refresh_rows)

    def on_unmount(self) -> None:
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()
            self._timer = None

    def action_close(self) -> None:
        self.dismiss(self.done)


class TuiReporter:
    """Adapts `core.updater.run_update` to a Textual screen.

    Mirrors `_animated_step` from cli.py: the work runs on a thread, this one
    polls until it finishes or the timeout expires, and the step is drawn while
    it runs. Same contract, same timeout semantics, no terminal output.
    """

    def __init__(self, app: Any, modal: UpdateProgressModal, tick: float = 0.08):
        self.app = app
        self.modal = modal
        self.tick = tick

    def _paint(self, fn, *args) -> None:
        """Run a modal update, from the app thread or the updater's thread.

        `call_from_thread` is the supported way in from a worker, and it raises
        when called on the app's own thread. Swallowing that and dropping the
        update loses state, not just a repaint: a finish() that never lands
        leaves the window showing a step as still running forever. So the
        same-thread case calls through directly.
        """
        try:
            if self._on_app_thread():
                fn(*args)
                return
            self.app.call_from_thread(fn, *args)
        except Exception:
            try:
                fn(*args)
            except Exception:
                pass

    def _on_app_thread(self) -> bool:
        import threading

        try:
            loop = getattr(self.app, "_loop", None)
            return bool(loop is not None and threading.get_ident() == loop._thread_id)
        except Exception:
            return False

    def step(self, label: str, work_fn, min_time: float = 1.0, timeout: float = 300.0,
             header: str = "", gap: int = 0) -> Tuple[str, str]:
        import threading

        result: Dict[str, str] = {}

        def _work() -> None:
            try:
                status, detail = work_fn()
            except Exception as e:
                status, detail = "FAIL", str(e)
            result["status"], result["detail"] = status, detail

        self._paint(self.modal.begin_step, label)
        thread = threading.Thread(target=_work, daemon=True)
        thread.start()

        # Respect min_time so a step that returns instantly still reads as a
        # step, and timeout so a hung git cannot hold the window open forever.
        start = time.monotonic()
        deadline = start + (timeout if timeout > 0 else 300.0)
        while time.monotonic() < deadline:
            if "status" in result and (time.monotonic() - start) >= min_time:
                break
            time.sleep(self.tick)

        # Decide before joining. Joining an already-hung step would block the
        # UI thread for the full join timeout, which is the one thing the
        # deadline exists to prevent.
        timed_out = "status" not in result
        if not timed_out:
            thread.join(timeout=10)
            timed_out = "status" not in result

        if timed_out:
            # The worker is a daemon and is left to finish on its own.
            status = "TIMEOUT"
            detail = f"timed out after {int(timeout)}s"
        else:
            status, detail = result["status"], result.get("detail", "")
        self._paint(self.modal.end_step, status, "" if status == "OK" else detail)
        return status, detail

    def message(self, text: str) -> None:
        self._paint(self.modal.add_message, text)

    def finish(self, text: str) -> None:
        self.modal.restart_required = True
        self._paint(self.modal.finish, text)
