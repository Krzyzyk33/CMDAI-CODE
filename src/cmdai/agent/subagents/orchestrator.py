import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Callable, Optional
from .models import SubagentTask, SubagentProgress, SubagentResult

# Subagents get every tool the lead agent has, except "subagent" itself (see
# run_subagent, which discards it from the allowlist). "agent_note" is added
# here because it only makes sense from inside a subagent.
SUBAGENT_AVAILABLE_TOOLS = [
    "read",
    "edit",
    "write",
    "command",
    "ls",
    "glob",
    "search",
    "scratch",
    "todo",
    "tasks",
    "ask",
    "web",
    "code_search",
    "bugs",
    "mcps",
    "screenshot",
    "vision",
    "agent_note",
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
    elif cname == "agent_note":
        note = str(res.get("note", "")).strip()
        return f"Note recorded for the lead agent: {note}" if note else "Note recorded for the lead agent."
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
        model_call: Optional[Callable[[List[Dict[str, Any]], SubagentTask], str]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
        max_turns: int = 25,
        on_progress: Optional[Callable[[SubagentProgress], None]] = None
    ) -> SubagentResult:
        allowed = set(t.lower() for t in (task.tools or SUBAGENT_AVAILABLE_TOOLS))
        allowed.discard("subagent")
        allowed.discard("spawn_subagent")
        allowed.discard("task")
        allowed.discard("tasks")
        allowed.discard("bg_task")
        allowed.discard("background_task")
        raw_workdir = task.context.get("workdir", ".") if isinstance(task.context, dict) else "."
        if not raw_workdir or raw_workdir == ".":
            raw_workdir = os.getcwd()
        workdir = os.path.abspath(raw_workdir)

        system_content = (
            f"You are {task.role}, a specialized autonomous subagent.\n"
            f"{task.system_prompt}\n\n"
            f"Workspace directory: {workdir}\n"
            "CRITICAL WORKSPACE CONSTRAINT:\n"
            f"- You MUST work exclusively within this workspace directory: {workdir}\n"
            "- All file paths (reads, edits, writes, globs, searches) are strictly relative to this workspace directory.\n"
            "- NEVER read, inspect, or reference files outside this workspace directory.\n\n"
            "You have full direct access to inspect the project and local filesystem using XML tags:\n"
            "- List Directory: <tool:ls path=\".\" />\n"
            "- Find Files: <tool:glob pattern=\"*.*\" dir=\".\" />\n"
            "- Read File: <tool:read path=\"relative/path.ext\" />\n"
            "- Search Text: <tool:search query=\"term\" path=\".\" />\n"
            "- Web Search: <tool:web query=\"query\" />\n"
            "- Symbol Search: <tool:code_search query=\"symbol\" />\n"
            "- Run Command: <tool:command cmd=\"command\" />\n"
            "- Scratchpad: <tool:scratch action=\"add\" note=\"...\" />\n"
            "- Note To Lead Agent: <tool:agent_note text=\"...\" />\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. WORK LIKE A REAL AUTONOMOUS AGENT IN A CHAT: Converse and explain your actions just like in a normal chat. Always write explanations, thoughts, and commentary before your tool calls (e.g. what you are going to check, what you just discovered from the previous tool, and what you plan to do next).\n"
            "2. EXECUTE REAL TOOLS FOR EVERY ACTION: Whenever you state that you are going to check, list, read, or search something, you MUST IMMEDIATELY EMIT the corresponding <tool:...> XML tag in that exact turn! NEVER write about checking a file or directory without actually executing the tool. NEVER imagine, assume, or fabricate tool results — wait for the real result.\n"
            "3. DIRECT INSPECTION: You MUST use <tool:read> and <tool:glob> to inspect files directly. Never search disk paths or write PowerShell probe scripts when <tool:read> can read the file.\n"
            "4. NO GUI APPS OR BROWSERS: NEVER attempt to launch, search for, or automate external GUI applications, web browsers (such as msedge, chrome, firefox), or web servers. You cannot interact with graphical windows.\n"
            "5. NEVER SPAWN SUBAGENTS OR BACKGROUND TASKS: You CANNOT spawn other subagents or background tasks. NEVER emit <tool:subagent> tags or background commands. Execute your assignment yourself directly.\n"
            "6. Never claim you do not have access to files, filesystem, or tools — use <tool:ls path=\".\"/> or <tool:glob pattern=\"*.*\"/> to inspect the files.\n"
            "7. FINAL REPORT: Once you have gathered sufficient findings, conclude with a thorough, well-structured final report answering the task with concrete recommendations, line numbers, and conclusions.\n"
            "8. Send <tool:agent_note text=\"...\" /> as soon as you find something the lead agent should know about, so it is not waiting for your report at the end.\n"
        )
        history: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": task.goal}
        ]
        findings: List[str] = []
        last_text = ""
        # Tool activity in call order, as real dicts. The TUI replays these into
        # the same ToolBlock widgets the lead agent's chat uses.
        events: List[Dict[str, Any]] = []

        def _emit(status: str, tool: str = "", arg: str = "", thought: str = "", note: str = "") -> None:
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
                        note=note,
                        events=list(events) + list(getattr(task, "events", []) or []),
                    ))
                except Exception:
                    pass

        _emit("running", "loading model", "…", f"Subagent {task.role} started.")
        import time as _time
        _t0 = _time.monotonic()
        if model_call is None:
            _emit("failed", thought="No model backend wired.")
            return SubagentResult(role=task.role, summary=f"{task.role}: no model backend available.", findings=[], history=history, system_prompt=task.system_prompt, notes=list(task.notes), success=False, status="failed")

        # One argument or two. The protocol carries the task so a backend with
        # server-side tools can attribute what ran; a backend from before that
        # still takes only the history. Checked once, not per turn, and by
        # signature rather than by catching TypeError - which would also
        # swallow a real bug raised inside the call.
        _takes_task = False
        if model_call is not None:
            try:
                import inspect as _inspect
                _takes_task = len(_inspect.signature(model_call).parameters) >= 2
            except (TypeError, ValueError):
                _takes_task = False

        was_aborted = False
        failed_tool_counts = {}

        for _turn_idx in range(max(1, max_turns)):
            if getattr(task, "abort_requested", False):
                was_aborted = True
                last_text = "Subagent was cancelled."
                break

            sub_history = list(history)
            if len(sub_history) > 20:
                # Keep the system prompt, the task, and a longer tail of tool
                # results. The old cap of 8 (last 6) threw away most tool output,
                # so a subagent doing real work kept re-reading the same files.
                sub_history = [sub_history[0], sub_history[1]] + sub_history[-18:]
            try:
                # The task travels with the call, so a model backend that runs
                # its own tools server-side can attribute what ran to the
                # subagent it ran for. A bare history cannot say that, which is
                # how server-side tool calls used to vanish: the loop only
                # understood `<tool:...>` tags in the text, and a model that
                # calls native tools instead emits none.
                text = (model_call(sub_history, task) if _takes_task
                        else model_call(sub_history)) or ""
            except Exception as e:
                last_text = f"Subagent {task.role} model error: {e}"
                break

            if getattr(task, "abort_requested", False):
                was_aborted = True
                last_text = "Subagent was cancelled."
                break

            if not text.strip() or any(text.startswith(p) for p in ("⚠", "Connection error:", "API key required")):
                last_text = f"Subagent {task.role} model error: {text or 'empty response'}"
                break
            last_text = text
            calls = self._extract_calls(text)
            history.append({"role": "assistant", "content": last_text})
            if not calls:
                if (
                    _turn_idx == 0
                    and max_turns > 1
                    and not events
                    and not any(
                        isinstance(ev, dict)
                        and bool(str(ev.get("tool", "") or "").strip())
                        and ev.get("type") not in ("text", "thinking")
                        for ev in (getattr(task, "events", None) or [])
                    )
                ):
                    history.append({
                        "role": "user",
                        "content": (
                            "Start by inspecting the workspace files immediately. Emit <tool:ls path=\".\" /> now."
                        ),
                    })
                    continue
                break
            done = False
            for cname, cargs in calls:
                if getattr(task, "abort_requested", False):
                    was_aborted = True
                    last_text = "Subagent was cancelled."
                    done = True
                    break

                if cname in ("subagent", "spawn_subagent", "task", "tasks", "bg_task", "background_task"):
                    history.append({"role": "user", "content": f"[ERROR: Tool '{cname}' is forbidden for subagents. You cannot spawn subagents or background tasks. Execute your task directly using read/edit/write/search.]"})
                    continue
                tl = cname.lower().strip()
                if tl in ("shell", "terminal", "bash", "cmd", "sh", "run_command", "exec"):
                    cname = "command"
                elif tl in ("file_read", "read_file"):
                    cname = "read"
                elif tl in ("file_write", "write_file"):
                    cname = "write"
                elif tl in ("file_edit", "edit_file"):
                    cname = "edit"
                if cname not in allowed:
                    history.append({"role": "user", "content": f"[tool {cname} not allowed for subagents]"})
                    continue
                arg_summary = str(
                    cargs.get("path")
                    or cargs.get("file")
                    or cargs.get("filePath")
                    or cargs.get("target")
                    or cargs.get("command")
                    or cargs.get("cmd")
                    or cargs.get("CommandLine")
                    or cargs.get("query")
                    or cargs.get("pattern")
                    or cargs.get("glob")
                    or cargs.get("text")
                    or cargs.get("note")
                    or (list(cargs.values())[0] if cargs and isinstance(list(cargs.values())[0], str) else "")
                    or ""
                )[:80]
                _emit("running", cname, arg_summary)
                try:
                    res = tool_executor(cname, cargs) if tool_executor else {"success": False, "error": "no executor"}
                except Exception as e:
                    res = {"success": False, "error": str(e)}
                ok = bool(res.get("success", True)) and "error" not in res
                events.append({
                    "tool": cname,
                    "args": dict(cargs or {}),
                    "target": arg_summary,
                    "result": dict(res) if isinstance(res, dict) else {"content": str(res)},
                    "ok": ok,
                })
                if cname == "agent_note" and ok:
                    # The executor appended the text to task.notes; surface it in
                    # the progress stream so the TUI marker and the modal can show
                    # the note without polling the task object.
                    _emit("running", "agent_note", str(res.get("note", ""))[:80], note=str(res.get("note", "")))
                snippet = _format_tool_snippet(cname, res)[:2000]
                history.append({"role": "user", "content": f"[tool:{cname} result]\n{snippet}"})
                if not ok:
                    call_sig = f"{cname}:{arg_summary}"
                    failed_tool_counts[call_sig] = failed_tool_counts.get(call_sig, 0) + 1
                    if failed_tool_counts[call_sig] >= 2:
                        history.append({"role": "user", "content": f"[tool:{cname} repeated error: '{arg_summary}' failed repeatedly. Do not retry this command. Provide your evaluation based on the available files.]"})
                        done = True
                        break
                    if cname in ("write", "edit"):
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

        if was_aborted or getattr(task, "abort_requested", False):
            _emit("cancelled", thought="Subagent was cancelled by user.")
            res_status = "cancelled"
            is_success = False
        elif is_model_err:
            _emit("failed", thought=last_text or f"Subagent {task.role} failed.")
            res_status = "failed"
            is_success = False
        else:
            _emit("completed", "", "", f"Subagent {task.role} finished.")
            res_status = "completed"
            is_success = True

        return SubagentResult(
            role=task.role,
            summary=last_text[:8000] if last_text else f"{task.role} produced no output.",
            findings=findings,
            history=history,
            system_prompt=task.system_prompt,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            notes=list(task.notes),
            # Locally executed tools first. The window pairs parsed `<tool:...>`
            # calls with events by position, so the locally produced events
            # must stay where the text put them; server-side outcomes recorded
            # on the task trail behind, and render as their own blocks.
            events=list(events) + list(task.events),
            elapsed=max(0.0, _time.monotonic() - _t0),
            success=is_success,
            status=res_status,
        )

    def _extract_calls(self, text: str) -> List[Any]:
        """Tool calls in one subagent turn.

        Delegates to the lead agent's parser instead of keeping a second copy
        of the patterns. That copy only understood <tool:name ...>, so a
        subagent on a local Gemma emitting <|tool_call>call:ls path="." />
        had zero calls found: the raw tag stayed in the text and showed up in
        the chat window verbatim, with the tool never running.
        """
        if not text:
            return []
        from ..runner import _parse_only
        calls = []
        for segment in _parse_only(text):
            if segment and segment[0] == "tool":
                calls.append(segment[1])
        return calls

    def execute_tasks(
        self,
        tasks: List[SubagentTask],
        on_progress: Optional[Callable[[SubagentProgress], None]] = None,
        is_local: Optional[bool] = None,
        model_call: Optional[Callable[[List[Dict[str, Any]], SubagentTask], str]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
        max_turns: int = 25,
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
        model_call: Optional[Callable[[List[Dict[str, Any]], SubagentTask], str]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
        max_turns: int = 25,
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
        model_call: Optional[Callable[[List[Dict[str, Any]], SubagentTask], str]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
        max_turns: int = 25,
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
                    results.append(SubagentResult(
                        role=task.role,
                        summary=f"Failed: {e}",
                        findings=[f"Failed: {e}"],
                        history=[],
                        system_prompt=task.system_prompt,
                        success=False,
                        status="failed",
                    ))
        return results
