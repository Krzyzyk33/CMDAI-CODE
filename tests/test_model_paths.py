"""Tests for the single models-directory resolution.

Six places used to resolve this independently, four of them carrying
hardcoded D:/E:/C: paths under a "CMDAI CODE" name that stopped existing
when the checkout moved to E:\\CMDAI-CODE. These tests pin the one
function that replaced that, and pin the guarantee that mattered most:
a configured directory is used as-is instead of being second-guessed.
"""

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from cmdai.core import model_paths  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(model_paths.MODELS_DIR_ENV, raising=False)
    monkeypatch.setenv("CMDAI_CODE_ROOT", ROOT)
    yield


class TestResolutionOrder:
    def test_env_wins_over_everything(self, tmp_path, monkeypatch):
        target = tmp_path / "from-env"
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(target))
        monkeypatch.setattr(model_paths, "_from_config", lambda: str(tmp_path / "from-config"))
        assert model_paths.resolve_models_dir() == str(target)

    def test_config_used_when_no_env(self, tmp_path, monkeypatch):
        target = tmp_path / "from-config"
        monkeypatch.setattr(model_paths, "_from_config", lambda: str(target))
        assert model_paths.resolve_models_dir() == str(target)

    def test_explicit_argument_wins_over_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(tmp_path / "from-env"))
        assert model_paths.resolve_models_dir(str(tmp_path / "explicit")) == str(tmp_path / "explicit")

    def test_relative_config_is_anchored_to_app_root(self, monkeypatch):
        monkeypatch.setattr(model_paths, "_from_config", lambda: "somewhere")
        got = model_paths.resolve_models_dir()
        assert os.path.isabs(got)
        # A relative config value must not resolve against whatever
        # directory a subprocess happened to start in.
        assert got == os.path.join(ROOT, "somewhere")

    def test_falls_back_to_app_root_models(self, monkeypatch):
        monkeypatch.setattr(model_paths, "_from_config", lambda: "")
        assert model_paths.resolve_models_dir() == os.path.join(ROOT, "models")

    def test_app_root_is_the_checkout_not_src(self):
        # This file lives at <root>/src/cmdai/core/, four levels down.
        # Getting it wrong yields <root>/src/models, which is where the
        # first version of this module pointed.
        assert model_paths.app_root() == ROOT
        assert os.path.isfile(os.path.join(model_paths.app_root(), "requirements.txt"))

    def test_unloadable_config_does_not_break_resolution(self, monkeypatch):
        # The guard lives inside _from_config, so break it there rather than
        # replacing the guarded function with an unguarded one.
        import cmdai.core.settings as settings_module
        def boom(*_a, **_k):
            raise RuntimeError("config unavailable")
        monkeypatch.setattr(settings_module, "get_settings", boom)
        assert model_paths._from_config() == ""
        assert model_paths.resolve_models_dir() == os.path.join(ROOT, "models")


class TestFileLookup:
    def test_finds_by_bare_name(self, tmp_path, monkeypatch):
        (tmp_path / "m.gguf").write_bytes(b"x")
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(tmp_path))
        assert model_paths.resolve_model_file("m.gguf") == str(tmp_path / "m.gguf")

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(tmp_path))
        assert model_paths.resolve_model_file("nope.gguf") == ""

    def test_absolute_path_is_honoured(self, tmp_path, monkeypatch):
        elsewhere = tmp_path / "elsewhere.gguf"
        elsewhere.write_bytes(b"x")
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(tmp_path / "models"))
        # A caller that already resolved the file must not be second-guessed.
        assert model_paths.resolve_model_file(str(elsewhere)) == str(elsewhere)

    def test_empty_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(tmp_path))
        assert model_paths.resolve_model_file("") == ""

    def test_tilde_is_expanded(self, monkeypatch, tmp_path):
        target = tmp_path / "m.gguf"
        target.write_bytes(b"x")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(tmp_path))
        got = model_paths.resolve_model_file(os.path.join("~", "m.gguf"))
        assert got == str(target)


class TestListing:
    def test_lists_only_gguf(self, tmp_path, monkeypatch):
        (tmp_path / "a.gguf").write_bytes(b"x")
        (tmp_path / "b.GGUF").write_bytes(b"x")
        (tmp_path / "notes.txt").write_text("hi")
        (tmp_path / "sub").mkdir()
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(tmp_path))
        found = [os.path.basename(p) for p in model_paths.list_gguf_files()]
        assert sorted(found) == ["a.gguf", "b.GGUF"]

    def test_missing_directory_lists_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(tmp_path / "absent"))
        assert model_paths.list_gguf_files() == []

    def test_with_gguf_requires_an_actual_model(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(empty))
        assert model_paths.models_dir_with_gguf() is None

        (empty / "m.gguf").write_bytes(b"x")
        assert model_paths.models_dir_with_gguf() == str(empty)

    def test_absent_directory_reports_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv(model_paths.MODELS_DIR_ENV, str(tmp_path / "absent"))
        assert model_paths.models_dir_with_gguf() is None


class TestNoHardcodedPathsRemain:
    def test_no_spaces_in_hardcoded_model_dirs(self):
        """Regression guard for the old "CMDAI CODE/models" candidates.

        Those never matched once the repo moved to E:\\CMDAI-CODE, and the
        space in the name is the same class of bug as the quoting fault in
        the launcher.
        """
        import re
        offenders = []
        pattern = re.compile(r"[A-Za-z]:[/\\]+\w[\w ]*[/\\]+models")
        base = os.path.join(ROOT, "src", "cmdai")
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    for lineno, line in enumerate(handle, 1):
                        if line.lstrip().startswith("#"):
                            continue
                        if pattern.search(line):
                            offenders.append(f"{os.path.relpath(path, ROOT)}:{lineno}")
        assert not offenders, f"hardcoded model directories came back: {offenders}"


class TestCallersUseTheSingleSource:
    # Check for the hardcoded *path shape*, not the phrase. Comments here
    # legitimately name "CMDAI CODE" while explaining that it was removed.
    PATHY = re.compile(r"[A-Za-z]:[/\\]+\w[\w ]*[/\\]+models")

    @staticmethod
    def _code(func) -> str:
        import inspect
        source = inspect.getsource(func)
        # Drop comments so the explanatory ones do not trip the pattern.
        return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))

    def test_resource_limits_delegates(self):
        from cmdai.core import resource_limits
        body = self._code(resource_limits.find_local_model_file)
        assert "resolve_model_file" in body
        assert not self.PATHY.search(body)

    def test_model_worker_delegates(self):
        from cmdai.core import model_worker
        body = self._code(model_worker.ModelWorker.find_model_path)
        assert "resolve_model_file" in body
        assert not self.PATHY.search(body)

    def test_engine_delegates(self):
        from cmdai.core import engine
        body = self._code(engine.SimpleGGUFLoader.__init__)
        assert "resolve_models_dir" in body
        assert not self.PATHY.search(body)

    def test_no_bare_models_default_in_signatures(self):
        """A default of "models" would be an explicit relative path and so
        would bypass env and config entirely."""
        from cmdai.core import resource_limits
        import inspect
        for func in (resource_limits.find_local_model_file,
                     resource_limits.get_dynamic_context_limit):
            params = inspect.signature(func).parameters
            assert params["models_dir"].default == "", \
                f"{func.__name__} still defaults models_dir to a literal path"
