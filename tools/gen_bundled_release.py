"""Rewrite the release notes baked into `core/releases.py`.

`BUNDLED_RELEASE` is the one release the app can always show, with no network.
It is what the post-update notice renders and what the newest entry in
`/changelog` opens from, so a user who is offline after an update still sees
what changed. Everything older comes from GitHub at runtime.

Run it when cutting a release:

    python tools/gen_bundled_release.py --from-github     # cut a release
    python tools/gen_bundled_release.py --from-changelog  # seed from a local CHANGELOG.md
    python tools/gen_bundled_release.py --check           # CI: fail if out of date

`--check` never writes. It exits 1 when the bundled tag is not the newest
release on GitHub, which is what stops the constant from silently going stale
after the second release.
"""

import argparse
import json
import os
import re
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(APP_ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cmdai.core import releases as R  # noqa: E402

RELEASES_PY = os.path.join(SRC, "cmdai", "core", "releases.py")
CHANGELOG_MD = os.path.join(APP_ROOT, "CHANGELOG.md")

BEGIN = "# --- BUNDLED_RELEASE_BEGIN (managed by tools/gen_bundled_release.py) ---"
END = "# --- BUNDLED_RELEASE_END ---"


def render_constant(rel: dict) -> str:
    """Serialize a release dict as a readable Python literal.

    Markdown bodies are the normal case, so the body goes in a triple-quoted
    string. Two shapes break that and fall back to an escaped literal: a body
    containing the delimiter, and one ending in a backslash (which would
    escape the closing quotes).
    """
    body = str(rel.get("body") or "").strip()
    if '"""' not in body and not body.endswith("\\"):
        body_repr = '"""' + body + '"""'
    else:
        body_repr = repr(body)

    # json.dumps rather than repr so the scalars come out double-quoted, which
    # is what the rest of releases.py uses.
    def field(key):
        return json.dumps(str(rel.get(key, "")))

    return "\n".join([
        BEGIN,
        "BUNDLED_RELEASE = {",
        f"    \"tag\": {field('tag')},",
        f"    \"name\": {field('name')},",
        f"    \"published_at\": {field('published_at')},",
        f"    \"url\": {field('url')},",
        f"    \"body\": {body_repr},",
        "}",
        END,
    ])


def replace_block(text: str, block: str) -> str:
    start = text.find(BEGIN)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(
            f"{RELEASES_PY}: bundled-release markers not found. Re-add them "
            f"around BUNDLED_RELEASE before running this script."
        )
    return text[:start] + block + text[end + len(END):]


def read_block_tag() -> str:
    """The tag currently baked into the source, or "" if unset."""
    try:
        with open(RELEASES_PY, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    m = re.search(r'"tag":\s*"([^"]*)"', text)
    return m.group(1) if m else ""


def write_block(block: str) -> None:
    with open(RELEASES_PY, "r", encoding="utf-8") as f:
        text = f.read()
    with open(RELEASES_PY, "w", encoding="utf-8", newline="\n") as f:
        f.write(replace_block(text, block))


def from_github() -> dict:
    try:
        rels = R.fetch_releases()
    except Exception as e:
        # The unauthenticated GitHub API allows 60 requests an hour per IP, and
        # this script is easy to run in a loop. Say so plainly instead of
        # dumping a traceback at someone cutting a release.
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 403:
            raise SystemExit(
                "GitHub API rate limit exceeded (403). Wait, or set GITHUB_TOKEN "
                "and export it before running this script."
            )
        if status == 404:
            raise SystemExit("GitHub returned 404 for the releases endpoint.")
        raise SystemExit(f"Could not reach the GitHub releases API: {e}")
    if not rels:
        raise SystemExit("GitHub returned no releases >= v3.0-alpha; nothing to bundle.")
    return rels[0]


def from_changelog(path: str) -> dict:
    """Parse the newest section out of a CHANGELOG.md.

    The changelog is generated from the release bodies in the first place, so
    it is a faithful seed when there is no network at release time.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    head = re.search(
        r"^## \[(?P<tag>[^\]]+)\]\s*-\s*(?P<date>[\d-]*)\s*\n"
        r"### (?P<name>[^\n]*)\n"
        r"(?P<body>.*?)"
        r"^\[(?P=tag)\]\((?P<url>[^)]+)\)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    if not head:
        raise SystemExit(f"Could not parse the newest release out of {path}.")

    body = head.group("body")
    # The generator drops the "Highlights & Core Fixes" scaffolding level; keep
    # the body as authored minus the trailing whitespace.
    return {
        "tag": head.group("tag").strip(),
        "name": head.group("name").strip(),
        "published_at": head.group("date").strip(),
        "url": head.group("url").strip(),
        "body": body.strip(),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-github", action="store_true", help="fetch the newest release and bake it in")
    src.add_argument("--from-changelog", action="store_true", help="bake in the newest local CHANGELOG.md section")
    src.add_argument("--check", action="store_true", help="verify the baked-in tag is the newest on GitHub")
    args = ap.parse_args(argv)

    if args.check:
        newest = from_github()
        current = read_block_tag()
        if current != newest.get("tag"):
            print(f"stale: bundled {current or '(none)'!r}, GitHub newest {newest.get('tag')!r}")
            print("run: python tools/gen_bundled_release.py --from-github")
            return 1
        print(f"ok: bundled release {current} matches GitHub")
        return 0

    rel = from_github() if args.from_github else from_changelog(CHANGELOG_MD)
    if not R.is_included(str(rel.get("tag", ""))):
        raise SystemExit(f"refusing to bundle {rel.get('tag')!r}: below the v3.0-alpha floor")
    before = read_block_tag()
    write_block(render_constant(rel))
    if before and before != rel["tag"]:
        print(f"replaced {before} with {rel['tag']}")
    print(f"bundled {rel['tag']} ({rel.get('published_at', '')}) into core/releases.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
