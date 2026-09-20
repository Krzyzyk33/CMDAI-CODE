from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Callable, Optional
from .models import SubagentTask, SubagentProgress, SubagentResult

SUBAGENT_AVAILABLE_TOOLS = [
    "read",
    "edit",
    "write",
    "command",
    "ls",
    "glob",
    "search",
    "scratch",
    "ask",
    "web",
    "code_search",
    "bugs",
]

def _format_tool_snippet(cname: str, res: Dict[str, Any]) -> str:
    if not res.get("success", True) or "error" in res:
        return f"ERROR: {res.get('error') or res.get('stderr') or 'Execution failed'}"
    if cname == "read":
        return str(res.get("content", ""))
    elif cname == "ls":
        entries = res.get("entries", [])
        if not entries:
            return "(Directory is empty)"
        lines = []
        for e in entries:
            t = "[DIR] " if e.get("type") == "dir" else "      "
            lines.append(f"{t}{e.get('name')}")
        return "\n".join(lines)
    elif cname == "glob":
        matches = res.get("matches", [])
        if not matches:
            return "(No files matched pattern)"
        return "\n".join(matches)
    elif cname in ("search", "code_search"):
        if "symbols" in res:
            syms = res.get("symbols", [])
            return "\n".join(f"{s.get('filepath')}:{s.get('start_line')} [{s.get('symbol_type')}] {s.get('signature')}" for s in syms) or "(No symbols found)"
        results = res.get("results", [])
        if not results:
            return "(No text matches found)"
        return "\n".join(f"{m.get('file')}:{m.get('line')}: {m.get('content')}" for m in results[:25])
    elif cname == "command":
        out = (res.get("stdout") or "") + "\n" + (res.get("stderr") or "")
        return out.strip() or f"Command finished with return code {res.get('returncode', 0)}"
    elif cname == "web":
        results = res.get("results", [])
        if results:
            return "\n".join(f"{r.get('title')}: {r.get('snippet')} ({r.get('url')})" for r in results[:5])
        return str(res.get("content", ""))[:1500] or "Done"
    elif cname in ("edit", "write"):
        return f"Successfully updated {res.get('path', '')}"
    elif cname in ("bugs", "tool_bugs"):
        errors = res.get("errors", {})
        if not errors:
            return "0 syntax errors found."
        lines = []
        for f, errs in errors.items():
            lines.append(f"{f}:")
            for e in errs:
                lines.append(f"  - {e}")
        return "\n".join(lines)
    elif "content" in res:
        return str(res["content"])
    elif "summary" in res:
        return str(res["summary"])
    return "OK"


class SubagentOrchestrator:

    def __init__(self, engine=None):
        self.engine = engine

    def build_task(self, name: str, system: str, goal: str, tools: Optional[List[str]] = None) -> SubagentTask:
        return SubagentTask(
            role=(name or "Subagent").strip()[:60],
            goal=(goal or "").strip(),
            system_prompt=(system or "").strip(),
            tools=list(tools or SUBAGENT_AVAILABLE_TOOLS),
        )

    def is_local_model(self) -> bool:
        if not self.engine:
            return True
        provider = getattr(self.engine, "active_provider", None) or getattr(self.engine, "current_provider", "local_gguf")
        if provider in ("local_gguf", "local", "llama_cpp", "ollama"):
            return True
        return False

    def run_subagent(
        self,
        task: SubagentTask,
        model_call: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
        max_turns: int = 8,
        on_progress: Optional[Callable[[SubagentProgress], None]] = None
    ) -> SubagentResult:
        allowed = set(t.lower() for t in (task.tools or SUBAGENT_AVAILABLE_TOOLS))
        allowed.discard("subagent")
        workdir = task.context.get("workdir", ".") if isinstance(task.context, dict) else "."

        system_content = (
            f"You are {task.role}, a specialized autonomous subagent.\n"
            f"{task.system_prompt}\n\n"
            f"Workspace directory: {workdir}\n"
            "You have full direct access to inspect the project and local filesystem using XML tags:\n"
            "- List Directory: <tool:ls path=\".\" />\n"
            "- Find Files: <tool:glob pattern=\"*.*\" dir=\".\" />\n"
            "- Read File: <tool:read path=\"relative/path.ext\" />\n"
            "- Search Text: <tool:search query=\"term\" path=\".\" />\n"
            "- Web Search: <tool:web query=\"query\" />\n"
            "- Symbol Search: <tool:code_search query=\"symbol\" />\n"
            "- Run Command: <tool:command cmd=\"command\" />\n"
            "- Scratchpad: <tool:scratch action=\"add\" note=\"...\" />\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. You MUST use the tools above to inspect files, search code, and investigate the workspace.\n"
            "2. Never claim you do not have access to files, filesystem, or tools — use <tool:ls path=\".\"/> or <tool:glob pattern=\"*.*\"/> to inspect the files.\n"
            "3. First invoke tools to gather the necessary data. Once you have enough findings, provide a concise, structured final report answering your task.\n"
        )
        history: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": task.goal}
        ]
        findings: List[str] = []
        last_text = ""

        def _emit(status: str, tool: str = "", arg: str = "", thought: str = "") -> None:
            if on_progress:
                try:
                    on_progress(SubagentProgress(
                        role=task.role,
                        status=status,
                        current_tool=tool,
                        tool_arg=arg,
                        thought=thought,
                        goal=task.goal,
                        system_prompt=task.system_prompt,
                        history=list(history),
                        findings=list(findings),
                    ))
                except Exception:
                    pass

        _emit("running", "loading model", "…", f"Subagent {task.role} started.")
        if model_call is None:
            _emit("failed", thought="No model backend wired.")
            return SubagentResult(role=task.role, summary=f"{task.role}: no model backend available.", findings=[], history=history, system_prompt=task.system_prompt)

        import re as _re
        for _ in range(max(1, max_turns)):
            sub_history = list(history)
            if len(sub_history) > 8:
                sub_history = [sub_history[0], sub_history[1]] + sub_history[-6:]
            try:
                text = model_call(sub_history) or ""
            except Exception as e:
                last_text = f"Subagent {task.role} model error: {e}"
                break
            if not text.strip() or any(text.startswith(p) for p in ("⚠", "Connection error:", "API key required")):
                last_text = f"Subagent {task.role} model error: {text or 'empty response'}"
                break
            last_text = text
            calls = self._extract_calls(text)
            history.append({"role": "assistant", "content": last_text})
            if not calls:
                break
            done = False
            for cname, cargs in calls:
                if cname not in allowed:
                    history.append({"role": "user", "content": f"[tool {cname} not allowed for subagents]"})
                    continue
                arg_summary = str(cargs.get("path") or cargs.get("query") or cargs.get("cmd") or cargs.get("pattern") or "")[:80]
                _emit("running", cname, arg_summary)
                try:
                    res = tool_executor(cname, cargs) if tool_executor else {"success": False, "error": "no executor"}
                except Exception as e:
                    res = {"success": False, "error": str(e)}
                ok = bool(res.get("success", True)) and "error" not in res
                snippet = _format_tool_snippet(cname, res)[:2000]
                history.append({"role": "user", "content": f"[tool:{cname} result]\n{snippet}"})
                if not ok and cname in ("write", "edit"):
                    done = True
                    break
            _emit("running", "thinking", "processing…")
            if done:
                break

        for line in last_text.splitlines():
            s = line.strip().lstrip("-*• ").strip()
            if len(s) > 20:
                findings.append(s[:200])
        findings = findings[:10]
        tokens_in = sum(max(1, len(str(m.get("content", "")).split()) * 4 // 3) for m in history if m.get("role") != "assistant")
        tokens_out = sum(max(1, len(str(m.get("content", "")).split()) * 4 // 3) for m in history if m.get("role") == "assistant")
        is_model_err = bool(
            not last_text.strip()
            or any(last_text.startswith(p) for p in (f"Subagent {task.role} model error:", "Failed:", "Error:", "⚠", "Connection error:"))
        )
        if is_model_err:
            _emit("failed", thought=last_text or f"Subagent {task.role} failed.")
        else:
            _emit("completed", "", "", f"Subagent {task.role} finished.")
        return SubagentResult(
            role=task.role,
            summary=last_text[:2000] if last_text else f"{task.role} produced no output.",
            findings=findings,
            history=history,
            system_prompt=task.system_prompt,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    def _extract_calls(self, text: str) -> List[Any]:
        import re as _re
        if not text:
            return []

        def _parse_attrs(attrs_str: str) -> Dict[str, str]:
            if not attrs_str:
                return {}
            matches = _re.findall(r'(\w+)=(?:["\'](.*?)["\']|([^\s>]+))', attrs_str, _re.DOTALL)
            attrs = {}
            for k, v1, v2 in matches:
                attrs[k.lower()] = v1 if (v1 is not None and v1 != "") else v2
            return attrs

        raw_matches = []
        body_pattern = _re.compile(r'<tool:(\w+)(?:\s+([^>]*?))?>(.*?)</tool:\1>', _re.DOTALL | _re.IGNORECASE)
        for match in body_pattern.finditer(text):
            tool_name = match.group(1).lower()
            attrs_str = match.group(2) or ""
            body = match.group(3).strip()
            attrs = _parse_attrs(attrs_str)
            if tool_name == "command" and "cmd" not in attrs:
                attrs["cmd"] = body
            elif tool_name == "scratch" and "note" not in attrs:
                attrs["note"] = body
            elif tool_name == "web" and "query" not in attrs:
                attrs["query"] = body
            elif tool_name == "ask" and "question" not in attrs:
                attrs["question"] = body
            elif tool_name == "write" and "content" not in attrs:
                attrs["content"] = body
            elif tool_name == "read" and "path" not in attrs:
                attrs["path"] = body
            elif tool_name in ("search", "code_search") and "query" not in attrs:
                attrs["query"] = body
            elif tool_name == "edit":
                old_m = _re.search(r'<old>(.*?)</old>', body, _re.DOTALL | _re.IGNORECASE)
                new_m = _re.search(r'<new>(.*?)</new>', body, _re.DOTALL | _re.IGNORECASE)
                if old_m:
                    attrs["old"] = old_m.group(1)
                if new_m:
                    attrs["new"] = new_m.group(1)
                elif not old_m and "new" not in attrs:
                    attrs["new"] = body
            raw_matches.append((match.start(), match.end(), tool_name, attrs))

        single_tag_pattern = _re.compile(r'<tool:(\w+)(?:\s+([^>]*?))?\s*/?>', _re.DOTALL | _re.IGNORECASE)
        for match in single_tag_pattern.finditer(text):
            tool_name = match.group(1).lower()
            attrs_str = match.group(2) or ""
            attrs = _parse_attrs(attrs_str)
            raw_matches.append((match.start(), match.end(), tool_name, attrs))

        raw_matches.sort(key=lambda m: (m[0], -m[1]))
        non_overlapping = []
        last_end = 0
        for start, end, tool_name, attrs in raw_matches:
            if start >= last_end:
                non_overlapping.append((tool_name, attrs))
                last_end = end

        return non_overlapping

    def execute_tasks(
        self,
        tasks: List[SubagentTask],
        on_progress: Optional[Callable[[SubagentProgress], None]] = None,
        is_local: Optional[bool] = None,
        model_call: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
        max_turns: int = 8,
        tool_executor_factory: Optional[Callable[[str], Callable[[str, Dict[str, str]], Dict[str, Any]]]] = None,
    ) -> List[SubagentResult]:
        if is_local is None:
            is_local = self.is_local_model()

        if is_local:
            return self.run_sequential(tasks, on_progress, model_call, tool_executor, max_turns, tool_executor_factory)
        else:
            return self.run_concurrent(tasks, on_progress, model_call, tool_executor, max_turns, tool_executor_factory)

    def run_sequential(
        self,
        tasks: List[SubagentTask],
        on_progress: Optional[Callable[[SubagentProgress], None]] = None,
        model_call: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
        max_turns: int = 8,
        tool_executor_factory: Optional[Callable[[str], Callable[[str, Dict[str, str]], Dict[str, Any]]]] = None,
    ) -> List[SubagentResult]:
        if on_progress:
            for t in tasks:
                on_progress(SubagentProgress(
                    role=t.role,
                    status="pending",
                    current_tool="loading model",
                    tool_arg="waiting in queue...",
                    thought="Waiting for local execution slot...",
                    goal=t.goal,
                    system_prompt=t.system_prompt,
                ))

        results: List[SubagentResult] = []
        for t in tasks:
            ex = tool_executor_factory(t.role) if tool_executor_factory else tool_executor
            res = self.run_subagent(t, model_call, ex, max_turns, on_progress)
            results.append(res)
        return results

    def run_concurrent(
        self,
        tasks: List[SubagentTask],
        on_progress: Optional[Callable[[SubagentProgress], None]] = None,
        model_call: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
        max_turns: int = 8,
        tool_executor_factory: Optional[Callable[[str], Callable[[str, Dict[str, str]], Dict[str, Any]]]] = None,
    ) -> List[SubagentResult]:
        results: List[SubagentResult] = []
        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as executor:
            futures = {
                executor.submit(self.run_subagent, t, model_call,
                                tool_executor_factory(t.role) if tool_executor_factory else tool_executor,
                                max_turns, on_progress): t
                for t in tasks
            }
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    results.append(res)
                except Exception as e:
                    task = futures[fut]
                    if on_progress:
                        try:
                            on_progress(SubagentProgress(role=task.role, status="failed", thought=f"Failed: {e}", goal=task.goal, system_prompt=task.system_prompt, error=str(e), success=False))
                        except Exception:
                            pass
                    results.append(SubagentResult(
                        role=task.role,
                        summary=f"Failed: {e}",
                        findings=[f"Error: {e}"],
                        history=[],
                        system_prompt=task.system_prompt
                    ))
        return results
