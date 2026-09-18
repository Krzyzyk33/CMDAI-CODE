import os
import sys
import json
import time
import re
import ctypes
import queue
import threading
from typing import Any, Dict, List, Optional

TOOL_COMPLETE_PATTERNS = re.compile(
    r'(?:'
    r'<tool:\w+[^>]*?/>'
    r'|</tool:\w+>'
    r'|<tool_call>[^>]*?/>'
    r'|</tool_call>'
    r'|<\|tool_call>[^>]*?/>'
    r'|<tool:(?:ls|read|glob|search|code_search|bugs)[^>]*?>'
    r')',
    re.IGNORECASE
)

try:
    from llama_cpp import Llama, llama_log_set
    def _null_log(level, text, user_data):
        pass
    _C_LOG = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)(_null_log)
    try:
        llama_log_set(_C_LOG, None)
    except Exception:
        pass
except Exception:
    Llama = None


class ModelWorker:
    def __init__(self):
        self.llm = None
        self.current_model_path = None
        self.current_model_id = None
        self.abort_requested = False
        self.msg_queue = queue.Queue()
        self._listener_thread = None

    def find_model_path(self, model_filename: str) -> Optional[str]:
        base_name = os.path.basename(model_filename)
        pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        candidates = [
            os.path.join("models", model_filename),
            os.path.join("models", base_name),
            os.path.abspath(model_filename),
            os.path.join(r"D:\CMDAI CODE\models", base_name),
            os.path.join(r"E:\CMDAI CODE\models", base_name),
            os.path.join(r"C:\CMDAI CODE\models", base_name),
            os.path.join(pkg_root, "models", base_name),
            os.path.join(os.path.expanduser("~"), ".cmdai", "models", base_name),
        ]
        for c in candidates:
            if c and os.path.exists(c):
                return c
        return None

    def load(self, model_filename: str, loader_type: str = "cpu", n_ctx: int = 2048) -> Dict[str, Any]:
        if not Llama:
            return {"status": "error", "error": "llama_cpp not installed"}
        path = self.find_model_path(model_filename)
        if not path:
            return {"status": "error", "error": f"Model not found: {model_filename}"}

        if self.llm is not None and self.current_model_path == path:
            return {"status": "ok", "elapsed": 0.0, "cached": True}

        t0 = time.time()
        n_gpu = -1 if loader_type.lower() in ("vulkan", "cuda") else 0
        try:
            if self.llm is not None:
                del self.llm
                self.llm = None

            self.llm = Llama(
                model_path=path,
                n_ctx=n_ctx or 8192,
                n_gpu_layers=n_gpu,
                flash_attn=True,
                verbose=False,
            )
            self.current_model_path = path
            self.current_model_id = model_filename
            elapsed = time.time() - t0
            return {"status": "ok", "elapsed": elapsed}
        except Exception as e:
            if n_gpu != 0:
                try:
                    self.llm = Llama(
                        model_path=path,
                        n_ctx=n_ctx or 8192,
                        n_gpu_layers=0,
                        flash_attn=True,
                        verbose=False,
                    )
                    self.current_model_path = path
                    self.current_model_id = model_filename
                    elapsed = time.time() - t0
                    return {"status": "ok", "elapsed": elapsed}
                except Exception as e2:
                    return {"status": "error", "error": str(e2)}
            return {"status": "error", "error": str(e)}

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.6, max_tokens: int = 2048):
        if not self.llm:
            sys.stdout.write(json.dumps({"type": "error", "error": "Model not loaded"}) + "\n")
            sys.stdout.flush()
            return

        self.abort_requested = False
        full_text = []
        full_thinking = []
        is_in_thinking = False
        t0 = time.time()

        stop_tokens = ["<end_of_turn>", "<|end_of_turn|>", "<eos>", "<|im_end|>", "<|turn_end|>", "</s>"]
        try:
            stream = self.llm.create_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop_tokens,
                stream=True,
            )
            for chunk in stream:
                if self.abort_requested:
                    break
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if not content:
                    continue

                think_open = "<think>" if "<think>" in content else ("<thought>" if "<thought>" in content else None)
                if think_open:
                    close_tag = "</think>" if think_open == "<think>" else "</thought>"
                    parts = content.split(think_open, 1)
                    if parts[0]:
                        full_text.append(parts[0])
                        sys.stdout.write(json.dumps({"type": "token", "content": parts[0]}) + "\n")
                        sys.stdout.flush()
                    is_in_thinking = True
                    remainder = parts[1]
                    if close_tag in remainder:
                        th_parts = remainder.split(close_tag, 1)
                        full_thinking.append(th_parts[0])
                        sys.stdout.write(json.dumps({"type": "thinking", "content": th_parts[0]}) + "\n")
                        sys.stdout.flush()
                        is_in_thinking = False
                        if th_parts[1]:
                            full_text.append(th_parts[1])
                            sys.stdout.write(json.dumps({"type": "token", "content": th_parts[1]}) + "\n")
                            sys.stdout.flush()
                    else:
                        full_thinking.append(remainder)
                        sys.stdout.write(json.dumps({"type": "thinking", "content": remainder}) + "\n")
                        sys.stdout.flush()
                    continue

                if is_in_thinking:
                    close_tag = "</think>" if "</think>" in content else ("</thought>" if "</thought>" in content else None)
                    if close_tag:
                        parts = content.split(close_tag, 1)
                        full_thinking.append(parts[0])
                        sys.stdout.write(json.dumps({"type": "thinking", "content": parts[0]}) + "\n")
                        sys.stdout.flush()
                        is_in_thinking = False
                        if parts[1]:
                            full_text.append(parts[1])
                            sys.stdout.write(json.dumps({"type": "token", "content": parts[1]}) + "\n")
                            sys.stdout.flush()
                            if TOOL_COMPLETE_PATTERNS.search("".join(full_text)):
                                break
                    else:
                        full_thinking.append(content)
                        sys.stdout.write(json.dumps({"type": "thinking", "content": content}) + "\n")
                        sys.stdout.flush()
                    continue

                full_text.append(content)
                sys.stdout.write(json.dumps({"type": "token", "content": content}) + "\n")
                sys.stdout.flush()

                curr_txt = "".join(full_text)
                if TOOL_COMPLETE_PATTERNS.search(curr_txt):
                    break

            elapsed = time.time() - t0
            sys.stdout.write(json.dumps({
                "type": "done",
                "response": "".join(full_text),
                "thinking": "".join(full_thinking),
                "elapsed": elapsed,
                "aborted": self.abort_requested,
            }) + "\n")
            sys.stdout.flush()

        except Exception as e:
            sys.stdout.write(json.dumps({"type": "error", "error": str(e)}) + "\n")
            sys.stdout.flush()

    def _listen_stdin(self):
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    self.msg_queue.put(None)
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("action") == "abort":
                    self.abort_requested = True
                else:
                    self.msg_queue.put(msg)
            except Exception:
                self.msg_queue.put(None)
                break

    def run(self):
        self._listener_thread = threading.Thread(target=self._listen_stdin, daemon=True)
        self._listener_thread.start()

        while True:
            msg = self.msg_queue.get()
            if msg is None:
                break

            action = msg.get("action")
            if action == "ping":
                sys.stdout.write(json.dumps({"status": "pong"}) + "\n")
                sys.stdout.flush()
            elif action == "load":
                res = self.load(
                    model_filename=msg.get("model", ""),
                    loader_type=msg.get("loader", "cpu"),
                    n_ctx=msg.get("n_ctx", 8192),
                )
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()
            elif action == "chat":
                self.chat(
                    messages=msg.get("messages", []),
                    temperature=msg.get("temperature", 0.6),
                    max_tokens=msg.get("max_tokens", 2048),
                )
            elif action == "abort":
                self.abort_requested = True
            elif action == "unload":
                if self.llm:
                    del self.llm
                    self.llm = None
                self.current_model_path = None
                self.current_model_id = None
                sys.stdout.write(json.dumps({"status": "unloaded"}) + "\n")
                sys.stdout.flush()
            elif action == "exit":
                break


if __name__ == "__main__":
    worker = ModelWorker()
    worker.run()
