import sys
import json
import threading
import time
import builtins as _builtins

_lock = threading.Lock()
interrupt_requested = False

_pending_interactive = None
_pending_event = threading.Event()


def send(msg):
    with _lock:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def request_interactive(prompt, choices=None):
    global _pending_interactive, _pending_event
    mid = str(time.time())[:8]
    _pending_interactive = {"id": mid, "result": None}
    _pending_event.clear()
    if choices:
        send(
            {
                "type": "interactive_choices",
                "prompt": prompt,
                "choices": choices,
                "id": mid,
            }
        )
    else:
        send({"type": "interactive_text", "prompt": prompt, "id": mid})
    _pending_event.wait()
    result = _pending_interactive["result"]
    _pending_interactive = None
    return result


_orig_input = _builtins.input


def _ipc_input(prompt=""):
    return request_interactive(prompt) or ""


def install_input_hook():
    _builtins.input = _ipc_input


def uninstall_input_hook():
    _builtins.input = _orig_input


def handle_interactive_response(msg):
    global _pending_interactive, _pending_event
    if _pending_interactive and msg.get("id") == _pending_interactive["id"]:
        _pending_interactive["result"] = msg.get("text", "")
        _pending_event.set()
        return True
    return False
