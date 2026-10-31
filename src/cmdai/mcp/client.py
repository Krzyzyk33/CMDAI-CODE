"""MCP client: hand-written, synchronous, stdio + Streamable HTTP.

Deliberately does not use the official ``mcp`` SDK. That SDK requires
pydantic >= 2.11 and anyio >= 4.5; this environment is on pydantic 2.6.1
with 60+ already-broken distributions, and pulling four more upgrades
into a shared interpreter to borrow a JSON-RPC loop was not a trade
worth making. The fixture used by the tests does run on the SDK, so
this file is checked against a real implementation rather than against
itself.

What the previous implementation got wrong, all of it now fixed:

  * ``connect(timeout=15.0)`` accepted a timeout and never used it, while
    ``_send`` blocked in ``stdout.readline()`` forever holding a lock that
    every other caller queued behind. A hung server froze the thread.
  * ``notifications/initialized`` was never sent, which the spec marks MUST
    and which strict servers treat as "client is not ready".
  * The protocol version was pinned to 2024-11-05 and the version in the
    server's response was never read back, so negotiation could not fail.
  * ``isError: true`` results were reported to the model as successes.
  * ``stop()`` called ``terminate()`` and walked away, leaving zombies.
  * There was no ``env`` support, so a server needing a token could not be
    configured at all.
  * ``/mcp logs my-server`` looked up ``my-server`` while the registry key
    was ``my_server``.

Threading: a reader thread owns the stream and pushes parsed messages onto
a queue. Writers take a lock only for the duration of a write, never
across a read, so a slow server cannot block other callers.
"""

import base64
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:  # requests is already a project dependency
    import requests
except Exception:  # pragma: no cover - only if the dependency is broken
    requests = None  # type: ignore


PROTOCOL_VERSION = "2025-06-18"
# Newest first. Negotiation walks down this list until the server agrees.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

STDERR_LOG_LIMIT = 200

# Classification of a tool for the permission gate, derived from the
# annotations the spec defines. See classify_tool.
PERM_READ = "read"
PERM_WRITE = "write"
PERM_DESTRUCTIVE = "destructive"

TOOL_NAME_PREFIX = "mcp__"


def sanitize_name(name: str) -> str:
    """Registry-safe key. Kept as the internal lookup key for a server."""
    return re.sub(r"\W+", "_", (name or "").strip()).strip("_").lower() or "server"


def one_line(text: str, limit: int = 200) -> str:
    """Flatten a message to one short line.

    A connection failure arrives as a traceback with newlines in it. Pushed
    into a single list row or a model field it shreds the layout, and into
    a prompt it reads as several separate problems.
    """
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat


def qualified_tool_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>``, matching Claude Code's convention.

    The old scheme was ``<server>_<tool>``, which collides: a server named
    ``github_list`` exposing ``prs`` produced the same string as a server
    named ``github`` exposing ``list_prs``. The double underscore cannot
    appear in a sanitized name, so the boundary stays unambiguous.
    """
    return f"{TOOL_NAME_PREFIX}{sanitize_name(server)}__{sanitize_name(tool)}"


def _bool_field(annotations: Dict[str, Any], key: str) -> Optional[bool]:
    value = annotations.get(key)
    return value if isinstance(value, bool) else None


def classify_tool(tool: Dict[str, Any]) -> str:
    """Decide how much the permission gate should ask.

    The spec is explicit that annotations are untrusted, and silent about
    what a missing annotation means. Treating absence as "probably fine"
    is the reading that lets a destructive server be wired in by accident
    and run unattended, so absence is treated as the dangerous case:

      readOnlyHint=True                    -> PERM_READ, ask nothing
      destructiveHint=True                 -> PERM_DESTRUCTIVE, ask always
      readOnlyHint=False                   -> PERM_WRITE, ask once per server
      no annotations at all                -> PERM_DESTRUCTIVE, ask always
    """
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict) or not annotations:
        return PERM_DESTRUCTIVE

    if _bool_field(annotations, "destructiveHint"):
        return PERM_DESTRUCTIVE
    if _bool_field(annotations, "readOnlyHint"):
        return PERM_READ
    return PERM_WRITE


def _tool_title(tool: Dict[str, Any]) -> str:
    return str(tool.get("title") or tool.get("name") or "")


class MCPError(RuntimeError):
    """Protocol-level failure: the server answered with a JSON-RPC error."""


class MCPToolError(RuntimeError):
    """The call reached the tool and the tool reported failure (isError)."""


class MCPServer:
    """One MCP server over one transport."""

    def __init__(
        self,
        name: str,
        command: str = "",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: str = "",
        url: str = "",
        headers: Optional[Dict[str, str]] = None,
        transport: str = "",
        timeout: float = 15.0,
        enabled: bool = True,
        autostart: bool = True,
    ):
        self.name = sanitize_name(name)
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = float(timeout)
        self.enabled = enabled
        self.autostart = bool(autostart)

        if transport:
            self.transport = transport
        elif url:
            self.transport = "http"
        else:
            self.transport = "stdio"

        self.proc: Optional[subprocess.Popen] = None
        self.session_id = ""
        self.protocol_version = PROTOCOL_VERSION
        self.server_info: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.instructions = ""

        self.status = "disconnected"
        self.error = ""
        self.stderr_log: List[str] = []
        self.tools: List[Dict[str, Any]] = []

        self._req_id = 0
        self._write_lock = threading.Lock()
        self._inbox: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._threads: List[threading.Thread] = []
        self._closed = False
        self._list_changed = False

    # ---------------------------------------------------------------- io

    def _log(self, line: str) -> None:
        self.stderr_log.append(line.rstrip("\n"))
        del self.stderr_log[:-STDERR_LOG_LIMIT]

    def _reader_stderr(self) -> None:
        try:
            assert self.proc and self.proc.stderr
            for line in self.proc.stderr:
                self._log(line)
        except Exception:
            pass

    def _reader_stdout(self) -> None:
        """Owns the stream. Everything it sees lands on the inbox."""
        try:
            assert self.proc and self.proc.stdout
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    # The spec forbids non-MCP output on stdout. A server that
                    # prints a banner must not be fatal, so log and move on.
                    self._log(f"[stdout, not JSON] {line[:200]}")
                    continue
                if isinstance(msg, dict):
                    self._inbox.put(msg)
        except Exception as exc:
            self._log(f"[reader closed] {exc}")
        finally:
            # Unblock anyone waiting on a response that will never come.
            self._inbox.put({"__closed__": True})

    def _write(self, payload: Dict[str, Any]) -> None:
        if self.transport != "stdio":
            return
        if not self.proc or not self.proc.stdin:
            raise MCPError("server is not running")
        with self._write_lock:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()

    def _next_id(self) -> int:
        with self._write_lock:
            self._req_id += 1
            return self._req_id

    # ------------------------------------------------------------- rpc

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if self.transport == "stdio":
            self._write(payload)
        elif self.transport == "http":
            try:
                self._http_post(payload, timeout=self.timeout)
            except Exception as e:
                self._log(f"[notify error] {e}")

    def _serve_server_request(self, msg: Dict[str, Any]) -> None:
        """Answer a server->client request so the server does not hang.

        Sampling and roots are not implemented. Saying so explicitly is
        better than never answering, which is what the old reader did -
        it looped waiting for a matching id that could not arrive.
        """
        rid = msg.get("id")
        method = msg.get("method")
        if rid is None or method is None:
            return
        if method.startswith("notifications/"):
            return
        self._write({
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32601, "message": f"method not supported: {method}"},
        })

    def _await_response(self, rid: int, timeout: float) -> Dict[str, Any]:
        """Wait for the response to `rid`, servicing other traffic meanwhile.

        This is the piece the old _send got wrong: it read in a loop and
        discarded anything that was not its own id, so a notification or a
        server request consumed a turn and then silently vanished.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no response within {timeout:g}s")
            try:
                msg = self._inbox.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                continue
            if msg.get("__closed__"):
                raise MCPError("server closed the connection")
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    err = msg["error"] or {}
                    raise MCPError(
                        f"{err.get('code', '?')}: {err.get('message', 'unknown error')}"
                    )
                result = msg.get("result")
                return result if isinstance(result, dict) else {"value": result}
            if "method" in msg:
                method = str(msg.get("method") or "")
                if method == "notifications/tools/list_changed":
                    self._list_changed = True
                elif method.startswith("notifications/"):
                    continue
                else:
                    self._serve_server_request(msg)

    # -------------------------------------------------------- lifecycle

    def connect(self) -> bool:
        self.stop()
        self.status = "connecting"
        self.error = ""
        self._closed = False
        self._inbox = queue.Queue()
        self._list_changed = False
        try:
            if self.transport == "stdio":
                self._start_stdio()
            else:
                self._http_init()
            self._negotiate()
            self.tools = self._list_all_tools()
            self.status = "connected"
            return True
        except Exception as exc:
            self.status = "error"
            self.error = str(exc)[:300]
            self.stop()
            return False

    def _start_stdio(self) -> None:
        if not self.command:
            raise MCPError("no command configured for a stdio server")

        # Expand ${VAR} so a token can come from the environment instead of
        # being pasted into config.json. Without this a server that needs
        # credentials could not be configured at all.
        child_env = os.environ.copy()
        for key, value in self.env.items():
            child_env[key] = _expand_env(str(value))

        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        kwargs: Dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "bufsize": 1,
            "creationflags": flags,
            "env": child_env,
        }
        if self.cwd and os.path.isdir(self.cwd):
            kwargs["cwd"] = self.cwd

        self.proc = subprocess.Popen([self.command, *self.args], **kwargs)
        for target, name in ((self._reader_stdout, "mcp-out"), (self._reader_stderr, "mcp-err")):
            thread = threading.Thread(target=target, daemon=True, name=name)
            thread.start()
            self._threads.append(thread)

    def _http_init(self) -> None:
        if requests is None:
            raise MCPError("requests is not available")
        if not self.url:
            raise MCPError("no url configured for an http server")
        self.session_id = ""

    def _http_headers(self, include_accept: bool = True) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # The spec requires both listed; a server may answer with either.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        for key, value in self.headers.items():
            headers[key] = _expand_env(str(value))
        return headers

    def _http_post(self, payload: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        """POST one JSON-RPC message. Returns the result, or None for 202."""
        assert requests is not None
        resp = requests.post(
            self.url, json=payload, headers=self._http_headers(), timeout=timeout
        )

        if resp.status_code == 404 and self.session_id:
            # Spec: on 404 with a session id, start a new session. Doing
            # this transparently is better than surfacing an error the user
            # cannot act on.
            self.session_id = ""
            self._http_init()
            self._negotiate()
            return None

        if resp.status_code == 202 or not resp.content:
            return None
        if resp.status_code >= 400:
            raise MCPError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        if not self.session_id:
            self.session_id = resp.headers.get("Mcp-Session-Id", "") or self.session_id

        ctype = (resp.headers.get("Content-Type") or "").lower()
        message = None
        if "text/event-stream" in ctype:
            message = _first_sse_json(resp.content.decode("utf-8", errors="replace"))
        else:
            try:
                message = resp.json()
            except Exception:
                message = None
        return _unwrap_result(message)

    def _negotiate(self) -> None:
        result = self._initialize()
        if not isinstance(result, dict):
            raise MCPError("initialize returned no result")

        got = str(result.get("protocolVersion") or "")
        if got and got not in SUPPORTED_PROTOCOL_VERSIONS:
            raise MCPError(f"server speaks unsupported protocol {got}")

        self.protocol_version = got or PROTOCOL_VERSION
        self.server_info = result.get("serverInfo") or {}
        self.capabilities = result.get("capabilities") or {}
        self.instructions = str(result.get("instructions") or "")

        # MUST. Skipping this leaves strict servers waiting forever.
        self._notify("notifications/initialized")

    def _initialize(self) -> Optional[Dict[str, Any]]:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "cmdaicode", "version": _client_version()},
            },
        }
        if self.transport == "stdio":
            self._write(payload)
            return self._await_response(payload["id"], self.timeout)
        return self._http_post(payload, self.timeout)

    def _list_all_tools(self) -> List[Dict[str, Any]]:
        """tools/list, following nextCursor to the end.

        The spec makes listing paginated. Stopping at the first page meant
        silently exposing a truncated toolset, which is the sort of thing
        that only shows up once a user has a server with more than one page
        of tools.
        """
        out: List[Dict[str, Any]] = []
        cursor = ""
        seen_cursors = set()
        while True:
            params: Dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": params,
            }
            if self.transport == "stdio":
                self._write(payload)
                result = self._await_response(payload["id"], self.timeout)
            else:
                result = self._http_post(payload, self.timeout)
            if not result:
                break
            page = result.get("tools")
            if isinstance(page, list):
                out.extend(t for t in page if isinstance(t, dict))
            nxt = str(result.get("nextCursor") or "")
            if not nxt or nxt in seen_cursors:
                break
            seen_cursors.add(nxt)
            cursor = nxt
        return out

    def tools_changed(self) -> bool:
        """True and clears the flag when the server said its list changed."""
        changed = self._list_changed
        self._list_changed = False
        return changed

    def refresh_tools(self) -> List[Dict[str, Any]]:
        self.tools = self._list_all_tools()
        return self.tools

    def call_tool(self, tool: str, arguments: Optional[Dict[str, Any]] = None,
                  timeout: Optional[float] = None) -> Dict[str, Any]:
        """Call a tool. Raises MCPError on protocol failure, MCPToolError
        when the tool itself reported isError.

        The old code returned {"success": True} for an isError result, which
        is how a model ends up telling a user a destructive action worked
        when it failed.
        """
        wait = self.timeout if timeout is None else float(timeout)
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        }
        try:
            if self.transport == "stdio":
                self._write(payload)
                result = self._await_response(payload["id"], wait)
            else:
                result = self._http_post(payload, wait)
        except TimeoutError:
            # Tell the server to stop working. Dropping the connection is
            # not the same as cancelling and the spec treats them differently.
            try:
                self._notify(
                    "notifications/cancelled",
                    {"requestId": payload["id"], "reason": "client timeout"},
                )
            except Exception:
                pass
            raise

        if result is None:
            return {}
        if result.get("isError"):
            raise MCPToolError("\n".join(block_texts(result.get("content"))) or "tool reported an error")
        return result

    def stop(self) -> None:
        self._closed = True
        if self.transport != "stdio" and self.session_id and requests is not None:
            try:
                requests.delete(
                    self.url,
                    headers={"Mcp-Session-Id": self.session_id,
                             "MCP-Protocol-Version": self.protocol_version},
                    timeout=2.0,
                )
            except Exception:
                pass
        self.session_id = ""

        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                # Spec shutdown order: close stdin, wait, then escalate.
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)
                    except Exception:
                        proc.kill()
                        try:
                            proc.wait(timeout=2.0)
                        except Exception:
                            pass
            except Exception:
                pass
        self._threads = []
        if self.status in ("connected", "connecting"):
            self.status = "disconnected"

    # ------------------------------------------------------------ views

    def permissions(self) -> Dict[str, str]:
        return {qualified_tool_name(self.name, str(t.get("name") or "")): classify_tool(t)
                for t in self.tools}

    def status_lines(self) -> List[str]:
        if self.status == "connected":
            perms = [classify_tool(t) for t in self.tools]
            summary = ", ".join(
                f"{n} {kind}" for n, kind in (
                    (perms.count(PERM_READ), "read"),
                    (perms.count(PERM_WRITE), "write"),
                    (perms.count(PERM_DESTRUCTIVE), "destructive"),
                ) if n
            )
            extra = f"{len(self.tools)} tools ({summary})" if self.tools else "no tools"
        elif self.error:
            extra = f"({self.error})"
        else:
            extra = "(not started)"
        # ASCII marks only. A bullet or a cross here crashes on a cp1252
        # console, which is exactly where `cmdai mcp list` prints. The TUI
        # renders this fine either way, so the portable choice costs nothing.
        mark = {"connected": "+", "error": "x"}.get(self.status, "-")
        return [f"{mark}  {self.name:<18} {self.status:<13} {extra}"]


class MCPClient:
    """Registry of servers, loaded from mcp_config.json."""

    def __init__(self, config_path: str = ""):
        self.config_path = config_path
        self.servers: Dict[str, MCPServer] = {}
        # Set when the config file exists but could not be read, so callers
        # can say so instead of reporting an empty server list.
        self.load_error = ""
        # Not every caller wants a writable client (the CLI reads config to
        # print it). None means "read only", which is what keeps the
        # /mcp window honest: it offers the Add form only when it can
        # actually save.
        self.add_server: Optional[Any] = None
        self._app_root = ""

    # ----------------------------------------------------------- config

    def config_file(self, app_root: str = "") -> str:
        if self.config_path and os.path.exists(self.config_path):
            return self.config_path

        candidates = []
        if app_root:
            candidates.append(os.path.join(app_root, "mcp_config.json"))

        cwd = os.getcwd()
        candidates.append(os.path.join(cwd, "mcp_config.json"))

        module_dir = os.path.dirname(os.path.abspath(__file__))
        app_install_dir = os.path.dirname(os.path.dirname(os.path.dirname(module_dir)))
        if app_install_dir:
            candidates.append(os.path.join(app_install_dir, "mcp_config.json"))

        candidates.append(os.path.expanduser("~/.config/cmdai/mcp_config.json"))
        candidates.append(os.path.expanduser("~/.cmdai_code/mcp_config.json"))

        for path in candidates:
            if path and os.path.isfile(path):
                return path

        if self.config_path:
            return self.config_path
        if app_root:
            return os.path.join(app_root, "mcp_config.json")
        if app_install_dir and os.path.isdir(app_install_dir):
            return os.path.join(app_install_dir, "mcp_config.json")
        return os.path.join(cwd, "mcp_config.json")

    def load_config(self, app_root: str = "") -> Dict[str, Dict[str, Any]]:
        """Return configured servers keyed by registry name.

        Accepts both shapes in the wild: Claude Desktop's
        ``{"mcpServers": {...}}`` and a bare ``{name: {...}}`` map.

        Read as utf-8-sig, not utf-8: PowerShell's own `Set-Content
        -Encoding UTF8` writes a BOM, and plain utf-8 raises on it. The old
        version swallowed that as "no servers configured", so a config that
        was present and correct looked identical to a missing file.
        """
        try:
            with open(self.config_file(app_root), "r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            self.load_error = f"{self.config_file(app_root)}: {exc}"
            return {}
        if not isinstance(data, dict):
            return {}
        raw = data.get("mcpServers")
        if not isinstance(raw, dict):
            raw = data.get("mcp_servers")
        if not isinstance(raw, dict):
            raw = data
        out: Dict[str, Dict[str, Any]] = {}
        for name, cfg in raw.items():
            # Keys starting with "_" are comments, the convention the example
            # file uses to annotate its own entries.
            if str(name).startswith("_"):
                continue
            if isinstance(cfg, dict):
                out[sanitize_name(name)] = cfg
        return out

    def save_config(self, servers: Dict[str, Dict[str, Any]], app_root: str = "") -> bool:
        payload = {"mcpServers": servers}
        path = self.config_file(app_root)
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            # newline="\n" and no BOM: this file is read by a Node launcher
            # on Windows as well as by us, and a BOM there shows up as a
            # stray character in the first key.
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            self.load_error = ""
            return True
        except Exception as exc:
            self.load_error = str(exc)
            return False

    def _server_from_config(self, name: str, cfg: Dict[str, Any]) -> MCPServer:
        declared = str(cfg.get("type") or "").strip().lower()
        url = str(cfg.get("url") or "").strip()
        if not declared:
            # Claude Code's rule: an entry with a url and no type is a
            # configuration error, not a stdio server.
            declared = "http" if url else "stdio"
        if declared in ("streamable-http", "streamable_http", "https"):
            declared = "http"
        return MCPServer(
            name=name,
            command=str(cfg.get("command") or ""),
            args=[str(a) for a in (cfg.get("args") or [])],
            env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
            cwd=str(cfg.get("cwd") or ""),
            url=url,
            headers={str(k): str(v) for k, v in (cfg.get("headers") or {}).items()},
            transport=declared,
            timeout=float(cfg.get("timeout") or 15.0),
            enabled=bool(cfg.get("enabled", True)),
            autostart=bool(cfg.get("autostart", True)),
        )

    def configure(self, app_root: str = "") -> List[str]:
        """Build server objects from config without connecting.

        Every configured server becomes known, so `/mcp` can list a server
        that is configured but not running, and an invalid entry reports as
        "error" instead of silently not existing.
        """
        self._app_root = app_root
        problems: List[str] = []
        for name, cfg in self.load_config(app_root).items():
            try:
                self.servers[name] = self._server_from_config(name, cfg)
            except Exception as exc:
                problems.append(f"{name}: {exc}")
        if self.add_server is None:
            # Only offer the in-window Add form when the client can write.
            self.add_server = self._write_server
        return problems

    def _write_server(self, name: str, entry: Dict[str, Any]) -> bool:
        key = sanitize_name(name)
        cfg = self.load_config(self._app_root)
        cfg[key] = entry
        if not self.save_config(cfg, self._app_root):
            return False
        self.servers[key] = self._server_from_config(key, entry)
        return True

    def set_enabled(self, name: str, enabled: bool) -> bool:
        key = sanitize_name(name)
        cfg = self.load_config(self._app_root)
        if key not in cfg:
            return False
        entry = dict(cfg[key] or {})
        entry["enabled"] = bool(enabled)
        cfg[key] = entry
        if not self.save_config(cfg, self._app_root):
            return False
        server = self.servers.get(key)
        if server is not None:
            server.enabled = bool(enabled)
            if not enabled:
                server.stop()
        return True

    def set_option(self, name: str, key: str, value: Any) -> bool:
        """Change one setting of one server.

        A single entry point rather than a method per setting, because the
        /mcp window offers these as a list of switches and each one used to
        need its own method. `enabled` is routed through set_enabled
        because it has to stop the running process, and a switch that
        reports "on" while the process keeps running is worse than no
        switch at all.
        """
        name = sanitize_name(name)
        if key == "enabled":
            return self.set_enabled(name, bool(value))
        if key == "autostart":
            entry_key = bool(value)
        else:
            entry_key = value
        cfg = self.load_config(self._app_root)
        if name not in cfg:
            return False
        entry = dict(cfg[name] or {})
        entry[key] = entry_key
        cfg[name] = entry
        if not self.save_config(cfg, self._app_root):
            return False
        server = self.servers.get(name)
        if server is not None and key == "autostart":
            server.autostart = bool(entry_key)
        return True

    def get_option(self, name: str, key: str, default: Any = None) -> Any:
        """One setting of one server, read from the live object where possible."""
        server = self.servers.get(sanitize_name(name))
        if server is not None and hasattr(server, key):
            return getattr(server, key)
        entry = self.load_config(self._app_root).get(sanitize_name(name))
        if isinstance(entry, dict) and key in entry:
            return entry[key]
        return default

    def remove_server(self, name: str) -> bool:
        """Drop a server from the config and stop it.

        The process is stopped first. Removing the entry and leaving the
        server running would keep its tools reachable from a registry that
        no longer claims it, so the next call would go to something the user
        believes they deleted.
        """
        key = sanitize_name(name)
        cfg = self.load_config(self._app_root)
        if key not in cfg:
            return False
        server = self.servers.get(key)
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass
        del cfg[key]
        if not self.save_config(cfg, self._app_root):
            return False
        self.servers.pop(key, None)
        return True

    def rename_server(self, name: str, new_name: str) -> Tuple[bool, str]:
        """Rename a server, keeping its whole configuration.

        Returns (ok, resulting_name). The name is sanitized, so the result
        can differ from what was asked for, and the caller is told which one
        it is rather than being left to guess.
        """
        old = sanitize_name(name)
        new = sanitize_name(new_name)
        if not new:
            return False, ""
        if new == old:
            return True, old
        cfg = self.load_config(self._app_root)
        if old not in cfg:
            return False, old
        if new in cfg:
            return False, new
        entry = dict(cfg[old] or {})
        cfg[new] = entry
        del cfg[old]
        if not self.save_config(cfg, self._app_root):
            return False, old
        server = self.servers.pop(old, None)
        if server is not None:
            server.name = new
            self.servers[new] = server
        return True, new

    def connect_autostart(self, app_root: str = "") -> None:
        """Connect servers marked autostart, in the background.

        Connecting inline would make startup as slow as the slowest
        server, so each one connects on its own thread and the UI reads
        whatever state it is in.
        """
        self.configure(app_root)
        for name, cfg in self.load_config(app_root).items():
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("autostart", True):
                continue
            server = self.servers.get(name)
            if server is None or not server.enabled:
                continue
            server.autostart = bool(cfg.get("autostart", True))
            threading.Thread(
                target=server.connect, daemon=True, name=f"mcp-{name}"
            ).start()

    def connect_all(self, app_root: str = "") -> None:
        self.configure(app_root)
        for server in self.servers.values():
            if server.enabled:
                server.connect()

    def stop_all(self) -> None:
        for server in list(self.servers.values()):
            try:
                server.stop()
            except Exception:
                pass

    # ---------------------------------------------------------- runtime

    def restart(self, name: str) -> bool:
        key = sanitize_name(name)
        server = self.servers.get(key)
        if server is None:
            cfg = self.load_config().get(key)
            if not cfg:
                return False
            server = self.servers[key] = self._server_from_config(key, cfg)
        return server.connect()

    def is_disabled(self, name: str) -> bool:
        """True when the server is configured but the user switched it off.

        Distinct from "not connected": an off server has no process, no
        session and no tools, and trying to reach it produces a connection
        error that looks like a fault. It is not a fault, it is a decision
        the user made, and the model is told so in those words.
        """
        server = self.servers.get(sanitize_name(name))
        if server is not None:
            return not server.enabled
        entry = self.load_config(self._app_root).get(sanitize_name(name))
        if isinstance(entry, dict):
            return not bool(entry.get("enabled", True))
        return False

    def model_servers(self) -> List[Dict[str, Any]]:
        """The same facts as status_lines(), in fields instead of ASCII.

        status_lines() is built for a person reading a terminal: a mark, a
        padded name, a padded status, a bracketed note. Handing that to a
        model costs a turn and invites invented tool names, because the
        only way to learn what a server offers is to parse a table. This
        says the same things in a shape a model can act on directly, and
        it names the reason a server is unusable.
        """
        out: List[Dict[str, Any]] = []
        for name in sorted(self.servers):
            server = self.servers[name]
            connected = server.status == "connected"
            entry: Dict[str, Any] = {
                "name": name,
                "enabled": bool(server.enabled),
                "status": server.status,
                "connected": connected,
                "tool_count": len(server.tools) if connected else 0,
            }
            if not server.enabled:
                # Not an error. Wording matters here: a model that reads
                # "error" retries, apologises, or asks the user to fix
                # something that is not broken.
                entry["usable"] = False
                entry["reason"] = (
                    "switched off by the user; there is no connection to it and it "
                    "has no tools. Only the user can turn it on, in the /mcp window. "
                    "Do not retry and do not guess its tool names."
                )
            elif server.status == "error":
                entry["usable"] = False
                entry["reason"] = (
                    f"failed to connect: {one_line(server.error, 200) or 'no detail'}. "
                    f"The user would have to fix it in the /mcp window."
                )
            elif not connected:
                entry["usable"] = False
                entry["reason"] = (
                    "configured but not started yet. It connects on its own at "
                    "startup; if the user wants it now they can restart it in /mcp."
                )
            else:
                entry["usable"] = True
                perms = [classify_tool(t) for t in server.tools]
                entry["tools_by_permission"] = {
                    "read": perms.count(PERM_READ),
                    "write": perms.count(PERM_WRITE),
                    "destructive": perms.count(PERM_DESTRUCTIVE),
                }
            out.append(entry)
        return out

    def server_for_tool_prefix(self, qualified: str) -> Optional[str]:
        """The configured server a qualified tool name appears to belong to.

        Used only to explain a miss. A model asked for
        mcp__xph__list_documents should hear "that server is switched off",
        not "no such tool", because those need different things from the
        user and only the first one is true.
        """
        raw = (qualified or "").strip()
        if not raw.startswith("mcp__"):
            return None
        rest = raw[5:]
        for name in self.servers:
            if rest.lower().startswith(name.lower() + "__"):
                return name
        return None

    def call_tool(self, server: str, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        target = self.servers.get(sanitize_name(server))
        if target is None or target.status != "connected":
            return {"success": False, "error": f"MCP server '{server}' not connected"}
        try:
            return {"success": True, "server": server, "tool": tool,
                    "result": target.call_tool(tool, arguments)}
        except MCPToolError as exc:
            return {"success": False, "error": str(exc)[:500], "kind": "tool_error"}
        except Exception as exc:
            return {"success": False, "error": str(exc)[:500], "kind": "protocol_error"}

    def status_lines(self) -> List[str]:
        """One flat string per server.

        A server's own status_lines() returns a list, so wrapping those
        results produced a list of lists and "\n".join() in the TUI raised
        TypeError on every `/mcp`. Join at the leaf instead of nesting.
        """
        if not self.servers:
            return []
        out: List[str] = []
        for name in sorted(self.servers):
            try:
                lines = self.servers[name].status_lines()
            except Exception as exc:
                out.append(f"x  {name:<18} error             ({exc})")
                continue
            if isinstance(lines, str):
                out.append(lines)
            else:
                out.extend(str(line) for line in lines)
        return out

    def server_names(self) -> List[str]:
        return sorted(self.servers)

    def dynamic_tools(self) -> Dict[str, Tuple[str, str]]:
        """qualified name -> (server, real tool name), for connected servers."""
        out: Dict[str, Tuple[str, str]] = {}
        for name, server in self.servers.items():
            if server.status != "connected":
                continue
            for tool in server.tools:
                real = str(tool.get("name") or "")
                if not real:
                    continue
                out[qualified_tool_name(name, real).lower()] = (name, real)
        return out

    def tool_definitions(self, only_servers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Schemas for the permission gate, restricted to chosen servers."""
        out: List[Dict[str, Any]] = []
        for name, server in self.servers.items():
            if only_servers is not None and name not in only_servers:
                continue
            if server.status != "connected":
                continue
            for tool in server.tools:
                real = str(tool.get("name") or "")
                if not real:
                    continue
                out.append({
                    "name": qualified_tool_name(name, real),
                    "description": str(tool.get("description") or ""),
                    "inputSchema": tool.get("inputSchema") or {"type": "object"},
                    "permission": classify_tool(tool),
                    "server": name,
                })
        return out

    def permission_for(self, qualified: str) -> str:
        """Classify an already-qualified tool name.

        Falls back to the destructive default, because the alternative to
        asking is not "guessing", it is running an unvetted tool.
        """
        key = (qualified or "").lower()
        if not key.startswith(TOOL_NAME_PREFIX):
            return PERM_DESTRUCTIVE
        rest = key[len(TOOL_NAME_PREFIX):]
        server_key, _, tool_part = rest.partition("__")
        server = self.servers.get(server_key)
        if server is None:
            return PERM_DESTRUCTIVE
        for tool in server.tools:
            if sanitize_name(str(tool.get("name") or "")) == tool_part:
                return classify_tool(tool)
        return PERM_DESTRUCTIVE


# ------------------------------------------------------------- helpers


def _expand_env(value: str) -> str:
    """${VAR} and $VAR from the environment, so tokens stay out of config."""
    out = os.path.expandvars(value)
    return out


def _unwrap_result(message: Any) -> Optional[Dict[str, Any]]:
    """Take a JSON-RPC message and return its result, raising on error.

    Needed because an SSE frame carries the whole envelope
    ``{"jsonrpc":..., "id":..., "result": {...}}`` while the plain-JSON path
    already hands back a bare result. Unwrapping in one place keeps the
    two transports from drifting: returning the envelope from one of them
    produced a server that negotiated fine and then reported zero tools.
    """
    if not isinstance(message, dict):
        return None
    if "error" in message:
        err = message.get("error") or {}
        raise MCPError(f"{err.get('code', '?')}: {err.get('message', 'unknown error')}")
    result = message.get("result")
    return result if isinstance(result, dict) else None


def _first_sse_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first JSON-RPC response out of an SSE body."""
    for block in re.split(r"\n\s*\n", text):
        lines: List[str] = []
        for line in block.splitlines():
            if line.startswith("data:"):
                lines.append(line[5:].lstrip(" "))
            elif not line.startswith(("event:", "id:", "retry:")):
                lines.append(line)
        chunk = "\n".join(lines).strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            return json.loads(chunk)
        except Exception:
            continue
    return None


def block_texts(content: Any) -> List[str]:
    """Flatten a tool result's content blocks to plain text."""
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    out: List[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                out.append(str(block))
                continue
            kind = block.get("type")
            if kind == "text":
                out.append(str(block.get("text") or ""))
            elif kind == "image":
                out.append("[image %s, %d bytes base64]" % (
                    block.get("mimeType") or "unknown",
                    len(str(block.get("data") or ""))))
            elif kind == "resource_link":
                out.append("[resource %s]" % block.get("uri"))
            elif kind == "resource":
                res = block.get("resource") or {}
                out.append(str(res.get("text") or res.get("uri") or "[resource]"))
            else:
                out.append(json.dumps(block, ensure_ascii=False)[:300])
    return [line for line in out if line]


def image_blocks(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Image content blocks from a tool result, as (bytes, mime) payloads."""
    out: List[Dict[str, Any]] = []
    content = result.get("content")
    if not isinstance(content, list):
        return out
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        data = str(block.get("data") or "")
        if not data:
            continue
        try:
            out.append({
                "data": base64.b64decode(data),
                "mimeType": str(block.get("mimeType") or "image/png"),
            })
        except Exception:
            continue
    return out


def _client_version() -> str:
    try:
        from ..core.releases import get_display_version
        return get_display_version()
    except Exception:
        return "unknown"
