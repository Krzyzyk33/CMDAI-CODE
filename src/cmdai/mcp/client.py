import json
import os
import re
import subprocess
import threading
from typing import Any, Dict, List, Optional


def sanitize_name(name: str) -> str:
    return re.sub(r"\W+", "_", (name or "").strip()).strip("_").lower() or "server"


class MCPServer:
    def __init__(self, name: str, command: str, args: List[str]):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.proc: Optional[subprocess.Popen] = None
        self._req_id = 0
        self._lock = threading.Lock()
        self.status = "disconnected"                                    
        self.error = ""
        self.stderr_log: List[str] = []
        self.tools: List[Dict[str, Any]] = []

    def _reader(self) -> None:
        try:
            assert self.proc and self.proc.stderr
            for line in self.proc.stderr:
                self.stderr_log.append(line.rstrip("\n"))
                del self.stderr_log[:-200]
        except Exception:
            pass

    def _send(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        assert self.proc and self.proc.stdin and self.proc.stdout
        with self._lock:
            self._req_id += 1
            rid = self._req_id
            self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
            self.proc.stdin.flush()
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("server closed stdout")
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise RuntimeError(str(msg["error"]))
                    return msg.get("result", {})

    def connect(self, timeout: float = 15.0) -> bool:
        try:
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1, creationflags=flags,
            )
            threading.Thread(target=self._reader, daemon=True).start()
            self._send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
            res = self._send("tools/list", {})
            self.tools = res.get("tools", []) if isinstance(res, dict) else []
            self.status = "connected"
            self.error = ""
            return True
        except Exception as e:
            self.status = "error"
            self.error = str(e)[:300]
            self.stop()
            return False

    def call_tool(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        res = self._send("tools/call", {"name": tool, "arguments": arguments or {}})
        return res if isinstance(res, dict) else {"content": res}

    def stop(self) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass
        self.proc = None
        if self.status == "connected":
            self.status = "disconnected"


class MCPClient:
    def __init__(self, config_path: str = ""):
        self.config_path = config_path
        self.servers: Dict[str, MCPServer] = {}

    def load_config(self, app_root: str = "") -> Dict[str, Dict[str, Any]]:
        path = self.config_path or os.path.join(app_root or os.getcwd(), "mcp_config.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return (json.load(f).get("mcp_servers", {}) or {})
        except Exception:
            return {}

    def connect_autostart(self, app_root: str = "") -> None:
        for name, cfg in self.load_config(app_root).items():
            if not isinstance(cfg, dict) or not cfg.get("autostart"):
                continue
            sname = sanitize_name(name)
            srv = MCPServer(sname, cfg.get("command", ""), cfg.get("args", []))
            self.servers[sname] = srv
            threading.Thread(target=srv.connect, daemon=True).start()

    def restart(self, name: str) -> bool:
        srv = self.servers.get(sanitize_name(name))
        if not srv:
            return False
        srv.stop()
        return srv.connect()

    def call_tool(self, server: str, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        srv = self.servers.get(sanitize_name(server))
        if not srv or srv.status != "connected":
            return {"success": False, "error": f"MCP server '{server}' not connected"}
        try:
            res = srv.call_tool(tool, arguments)
            return {"success": True, "server": server, "tool": tool, "result": res}
        except Exception as e:
            return {"success": False, "error": str(e)[:500]}

    def status_lines(self) -> List[str]:
        lines = []
        for name, srv in self.servers.items():
            tools = ", ".join(t.get("name", "") for t in srv.tools) if srv.status == "connected" else ""
            mark = "●" if srv.status == "connected" else ("✕" if srv.status == "error" else "○")
            extra = f"tools: {tools}" if tools else (f"({srv.error})" if srv.error else "(not started)")
            lines.append(f"{mark}  {name:<18} {srv.status:<13} {extra}")
        return lines

    def dynamic_tools(self) -> Dict[str, tuple]:
        out = {}
        for sname, srv in self.servers.items():
            if srv.status != "connected":
                continue
            for t in srv.tools:
                tname = sanitize_name(str(t.get("name", "")))
                if tname:
                    out[f"{sname}_{tname}"] = (sname, str(t.get("name", "")))
        return out
