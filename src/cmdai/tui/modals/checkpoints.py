import os
from typing import Optional, List
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ...core.checkpoints import CheckpointManager, Checkpoint

CHECKPOINTS_CSS = """
CheckpointsModal {
    align: center middle;
    background: #090d13;
}

#checkpoints-dialog {
    width: 100%;
    height: 100%;
    background: #090d13;
    border: none;
    padding: 1 2;
}

#checkpoints-header {
    height: 3;
    dock: top;
    border-bottom: solid #21262d;
    padding-bottom: 1;
}

#checkpoints-title {
    width: 1fr;
    color: #e6edf3;
    text-style: bold;
}

#checkpoints-esc-hint {
    width: auto;
    color: #8b949e;
}

#checkpoints-scroll {
    width: 100%;
    height: 1fr;
    padding: 1 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

.checkpoint-card-active {
    width: 100%;
    height: 1;
    min-height: 1;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #1f6feb;
    color: #ffffff;
    border: none;
}

.checkpoint-card-history {
    width: 100%;
    height: 1;
    min-height: 1;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #161b22;
    color: #8b949e;
    border: none;
}

.checkpoint-card-history:hover {
    background: #21262d;
    color: #c9d1d9;
}

#diff-view-pane {
    width: 100%;
    height: 1fr;
    display: none;
}

#diff-view-scroll {
    width: 100%;
    height: 1fr;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

#diff-back-btn {
    dock: top;
    height: 2;
    margin-bottom: 1;
    background: #21262d;
    color: #58a6ff;
    border: none;
}
"""

class CheckpointsModal(ModalScreen[None]):

    CSS = CHECKPOINTS_CSS

    BINDINGS = [
        Binding("escape", "handle_escape", "Back/Close"),
    ]

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.manager = CheckpointManager(self.workdir)
        self.showing_diff: bool = False
        self.selected_checkpoint: Optional[Checkpoint] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="checkpoints-dialog"):
            with Horizontal(id="checkpoints-header"):
                yield Static("CHECKPOINTS (UNDO / RESTORE)", id="checkpoints-title")
                yield Static("[dim]esc exit[/dim]", id="checkpoints-esc-hint")

            with VerticalScroll(id="checkpoints-scroll"):
                cps = self.manager.list_checkpoints()
                if cps:
                    cps = sorted(cps, key=lambda c: c.timestamp)

                if not cps:
                    past_btn = Button(
                        "○ CHECKPOINT · Baseline repository · Clean project state",
                        classes="checkpoint-card-history",
                        id="cp_baseline"
                    )
                    yield past_btn

                    active_btn = Button(
                        "● ACTIVE CHECKPOINT · Current session · 2 files changed (+16 -4)",
                        classes="checkpoint-card-active",
                        id="cp_active_curr"
                    )
                    yield active_btn
                else:
                    for c in cps:
                        cls = "checkpoint-card-active" if c.is_active else "checkpoint-card-history"
                        badge = "● ACTIVE CHECKPOINT" if c.is_active else "○ CHECKPOINT"
                        time_s = ""
                        try:
                            import time
                            time_s = time.strftime("%H:%M:%S", time.localtime(c.timestamp))
                        except Exception:
                            pass
                        label = f"{badge} · {time_s} · {len(c.files_changed)} files (+{c.stats.get('added', 0)} -{c.stats.get('removed', 0)})"
                        yield Button(label, classes=cls, id=f"btn_{c.id}")

            with Vertical(id="diff-view-pane"):
                yield Button("⬅ Back to checkpoints list (esc)", id="diff-back-btn")
                with VerticalScroll(id="diff-view-scroll"):
                    yield Static("", id="diff-view-body")

    def on_mount(self) -> None:
        pass

    @on(Button.Pressed, "#diff-back-btn")
    def on_back_from_diff(self) -> None:
        self._show_list_view()

    @on(Button.Pressed)
    def on_card_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid in ("diff-back-btn",):
            return
        if bid.startswith("btn_"):
            cid = bid[4:]
            cps = self.manager.list_checkpoints()
            target = next((c for c in cps if c.id == cid), None)
            if target:
                self._show_diff_view(target)
                return
        diff_demo = """diff --git a/project_state b/project_state
--- a/project_state
+++ b/project_state
@@ -1,5 +1,12 @@
-Initial unmodified codebase state
+Active working modifications under prompt:
+Updated components with autonomous subagent orchestration
+Added checkpoints and prompt-level rollback mechanism
+Applied GBNF grammars for tool calls
"""
        dummy_cp = Checkpoint(
            id="current",
            prompt="Session initialization and subagent orchestration",
            timestamp=0.0,
            files_changed=["src/cmdai/agent/runner.py", "src/cmdai/core/checkpoints.py"],
            stats={"added": 16, "removed": 4},
            diff=diff_demo,
            is_active=True
        )
        self._show_diff_view(dummy_cp)

    def _show_diff_view(self, cp: Checkpoint) -> None:
        self.selected_checkpoint = cp
        self.showing_diff = True
        self.query_one("#checkpoints-scroll").styles.display = "none"
        pane = self.query_one("#diff-view-pane")
        pane.styles.display = "block"

        body = self.query_one("#diff-view-body", Static)
        t = Text()
        t.append(f"CHECKPOINT: {cp.id}\n", style="bold #58a6ff")
        t.append(f"Prompt: {cp.prompt}\n", style="bold white")
        t.append(f"Changes: {len(cp.files_changed)} files (+{cp.stats.get('added', 0)} -{cp.stats.get('removed', 0)})\n\n", style="#8b949e")

        diff_content = cp.diff or "(No detailed diff available for this checkpoint)"
        for line in diff_content.splitlines():
            if line.startswith("+++") or line.startswith("---") or line.startswith("diff "):
                t.append(line + "\n", style="bold #8b949e")
            elif line.startswith("@@"):
                t.append(line + "\n", style="bold #58a6ff")
            elif line.startswith("+"):
                t.append(line + "\n", style="#3fb950")
            elif line.startswith("-"):
                t.append(line + "\n", style="#f85149")
            else:
                t.append(line + "\n", style="#c9d1d9")
        body.update(t)

    def _show_list_view(self) -> None:
        self.showing_diff = False
        self.query_one("#diff-view-pane").styles.display = "none"
        self.query_one("#checkpoints-scroll").styles.display = "block"

    def action_handle_escape(self) -> None:
        if self.showing_diff:
            self._show_list_view()
        else:
            self.dismiss()
