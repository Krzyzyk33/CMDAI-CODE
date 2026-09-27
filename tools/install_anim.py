"""Installer/update step animator.

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

Reusable from Python: animate(...) runs one step and returns the
status string ("OK", "FAIL", "TIMEOUT"); print_footer(...) closes
the table with a custom message.
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


def print_footer(text):
    """Close the rounded table with a custom message."""
    print("│")
    print("╰ " + str(text))
    sys.stdout.flush()


def animate(label, wait_file="", fixed=0.0, min_time=0.0, timeout=900.0,
            header="", gap=0, footer=""):
    """Run one animated step; return status string (OK/FAIL/TIMEOUT)."""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        tty = sys.stdout.isatty()
    except Exception:
        tty = False

    if header:
        print("╭ " + header)
        print("│")
        sys.stdout.flush()

    start = time.time()
    status = ""

    if fixed > 0:
        if not tty:
            print("✻  " + label + " ...")
            sys.stdout.flush()
        while time.time() - start < fixed:
            if tty:
                sys.stdout.write(CR + live_line(label, time.time() - start))
                sys.stdout.flush()
            time.sleep(TICK)
        status = "OK"
    else:
        if not tty:
            print("✻  " + label + " ...")
            sys.stdout.flush()
        while True:
            elapsed = time.time() - start
            status = read_status(wait_file)
            if status in ("OK", "FAIL") and elapsed >= min_time:
                break
            if timeout > 0 and elapsed >= timeout:
                status = "TIMEOUT"
                break
            if tty:
                sys.stdout.write(CR + live_line(label, elapsed))
                sys.stdout.flush()
            time.sleep(TICK)

    if tty:
        sys.stdout.write(CR + "⌬  " + label + chr(10))
        sys.stdout.flush()
    else:
        print("⌬  " + label)
        sys.stdout.flush()

    for _ in range(max(0, int(gap))):
        print("│")
    if footer:
        print_footer(footer)
    else:
        sys.stdout.flush()

    return status


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

    status = animate(
        label=args.label,
        wait_file=args.wait_file,
        fixed=args.fixed,
        min_time=args.min_time,
        timeout=args.timeout,
        header=args.header,
        gap=args.gap,
        footer=args.footer,
    )
    return 0 if status == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
