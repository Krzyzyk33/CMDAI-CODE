import os
import json
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from .stroker import stroke_svg_file

UPEM = 1000
ADVANCE_WIDTH = 1000

PUA_BASE = 0xF0000

ICON_NAMES = [
    "terminal-2",
    "git-branch",
    "history",
    "layout-sidebar",
    "folder",
    "folder-open",
    "file-code",
    "device-floppy",
    "x",
    "code",
    "file",
    "file-plus",
    "device-floppy-save-as",
    "exit",
    "numbers",
    "palette",
    "search",
    "settings",
    "check",
]

ICON_MAPPINGS = [(name, PUA_BASE + i) for i, name in enumerate(ICON_NAMES)]

def build():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    svg_dir = os.path.join(script_dir, "svg")
    out_dir = os.path.join(script_dir, "font")
    os.makedirs(out_dir, exist_ok=True)

    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder([".notdef"] + [name for name, _ in ICON_MAPPINGS])

    cmap = {}
    glyph_map = {}
    glyphs = {}

    pen = TTGlyphPen(None)
    pen.moveTo((100, 100))
    pen.lineTo((100, 900))
    pen.lineTo((900, 900))
    pen.lineTo((900, 100))
    pen.closePath()
    glyphs[".notdef"] = pen.glyph()

    optical_scale = 0.75
    margin = (1.0 - optical_scale) / 2.0

    for name, codepoint in ICON_MAPPINGS:
        svg_file = os.path.join(svg_dir, f"{name}.svg")
        pen = TTGlyphPen(None)

        if os.path.exists(svg_file):
            contours = stroke_svg_file(svg_file, stroke_width=2.0)
            for c in contours:
                if len(c) < 3:
                    continue
                first = True
                for x, y in c:
                    norm_x = (x / 24.0) * optical_scale + margin
                    norm_y = (1.0 - (y / 24.0)) * optical_scale + margin
                    gx = int(norm_x * UPEM)
                    gy = int(norm_y * UPEM)
                    if first:
                        pen.moveTo((gx, gy))
                        first = False
                    else:
                        pen.lineTo((gx, gy))
                pen.closePath()
        else:
            pen.moveTo((200, 200))
            pen.lineTo((800, 800))
            pen.closePath()

        glyphs[name] = pen.glyph()
        cmap[codepoint] = name
        glyph_map[name] = {
            "codepoint": f"0x{codepoint:04X}",
            "char": chr(codepoint)
        }

    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)

    metrics = {".notdef": (ADVANCE_WIDTH, 100)}
    for name, _ in ICON_MAPPINGS:
        metrics[name] = (ADVANCE_WIDTH, 100)
    fb.setupHorizontalMetrics(metrics)

    fb.setupHorizontalHeader(ascent=800, descent=-200, lineGap=0)

    name_strings = {
        "familyName": "CMDAI Icons",
        "styleName": "Regular",
        "uniqueFontIdentifier": "CMDAI Icons 1.0",
        "fullName": "CMDAI Icons Regular",
        "psName": "CMDAIIcons-Regular",
        "version": "Version 1.0",
    }
    fb.setupNameTable(name_strings)
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    fb.setupPost()

    ttf_path = os.path.join(out_dir, "cmdai-icons.ttf")
    json_path = os.path.join(out_dir, "glyph_map.json")

    fb.save(ttf_path)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(glyph_map, f, indent=2)

    return ttf_path, json_path

if __name__ == "__main__":
    tpath, jpath = build()
    print(f"Font built successfully: {tpath}")
