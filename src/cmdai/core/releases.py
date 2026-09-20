import json
import os
import re
from typing import Any, Dict, List, Tuple

REPO = "Krzyzyk33/CMDAI-CODE"
API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=50"
MIN_TAG = (3, 0, "alpha")


def _app_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _cache_path() -> str:
    return os.path.join(_app_root(), "cache", "releases.json")


def parse_tag(tag: str) -> Tuple[int, int, str]:
    t = tag.strip().lstrip("vV")
    m = re.match(r"(\d+)\.(\d+)(?:[-.]([A-Za-z0-9.]+))?", t)
    if not m:
        return (0, 0, "")
    major, minor, suffix = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    return (major, minor, suffix)


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
    major, minor, suffix = parse_tag(tag)
    mm, nn, ss = MIN_TAG
    if (major, minor) < (mm, nn):
        return False
    if (major, minor) > (mm, nn):
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
    out.sort(key=lambda x: parse_tag(x["tag"]), reverse=False)
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


def get_display_version() -> str:
    try:
        cached = load_cache()
        if cached:
            cached_sorted = sorted(cached, key=lambda r: parse_tag(str(r.get("tag", ""))))
            return str(cached_sorted[-1].get("tag", "")).strip()
    except Exception:
        pass
    try:
        with open(os.path.join(_app_root(), "config.json"), "r", encoding="utf-8") as f:
            return str(json.load(f).get("version", "")).strip()
    except Exception:
        return ""


def render_changelog_md(releases: List[Dict[str, Any]]) -> str:
    lines = ["# CHANGELOG", "", "Generowane z GitHub Releases (>= v3.0-alpha).", ""]
    for r in reversed(releases):
        lines.append(f"## [{r['tag']}] - {r.get('published_at', '')}")
        lines.append(f"### {r.get('name', '')}")
        body = (r.get("body") or "").strip()
        lines.append(body if body else "_Brak opisu._")
        if r.get("url"):
            lines.append(f"\n[{r['tag']}]({r['url']})")
        lines.append("")
    text = "\n".join(lines)
    try:
        with open(os.path.join(_app_root(), "CHANGELOG.md"), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    return text
