"""Derive the packaged versions from the newest release, not from a hand edit.

`package.json` and `pyproject.toml` both carry a version field that npm and
pip read, and both used to be typed in by hand - so the npm package sat at
3.0.1-alpha while the release it ships was 3.0.4-alpha. The version is now
generated from the release itself and refreshed on publish.

    python tools/sync_versions.py            # write
    python tools/sync_versions.py --check    # CI: fail if stale

The source of truth is the release baked into `core/releases.py`
(BUNDLED_RELEASE, written by tools/gen_bundled_release.py), so this works with
no network. Pass --from-github to refresh the bundled release first and sync
against the freshly fetched one.

Only the two packaging manifests are touched. The `version` in config.json is a
different thing - it is a floor for the app's own version detection, and it
feeds `releases.get_display_version()`, so it is left alone.
"""

import argparse
import json
import os
import re
import sys
from typing import Dict

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(APP_ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cmdai.core import releases as R  # noqa: E402

PACKAGE_JSON = os.path.join(APP_ROOT, "package.json")
PYPROJECT = os.path.join(APP_ROOT, "pyproject.toml")


def source_version() -> str:
    """The version to publish, as a bare semver string without the tag prefix.

    Falls back to the newest local tag when nothing is bundled, so a fresh
    checkout that has not regenerated the constant still gets a usable answer
    instead of an empty version.
    """
    bundled = R.get_bundled_release().get("tag") or ""
    if not bundled:
        bundled = R.get_local_git_version()
    if not bundled:
        raise SystemExit(
            "No version to sync: BUNDLED_RELEASE is empty and there is no local "
            "tag. Run: python tools/gen_bundled_release.py --from-github"
        )
    version = bundled.strip().lstrip("vV")
    if not re.match(r"^\d+\.\d+\.\d+", version):
        raise SystemExit(f"Cannot turn tag {bundled!r} into a version.")
    return version


def _write_if_changed(path: str, old: str, new: str) -> bool:
    if old == new:
        return False
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    return True


def sync_package_json(version: str) -> bool:
    with open(PACKAGE_JSON, "r", encoding="utf-8") as f:
        original = f.read()
    data = json.loads(original)
    data["version"] = version
    # Re-serialised with indent 2, matching how the file is already written.
    updated = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return _write_if_changed(PACKAGE_JSON, original, updated)


def sync_pyproject(version: str) -> bool:
    with open(PYPROJECT, "r", encoding="utf-8") as f:
        original = f.read()
    # Only the top-level `version = "..."` under [project]. `requires = [...]`
    # and the pytest section are left exactly as they are.
    updated, n = re.subn(r'(?m)^version = "[^"]*"$', f'version = "{version}"',
                         original, count=1)
    if n != 1:
        raise SystemExit(f"{PYPROJECT}: could not find a single `version = \"...\"` line.")
    return _write_if_changed(PYPROJECT, original, updated)


def read_current() -> Dict[str, str]:
    with open(PACKAGE_JSON, "r", encoding="utf-8") as f:
        pkg = json.load(f).get("version", "")
    with open(PYPROJECT, "r", encoding="utf-8") as f:
        m = re.search(r'(?m)^version = "([^"]*)"$', f.read())
    return {"package.json": pkg, "pyproject.toml": m.group(1) if m else ""}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the manifests disagree with the release")
    args = ap.parse_args(argv)

    version = source_version()
    current = read_current()

    if args.check:
        stale = {k: v for k, v in current.items() if v != version}
        if stale:
            print(f"stale: release is {version}, manifests say {stale}")
            print("run: python tools/sync_versions.py")
            return 1
        print(f"ok: manifests at {version}")
        return 0

    changed = []
    if sync_package_json(version):
        changed.append("package.json")
    if sync_pyproject(version):
        changed.append("pyproject.toml")
    if changed:
        print(f"synced {version} into {', '.join(changed)}")
    else:
        print(f"already at {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
