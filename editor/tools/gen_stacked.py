import argparse
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from editor.tools.gen_logo import BLOCK_FONT, stacked_logo

TARGET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ui", "logo_variants.py",
)

def to_literal(rows: list) -> str:

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    parts = ['    "%s\\n"' % esc(r) for r in rows[:-1]]
    parts.append('    "%s"' % esc(rows[-1]))
    return "LOGO = (\n" + "\n".join(parts) + "\n)\n"

def write_target(rows: list) -> None:
    text = io.open(TARGET, encoding="utf-8").read()
    start = text.index("LOGO = (")
    end = text.index("\n)\n", start) + 3
    io.open(TARGET, "w", encoding="utf-8", newline="\n").write(
        text[:start] + to_literal(rows) + text[end:])

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", default=BLOCK_FONT)
    ap.add_argument("--write", action="store_true",
                    help="wpisz do ui/logo_variants.py")
    args = ap.parse_args()

    rows = stacked_logo(args.font)
    width = max(len(r) for r in rows)
    print("wierszy=%d kolumn=%d font=%s" % (len(rows), width, args.font),
          file=sys.stderr)
    for r in rows:
        print(r)
    if args.write:
        write_target(rows)
        print("zapisano %s" % TARGET, file=sys.stderr)

if __name__ == "__main__":
    main()
