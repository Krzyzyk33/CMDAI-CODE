import os
import re
import shutil
import atexit
import unicodedata
import asyncio
from typing import List, Optional, Set
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from .clickable import MouseActivatable
from textual.widgets import Static, Input, RichLog
from textual.message import Message
from textual.binding import Binding
from rich.style import Style
from rich.text import Text

_SHELL_PIDS: Set[int] = set()

def _kill_pid(pid: int) -> None:
    _SHELL_PIDS.discard(pid)
    if os.name != "nt":
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        return

    for args in (["taskkill", "/PID", str(pid), "/T", "/F"],
                 ["taskkill", "/PID", str(pid), "/F"]):
        try:
            import subprocess
            subprocess.run(args, capture_output=True, timeout=5)
        except Exception:
            continue

@atexit.register
def _kill_all_shells() -> None:

    for pid in list(_SHELL_PIDS):
        _kill_pid(pid)
    _SHELL_PIDS.clear()

if os.name == "nt":

    try:
        import ctypes
        ctypes.WinDLL("kernel32", use_last_error=True).SetConsoleOutputCP(65001)
    except Exception:
        pass

def _which(exe: str) -> Optional[str]:
    return shutil.which(exe)

_SGR_FG = {
    30: "black", 31: "red", 32: "green", 33: "yellow", 34: "blue",
    35: "magenta", 36: "cyan", 37: "white",
    90: "bright_black", 91: "bright_red", 92: "bright_green",
    93: "bright_yellow", 94: "bright_blue", 95: "bright_magenta",
    96: "bright_cyan", 97: "bright_white",
}
_SGR_BG = {k + 10: v for k, v in _SGR_FG.items()}
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")

def _ansi_to_rich(line: str) -> Text:

    text = Text()
    fg: Optional[str] = None
    bg: Optional[str] = None
    bold = False
    pos = 0
    for m in _SGR_RE.finditer(line):
        chunk = line[pos:m.start()]
        if chunk:
            text.append(chunk, style=Style(color=fg, bgcolor=bg, bold=bold or None))
        codes = [int(c) for c in m.group(1).split(";") if c.isdigit()] or [0]
        for code in codes:
            if code == 0:
                fg = bg = None
                bold = False
            elif code == 1:
                bold = True
            elif code == 22:
                bold = False
            elif code in _SGR_FG:
                fg = _SGR_FG[code]
            elif code in _SGR_BG:
                bg = _SGR_BG[code]
            elif code == 39:
                fg = None
            elif code == 49:
                bg = None
        pos = m.end()
    rest = line[pos:]
    if rest:
        text.append(rest, style=Style(color=fg, bgcolor=bg, bold=bold or None))
    return text or Text("")

def _printable(line: str) -> str:

    return "".join(
        ch if ch in "\t" or unicodedata.category(ch)[0] != "C" else ""
        for ch in line
    )

class TermBtn(MouseActivatable, Static):

    DEFAULT_CSS = """
    TermBtn {
        width: 2;
        min-width: 2;
        height: 1;
        margin-left: 1;
        background: transparent;
        color: #8b949e;
        text-align: center;
    }

    TermBtn:hover {
        background: #21262d;
        color: #ffffff;
    }
    """

    def on_activate(self) -> None:
        self.post_message(self.Pressed(self.id or ""))

    class Pressed(Message):
        def __init__(self, btn_id: str):
            super().__init__()
            self.btn_id = btn_id

class EditorBottomTerminal(Vertical):

    DEFAULT_CSS = """EditorBottomTerminal {
        width: 100%;
        height: 100%;
        background: #0d1117;
        border: none;
        padding: 0 1;
    }
    #term-output-log {
        width: 100%;
        height: 1fr;
        background: #090d13;
        color: #c9d1d9;
        scrollbar-size-vertical: 1;
        scrollbar-color: #30363d;
        padding: 0 1;
    }

    #term-input-row {
        height: 1;
        background: transparent;
        padding: 0 1;
        align: left middle;
    }
    #term-prompt-prefix {
        width: auto;
        color: #3fb950;
        text-style: bold;
        margin-right: 1;
    }
    #term-command-input {
        width: 1fr;
        height: 1;
        background: transparent;
        border: none;
        color: #e6edf3;
        padding: 0;
    }
    #term-command-input:focus {
        border: none;
        background: transparent;
    }
"""

    BINDINGS = [
        Binding("escape", "dismiss_terminal", "Close Terminal"),
    ]
    class Closed(Message):
        pass

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.command_history: List[str] = []
        self.history_index: int = -1
        self._dragging = False

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._pump: Optional[asyncio.Task] = None
        self._seq: int = 0
        self._sentinel: Optional[str] = None
        self._pending_cwd: Optional[tuple] = None
        self._eats_prompt: bool = True
        self.shell_prompt: str = "PS >"
        self._argv: List[str] = []

    def compose(self) -> ComposeResult:

        yield RichLog(id="term-output-log", markup=True, highlight=True, wrap=True)

        with Horizontal(id="term-input-row"):
            yield Static("PS >", id="term-prompt-prefix")

            yield Input(id="term-command-input")

    _PS_BOOT = (
        "$OutputEncoding = [Console]::OutputEncoding = "
        "[Text.Encoding]::UTF8; "
        "try { $PSStyle.OutputRendering = 'AnsiRedirect' } catch {}; "
        "Remove-Module PSReadLine -ErrorAction SilentlyContinue"
    )

    def _new_sentinel(self) -> str:
        self._seq += 1
        return f"__CMDAI_DONE_{os.getpid()}_{self._seq}__"

    def _shells_for_windows(self) -> tuple:

        for exe, prompt in (
            ("pwsh.exe", "PS >"),
            ("powershell.exe", "PS >"),
            ("bash.exe", "$ "),
        ):
            path = _which(exe)
            if not path:
                continue
            if exe == "bash.exe":
                return [path, "--norc", "--noprofile"], prompt

            return [path, "-NoLogo", "-NoProfile", "-NoExit", "-Command", self._PS_BOOT],\
                prompt

        return [_which("cmd.exe") or "cmd.exe", "/Q", "/K", "prompt"], "PS >"

    def on_mount(self) -> None:
        log = self.query_one("#term-output-log", RichLog)
        argv, self.shell_prompt = self._shells_for_windows()
        exe = os.path.basename(argv[0])
        log.write(Text.from_markup(
            f"[bold #58a6ff]CMDAI Integrated Terminal[/] [dim]{exe} · persistent session[/]"))
        log.write(Text.from_markup(f"[dim]Working directory: {self.workdir}[/]\n"))
        self.set_prompt(self.shell_prompt)
        self.call_after_refresh(self._start_shell, argv)

    def set_prompt(self, prompt: str) -> None:
        self.shell_prompt = prompt
        try:
            self.query_one("#term-prompt-prefix", Static).update(prompt)
        except Exception:
            pass

    async def _start_shell(self, argv: List[str]) -> None:

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.workdir,
                bufsize=0,
            )
        except Exception as e:
            log = self.query_one("#term-output-log", RichLog)
            log.write(Text.from_markup(
                f"[#f85149]Could not start the shell: {e}[/]"))
            self._proc = None
            return

        self._argv = list(argv)

        if self._proc.pid:
            _SHELL_PIDS.add(self._proc.pid)
        self._pump = asyncio.create_task(self._pump_output())

        exe = os.path.basename(argv[0]).lower()
        if exe.startswith("bash"):
            await self._write(f"PS1='{self.shell_prompt}'\n")
        elif exe == "cmd.exe":
            await self._write(f"prompt {self.shell_prompt}\n")
        else:
            await self._write(f"function global:prompt {{ '{self.shell_prompt}' }}\n")

    async def _write(self, data: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write(data.encode("utf-8", "replace"))
            await self._proc.stdin.drain()
        except Exception as e:
            self.query_one("#term-output-log", RichLog).write(
                Text.from_markup(f"[#f85149]Terminal I/O error: {e}[/]"))

    async def _pump_output(self) -> None:

        assert self._proc is not None and self._proc.stdout is not None
        pending: List[str] = []
        while True:
            try:
                raw = await self._proc.stdout.readline()
            except Exception:
                break
            if not raw:
                break
            line = _printable(raw.decode("utf-8", "replace").rstrip("\r\n"))
            if self._sentinel and self._sentinel in line:

                out = "\n".join(pending)
                pending = []
                self._sentinel = None
                if self._pending_cwd:
                    self._sync_cwd(out)
                    self._pending_cwd = None
                else:
                    self._flush(out)
                continue

            echo = self._strip_prompt_echo(line)
            if echo is None:
                continue
            line = echo

            pending.append(line)
        self._flush("\n".join(pending))

    def _strip_prompt_echo(self, line: str) -> Optional[str]:

        prompt = self.shell_prompt
        if not prompt:
            return line
        bare = line.rstrip()
        if bare == prompt or bare == prompt.strip():
            return None
        if bare.startswith(prompt + " "):
            return None
        if bare.startswith(prompt):
            return bare[len(prompt):].lstrip()
        return line

    def _flush(self, text: str) -> None:

        if not text.strip():
            return
        log = self.query_one("#term-output-log", RichLog)
        for line in text.split("\n"):
            log.write(_ansi_to_rich(line))

    def _sync_cwd(self, out: str) -> None:

        candidate = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if candidate and os.path.isdir(candidate):
            self.workdir = os.path.abspath(candidate)
        self._refresh_cwd_label()

    def focus_input(self) -> None:
        self._refresh_cwd_label()
        inp = self.query_one("#term-command-input", Input)
        inp.focus()

    def write_output(self, text: str) -> None:

        log = self.query_one("#term-output-log", RichLog)
        log.write(text)

    def write_run_header(self, display: str) -> None:

        log = self.query_one("#term-output-log", RichLog)
        log.write(Text.from_markup(
            f"[bold #3fb950]RUN[/] [dim]{display}[/]"))

    def _short_cwd(self, path: str) -> str:

        home = os.path.expanduser("~")
        if home and path.lower().startswith(home.lower()):
            path = "~" + path[len(home):]
        if len(path) <= 38:
            return path
        return "..." + path[-35:]

    def _refresh_cwd_label(self) -> None:

        try:
            panel = self.app.query_one("#bottom-panel")
        except Exception:
            return
        setter = getattr(panel, "set_cwd", None)
        if callable(setter):
            setter(self.workdir)

    def action_dismiss_terminal(self) -> None:

        try:
            panel = self.app.query_one("#bottom-panel")
        except Exception:
            return
        closer = getattr(panel, "close_panel", None)
        if callable(closer):
            closer()

    def on_unmount(self) -> None:

        if self._pump is not None:
            self._pump.cancel()
            self._pump = None
        proc, self._proc = self._proc, None
        if proc is None:
            return
        pid = proc.pid
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        if pid:
            _kill_pid(pid)

    @on(TermBtn.Pressed)
    def on_term_btn_pressed(self, event: TermBtn.Pressed) -> None:

        bid = event.btn_id
        if bid == "term-btn-close":
            self.action_dismiss_terminal()
        elif bid == "term-btn-clear":
            self.query_one("#term-output-log", RichLog).clear()

    @on(Input.Submitted, "#term-command-input")
    async def on_command_submitted(self, event: Input.Submitted) -> None:
        raw_cmd = event.value.strip()
        inp = self.query_one("#term-command-input", Input)
        inp.value = ""
        if not raw_cmd:
            return

        self.command_history.append(raw_cmd)
        self.history_index = len(self.command_history)

        log = self.query_one("#term-output-log", RichLog)
        log.write(Text.from_markup(
            f"[bold #3fb950]{self.shell_prompt}[/] [bold #e6edf3]{raw_cmd}[/]"))

        if raw_cmd.lower() in ("exit", "quit"):
            self.action_dismiss_terminal()
            return

        if self._proc is None:
            log.write(Text.from_markup(
                "[#f85149]Shell is not running - reopen the terminal.[/]"))
            return

        sentinel = self._new_sentinel()
        self._sentinel = sentinel
        is_cd = raw_cmd.lower().startswith("cd ") or raw_cmd.lower() == "cd"
        if is_cd:

            self._pending_cd = ("", "")
            payload = f"{raw_cmd}; $PWD.Path"
        else:
            payload = raw_cmd
        await self._write(f"{payload}\nWrite-Output '{sentinel}'\n")
