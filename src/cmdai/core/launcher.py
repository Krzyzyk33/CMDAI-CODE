import os
import sys
import ctypes
from typing import Dict

try:
    import winreg
except Exception:
    winreg = None

LAUNCHER_DIR_NAME = "CMDAI"
FIXED_ROOT_NAME = "CMDAI-CODE"
HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


def get_fixed_root() -> str:
    return os.path.join(os.path.expanduser("~"), FIXED_ROOT_NAME)


def build_launcher_scripts(project_root: str) -> Dict[str, str]:
    """Build Windows launcher script contents.

    The install-time project_root is only a fallback: at runtime the
    launcher resolves CMDAI_CODE_ROOT env first, then %USERPROFILE%\\CMDAI-CODE.
    """
    main_cmd = (
        "@echo off\n"
        "rem CMDAI CODE global launcher (generated). Regen via: cmdai --install-launcher\n"
        'if not defined CMDAI_CODE_ROOT set "CMDAI_CODE_ROOT=%USERPROFILE%\\CMDAI-CODE"\n'
        f'if not exist "%CMDAI_CODE_ROOT%\\cmdai.py" if exist "{project_root}\\cmdai.py" set "CMDAI_CODE_ROOT={project_root}"\n'
        'if not exist "%CMDAI_CODE_ROOT%\\cmdai.py" (\n'
        "    echo [ERROR] CMDAI CODE checkout not found. Set CMDAI_CODE_ROOT or reinstall.\n"
        "    exit /b 1\n"
        ")\n"
        'set "USER_WORKDIR=%CD%"\n'
        'if exist "%CMDAI_CODE_ROOT%\\.venv\\Scripts\\python.exe" (\n'
        '    set "PYTHON_BIN=%CMDAI_CODE_ROOT%\\.venv\\Scripts\\python.exe"\n'
        ") else (\n"
        '    set "PYTHON_BIN=python"\n'
        ")\n"
        '"%PYTHON_BIN%" "%CMDAI_CODE_ROOT%\\cmdai.py" --workdir "%USER_WORKDIR%" %*\n'
        "exit /b %ERRORLEVEL%\n"
    )
    shim = '@echo off\ncall "%~dp0cmdai.cmd" %*\n'
    editor_shim = '@echo off\ncall "%~dp0cmdai.cmd" editor %*\n'
    # NOTE: no separate CMDAI.cmd - Windows filenames are case-insensitive,
    # so CMDAI.cmd and cmdai.cmd are the same file. cmdai.cmd IS the launcher.
    return {
        "cmdai.cmd": main_cmd,
        "cmdai-code.cmd": shim,
        "editor.cmd": editor_shim,
    }


def _notify_windows_environment_change():
    if os.name != "nt":
        return
    try:
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )
    except Exception:
        pass


def _ensure_windows_user_path_contains(path_entry: str) -> bool:
    if os.name != "nt" or not winreg or not path_entry:
        return False

    try:
        env_key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Environment",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        )
    except Exception:
        return False

    try:
        current_path, value_type = winreg.QueryValueEx(env_key, "Path")
    except FileNotFoundError:
        current_path, value_type = "", winreg.REG_EXPAND_SZ
    except Exception:
        winreg.CloseKey(env_key)
        return False

    try:
        parts = [p.strip() for p in str(current_path).split(";") if p.strip()]
        lowered = {p.lower() for p in parts}
        if path_entry.lower() in lowered:
            _notify_windows_environment_change()
            return True

        new_parts = parts + [path_entry]
        new_path = ";".join(new_parts)
        winreg.SetValueEx(env_key, "Path", 0, value_type, new_path)

        process_parts = [
            p.strip() for p in os.environ.get("PATH", "").split(";") if p.strip()
        ]
        process_lower = {p.lower() for p in process_parts}
        if path_entry.lower() not in process_lower:
            process_parts.append(path_entry)
            os.environ["PATH"] = ";".join(process_parts)

        _notify_windows_environment_change()
        return True
    except Exception:
        return False
    finally:
        winreg.CloseKey(env_key)


def install_global_launcher(silent: bool = False) -> bool:
    if os.name != "nt":
        if not silent:
            print("Launcher install is currently available on Windows only.")
        return False

    try:
        here = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        launcher_dir = os.path.join(os.path.expanduser("~"), LAUNCHER_DIR_NAME)
        os.makedirs(launcher_dir, exist_ok=True)

        scripts = build_launcher_scripts(project_root)
        installed = []
        for name, content in scripts.items():
            script_path = os.path.join(launcher_dir, name)
            with open(script_path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(content)
            installed.append(script_path)

        path_ok = _ensure_windows_user_path_contains(launcher_dir)

        if not silent:
            for p in installed:
                print(f"Launcher installed: {p}")
            if path_ok:
                print("Global command registered! Open a NEW terminal and run: cmdai code")
            else:
                print(f"Add this folder to PATH manually: {launcher_dir}")
        return True
    except Exception as e:
        if not silent:
            print(f"Launcher install failed: {e}")
        return False
