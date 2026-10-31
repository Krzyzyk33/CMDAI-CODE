from __future__ import annotations

import atexit
import os
import shutil
import subprocess
from typing import Set

_PIDS: Set[int] = set()

def register(pid: int) -> None:

    if pid:
        _PIDS.add(pid)

def unregister(pid: int) -> None:
    _PIDS.discard(pid)

def tracked() -> Set[int]:
    return set(_PIDS)

def kill(pid: int) -> None:

    unregister(pid)
    if os.name != "nt":
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        return
    for args in (["taskkill", "/PID", str(pid), "/T", "/F"],
                 ["taskkill", "/PID", str(pid), "/F"]):
        try:
            subprocess.run(args, capture_output=True, timeout=5)
        except Exception:
            continue

@atexit.register
def kill_all() -> None:

    for pid in list(_PIDS):
        kill(pid)
    _PIDS.clear()

def which(exe: str):

    found = shutil.which(exe)
    if found:
        return found
    if os.name == "nt" and not exe.lower().endswith(".exe"):
        return shutil.which(exe + ".exe")
    return None
