"""Group E - the in-app changelog list and its offline behaviour.

The rule the user asked for: the newest release is always readable, and an
older one that has not been fetched says so in English rather than rendering an
empty window.
"""

import pytest

from cmdai.core import releases as R
from cmdai.tui.modals.releases import ReleaseNoteModal, ReleasesModal


def _rel(tag, body="notes", **kw):
    d = {"tag": tag, "name": f"CMDAI {tag}", "body": body, "published_at": "2026-09-20"}
    d.update(kw)
    return d


@pytest.fixture()
def bundled(monkeypatch):
    rel = _rel("v3.0.4-alpha", "newest notes")
    monkeypatch.setattr(R, "BUNDLED_RELEASE", rel)
    return rel


# ------------------------------------------------------------- the list itself

def test_offline_lists_the_bundled_release(bundled):
    """No network, no cache: the list is still usable."""
    entries = R.get_release_list([])
    assert [e["tag"] for e in entries] == ["v3.0.4-alpha"]
    assert entries[0]["bundled"] is True
    assert entries[0]["has_notes"] is True


def test_the_bundled_release_is_not_duplicated_by_the_fetch(bundled):
    """A fetch that includes the same tag must not list it twice."""
    fetched = [_rel("v3.0.4-alpha", "remote copy"), _rel("v3.0.2-alpha")]
    entries = R.get_release_list(fetched)
    assert [e["tag"] for e in entries] == ["v3.0.4-alpha", "v3.0.2-alpha"]
    # The bundled copy wins, so the notes are the ones in the source.
    assert entries[0]["body"] == "newest notes"


def test_the_bundled_release_leads_even_when_older_than_a_fetch(bundled):
    """Ordering is by tag, not by insertion."""
    fetched = [_rel("v9.0.0-alpha", "future"), _rel("v3.0.2-alpha")]
    entries = R.get_release_list(fetched)
    assert [e["tag"] for e in entries] == ["v9.0.0-alpha", "v3.0.4-alpha", "v3.0.2-alpha"]


def test_entries_without_notes_are_flagged(bundled):
    """Listed but unreadable is a distinct state from absent."""
    entries = R.get_release_list([_rel("v3.0.1-alpha", body=""), _rel("v3.0.2-alpha")])
    flags = {e["tag"]: e["has_notes"] for e in entries}
    assert flags == {"v3.0.4-alpha": True, "v3.0.2-alpha": True, "v3.0.1-alpha": False}


def test_no_bundled_release_still_lists_the_fetch(bundled, monkeypatch):
    """A build with nothing baked in must not lose the fetched list."""
    monkeypatch.setattr(R, "BUNDLED_RELEASE", {"tag": "", "name": "", "body": ""})
    entries = R.get_release_list([_rel("v3.0.2-alpha")])
    assert [e["tag"] for e in entries] == ["v3.0.2-alpha"]


def test_malformed_entries_are_dropped(bundled):
    entries = R.get_release_list(["junk", None, {}, {"name": "no tag"}])
    assert [e["tag"] for e in entries] == ["v3.0.4-alpha"]


# ------------------------------------------------------------ the offline text

def test_the_offline_text_names_the_release():
    text = R.offline_notice_text("v3.0.1-alpha")
    assert "v3.0.1-alpha" in text
    assert "not available offline" in text


def test_the_offline_text_is_english():
    """A diagnostic about the connection, not app content."""
    text = R.offline_notice_text("v3.0.1-alpha")
    assert text.isascii()
    for word in ("Release", "notes", "Connect", "internet"):
        assert word in text


def test_the_offline_text_survives_a_missing_tag():
    assert "not available offline" in R.offline_notice_text("")


# ------------------------------------------------------------- the modal itself

def test_the_release_window_falls_back_to_the_offline_text(bundled):
    """The blank-page case is replaced by the explanation."""
    modal = ReleaseNoteModal(_rel("v3.0.1-alpha", body=""))
    # compose is a generator; the guard is the body the window will render.
    assert not str(modal.release.get("body") or "").strip()
    assert "not available offline" in R.offline_notice_text("v3.0.1-alpha")


def test_the_modal_stores_what_it_was_given(bundled):
    rel = _rel("v3.0.2-alpha", "body text")
    modal = ReleaseNoteModal(rel)
    assert modal.release["tag"] == "v3.0.2-alpha"


def test_the_list_modal_keeps_its_offline_flag(bundled):
    modal = ReleasesModal()
    assert modal.releases == []
    assert modal.offline is False


# ------------------------------------------------------------ rate limits

def test_a_rate_limited_fetch_falls_back_to_the_cache(monkeypatch, bundled, tmp_path):
    """A 403 from GitHub must not empty the changelog.

    The unauthenticated API allows 60 requests an hour per IP, and
    `get_releases` is called every time /changelog opens. When the limit is hit
    the cached list has to serve, with the bundled release still on top.
    """
    import requests

    class _Resp:
        status_code = 403

        def raise_for_status(self):
            raise requests.exceptions.HTTPError(
                "403 rate limit exceeded", response=self)

    monkeypatch.setattr(requests, "get", lambda *a, **kw: _Resp())

    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr(R, "_app_root", lambda: str(root))
    R.save_cache([_rel("v3.0.2-alpha", "cached body")])

    fetched, source = R.get_releases(refresh=True)
    assert source == "cache"
    assert [r["tag"] for r in fetched] == ["v3.0.2-alpha"]

    entries = R.get_release_list(fetched)
    assert [e["tag"] for e in entries] == ["v3.0.4-alpha", "v3.0.2-alpha"]
    assert entries[0]["has_notes"] is True


def test_a_network_error_with_no_cache_still_shows_the_bundled_release(monkeypatch,
                                                                      bundled, tmp_path):
    """Worst case: no network, no cache. One usable entry beats an error."""
    import requests

    def _boom(*a, **kw):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(requests, "get", _boom)

    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr(R, "_app_root", lambda: str(root))

    fetched, source = R.get_releases(refresh=True)
    assert fetched == []
    assert source == "none"

    entries = R.get_release_list(fetched)
    assert [e["tag"] for e in entries] == ["v3.0.4-alpha"]
    assert entries[0]["has_notes"] is True
