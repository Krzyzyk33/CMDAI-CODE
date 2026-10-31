import argparse
import glob
import os
import re
import subprocess
import sys
import unicodedata

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

TEXT = "CMDAI EDITOR"

FONT = "simple"

ROWS_PER_TEXT_LINE = 4
MAX_WIDTH = 96
BAD_EAW = ("A", "W", "F")

BLOCK_FONT = "block"
STACK_WORDS = ("CMDAI", "EDITOR")

def npx_path() -> str:
    hits = glob.glob(os.path.join(os.environ.get("APPDATA", ""), "npm", "npx.cmd"))
    if hits:
        return hits[0]
    import shutil
    found = shutil.which("npx.cmd") or shutil.which("npx")
    if not found:
        raise SystemExit("npx not found - zainstaluj Node.js")
    return found

def strip_ansi(out: str) -> str:
    out = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", out)
    out = re.sub(r"\x1b[\]P][^\x07\x1b]*(\x07|\x1b\\)", "", out)
    return "".join(
        ch for ch in out
        if ch in "\n\r\t "
        or (ord(ch) >= 32 and unicodedata.category(ch)[0] != "C")
    )

def generate(text: str, font: str) -> list:

    res = subprocess.run(
        [npx_path(), "--yes", "oh-my-logo", text, "--filled",
         "--block-font", font, "--no-color"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    if res.returncode != 0:
        raise SystemExit(f"oh-my-logo zwrocil {res.returncode}: {res.stderr[:200]}")
    rows = [l.rstrip() for l in strip_ansi(res.stdout).splitlines() if l.strip()]
    return [l for l in rows if l.strip()]

def first_text_line(rows: list) -> list:

    return rows[:ROWS_PER_TEXT_LINE]

def validate(rows: list, allow_ambiguous: bool = False) -> None:

    if not rows:
        raise SystemExit("generator zwrocil pusto")
    width = max(len(r) for r in rows)
    if width > MAX_WIDTH:
        raise SystemExit(f"logo za szerokie: {width} > {MAX_WIDTH}")
    bad = {}
    for ch in set("".join(rows)):
        if ch in " \n":
            continue
        from rich.cells import cell_len
        w = cell_len(ch)
        eaw = unicodedata.east_asian_width(ch)
        if w != 1 or eaw in BAD_EAW:
            bad[ch] = (w, eaw, unicodedata.name(ch, "?"))
    if bad and not allow_ambiguous:
        lines = "\n".join(f"    {c!r} cell_len={w} east_asian_width={e} {n}"
                          for c, (w, e, n) in bad.items())
        raise SystemExit(
            "logo zawiera znaki, ktore terminal narysuje SZEROKO "
            "(East Asian Ambiguous) - rozpadnie sie:\n" + lines
            + "\n  Jesli mimo to chcesz te czcionke, uzyj --allow-ambiguous."
        )
    widths = [len(r) for r in rows]
    if max(widths) - min(widths) > 2:
        raise SystemExit(f"wiersze roznia sie szerokoscia: {widths}")

def stacked_logo(font: str = BLOCK_FONT) -> list:

    parts = [generate(word, font) for word in STACK_WORDS]
    width = max(max(len(r) for r in part) for part in parts)
    out: list = []
    for part in parts:
        out.extend(r.ljust(width) for r in part)
    return out

def report(rows: list, font: str) -> None:
    width = max(len(r) for r in rows)
    ambiguous = {c for c in "".join(rows)
                 if c != " " and unicodedata.east_asian_width(c) == "A"}
    print("font=%s  wierszy=%d  kolumn=%d" % (font, len(rows), width),
          file=sys.stderr)
    if ambiguous:
        print("UWAGA: %d znakow East Asian Ambiguous - w terminalu CJK "
              "logo sie rozjedzie: %s"
              % (len(ambiguous), "".join(sorted(ambiguous))), file=sys.stderr)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="tylko zwaliduj, nie wypisuj")
    ap.add_argument("--font", default=FONT)
    ap.add_argument("--stacked", action="store_true",
                    help="CMDAI nad EDITOR zamiast jednego wiersza")
    ap.add_argument("--allow-ambiguous", action="store_true",
                    help="nie odrzucaj czcionek blokowych")
    args = ap.parse_args()

    if args.stacked:
        rows = stacked_logo(args.font)
    else:
        rows = first_text_line(generate(TEXT, args.font))
    validate(rows, allow_ambiguous=args.allow_ambiguous or args.stacked)
    report(rows, args.font)

    if args.check:
        print("OK", file=sys.stderr)
        return
    for r in rows:
        print(r)

if __name__ == "__main__":
    main()
