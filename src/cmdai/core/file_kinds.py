"""Single source of truth for file kinds, icons and colors.

Shared by the TUI (file picker, diff lists) and the editor (explorer, tabs)
so a file always looks the same in both places.
"""

from __future__ import annotations

import os
from typing import Dict, Tuple

# Extension -> (label, color)
EXT_KINDS: Dict[str, Tuple[str, str]] = {
    ".py": ("python", "#3572A5"),
    ".pyi": ("python", "#3572A5"),
    ".js": ("javascript", "#f1e05a"),
    ".mjs": ("javascript", "#f1e05a"),
    ".cjs": ("javascript", "#f1e05a"),
    ".ts": ("typescript", "#3178c6"),
    ".tsx": ("typescript", "#3178c6"),
    ".jsx": ("javascript", "#f1e05a"),
    ".json": ("json", "#d29922"),
    ".jsonc": ("json", "#d29922"),
    ".md": ("markdown", "#58a6ff"),
    ".markdown": ("markdown", "#58a6ff"),
    ".txt": ("text", "#8b949e"),
    ".log": ("text", "#8b949e"),
    ".cfg": ("config", "#8b949e"),
    ".ini": ("config", "#8b949e"),
    ".toml": ("config", "#8b949e"),
    ".yaml": ("config", "#8b949e"),
    ".yml": ("config", "#8b949e"),
    ".env": ("config", "#8b949e"),
    ".bat": ("shell", "#8b949e"),
    ".cmd": ("shell", "#8b949e"),
    ".ps1": ("shell", "#8b949e"),
    ".sh": ("shell", "#8b949e"),
    ".bash": ("shell", "#8b949e"),
    ".html": ("html", "#e34c26"),
    ".htm": ("html", "#e34c26"),
    ".css": ("css", "#563d7c"),
    ".scss": ("css", "#563d7c"),
    ".rs": ("rust", "#dea584"),
    ".go": ("go", "#00add8"),
    ".c": ("c", "#599eff"),
    ".h": ("c", "#599eff"),
    ".cpp": ("c++", "#f34b7d"),
    ".hpp": ("c++", "#f34b7d"),
    ".java": ("java", "#b07219"),
    ".cs": ("csharp", "#178600"),
    ".rb": ("ruby", "#701516"),
    ".php": ("php", "#4f5d95"),
    ".lua": ("lua", "#000080"),
    ".sql": ("sql", "#e38c00"),
    ".xml": ("xml", "#e38c00"),
    ".svg": ("svg", "#ff9900"),
    ".ttf": ("font", "#bc8cff"),
    ".otf": ("font", "#bc8cff"),
    ".woff": ("font", "#bc8cff"),
    ".woff2": ("font", "#bc8cff"),
    ".png": ("image", "#a074c4"),
    ".jpg": ("image", "#a074c4"),
    ".jpeg": ("image", "#a074c4"),
    ".gif": ("image", "#a074c4"),
    ".webp": ("image", "#a074c4"),
    ".ico": ("image", "#a074c4"),
    ".zip": ("archive", "#d29922"),
    ".gz": ("archive", "#d29922"),
    ".tar": ("archive", "#d29922"),
    ".7z": ("archive", "#d29922"),
    ".exe": ("binary", "#8b949e"),
    ".dll": ("binary", "#8b949e"),
    ".bin": ("binary", "#8b949e"),
    ".gguf": ("model", "#7ee787"),
    ".pdf": ("pdf", "#f85149"),
}

# Well-known file names without an extension
NAME_KINDS: Dict[str, Tuple[str, str]] = {
    "dockerfile": ("docker", "#58a6ff"),
    "makefile": ("make", "#6e7681"),
    "license": ("license", "#d29922"),
    "readme": ("markdown", "#58a6ff"),
    "changelog": ("markdown", "#58a6ff"),
    ".gitignore": ("config", "#8b949e"),
    ".gitattributes": ("config", "#8b949e"),
    ".editorconfig": ("config", "#8b949e"),
}

DEFAULT_KIND: Tuple[str, str] = ("file", "#8b949e")


def kind_for(path: str) -> Tuple[str, str]:
    """Return (kind, color) for a file path or name."""
    base = os.path.basename(str(path).replace("\\", "/")).lower()
    if base in NAME_KINDS:
        return NAME_KINDS[base]
    _stem, ext = os.path.splitext(base)
    if ext and ext in EXT_KINDS:
        return EXT_KINDS[ext]
    return DEFAULT_KIND


def color_for(path: str) -> str:
    return kind_for(path)[1]


def label_for(path: str) -> str:
    return kind_for(path)[0]
