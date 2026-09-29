import os
from typing import Dict, Optional, Tuple
from .svg_loader import SVGLoader

_NERD_FONT_CACHE: Optional[bool] = None

_NERD_PROBE_CP = 0xE0B0

def nerd_font_installed() -> bool:

    global _NERD_FONT_CACHE
    if _NERD_FONT_CACHE is not None:
        return _NERD_FONT_CACHE
    try:
        import glob
        from fontTools.ttLib import TTFont
    except Exception:

        _NERD_FONT_CACHE = False
        return False
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    paths = glob.glob(os.path.join(fonts_dir, "*.ttf")) +\
        glob.glob(os.path.join(fonts_dir, "*.otf"))
    found = False
    for path in paths:
        try:
            font = TTFont(path, fontNumber=0, lazy=True)
            names = font["name"].names
            family = " ".join(
                str(n.toUnicode()) for n in names
                if n.nameID in (1, 4, 6)
            ).lower()
            if "nerd font" not in family and "nerdfont" not in family:
                font.close()
                continue
            cmap = font.getBestCmap() or {}
            if _NERD_PROBE_CP in cmap:
                found = True
            font.close()
            if found:
                break
        except Exception:
            continue
    _NERD_FONT_CACHE = found
    return found

ICON_STYLES: Dict[str, Dict[str, Tuple[str, str]]] = {
    "modern": {

        "play": ("▸", "#3fb950"),
        "stop": ("◼", "#f85149"),
        "terminal-2": (">_", "#58a6ff"),
        "device-floppy": ("💾", "#3fb950"),
        "device-floppy-save-as": ("💾", "#3fb950"),
        "git-branch": ("⎇", "#bc8cff"),
        "history": ("⏱", "#d29922"),
        "folder": ("📁", "#e3b341"),
        "folder-open": ("📂", "#f0883e"),
        "file": ("📄", "#8b949e"),
        "file-code": ("📄", "#58a6ff"),
        "file-plus": ("+", "#3fb950"),
        "layout-sidebar": ("◫", "#8b949e"),
        "numbers": ("#", "#58a6ff"),
        "palette": ("🎨", "#bc8cff"),
        "x": ("✕", "#f85149"),
        "exit": ("✕", "#f85149"),
        "check": ("✔", "#3fb950"),
        "search": ("🔍", "#8b949e"),
        "settings": ("⚙", "#8b949e"),
        "new-project": ("⊞", "#3fb950"),
        "code": ("</>", "#58a6ff"),
    },
    "nerd": {
        "terminal-2": ("\uea85", "#58a6ff"),
        "device-floppy": ("\ueb4b", "#3fb950"),
        "device-floppy-save-as": ("\ueb4b", "#3fb950"),
        "git-branch": ("\uea68", "#bc8cff"),
        "history": ("\uea82", "#d29922"),
        "folder": ("\uea83", "#e3b341"),
        "folder-open": ("\uea84", "#f0883e"),
        "file": ("\uea7b", "#8b949e"),
        "file-code": ("\uea7c", "#58a6ff"),
        "file-plus": ("\uea7f", "#3fb950"),
        "layout-sidebar": ("\ueb12", "#8b949e"),
        "numbers": ("\ueb07", "#58a6ff"),
        "palette": ("\ueb01", "#bc8cff"),
        "x": ("\uea76", "#f85149"),
        "exit": ("\ueab8", "#f85149"),

        "new-project": ("\uea31", "#3fb950"),
        "check": ("\ueab2", "#3fb950"),
        "search": ("\uea6d", "#8b949e"),
        "settings": ("\ueb51", "#8b949e"),
        "code": ("\uea7c", "#58a6ff"),
    },
    "ascii": {
        "terminal-2": (">_", "#58a6ff"),
        "device-floppy": ("[S]", "#3fb950"),
        "device-floppy-save-as": ("[S+]", "#3fb950"),
        "git-branch": ("[G]", "#bc8cff"),
        "history": ("[H]", "#d29922"),
        "folder": ("[+]", "#e3b341"),
        "folder-open": ("[-]", "#f0883e"),
        "file": (" - ", "#8b949e"),
        "file-code": (" * ", "#58a6ff"),
        "file-plus": (" + ", "#3fb950"),
        "layout-sidebar": ("[|]", "#8b949e"),
        "numbers": ("#", "#58a6ff"),
        "palette": ("[*]", "#bc8cff"),
        "x": ("x", "#f85149"),
        "exit": ("x", "#f85149"),

        "new-project": ("[+]", "#3fb950"),
        "check": ("v", "#3fb950"),
        "search": ("?", "#8b949e"),
        "settings": ("*", "#8b949e"),
        "code": ("<>", "#58a6ff"),
    }
}

EXT_MAP: Dict[str, Tuple[str, str, str]] = {
    ".py": ("🐍", "🐍", "#3572A5"),
    ".pyi": ("🐍", "🐍", "#58a6ff"),
    ".js": ("⚡", "⚡", "#f1e05a"),
    ".jsx": ("⚛", "⚛", "#61dafb"),
    ".ts": ("⚡", "⚡", "#3178c6"),
    ".tsx": ("⚛", "⚛", "#3178c6"),
    ".json": ("{}", "{}", "#cbcb41"),
    ".md": ("📝", "📝", "#58a6ff"),
    ".markdown": ("📝", "📝", "#58a6ff"),
    ".html": ("🌐", "🌐", "#e34c26"),
    ".htm": ("🌐", "🌐", "#e34c26"),
    ".css": ("🎨", "🎨", "#563d7c"),
    ".scss": ("🎨", "🎨", "#c6538c"),
    ".rs": ("🦀", "🦀", "#dea584"),
    ".go": ("🔷", "🔷", "#00add8"),
    ".yaml": ("⚙", "⚙", "#cb171e"),
    ".yml": ("⚙", "⚙", "#cb171e"),
    ".toml": ("⚙", "⚙", "#9c4221"),
    ".sql": ("🗄", "🗄", "#e38c00"),
    ".sh": (">_", ">_", "#89e051"),
    ".bash": (">_", ">_", "#89e051"),
    ".ps1": (">_", ">_", "#58a6ff"),
    ".bat": (">_", ">_", "#c1f12e"),
    ".cmd": (">_", ">_", "#c1f12e"),
    ".txt": ("📄", "📄", "#8b949e"),
    ".log": ("📄", "📄", "#8b949e"),
    ".lock": ("🔒", "🔒", "#ffa657"),
    ".gitignore": ("⎇", "⎇", "#f54d27"),
}

from .font_probe import font_probe

class IconRegistry:

    MODES = ["modern", "nerd"]

    CODE_EXTS = {
        ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".c", ".h",
        ".cpp", ".hpp", ".cs", ".java", ".kt", ".kts", ".rb", ".php", ".swift",
        ".m", ".mm", ".lua", ".vue", ".svelte", ".sh", ".bash", ".zsh", ".ps1",
        ".bat", ".cmd", ".html", ".htm", ".css", ".scss", ".sass", ".less",
        ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".sql",
        ".graphql", ".dart", ".ex", ".exs", ".erl", ".hs", ".clj", ".pl",
    }

    def __init__(self, mode: Optional[str] = None):
        if mode and mode in self.MODES:
            self.mode = mode
        elif self._default_mode() in self.MODES:
            self.mode = self._default_mode()
        else:
            self.mode = "modern"

    @staticmethod
    def _default_mode() -> str:

        return "modern"

    @classmethod
    def available_modes(cls) -> list:
        modes = list(cls.MODES)

        if not nerd_font_installed() and "nerd" in modes:
            modes.remove("nerd")

        if not (font_probe.is_font_built()
                and font_probe.is_font_installed()
                and font_probe.glyph_map):
            if "svg" in modes:
                modes.remove("svg")
        return modes

    def cycle_mode(self) -> str:
        modes = self.available_modes()
        idx = (modes.index(self.mode) + 1) % len(modes)
        self.mode = modes[idx]
        return self.mode

    def get_raw(self, name: str) -> str:
        if self.mode == "svg":
            glyph_map = font_probe.glyph_map
            glyph = glyph_map.get(name)
            if glyph:
                return glyph

            for fallback in ("file", "file-code", "x"):
                if fallback in glyph_map:
                    return glyph_map[fallback]
            return "\uf101"

        style_dict = ICON_STYLES.get(self.mode) or ICON_STYLES["modern"]
        if name in style_dict:
            return style_dict[name][0]
        return ICON_STYLES["modern"].get(name, ("?", "#ffffff"))[0]

    def get_color(self, name: str) -> str:
        style_dict = ICON_STYLES.get(self.mode) or ICON_STYLES["modern"]
        if name in style_dict:
            return style_dict[name][1]
        return "#8b949e"

    def get_styled(self, name: str, with_space: bool = True) -> str:
        char = self.get_raw(name)
        color = self.get_color(name)
        suffix = " " if with_space else ""
        return f"[{color}]{char}[/]{suffix}"

    def get_file_icon(self, filename: str, is_dir: bool = False, is_open: bool = False) -> str:
        if is_dir:
            key = "folder-open" if is_open else "folder"
            return self.get_styled(key, with_space=True)

        base = os.path.basename(filename).lower()
        if base.startswith(".git"):
            return self.get_styled("git-branch", with_space=True)

        ext = os.path.splitext(base)[1].lower()

        if self.mode != "modern":

            color = EXT_MAP.get(ext, (None, None, "#8b949e"))[2]
            if ext in self.CODE_EXTS:
                name = "file-code"
            else:
                name = "file"
            return f"[{color}]{self.get_raw(name)}[/] "

        if ext in EXT_MAP:
            char = EXT_MAP[ext][0]
            color = EXT_MAP[ext][2]
            return f"[{color}]{char}[/] "

        return self.get_styled("file", with_space=True)

    def has_svg(self, name: str) -> bool:
        return SVGLoader.get_svg_path(name) is not None

icons = IconRegistry()
