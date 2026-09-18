import argparse
import os
import sys
import traceback

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_PATH = os.path.join(APP_ROOT, "src")
if SRC_PATH not in sys.path:
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
    print("\n" + "=" * 65)
    print("  ⌬  CMDAI CODE — GitHub Repository Update")
    print("=" * 65 + "\n")

    try:
        subprocess.check_output(["git", "--version"], stderr=subprocess.DEVNULL)
    except Exception:
        print("[!] Error: Git is not installed or not found in system PATH.")
        return 1

    print("[*] Pulling latest changes from repository...")
    try:
        res = subprocess.run(["git", "pull"], cwd=APP_ROOT, text=True, capture_output=True)
        if res.stdout:
            print(res.stdout.strip())
        if res.stderr:
            print(res.stderr.strip())
        if res.returncode != 0:
            print("[!] Update failed.")
            return res.returncode
    except Exception as e:
        print(f"[!] Error while pulling update: {e}")
        return 1

    req_path = os.path.join(APP_ROOT, "requirements.txt")
    if os.path.exists(req_path):
        print("[*] Synchronizing Python packages...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path, "--quiet"], cwd=APP_ROOT)
            print("[✔] Dependencies are up to date.")
        except Exception:
            pass

    print("\n[✔] Success: CMDAI CODE has been successfully updated!\n")
    return 0


def handle_add_local_model():
    """Opens Windows File Explorer dialog to pick a local model (*.gguf) and imports it into models/."""
    import shutil
    import json

    print("\n" + "=" * 65)
    print("  ⌬  CMDAI CODE — Add Local Model (GGUF)")
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
            print("[✔] Copying completed successfully!")
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
            print(f"[✔] Updated config.json — model '{model_filename}' set as default local model!")
        except Exception as e:
            print(f"[!] Warning updating config: {e}")

    print("\n[✔] Success: Model is ready for use in CMDAI CODE.")
    print("    Launch the assistant by running: cmdai.bat\n")
    return 0


def main():
    setup_crash_logger()

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

