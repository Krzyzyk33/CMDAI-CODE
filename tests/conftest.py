"""Shared fixtures for the CMDAI CODE test suite.

Every test runs against the real source tree in `src/`, never against an
installed copy, so a checkout is testable without `pip install -e .`.
"""

import os
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PATH = os.path.join(APP_ROOT, "src")

if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


@pytest.fixture(autouse=True)
def isolated_release_cache(tmp_path, monkeypatch):
    """Point the release layer's cache at a per-test directory.

    Autouse, because the damage is easy and slow to spot: any test that mounts
    the app fires the post-update notice, which writes `last_seen_version.json`.
    That file is read by every later test, so one run seeds a version and the
    next run's notice fires or not depending on file order - and all of it lands
    in the checkout's real cache/.

    Tests that care about the real cache override this with their own patch,
    which applies later and wins.
    """
    from cmdai.core import releases

    root = tmp_path / "release-cache"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(releases, "_app_root", lambda: str(root))
    return root
