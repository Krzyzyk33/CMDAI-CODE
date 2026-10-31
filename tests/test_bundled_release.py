"""Group B - the release baked into the source, and the script that writes it.

The bundled entry is the only release the app can show with no network, so it
has to be present, well-formed, and above the v3.0-alpha floor. The generator
has to replace it cleanly and refuse to bake in something ineligible.
"""

import importlib.util
import os

import pytest

from cmdai.core import releases as R

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_generator():
    path = os.path.join(REPO_ROOT, "tools", "gen_bundled_release.py")
    spec = importlib.util.spec_from_file_location("gen_bundled_release", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ bundled release

def test_a_release_is_bundled():
    """The build ships with a release baked in, not an empty slot."""
    bundled = R.get_bundled_release()
    assert bundled, "BUNDLED_RELEASE is empty - run tools/gen_bundled_release.py"
    assert bundled["tag"]
    assert bundled["name"]
    assert bundled["body"]


def test_bundled_release_is_above_the_floor():
    """A bundled tag below v3.0-alpha would render a notice the list rejects."""
    assert R.is_included(R.get_bundled_release()["tag"])


def test_bundled_release_is_inside_the_marked_block():
    """The generator rewrites between its markers; a stray edit breaks it."""
    with open(R.__file__, "r", encoding="utf-8") as f:
        source = f.read()
    gen = _load_generator()
    start = source.find(gen.BEGIN)
    end = source.find(gen.END)
    assert start != -1 and end > start, "bundled-release markers are missing"
    block = source[start:end + len(gen.END)]
    assert "BUNDLED_RELEASE = {" in block


def test_get_bundled_release_is_a_copy(monkeypatch):
    """Callers must not be able to mutate the module constant."""
    monkeypatch.setitem(R.BUNDLED_RELEASE, "tag", "v9.9.9-alpha")
    assert R.get_bundled_release()["tag"] == "v9.9.9-alpha"
    assert R.BUNDLED_RELEASE["tag"] == "v9.9.9-alpha"
    # And a fresh dict each call, so a caller mutating the result is harmless.
    first = R.get_bundled_release()
    first["tag"] = "mutated"
    assert R.get_bundled_release()["tag"] == "v9.9.9-alpha"


def test_get_bundled_release_is_empty_when_unset(monkeypatch):
    monkeypatch.setattr(R, "BUNDLED_RELEASE", {"tag": "", "name": "", "body": ""})
    assert R.get_bundled_release() == {}


# ------------------------------------------------------- resolve_newest_release

def test_offline_falls_back_to_the_bundled_release():
    """No fetch, no crash: the notice still has something to show."""
    assert R.resolve_newest([])["tag"] == R.get_bundled_release()["tag"]
    assert R.resolve_newest(None)["tag"] == R.get_bundled_release()["tag"]


def test_a_newer_fetch_beats_the_bundled_release():
    """A release published after this build shipped must win."""
    newer = {"tag": "v99.0.0-alpha", "name": "Newer", "body": "b", "published_at": "2026-10-01"}
    assert R.resolve_newest([newer])["tag"] == "v99.0.0-alpha"


def test_an_older_fetch_loses_to_the_bundled_release():
    """A stale cache must not drag the notice back in time."""
    older = {"tag": "v0.0.1-alpha", "name": "Ancient", "body": "b"}
    assert R.resolve_newest([older])["tag"] == R.get_bundled_release()["tag"]


def test_an_equal_fetch_loses_to_the_bundled_release():
    """Same tag: prefer the copy in the source, which needs no network."""
    same = dict(R.get_bundled_release(), body="from the network")
    assert R.resolve_newest([same])["body"] == R.get_bundled_release()["body"]


def test_resolve_newest_uses_the_first_newest_first_entry():
    """A malformed leading entry must not become the notice."""
    junk = [{}, {"name": "no tag"}, {"tag": "v99.0.0-alpha", "body": "real"}]
    assert R.resolve_newest(junk)["tag"] == "v99.0.0-alpha"


# ------------------------------------------------------------------- generator

def test_render_constant_roundtrips_a_release():
    gen = _load_generator()
    rel = {
        "tag": "v3.1.0-alpha", "name": "CMDAI v3.1.0-alpha",
        "published_at": "2026-10-02",
        "url": "https://example.invalid/tag",
        "body": "## What changed\n\n* a thing",
    }
    block = gen.render_constant(rel)
    namespace = {}
    exec(compile(block, "<generated>", "exec"), namespace)
    assert namespace["BUNDLED_RELEASE"] == rel


def test_render_constant_survives_a_body_containing_the_delimiter():
    """A body with triple quotes must not produce an unparseable file."""
    gen = _load_generator()
    rel = {"tag": "v3.1.0-alpha", "name": "n", "published_at": "d",
           "url": "u", "body": 'a line with """ inside it'}
    block = gen.render_constant(rel)
    namespace = {}
    exec(compile(block, "<generated>", "exec"), namespace)
    assert namespace["BUNDLED_RELEASE"]["body"] == rel["body"]


def test_render_constant_survives_a_body_ending_in_a_backslash():
    """A trailing backslash would escape the closing quotes of a raw literal."""
    gen = _load_generator()
    rel = {"tag": "v3.1.0-alpha", "name": "n", "published_at": "d",
           "url": "u", "body": "line ending in a backslash \\"}
    block = gen.render_constant(rel)
    namespace = {}
    exec(compile(block, "<generated>", "exec"), namespace)
    assert namespace["BUNDLED_RELEASE"]["body"] == rel["body"]


def test_replace_block_is_idempotent():
    """Running the generator twice must not nest or duplicate the block."""
    gen = _load_generator()
    source = f"header\n{gen.BEGIN}\nOLD\n{gen.END}\nfooter\n"
    rel = {"tag": "v3.1.0-alpha", "name": "n", "published_at": "d", "url": "u", "body": "b"}
    once = gen.replace_block(source, gen.render_constant(rel))
    twice = gen.replace_block(once, gen.render_constant(rel))
    assert once == twice
    assert once.count(gen.BEGIN) == 1
    assert once.startswith("header\n")
    assert once.rstrip().endswith("footer")


def test_replace_block_refuses_a_file_without_markers():
    gen = _load_generator()
    with pytest.raises(SystemExit):
        gen.replace_block("no markers here", gen.render_constant({}))


def test_from_changelog_reads_the_newest_section(tmp_path):
    """The offline seed path: newest section out of a changelog file."""
    gen = _load_generator()
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# CHANGELOG\n\n"
        "## [v3.0.4-alpha] - 2026-10-05\n"
        "### CMDAI CODE v3.0.4-alpha\n"
        "### Highlights\n\n* newest thing\n\n"
        "[v3.0.4-alpha](https://example.invalid/v3.0.4)\n\n"
        "## [v3.0.3-alpha] - 2026-09-27\n"
        "### CMDAI CODE v3.0.3-alpha\n"
        "### Highlights\n\n* older thing\n\n"
        "[v3.0.3-alpha](https://example.invalid/v3.0.3)\n",
        encoding="utf-8",
    )
    rel = gen.from_changelog(str(path))
    assert rel["tag"] == "v3.0.4-alpha"
    assert rel["published_at"] == "2026-10-05"
    assert rel["url"] == "https://example.invalid/v3.0.4"
    assert "newest thing" in rel["body"]
    assert "older thing" not in rel["body"]


def test_from_changelog_rejects_an_unparseable_file(tmp_path):
    gen = _load_generator()
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# CHANGELOG\n\nnothing structured here\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        gen.from_changelog(str(path))


def test_check_fails_when_the_bundled_tag_is_stale(monkeypatch, capsys):
    """CI gate: a bundled tag behind GitHub must fail the build."""
    gen = _load_generator()
    monkeypatch.setattr(gen, "from_github", lambda: {"tag": "v9.9.9-alpha"})
    monkeypatch.setattr(gen, "read_block_tag", lambda: "v3.0.3-alpha")
    assert gen.main(["--check"]) == 1
    assert "stale" in capsys.readouterr().out


def test_check_passes_when_the_bundled_tag_matches(monkeypatch, capsys):
    gen = _load_generator()
    monkeypatch.setattr(gen, "from_github", lambda: {"tag": "v3.0.3-alpha"})
    monkeypatch.setattr(gen, "read_block_tag", lambda: "v3.0.3-alpha")
    assert gen.main(["--check"]) == 0
    assert "matches GitHub" in capsys.readouterr().out


def test_generator_refuses_to_bundle_below_the_floor(monkeypatch):
    """Baking in a pre-3.0 release would put a notice the list would reject."""
    gen = _load_generator()
    monkeypatch.setattr(gen, "from_github", lambda: {
        "tag": "v2.9.9", "name": "old", "published_at": "d", "url": "u", "body": "b"})
    written = []
    monkeypatch.setattr(gen, "write_block", lambda block: written.append(block))
    with pytest.raises(SystemExit):
        gen.main(["--from-github"])
    assert written == []
