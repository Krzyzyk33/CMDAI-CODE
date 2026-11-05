import os
import subprocess
from typing import List, Dict, Tuple, Optional
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

GIT_MODAL_CSS = """
GitBranchesModal {
    align: center middle;
    background: rgba(9, 13, 19, 0.85);
}

/* Fullscreen, frameless - shared by the TUI and the editor. */
#git-dialog {
    width: 100%;
    height: 100%;
    max-width: 100%;
    max-height: 100%;
    background: #0d1117;
    border: none;
    padding: 2 4;
}

#git-header {
    height: 3;
    dock: top;
    border-bottom: solid #21262d;
    padding-bottom: 1;
}

#git-title {
    width: 1fr;
    color: #e6edf3;
    text-style: bold;
    text-overflow: ellipsis;
    overflow: hidden;
}

#git-esc-hint {
    width: auto;
    color: #8b949e;
}

#git-input-row {
    height: 3;
    margin: 1 0;
    align: left middle;
}

#git-branch-input {
    width: 1fr;
    height: 3;
    min-height: 3;
    max-height: 3;
    background: #161b22;
    color: #e6edf3;
    /* !important - app-level `Input { border: none !important; max-height: 1 }`
       in styles.py would otherwise strip the frame and hide the text. */
    border: round #30363d !important;
    padding: 0 1;
    margin-right: 1;
}

/* Pole nie zmienia koloru na bialy po fokusie - dostaje tylko tlo. */
#git-branch-input:focus {
    background: #21262d;
    border: round #30363d !important;
}

#git-create-btn {
    width: auto;
    height: 3;
    background: #238636;
    color: #ffffff;
    border: none;
}

#git-create-btn:hover {
    background: #2ea043;
}

#git-scroll {
    width: 100%;
    height: 1fr;
    padding: 1 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

/* Aktywna galeaz. Wczesniej `border: round #58a6ff` robil z niej
   jaskrawoniebieska ramke - user prosil, zeby pozycje z ramka nie byly
   podswietlone. Teraz ramka jest jak u innych pozycji (szara), a
   aktywnosc niesie wypelnienie + pogrubiona nazwa + lewy pasek.
   Wcześniej było też `background: #238636` + `border: none`, czyli
   pelny zielony pas z bialym tekstem - wygladalo jak zaznaczenie. */
.git-branch-active {
    width: 100%;
    height: 3;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #21262d;
    color: #e6edf3;
    border: round #30363d;
    content-align: left middle;
    text-style: bold;
    text-align: left;
}

/* Wyglad jak karty checkpointow: ramka zamiast wypelnionego tla.
   Wczesniej `border: none` + pelne tlo robilo z tego jeden pas
   koloru, a najechanie dawalo jasne tlo - teraz jest tylko obwodka.
   Zaznaczenie fokusem NIE daje bialego tla - tylko niebieska ramka. */
.git-branch-item {
    width: 100%;
    height: 3;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #161b22;
    color: #8b949e;
    border: round #30363d;
    content-align: left middle;
}

.git-branch-item:hover,
.git-branch-item:focus {
    background: #161b22;
    color: #e6edf3;
    /* Niebieska ramka na hoverze tez zniknela - zostaje wypelnienie
       i pogrubienie, dzieki czemu kursor nie wyglada jak zaznaczenie. */
    border: round #30363d;
    text-style: bold;
}

#git-log-pane {
    dock: bottom;
    height: auto;
    max-height: 40%;
    min-height: 4;
    border-top: solid #21262d;
    padding: 1 0;
    background: #090d13;
}

#git-log-header {
    height: 1;
    color: #8b949e;
    text-style: none;
    margin-bottom: 1;
}

#git-log-body {
    width: 100%;
    color: #8b949e;
}
"""

class GitBranchesModal(ModalScreen[None]):

    CSS = GIT_MODAL_CSS

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Back/Close"),
    ]

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self._branch_id_map: Dict[str, str] = {}

    def _is_git_repo(self) -> bool:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=3
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False

    def _get_branches(self) -> Tuple[str, List[str]]:
        current_branch = ""
        branches: List[str] = []
        if not self._is_git_repo():
            return "", []
        try:
            res = subprocess.run(
                ["git", "branch", "-a"],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    clean = line.strip()
                    if clean.startswith("*"):
                        current_branch = clean.lstrip("*").strip()
                    elif clean and "->" not in clean:
                        b_name = clean
                        if b_name.startswith("remotes/"):
                            b_name = b_name.replace("remotes/", "")
                        if b_name and b_name != current_branch and b_name not in branches:
                            branches.append(b_name)
        except Exception:
            pass
        return current_branch, branches

    def _get_recent_log(self) -> str:
        if not self._is_git_repo():
            return "(Directory is not a git repository)"
        try:
            res = subprocess.run(
                ["git", "log", "--oneline", "-n", "6"],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception:
            pass
        return "(No commits recorded yet in repository)"

    def compose(self) -> ComposeResult:
        is_repo = self._is_git_repo()
        curr, other_branches = self._get_branches()
        self._branch_id_map.clear()

        with Vertical(id="git-dialog"):
            with Horizontal(id="git-header"):
                yield Static("GIT BRANCHES & FORKS (REAL REPO · BRANCH / CHECKOUT / FORK)", id="git-title")
                yield Static("[dim]esc[/dim]", id="git-esc-hint")

            with Horizontal(id="git-input-row"):
                yield Input(placeholder="Create & switch to new branch name... (Enter to fork)", id="git-branch-input")
                yield Button("Fork / Branch", id="git-create-btn")

            with VerticalScroll(id="git-scroll"):
                if not is_repo:
                    yield Button("⚠ Directory is not a git repository · Click to run [git init]", classes="git-branch-item", id="btn_git_init")
                else:
                    active_label = curr if curr else "(uncommitted HEAD)"
                    yield Button(f"● ACTIVE BRANCH · {active_label} · (HEAD -> {active_label})", classes="git-branch-active", id="branch_active")

                    if not other_branches:
                        yield Static("[dim]  (No other branches exist in this repository. Type a name above to create one)[/dim]", id="git-empty-hint")
                    else:
                        for idx, b in enumerate(other_branches):
                            safe_id = f"branch_{idx}"
                            self._branch_id_map[safe_id] = b
                            yield Button(f"○ BRANCH · {b} · click to checkout", classes="git-branch-item", id=safe_id)

            # Panel "RECENT COMMITS" usuniety na zadanie - modal jest
            # teraz lista galez, bez dolnego paska logu.

    @on(Input.Submitted, "#git-branch-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._create_and_checkout(event.value)

    @on(Button.Pressed, "#git-create-btn")
    def on_create_pressed(self) -> None:
        inp = self.query_one("#git-branch-input", Input)
        self._create_and_checkout(inp.value)

    @on(Button.Pressed, "#btn_git_init")
    def on_git_init_pressed(self) -> None:
        try:
            res = subprocess.run(["git", "init"], cwd=self.workdir, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                self.notify("Git repository initialized successfully!", severity="information")
                self.dismiss()
            else:
                self.notify(f"git init error: {res.stderr.strip() or res.stdout.strip()}", severity="error")
        except Exception as e:
            self.notify(f"Failed to run git init: {e}", severity="error")

    def _create_and_checkout(self, branch_name: str) -> None:
        branch_name = branch_name.strip()
        if not branch_name:
            self.notify("Please specify a branch or fork name.", severity="warning")
            return
        if not self._is_git_repo():
            self.notify("Cannot create branch: Directory is not a git repository. Run 'git init' first.", severity="error")
            return
        try:
            res = subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5
            )
            if res.returncode == 0:
                self.notify(f"Created and checked out branch: {branch_name}", severity="information")
                self.dismiss()
            else:
                self.notify(f"Git error: {res.stderr.strip() or res.stdout.strip()}", severity="error")
        except Exception as e:
            self.notify(f"Failed to create branch: {e}", severity="error")

    @on(Button.Pressed)
    def on_branch_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid in ("git-create-btn", "branch_active", "btn_git_init"):
            return
        if bid in self._branch_id_map:
            target_branch = self._branch_id_map[bid]
            checkout_args = ["git", "checkout"]
            if target_branch.startswith("origin/"):
                local_name = target_branch.replace("origin/", "")
                checkout_args += ["-B", local_name, target_branch]
            else:
                checkout_args.append(target_branch)

            try:
                res = subprocess.run(
                    checkout_args,
                    cwd=self.workdir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5
                )
                if res.returncode == 0:
                    self.notify(f"Switched to branch: {target_branch}", severity="information")
                    self.dismiss()
                else:
                    self.notify(f"Git checkout: {res.stderr.strip() or res.stdout.strip()}", severity="error")
            except Exception as e:
                self.notify(f"Failed to checkout branch: {e}", severity="error")

    def action_dismiss_modal(self) -> None:
        self.dismiss()

