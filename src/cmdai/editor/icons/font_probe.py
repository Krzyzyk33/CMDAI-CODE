import os
import json
from typing import Dict, Optional

class FontProbe:

    def __init__(self):
        self._cache_available: Optional[bool] = None
        self._glyph_map: Dict[str, str] = {}
        self._load_glyph_map()

    def _load_glyph_map(self) -> None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(script_dir, "font", "glyph_map.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self._glyph_map[k] = v.get("char", "")
            except Exception:
                pass

    @property
    def glyph_map(self) -> Dict[str, str]:
        if not self._glyph_map:
            self._load_glyph_map()
        return self._glyph_map

    def is_font_built(self) -> bool:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        ttf_path = os.path.join(script_dir, "font", "cmdai-icons.ttf")
        if not (os.path.isfile(ttf_path) and os.path.getsize(ttf_path) > 1000):
            return False
        return self._font_is_loadable(ttf_path)

    @staticmethod
    def _font_is_loadable(ttf_path: str) -> bool:

        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            return True

        try:
            font = TTFont(ttf_path, lazy=True)
        except Exception:
            return False

        required = ("head", "hhea", "maxp", "hmtx", "cmap", "name", "OS/2", "post")
        try:
            missing = [tag for tag in required if tag not in font]
        finally:
            try:
                font.close()
            except Exception:
                pass
        return not missing

    def is_font_installed(self) -> bool:
        if self._cache_available is not None:
            return self._cache_available

        if not self.is_font_built():
            self._cache_available = False
            return False

        try:
            import winreg
            paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
            ]
            for hkey, subkey in paths:
                try:
                    with winreg.OpenKey(hkey, subkey) as key:
                        idx = 0
                        while True:
                            name, val, _ = winreg.EnumValue(key, idx)
                            if "CMDAI Icons" in name or "cmdai-icons" in str(val).lower():

                                if os.path.isfile(str(val)):
                                    self._cache_available = True
                                    return True
                            idx += 1
                except (OSError, WindowsError):
                    pass
        except Exception:
            pass

        self._cache_available = False
        return False

font_probe = FontProbe()
