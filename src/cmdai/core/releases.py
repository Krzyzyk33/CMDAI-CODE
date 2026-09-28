import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

REPO = "Krzyzyk33/CMDAI-CODE"
API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=50"
MIN_TAG = (3, 0, 0, "alpha")

# The newest release is baked into the source so the app can always say what
# changed, with no network. Managed by tools/gen_bundled_release.py: at every
# release the old entry is replaced by the new one. Everything older is fetched
# from GitHub at runtime, so this holds exactly one release, not a changelog.
# --- BUNDLED_RELEASE_BEGIN (managed by tools/gen_bundled_release.py) ---
BUNDLED_RELEASE = {
    "tag": "v3.0.4-alpha",
    "name": "CMDAI CODE v3.0.4-alpha",
    "published_at": "2026-09-28",
    "url": "https://github.com/Krzyzyk33/CMDAI-CODE/releases/tag/v3.0.4-alpha",
    "body": """### Highlights & Core Fixes

#### 1. Subagents Unlocked

- **One-Call Pack Spawning** : The main agent can now spawn a pack of subagents in a single tool call. Each subagent appears in the chat as a single marker showing its role, the currently active tool + path, and finally `complete`.
- **Real Chat Window** : Clicking a marker opens a full-screen subagent chat window built on the exact same chat code as the main agent — same turn cards, same tool blocks, same footer. No custom renderer.
- **Full-Screen, No Scrollbar** : Window is fullscreen (scrolling still works). A TASK dock shows the raw original task from the start. Arrow keys ← → switch between subagents.
- **True Context Isolation** : Every subagent has its own context and its own todo list. Only the final report + notes return to the main agent. Transcript and raw tool output stay private to the subagent. The report is the single data channel — it must carry concrete paths, line numbers and values, not a narrative of what the subagent “did”.
- **Notes via Tool** : Notes are sent with `<tool:agent_note text="..." />` and arrive at the main agent verbatim. In the subagent window they look and behave like any other tool (clickable, same color).
- **Tool Restrictions** : Subagents get every tool except `subagent` (no nesting) and `ask` (nobody can answer mid-turn — they receive a clear error with the correct alternative).
- **Execution Order** : Local models run subagents sequentially; API models run them in parallel.

#### 2. Technical Improvements

- **Clean Main Chat** : Subagent tool calls use `notify=False`, so the main chat only shows one marker per subagent and the TODO bar does not light up for their work.
- **Fixed `resolve_path` Crash** : `ctx` was defaulting to `None` on tool calls, breaking `ls`, `read`, `glob` and `search` with `'NoneType' object has no attribute 'resolve_path'`. This also affected the main agent.
- **Unified Tool-Tag Parser** : One parser instead of three separate copies. Correctly handles `<tool:…>`, Gemma’s `<|tool_call>call:…>` and `<tool_call>`. Previously a local-model subagent executed zero tools and dumped raw protocol into the window as text.
- **Incremental Window Growth** : Subagent window grows incrementally instead of rebuilding the card 5 times per second (no more full-window flicker).
- **Honest Failure Status** : A subagent whose tools all failed now reports failure instead of `completed` (its report could never be based on the workspace if it never read anything).
- **Cleaner Subagent UI** : Removed the Thinking row and stopped pointing at tools that had already finished.

#### 3. Bug Fixes

- Path tools (`ls`, `read`, `glob`, `search`) crashed across the entire application, not only inside subagents (`resolve_path` with `ctx=None`).
- Subagent window flickered while working and displayed already-finished tools as active.
- Local-model subagents using Gemma syntax never executed any tools; raw tags appeared as plain text in the window.
- Raw `<|tool_call>` / `." />` syntax could leak into the main chat as prose.

---

### Experimental & In-Progress Features (`--alpha`)

- **Subagents** : Now unlocked and usable. Multi-agent orchestration, context isolation and dedicated windows are live. `--alpha`
- **Direct CLI Execution** : Terminal-first commands to run tasks and updates without launching the full TUI. `--alpha`
- **In-App Changelog** : Dynamic GitHub release list (≥ v3.0-alpha) with post-update notifications. `--alpha`
- **MCP (Model Context Protocol)** : External tool-server support via `mcp_config.example.json` with autostart and live status. `--alpha`
- **Vision & Screenshot Tools** : Available only for multimodal / vision-capable models. `--alpha`""",
}
# --- BUNDLED_RELEASE_END ---


def get_bundled_release() -> Dict[str, Any]:
    """The release baked into the source, or {} when none is set yet."""
    if not BUNDLED_RELEASE.get("tag"):
        return {}
    return dict(BUNDLED_RELEASE)


def is_newer(tag_a: str, tag_b: str) -> bool:
    """True when tag_a sorts strictly above tag_b.

    Version tags never compare correctly as strings: v3.0.10 sorts below
    v3.0.9. Everything that picks between two release candidates goes through
    here rather than a bare comparison.
    """
    if not tag_a:
        return False
    if not tag_b:
        return True
    return _tag_sort_key(tag_a) > _tag_sort_key(tag_b)


def resolve_newest(fetched: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The newest release to show, bundled or freshly fetched.

    The bundled entry is the floor: it is what the notice and the top of
    /changelog render when there is no network, so the app can always say what
    changed. When a fetch succeeds and brings something newer - a release
    published after this build shipped - that wins, so the notice is never
    stale just because the checkout has not been regenerated yet.

    `fetched` is a list as returned by get_releases(), already newest-first.
    An empty or missing list means "no network": the bundled entry is used and
    nothing raises.
    """
    bundled = get_bundled_release()
    newest_fetched = None
    for r in (fetched or []):
        if isinstance(r, dict) and r.get("tag"):
            newest_fetched = r
            break
    if newest_fetched is None:
        return bundled
    if not bundled:
        return dict(newest_fetched)
    if is_newer(str(newest_fetched.get("tag", "")), str(bundled.get("tag", ""))):
        return dict(newest_fetched)
    return bundled


def get_release_list(fetched: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Every release the changelog should offer, newest first.

    The bundled release always leads the list, whether or not there is a
    network, because that is the entry a user is most likely to be looking for
    right after an update. Anything older comes from the fetch, or from the
    on-disk cache when the fetch failed.

    Each entry carries `has_notes`. An entry without one can still be listed -
    the tag and the date are known - but opening it has nothing to show, which
    is the case the changelog window reports as an offline condition rather
    than rendering an empty page.
    """
    entries: List[Dict[str, Any]] = []
    seen = set()

    bundled = get_bundled_release()
    if bundled:
        entry = dict(bundled)
        entry["has_notes"] = bool(str(entry.get("body") or "").strip())
        entry["bundled"] = True
        entries.append(entry)
        seen.add(str(entry.get("tag", "")))

    for r in (fetched or []):
        if not isinstance(r, dict):
            continue
        tag = str(r.get("tag", ""))
        if not tag or tag in seen:
            continue
        seen.add(tag)
        entry = dict(r)
        entry["has_notes"] = bool(str(entry.get("body") or "").strip())
        entry["bundled"] = False
        entries.append(entry)

    entries.sort(key=lambda e: _tag_sort_key(str(e.get("tag", ""))), reverse=True)
    return entries


def offline_notice_text(tag: str = "") -> str:
    """Shown when a release was listed but its notes are not on disk.

    English on purpose: it is a diagnostic about the connection rather than
    app content, and the other diagnostics in the changelog are English.
    """
    if tag:
        return (f"Release notes for {tag} are not available offline. "
                f"Connect to the internet and try again.")
    return "Release notes are not available offline. Connect to the internet and try again."


# --------------------------------------------------------------- update notice

def notice_path() -> str:
    """Where the "an update landed" note waits for the next launch."""
    return os.path.join(_app_root(), "cache", "pending_changelog.json")


def last_seen_path() -> str:
    """The version this installation last started on."""
    return os.path.join(_app_root(), "cache", "last_seen_version.json")


def write_pending_notice(previous: str, version: str) -> bool:
    """Record that an update moved the checkout from `previous` to `version`.

    Stores only the version transition. The notes themselves come from
    BUNDLED_RELEASE at display time, so the notice renders with no network -
    which is the normal case, because a machine that just updated is often the
    one that cannot reach GitHub.
    """
    try:
        os.makedirs(os.path.dirname(notice_path()), exist_ok=True)
        with open(notice_path(), "w", encoding="utf-8") as f:
            json.dump({"previous": previous or "", "version": version or ""}, f)
        return True
    except Exception:
        return False


def read_pending_notice() -> Optional[Dict[str, str]]:
    """The pending notice if there is one, else None. Never raises."""
    try:
        with open(notice_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("version"):
            return None
        return {"previous": str(data.get("previous", "")), "version": str(data["version"])}
    except Exception:
        return None


def clear_pending_notice() -> None:
    try:
        os.remove(notice_path())
    except OSError:
        pass


def read_last_seen_version() -> str:
    try:
        with open(last_seen_path(), "r", encoding="utf-8") as f:
            return str(json.load(f).get("tag", "") or "")
    except Exception:
        return ""


def mark_version_seen(tag: str) -> None:
    try:
        path = last_seen_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"tag": tag or ""}, f)
    except Exception:
        pass


def detect_version_change(current: str) -> Optional[Dict[str, str]]:
    """Notice the version moved, without nagging about anything else.

    Returns {"previous", "version"} to show a notice, or None to stay quiet,
    and records `current` either way so the next launch compares against it.

    Quiet on a first run, and quiet on a downgrade. Both are normal: a fresh
    clone has no history to report, and switching to an older tag or a feature
    branch is not something the user needs a changelog popup about. Only a
    move forward is worth interrupting them for.
    """
    current = (current or "").strip()
    previous = read_last_seen_version()
    if current:
        # Only a resolved version carries information. Overwriting the baseline
        # with "" would drop it on a launch where tags and the network are both
        # unavailable, and the next real update would then have nothing to
        # compare against.
        mark_version_seen(current)
    if not current or not previous or current == previous:
        return None
    if not is_newer(current, previous):
        return None
    return {"previous": previous, "version": current}


def _app_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _cache_path() -> str:
    return os.path.join(_app_root(), "cache", "releases.json")


def parse_tag(tag: str) -> Tuple[int, int, int, str]:
    t = tag.strip().lstrip("vV")
    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?(?:[-.]([A-Za-z0-9.]+))?", t)
    if not m:
        return (0, 0, 0, "")
    major, minor = int(m.group(1)), int(m.group(2))
    patch = int(m.group(3)) if m.group(3) is not None else 0
    suffix = (m.group(4) or "").lower()
    return (major, minor, patch, suffix)


def _suffix_rank(suffix: str) -> int:
    if not suffix or suffix.startswith("final") or suffix.startswith("stable"):
        return 99
    if "alpha" in suffix:
        return 1
    if "beta" in suffix:
        return 2
    if "rc" in suffix:
        return 3
    return 0


def is_included(tag: str) -> bool:
    major, minor, patch, suffix = parse_tag(tag)
    mm, nn, pp, ss = MIN_TAG
    if (major, minor, patch) < (mm, nn, pp):
        return False
    if (major, minor, patch) > (mm, nn, pp):
        return True
    return _suffix_rank(suffix) >= _suffix_rank(ss)


def fetch_releases(timeout: float = 8.0) -> List[Dict[str, Any]]:
    import requests

    resp = requests.get(
        API_URL,
        headers={"User-Agent": "CMDAI-CODE", "Accept": "application/vnd.github+json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for r in data if isinstance(data, list) else []:
        tag = str(r.get("tag_name", ""))
        if not tag or not is_included(tag):
            continue
        out.append(
            {
                "tag": tag,
                "name": r.get("name") or tag,
                "body": r.get("body") or "",
                "published_at": (r.get("published_at") or "")[:10],
                "prerelease": bool(r.get("prerelease")),
                "url": r.get("html_url") or "",
            }
        )
    # Newest first - the UI lists releases top-down.
    out.sort(key=lambda x: _tag_sort_key(x["tag"]), reverse=True)
    return out


def save_cache(releases: List[Dict[str, Any]]) -> None:
    try:
        path = _cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(releases, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_cache() -> List[Dict[str, Any]]:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
            return [r for r in data if is_included(str(r.get("tag", "")))]
    except Exception:
        return []


def get_releases(refresh: bool = True) -> Tuple[List[Dict[str, Any]], str]:
    if refresh:
        try:
            rels = fetch_releases()
            save_cache(rels)
            render_changelog_md(rels)
            return rels, "github"
        except Exception:
            pass
    cached = load_cache()
    if cached:
        return cached, "cache"
    return [], "none"


def _tag_sort_key(tag: str) -> Tuple[int, int, int, int, str]:
    major, minor, patch, suffix = parse_tag(tag)
    return (major, minor, patch, _suffix_rank(suffix), str(tag))


def get_local_git_version(root: str = "") -> str:
    """Newest local git tag (e.g. v3.0.1-alpha). Empty string when unavailable.

    `root` overrides which checkout is inspected. Without it the root is derived
    from this file's location, which is wrong whenever the caller is working on
    a different checkout than the one holding the running code: the updater
    pulls into its own app_root, so it has to report that directory's version
    or it concludes nothing changed and never shows a notice.
    """
    import subprocess

    try:
        if not root:
            here = os.path.dirname(os.path.abspath(__file__))
            root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        desc = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=root, text=True, capture_output=True, timeout=10,
        )
        candidates = []
        if desc.returncode == 0 and (desc.stdout or "").strip():
            candidates.append((desc.stdout or "").strip())
        tags = subprocess.run(
            ["git", "tag", "--list"],
            cwd=root, text=True, capture_output=True, timeout=10,
        )
        if tags.returncode == 0:
            candidates.extend(t.strip() for t in (tags.stdout or "").splitlines() if t.strip())
        candidates = [c for c in candidates if is_included(c)]
        if not candidates:
            return ""
        return sorted(candidates, key=_tag_sort_key)[-1].strip()
    except Exception:
        return ""


def get_display_version(root: str = "") -> str:
    candidates: List[str] = []
    try:
        cached = load_cache()
        if cached:
            cached_sorted = sorted(cached, key=lambda r: _tag_sort_key(str(r.get("tag", ""))))
            tag = str(cached_sorted[-1].get("tag", "")).strip()
            if tag:
                candidates.append(tag)
    except Exception:
        pass
    try:
        local_tag = get_local_git_version(root)
        if local_tag:
            candidates.append(local_tag)
    except Exception:
        pass
    try:
        with open(os.path.join(root or _app_root(), "config.json"), "r", encoding="utf-8") as f:
            ver = str(json.load(f).get("version", "")).strip()
            if ver:
                candidates.append(ver)
    except Exception:
        pass
    if not candidates:
        return ""
    return sorted(candidates, key=_tag_sort_key)[-1].strip()


def _generated_changelog_path() -> str:
    """Where the rendered changelog goes.

    Under cache/, never at the repo root. It used to overwrite the tracked
    CHANGELOG.md on every successful fetch, so merely starting the app with a
    network connection left the working tree dirty and the next
    `cmdai code update` had local changes to reconcile.
    """
    return os.path.join(_app_root(), "cache", "CHANGELOG.md")


def render_changelog_md(releases: List[Dict[str, Any]]) -> str:
    lines = ["# CHANGELOG", "", "Generowane z GitHub Releases (>= v3.0-alpha).", ""]
    for r in releases:
        lines.append(f"## [{r['tag']}] - {r.get('published_at', '')}")
        lines.append(f"### {r.get('name', '')}")
        body = (r.get("body") or "").strip()
        lines.append(body if body else "_Brak opisu._")
        if r.get("url"):
            lines.append(f"\n[{r['tag']}]({r['url']})")
        lines.append("")
    text = "\n".join(lines)
    try:
        path = _generated_changelog_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    return text
