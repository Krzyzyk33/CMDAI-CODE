import ctypes
import os
import sys

from .build_font import build

FONT_FAMILY = "CMDAI Icons"
REG_SUBKEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
REG_VALUE = f"{FONT_FAMILY} (TrueType)"
WM_FONTCHANGE = 0x001D
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002

def user_font_dir() -> str:
    return os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "Windows", "Fonts")

def _broadcast_fontchange() -> None:
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_FONTCHANGE, 0, 0,
        SMTO_ABORTIFHUNG, 2000, ctypes.byref(result),
    )

def _unregister() -> None:
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_SUBKEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, REG_VALUE)
    except OSError:
        pass

def _prune_old(target: str, verbose: bool = True) -> None:

    directory = os.path.dirname(target)
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not name.lower().startswith("cmdai-icons"):
            continue
        path = os.path.join(directory, name)
        if os.path.abspath(path) == os.path.abspath(target):
            continue
        try:
            ctypes.windll.gdi32.RemoveFontResourceW(path)
        except Exception:
            pass
        try:
            os.remove(path)
            if verbose:
                print(f"  removed old version: {name}")
        except OSError as err:
            if verbose:
                print(f"  cannot remove {name} (code {err.winerror}) - "
                      f"delete it by hand after restarting the computer")

def install(verbose: bool = True) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    ttf = os.path.join(here, "font", "cmdai-icons.ttf")

    if not os.path.isfile(ttf) or os.path.getsize(ttf) < 1000:
        if verbose:
            print("Font missing - building...")
        ttf, _ = build()

    with open(ttf, "rb") as fh:
        data = fh.read()

    _unregister()
    _broadcast_fontchange()

    target_dir = user_font_dir()
    os.makedirs(target_dir, exist_ok=True)

    target = ""
    for attempt in range(1, 50):
        candidate = os.path.join(target_dir, f"cmdai-icons-v{attempt}.ttf")
        try:

            same = False
            if os.path.isfile(candidate):
                with open(candidate, "rb") as fh:
                    same = fh.read() == data
            if same:
                target = candidate
                break
            with open(candidate, "wb") as fh:
                fh.write(data)
            target = candidate
            break
        except PermissionError:
            continue
    if not target:
        raise RuntimeError("Cannot write the font to the user font directory")

    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, REG_SUBKEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, REG_VALUE, 0, winreg.REG_SZ, target)

    _prune_old(target, verbose)
    _broadcast_fontchange()

    if verbose:
        print(f"Installed: {target}")
        print(f"Registry: HKCU\\{REG_SUBKEY}\\{REG_VALUE}")
        print("Close the terminal and open it again.")
    return target

if __name__ == "__main__":
    if os.name != "nt":
        print("This script works on Windows only.")
        sys.exit(1)
    install()
