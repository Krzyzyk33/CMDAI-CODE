"""Installer step animator.

Mirrors the app loading animation (LoadingBlock in src/cmdai/tui/app.py):
live glyph alternates ["⌬", "✻"] every 0.35s. Each step renders as a
bare point line, e.g.:
  ✻  Installing dependencies
The finished line is just the animation stopping on its glyph,
nothing else, e.g.:
  ⌬  Installing dependencies
Optional rounded table chrome around points: --header prints
"╭ {header}" + "│", --gap N prints N empty bordered lines after the
step, --footer prints "│" + "╰ {footer}". Stdlib only.
"""

import argparse
import sys
import time

GLYPHS = ["⌬", "✻"]
TICK = 0.08
FLIP = 0.35


def parse_args(argv):
    p = argparse.ArgumentParser(description="CMDAI CODE install step animator")
    p.add_argument("--label", required=True, help="Step text, e.g. Installing dependencies")
    p.add_argument("--wait-file", default="", help="Poll file until it contains OK or FAIL")
    p.add_argument("--fixed", type=float, default=0.0, help="Animate a fixed number of seconds")
    p.add_argument("--min-time", type=float, default=0.0, help="Minimum animation time for wait mode")
    p.add_argument("--timeout", type=float, default=900.0, help="Give up after N seconds in wait mode")
    p.add_argument("--header", default="", help="Print rounded table opening first")
    p.add_argument("--gap", type=int, default=0, help="Print N empty bordered lines after the step")
    p.add_argument("--footer", default="", help="Print rounded table closing after the step")
    return p.parse_args(argv)


def read_status(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip().upper()
    except OSError:
        return ""


def live_line(label, elapsed):
    glyph = GLYPHS[int(elapsed / FLIP) % 2]
    return glyph + "  " + label


CR = chr(13)


def main(argv=None) -> int:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if bool(args.wait_file) == bool(args.fixed > 0):
        print("Error: pass exactly one of --wait-file / --fixed", file=sys.stderr)
        return 1

    try:
        tty = sys.stdout.isatty()
    except Exception:
        tty = False

    if args.header:
        print("╭ " + args.header)
        print("│")
        sys.stdout.flush()

    start = time.time()
    status = ""

    if args.fixed > 0:
        if not tty:
            print("✻  " + args.label + " ...")
            sys.stdout.flush()
        while time.time() - start < args.fixed:
            if tty:
                sys.stdout.write(CR + live_line(args.label, time.time() - start))
                sys.stdout.flush()
            time.sleep(TICK)
        status = "OK"
    else:
        if not tty:
            print("✻  " + args.label + " ...")
            sys.stdout.flush()
        while True:
            elapsed = time.time() - start
            status = read_status(args.wait_file)
            if status in ("OK", "FAIL") and elapsed >= args.min_time:
                break
            if args.timeout > 0 and elapsed >= args.timeout:
                status = "TIMEOUT"
                break
            if tty:
                sys.stdout.write(CR + live_line(args.label, elapsed))
                sys.stdout.flush()
            time.sleep(TICK)

    if tty:
        sys.stdout.write(CR + "⌬  " + args.label + chr(10))
        sys.stdout.flush()
    else:
        print("⌬  " + args.label)
        sys.stdout.flush()

    for _ in range(max(0, args.gap)):
        print("│")
    if args.footer:
        print("│")
        print("╰ " + args.footer)
    sys.stdout.flush()

    return 0 if status == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
