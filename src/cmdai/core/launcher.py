import os
import sys
import ctypes

try:
    import winreg
except Exception:
    winreg = None

LAUNCHER_DIR_NAME = "CMDAI"
HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


def _notify_windows_environment_change():
    """Notify all top-level windows that the environment variables have changed."""
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
    """Ensure path_entry is registered in HKCU\\Environment\\Path."""
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
    """Installs the global CMDAI launcher in %USERPROFILE%\\CMDAI and adds it to PATH.
    
    Supports:
        cmdai code [dir] -> launches CMDAI CODE in target dir
        cmdai-code [dir] -> alias for cmdai code
        cmdai [args]     -> fallback to CMDAI standard if present
    """
    if os.name != "nt":
        if not silent:
            print("Launcher install is currently available on Windows only.")
        return False

    try:
        here = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        entry_py = os.path.join(project_root, "cmdai.py")
        launcher_dir = os.path.join(os.path.expanduser("~"), LAUNCHER_DIR_NAME)
        os.makedirs(launcher_dir, exist_ok=True)

        cmdai_base_root = "E:\\CMDAI"

        cmd_path = os.path.join(launcher_dir, "CMDAI.cmd")
        cmd_content = (
            "@echo off\n"
            "setlocal enabledelayedexpansion\n\n"
            'if /i "%~1"=="editor" (\n'
            "    shift\n"
            f'    set "CMDAI_CODE_ROOT={project_root}"\n'
            '    if exist "!CMDAI_CODE_ROOT!\\.venv\\Scripts\\python.exe" (\n'
            '        set "PYTHON_BIN=!CMDAI_CODE_ROOT!\\.venv\\Scripts\\python.exe"\n'
            "    ) else (\n"
            '        set "PYTHON_BIN=python"\n'
            "    )\n"
            f'    !PYTHON_BIN! "{entry_py}" editor %*\n'
            "    exit /b !ERRORLEVEL!\n"
            ")\n\n"
            'if /i "%~1"=="code" (\n'
            "    shift\n"
            f'    set "CMDAI_CODE_ROOT={project_root}"\n'
            '    set "USER_WORKDIR=%CD%"\n\n'
            '    if exist "!CMDAI_CODE_ROOT!\\.venv\\Scripts\\python.exe" (\n'
            '        set "PYTHON_BIN=!CMDAI_CODE_ROOT!\\.venv\\Scripts\\python.exe"\n'
            "    ) else (\n"
            '        set "PYTHON_BIN=python"\n'
            "    )\n\n"
            f'    !PYTHON_BIN! "{entry_py}" --workdir "!USER_WORKDIR!" %1 %2 %3 %4 %5 %6 %7 %8 %9\n'
            "    exit /b !ERRORLEVEL!\n"
            ")\n\n"
            f'set "CMDAI_BASE_ROOT={cmdai_base_root}"\n'
            'if exist "!CMDAI_BASE_ROOT!\\cmdai.py" (\n'
            '    pushd "!CMDAI_BASE_ROOT!" >nul 2>&1\n'
            '    py -3 "!CMDAI_BASE_ROOT!\\cmdai.py" %*\n'
            "    set ERR=!ERRORLEVEL!\n"
            "    popd >nul 2>&1\n"
            "    exit /b !ERR!\n"
            ")\n\n"
            f'set "CMDAI_CODE_ROOT={project_root}"\n'
            'set "USER_WORKDIR=%CD%"\n'
            'if exist "!CMDAI_CODE_ROOT!\\.venv\\Scripts\\python.exe" (\n'
            '    set "PYTHON_BIN=!CMDAI_CODE_ROOT!\\.venv\\Scripts\\python.exe"\n'
            ") else (\n"
            '    set "PYTHON_BIN=python"\n'
            ")\n"
            f'!PYTHON_BIN! "{entry_py}" --workdir "!USER_WORKDIR!" %*\n'
            "exit /b !ERRORLEVEL!\n"
        )
        with open(cmd_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(cmd_content)

        editor_alias_path = os.path.join(launcher_dir, "editor.cmd")
        with open(editor_alias_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("@echo off\ncall \"%~dp0CMDAI.cmd\" editor %*\n")

        code_alias_path = os.path.join(launcher_dir, "cmdai-code.cmd")
        code_alias_content = (
            "@echo off\n"
            'call "%~dp0CMDAI.cmd" code %*\n'
        )
        with open(code_alias_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(code_alias_content)

        path_ok = _ensure_windows_user_path_contains(launcher_dir)

        if not silent:
            print(f"Launcher installed: {cmd_path}")
            print(f"Alias installed: {code_alias_path}")
            if path_ok:
                print("Global command registered! Run anywhere: cmdai code")
            else:
                print(f"Add this folder to PATH manually: {launcher_dir}")
        return True
    except Exception as e:
        if not silent:
            print(f"Launcher install failed: {e}")
        return False
