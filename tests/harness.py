"""Fakes for the update-path tests.

`handle_update` / `run_update` reach the outside world through exactly three
doors: `subprocess.check_output`, `subprocess.run`, and the step animator.
Faking those three is enough to characterize the whole flow with no git, no
network and no terminal.
"""

import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

import cmdai.cli as cli_module
import cmdai.core.releases as releases_module

_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def is_path_arg(arg: str) -> bool:
    """True for filesystem paths, False for git revs and flags.

    Routes and lookups match on the non-path tokens of an argv. Without this,
    a needle like "pull" also matches a temp directory whose name was derived
    from the test name (`test_pull_uses_.../app_root`), and a needle like
    "config" matches any checkout path.

    The test suite is Windows-only, matching the app itself, so a token is
    treated as a path when it carries a backslash or a drive letter. Git
    revisions such as `refs/remotes/origin/main` use forward slashes and are
    correctly left in the match set.
    """
    if _DRIVE_RE.match(arg):
        return True
    return os.sep in arg


class FakeResult:
    """Stands in for `subprocess.CompletedProcess`."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeSubprocess:
    """Records every `subprocess.run` call and replies from a routing table.

    Routes are matched on a substring of the joined argv, so a test only has
    to describe the calls it cares about:

        fake.on("pull", returncode=1, stdout="", stderr="no tracking information")

    The first matching route wins; `fallback` answers everything else.
    """

    def __init__(self, fallback: Optional[FakeResult] = None):
        self.routes: List[tuple] = []
        self.calls: List[Dict[str, Any]] = []
        self.fallback = fallback or FakeResult()

    def on(self, needle: str, returncode: int = 0, stdout: str = "", stderr: str = "") -> "FakeSubprocess":
        self.routes.append((needle, FakeResult(returncode, stdout, stderr)))
        return self

    def on_argv(self, needle: str, fn) -> "FakeSubprocess":
        """Route to a callable receiving the argv list.

        Needed when two calls share a substring but differ in an argument, e.g.
        `git config --add safe.directory` is called once with a backslash path
        and, if that fails, again with a forward-slash one.
        """
        self.routes.append((needle, fn))
        return self

    def check_output(self, args, **kwargs):
        argv = [str(a) for a in args]
        self.calls.append({"args": argv, "kwargs": kwargs, "via": "check_output"})
        joined = " ".join(self._match_text(argv))
        if joined.startswith("git --version"):
            return b"git version 2.44.0"
        for needle, result in self.routes:
            if needle in joined:
                return result
        raise subprocess.CalledProcessError(1, args, "", "")

    def run(self, args, **kwargs):
        argv = [str(a) for a in args]
        self.calls.append({"args": argv, "kwargs": kwargs, "via": "run"})
        joined = " ".join(self._match_text(argv))
        for needle, result in self.routes:
            if needle in joined:
                return result(argv) if callable(result) else result
        return self.fallback

    @staticmethod
    def _match_text(argv: List[str]) -> List[str]:
        return [a for a in argv if not is_path_arg(a)]

    def ran(self, needle: str) -> bool:
        return any(needle in " ".join(self._match_text(c["args"])) for c in self.calls)

    def argv_for(self, needle: str) -> List[str]:
        for c in self.calls:
            if needle in " ".join(self._match_text(c["args"])):
                return c["args"]
        return []


class FakeAnimator:
    """Drop-in for `tools/install_anim.py` used as a module.

    Records the step parameters the caller asked for and polls the wait file
    the same way the real animator does, so `_animated_step` keeps its timing
    semantics without actually waiting.
    """

    def __init__(self, delay: float = 0.0):
        self.steps: List[Dict[str, Any]] = []
        self.footers: List[str] = []
        self.delay = delay

    def animate(self, label, wait_file="", fixed=0.0, min_time=0.0, timeout=900.0,
                header="", gap=0, footer=""):
        self.steps.append({
            "label": label,
            "min_time": min_time,
            "timeout": timeout,
            "header": header,
            "gap": gap,
        })
        if self.delay:
            time.sleep(self.delay)
        status = ""
        deadline = time.time() + min(timeout if timeout > 0 else 5.0, 5.0)
        while time.time() < deadline:
            try:
                with open(wait_file, "r", encoding="utf-8") as f:
                    status = f.read().strip().upper()
            except OSError:
                status = ""
            if status in ("OK", "FAIL"):
                break
            time.sleep(0.01)
        return status or "TIMEOUT"

    def print_footer(self, text):
        self.footers.append(str(text))

    def labels(self) -> List[str]:
        return [s["label"] for s in self.steps]


class GitMissing:
    """Makes `git --version` look unrunnable, as on a machine without git."""

    def __enter__(self):
        self._real = subprocess.check_output

        def _boom(*a, **kw):
            raise FileNotFoundError("git")

        subprocess.check_output = _boom
        return self

    def __exit__(self, *exc):
        subprocess.check_output = self._real
        return False


def fake_app_root(tmp_path, with_requirements=True, config='{"api_keys": {}}'):
    """Build a throwaway app root for the update path to operate on.

    `handle_update` only ever touches `<APP_ROOT>/requirements.txt` and never
    writes `config.json`, so an empty directory is a faithful stand-in and
    keeps the test away from the real checkout.
    """
    root = tmp_path / "app_root"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(config, encoding="utf-8")
    if with_requirements:
        (root / "requirements.txt").write_text("textual>=0.70.0\n", encoding="utf-8")
    return root


def patch_update_env(monkeypatch, animator, versions=None, releases=None,
                     app_root=None):
    """Point `cmdai.cli` at fakes for everything the update path touches.

    versions:  consecutive `_display_version()` results, e.g.
               ["v3.0.2-alpha", "v3.0.3-alpha"]. Defaults to a single stable
               version, which is the "already up to date" case.
    releases:  list of release dicts `get_releases` should return.
    app_root:  overrides the module-level APP_ROOT.
    """
    versions = list(versions) if versions else ["v3.0.3-alpha"]
    calls = {"n": 0}
    roots = []

    def _display_version(root=""):
        idx = min(calls["n"], len(versions) - 1)
        calls["n"] += 1
        roots.append(root)
        return versions[idx]

    def _get_releases(refresh=True):
        return (releases if releases is not None else []), "github"

    monkeypatch.setattr(cli_module, "_load_animator", lambda: animator)
    monkeypatch.setattr(releases_module, "get_display_version", _display_version)
    monkeypatch.setattr(releases_module, "get_releases", _get_releases)

    if app_root is not None:
        monkeypatch.setattr(cli_module, "APP_ROOT", str(app_root))

    # Exposed so a test can assert which checkout the version was read from.
    patch_update_env.last_roots = roots
    return roots


def install_fake_subprocess(monkeypatch, fake: FakeSubprocess) -> FakeSubprocess:
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(subprocess, "check_output", fake.check_output)
    return fake
