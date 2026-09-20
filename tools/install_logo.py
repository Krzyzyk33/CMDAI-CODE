"""Installer splash screen.

Prints the same block logo as the app hero (mirror of CMDAI_PARTS /
CODE_PARTS in src/cmdai/tui/app.py), centered to the console width.
Stdlib only, so it works before dependencies are installed.
"""

import shutil
import sys

# Mirror of CMDAI_PARTS in src/cmdai/tui/app.py
CMDAI_PARTS = [
    " ██████╗███╗   ███╗██████╗  █████╗ ██╗",
    "██╔════╝████╗ ████║██╔══██╗██╔══██╗██║",
    "██║     ██╔████╔██║██║  ██║███████║██║",
    "██║     ██║╚██╔╝██║██║  ██║██╔══██║██║",
    "╚██████╗██║ ╚═╝ ██║██████╔╝██║  ██║██║",
    " ╚═════╝╚═╝     ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝",
]

# Mirror of CODE_PARTS in src/cmdai/tui/app.py
CODE_PARTS = [
    " ██████╗ ██████╗ ██████╗ ███████╗",
    "██╔════╝██╔═══██╗██╔══██╗██╔════╝",
    "██║     ██║   ██║██║  ██║█████╗  ",
    "██║     ██║   ██║██║  ██║██╔══╝  ",
    "╚██████╗╚██████╔╝██████╔╝███████╗",
    " ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
]

GAP = "   "
SUBTITLE = "Autonomous terminal coding agent"


def main() -> int:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        width = shutil.get_terminal_size().columns
    except Exception:
        width = 80
    if width < 72:
        width = 72
    print()
    for left, right in zip(CMDAI_PARTS, CODE_PARTS):
        print((left + GAP + right).center(width))
    print(SUBTITLE.center(width))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
