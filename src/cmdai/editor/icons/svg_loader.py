import os
import xml.etree.ElementTree as ET
from typing import Dict, Optional, Any

SVG_DIR = os.path.join(os.path.dirname(__file__), "svg")

class SVGLoader:

    @staticmethod
    def list_available_svgs() -> list[str]:
        if not os.path.isdir(SVG_DIR):
            return []
        return [
            f[:-4] for f in os.listdir(SVG_DIR)
            if f.endswith(".svg") and os.path.isfile(os.path.join(SVG_DIR, f))
        ]

    @staticmethod
    def get_svg_path(name: str) -> Optional[str]:
        target = os.path.join(SVG_DIR, f"{name}.svg")
        if os.path.isfile(target):
            return target
        return None

    @staticmethod
    def read_raw_svg(name: str) -> Optional[str]:
        path = SVGLoader.get_svg_path(name)
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    @staticmethod
    def parse_svg_meta(name: str) -> Optional[Dict[str, Any]]:
        raw = SVGLoader.read_raw_svg(name)
        if not raw:
            return None
        try:
            root = ET.fromstring(raw)
            return {
                "name": name,
                "width": root.attrib.get("width", "24"),
                "height": root.attrib.get("height", "24"),
                "viewBox": root.attrib.get("viewBox", "0 0 24 24"),
                "class": root.attrib.get("class", ""),
                "paths": [
                    elem.attrib.get("d", "")
                    for elem in root.iter()
                    if elem.tag.endswith("path") and "d" in elem.attrib
                ],
            }
        except Exception:
            return None
