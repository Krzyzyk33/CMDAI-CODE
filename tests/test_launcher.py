"""The generated Windows launcher.

Two things it has to get right, both of which broke real use:

- a workdir with a trailing backslash must not swallow the subcommand, which is
  what made `cmdai code` print the usage screen when run from a drive root
- it must not hardcode the installing account's checkout, or a launcher on a
  second account runs the first account's config.json and API keys
"""

import os
import subprocess

import pytest

from cmdai.core.launcher import build_launcher_scripts


# ------------------------------------------------------------- content checks

def test_the_trailing_separator_is_stripped():
    """`"D:\"` closes the quote early and eats the next argument."""
    cmd = build_launcher_scripts(r"E:\\x")["cmdai.cmd"]
    assert 'set "USER_WORKDIR=%CD%"' in cmd
    assert 'if "%USER_WORKDIR:~-1%"=="\\"' in cmd
    assert "%USER_WORKDIR:~0,-1%" in cmd


def test_workdir_is_passed_before_the_users_own_arguments():
    """%* last, so a subcommand can never be consumed as --workdir's value."""
    cmd = build_launcher_scripts(r"E:\\x")["cmdai.cmd"]
    line = [ln for ln in cmd.splitlines() if "--workdir" in ln and "python" not in ln]
    assert line, "the launcher has no --workdir line"
    assert line[0].rstrip().endswith("%*"), line[0]


def test_the_installing_account_path_is_not_the_first_choice():
    """Regression: a launcher copied to another account must not adopt this one.

    The old order put %USERPROFILE% first and the install-time path second, so
    on a shared machine the second account's launcher found the first account's
    checkout and ran it - including its config.json and its API keys.
    """
    root = r"C:\fake-root"
    cmd = build_launcher_scripts(root)["cmdai.cmd"]
    lines = cmd.splitlines()
    first = next(i for i, ln in enumerate(lines) if "CMDAI_CODE_ROOT=" in ln and "if not defined" in ln)
    second = next(i for i, ln in enumerate(lines) if "USERPROFILE" in ln)
    assert root in lines[first]
    assert second > first, "the install-time path must be tried before %USERPROFILE%"
    # And it is still there, as the installer's own answer.
    assert root in cmd


def test_an_explicit_env_var_still_wins():
    """Setting CMDAI_CODE_ROOT has to override everything, or it is useless."""
    cmd = build_launcher_scripts(r"C:\fake-root")["cmdai.cmd"]
    assert "if not defined CMDAI_CODE_ROOT" in cmd
    assert cmd.index("if not defined CMDAI_CODE_ROOT") < cmd.index("USERPROFILE")


def test_a_missing_checkout_reports_itself():
    """Silent exit 1 would look like a broken command."""
    cmd = build_launcher_scripts(r"E:\\x")["cmdai.cmd"]
    assert "[ERROR] CMDAI CODE checkout not found" in cmd
    assert "exit /b 1" in cmd


def test_all_three_scripts_are_generated():
    scripts = build_launcher_scripts(r"E:\\x")
    assert set(scripts) == {"cmdai.cmd", "cmdai-code.cmd", "editor.cmd"}
    assert scripts["editor.cmd"].rstrip().endswith("editor %*")


# ------------------------------------------------- the real thing, via cmd.exe

def _make_probe_launcher(dest, project_root):
    """A real launcher in `dest`, wired to a script that reports its argv."""
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "cmdai.py"), "w", encoding="utf-8") as f:
        f.write("import sys\nprint('ARGV=%r' % (sys.argv[1:],))\n")
    launcher = os.path.join(dest, "cmdai.cmd")
    with open(launcher, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(build_launcher_scripts(project_root)["cmdai.cmd"])
    return launcher


def _run(launcher, cwd, *args, timeout=60):
    env = {**os.environ, "CMDAI_CODE_ROOT": os.path.dirname(launcher)}
    res = subprocess.run([launcher, *args], cwd=cwd, text=True,
                         capture_output=True, timeout=timeout, env=env)
    return (res.stdout or "") + (res.stderr or "")


@pytest.mark.skipif(os.name != "nt", reason="the launcher is Windows-only")
def test_arguments_survive_a_drive_root_workdir(tmp_path):
    """The bug: from `D:\\`, `cmdai code` reached python as `--workdir D:" code`.

    Verified through cmd.exe because cmd's quote handling is what decides this;
    a pure-python test of the string would pass while the launcher is broken.
    """
    launcher = _make_probe_launcher(str(tmp_path / "probe"), r"C:\fake-root")
    drive = os.path.splitdrive(str(tmp_path))[0] or "C:"
    out = _run(launcher, drive + "\\", "code")
    assert "ARGV=" in out, out
    assert "'code'" in out, f"the subcommand was swallowed: {out.strip()}"
    # And the workdir is the clean path, not a fragment of the argument.
    assert 'D:"' not in out, out
    assert 'C:"' not in out, out


@pytest.mark.skipif(os.name != "nt", reason="the launcher is Windows-only")
def test_arguments_survive_a_nested_workdir(tmp_path):
    """The ordinary case, kept as a control for the drive-root test."""
    launcher = _make_probe_launcher(str(tmp_path / "probe"), r"C:\fake-root")
    out = _run(launcher, str(tmp_path), "code")
    assert "'code'" in out, out
    # Compared on the tail, because ARGV is printed as a repr and every
    # backslash in the path is doubled there.
    assert os.path.basename(str(tmp_path)) in out, out


@pytest.mark.skipif(os.name != "nt", reason="the launcher is Windows-only")
def test_no_arguments_still_passes_a_workdir(tmp_path):
    """`cmdai` with nothing after it must still start in the current dir."""
    launcher = _make_probe_launcher(str(tmp_path / "probe"), r"C:\fake-root")
    out = _run(launcher, str(tmp_path))
    assert "ARGV=" in out, out
    assert "'--workdir'" in out, out
