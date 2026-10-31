import json
import os
import re
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..mcp.client import (
    PERM_DESTRUCTIVE,
    PERM_READ,
    PERM_WRITE,
    TOOL_NAME_PREFIX,
    block_texts,
    image_blocks,
)
from .render import render_command_output, render_edit_diff_opencode, render_tool_header
from .tools import (
    AgentContext,
    tool_command,
    tool_edit,
    tool_glob,
    tool_ls,
    tool_read,
    tool_scratch,
    tool_screenshot,
    tool_search,
    tool_vision,
    tool_web,
    tool_write,
    tool_bugs,
    tool_code_search,
)


def _parse_json_body(body: str, tool_name: str) -> Dict[str, Any]:
    """Parse an MCP tool's JSON argument object.

    Returns flat key/value pairs that fit the rest of the dispatch, which is
    typed as Dict[str, str]. A malformed body is reported back to the model
    as a readable sentence naming the tool, because a generic "invalid call"
    is the kind of message a small local model retries the exact same way
    twice before trying anything else.

    Nested values are kept as JSON text rather than flattened away: a server
    that expects {"filter": {"a": 1}} must receive that structure, and the
    client re-parses before sending.
    """
    text = (body or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception as exc:
        return {"__json_error__": f"Arguments for {tool_name} are not valid JSON: {exc}"}
    if not isinstance(data, dict):
        return {"__json_error__": (
            f"Arguments for {tool_name} must be a JSON object with named fields, "
            f"got {type(data).__name__}.")}
    out: Dict[str, Any] = {}
    for key, value in data.items():
        out[str(key)] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return out


def _all_tools_failed(events: List[Any]) -> bool:
    """True when a subagent ran real work and every single call errored.

    agent_note and scratch are bookkeeping, not evidence: a subagent that only
    wrote notes and had every real tool fail still got nowhere. Requiring one
    non-bookkeeping event keeps that case out of the "everything broke" verdict
    while still catching a run where the workspace was never actually read.
    """
    real = [e for e in (events or [])
            if isinstance(e, dict)
            and str(e.get("tool", "")) not in ("agent_note", "scratch", "todo", "tasks")]
    if not real:
        return False
    return all(not e.get("ok", False) for e in real)


def _parse_only(text: str) -> List[Tuple[str, Any]]:
    """Parse a model turn into ordered ("text"|"tool", payload) segments.

    Module-level so the subagent orchestrator and the TUI window can share the
    one parser. The orchestrator used to keep its own copy of the patterns and
    it drifted: it only knew <tool:name>, so <|tool_call>call:ls path="." />
    from a local Gemma was never seen as a call.
    """
    # Parsing never touches instance state - no engine, no context, no
    # callbacks - so one bare runner serves every caller. Built here rather
    # than at import time because the class is not defined yet at the top.
    if _BARE_RUNNER is None:
        globals()["_BARE_RUNNER"] = AgentRunner.__new__(AgentRunner)
    return _BARE_RUNNER.parse_chronological_segments(text)


_BARE_RUNNER: Optional["AgentRunner"] = None


class AgentRunner:

    def __init__(
        self,
        ctx: AgentContext,
        on_tool_start: Optional[Callable[[str, str], None]] = None,
        on_tool_finish: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        on_todo_updated: Optional[Callable[[List[str]], None]] = None,
        ask_handler: Optional[Callable[[str, List[str]], str]] = None,
        mcp_client: Any = None,
        vision_capable: Optional[Callable[[], bool]] = None,
    ):
        self.ctx = ctx
        self.workdir = getattr(ctx, "workdir", ".")
        self.on_tool_start = on_tool_start
        self.on_tool_finish = on_tool_finish
        self.on_todo_updated = on_todo_updated
        self.ask_handler = ask_handler
        self.mcp = mcp_client
        # Optional hook telling us whether the loaded model can accept images.
        # Defaults to False so an image from a tool is never silently attached
        # to a text-only model - the caller gets told instead.
        self.vision_capable = vision_capable
        # Permission gate state per server: "closed" -> "open" -> "write_granted".
        # Replaces a single boolean that either unlocked everything or nothing.
        self._mcp_state: Dict[str, str] = {}
        self.provider_id = "local_gguf"
        self.detach_requested: bool = False

    def request_background(self) -> bool:
        self.detach_requested = True
        return True

    # ------------------------------------------------------------- MCP gate

    def _mcp_state_of(self, server: str) -> str:
        return self._mcp_state.get(server, "closed")

    def _mcp_set_state(self, server: str, state: str) -> None:
        self._mcp_state[server] = state

    def _mcp_list_servers(self) -> Dict[str, Any]:
        """`<tool:mcp />` - always visible to the model, lists what exists.

        Sent as fields, not as the ASCII table `cmdai mcp list` prints. The
        table is for a person; a model handed it spends a turn working out
        that "-  xph  connected  10 tools" means a server with ten tools,
        and a model that gets this wrong tends to invent tool names.
        """
        if not self.mcp:
            return {"success": False, "servers": [],
                    "error": ("MCP is not configured, so there is nothing to list. "
                              "The user can add a server with `cmdai mcp add`. "
                              "Do not retry this tool and do not invent a server "
                              "name."),
                    "kind": "mcp_not_configured"}
        servers = self.mcp.model_servers()
        if not servers:
            # success=False on purpose. An empty list returned success=True, so
            # the tool card rendered blank and the failure looked like nothing
            # happening. There is no MCP here, and that is an error worth
            # saying out loud.
            return {"success": False, "servers": [],
                    "error": ("No MCP servers are configured, so no MCP tool can "
                              "be called. The user can add one with "
                              "`cmdai mcp add`. Do not retry and do not invent a "
                              "server or tool name."),
                    "kind": "mcp_no_servers"}
        usable = [s["name"] for s in servers if s.get("usable")]
        off = [s["name"] for s in servers if not s.get("enabled")]
        note = ("Call <tool:mcp open server=\"NAME\" /> for a server with usable=true. "
                "It returns that server's tools with their exact call syntax. "
                "Do not guess tool names.")
        if off:
            # Naming the switched-off servers here means the model can say
            # "xph is switched off" instead of reporting a missing tool,
            # which is the difference between a useful message and a wrong
            # one that sends the user looking for a typo.
            note += (" Servers with enabled=false are switched off by the user: "
                     + ", ".join(off) + ". They have no connection and no tools, "
                     "so do not call them and do not offer to fix them - the user "
                     "knows they are off.")
        if not usable:
            note += (" No server is currently usable, so no MCP tool can be "
                     "called at all this turn. Say that plainly instead of "
                     "pretending a tool failed.")
        return {"success": True, "servers": servers, "note": note}

    def _mcp_open_server(self, server_key: str) -> Dict[str, Any]:
        """`<tool:mcp open server="..." />` - the only unlock step.

        Tool schemas are returned as the result of this call, not injected
        into the system prompt. The prompt prefix therefore stays byte-identical
        between turns, which is what keeps llama.cpp's prompt cache working.
        """
        if not self.mcp:
            return {"success": False, "error": "MCP is not configured."}
        key = (server_key or "").strip().lower()
        target = self.mcp.servers.get(key)
        if target is None:
            known = ", ".join(self.mcp.server_names()) or "(none)"
            return {"success": False,
                    "error": f"No MCP server named '{key}'. Available: {known}"}
        # Switched off is not a fault and must not read like one. The
        # distinction decides what the model does next: a connection error
        # invites a retry, an off switch invites telling the user, and
        # there is nothing to retry here.
        if not target.enabled:
            return {"success": False, "error": (
                f"MCP server '{key}' is switched off in the user's configuration. "
                f"There is no connection to it and it has no tools, so nothing can "
                f"be called on it. The user has to turn it on themselves, in the "
                f"/mcp window. Do not retry and do not invent tool names for it."),
                "kind": "server_disabled", "server": key}
        if target.status != "connected":
            return {"success": False,
                    "error": f"MCP server '{key}' is not connected ({target.status})."
                             f" {target.error or ''}".strip(),
                    "kind": "server_not_connected", "server": key}

        defs = self.mcp.tool_definitions(only_servers=[key])
        if not defs:
            return {"success": False, "error": f"MCP server '{key}' exposed no tools."}

        self._mcp_set_state(key, "open")
        lines = [f"MCP server '{key}' is open. {len(defs)} tools available.", ""]
        for d in defs:
            schema = d.get("inputSchema") or {}
            required = schema.get("required") or []
            props = list((schema.get("properties") or {}).keys())
            arg_doc = ", ".join(props) if props else "none"
            lines.append(f"- {d['name']}  [perm: {d['permission']}]")
            if d.get("description"):
                lines.append(f"    {d['description']}")
            lines.append(f"    args: {arg_doc}; required: {', '.join(required) or 'none'}")
            lines.append("    call: <tool:%s>{\"arg\": \"value\"}</tool:%s>" % (d["name"], d["name"]))
        lines.append("")
        lines.append("Send arguments as a JSON object between the tags. "
                     "Write tools need the user's approval; you do not get to grant it yourself.")
        return {"success": True, "server": key, "tools": [d["name"] for d in defs],
                "schema_text": "\n".join(lines)}

    def _mcp_resolve(self, qualified: str) -> Optional[Tuple[str, str, str]]:
        """qualified name -> (server, real tool name, permission)."""
        if not self.mcp:
            return None
        try:
            tools = self.mcp.dynamic_tools()
        except Exception:
            return None
        found = tools.get((qualified or "").lower())
        if not found:
            return None
        server, real = found
        return server, real, self.mcp.permission_for(qualified)

    def _mcp_miss(self, name: str) -> Dict[str, Any]:
        """Explain a tool name that did not resolve, as precisely as we can.

        "No such tool" is true and useless when the real reason is that the
        server is switched off. Those two ask different things of the user -
        one asks them to check a spelling, the other tells them nothing to
        do - and only one of them is a real answer. A model that reports
        the wrong one sends the user off to fix something that is not
        broken.
        """
        base = {
            "success": False,
            "server": "",
            "tool": name,
        }
        if not self.mcp:
            base["error"] = "MCP is not configured, so no tool can be called."
            return base

        owner = self.mcp.server_for_tool_prefix(name)
        if owner and self.mcp.is_disabled(owner):
            base["server"] = owner
            base["error"] = (
                f"'{name}' belongs to MCP server '{owner}', which is switched off. "
                f"There is no connection to that server and it exposes no tools right "
                f"now, so this call cannot run. The user would have to switch the server "
                f"on themselves, in the /mcp window. Do not retry and do not try a "
                f"similar name."
            )
            return base

        if owner:
            base["server"] = owner
            state = self._mcp_state_of(owner)
            base["error"] = (
                f"MCP server '{owner}' is configured but has not been opened this "
                f"session (state: {state}), so its tool names are unknown to you. Call "
                f"<tool:mcp open server=\"{owner}\" /> and use the name it returns. "
                f"Do not guess it."
            )
            return base

        base["error"] = (
            f"No connected MCP tool named '{name}'. Call <tool:mcp /> to list servers, "
            f"then <tool:mcp open server=\"NAME\" /> for the exact tool names. Do not "
            f"guess them."
        )
        return base

    def _mcp_gate(self, server: str, tool: str, permission: str) -> Tuple[bool, str]:
        """Decide whether a call may proceed. Returns (allowed, reason)."""
        if permission == PERM_READ:
            return True, ""

        if permission == PERM_DESTRUCTIVE:
            # Asked every time, and answering yes does not grant anything:
            # deleting something cannot be undone, so no standing consent.
            if not self.ask_handler:
                return False, ("This tool is marked destructive and no confirmation "
                               "handler is available. Ask the user to run it themselves.")
            answer = self.ask_handler(
                f"MCP tool '{tool}' on server '{server}' is marked destructive. "
                f"This cannot be undone. Run it?", ["Yes", "No"])
            if str(answer).strip().lower().startswith("yes"):
                return True, ""
            return False, "User declined a destructive MCP call."

        # PERM_WRITE
        if self._mcp_state_of(server) == "write_granted":
            return True, ""
        if not self.ask_handler:
            return False, ("This tool writes, and no confirmation handler is available. "
                           "Ask the user to allow it first.")
        answer = self.ask_handler(
            f"MCP tool '{tool}' on server '{server}' writes data. "
            f"Allow it?", ["Yes", "Always", "No"])
        lowered = str(answer).strip().lower()
        if lowered.startswith("always"):
            self._mcp_set_state(server, "write_granted")
            return True, "granted"
        if lowered.startswith("yes"):
            return True, ""
        return False, "User declined the write."

    def _mcp_legacy_status(self) -> Dict[str, Any]:
        return self._mcp_list_servers()

    def _validate_mcp_args(self, server: str, tool: str,
                           args: Dict[str, Any]) -> Optional[str]:
        """Check arguments against the tool's own inputSchema.

        Cheap, and it happens before the call rather than after, so a
        malformed request does not reach a server that might act on half of
        it. Returns a human-readable problem, or None when fine.
        """
        schema = None
        for candidate in self.mcp.servers[server].tools:
            if str(candidate.get("name") or "") == tool:
                schema = candidate.get("inputSchema")
                break
        if not isinstance(schema, dict):
            return None
        try:
            import jsonschema  # already a project dependency
        except Exception:
            return None  # validation is a bonus, not a hard gate
        try:
            jsonschema.validate(instance=dict(args), schema=schema)
        except jsonschema.ValidationError as exc:
            where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            return f"Invalid arguments for {tool}: at '{where}', {exc.message}"
        except Exception:
            return None
        return None

    def _execute_mcp_tool(self, name: str, args: Dict[str, Any],
                          notify: bool) -> Dict[str, Any]:
        """Run one MCP tool, behind the permission gate."""
        if notify and self.on_tool_start:
            self.on_tool_start(name, "")

        def finish(res: Dict[str, Any], target: str = "") -> Dict[str, Any]:
            if notify and self.on_tool_finish:
                self.on_tool_finish(name, target, res)
            return res

        json_error = str(args.pop("__json_error__", "") or "")
        if json_error:
            return finish({"success": False, "error": json_error})

        resolved = self._mcp_resolve(name)
        if resolved is None:
            return finish(self._mcp_miss(name))
        server, real_tool, permission = resolved

        # Checked here, not only during resolution. A server can be
        # switched off between the tool name being resolved and the call
        # going out - the user presses a key in another window - and the
        # resolution table is a snapshot. Without this the call would run
        # against a server the user has just turned off, which is the one
        # outcome they were trying to prevent.
        if self.mcp.is_disabled(server):
            return finish({
                "success": False,
                "server": server,
                "tool": real_tool,
                "error": (
                    f"MCP server '{server}' is switched off, so there is no connection "
                    f"to it and '{real_tool}' cannot run. The user switched it off; do "
                    f"not retry and do not look for a different tool name."
                ),
                "kind": "server_disabled",
            })

        # Plan mode is read-only. An MCP write would be a change to the world
        # made behind the gate's back: the turn would be "planning" while the
        # server wrote something. Refused here, before the permission prompt,
        # because asking would already make it look allowed.
        if getattr(self.ctx, "mode", "auto") == "plan" and permission != PERM_READ:
            return finish({
                "success": False,
                "server": server,
                "tool": real_tool,
                "error": (
                    f"PLAN MODE ACTIVE: MCP tool '{real_tool}' on '{server}' is marked "
                    f"'{permission}', so it cannot run while planning. Plan mode only "
                    f"reads; write the action into the plan as a step instead."
                ),
            })

        # "open" and "write_granted" both count as open. Testing for the
        # literal "open" here would lock the server out again the moment the
        # user granted write access, which is the opposite of what granting
        # is supposed to do.
        if self._mcp_state_of(server) == "closed":
            target = self.mcp.servers.get(server)
            if target and target.status == "connected":
                self._mcp_set_state(server, "open")
            else:
                return finish({
                    "success": False,
                    "error": f"Server '{server}' is not open yet. Call "
                             f"<tool:mcp open server=\"{server}\" /> first and use the tool "
                             f"name it returns.",
                })

        allowed, reason = self._mcp_gate(server, real_tool, permission)
        if not allowed:
            return finish({"success": False, "error": reason})

        problem = self._validate_mcp_args(server, real_tool, args)
        if problem:
            # One retry is worth the round trip: the message names the field
            # and says what is wrong, which small local models act on well.
            return finish({"success": False, "error": problem})

        try:
            out = self.mcp.call_tool(server, real_tool, args)
        except Exception as exc:
            return finish({"success": False, "error": str(exc)[:500]})

        result = (out or {}).get("result") or {}
        texts = block_texts(result.get("content"))
        images = image_blocks(result)
        structured = result.get("structuredContent")

        if images and not (self.vision_capable and self.vision_capable()):
            # Say so. Dropping the image silently leaves the model claiming it
            # saw something it never received.
            texts.insert(0, (
                f"[{len(images)} image(s) returned by this tool, but the loaded model "
                f"is not vision-capable, so they were not attached. Switch to a "
                f"multimodal model to use them."
            ))

        payload: Dict[str, Any] = {"success": True, "server": server, "tool": real_tool}
        if texts:
            payload["text"] = "\n".join(texts)
        if structured is not None:
            payload["structured"] = structured
        if images and self.vision_capable and self.vision_capable():
            saved = self._queue_mcp_images(images, real_tool)
            if saved:
                payload["images"] = saved
                payload["text"] = (payload.get("text", "") +
                                   f"\n{len(saved)} image(s) from this tool were saved and "
                                   f"will be attached to the next request: "
                                   f"{', '.join(saved)}").strip()
            else:
                payload["text"] = (payload.get("text", "") +
                                   "\nImages were returned but could not be written to "
                                   "disk, so they were not attached.").strip()
        return finish(payload)

    def _queue_mcp_images(self, images: List[Dict[str, Any]], tool: str) -> List[str]:
        """Write tool-returned images to disk and queue them for the next turn.

        Reuses the same pending_images queue as the vision tool, so images
        from an MCP server and images from a screenshot take one path to the
        request instead of two that can drift apart.
        """
        saved: List[str] = []
        for index, item in enumerate(images):
            try:
                directory = os.path.join(tempfile.gettempdir(), "cmdaicode-mcp")
                os.makedirs(directory, exist_ok=True)
                ext = ".png"
                mime = str(item.get("mimeType") or "")
                if "jpeg" in mime or "jpg" in mime:
                    ext = ".jpg"
                elif "webp" in mime:
                    ext = ".webp"
                path = os.path.join(
                    directory, f"mcp_{re.sub(r'[^a-z0-9]+', '_', tool.lower())}_{index}{ext}")
                with open(path, "wb") as handle:
                    handle.write(item["data"])
                saved.append(path)
            except Exception:
                continue
        for path in saved:
            try:
                self.ctx.pending_images.append({"path": path, "question": ""})
            except Exception:
                pass
        return saved

    def parse_chronological_segments(self, text: str) -> List[Tuple[str, Any]]:
        if not text:
            return []

        def _parse_attrs(attrs_str: str) -> Dict[str, str]:
            if not attrs_str:
                return {}
            matches = re.findall(r'(\w+)=(?:["\'](.*?)["\']|([^\s>]+))', attrs_str, re.DOTALL)
            attrs = {}
            for k, v1, v2 in matches:
                attrs[k.lower()] = v1 if (v1 is not None and v1 != "") else v2
            return attrs

        def _normalize_attrs(t_name: str, a_dict: Dict[str, str], b_text: str = "") -> None:
            for p_alias in ("targetfile", "target_file", "filepath", "file_path", "filename", "file_name", "absolutepath", "absolute_path", "dest", "destination", "file", "target"):
                if p_alias in a_dict and "path" not in a_dict:
                    a_dict["path"] = a_dict[p_alias]
            for c_alias in ("codecontent", "code_content", "code", "replacementcontent", "replacement_content", "new_content"):
                if c_alias in a_dict and "content" not in a_dict:
                    a_dict["content"] = a_dict[c_alias]
            for o_alias in ("targetcontent", "target_content", "old_str", "old_content", "search", "original"):
                if o_alias in a_dict and "old" not in a_dict:
                    a_dict["old"] = a_dict[o_alias]
            for n_alias in ("replacementcontent", "replacement_content", "new_str", "new_content", "replace"):
                if n_alias in a_dict and "new" not in a_dict:
                    a_dict["new"] = a_dict[n_alias]
            for cmd_alias in ("commandline", "command_line", "command", "script", "exec"):
                if cmd_alias in a_dict and "cmd" not in a_dict:
                    a_dict["cmd"] = a_dict[cmd_alias]

            if t_name in ("write", "edit", "read") and not a_dict.get("path") and b_text:
                p_m = re.search(r'<(?:path|file|filename|target|target_file|targetfile|dest)>([^<>\r\n]+)</(?:path|file|filename|target|target_file|targetfile|dest)>', b_text, re.IGNORECASE)
                if p_m:
                    a_dict["path"] = p_m.group(1).strip()
                    b_text = re.sub(r'<(?:path|file|filename|target|target_file|targetfile|dest)>[^<>\r\n]+</(?:path|file|filename|target|target_file|targetfile|dest)>', '', b_text, flags=re.IGNORECASE).strip()
                else:
                    lines = [l.strip() for l in b_text.splitlines() if l.strip()]
                    if lines and "." in lines[0] and len(lines[0]) < 100 and not any(k in lines[0] for k in ("<", "{", "(", "import", "def", "class", "=", " ", "\t")):
                        a_dict["path"] = lines[0]
                        b_text = "\n".join(b_text.splitlines()[1:]).strip()

            def _strip_code_fence(s: str, target_path: str) -> str:
                if not s:
                    return s
                if str(target_path or "").lower().endswith((".md", ".markdown", ".mdx")):
                    return s
                st = s.strip()
                if st.startswith("```"):
                    first_nl = st.find("\n")
                    if first_nl != -1:
                        inner = st[first_nl + 1:]
                        if inner.rstrip().endswith("```"):
                            inner = inner.rstrip()[:-3].rstrip("\r\n")
                        return inner
                return s

            if t_name == "write":
                c_m = re.search(r'<(?:content|code|codecontent)>([\s\S]*?)</(?:content|code|codecontent)>', b_text, re.IGNORECASE)
                if c_m and "content" not in a_dict:
                    a_dict["content"] = c_m.group(1).strip()
                elif "content" not in a_dict and b_text:
                    a_dict["content"] = b_text
                if "content" in a_dict:
                    a_dict["content"] = _strip_code_fence(a_dict["content"], a_dict.get("path", ""))
            elif t_name == "edit":
                old_m = re.search(r'<old>(.*?)</old>', b_text, re.DOTALL | re.IGNORECASE)
                new_m = re.search(r'<new>(.*?)</new>', b_text, re.DOTALL | re.IGNORECASE)
                if old_m:
                    a_dict["old"] = _strip_code_fence(old_m.group(1), a_dict.get("path", ""))
                if new_m:
                    a_dict["new"] = _strip_code_fence(new_m.group(1), a_dict.get("path", ""))
                elif not old_m and "new" not in a_dict and b_text:
                    a_dict["new"] = _strip_code_fence(b_text, a_dict.get("path", ""))
            elif t_name == "command":
                if b_text and "cmd" not in a_dict:
                    a_dict["cmd"] = _strip_code_fence(b_text, "")
            elif t_name in ("scratch", "todo", "tasks"):
                if b_text and "note" not in a_dict:
                    a_dict["note"] = b_text
            elif t_name == "web":
                if "query" not in a_dict and b_text:
                    a_dict["query"] = b_text
            elif t_name == "ask":
                if "question" not in a_dict and b_text:
                    a_dict["question"] = b_text
            elif t_name == "subagent":
                if not a_dict.get("goal") and not a_dict.get("task") and b_text:
                    a_dict["goal"] = b_text

        raw_matches = []

        gemma_pattern = re.compile(
            r'<\|tool_call>call:(\w+)(?:\s+([^>]*?))?(?:\s*/>|>(.*?)</\|tool_call>)',
            re.DOTALL | re.IGNORECASE,
        )
        for match in gemma_pattern.finditer(text):
            tool_name = match.group(1).lower()
            attrs_str = match.group(2) or ""
            body = (match.group(3) or "").strip()
            attrs = _parse_attrs(attrs_str)
            _normalize_attrs(tool_name, attrs, body)
            raw_matches.append((match.start(), match.end(), tool_name, attrs))

        body_pattern = re.compile(
            r'<tool:(\w+)(?:\s+([^>]*?))?\s*/?>(.*?)</(?:tool:)?(?:\1|tool)>',
            re.DOTALL | re.IGNORECASE,
        )
        for match in body_pattern.finditer(text):
            tool_name = match.group(1).lower()
            attrs_str = (match.group(2) or "").rstrip("/").strip()
            body = match.group(3).strip()
            attrs = _parse_attrs(attrs_str)
            _normalize_attrs(tool_name, attrs, body)
            if tool_name.startswith(TOOL_NAME_PREFIX) and body:
                attrs.update(_parse_json_body(body, tool_name))
            raw_matches.append((match.start(), match.end(), tool_name, attrs))

        unclosed_body_pattern = re.compile(
            r'<tool:(write|edit|command)\b(?:\s+([^>]*?))?\s*/?>\s*((?:(?!</?(?:tool:|tool_call|\|tool_call)).)+)$',
            re.DOTALL | re.IGNORECASE,
        )
        for match in unclosed_body_pattern.finditer(text):
            tool_name = match.group(1).lower()
            attrs_str = (match.group(2) or "").rstrip("/").strip()
            body = match.group(3).strip()
            if body:
                attrs = _parse_attrs(attrs_str)
                _normalize_attrs(tool_name, attrs, body)
                raw_matches.append((match.start(), match.end(), tool_name, attrs))

        single_tag_pattern = re.compile(r'<tool:(\w+)(?:\s+([^>]*?))?\s*/?>', re.DOTALL | re.IGNORECASE)
        for match in single_tag_pattern.finditer(text):
            tool_name = match.group(1).lower()
            attrs_str = match.group(2) or ""
            attrs = _parse_attrs(attrs_str)
            _normalize_attrs(tool_name, attrs, "")
            raw_matches.append((match.start(), match.end(), tool_name, attrs))

        tool_call_block = re.compile(
            r'<tool_call>(?:call:)?(\w+)(?:\s+([^>]*?))?(?:\s*/>|>(.*?)</tool_call>)',
            re.DOTALL | re.IGNORECASE,
        )
        for match in tool_call_block.finditer(text):
            tool_name = match.group(1).lower()
            attrs_str = match.group(2) or ""
            body = (match.group(3) or "").strip()
            attrs = _parse_attrs(attrs_str)
            _normalize_attrs(tool_name, attrs, body)
            raw_matches.append((match.start(), match.end(), tool_name, attrs))

        _NORM_NAMES = {
            "create_file": "write", "write_file": "write", "write_to_file": "write", "save_file": "write",
            "replace_file_content": "edit", "edit_file": "edit", "patch_file": "edit",
            "read_file": "read", "view_file": "read", "cat": "read",
            "list_directory": "ls", "list_dir": "ls",
            "run_command": "command", "execute_command": "command", "bash": "command", "sh": "command", "terminal": "command", "cmd": "command",
            "web_search": "web", "search_web": "web", "fetch_web_page": "webfetch", "read_url_content": "webfetch", "read_browser_page": "webfetch",
        }
        dec = json.JSONDecoder()
        scan_pos = 0
        while scan_pos < len(text):
            idx_obj = text.find("{", scan_pos)
            idx_arr = text.find("[", scan_pos)
            candidates = [p for p in (idx_obj, idx_arr) if p != -1]
            if not candidates:
                break
            start_idx = min(candidates)
            try:
                parsed, end_offset = dec.raw_decode(text[start_idx:])
                full_end = start_idx + end_offset
                calls = parsed if isinstance(parsed, list) else [parsed]
                has_tool = False
                for call_obj in calls:
                    if not isinstance(call_obj, dict):
                        continue
                    raw_name = call_obj.get("tool_name") or call_obj.get("name") or call_obj.get("tool") or ""
                    if not raw_name and isinstance(call_obj.get("function"), dict):
                        raw_name = call_obj["function"].get("name", "")
                    elif not raw_name and isinstance(call_obj.get("function"), str):
                        raw_name = call_obj["function"]
                    if not raw_name:
                        continue
                    norm_name = _NORM_NAMES.get(str(raw_name).lower().strip(), str(raw_name).lower().strip())
                    args: Dict[str, str] = {}
                    raw_args = call_obj.get("arguments") or call_obj.get("args") or call_obj.get("parameters")
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except Exception:
                            raw_args = {}
                    if isinstance(raw_args, dict):
                        args.update({str(k).lower(): str(v) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False) for k, v in raw_args.items()})
                    for k, v in call_obj.items():
                        k_low = str(k).lower()
                        if k_low not in ("tool_name", "name", "tool", "tool_call_id", "id", "type", "function", "arguments", "args", "parameters"):
                            if k_low not in args:
                                args[k_low] = str(v) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)

                    # Normalize common parameter aliases
                    for p_alias in ("targetfile", "target_file", "filepath", "file_path", "filename", "file_name", "absolutepath", "absolute_path", "dest", "destination"):
                        if p_alias in args and "path" not in args:
                            args["path"] = args[p_alias]
                    for c_alias in ("codecontent", "code_content", "code", "replacementcontent", "replacement_content", "new_content"):
                        if c_alias in args and "content" not in args:
                            args["content"] = args[c_alias]
                    for o_alias in ("targetcontent", "target_content", "old_str", "old_content", "search", "original"):
                        if o_alias in args and "old" not in args:
                            args["old"] = args[o_alias]
                    for n_alias in ("replacementcontent", "replacement_content", "new_str", "new_content", "replace"):
                        if n_alias in args and "new" not in args:
                            args["new"] = args[n_alias]
                    for cmd_alias in ("commandline", "command_line", "command", "script", "exec"):
                        if cmd_alias in args and "cmd" not in args:
                            args["cmd"] = args[cmd_alias]

                    m_start = start_idx
                    m_end = full_end
                    pre_text = text[:start_idx].rstrip()
                    if pre_text.endswith("```json") or pre_text.endswith("```"):
                        fence_idx = pre_text.rfind("```")
                        if fence_idx != -1:
                            m_start = fence_idx
                    post_text = text[full_end:].lstrip()
                    if post_text.startswith("```"):
                        m_end = full_end + text[full_end:].find("```") + 3

                    raw_matches.append((m_start, m_end, norm_name, args))
                    has_tool = True
                scan_pos = full_end if has_tool else (start_idx + 1)
            except Exception:
                scan_pos = start_idx + 1

        raw_matches.sort(key=lambda m: (m[0], -m[1]))

        non_overlapping = []
        last_end = 0
        for start, end, tool_name, attrs in raw_matches:
            if start >= last_end:
                non_overlapping.append((start, end, tool_name, attrs))
                last_end = end

        segments: List[Tuple[str, Any]] = []
        pos = 0
        last_tool_sig: Optional[Tuple[str, str, str]] = None
        for start, end, tool_name, attrs in non_overlapping:
            if start > pos:
                raw_chunk = text[pos:start]
                clean_chunk = self.strip_xml_tool_calls(raw_chunk).strip()
                if clean_chunk:
                    segments.append(("text", clean_chunk))
                    last_tool_sig = None

            # Deduplicate immediate identical tool calls (e.g. repeated write calls for same file)
            cur_path = str(attrs.get("path") or attrs.get("targetfile") or attrs.get("file") or attrs.get("cmd") or "").strip()
            cur_content = str(attrs.get("content") or attrs.get("code") or attrs.get("codecontent") or "").strip()
            cur_sig = (tool_name, cur_path, cur_content[:100])
            if last_tool_sig and cur_sig == last_tool_sig:
                pos = end
                continue

            last_tool_sig = cur_sig
            segments.append(("tool", (tool_name, attrs)))
            pos = end

        if pos < len(text):
            raw_chunk = text[pos:]
            clean_chunk = self.strip_xml_tool_calls(raw_chunk).strip()
            if clean_chunk:
                segments.append(("text", clean_chunk))

        return segments

    def parse_xml_tool_calls(self, text: str) -> List[Tuple[str, Dict[str, str]]]:
        segments = self.parse_chronological_segments(text)
        return [data for kind, data in segments if kind == "tool"]

    def execute_tool(self, tool_name: str, args: Dict[str, str], ctx: Any = None,
                   ask: bool = True, notify: bool = True) -> Dict[str, Any]:
        """Run one tool.

        ctx:  override the AgentContext. Subagents get their own instance so
              their scratch/todo list does not land on the lead agent's
              checklist. Defaults to the runner context.
        ask:    set False for subagent calls. Subagents work without popping the
                approval dialog (variant C: the commit modal stays the gate).
        notify: set False for subagent calls. Without this a subagent's own tool
                activity fires on_tool_start/on_tool_finish and lands in the lead
                agent's chat as extra markers next to the subagent marker.
        """
        name = tool_name.lower().strip()
        # Without this every tool that takes a context (ls, read, write, glob,
        # search...) got None and died on "NoneType has no attribute
        # resolve_path". The subagent window showed it twice in a row.
        ctx = ctx if ctx is not None else self.ctx

        mode = getattr(ctx, "mode", "auto")
        if mode == "plan" and name in ("write", "edit", "command"):
            return {
                "success": False,
                "error": (
                    f"PLAN MODE ACTIVE: '{name}' is not available in Plan mode - the app "
                    "refuses it, so do not call it again in this turn. Record your plan "
                    "with <tool:todo action=\"set\" todos=\"step one|step two\" /> (one "
                    "call, 4-8 steps, each step naming the file it touches) and then write "
                    "the plan out for the user: Goal / Files to create / Files to change / "
                    "Steps / How to verify. The plan is executed later, in Code mode."
                ),
            }

        # An empty argument is a malformed call, not something to confirm.
        # `<tool:command />` with no cmd reaches here as args={}, and asking
        # "allow running this?" with a blank command pops a dialog the user
        # cannot judge - they cannot see what they would be allowing. The model
        # needs to be told what was wrong so it can send the real call.
        if name in ("command", "shell", "bash", "terminal", "sh", "cmd", "run_command") and not str(args.get("cmd", "") or args.get("command", "") or "").strip():
            return {
                "success": False,
                "error": (
                    'The command tool was called with no command. Send it as '
                    '<tool:command cmd="the shell command to run" /> with the '
                    "command inside the cmd attribute. If you were trying to "
                    "look something up rather than run it, you want a different "
                    "tool: <tool:mcp /> lists MCP servers, <tool:ls /> lists a "
                    "directory, <tool:search /> searches files."
                ),
                "kind": "missing_argument",
            }


        should_ask = (mode == "ask" and name in ("write", "edit", "command")) or (mode == "code" and name == "command")
        if should_ask and self.ask_handler and ask:
            target = args.get("path") or args.get("cmd") or name
            diff_info = ""
            if name == "edit":
                old_lines = len(args.get("old", "").splitlines())
                new_lines = len(args.get("new", "").splitlines())
                diff_info = f"+{new_lines} -{old_lines} lines"
            elif name == "write":
                diff_info = f"+{len(args.get('content', '').splitlines())} lines"
            elif name == "command":
                diff_info = f"command: {args.get('cmd', '')}"

            details = {
                "action": name,
                "target": target,
                "path": args.get("path", target),
                "cmd": args.get("cmd", ""),
                "old": args.get("old", ""),
                "new": args.get("new", ""),
                "content": args.get("content", ""),
                "diff_info": diff_info,
                "file_count": 1,
            }
            try:
                approved = self.ask_handler(
                    f"Allow agent to execute '{name}' on '{target}' ({diff_info})?",
                    ["Yes, proceed", "No, skip"],
                    details,
                )
            except TypeError:
                approved = self.ask_handler(
                    f"Allow agent to execute '{name}' on '{target}' ({diff_info})?",
                    ["Yes, proceed", "No, skip"],
                )
            if approved not in ("Yes, proceed", "allow", "yes"):
                return {"success": False, "error": f"User rejected execution of '{name}': {target}"}

        try:
            if name == "read":
                path = (
                    args.get("path")
                    or args.get("file")
                    or args.get("targetfile")
                    or args.get("target_file")
                    or args.get("filepath")
                    or args.get("file_path")
                    or args.get("filename")
                    or args.get("file_name")
                    or args.get("target")
                    or ""
                )
                lines = args.get("lines")
                if not path:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'path'",
                        "missing": "path",
                        "example": '<tool:read path="filename.ext" />',
                    }
                    if notify and self.on_tool_start:
                        self.on_tool_start("read", "")
                    if notify and self.on_tool_finish:
                        self.on_tool_finish("read", "", err_res)
                    return err_res
                if notify and self.on_tool_start:
                    self.on_tool_start("read", path)
                res = tool_read(ctx, path, lines)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("read", path, res)
                return res

            elif name == "edit":
                path = (
                    args.get("path")
                    or args.get("file")
                    or args.get("targetfile")
                    or args.get("target_file")
                    or args.get("filepath")
                    or args.get("file_path")
                    or args.get("filename")
                    or args.get("file_name")
                    or args.get("target")
                    or ""
                )
                old = (
                    args.get("old")
                    or args.get("targetcontent")
                    or args.get("target_content")
                    or args.get("old_str")
                    or args.get("old_content")
                    or args.get("search")
                    or args.get("original")
                    or ""
                )
                new = (
                    args.get("new")
                    or args.get("replacementcontent")
                    or args.get("replacement_content")
                    or args.get("new_str")
                    or args.get("new_content")
                    or args.get("replace")
                    or args.get("content")
                    or ""
                )
                if not path and (old or new):
                    combined = f"{old}\n{new}"
                    p_m = re.search(r'<(?:path|file|filename|target|target_file|targetfile|dest)>([^<>\r\n]+)</(?:path|file|filename|target|target_file|targetfile|dest)>', combined, re.IGNORECASE)
                    if p_m:
                        path = p_m.group(1).strip()
                if not path:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'path'",
                        "missing": "path",
                        "example": '<tool:edit path="file.ext"><old>exact text</old><new>replacement</new></tool:edit>',
                    }
                    if notify and self.on_tool_start:
                        self.on_tool_start("edit", "")
                    if notify and self.on_tool_finish:
                        self.on_tool_finish("edit", "", err_res)
                    return err_res
                if not old:
                    err_res = {
                        "success": False,
                        "error": "missing <old> snippet to replace",
                        "missing": "old snippet (<old>...</old>)",
                        "example": '<tool:edit path="' + path + '"><old>exact text to replace</old><new>replacement</new></tool:edit>',
                    }
                    if notify and self.on_tool_start:
                        self.on_tool_start("edit", path)
                    if notify and self.on_tool_finish:
                        self.on_tool_finish("edit", path, err_res)
                    return err_res
                if notify and self.on_tool_start:
                    self.on_tool_start("edit", path)
                res = tool_edit(ctx, path, old, new)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("edit", path, res)
                return res

            elif name == "write":
                path = (
                    args.get("path")
                    or args.get("file")
                    or args.get("targetfile")
                    or args.get("target_file")
                    or args.get("filepath")
                    or args.get("file_path")
                    or args.get("filename")
                    or args.get("file_name")
                    or args.get("dest")
                    or args.get("destination")
                    or args.get("target")
                    or ""
                )
                content = (
                    args.get("content")
                    or args.get("codecontent")
                    or args.get("code_content")
                    or args.get("code")
                    or args.get("replacementcontent")
                    or args.get("text")
                    or args.get("body")
                    or args.get("data")
                    or ""
                )
                if not path and content:
                    p_m = re.search(r'<(?:path|file|filename|target|target_file|targetfile|dest)>([^<>\r\n]+)</(?:path|file|filename|target|target_file|targetfile|dest)>', content, re.IGNORECASE)
                    if p_m:
                        path = p_m.group(1).strip()
                        content = re.sub(r'<(?:path|file|filename|target|target_file|targetfile|dest)>[^<>\r\n]+</(?:path|file|filename|target|target_file|targetfile|dest)>', '', content, flags=re.IGNORECASE).strip()
                    else:
                        lines = [l.strip() for l in content.splitlines() if l.strip()]
                        if lines and "." in lines[0] and len(lines[0]) < 100 and not any(k in lines[0] for k in ("<", "{", "(", "import", "def", "class", "=", " ", "\t")):
                            path = lines[0]
                            content = "\n".join(content.splitlines()[1:]).strip()
                if not path:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'path'",
                        "missing": "path",
                        "example": '<tool:write path="filename.ext">content</tool:write>',
                    }
                    if notify and self.on_tool_start:
                        self.on_tool_start("write", "")
                    if notify and self.on_tool_finish:
                        self.on_tool_finish("write", "", err_res)
                    return err_res
                if not content:
                    err_res = {
                        "success": False,
                        "error": "missing file content to write",
                        "missing": "content",
                        "example": '<tool:write path="' + path + '">file content here</tool:write>',
                    }
                    if notify and self.on_tool_start:
                        self.on_tool_start("write", path)
                    if notify and self.on_tool_finish:
                        self.on_tool_finish("write", path, err_res)
                    return err_res
                if notify and self.on_tool_start:
                    self.on_tool_start("write", path)
                res = tool_write(ctx, path, content)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("write", path, res)
                return res

            elif name == "ls":
                path = args.get("path") or args.get("dir") or "."
                if notify and self.on_tool_start:
                    self.on_tool_start("ls", path)
                res = tool_ls(ctx, path)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("ls", path, res)
                return res

            elif name == "glob":
                pattern = args.get("pattern", "*")
                dir_path = args.get("dir", ".")
                if notify and self.on_tool_start:
                    self.on_tool_start("glob", pattern)
                res = tool_glob(ctx, pattern, dir_path)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("glob", pattern, res)
                return res

            elif name == "search":
                query = args.get("query", "")
                path = args.get("path", ".")
                if not query:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'query'",
                        "missing": "query",
                        "example": '<tool:search query="search_term" path="." />',
                    }
                    if notify and self.on_tool_start:
                        self.on_tool_start("search", "")
                    if notify and self.on_tool_finish:
                        self.on_tool_finish("search", "", err_res)
                    return err_res
                if notify and self.on_tool_start:
                    self.on_tool_start("search", query)
                res = tool_search(ctx, query, path)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("search", query, res)
                return res

            elif name in ("command", "shell", "bash", "terminal", "sh", "cmd", "run_command"):
                cmd = args.get("cmd") or args.get("command") or args.get("command_line") or ""
                if not cmd:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'cmd'",
                        "missing": "cmd",
                        "example": '<tool:command cmd="command_line" />',
                    }
                    if notify and self.on_tool_start:
                        self.on_tool_start("command", "")
                    if notify and self.on_tool_finish:
                        self.on_tool_finish("command", "", err_res)
                    return err_res
                is_bg = str(args.get("background", "") or args.get("daemon", "")).lower() in ("true", "1", "yes")
                if notify and self.on_tool_start:
                    target_display = f"{cmd} (background)" if is_bg else cmd
                    self.on_tool_start("command", target_display)
                self.detach_requested = False
                res = tool_command(
                    ctx,
                    cmd,
                    background=is_bg,
                    detach_check=lambda: self.detach_requested,
                )
                if notify and self.on_tool_finish:
                    self.on_tool_finish("command", cmd, res)
                return res

            elif name in ("task", "tasks", "bg_task", "background_task"):
                from .tasks import BackgroundTaskManager
                mgr = BackgroundTaskManager.get_instance()
                action = str(args.get("action", "list")).lower()
                tid = str(args.get("task_id", "") or args.get("id", "")).strip()
                if notify and self.on_tool_start:
                    self.on_tool_start("task", f"{action} {tid}".strip())
                if action == "kill":
                    ok = mgr.kill_task(tid)
                    res = {"success": ok, "task_id": tid, "action": "kill"}
                elif action in ("status", "logs", "read"):
                    t = mgr.get_task(tid)
                    if t:
                        logs = t.read_logs()
                        res = {"success": True, "task_id": tid, "running": t.is_running(), "status": "running" if t.is_running() else f"finished (code {t.poll()})", "logs": logs}
                    else:
                        res = {"success": False, "error": f"Task '{tid}' not found."}
                else:
                    tasks_list = mgr.list_tasks()
                    res = {"success": True, "tasks": tasks_list}
                if notify and self.on_tool_finish:
                    self.on_tool_finish("task", f"{action} {tid}".strip(), res)
                return res


            elif name == "web":
                target = args.get("query") or args.get("url") or args.get("q") or ""
                if not target:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'query'",
                        "missing": "query",
                        "example": '<tool:web query="search query" />',
                    }
                    if notify and self.on_tool_start:
                        self.on_tool_start("web", "")
                    if notify and self.on_tool_finish:
                        self.on_tool_finish("web", "", err_res)
                    return err_res
                if notify and self.on_tool_start:
                    self.on_tool_start("web", target)
                res = tool_web(ctx, target)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("web", target, res)
                return res

            elif name in ("scratch", "todo", "tasks"):
                action = args.get("action", "add")
                note = args.get("note", "")
                # A plan is recorded in one call:
                #   <tool:todo action="set" todos="step one|step two" />
                # Only `action` and `note` used to reach the tool, so every other
                # attribute was dropped and a whole checklist could never be
                # stored in one call.
                todos = args.get("todos", "")
                new_text = args.get("new_text", "") or args.get("newtext", "")
                raw_index = args.get("index")
                index_val: Optional[int] = None
                if raw_index not in (None, ""):
                    try:
                        index_val = int(str(raw_index).strip())
                    except (TypeError, ValueError):
                        index_val = None
                target_str = f"{action}: {note}" if note else action
                if todos:
                    target_str = f"{action}: {todos}"
                if notify and self.on_tool_start:
                    self.on_tool_start("todo", target_str)
                res = tool_scratch(
                    ctx,
                    action,
                    note,
                    index=index_val,
                    new_text=new_text,
                    todos=todos,
                )
                if notify and self.on_todo_updated:
                    self.on_todo_updated(res.get("visible_steps", []))
                if notify and self.on_tool_finish:
                    self.on_tool_finish("todo", target_str, res)
                return res

            elif name == "ask":
                question = args.get("question") or args.get("prompt") or args.get("note") or "Agent confirmation requested"
                options_str = args.get("options", "")
                if options_str:
                    if "|" in options_str:
                        options = [o.strip() for o in options_str.split("|") if o.strip()]
                    elif "," in options_str:
                        options = [o.strip() for o in options_str.split(",") if o.strip()]
                    else:
                        options = [options_str.strip()]
                else:
                    options = ["Yes", "No", "Always", "Cancel"]

                if notify and self.on_tool_start:
                    self.on_tool_start("ask", question)

                answer = ""
                if self.ask_handler:
                    answer = self.ask_handler(question, options)
                else:
                    answer = "Interactive confirmation modal unavailable"

                res = {"success": True, "question": question, "answer": answer}
                if notify and self.on_tool_finish:
                    self.on_tool_finish("ask", question, res)
                return res

            elif name in ("bugs", "tool_bugs"):
                if notify and self.on_tool_start:
                    self.on_tool_start("bugs", "")
                res = tool_bugs(ctx)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("bugs", "", res)
                return res

            elif name in ("code_search", "codesearch", "symbol_search"):
                query = args.get("query", "")
                if notify and self.on_tool_start:
                    self.on_tool_start("code_search", query)
                res = tool_code_search(ctx, query)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("code_search", query, res)
                return res

            elif name == "screenshot":
                target = args.get("target", "active_window")
                if notify and self.on_tool_start:
                    self.on_tool_start("screenshot", target)
                res = tool_screenshot(ctx, target)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("screenshot", target, res)
                return res

            elif name == "vision":
                image_path = args.get("image_path") or args.get("path", "")
                question = args.get("question", "")
                if notify and self.on_tool_start:
                    self.on_tool_start("vision", image_path)
                res = tool_vision(ctx, image_path, question)
                if notify and self.on_tool_finish:
                    self.on_tool_finish("vision", image_path, res)
                return res

            elif name == "mcp":
                wanted = (args.get("server") or args.get("name") or "").strip()
                if notify and self.on_tool_start:
                    # The server name has to be known *before* the callback:
                    # the start hook decides whether this call draws a card,
                    # and with an empty target it could not tell a bare
                    # listing from `<tool:mcp open server="X" />` and silenced
                    # both, hiding the tool list the model just fetched.
                    self.on_tool_start("mcp", wanted)
                res = self._mcp_open_server(wanted) if wanted else self._mcp_list_servers()
                if notify and self.on_tool_finish:
                    self.on_tool_finish("mcp", wanted, res)
                return res

            elif name == "subagent":
                return self.execute_subagents([args])

            elif name == "agent_note":
                # Only reachable if a subagent forgot to route through
                # execute_subagents. There is no lead agent to read a note.
                res = {
                    "success": False,
                    "error": "agent_note is only available inside a subagent. Use <tool:scratch action=\"add\" /> for your own checklist.",
                }
                if notify and self.on_tool_finish:
                    self.on_tool_finish("agent_note", "", res)
                return res

            elif name.startswith(TOOL_NAME_PREFIX):
                return self._execute_mcp_tool(name, args, notify)

            return {"success": False, "error": f"Unknown tool: {name}"}
        except Exception as e:
            err_res = {"success": False, "error": str(e)}
            if notify and self.on_tool_finish:
                self.on_tool_finish(name, str(args.get("path") or args.get("cmd") or ""), err_res)
            return err_res


    def execute_subagents(
        self,
        calls: List[Dict[str, str]],
        on_progress: Optional[Callable[[Any], None]] = None,
        is_local: Optional[bool] = None,
        model_call: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
        max_turns: int = 8,
    ) -> Dict[str, Any]:
        """Run a batch of <tool:subagent ... /> calls.

        One task per call, so N calls produce N markers in the chat. A local
        model runs them one after another; an API model runs them all at once
        (SubagentOrchestrator.execute_tasks branches on is_local).
        """
        from .subagents.models import SubagentTask
        from .subagents.orchestrator import SUBAGENT_AVAILABLE_TOOLS, SubagentOrchestrator
        from .tools import AgentContext

        calls = [c for c in (calls or []) if isinstance(c, dict)]
        if not calls:
            return {"success": False, "error": "subagent was called without a task", "subagents": []}

        mode = getattr(self.ctx, "mode", "auto")
        orch = SubagentOrchestrator(engine=None)
        tasks: List[SubagentTask] = []

        for raw in calls:
            role = str(raw.get("name") or raw.get("role") or "Subagent").strip()[:60] or "Subagent"
            goal = str(raw.get("goal") or raw.get("task") or raw.get("description") or "").strip()
            system = str(raw.get("system") or raw.get("system_prompt") or raw.get("prompt") or "").strip()
            if not goal:
                goal = system or "Inspect the workspace and report what you find."
            if not system:
                system = f"You are the autonomous {role} subagent."
            task = SubagentTask(
                role=role,
                goal=goal,
                system_prompt=system,
                # Explicit, not the dataclass default: SubagentTask.tools
                # defaults to a 4-item read-only list, and run_subagent only
                # falls back to SUBAGENT_AVAILABLE_TOOLS when tools is empty.
                tools=list(SUBAGENT_AVAILABLE_TOOLS),
                context={"workdir": self.workdir},
            )
            if mode == "ask":
                # Ask mode needs a human OK for write/edit/command, and a
                # subagent cannot ask mid-run. Take the tools away instead of
                # letting it write unattended.
                task.tools = [t for t in task.tools if t not in ("write", "edit", "command")]
            tasks.append(task)

        # SubagentOrchestrator calls tool_executor_factory(role), not
        # factory(task), and it does so once per task in list order. Hand out
        # the matching task so two subagents with the same role stay separate.
        pending: Dict[str, List[SubagentTask]] = {}
        for t in tasks:
            pending.setdefault(t.role, []).append(t)

        def _make_executor(role: str):
            queue = pending.get(role) or []
            task = queue.pop(0) if queue else None
            # Per-subagent context so scratch/todo entries and queued images do
            # not bleed into the lead agent's context.
            sub_ctx = AgentContext(workdir=self.workdir)
            sub_ctx.mode = mode

            def _exec(name: str, args: Dict[str, str]) -> Dict[str, Any]:
                n = (name or "").lower().strip()
                if n in ("todo", "tasks"):
                    n = "scratch"
                if n == "agent_note":
                    text = str(args.get("text") or args.get("note") or "").strip()
                    if text and task is not None:
                        task.notes.append(text)
                    return {"success": True, "note": text}
                if n == "ask":
                    # The user cannot answer mid-run and the lead agent is busy
                    # waiting, so ask would only pop a modal in the main window
                    # that the subagent then cannot read. Fail with the way out.
                    return {
                        "success": False,
                        "error": "You cannot ask anyone anything. Decide on your own and "
                                 "continue. If the lead agent needs to know something, put "
                                 "it in <tool:agent_note text=\"...\" />.",
                    }
                # notify=False: a subagent's own tools must not add markers to
                # the lead agent's chat or touch the main app's todo bar. Only the
                # one subagent marker shows.
                return self.execute_tool(n, args, ctx=sub_ctx, ask=False, notify=False)

            return _exec

        if is_local is None:
            provider = getattr(self, "provider_id", "local_gguf") or "local_gguf"
            is_local = provider in ("local_gguf", "local", "llama_cpp", "ollama")

        results = orch.execute_tasks(
            tasks,
            on_progress=on_progress,
            is_local=is_local,
            model_call=model_call,
            tool_executor=None,
            tool_executor_factory=_make_executor,
            max_turns=max_turns,
        )

        by_role: Dict[str, Any] = {t.role: t for t in tasks}
        agents: List[Dict[str, Any]] = []
        for r in results:
            task = by_role.get(r.role)
            history = r.history or []
            tool_count = sum(
                1 for m in history
                if m.get("role") == "user" and str(m.get("content", "")).startswith("[tool:")
            )
            all_failed = _all_tools_failed(r.events or [])
            # A subagent whose every tool errored did not do its task, whatever
            # its closing text claims. Trusting that text let "done, all fine"
            # reach the lead agent over a run where nothing was read.
            status = "failed" if (not r.success or all_failed) else "completed"
            if all_failed and r.success:
                summary = ("Every tool this subagent ran failed, so its report "
                           "could not be based on the workspace.\n"
                           + str(r.summary or "").strip())
            else:
                summary = r.summary
            agents.append({
                "role": r.role,
                "goal": getattr(task, "goal", "") if task else "",
                "status": status,
                "summary": summary,
                "findings": list(r.findings or []),
                "notes": list(r.notes or []),
                "tool_count": tool_count,
                "error": "" if status == "completed" else summary,
                "history": history,
                "events": list(r.events or []),
                "elapsed": float(r.elapsed or 0.0),
                "tools_all_failed": all_failed,
                "system_prompt": r.system_prompt,
                "tokens": int(r.tokens_in or 0) + int(r.tokens_out or 0),
            })

        total_tools = sum(a["tool_count"] for a in agents)
        total_notes = sum(len(a["notes"]) for a in agents)
        failed_n = sum(1 for a in agents if a["status"] == "failed")
        out: Dict[str, Any] = {
            "success": failed_n < len(agents),
            "subagents": agents,
            "count": len(agents),
            "tool_count": total_tools,
            "notes": [f"{a['role']}: {n}" for a in agents for n in a["notes"]],
            "is_local": bool(is_local),
        }
        if failed_n:
            # Only set the key when it carries a message. format_tool_result_for_llm
            # treats the mere presence of "error" as a tool failure.
            out["error"] = f"{failed_n} of {len(agents)} subagent(s) failed."
        return out

    def strip_xml_tool_calls(self, text: str) -> str:
        if not text:
            return ""
        clean = text
        try:
            dec = json.JSONDecoder()
            scan_pos = 0
            json_ranges = []
            while scan_pos < len(clean):
                idx_obj = clean.find("{", scan_pos)
                idx_arr = clean.find("[", scan_pos)
                candidates = [p for p in (idx_obj, idx_arr) if p != -1]
                if not candidates:
                    break
                start_idx = min(candidates)
                try:
                    parsed, end_offset = dec.raw_decode(clean[start_idx:])
                    full_end = start_idx + end_offset
                    calls = parsed if isinstance(parsed, list) else [parsed]
                    is_tool_chunk = False
                    for call_obj in calls:
                        if isinstance(call_obj, dict) and any(k in call_obj for k in ("tool_name", "tool", "name", "tool_call_id", "function")):
                            is_tool_chunk = True
                            break
                    if is_tool_chunk:
                        m_start = start_idx
                        m_end = full_end
                        pre = clean[:start_idx].rstrip()
                        if pre.endswith("```json") or pre.endswith("```"):
                            fence = pre.rfind("```")
                            if fence != -1:
                                m_start = fence
                        post = clean[full_end:].lstrip()
                        if post.startswith("```"):
                            m_end = full_end + clean[full_end:].find("```") + 3
                        json_ranges.append((m_start, m_end))
                        scan_pos = m_end
                    else:
                        scan_pos = start_idx + 1
                except Exception:
                    scan_pos = start_idx + 1
            for r_start, r_end in reversed(sorted(json_ranges, key=lambda r: r[0])):
                clean = clean[:r_start] + clean[r_end:]
        except Exception:
            pass

        clean = re.sub(
            r'<\|tool_call>call:\w+(?:\s+[^>]*?)?(?:\s*/>|>(.*?)</\|tool_call>)',
            '',
            clean,
            flags=re.DOTALL | re.IGNORECASE,
        )
        clean = re.sub(
            r'<tool:(\w+)(?:\s+[^>]*?)?\s*/?>.*?</(?:tool:)?(?:\1|tool)>',
            '',
            clean,
            flags=re.DOTALL | re.IGNORECASE,
        )
        clean = re.sub(r'<tool:\w+(?:\s+[^>]*?)?\s*/>', '', clean, flags=re.IGNORECASE)
        # A server-side subagent reports back as prose, not as an event: the
        # model writes `<subagent sessionID="..." state="completed">` into its
        # answer, and without this it was printed verbatim in the chat as if it
        # were something the user was meant to read. Same for the server's
        # `task` tool. The completion itself still closes the marker - that
        # travels as a `session.tool.success` event, not as this text.
        clean = re.sub(r'<subagent(?:\s+[^>]*?)?\s*/>', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<subagent(?:\s+[^>]*?)?>.*?</subagent>', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<task(?:\s+[^>]*?)?\s*/>', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<task(?:\s+[^>]*?)?>.*?</task>', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<tool_call(?:\s+[^>]*?)?(?:\s*/>|>.*?</tool_call>)', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<tool:\w+[^>]*>.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<\|tool_call.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<tool_call.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<(?:tool:?\w*|\|tool_call\w*|tool_call\w*)[^>]*$', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'</?tool:\w+[^>]*>', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'</?\|?tool_call[^>]*>', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<subagent[^>]*$', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<task[^>]*$', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'</?subagent[^>]*>', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'</?task[^>]*>', '', clean, flags=re.IGNORECASE)
        return clean.strip()

    def format_tool_result_for_llm(self, tool_name: str, res: Dict[str, Any]) -> str:
        try:
            return self._format_tool_result_impl(tool_name, res)
        except Exception as exc:
            import json
            name = tool_name.lower().strip()
            try:
                dumped = json.dumps(res, default=str, ensure_ascii=False)
            except Exception:
                dumped = str(res)
            return f'<tool_response name="{name}">\n{dumped}\n</tool_response>\n[Proceed with the next step or final answer based on the tool result. Do NOT repeat previous messages.]'

    def _format_tool_result_impl(self, tool_name: str, res: Dict[str, Any]) -> str:
        name = tool_name.lower().strip()

        # A subagent batch always goes through the full formatter below, even
        # when it failed: its per-agent block carries the reports, the notes and
        # the reason, and "ERROR: 1 of 1 subagent(s) failed." told the lead
        # agent nothing it could act on.
        if name == "subagent":
            pass
        elif not res.get("success", True) or "error" in res:
            err = res.get("error") or res.get("stderr") or "Execution failed"
            missing = res.get("missing")
            example = res.get("example")
            if missing and example:
                return f'<tool_response name="{name}">ERROR: missing {missing}. Use: {example}</tool_response>'
            elif missing:
                return f'<tool_response name="{name}">ERROR: missing {missing}</tool_response>'
            if name == "edit" and "target content not found" in str(err).lower():
                return f'<tool_response name="{name}">ERROR: <old> snippet not found. Call <tool:read path="{res.get("path", "")}" /> to check exact lines, then retry <tool:edit>.</tool_response>'
            if res.get("verification") == "FAILED":
                v_err = res.get("verification_error", "") or res.get("error", "")
                return f'<tool_response name="{name}">ERROR: syntax error: {v_err}. Fix this syntax error in your next tool call.</tool_response>'
            return f'<tool_response name="{name}">ERROR: {err}</tool_response>'

        lines = [f'<tool_response name="{name}">']
        if name == "read":
            lines.append(f"File: {res.get('path')}")
            lines.append(f"Total lines: {res.get('total_lines')}")
            lines.append(f"```\n{res.get('content', '')}\n```")
        elif name in ("edit", "write"):
            lines.append(f"Successfully modified: {res.get('path')}")
            if "lines" in res:
                lines.append(f"Total lines written: {res['lines']}")
            if res.get("verification") == "OK":
                lines.append("Self-check syntax verification: PASSED (0 errors).")
            if res.get("test_status"):
                lines.append(f"\n{res['test_status']}")
        elif name in ("command", "shell", "bash", "terminal", "sh", "cmd", "run_command"):
            lines.append(f"Return code: {res.get('returncode')}")

            if res.get("stdout"):
                lines.append(f"Stdout:\n{res['stdout']}")
            if res.get("stderr"):
                lines.append(f"Stderr:\n{res['stderr']}")
        elif name == "web":
            results = res.get("results", [])
            if results:
                lines.append(f"Web search top results for: \"{res.get('query', '')}\"")
                for i, r in enumerate(results[:5], 1):
                    title = r.get("title", "")
                    url = r.get("url", "")
                    snippet = r.get("snippet", "").strip()
                    if len(snippet) > 180:
                        snippet = snippet[:177] + "..."
                    lines.append(f"{i}. {title}\n   URL: {url}\n   Snippet: {snippet}")
            elif res.get("content"):
                text = str(res["content"]).strip()
                if len(text) > 1200:
                    text = text[:1150] + "\n... [Remaining web content truncated to conserve context tokens]"
                lines.append(text)
        elif name in ("bugs", "tool_bugs"):
            scanned = res.get("scanned_files", 0)
            errors = res.get("errors", {})
            if not errors:
                lines.append(f"Project syntax check passed! {scanned} files scanned with 0 syntax errors.")
            else:
                lines.append(f"Found syntax errors in {len(errors)} file(s) (out of {scanned} scanned):")
                for fpath, errs in errors.items():
                    lines.append(f"  File {fpath}:")
                    for e in errs:
                        lines.append(f"    - {e}")
        elif name in ("code_search", "codesearch", "search"):
            if "symbols" in res:
                lines.append(f"Found {res.get('count', 0)} symbol(s):")
                for s in res.get("symbols", []):
                    lines.append(f"- {s.get('filepath')}:{s.get('start_line')} [{s.get('symbol_type')}] {s.get('signature')}")
            elif "results" in res:
                lines.append(f"Found {len(res['results'])} text match(es):")
                for m in res["results"][:15]:
                    lines.append(f"- {m.get('file')}:{m.get('line')}: {m.get('content')}")
        elif name == "screenshot":
            if res.get("path"):
                lines.append(f"Screenshot saved: {res['path']} ({res.get('size_bytes', 0)} bytes)")
                lines.append("To analyze it, call <tool:vision ... /> with this image_path.")
            else:
                lines.append(f"Screenshot failed: {res.get('error', 'unknown')}")
        elif name == "vision":
            if res.get("path"):
                lines.append(f"Image queued: {res['path']}")
                if res.get("question"):
                    lines.append(f"Question: {res['question']}")
            else:
                lines.append(f"Vision failed: {res.get('error', 'unknown')}")
        elif name == "subagent":
            # The subagent's final report comes back as the tool result - that is
            # the data channel, the same shape OpenCode's task tool uses. What
            # stays out is the transcript and the raw tool output, which is what
            # actually keeps the lead agent's context small: a subagent that
            # read forty files hands over its conclusions, not the files.
            agents = res.get("subagents", [])
            if not agents:
                lines.append("No subagent produced a result.")
            else:
                names = [str(a.get("role", "Subagent")) for a in agents]
                failed = [str(a.get("role", "Subagent")) for a in agents
                          if str(a.get("status", "")) == "failed"]
                lines.append(f"{len(agents)} subagent(s) finished: {', '.join(names)}.")
                if failed:
                    lines.append(f"Failed: {', '.join(failed)}.")
                notes = res.get("notes") or []
                if notes:
                    lines.append("Notes they left for you:")
                    for nt in notes:
                        lines.append(f"  - {nt}")
                for a in agents:
                    summary = str(a.get("summary", "") or "").strip()
                    if not summary:
                        continue
                    role = str(a.get("role", "Subagent"))
                    if len(summary) > 1800:
                        summary = summary[:1800] + "\n... [report truncated]"
                    lines.append(f"\nReport from {role}:")
                    lines.append(summary)
                if not any(str(a.get("summary", "") or "").strip() for a in agents):
                    lines.append("None of them wrote a report. Their work is not in your context: "
                                 "do not assume what they found, check the files yourself.")
                lines.append("Review before telling the user it is done: "
                             "<tool:command cmd=\"git diff --stat\" />, then <tool:read> the files that matter, "
                             "verify with <tool:bugs />, and describe the result in your own words.")
        elif name == "mcp":
            if res.get("error"):
                lines.append(f"Error: {res.get('error')}")
            servers = res.get("servers", [])
            if servers:
                lines.append("Available MCP servers:")
                for s in servers:
                    if isinstance(s, dict):
                        status = s.get("status", "unknown")
                        count = s.get("tool_count", 0)
                        usable = s.get("usable", False)
                        perms = s.get("tools_by_permission") or {}
                        perm_str = f" [read: {perms.get('read', 0)}, write: {perms.get('write', 0)}, destructive: {perms.get('destructive', 0)}]" if perms else ""
                        lines.append(f"- Server '{s.get('name')}': status={status}, usable={usable}, {count} tools{perm_str}")
                    else:
                        lines.append(f"- {s}")
            if res.get("schema_text"):
                # The whole point: schemas live in the transcript, so the
                # prompt prefix stays identical between turns.
                lines.append(str(res["schema_text"]))
            if res.get("note"):
                lines.append(str(res["note"]))
        elif name.startswith(TOOL_NAME_PREFIX):
            if res.get("text"):
                lines.append(res["text"])
            if res.get("structured") is not None:
                lines.append(json.dumps(res["structured"], ensure_ascii=False, indent=2))
            if res.get("images"):
                lines.append(f"({len(res['images'])} image(s) attached to this request.)")
            if not res.get("text") and res.get("structured") is None and not res.get("images"):
                lines.append(f"{res.get('tool')} returned no content.")
        else:
            clean_res = {k: v for k, v in res.items() if k not in ("diff_entries",)}
            lines.append(json.dumps(clean_res, indent=2, ensure_ascii=False))

        lines.append('</tool_response>')
        lines.append('[Proceed with the next step or final answer based on the tool result. Do NOT repeat previous messages.]')
        return "\n".join(str(l) if not isinstance(l, str) else l for l in lines)


# The checklist rule is the same in both writing modes, so it is written once.
# It is what turns a plan into work: without it a plan made in PLAN mode sits in
# the context and the agent starts the next task from scratch.
_CHECKLIST_RULE = (
    "- If a checklist exists (from PLAN mode), it is your work order: execute it step "
    "by step, mark each finished step with <tool:todo action=\"done\" index=\"N\" />, "
    "and do not end the turn while steps remain.\n"
)

# The tool list used to be the same in every mode, which told a model in PLAN
# mode that Write File was available. It called it, the app refused, and the turn
# ended in an apology instead of a plan - the symptom was a model that described
# the app it wanted to build instead of planning it. The list now matches what
# `execute_tool` actually allows, and names what is blocked.
_PLAN_TOOLS_BLOCK = (
    "AVAILABLE TOOLS IN THIS MODE (use these):\n"
    "1. Read File: <tool:read path=\"relative/path.ext\" lines=\"1-100\" />\n"
    "2. List Directory: <tool:ls path=\".\" />\n"
    "3. Find Files: <tool:glob pattern=\"*.*\" dir=\".\" />\n"
    "4. Text Search: <tool:search query=\"search_term\" path=\".\" />\n"
    "5. Symbol Search (AST/FTS5): <tool:code_search query=\"symbol_or_function\" />\n"
    "6. Web Search: <tool:web query=\"docs or query or https://url\" />\n"
    "7. Plan/Todo Tracker: <tool:todo action=\"set\" todos=\"step one|step two|step three\" /> - "
    "that is how you record the plan. 'set' replaces the checklist, 'add' appends one "
    "step, 'done' marks a finished step.\n"
    "\n"
    "NOT AVAILABLE IN PLAN MODE: Write File, Edit File and Run Command. Calling them does "
    "not work - the app answers with an error and the step is wasted. Never call "
    "<tool:write>, <tool:edit> or <tool:command> in this mode. MCP servers and subagents "
    "are not used in PLAN mode either.\n"
)

_PLAN_DIRECTIVES_BLOCK = (
    "\nEXECUTION DIRECTIVES:\n"
    "- INTERLEAVED EXPLANATIONS & ACTIONS: say in one sentence what you are looking at "
    "before each tool call, so the user can follow the investigation.\n"
    "- STRUCTURE: [what you checked] -> <tool:...> -> [what you found] -> <tool:...> -> "
    "[the plan]. Do NOT dump a wall of tools without explanations.\n"
    "- ONE CHECKLIST CALL: record the plan with a single <tool:todo action=\"set\" ... /> "
    "call, after the investigation and before your summary.\n"
    "- STOP AFTER THE PLAN: no tool call after the checklist, and no second checklist.\n"
)


def _full_tools_block(vision: bool) -> str:
    """The complete tool list, for the modes where nothing is blocked."""
    return (
        "AVAILABLE TOOLS:\n"
        "1. Write File: <tool:write path=\"relative/path.ext\">file content here</tool:write>\n"
        "2. Read File: <tool:read path=\"relative/path.ext\" lines=\"1-100\" />\n"
        "3. Edit File: <tool:edit path=\"file.ext\" old=\"exact old snippet\">new replacement snippet</tool:edit>\n"
        "4. List Directory: <tool:ls path=\".\" />\n"
        "5. Find Files: <tool:glob pattern=\"*.*\" dir=\".\" />\n"
        "6. Text Search: <tool:search query=\"search_term\" path=\".\" />\n"
        "7. Symbol Search (AST/FTS5): <tool:code_search query=\"symbol_or_function\" />\n"
        "8. Run Command: <tool:command cmd=\"shell command\" />\n"
        "9. Web Search: <tool:web query=\"docs or query or https://url\" />\n"
        "10. Syntax Bug Check: <tool:bugs />\n"
        "11. Plan/Todo Tracker: <tool:todo action=\"add\" note=\"step description\" /> - "
        "'set' replaces the checklist, 'add' appends one step, 'done' marks a finished "
        "step.\n"
        "12. MCP Servers: <tool:mcp /> lists the configured servers. Then "
        "<tool:mcp open server=\"NAME\" /> returns that server's tools, their arguments "
        "and their exact call names. Call those tools as "
        "<tool:mcp__NAME__TOOL>{\"arg\": \"value\"}</tool:mcp__NAME__TOOL> with a JSON "
        "object between the tags. Only the two `mcp` tools are listed here on purpose: "
        "server tools appear in context only after you open a server, so you never have to "
        "guess a name. Writing tools are approved by the user at call time.\n"
        "13. Spawn Subagent: <tool:subagent name=\"Role\" system=\"instructions for the subagent\">the task it must do</tool:subagent>\n"
        + (
            "14. Screenshot: <tool:screenshot target=\"active_window\" /> (captures a window to a PNG file)\n"
            "15. Vision: <tool:vision image_path=\"path.png\" question=\"what to check\" /> (attaches the image to your next request)\n"
            if vision else ""
        )
    )


_FULL_DIRECTIVES_BLOCK = (
    "\nEXECUTION DIRECTIVES:\n"
    "- INTERLEAVED EXPLANATIONS & ACTIONS: Always include a brief 1-2 sentence explanation before and between your tool actions (e.g. state what you are inspecting, what you found, or why you are making a change), followed by the tool call. This allows the user to follow your step-by-step progress chronologically.\n"
    "- STRUCTURE: Write naturally as: [Brief reasoning/finding] -> <tool:...> -> [Next finding/decision] -> <tool:...> -> [Final outcome]. Do NOT dump a wall of tools without explanations.\n"
    "- SUBAGENTS: When a task is large, split it and delegate with <tool:subagent>. You may emit several in one turn — they run one after another on a local model, or all at once on an API model.\n"
    "- SUBAGENT CONTRACT: `name` is the short role shown in the UI (e.g. \"Code Architect\"). `system` is the prompt you generate for it, so spell out what it must and must not do. The element body is the task itself, and it is the only text the user sees under TASK. Subagents can use every tool you have except spawning more subagents. They cannot ask the user anything, so never give them a task that needs a decision.\n"
    "- AGENT NOTES: a subagent works in its own context. It cannot spawn more subagents and cannot ask the user anything. Its transcript, its tool calls and their raw output never reach you. What reaches you is its final report, plus any notes it chose to send with <tool:agent_note text=\"...\" />, all arriving when the batch finishes. Treat that report as the subagent's answer and build on it, but do not present it as verified: after the batch, review before reporting — run <tool:command cmd=\"git diff --stat\" />, check the files that matter, verify with <tool:bugs />, and describe the result in your own words.\n"
    "- Be fast, direct, and proactive: execute the necessary reads, edits, writes, and commands to complete the user request.\n"
    "- Always verify your work with `<tool:bugs />` or `<tool:command>` to ensure zero errors.\n"
)


def get_agent_system_prompt(workdir: str = ".", mode: str = "auto", vision: bool = False) -> str:
    import os
    abs_work = os.path.abspath(workdir)
    mode_clean = (mode or "auto").strip().lower()

    if mode_clean == "plan":
        mode_section = (
            "CURRENT EXECUTION MODE: PLAN\n"
            "- You are the agent in PLAN mode. This turn produces a plan, not code.\n"
            "- Work in this order and do not skip a stage: 1) investigate the workspace with "
            "the read-only tools, 2) decide the plan, 3) record the plan, 4) report the plan.\n"
            "- Record the plan as your checklist, in exactly one call: "
            "<tool:todo action=\"set\" todos=\"step one|step two|step three\" />. "
            "4-8 steps, and every step names the file it touches.\n"
            "- Then report the plan: Goal / Files to create / Files to change / Steps / How "
            "to verify, at most 15 lines.\n"
            "- Do NOT paste code, do NOT describe the finished app in prose, and do NOT write "
            "out a file-by-file blueprint of the app you would build. A plan is a list of "
            "steps, nothing else.\n"
            "- Do NOT create dummy, placeholder, or arbitrary files (such as index.html, "
            "index.py, index.js) - you cannot create files in this mode.\n"
            "- Stop after the plan. No other tool call in this turn.\n"
            "- That checklist is your work order: later, in code mode, you execute it step "
            "by step. So every step must be executable as written - \"create "
            "notes/app.py with the note list\", not \"implement the feature\"."
        )
        tools_block = _PLAN_TOOLS_BLOCK
        directives_block = _PLAN_DIRECTIVES_BLOCK
    elif mode_clean == "code":
        mode_section = (
            "CURRENT EXECUTION MODE: CODE\n"
            "- In CODE mode, you are an expert implementation engineer. Your goal is to write clean, complete production code, perform edits, run tests, and fix bugs based on the user's instructions and previous plans.\n"
            "- DO NOT create arbitrary, unrequested dummy or placeholder files (such as index.html, index.py, index.js) unless the user explicitly requested that specific file or technology stack.\n"
            "- Focus directly on implementing the exact project files required for the task.\n"
            + _CHECKLIST_RULE
        )
        tools_block = _full_tools_block(vision)
        directives_block = _FULL_DIRECTIVES_BLOCK
    else:
        mode_section = (
            "CURRENT EXECUTION MODE: AUTO\n"
            "- In AUTO mode, you autonomously inspect, plan, write code, run tests, and verify results end-to-end.\n"
            "- DO NOT invent arbitrary dummy files (e.g. index.html) unless required by the task or user.\n"
            + _CHECKLIST_RULE
        )
        tools_block = _full_tools_block(vision)
        directives_block = _FULL_DIRECTIVES_BLOCK

    return (
        f"\n\n### AUTONOMOUS CODE AGENT ({mode_clean.upper()} MODE) IN WORKSPACE: `{abs_work}`\n"
        f"{mode_section}\n\n"
        f"{tools_block}"
        f"{directives_block}"
    )
