"""Update flow for CMDAI CODE, independent of any front end.

The whole point of this module is that `cmdai code update` and the in-app
`/update` do exactly the same thing. That used to be impossible: the update
lived in `cmdai.cli` and drove `tools/install_anim.py`, which writes escape
codes straight to stdout. Called from a Textual screen that shreds the
rendering. So the logic sits here, printing nothing, and a front end supplies a
reporter to draw progress with.

A reporter implements three methods:

    step(label, work_fn, min_time, timeout, header, gap) -> (status, detail)
    message(text)                     one plain line of output
    finish(text)                      the closing summary line

`work_fn` runs off the UI thread and returns (status, detail) with status
"OK" or "FAIL"; the reporter is responsible for waiting and drawing.

The step sequence, parameters, hint wording and exit codes are pinned by
`tests/test_update_characterization.py`. In particular the pip step is
deliberately non-fatal: the code has already been pulled by that point, so
returning a failure would tell the user the update did not land when it did.
"""

import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

REPO_URL = "https://github.com/Krzyzyk33/CMDAI-CODE.git"

# Noise pip emits on a machine that has seen a few broken dist-info
# directories. Kept out of the failure report so the real error is visible.
_PIP_NOISE = (
    "Ignoring invalid distribution",
    "new release of pip is available",
    "to update, run:",
)


@dataclass
class UpdateResult:
    ok: bool
    version_before: str = ""
    version_after: str = ""
    steps: List[str] = field(default_factory=list)
    detail: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.version_before and self.version_after
                    and self.version_before != self.version_after)

    @property
    def summary(self) -> str:
        """The closing line, in the wording the CLI has always used."""
        if self.changed:
            return f"Updated: {self.version_before} -> {self.version_after}"
        if self.version_after:
            return f"Already up to date ({self.version_after})"
        return "CMDAI CODE has been successfully updated!"


class NullReporter:
    """Draws nothing. Used by tests and by any caller that only wants a result."""

    def step(self, label, work_fn, min_time=1.0, timeout=300.0, header="", gap=0):
        return work_fn()

    def message(self, text: str) -> None:
        pass

    def finish(self, text: str) -> None:
        pass


def _run(args: List[str], app_root: str):
    return subprocess.run(args, cwd=app_root, text=True, capture_output=True)


def _git_available() -> bool:
    try:
        subprocess.check_output(["git", "--version"], stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _ensure_safe_directory(app_root: str) -> None:
    """Register the checkout in git's global safe.directory if it is missing.

    Best effort and silent. A checkout on a filesystem that records no
    ownership fails every git call with "dubious ownership", which is
    invisible until the pull step; fixing it here keeps that hint off the
    screen in the normal case.
    """
    try:
        res = subprocess.run(
            ["git", "config", "--global", "--get-all", "safe.directory"],
            text=True, capture_output=True,
        )
        existing = (res.stdout or "").splitlines() if res.returncode == 0 else []
        norm_root = app_root.replace("\\", "/")
        norm_existing = [p.strip().replace("\\", "/") for p in existing]
        if norm_root in norm_existing or app_root in [p.strip() for p in existing]:
            return
        add = subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", app_root],
            text=True, capture_output=True,
        )
        if add.returncode != 0:
            # Retry with the forward-slash form: Git on Windows prefers it.
            subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", norm_root],
                text=True, capture_output=True,
            )
    except Exception:
        pass


def _ensure_upstream(app_root: str) -> None:
    """Point the current branch at a remote when it has no upstream.

    Best effort and silent, same as the safe.directory fix. A bare
    `git pull` on a fresh clone fails with "no tracking information", which is
    a confusing way to learn that.
    """
    try:
        upstream = _run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], app_root)
        if upstream.returncode == 0:
            return
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], app_root)
        cur = (branch.stdout or "").strip() if branch.returncode == 0 else ""
        candidates: List[str] = []
        if cur and cur != "HEAD":
            candidates.append(f"origin/{cur}")
        for fallback in ("origin/main", "origin/master"):
            if fallback not in candidates:
                candidates.append(fallback)
        for cand in candidates:
            verify = _run(["git", "rev-parse", "--verify", f"refs/remotes/{cand}"], app_root)
            if verify.returncode == 0:
                _run(["git", "branch", "--set-upstream-to", cand], app_root)
                return
    except Exception:
        pass


def _pull_hint(output: str, app_root: str) -> str:
    """Turn git's failure text into one actionable line, or "" if unrecognized."""
    if "dubious ownership" in output:
        return f"Hint: run: git config --global --add safe.directory {app_root}"
    if "no tracking information" in output:
        return ("Hint: no upstream set. Run: git branch --set-upstream-to=origin/main "
                "(or origin/master)")
    if "Your local changes" in output or "would be overwritten" in output:
        return ("Hint: you have local changes. Run: git stash push -m update-backup, "
                "then retry update.")
    return ""


def _filter_pip_output(text: str, keep: int = 15) -> str:
    lines: List[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if any(noise in s for noise in _PIP_NOISE):
            continue
        if s.startswith("[notice]"):
            continue
        lines.append(s)
    return "\n".join(lines[-keep:])


def _display_version(app_root: str, releases_module) -> str:
    """Current version, preferring the release cache over the local tag.

    `root=app_root` matters: the release layer otherwise inspects the checkout
    holding the running code, not the one being updated, and would report the
    same version before and after the pull.
    """
    try:
        ver = (releases_module.get_display_version(root=app_root) or "").strip()
        if ver and not ver.lower().startswith("v"):
            ver = f"v{ver}"
        return ver
    except Exception:
        pass
    try:
        tag = _run(["git", "describe", "--tags", "--abbrev=0"], app_root)
        ver = (tag.stdout or "").strip() if tag.returncode == 0 else ""
        if ver and not ver.lower().startswith("v"):
            ver = f"v{ver}"
        return ver
    except Exception:
        return ""


def run_update(
    app_root: str,
    reporter=None,
    releases_module=None,
    repo_url: str = REPO_URL,
    on_updated: Optional[Callable[[str, str], None]] = None,
) -> UpdateResult:
    """Pull the latest CMDAI CODE without touching the user's personal config.

    on_updated(version_before, version_after) fires only when the run landed
    and the version actually moved, which is the signal the post-update
    changelog notice is built on.
    """
    if reporter is None:
        reporter = NullReporter()
    if releases_module is None:
        from . import releases as releases_module  # noqa: PLC0415

    result = UpdateResult(ok=False)

    if not _git_available():
        reporter.message("[!] Error: Git is not installed or not found in system PATH.")
        reporter.message(f"[!] You can re-clone manually: git clone {repo_url}")
        return result

    _ensure_safe_directory(app_root)
    _ensure_upstream(app_root)

    result.version_before = _display_version(app_root, releases_module)

    # ---------------------------------------------------------------- pull
    def _pull_work() -> Tuple[str, str]:
        res = _run(["git", "pull", "--rebase", "--autostash"], app_root)
        return ("OK" if res.returncode == 0 else "FAIL", f"{res.stdout or ''}\n{res.stderr or ''}")

    status, out = reporter.step(
        "Pulling latest changes", _pull_work,
        min_time=1.2, timeout=300.0,
        header="update cmdai code", gap=3,
    )
    result.steps.append("pull")
    if status != "OK":
        hint = _pull_hint(out, app_root)
        if hint:
            reporter.message(hint)
        reporter.message("Error: update failed.")
        result.detail = out
        return result

    # ----------------------------------------------------------------- pip
    req_path = os.path.join(app_root, "requirements.txt")
    if os.path.exists(req_path):
        def _pip_work() -> Tuple[str, str]:
            pip = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", req_path,
                 "--quiet", "--disable-pip-version-check"],
                cwd=app_root, text=True, capture_output=True,
            )
            pip_text = f"{pip.stdout or ''}\n{pip.stderr or ''}"
            detail = _filter_pip_output(pip_text)
            return ("OK" if pip.returncode == 0 else "FAIL", detail)

        pip_status, pip_detail = reporter.step(
            "Synchronizing packages", _pip_work,
            min_time=1.0, timeout=900.0, gap=3,
        )
        result.steps.append("pip")
        if pip_status != "OK":
            # Deliberately not fatal. The code is already pulled; aborting here
            # would report a failed update for a dependency hiccup.
            if pip_detail.strip():
                reporter.message(pip_detail)
            reporter.message("Error: dependency sync failed (see lines above).")

    # ------------------------------------------------------------- version
    def _refresh_work() -> Tuple[str, str]:
        releases_module.get_releases(refresh=True)
        return "OK", _display_version(app_root, releases_module)

    refresh_status, version_after = reporter.step(
        "Refreshing version", _refresh_work,
        min_time=1.0, timeout=120.0,
    )
    result.steps.append("version")

    if refresh_status == "OK" and version_after:
        result.version_after = version_after
    else:
        result.version_after = result.version_before

    result.ok = True
    reporter.finish(result.summary)

    if result.changed and on_updated:
        try:
            on_updated(result.version_before, result.version_after)
        except Exception:
            pass

    return result
