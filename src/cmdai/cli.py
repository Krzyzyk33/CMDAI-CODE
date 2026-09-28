import argparse
import os
import sys
import traceback

REPO_URL = "https://github.com/Krzyzyk33/CMDAI-CODE.git"
FIXED_ROOT_NAME = "CMDAI-CODE"


def _resolve_app_root() -> str:
    """Locate the CMDAI CODE checkout.

    Priority: CMDAI_CODE_ROOT env -> repo layout next to this file ->
    fixed user location (~/CMDAI-CODE) -> fallback to file-based root.
    """
    override = os.environ.get("CMDAI_CODE_ROOT")
    if override and os.path.isdir(override):
        return os.path.abspath(override)
    here_based = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for marker in ("cmdai.py", "config.example.json", "requirements.txt"):
        if os.path.exists(os.path.join(here_based, marker)):
            return here_based
    fixed = os.path.join(os.path.expanduser("~"), FIXED_ROOT_NAME)
    if os.path.isdir(fixed):
        return fixed
    return here_based


APP_ROOT = _resolve_app_root()
SRC_PATH = os.path.join(APP_ROOT, "src")
if os.path.isdir(SRC_PATH) and SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from cmdai.core.launcher import install_global_launcher
from cmdai.core.settings import get_settings
from cmdai.tui.app import CMDAICodeTUI


def setup_crash_logger():
    os.makedirs(os.path.join(APP_ROOT, "logs"), exist_ok=True)
    crash_log = os.path.join(APP_ROOT, "logs", "cmdai_crash.log")

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        with open(crash_log, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 60 + "\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)

        print("\n[!] CMDAI CODE encountered an unexpected error.")
        print(f"[!] Crash details saved to: {crash_log}")

        if os.environ.get("RUN_AI_KEEP_OPEN") == "1":
            input("\nPress Enter to exit...")

    sys.excepthook = handle_exception


_ANIM_MODULE = None


def _load_animator():
    """Load tools/install_anim.py as a module (shared step animation)."""
    global _ANIM_MODULE
    if _ANIM_MODULE is not None:
        return _ANIM_MODULE
    import importlib.util
    path = os.path.join(APP_ROOT, "tools", "install_anim.py")
    spec = importlib.util.spec_from_file_location("install_anim", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _ANIM_MODULE = mod
    return mod


def _animated_step(label, work_fn, min_time=1.0, timeout=300.0, header="", gap=0):
    """Run work_fn in a background thread while animating the step.

    work_fn returns (status, detail) with status OK/FAIL.
    Returns (status, detail). Falls back to plain output when the
    animator module is unavailable.
    """
    import tempfile
    import threading
    try:
        anim = _load_animator()
    except Exception:
        anim = None
    fd, flag = tempfile.mkstemp(prefix="cmdai-update-", suffix=".done")
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(flag)
    except OSError:
        pass
    result = {}

    def _work():
        try:
            result["status"], result["detail"] = work_fn()
        except Exception as e:
            result["status"], result["detail"] = ("FAIL", str(e))
        try:
            with open(flag, "w", encoding="utf-8") as f:
                f.write(result["status"])
        except OSError:
            pass

    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    try:
        if anim is not None:
            status = anim.animate(
                label=label, wait_file=flag,
                min_time=min_time, timeout=timeout,
                header=header, gap=gap,
            )
        else:
            print("... " + label + " ...")
            thread.join(timeout if timeout > 0 else None)
            status = result.get("status", "TIMEOUT")
    except KeyboardInterrupt:
        print("\nCancelled.")
        return ("FAIL", "cancelled by user")
    thread.join(timeout=10)
    try:
        os.remove(flag)
    except OSError:
        pass
    status = result.get("status", status)
    return (status, result.get("detail", ""))


class _CliReporter:
    """Draws the update steps in a terminal, the way install.bat does.

    Holds the only animation the update path has ever used. The TUI gets its
    own reporter over the same `core.updater.run_update`, which is what keeps
    `cmdai code update` and `/update` behaving identically.
    """

    def step(self, label, work_fn, min_time=1.0, timeout=300.0, header="", gap=0):
        return _animated_step(label, work_fn, min_time=min_time, timeout=timeout,
                              header=header, gap=gap)

    def message(self, text: str) -> None:
        print(text)

    def finish(self, text: str) -> None:
        try:
            _load_animator().print_footer(text)
        except Exception:
            print(text)


def handle_update():
    """Updates CMDAI CODE from GitHub repository without modifying user's personal config."""
    from cmdai.core.releases import write_pending_notice
    from cmdai.core.updater import run_update

    result = run_update(APP_ROOT, reporter=_CliReporter(),
                        on_updated=write_pending_notice)
    return 0 if result.ok else 1


def handle_add_local_model():
    """Opens Windows File Explorer dialog to pick a local model (*.gguf) and imports it into models/."""
    import shutil
    import json

    print("\n" + "=" * 65)
    print("  CMDAI CODE - Add Local Model (GGUF)")
    print("=" * 65 + "\n")
    print("[*] Opening Windows File Explorer to select a model file...")

    selected_file = ""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected_file = filedialog.askopenfilename(
            title="Select Local Model File for CMDAI CODE",
            filetypes=[
                ("GGUF Model Files (*.gguf)", "*.gguf"),
                ("Binary Model Files (*.bin)", "*.bin"),
                ("All Files (*.*)", "*.*"),
            ],
        )
        root.destroy()
    except Exception:
        try:
            import subprocess
            ps_cmd = (
                "[System.Reflection.Assembly]::LoadWithPartialName('System.windows.forms') | Out-Null; "
                "$f = New-Object System.Windows.Forms.OpenFileDialog; "
                "$f.Filter = 'GGUF Model Files (*.gguf)|*.gguf|All Files (*.*)|*.*'; "
                "$f.Title = 'Select Local Model File (GGUF)'; "
                "$res = $f.ShowDialog(); "
                "if ($res -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.FileName }"
            )
            out = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps_cmd], text=True)
            selected_file = out.strip()
        except Exception as pe:
            print(f"[!] Failed to open File Explorer dialog: {pe}")
            return 1

    if not selected_file or not os.path.exists(selected_file):
        print("[!] Model file selection cancelled.")
        return 0

    model_filename = os.path.basename(selected_file)
    models_dir = os.path.join(APP_ROOT, "models")
    os.makedirs(models_dir, exist_ok=True)
    dest_path = os.path.join(models_dir, model_filename)

    file_size_mb = os.path.getsize(selected_file) / (1024 * 1024)
    file_size_gb = file_size_mb / 1024

    print(f"\n[+] Selected model: {model_filename}")
    if file_size_gb >= 1.0:
        print(f"    Size: {file_size_gb:.2f} GB")
    else:
        print(f"    Size: {file_size_mb:.1f} MB")
    print(f"    Target path: models/{model_filename}")

    if os.path.abspath(selected_file) == os.path.abspath(dest_path):
        print("\n[i] File is already located in the models/ directory!")
    else:
        print("[*] Copying model file to models/ directory (please wait)...")
        try:
            shutil.copy2(selected_file, dest_path)
            print("[OK] Copying completed successfully!")
        except Exception as ce:
            print(f"[!] Copy error: {ce}")
            return 1

    cfg_file = os.path.join(APP_ROOT, "config.json")
    if not os.path.exists(cfg_file) and os.path.exists(os.path.join(APP_ROOT, "config.example.json")):
        shutil.copy2(os.path.join(APP_ROOT, "config.example.json"), cfg_file)

    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["default_provider"] = "local_gguf"
            cfg["default_model"] = model_filename
            cfg.setdefault("settings", {})["default_provider"] = "local_gguf"
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            print(f"[OK] Updated config.json - model '{model_filename}' set as default local model!")
        except Exception as e:
            print(f"[!] Warning updating config: {e}")

    print("\n[OK] Success: Model is ready for use in CMDAI CODE.")
    print("    Launch the assistant by running: cmdai.bat\n")
    return 0


CLI_HELP = """
  [bold]Usage[/]
    cmdai code [target]            launch the app (default)
    cmdai code help | -h | --help  show this help
    cmdai editor [folder]          CMDAI EDITOR: pick/create a project,
                                   or open <folder> right away
    cmdai code update              update from GitHub
    cmdai addlocal                 add a local model (.gguf)

  [bold]Options[/]
    --workdir <path>     working directory (default: current)
    --provider <id>      model provider, e.g. local_gguf, openrouter
    --model <id>         model, e.g. gemma-4-E4B-it-Q4_0.gguf
    --install-launcher   install the cmdai shortcut in PATH

  [bold]In-app commands[/]
    /help                every slash command with descriptions
    /mode                auto / plan / code
    /model               pick a model
    /git                 git window in a new terminal
    /diff                review working changes
    /plan                task checklist (CMDAIPLAN.md)
    /editor              code editor in a new window
    /update              pull the latest code (same as this command)
    /updatesettings      automatic updates on / off / check interval
    /changelog           GitHub releases (>= v3.0-alpha)

  [dim]{repo}[/]
"""


def _cmdaieditor_main() -> str:
    """Sciezka do `editor/main.py` albo pusty napis, gdy go nie ma."""
    # `src/cmdai/cli.py` -> `src/cmdai` -> `src` -> korzen repo.
    # Liczymy od `__file__`, nie od `cwd`: `cmdai` jest instalowany
    # globalnie i bywa wywolywany z kazdego katalogu.
    korzen = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    main_py = os.path.join(korzen, "editor", "main.py")
    return main_py if os.path.isfile(main_py) else ""


def launch_cmdaieditor(args: list) -> int:
    """`cmdai editor [folder]` - uruchamia edytor CMDAI.

    Zwraca kod wyjścia procesu albo 1, gdy nie da się uruchomić.
    """
    main_py = _cmdaieditor_main()
    if not main_py:
        print("CMDAI EDITOR not found: expected editor/main.py next to src/.",
              file=sys.stderr)
        return 1

    # Ten sam interpreter, ktory uruchomił `cmdai`. Dzięki temu
    # edytor dostaje te same pakiety co aplikacja (a jego
    # `requirements-editor.txt` jest podzbiorem `requirements.txt`).
    polecenie = [sys.executable, main_py, *[str(a) for a in args if str(a)]]
    try:
        import subprocess

        # Bez `shell=True` - argumenty zostaja argumentami, a nazwa
        # folderu ze spacja albo cudzysłowem nie robi sie komenda.
        return subprocess.call(polecenie)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"Cannot start CMDAI EDITOR: {exc}", file=sys.stderr)
        return 1


def print_cli_help() -> None:
    from .core.branding import get_hero_logo, get_plain_logo

    logo = get_hero_logo()
    # The ASCII logo already spells out CMDAI CODE, so only the tagline goes
    # under it - centred against the logo width instead of hardcoded spaces.
    plain_lines = get_plain_logo().splitlines()
    width = max((len(l) for l in plain_lines), default=0)
    tagline = "Next-gen Terminal Code Agent"
    pad = " " * max(0, (width - len(tagline)) // 2)
    header = f"\n{pad}[bold white]{tagline}[/]\n"
    body = CLI_HELP.format(repo=REPO_URL)
    try:
        from rich.console import Console

        console = Console(highlight=False)
        for line in logo.splitlines():
            console.print(line)
        console.print(header, markup=True, highlight=False)
        console.print(body, markup=True, highlight=False)
    except Exception:
        print(get_plain_logo())
        print("\n" + tagline.center(width))
        print(body)


def main():
    setup_crash_logger()
    # Windows PL console (cp1250) can't encode box glyphs — force UTF-8 output.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    raw_args = [a.lower() for a in sys.argv[1:]]
    args_joined = " ".join(raw_args)

    # `help` / `-h` / `--help` print the CMDAI CODE help. Handled before
    # argparse so it never falls through to its own "usage: cmdai.py" output.
    # `cmdai editor --help` NIE jest helpem CMDAI CODE - to edytor, i
    # ten gałąź obsługuje siebie sam (`--no-picker` itd.).
    if not (raw_args and raw_args[0] == "editor") and \
            any(a in ("help", "-h", "--help", "-?", "/?") for a in raw_args):
        print_cli_help()
        return

    if "code update" in args_joined or (len(raw_args) >= 1 and raw_args[0] == "update"):
        sys.exit(handle_update())

    if "code addlocal" in args_joined or "addlocal" in args_joined or "add-model" in args_joined:
        sys.exit(handle_add_local_model())

    parser = argparse.ArgumentParser(prog="cmdai code", add_help=False,
                                     description="CMDAI CODE - Next-gen Terminal Code Agent")
    parser.add_argument("command", nargs="?", default="launch", help="Command (code, launch)")
    parser.add_argument("--workdir", default=os.getcwd(), help="Target project working directory")
    parser.add_argument("--provider", default="", help="LLM Provider ID")
    parser.add_argument("--model", default="", help="Model ID")
    parser.add_argument("--install-launcher", action="store_true", help="Install global Windows PATH launcher")
    args, unknown = parser.parse_known_args()

    if args.install_launcher:
        install_global_launcher(silent=False)
        return

    try:
        install_global_launcher(silent=True)
    except Exception:
        pass

    # `cmdai editor` -> EDYTOR CMDAI (osobna aplikacja w `editor/`).
    #
    # To jest DODATKOWA gałąź, nie zamiana. Gałąź poniżej
    # (`if "editor" in raw_args`) obsługuje `cmdai code editor` i
    # celowo zostaje nietknięta - to inna aplikacja, inny kod
    # (`cmdai.editor.editor_app`), inne zachowanie. Użytkownik
    # wyraźnie poprosił, żeby CMDAI CODE było niezależne.
    #
    # Uruchamiamy `editor/main.py` jako PROCES POTOMNY w tym samym
    # terminalu, a nie `import` - edytor ma własny `main()`,
    # własne ustawienia kodowania (SetConsoleOutputCP) i własny
    # `sys.path`. `import` przeplatałby te trzy rzeczy z CLI.
    if raw_args and raw_args[0] == "editor":
        sys.exit(launch_cmdaieditor(sys.argv[2:]))

    if "editor" in raw_args:
        from cmdai.editor.editor_app import CMDAICodeEditor
        raw_list = sys.argv[1:]
        target_file = "."
        i = 0
        while i < len(raw_list):
            a = raw_list[i]
            if a.lower() in ("editor", "code"):
                i += 1
                continue
            if a.startswith("-"):
                i += 1
                if "=" not in a and i < len(raw_list):
                    i += 1
                continue
            target_file = a
            break
        app = CMDAICodeEditor(initial_path=os.path.abspath(target_file))
        app.run()
        return

    settings = get_settings()
    if args.provider:
        settings.config["default_provider"] = args.provider
    if args.model:
        settings.config["default_model"] = args.model

    workdir = os.path.abspath(args.workdir)
    try:
        sys.stdout.write("\x1b]11;#000000\x07")
        sys.stdout.flush()
    except Exception:
        pass

    try:
        app = CMDAICodeTUI(workdir=workdir)
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sys.stdout.write("\x1b]111\x07")
            sys.stdout.flush()
        except Exception:
            pass


if __name__ == "__main__":
    main()
