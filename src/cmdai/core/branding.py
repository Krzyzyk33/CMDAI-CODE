"""CMDAI CODE ASCII logo.

Kept in core/ (not in tui/) so the CLI can print the logo for
`cmdai code help` without importing Textual.
"""

from __future__ import annotations

import datetime
from typing import List, Optional

CMDAI_PARTS: List[str] = [
    " ██████╗███╗   ███╗██████╗  █████╗ ██╗",
    "██╔════╝████╗ ████║██╔══██╗██╔══██╗██║",
    "██║     ██╔████╔██║██║  ██║███████║██║",
    "██║     ██║╚██╔╝██║██║  ██║██╔══██║██║",
    "╚██████╗██║ ╚═╝ ██║██████╔╝██║  ██║██║",
    " ╚═════╝╚═╝     ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝",
]

CODE_PARTS: List[str] = [
    " ██████╗ ██████╗ ██████╗ ███████╗",
    "██╔════╝██╔═══██╗██╔══██╗██╔════╝",
    "██║     ██║   ██║██║  ██║█████╗  ",
    "██║     ██║   ██║██║  ██║██╔══╝  ",
    "╚██████╗╚██████╔╝██████╔╝███████╗",
    " ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
]

WHITE = "#ffffff"
GRAY = "#888888"
GAP = "   "


def get_hero_logo(d: Optional[datetime.date] = None) -> str:
    """Logo as rich markup; the white/gray mix rotates with the day of year."""
    if d is None:
        d = datetime.date.today()
    cycle = d.timetuple().tm_yday % 4
    if cycle == 0:
        cmdai_color, code_color = WHITE, WHITE
    elif cycle == 1:
        cmdai_color, code_color = GRAY, WHITE
    elif cycle == 2:
        cmdai_color, code_color = WHITE, GRAY
    else:
        cmdai_color, code_color = GRAY, GRAY

    lines = []
    for c_line, cd_line in zip(CMDAI_PARTS, CODE_PARTS):
        lines.append(f"[{cmdai_color}]{c_line}[/]{GAP}[{code_color}]{cd_line}[/]")
    return "\n".join(lines)


def get_plain_logo(d: Optional[datetime.date] = None) -> str:
    """Same logo without rich markup (for plain consoles and pipes)."""
    logo = get_hero_logo(d)
    out = []
    for line in logo.splitlines():
        # strip [color] and [/] markers, keep the glyphs
        while "[" in line and "]" in line:
            start = line.index("[")
            end = line.index("]", start)
            if start + 1 < len(line) and line[start + 1] == "/":
                line = line[:start] + line[end + 1:]
            else:
                line = line[:start] + line[end + 1:]
        out.append(line)
    return "\n".join(out)


LOGO_HERO = get_hero_logo()
