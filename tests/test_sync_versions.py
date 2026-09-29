"""The packaged version comes from the release, not from a hand edit.

`package.json` and `pyproject.toml` both used to carry a version someone typed
in, so the npm package sat at 3.0.1-alpha while the release it shipped was
3.0.4-alpha - and `pyproject.toml` held `3.0.1a0`, which is not even valid
semver. Publishing now regenerates both from the release.
"""

import importlib.util
import json
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_JSON = os.path.join(REPO_ROOT, "package.json")
PYPROJECT = os.path.join(REPO_ROOT, "pyproject.toml")


def _load_sync():
    path = os.path.join(REPO_ROOT, "tools", "sync_versions.py")
    spec = importlib.util.spec_from_file_location("sync_versions", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def sync():
    return _load_sync()


def _manifest_versions():
    with open(PACKAGE_JSON, encoding="utf-8") as f:
        pkg = json.load(f).get("version", "")
    with open(PYPROJECT, encoding="utf-8") as f:
        m = re.search(r'(?m)^version = "([^"]*)"$', f.read())
    return pkg, (m.group(1) if m else "")


# ------------------------------------------------------------------ the source

def test_the_version_comes_from_the_bundled_release(sync, monkeypatch):
    monkeypatch.setattr(sync.R, "get_bundled_release", lambda: {"tag": "v3.1.2-beta"})
    assert sync.source_version() == "3.1.2-beta"


def test_the_tag_prefix_is_stripped(sync, monkeypatch):
    """A tag is `v3.1.2`, a semver version is `3.1.2`."""
    monkeypatch.setattr(sync.R, "get_bundled_release", lambda: {"tag": "V3.1.2"})
    assert sync.source_version() == "3.1.2"


def test_it_falls_back_to_a_local_tag(sync, monkeypatch, capsys):
    """A fresh checkout that has not regenerated the constant still resolves."""
    monkeypatch.setattr(sync.R, "get_bundled_release", lambda: {})
    monkeypatch.setattr(sync.R, "get_local_git_version", lambda: "v3.0.5-alpha")
    assert sync.source_version() == "3.0.5-alpha"


def test_nothing_to_sync_is_an_error_not_a_blank_version(sync, monkeypatch):
    """An empty version would be a silent publish-time disaster."""
    monkeypatch.setattr(sync.R, "get_bundled_release", lambda: {})
    monkeypatch.setattr(sync.R, "get_local_git_version", lambda: "")
    with pytest.raises(SystemExit):
        sync.source_version()


def test_a_tag_that_is_not_a_version_is_rejected(sync, monkeypatch):
    monkeypatch.setattr(sync.R, "get_bundled_release", lambda: {"tag": "nightly"})
    with pytest.raises(SystemExit):
        sync.source_version()


# ------------------------------------------------------------------- the check

def test_check_fails_when_a_manifest_is_behind(sync, monkeypatch, capsys):
    monkeypatch.setattr(sync, "source_version", lambda: "3.1.0")
    monkeypatch.setattr(sync, "read_current", lambda: {"package.json": "3.0.0",
                                                       "pyproject.toml": "3.1.0"})
    assert sync.main(["--check"]) == 1
    assert "stale" in capsys.readouterr().out


def test_check_passes_when_both_agree(sync, monkeypatch, capsys):
    monkeypatch.setattr(sync, "source_version", lambda: "3.1.0")
    monkeypatch.setattr(sync, "read_current", lambda: {"package.json": "3.1.0",
                                                       "pyproject.toml": "3.1.0"})
    assert sync.main(["--check"]) == 0
    assert "ok" in capsys.readouterr().out


def test_the_shipped_manifests_are_in_sync_right_now():
    """The guard that matters: nobody can hand-edit a version and commit it."""
    from cmdai.core.releases import get_bundled_release

    tag = (get_bundled_release().get("tag") or "").lstrip("vV")
    pkg, py = _manifest_versions()
    assert pkg == tag, f"package.json is {pkg}, release is {tag}"
    assert py == tag, f"pyproject.toml is {py}, release is {tag}"


# ------------------------------------------------------------------ the writer

def test_writing_leaves_the_rest_of_pyproject_alone(sync, tmp_path):
    target = tmp_path / "pyproject.toml"
    original = (
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        "\n"
        "[project]\n"
        'name = "cmdai-code"\n'
        'version = "0.0.1"\n'
        'requires-python = ">=3.10"\n'
        "\n"
        "[tool.pytest.ini_options]\n"
        "# a comment that must survive\n"
        'testpaths = ["tests"]\n'
    )
    target.write_text(original, encoding="utf-8")
    sync.PYPROJECT = str(target)

    assert sync.sync_pyproject("3.2.0") is True
    after = target.read_text(encoding="utf-8")
    assert 'version = "3.2.0"' in after
    assert 'requires = ["setuptools>=68"]' in after
    assert "# a comment that must survive" in after
    assert 'testpaths = ["tests"]' in after
    assert 'requires-python = ">=3.10"' in after
    # Only the version line moved.
    assert len(after.splitlines()) == len(original.splitlines())


def test_writing_is_a_no_op_when_already_correct(sync, tmp_path):
    target = tmp_path / "package.json"
    target.write_text('{\n  "name": "cmdai-code",\n  "version": "3.2.0"\n}\n',
                      encoding="utf-8")
    sync.PACKAGE_JSON = str(target)
    assert sync.sync_package_json("3.2.0") is False
    assert sync.sync_package_json("3.3.0") is True
    with open(target, encoding="utf-8") as f:
        data = json.load(f)
    assert data["name"] == "cmdai-code"
    assert data["version"] == "3.3.0"


def test_a_pyproject_without_a_version_line_is_an_error(sync, tmp_path):
    """Silently writing nothing would leave the package mis-versioned."""
    target = tmp_path / "pyproject.toml"
    target.write_text("[project]\nname = \"x\"\n", encoding="utf-8")
    sync.PYPROJECT = str(target)
    with pytest.raises(SystemExit):
        sync.sync_pyproject("3.2.0")
