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


def handle_update():
    """Updates CMDAI CODE from GitHub repository without modifying user's personal config."""
    import subprocess

    def _run(args):
        return subprocess.run(args, cwd=APP_ROOT, text=True, capture_output=True)

    print("\n" + "=" * 65)
    print("  CMDAI CODE - GitHub Repository Update")
    print("=" * 65 + "\n")

    try:
        subprocess.check_output(["git", "--version"], stderr=subprocess.DEVNULL)
    except Exception:
        print("[!] Error: Git is not installed or not found in system PATH.")
        print(f"[!] You can re-clone manually: git clone {REPO_URL}")
        return 1

    # Auto-fix: Git "dubious ownership" on Windows (e.g. E:/CMDAI-CODE on a
    # filesystem that does not record ownership). Add APP_ROOT to global
    # safe.directory list if missing.
    try:
        res = subprocess.run(
            ["git", "config", "--global", "--get-all", "safe.directory"],
            text=True, capture_output=True,
        )
        existing = (res.stdout or "").splitlines() if res.returncode == 0 else []
        norm_root = APP_ROOT.replace("\\", "/")
        norm_existing = [p.strip().replace("\\", "/") for p in existing]
        if norm_root not in norm_existing and APP_ROOT not in [p.strip() for p in existing]:
            _add = subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", APP_ROOT],
                text=True, capture_output=True,
            )
            if _add.returncode != 0:
                # Retry with forward-slash form (Git on Windows prefers it).
                subprocess.run(
                    ["git", "config", "--global", "--add", "safe.directory", norm_root],
                    text=True, capture_output=True,
                )
    except Exception:
        pass

    # Auto-fix: missing upstream tracking (bare `git pull` fails with
    # "There is no tracking information for the current branch").
    try:
        upstream = _run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if upstream.returncode != 0:
            branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
            cur = (branch.stdout or "").strip() if branch.returncode == 0 else ""
            candidates = []
            if cur and cur != "HEAD":
                candidates.append(f"origin/{cur}")
            # Common fallbacks if current branch has no same-name remote.
            for fallback in ("origin/main", "origin/master"):
                if fallback not in candidates:
                    candidates.append(fallback)
            for cand in candidates:
                verify = _run(["git", "rev-parse", "--verify", f"refs/remotes/{cand}"])
                if verify.returncode == 0:
                    _run(["git", "branch", "--set-upstream-to", cand])
                    break
    except Exception:
        pass

    def _display_version() -> str:
        try:
            sys.path.insert(0, os.path.join(APP_ROOT, "src"))
            from cmdai.core.releases import get_display_version as _gdv
            ver = (_gdv() or "").strip()
            if ver and not ver.lower().startswith("v"):
                ver = f"v{ver}"
            return ver
        except Exception:
            pass
        try:
            tag = _run(["git", "describe", "--tags", "--abbrev=0"])
            ver = (tag.stdout or "").strip() if tag.returncode == 0 else ""
            if ver and not ver.lower().startswith("v"):
                ver = f"v{ver}"
            return ver
        except Exception:
            return ""

    ver_before = _display_version()

    print("[*] Pulling latest changes from repository...")
    try:
        res = _run(["git", "pull", "--rebase", "--autostash"])
        pull_text = f"{res.stdout or ''}\n{res.stderr or ''}".strip()
        # Show only meaningful git lines; hide transport noise.
        for line in pull_text.splitlines():
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            if low.startswith("fetching ") or low.startswith("from http"):
                continue
            print(f"    {s}")
        if res.returncode != 0:
            combined = f"{res.stdout or ''}\n{res.stderr or ''}"
            if "dubious ownership" in combined:
                print(f"[!] Hint: run: git config --global --add safe.directory {APP_ROOT}")
            elif "no tracking information" in combined:
                print("[!] Hint: no upstream set. Run: git branch --set-upstream-to=origin/main (or origin/master)")
            elif "Your local changes" in combined or "would be overwritten" in combined:
                print("[!] Hint: you have local changes. Run: git stash push -m update-backup, then retry update.")
            print("[!] Update failed.")
            return res.returncode
    except Exception as e:
        print(f"[!] Error while pulling update: {e}")
        return 1

    req_path = os.path.join(APP_ROOT, "requirements.txt")
    if os.path.exists(req_path):
        print("[*] Synchronizing Python packages...")
        try:
            pip = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", req_path,
                 "--quiet", "--disable-pip-version-check"],
                cwd=APP_ROOT, text=True, capture_output=True,
            )
            pip_text = f"{pip.stdout or ''}\n{pip.stderr or ''}"
            # Filter pip noise: broken "~" dist warnings and self-version notice.
            # Real errors (ERROR/Failed) are still shown.
            interesting = []
            pip_notice = ""
            for line in pip_text.splitlines():
                s = line.strip()
                if not s:
                    continue
                if "Ignoring invalid distribution" in s:
                    continue
                if "new release of pip is available" in s:
                    pip_notice = s
                    continue
                if s.startswith("[notice]"):
                    continue
                if "to update, run:" in s and "pip install" in s:
                    continue
                interesting.append(s)
            if pip.returncode != 0:
                for s in interesting[-15:]:
                    print(f"    {s}")
                print("[!] Dependency sync failed (see lines above).")
            else:
                print("[OK] Dependencies are up to date.")
                if pip_notice:
                    print(f"     ({pip_notice})")
        except Exception:
            pass

    # Refresh release/version cache so hero shows the new tag immediately.
    ver_after = ver_before
    try:
        sys.path.insert(0, os.path.join(APP_ROOT, "src"))
        from cmdai.core.releases import get_releases as _gr, get_display_version as _gdv2
        _gr(refresh=True)
        ver_after = (_gdv2() or "").strip()
        if ver_after and not ver_after.lower().startswith("v"):
            ver_after = f"v{ver_after}"
    except Exception:
        ver_after = _display_version()

    if ver_before and ver_after and ver_before != ver_after:
        print(f"\n[OK] Updated: {ver_before} -> {ver_after}\n")
    elif ver_after:
        print(f"\n[OK] Already up to date ({ver_after}).\n")
    else:
        print("\n[OK] CMDAI CODE has been successfully updated!\n")
    return 0


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

    if "code update" in args_joined or (len(raw_args) >= 1 and raw_args[0] == "update"):
        sys.exit(handle_update())

    if "code addlocal" in args_joined or "addlocal" in args_joined or "add-model" in args_joined:
        sys.exit(handle_add_local_model())

    parser = argparse.ArgumentParser(description="CMDAI CODE - Next-gen Terminal Code Agent")
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

    if args.command == "editor" or (len(sys.argv) > 1 and sys.argv[1].lower() == "editor"):
        from cmdai.editor.editor_app import CMDAICodeEditor
        target_file = "."
        for a in (sys.argv[2:] if sys.argv[1].lower() == "editor" else sys.argv[1:]):
            if not a.startswith("-") and a.lower() != "editor":
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
