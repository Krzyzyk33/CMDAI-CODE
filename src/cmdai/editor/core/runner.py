from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .proc_registry import kill, register, unregister, which

_PY: object = None

MAPPING = {
    ".py": (None, (), "python"),
    ".pyw": (None, (), "python"),
    ".js": ("node", (), "node"),
    ".mjs": ("node", (), "node"),
    ".cjs": ("node", (), "node"),
    ".jsx": ("node", (), "node"),
    ".bat": ("cmd", ("/c",), "cmd /c"),
    ".cmd": ("cmd", ("/c",), "cmd /c"),
    ".ps1": ("powershell", ("-NoLogo", "-NoProfile", "-File"), "powershell -File"),
    ".sh": ("bash", (), "bash"),
    ".bash": ("bash", (), "bash"),
}

UNSUPPORTED = {
    ".json", ".yaml", ".yml", ".toml", ".md", ".markdown", ".html",
    ".htm", ".css", ".xml", ".csv", ".txt", ".log", ".lock", ".cfg",
    ".ini", ".env", ".gitignore", ".ts", ".tsx", ".c", ".h", ".cpp",
    ".hpp", ".cs", ".java", ".kt", ".rb", ".php", ".swift", ".lua",
    ".vue", ".svelte", ".sql", ".rs", ".go", ".png", ".jpg", ".svg",
}

@dataclass
class RunSpec:

    argv: List[str] = field(default_factory=list)
    cwd: str = ""
    display: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.argv)

def python_executable() -> Optional[str]:

    return sys.executable or None

def run_command_for(path: str, args: Optional[List[str]] = None,
                    cwd: Optional[str] = None) -> RunSpec:

    args = list(args or [])
    if not path:
        return RunSpec(reason="no file selected")
    ext = os.path.splitext(path)[1].lower()
    if ext in UNSUPPORTED:
        return RunSpec(reason=f"{ext or 'file'} is not directly runnable")
    entry = MAPPING.get(ext)
    if entry is None:
        return RunSpec(reason=f"no runner for {ext or 'this file'}")

    exe_key, pre, label = entry
    if exe_key is None:
        exe = python_executable()
    else:
        exe = which(exe_key)
    if not exe:
        return RunSpec(
            reason=f"{exe_key} not found on PATH"
                   if exe_key else "no Python interpreter")

    run_dir = cwd or os.path.dirname(os.path.abspath(path))
    argv = [exe, *pre, os.path.abspath(path), *args]
    return RunSpec(argv=argv, cwd=run_dir, display=" ".join(argv), reason="")

class RunProcess:

    def __init__(self, on_line: Optional[Callable[[str], None]] = None,
                 on_done: Optional[Callable[[int], None]] = None) -> None:
        self._on_line = on_line
        self._on_done = on_done
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._pump: Optional[asyncio.Task] = None
        self._seq: int = 0
        self.argv: List[str] = []

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self, spec: RunSpec) -> bool:

        if not spec.ok:
            return False
        if self.is_running:
            return False
        exe = os.path.basename(spec.argv[0]).lower()
        if exe.startswith("python") and "-u" not in spec.argv:

            spec.argv.insert(1, "-u")
        self.argv = list(spec.argv)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *spec.argv,
                cwd=spec.cwd or None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except Exception:
            self._proc = None
            return False
        if self._proc.pid:
            register(self._proc.pid)
        self._pump = asyncio.create_task(self._pump_output())
        return True

    async def _pump_output(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if self._on_line is not None:
                    self._on_line(line)
        except Exception:
            pass
        finally:

            try:
                rc = await proc.wait()
            except Exception:
                rc = proc.returncode if proc.returncode is not None else -1
            if proc.pid:
                unregister(proc.pid)
            if self._on_done is not None:
                self._on_done(rc if rc is not None else -1)
            self._proc = None
            self._pump = None

    def stop(self) -> bool:

        proc, self._proc = self._proc, None
        if proc is None:
            return False
        if proc.pid:
            kill(proc.pid)
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
        return True
