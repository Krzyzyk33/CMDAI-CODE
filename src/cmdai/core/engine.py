import os
import sys
import threading
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

try:
    from llama_cpp import Llama, llama_log_set
    import ctypes

    def _null_log_callback(level, text, user_data):
        pass

    _C_LOG_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)(_null_log_callback)
    try:
        llama_log_set(_C_LOG_CALLBACK, None)
    except Exception:
        pass
except ImportError:
    Llama = None


import json
import subprocess


class SimpleGGUFLoader:
    """Manages loading and running local GGUF models in an isolated out-of-process worker
    to prevent UI freezing, memory lock, and GIL contention."""

    def __init__(self, models_dir: Optional[str] = None):
        if not models_dir:
            from .settings import get_settings
            cfg_dir = get_settings().config.get("models_dir")
            pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            candidates = [
                cfg_dir,
                os.path.abspath("models"),
                "D:/CMDAI CODE/models",
                "D:\\CMDAI CODE\\models",
                "E:/CMDAI CODE/models",
                "E:\\CMDAI CODE\\models",
                os.path.join(pkg_root, "models"),
            ]
            found_dir = None
            for cand in candidates:
                if cand and os.path.exists(cand) and os.path.isdir(cand):
                    try:
                        if any(f.endswith(".gguf") for f in os.listdir(cand)):
                            found_dir = cand
                            break
                    except Exception:
                        pass
            if not found_dir:
                for cand in candidates:
                    if cand and os.path.exists(cand) and os.path.isdir(cand):
                        found_dir = cand
                        break
            models_dir = found_dir or "models"
        self.models_dir = models_dir
        self.current_model_path: Optional[str] = None
        self.current_loader: Optional[str] = None
        self._is_loaded = False
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    @property
    def llm(self) -> Optional[Any]:
        return True if self._is_loaded else None

    def _ensure_worker(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True

        worker_script = os.path.join(os.path.dirname(__file__), "model_worker.py")
        if not os.path.exists(worker_script):
            return False

        try:
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.CREATE_NO_WINDOW | 0x00004000

            self._proc = subprocess.Popen(
                [sys.executable, worker_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=flags,
            )
            return True
        except Exception:
            return False

    def unload(self) -> None:
        """Unloads current model from memory in the worker process."""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(json.dumps({"action": "unload"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.stdout.readline()
                except Exception:
                    pass
            self._is_loaded = False
            self.current_model_path = None
            self.current_loader = None

    def close(self) -> None:
        """Closes and terminates background worker process."""
        with self._lock:
            if self._proc:
                try:
                    self._proc.stdin.write(json.dumps({"action": "exit"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None
            self._is_loaded = False

    def list_models(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.models_dir):
            return []
        models = []
        for filename in sorted(os.listdir(self.models_dir)):
            if filename.endswith(".gguf"):
                path = os.path.join(self.models_dir, filename)
                size_mb = os.path.getsize(path) / (1024 * 1024)
                models.append({
                    "id": filename,
                    "name": filename,
                    "path": path,
                    "size_mb": round(size_mb, 1),
                })
        return models

    def load_model(
        self,
        model_filename: str,
        n_ctx: int = 0,
        n_gpu_layers: Optional[int] = None,
        loader_type: Optional[str] = None,
    ) -> bool:
        with self._lock:
            if (
                self._is_loaded
                and self.current_model_path
                and os.path.basename(self.current_model_path) == os.path.basename(model_filename)
            ):
                return True

            if not self._ensure_worker():
                return False

            from .settings import get_settings
            loader = loader_type or get_settings().config.get("active_loader", "cpu")

            req = {
                "action": "load",
                "model": model_filename,
                "loader": loader,
                "n_ctx": n_ctx or 8192,
            }
            try:
                self._proc.stdin.write(json.dumps(req) + "\n")
                self._proc.stdin.flush()
                resp_line = self._proc.stdout.readline()
                if not resp_line:
                    return False
                res = json.loads(resp_line)
                if res.get("status") == "ok":
                    self._is_loaded = True
                    self.current_model_path = model_filename
                    self.current_loader = loader
                    return True
                return False
            except Exception:
                return False

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.6,
        max_tokens: int = 2048,
        on_token: Optional[Callable[[str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
        model_filename: Optional[str] = None,
        is_aborted: Optional[Callable[[], bool]] = None,
    ) -> Tuple[str, str]:
        if not self._is_loaded:
            target = model_filename or "gemma-4-E4B-it-Q4_0.gguf"
            ok = self.load_model(target)
            if not ok:
                err = f"Failed to load model: {target}"
                if on_token:
                    on_token(err)
                return err, ""

        if not self._ensure_worker():
            err = "Model worker process unavailable."
            if on_token:
                on_token(err)
            return err, ""

        req = {
            "action": "chat",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
        except Exception as e:
            err = f"Worker communication error: {e}"
            if on_token:
                on_token(err)
            return err, ""

        abort_stopped = threading.Event()

        def _monitor_abort():
            while not abort_stopped.is_set():
                if is_aborted and is_aborted():
                    try:
                        self._proc.stdin.write(json.dumps({"action": "abort"}) + "\n")
                        self._proc.stdin.flush()
                    except Exception:
                        pass
                    break
                time.sleep(0.04)

        abort_thread = threading.Thread(target=_monitor_abort, daemon=True)
        abort_thread.start()

        full_text: List[str] = []
        full_thinking: List[str] = []

        try:
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue

                mtype = msg.get("type")
                if mtype == "token":
                    content = msg.get("content", "")
                    full_text.append(content)
                    if on_token:
                        on_token(content)
                elif mtype == "thinking":
                    content = msg.get("content", "")
                    full_thinking.append(content)
                    if on_thinking:
                        on_thinking(content)
                elif mtype == "done":
                    resp = msg.get("response", "".join(full_text))
                    think = msg.get("thinking", "".join(full_thinking))
                    return resp, think
                elif mtype == "error":
                    err = msg.get("error", "Generation error")
                    if on_token:
                        on_token(f"\n[Error: {err}]")
                    return err, ""
        finally:
            abort_stopped.set()

        return "".join(full_text), "".join(full_thinking)

