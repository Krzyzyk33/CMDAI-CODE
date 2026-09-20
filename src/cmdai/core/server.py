import http.server
import json
import socketserver
import threading
from typing import Any, Dict, Optional


class BackgroundHTTPServer:

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        app_ref: Optional[Any] = None,
    ):
        self.host = host
        self.port = port
        self.app_ref = app_ref
        self._server: Optional[socketserver.TCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.is_running = False

    def start(self) -> bool:
        if self.is_running:
            return True

        handler_cls = self._create_handler()
        try:
            socketserver.TCPServer.allow_reuse_address = True
            self._server = socketserver.TCPServer((self.host, self.port), handler_cls)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            self.is_running = True
            return True
        except Exception:
            self.is_running = False
            return False

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        self.is_running = False

    def _create_handler(self):
        server_self = self

        class CMDAIHTTPHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path in ("/", "/api/version"):
                    self._send_json({
                        "version": "1.0.0",
                        "status": "CMDAI CODE background server online",
                        "active_model": getattr(server_self.app_ref, "current_model_id", "none"),
                    })
                elif self.path == "/api/tags":
                    self._send_json({
                        "models": [
                            {
                                "name": getattr(server_self.app_ref, "current_model_id", "cmdai-code"),
                                "model": getattr(server_self.app_ref, "current_model_id", "cmdai-code"),
                                "modified_at": "2026-09-04T00:00:00Z",
                                "size": 7000000000,
                            }
                        ]
                    })
                else:
                    self._send_json({"error": "Endpoint not found"}, status=404)

            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}

                if self.path in ("/api/chat", "/api/generate", "/v1/chat/completions"):
                    messages = data.get("messages", [])
                    prompt = data.get("prompt", "")
                    if not messages and prompt:
                        messages = [{"role": "user", "content": prompt}]

                    self._send_json({
                        "id": "chatcmpl-cmdai",
                        "object": "chat.completion",
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "CMDAI CODE background server response.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                    })
                else:
                    self._send_json({"error": "Endpoint not found"}, status=404)

            def _send_json(self, data: Dict[str, Any], status: int = 200):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))

        return CMDAIHTTPHandler
