"""How the CLI decides what the user asked for.

A subcommand is never reliably at position 0. The global launcher
(`cmdai.cmd`) and the npm wrapper (`bin/cmdai.js`) both inject
`--workdir <path>` ahead of the user's words, so `cmdai update`,
`cmdai code update` and `npx cmdai-code update` all arrive shaped differently
and all have to reach the same branch.

The second half of the file drives the real `main()`. The `_route` helper
above is a transcription of the branch order, so on its own it could agree with
itself while main() does something else - which is exactly how the broken
`npx cmdai-code update` survived a rewrite of the checks.
"""

import sys

import pytest

from cmdai.cli import _bare_words, _editor_target_args, _VALUE_FLAGS


@pytest.mark.parametrize("argv,expected", [
    # The three documented spellings, plus the shape the wrappers produce.
    (["update"], ["update"]),
    (["code", "update"], ["code", "update"]),
    (["--workdir", "D:\\proj", "update"], ["update"]),
    (["--workdir", "D:\\proj", "code", "update"], ["code", "update"]),
    (["--workdir=D:\\proj", "update"], ["update"]),
    (["code"], ["code"]),
    ([], []),
])
def test_a_subcommand_is_found_wherever_it_sits(argv, expected):
    assert _bare_words(argv) == expected


def test_a_flag_value_is_not_mistaken_for_a_subcommand():
    """The risk of a substring match: a path can contain the word.

    A checkout at `D:\\update\\sub` must not read as the update command.
    """
    assert _bare_words(["--workdir", "D:\\update\\sub"]) == []
    assert _bare_words(["--workdir", "D:\\addlocal", "code"]) == ["code"]


def test_every_flag_that_takes_a_value_is_listed():
    """A value flag missing from the list swallows the word after it."""
    for flag in ("--workdir", "--provider", "--model"):
        assert flag in _VALUE_FLAGS


def test_switch_flags_do_not_swallow_the_next_word():
    """--install-launcher is store_true, so nothing after it is a value."""
    assert _bare_words(["--install-launcher", "update"]) == ["update"]


def test_unrelated_flags_are_ignored_without_eating_the_next_word():
    assert _bare_words(["--no-picker", "editor"]) == ["editor"]


# ------------------------------------------------------------- editor targets

@pytest.mark.parametrize("argv,expected", [
    (["editor"], []),
    (["editor", "a.py"], ["a.py"]),
    (["--workdir", "D:\\proj", "editor", "a.py"], ["a.py"]),
    (["--workdir", "D:\\proj", "code", "editor", "a.py"], ["a.py"]),
    (["code", "editor", "src", "x.py"], ["src", "x.py"]),
    (["editor", "--no-picker"], []),
])
def test_the_editor_gets_only_its_own_words(argv, expected):
    """editor/main.py is a separate app; the launcher's flags must not leak."""
    assert _editor_target_args(argv) == expected


# ------------------------------------------------------------------- routing

def _route(argv):
    """A transcription of the branch order in main(). Cross-checked below."""
    import cmdai.cli as cli

    words = _bare_words(argv)
    first = words[0] if words else ""
    editor_early = bool(words[:2]) and "editor" in words[:2]
    if not editor_early and any(a in cli._HELP_TOKENS for a in [x.lower() for x in argv]):
        return "help"
    if "update" in words:
        return "update"
    if "addlocal" in words or "add-model" in words:
        return "addlocal"
    if first == "editor":
        return "editor-process"
    if "editor" in words:
        return "editor-inproc"
    return "launch"


@pytest.mark.parametrize("argv,expected", [
    (["update"], "update"),
    (["code", "update"], "update"),
    (["--workdir", "D:\\proj", "update"], "update"),
    (["--workdir", "D:\\proj", "code", "update"], "update"),
    (["addlocal"], "addlocal"),
    (["--workdir", "D:\\proj", "addlocal"], "addlocal"),
    (["code", "addlocal"], "addlocal"),
    (["add-model"], "addlocal"),
    (["help"], "help"),
    (["-h"], "help"),
    (["--workdir", "D:\\proj", "code", "help"], "help"),
    (["editor"], "editor-process"),
    (["--workdir", "D:\\proj", "editor"], "editor-process"),
    # `cmdai code editor` is deliberately the in-process editor, a different
    # application from `editor/main.py`. Kept as it was.
    (["code", "editor"], "editor-inproc"),
    ([], "launch"),
    (["--workdir", "D:\\proj"], "launch"),
    (["--install-launcher"], "launch"),
])
def test_the_npm_wrapper_shape_reaches_the_right_branch(argv, expected):
    """The wrapper always emits `--workdir <cwd>` first, so every shape with it
    has to work - that is what publishing the package depends on."""
    assert _route(argv) == expected


def test_editor_help_is_not_the_app_help():
    """`cmdai editor --help` must reach the editor, which handles its own flags.

    Regression: the exemption only looked at position 0, so `cmdai code editor
    --help` - the in-process editor, a different application - was swallowed by
    the app help and the user never reached it.
    """
    assert _route(["editor", "--help"]) == "editor-process"
    assert _route(["--workdir", "D:\\proj", "editor", "--help"]) == "editor-process"
    assert _route(["code", "editor", "--help"]) == "editor-inproc"


def test_a_later_editor_does_not_disable_the_app_help():
    """The exemption is deliberately limited to the first two words."""
    assert _route(["code", "launch", "editor", "--help"]) == "help"


# ------------------------------------------------- the real main(), not a copy

class _FakeSettings:
    config = {}

    def get_api_key(self, provider_id):
        return ""

    def get(self, key, default=None):
        return self.config.get(key, default)


def _run_main(monkeypatch, argv):
    """Run cli.main() with the slow branches stubbed, and report which fired."""
    import cmdai.cli as cli

    calls = {}
    launched = {}
    monkeypatch.setattr(cli, "setup_crash_logger", lambda: None)
    monkeypatch.setattr(cli, "print_cli_help", lambda: calls.setdefault("help", True))
    monkeypatch.setattr(cli, "handle_update", lambda: calls.setdefault("update", 0))
    monkeypatch.setattr(cli, "handle_add_local_model", lambda: calls.setdefault("addlocal", 0))
    monkeypatch.setattr(cli, "install_global_launcher", lambda silent=False: True)
    monkeypatch.setattr(cli, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(sys, "argv", ["cmdai.py"] + list(argv))

    class _FakeApp:
        def __init__(self, workdir=""):
            launched["workdir"] = workdir

        def run(self):
            launched["ran"] = True

    monkeypatch.setattr(cli, "CMDAICodeTUI", _FakeApp)

    def _editor(args):
        calls["editor_args"] = list(args)
        return 0

    monkeypatch.setattr(cli, "launch_cmdaieditor", _editor)

    # The in-process branch imports the editor class inside the function, so
    # patching the name in cli would not reach it. Without this the real
    # Textual app starts and the test hangs on a full-screen editor.
    import cmdai.editor.editor_app as editor_app

    class _FakeEditor:
        def __init__(self, initial_path=""):
            calls["editor_inproc"] = initial_path

        def run(self):
            calls["editor_ran"] = True

    monkeypatch.setattr(editor_app, "CMDAICodeEditor", _FakeEditor)
    try:
        cli.main()
    except SystemExit:
        pass
    return calls, launched


def test_the_npm_wrapper_shape_really_reaches_the_updater(monkeypatch):
    """The bug this fixes: `npx cmdai-code update` launched the TUI instead.

    The wrapper always emits `--workdir <cwd>` first, so `update` never sat at
    position 0 and the old `raw_args[0] == "update"` check missed it.
    """
    calls, launched = _run_main(monkeypatch, ["--workdir", "D:\\proj", "update"])
    assert calls.get("update") == 0
    assert "ran" not in launched


def test_the_documented_spellings_also_reach_the_updater(monkeypatch):
    for argv in (["update"], ["code", "update"],
                 ["--workdir", "D:\\proj", "code", "update"]):
        calls, launched = _run_main(monkeypatch, argv)
        assert calls.get("update") == 0, argv
        assert "ran" not in launched, argv


def test_addlocal_reaches_its_handler_from_the_wrapper(monkeypatch):
    calls, _ = _run_main(monkeypatch, ["--workdir", "D:\\proj", "addlocal"])
    assert "addlocal" in calls


def test_help_still_wins_for_a_plain_help(monkeypatch):
    calls, launched = _run_main(monkeypatch, ["--workdir", "D:\\proj", "code", "help"])
    assert calls.get("help") is True
    assert "ran" not in launched


def test_editor_gets_its_own_words_and_not_the_launchers_flags(monkeypatch):
    """`cmdai editor <file>` runs editor/main.py as a subprocess."""
    calls, _ = _run_main(monkeypatch,
                         ["--workdir", "D:\\proj", "editor", "a.py"])
    assert calls.get("editor_args") == ["a.py"]


def test_the_in_process_editor_is_stubbed_not_launched(monkeypatch):
    """Guard on the guard: `cmdai code editor` must not open a real window."""
    calls, _ = _run_main(monkeypatch, ["--workdir", "D:\\proj", "code", "editor"])
    assert calls.get("editor_ran") is True
    assert "editor_args" not in calls


def test_nothing_else_launches_the_app(monkeypatch):
    calls, launched = _run_main(monkeypatch, ["--workdir", "D:\\proj", "code"])
    assert not calls
    assert launched.get("ran") is True
