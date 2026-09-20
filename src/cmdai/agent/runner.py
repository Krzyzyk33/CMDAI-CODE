import re
from typing import Any, Callable, Dict, List, Optional, Tuple

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


class AgentRunner:

    def __init__(
        self,
        ctx: AgentContext,
        on_tool_start: Optional[Callable[[str, str], None]] = None,
        on_tool_finish: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        on_todo_updated: Optional[Callable[[List[str]], None]] = None,
        ask_handler: Optional[Callable[[str, List[str]], str]] = None,
        mcp_client: Any = None,
    ):
        self.ctx = ctx
        self.workdir = getattr(ctx, "workdir", ".")
        self.on_tool_start = on_tool_start
        self.on_tool_finish = on_tool_finish
        self.on_todo_updated = on_todo_updated
        self.ask_handler = ask_handler
        self.mcp = mcp_client
        self._mcp_unlocked = False
        self.provider_id = "local_gguf"

    def _mcps_status(self) -> Dict[str, Any]:
        if not self.mcp:
            return {"success": True, "servers": [], "note": "MCP not configured (mcp_config.json)."}
        lines = self.mcp.status_lines()
        self._mcp_unlocked = True
        return {"success": True, "servers": lines,
                "note": "Connected server tools are now available as <server>_<tool> (e.g. github_list_prs)."}

    def _dynamic_mcp_tool(self, name: str) -> Optional[Tuple[str, str]]:
        if not self.mcp or not self._mcp_unlocked:
            return None
        try:
            return self.mcp.dynamic_tools().get(name.lower())
        except Exception:
            return None

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
            if body:
                if tool_name == "command":
                    attrs["cmd"] = body
                elif tool_name == "scratch":
                    attrs["note"] = body
                elif tool_name == "ask":
                    if "question" not in attrs:
                        attrs["question"] = body
                elif tool_name == "write":
                    if "content" not in attrs:
                        attrs["content"] = body
                elif tool_name == "subagent":
                    if "goal" not in attrs and "task" not in attrs:
                        attrs["goal"] = body
                elif tool_name == "edit":
                    old_m = re.search(r'<old>(.*?)</old>', body, re.DOTALL | re.IGNORECASE)
                    new_m = re.search(r'<new>(.*?)</new>', body, re.DOTALL | re.IGNORECASE)
                    if old_m:
                        attrs["old"] = old_m.group(1)
                    if new_m:
                        attrs["new"] = new_m.group(1)
                    elif not old_m and "new" not in attrs:
                        attrs["new"] = body
            raw_matches.append((match.start(), match.end(), tool_name, attrs))

        body_pattern = re.compile(r'<tool:(\w+)(?:\s+([^>]*?))?>(.*?)</tool:\1>', re.DOTALL | re.IGNORECASE)
        for match in body_pattern.finditer(text):
            tool_name = match.group(1).lower()
            attrs_str = match.group(2) or ""
            body = match.group(3).strip()
            attrs = _parse_attrs(attrs_str)
            if tool_name == "command":
                attrs["cmd"] = body
            elif tool_name == "scratch":
                attrs["note"] = body
            elif tool_name == "web":
                if "query" not in attrs:
                    attrs["query"] = body
            elif tool_name == "ask":
                if "question" not in attrs:
                    attrs["question"] = body
            elif tool_name == "write":
                if "content" not in attrs:
                    attrs["content"] = body
            elif tool_name == "subagent":
                if "goal" not in attrs and "task" not in attrs:
                    attrs["goal"] = body
            elif tool_name == "edit":
                old_m = re.search(r'<old>(.*?)</old>', body, re.DOTALL | re.IGNORECASE)
                new_m = re.search(r'<new>(.*?)</new>', body, re.DOTALL | re.IGNORECASE)
                if old_m:
                    attrs["old"] = old_m.group(1)
                if new_m:
                    attrs["new"] = new_m.group(1)
                elif not old_m and "new" not in attrs:
                    attrs["new"] = body
            raw_matches.append((match.start(), match.end(), tool_name, attrs))

        single_tag_pattern = re.compile(r'<tool:(\w+)(?:\s+([^>]*?))?\s*/?>', re.DOTALL | re.IGNORECASE)
        for match in single_tag_pattern.finditer(text):
            tool_name = match.group(1).lower()
            attrs_str = match.group(2) or ""
            attrs = _parse_attrs(attrs_str)
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
            if body:
                if tool_name == "command":
                    attrs["cmd"] = body
                elif tool_name == "subagent":
                    if "goal" not in attrs and "task" not in attrs:
                        attrs["goal"] = body
                elif tool_name == "edit":
                    old_m = re.search(r'<old>(.*?)</old>', body, re.DOTALL | re.IGNORECASE)
                    new_m = re.search(r'<new>(.*?)</new>', body, re.DOTALL | re.IGNORECASE)
                    if old_m:
                        attrs["old"] = old_m.group(1)
                    if new_m:
                        attrs["new"] = new_m.group(1)
                    elif not old_m and "new" not in attrs:
                        attrs["new"] = body
            raw_matches.append((match.start(), match.end(), tool_name, attrs))

        raw_matches.sort(key=lambda m: (m[0], -m[1]))

        non_overlapping = []
        last_end = 0
        for start, end, tool_name, attrs in raw_matches:
            if start >= last_end:
                non_overlapping.append((start, end, tool_name, attrs))
                last_end = end

        segments: List[Tuple[str, Any]] = []
        pos = 0
        for start, end, tool_name, attrs in non_overlapping:
            if start > pos:
                raw_chunk = text[pos:start]
                clean_chunk = self.strip_xml_tool_calls(raw_chunk).strip()
                if clean_chunk:
                    segments.append(("text", clean_chunk))
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

    def execute_tool(self, tool_name: str, args: Dict[str, str]) -> Dict[str, Any]:
        name = tool_name.lower().strip()

        mode = getattr(self.ctx, "mode", "auto")
        if mode == "plan" and name in ("write", "edit", "command"):
            return {
                "success": False,
                "error": f"PLAN MODE ACTIVE: Modification tool '{name}' is disabled in Plan mode. Propose the planned changes in text, or switch to Auto or Code mode to execute.",
            }
        should_ask = (mode == "ask" and name in ("write", "edit", "command")) or (mode == "code" and name == "command")
        if should_ask and self.ask_handler:
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
                path = args.get("path") or args.get("file") or args.get("target") or ""
                lines = args.get("lines")
                if not path:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'path'",
                        "missing": "path",
                        "example": '<tool:read path="filename.ext" />',
                    }
                    if self.on_tool_start:
                        self.on_tool_start("read", "")
                    if self.on_tool_finish:
                        self.on_tool_finish("read", "", err_res)
                    return err_res
                if self.on_tool_start:
                    self.on_tool_start("read", path)
                res = tool_read(self.ctx, path, lines)
                if self.on_tool_finish:
                    self.on_tool_finish("read", path, res)
                return res

            elif name == "edit":
                path = args.get("path") or args.get("file") or args.get("target") or ""
                old = args.get("old", "")
                new = args.get("new", "")
                if not path:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'path'",
                        "missing": "path",
                        "example": '<tool:edit path="file.ext"><old>exact text</old><new>replacement</new></tool:edit>',
                    }
                    if self.on_tool_start:
                        self.on_tool_start("edit", "")
                    if self.on_tool_finish:
                        self.on_tool_finish("edit", "", err_res)
                    return err_res
                if not old:
                    err_res = {
                        "success": False,
                        "error": "missing <old> snippet to replace",
                        "missing": "old snippet (<old>...</old>)",
                        "example": '<tool:edit path="' + path + '"><old>exact text to replace</old><new>replacement</new></tool:edit>',
                    }
                    if self.on_tool_start:
                        self.on_tool_start("edit", path)
                    if self.on_tool_finish:
                        self.on_tool_finish("edit", path, err_res)
                    return err_res
                if self.on_tool_start:
                    self.on_tool_start("edit", path)
                res = tool_edit(self.ctx, path, old, new)
                if self.on_tool_finish:
                    self.on_tool_finish("edit", path, res)
                return res

            elif name == "write":
                path = args.get("path") or args.get("file") or args.get("target") or ""
                content = args.get("content", "")
                if not path:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'path'",
                        "missing": "path",
                        "example": '<tool:write path="filename.ext">content</tool:write>',
                    }
                    if self.on_tool_start:
                        self.on_tool_start("write", "")
                    if self.on_tool_finish:
                        self.on_tool_finish("write", "", err_res)
                    return err_res
                if not content:
                    err_res = {
                        "success": False,
                        "error": "missing file content to write",
                        "missing": "content",
                        "example": '<tool:write path="' + path + '">file content here</tool:write>',
                    }
                    if self.on_tool_start:
                        self.on_tool_start("write", path)
                    if self.on_tool_finish:
                        self.on_tool_finish("write", path, err_res)
                    return err_res
                if self.on_tool_start:
                    self.on_tool_start("write", path)
                res = tool_write(self.ctx, path, content)
                if self.on_tool_finish:
                    self.on_tool_finish("write", path, res)
                return res

            elif name == "ls":
                path = args.get("path") or args.get("dir") or "."
                if self.on_tool_start:
                    self.on_tool_start("ls", path)
                res = tool_ls(self.ctx, path)
                if self.on_tool_finish:
                    self.on_tool_finish("ls", path, res)
                return res

            elif name == "glob":
                pattern = args.get("pattern", "*")
                dir_path = args.get("dir", ".")
                if self.on_tool_start:
                    self.on_tool_start("glob", pattern)
                res = tool_glob(self.ctx, pattern, dir_path)
                if self.on_tool_finish:
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
                    if self.on_tool_start:
                        self.on_tool_start("search", "")
                    if self.on_tool_finish:
                        self.on_tool_finish("search", "", err_res)
                    return err_res
                if self.on_tool_start:
                    self.on_tool_start("search", query)
                res = tool_search(self.ctx, query, path)
                if self.on_tool_finish:
                    self.on_tool_finish("search", query, res)
                return res

            elif name == "command":
                cmd = args.get("cmd") or args.get("command") or ""
                if not cmd:
                    err_res = {
                        "success": False,
                        "error": "missing required attribute 'cmd'",
                        "missing": "cmd",
                        "example": '<tool:command cmd="command_line" />',
                    }
                    if self.on_tool_start:
                        self.on_tool_start("command", "")
                    if self.on_tool_finish:
                        self.on_tool_finish("command", "", err_res)
                    return err_res
                if self.on_tool_start:
                    self.on_tool_start("command", cmd)
                res = tool_command(self.ctx, cmd)
                if self.on_tool_finish:
                    self.on_tool_finish("command", cmd, res)
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
                    if self.on_tool_start:
                        self.on_tool_start("web", "")
                    if self.on_tool_finish:
                        self.on_tool_finish("web", "", err_res)
                    return err_res
                if self.on_tool_start:
                    self.on_tool_start("web", target)
                res = tool_web(self.ctx, target)
                if self.on_tool_finish:
                    self.on_tool_finish("web", target, res)
                return res

            elif name in ("scratch", "todo", "tasks"):
                action = args.get("action", "add")
                note = args.get("note", "")
                target_str = f"{action}: {note}" if note else action
                if self.on_tool_start:
                    self.on_tool_start("todo", target_str)
                res = tool_scratch(self.ctx, action, note)
                if self.on_todo_updated:
                    self.on_todo_updated(res.get("visible_steps", []))
                if self.on_tool_finish:
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

                if self.on_tool_start:
                    self.on_tool_start("ask", question)

                answer = ""
                if self.ask_handler:
                    answer = self.ask_handler(question, options)
                else:
                    answer = "Interactive confirmation modal unavailable"

                res = {"success": True, "question": question, "answer": answer}
                if self.on_tool_finish:
                    self.on_tool_finish("ask", question, res)
                return res

            elif name in ("bugs", "tool_bugs"):
                if self.on_tool_start:
                    self.on_tool_start("bugs", "")
                res = tool_bugs(self.ctx)
                if self.on_tool_finish:
                    self.on_tool_finish("bugs", "", res)
                return res

            elif name in ("code_search", "codesearch", "symbol_search"):
                query = args.get("query", "")
                if self.on_tool_start:
                    self.on_tool_start("code_search", query)
                res = tool_code_search(self.ctx, query)
                if self.on_tool_finish:
                    self.on_tool_finish("code_search", query, res)
                return res

            elif name == "screenshot":
                target = args.get("target", "active_window")
                if self.on_tool_start:
                    self.on_tool_start("screenshot", target)
                res = tool_screenshot(self.ctx, target)
                if self.on_tool_finish:
                    self.on_tool_finish("screenshot", target, res)
                return res

            elif name == "vision":
                image_path = args.get("image_path") or args.get("path", "")
                question = args.get("question", "")
                if self.on_tool_start:
                    self.on_tool_start("vision", image_path)
                res = tool_vision(self.ctx, image_path, question)
                if self.on_tool_finish:
                    self.on_tool_finish("vision", image_path, res)
                return res

            elif name == "mcps":
                if self.on_tool_start:
                    self.on_tool_start("mcps", "")
                res = self._mcps_status()
                if self.on_tool_finish:
                    self.on_tool_finish("mcps", "", res)
                return res

            elif name == "subagent":
                # Subagents temporarily removed: tell the model to do the
                # work itself instead of failing silently.
                res = {"success": False, "error": "Subagents are temporarily disabled. Do the task yourself using your own tools (read/ls/search/edit/write/command)."}
                if self.on_tool_finish:
                    self.on_tool_finish("subagent", args.get("name", ""), res)
                return res

            else:
                dyn = self._dynamic_mcp_tool(name)
                if dyn:
                    sname, mcp_tool = dyn
                    if self.on_tool_start:
                        self.on_tool_start(name, "")
                    try:
                        res = self.mcp.call_tool(sname, mcp_tool, args)
                    except Exception as e:
                        res = {"success": False, "error": str(e)}
                    if self.on_tool_finish:
                        self.on_tool_finish(name, "", res)
                    return res

            return {"success": False, "error": f"Unknown tool: {name}"}
        except Exception as e:
            err_res = {"success": False, "error": str(e)}
            if self.on_tool_finish:
                self.on_tool_finish(name, str(args.get("path") or args.get("cmd") or ""), err_res)
            return err_res


    def strip_xml_tool_calls(self, text: str) -> str:
        if not text:
            return ""
        clean = re.sub(
            r'<\|tool_call>call:\w+(?:\s+[^>]*?)?(?:\s*/>|>(.*?)</\|tool_call>)',
            '',
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        clean = re.sub(r'<tool:\w+(?:\s+[^>]*?)?\s*/>', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<tool:(\w+)(?:\s+[^>]*?)?>.*?</tool:\1>', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<tool_call(?:\s+[^>]*?)?(?:\s*/>|>.*?</tool_call>)', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<tool:\w+[^>]*>.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<\|tool_call.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<tool_call.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<(?:tool:?\w*|\|tool_call\w*|tool_call\w*)[^>]*$', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'</?tool:\w+[^>]*>', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'</?\|?tool_call[^>]*>', '', clean, flags=re.IGNORECASE)
        return clean.strip()

    def format_tool_result_for_llm(self, tool_name: str, res: Dict[str, Any]) -> str:
        name = tool_name.lower().strip()

        if not res.get("success", True) or "error" in res:
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
        elif name == "command":
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
        elif name == "mcps":
            for s in res.get("servers", []):
                lines.append(s)
            if res.get("note"):
                lines.append(res["note"])
        else:
            import json
            clean_res = {k: v for k, v in res.items() if k not in ("diff_entries",)}
            lines.append(json.dumps(clean_res, indent=2, ensure_ascii=False))

        lines.append('</tool_response>')
        return "\n".join(lines)


def get_agent_system_prompt(workdir: str = ".", mode: str = "auto", vision: bool = False) -> str:
    import os
    abs_work = os.path.abspath(workdir)
    mode_clean = (mode or "auto").strip().lower()

    if mode_clean == "plan":
        mode_section = (
            "CURRENT EXECUTION MODE: PLAN\n"
            "- In PLAN mode, you are a software architect. Your goal is ONLY to analyze, research, explore (read/ls/search/web), and formulate a comprehensive step-by-step implementation plan.\n"
            "- You MUST NOT write, edit, or modify any project code files in PLAN mode! Do NOT call <tool:write> or <tool:edit> for source files.\n"
            "- DO NOT create dummy, placeholder, or arbitrary files (such as index.html, index.py, index.js).\n"
            "- When your plan is ready, summarize it clearly for the user and finish the turn."
        )
    elif mode_clean == "code":
        mode_section = (
            "CURRENT EXECUTION MODE: CODE\n"
            "- In CODE mode, you are an expert implementation engineer. Your goal is to write clean, complete production code, perform edits, run tests, and fix bugs based on the user's instructions and previous plans.\n"
            "- DO NOT create arbitrary, unrequested dummy or placeholder files (such as index.html, index.py, index.js) unless the user explicitly requested that specific file or technology stack.\n"
            "- Focus directly on implementing the exact project files required for the task."
        )
    else:
        mode_section = (
            "CURRENT EXECUTION MODE: AUTO\n"
            "- In AUTO mode, you autonomously inspect, plan, write code, run tests, and verify results end-to-end.\n"
            "- DO NOT invent arbitrary dummy files (e.g. index.html) unless required by the task or user."
        )

    return (
        f"\n\n### AUTONOMOUS CODE AGENT ({mode_clean.upper()} MODE) IN WORKSPACE: `{abs_work}`\n"
        f"{mode_section}\n\n"
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
        "11. Plan/Todo Tracker: <tool:todo action=\"add\" note=\"step description\" />\n"
        "12. MCP Servers: <tool:mcps /> (lists configured MCP servers; connected ones unlock <server>_<tool>)\n"
        + (
            "13. Screenshot: <tool:screenshot target=\"active_window\" /> (captures a window to a PNG file)\n"
            "14. Vision: <tool:vision image_path=\"path.png\" question=\"what to check\" /> (attaches the image to your next request)\n"
            if vision else ""
        ) +
        "\nEXECUTION DIRECTIVES:\n"
        "- INTERLEAVED EXPLANATIONS & ACTIONS: Always include a brief 1-2 sentence explanation before and between your tool actions (e.g. state what you are inspecting, what you found, or why you are making a change), followed by the tool call. This allows the user to follow your step-by-step progress chronologically.\n"
        "- STRUCTURE: Write naturally as: [Brief reasoning/finding] -> <tool:...> -> [Next finding/decision] -> <tool:...> -> [Final outcome]. Do NOT dump a wall of tools without explanations.\n"
        "- Subagents are temporarily disabled: never emit <tool:subagent>. If the user asks for delegation, do the work yourself with your own tools and say so briefly.\n"
        "- Be fast, direct, and proactive: execute the necessary reads, edits, writes, and commands to complete the user request.\n"
        "- Always verify your work with `<tool:bugs />` or `<tool:command>` to ensure zero errors.\n"
    )
