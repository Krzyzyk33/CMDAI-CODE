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

if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

if "dev" in os.path.basename(APP_ROOT).lower() or os.environ.get("CMDAI_DEV") == "1":
    try:
        import cmdai_opencode.patch
        cmdai_opencode.patch.install(APP_ROOT)
    except Exception:
        pass

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

def handle_mcp(mcp_args: list) -> int:
    """Command-line interface for managing Model Context Protocol (MCP) servers."""
    from cmdai.mcp.client import MCPClient, sanitize_name

    client = MCPClient()
    cfg_path = client.config_file(APP_ROOT)
    servers = client.load_config(APP_ROOT)

    if not mcp_args or mcp_args[0] in ("-h", "--help", "help"):
        print("\n" + "=" * 65)
        print("  ⌬  CMDAI CODE — Model Context Protocol (MCP)")
        print("=" * 65 + "\n")
        print(f"  Plik konfiguracyjny / Config: {cfg_path}\n")
        print("Użycie / Usage:")
        print("  cmdai mcp list                  Lista skonfigurowanych serwerów MCP")
        print("  cmdai mcp add <nazwa> --url <url>   Dodanie serwera HTTP (np. xph-docs)")
        print("  cmdai mcp add <nazwa> <komenda> [arg...]  Dodanie serwera stdio")
        print("  cmdai mcp remove <nazwa>        Usunięcie serwera")
        print("  cmdai mcp enable <nazwa>        Włączenie serwera")
        print("  cmdai mcp disable <nazwa>       Wyłączenie serwera")
        print("  cmdai mcp test <nazwa>          Test połączenia i lista narzędzi")
        print()
        return 0

    sub = mcp_args[0].lower()

    if sub in ("list", "ls", "status"):
        print("\n" + "=" * 65)
        print("  ⌬  CMDAI CODE — Serwery MCP / MCP Servers")
        print("=" * 65)
        print(f"  Konfiguracja / Config: {cfg_path}\n")
        if not servers:
            print("  [i] Brak skonfigurowanych serwerów MCP.")
            print("      Dodaj serwer np.: cmdai mcp add xph-docs --url http://127.0.0.1:8765/mcp\n")
            return 0

        print(f"  {'Nazwa / Name':<18} {'Typ':<8} {'Status/URL/Komenda':<32} {'Autostart':<10}")
        print("  " + "-" * 70)
        for name, s in sorted(servers.items()):
            stype = s.get("type") or ("http" if s.get("url") else "stdio")
            details = s.get("url") or (s.get("command", "") + " " + " ".join(s.get("args", []))).strip()
            if len(details) > 30:
                details = details[:29] + "…"
            enabled = s.get("enabled", True)
            auto = "tak" if s.get("autostart", True) else "nie"
            status_str = f"[{'aktywny' if enabled else 'wyłączony'}] {details}"
            print(f"  {name:<18} {stype:<8} {status_str:<32} {auto:<10}")
        print()
        return 0

    if sub == "add":
        if len(mcp_args) < 2:
            print("[!] Użycie: cmdai mcp add <nazwa> [--url <url> | <komenda> [args]]")
            return 1
        name = sanitize_name(mcp_args[1])
        rest = mcp_args[2:]
        if "--url" in rest:
            u_idx = rest.index("--url")
            if u_idx + 1 >= len(rest):
                print("[!] Brak adresu URL po fladze --url.")
                return 1
            url = rest[u_idx + 1]
            entry = {"type": "http", "url": url, "autostart": True, "enabled": True}
        elif any(a.startswith("http://") or a.startswith("https://") for a in rest):
            url = [a for a in rest if a.startswith("http://") or a.startswith("https://")][0]
            entry = {"type": "http", "url": url, "autostart": True, "enabled": True}
        else:
            if not rest:
                print("[!] Podaj URL (--url http://...) lub komendę (np. npx, python).")
                return 1
            cmd = rest[0]
            c_args = rest[1:]
            entry = {
                "type": "stdio",
                "command": cmd,
                "args": c_args,
                "autostart": True,
                "enabled": True,
            }

        servers[name] = entry
        if client.save_config(servers, APP_ROOT):
            print(f"\n[✔] Pomyślnie dodano serwer MCP '{name}' do konfiguracji: {cfg_path}\n")
            return 0
        else:
            print(f"[!] Błąd zapisu konfiguracji: {client.load_error}")
            return 1

    if sub in ("remove", "rm", "delete"):
        if len(mcp_args) < 2:
            print("[!] Użycie: cmdai mcp remove <nazwa>")
            return 1
        name = sanitize_name(mcp_args[1])
        if name not in servers:
            print(f"[!] Nie znaleziono serwera '{name}'. Dostępne: {', '.join(servers.keys()) or 'brak'}")
            return 1
        del servers[name]
        if client.save_config(servers, APP_ROOT):
            print(f"\n[✔] Serwer MCP '{name}' został usunięty z konfiguracji.\n")
            return 0
        else:
            print(f"[!] Błąd zapisu: {client.load_error}")
            return 1

    if sub in ("enable", "disable"):
        if len(mcp_args) < 2:
            print(f"[!] Użycie: cmdai mcp {sub} <nazwa>")
            return 1
        name = sanitize_name(mcp_args[1])
        if name not in servers:
            print(f"[!] Nie znaleziono serwera '{name}'.")
            return 1
        servers[name]["enabled"] = (sub == "enable")
        if client.save_config(servers, APP_ROOT):
            state_pl = "włączony" if sub == "enable" else "wyłączony"
            print(f"\n[✔] Serwer MCP '{name}' został {state_pl}.\n")
            return 0
        else:
            print(f"[!] Błąd zapisu: {client.load_error}")
            return 1

    if sub in ("test", "check"):
        if len(mcp_args) < 2:
            print("[!] Użycie: cmdai mcp test <nazwa>")
            return 1
        name = sanitize_name(mcp_args[1])
        if name not in servers:
            print(f"[!] Nie znaleziono serwera '{name}'. Dostępne: {', '.join(servers.keys()) or 'brak'}")
            return 1
        print(f"\n[*] Łączenie z serwerem MCP '{name}'...")
        client.configure(APP_ROOT)
        server = client.servers.get(name)
        if not server:
            print("[!] Nie można utworzyć instancji serwera.")
            return 1
        ok = server.connect()
        if ok:
            print(f"[✔] Połączono pomyślnie z '{name}'!")
            print(f"    Informacje: {server.server_info.get('name', name)} v{server.server_info.get('version', '?')}")
            print(f"    Liczba narzędzi: {len(server.tools)}")
            for t in server.tools:
                print(f"      - {t.get('name')}: {t.get('description', '')[:60]}")
            print()
            server.stop()
            return 0
        else:
            print(f"[!] Błąd połączenia z '{name}': {server.error}\n")
            return 1

    print(f"[!] Nieznane polecenie mcp: '{sub}'. Wpisz: cmdai mcp help")
    return 1


def main():
    setup_crash_logger()
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    raw_args = [a.lower() for a in sys.argv[1:]]
    args_joined = " ".join(raw_args)

    if len(raw_args) >= 1 and raw_args[0] == "mcp":
        sys.exit(handle_mcp(sys.argv[2:]))
    if len(raw_args) >= 2 and raw_args[0] == "code" and raw_args[1] == "mcp":
        sys.exit(handle_mcp(sys.argv[3:]))

    if "code update" in args_joined or (len(raw_args) >= 1 and raw_args[0] == "update"):
        sys.exit(handle_update())

    if "code addlocal" in args_joined or "addlocal" in args_joined or "add-model" in args_joined:
        sys.exit(handle_add_local_model())

    parser = argparse.ArgumentParser(
        prog="cmdai",
        description="CMDAI CODE - Next-gen Terminal Code Agent",
        add_help=False,
    )
    parser.add_argument("command", nargs="?", default="", help="Command (code, update, addlocal, editor)")
    parser.add_argument("path", nargs="?", default="", help="Optional project directory or file")
    parser.add_argument("--workdir", default="", help="Target project working directory")
    parser.add_argument("--provider", default="", help="LLM Provider ID")
    parser.add_argument("--model", default="", help="Model ID")
    parser.add_argument("--install-launcher", action="store_true", help="Install global Windows PATH launcher")
    parser.add_argument("-h", "--help", action="store_true", help="Show help message")
    args, unknown = parser.parse_known_args()

    if args.install_launcher:
        install_global_launcher(silent=False)
        return

    try:
        install_global_launcher(silent=True)
    except Exception:
        pass

    if args.help or args.command in ("help", "-h", "--help"):
        print_usage = True
    else:
        print_usage = False

    if args.command in ("update", "code-update") or "code update" in args_joined:
        sys.exit(handle_update())

    if args.command in ("addlocal", "add-model", "code-addlocal") or "code addlocal" in args_joined:
        sys.exit(handle_add_local_model())

    if args.command == "editor" or (len(sys.argv) > 1 and sys.argv[1].lower() == "editor"):
        try:
            from editor.ui.app import CMDAIEditorApp
            editor_cls = CMDAIEditorApp
        except Exception:
            from cmdai.editor.editor_app import CMDAICodeEditor
            editor_cls = CMDAICodeEditor
        target_file = args.path or "."
        for a in (sys.argv[2:] if sys.argv[1].lower() == "editor" else sys.argv[1:]):
            if not a.startswith("-") and a.lower() != "editor":
                target_file = a
                break
        app = editor_cls(initial_path=os.path.abspath(target_file))
        app.run()
        return

    # Check if a directory or file was passed as first argument instead of 'code'
    if args.command and args.command not in ("code", "app") and not print_usage:
        if os.path.exists(args.command) and not args.path:
            args.path = args.command
            args.command = "code"

    if print_usage or (args.command and args.command not in ("code", "app", "")):
        print("""
  \x1b[36m██████╗███╗   ███╗██████╗  █████╗ ██╗   \x1b[97m██████╗ ██████╗ ██████╗ ███████╗\x1b[0m
 \x1b[36m██╔════╝████╗ ████║██╔══██╗██╔══██╗██║  \x1b[97m██╔════╝██╔═══██╗██╔══██╗██╔════╝\x1b[0m
 \x1b[36m██║     ██╔████╔██║██║  ██║███████║██║  \x1b[97m██║     ██║   ██║██║  ██║█████╗  \x1b[0m
 \x1b[36m██║     ██║╚██╔╝██║██║  ██║██╔══██║██║  \x1b[97m██║     ██║   ██║██║  ██║██╔══╝  \x1b[0m
 \x1b[36m╚██████╗██║ ╚═╝ ██║██████╔╝██║  ██║██║  \x1b[97m╚██████╗╚██████╔╝██████╔╝███████╗\x1b[0m
  \x1b[36m╚═════╝╚═╝     ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝   \x1b[97m╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝\x1b[0m

\x1b[1mUżycie / Usage:\x1b[0m
  cmdai code [katalog]      Uruchomienie asystenta terminalowego CMDAI CODE
  cmdai mcp [komenda]       Zarządzanie serwerami MCP (list, add, remove, test)
  cmdai update              Aktualizacja z repozytorium GitHub
  cmdai addlocal            Dodanie lokalnego modelu GGUF
  cmdai editor [plik]       Wbudowany edytor kodu
  cmdai --install-launcher  Instalacja globalnego launchera Windows

\x1b[1mOpcje / Options:\x1b[0m
  --workdir <ścieżka>       Katalog roboczy projektu
  --provider <nazwa>        ID dostawcy LLM
  --model <nazwa>           Nazwa lub ID modelu
  -h, --help                Pomoc
""")
        return

    settings = get_settings()
    if args.provider:
        settings.config["default_provider"] = args.provider
    if args.model:
        settings.config["default_model"] = args.model

    workdir = os.path.abspath(args.path or args.workdir or os.getcwd())
    try:
        os.chdir(workdir)
    except Exception:
        pass
    try:
        sys.stdout.write("\x1b[2J\x1b[3J\x1b[H\x1b]11;#000000\x07")
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

