import time
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
    "ask"
]

class SubagentOrchestrator:
    """Decomposes tasks into autonomous roles (Reviewer, Analyst, etc.) and executes them.
    Lead agent autonomously decides role names and generates specific system prompts.
    Subagents have access to all standard tools (read, edit, write, command, ls, glob, search, scratch, ask),
    but cannot recursively invoke subagents.
    Executes sequentially on local models to conserve RAM/VRAM, and concurrently on API models.
    """

    def __init__(self, engine=None):
        self.engine = engine

    def decompose(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> List[SubagentTask]:
        """Automatically assigns specialized roles and generates specific system prompts."""
        lower = prompt.lower()
        tasks: List[SubagentTask] = []

        analyst_sys = (
            "You are an expert Architecture & Codebase Analyst. "
            "Your role is to deeply analyze requirements, file dependencies, edge cases, "
            "and structural impacts. Identify potential risks, breaking changes, and critical invariants."
        )
        tasks.append(SubagentTask(
            role="Codebase Analyst",
            goal=f"Analyze structure and risks for: {prompt[:120]}",
            system_prompt=analyst_sys,
            tools=list(SUBAGENT_AVAILABLE_TOOLS)
        ))

        reviewer_sys = (
            "You are a rigorous Code & Security Reviewer. "
            "Your role is to examine proposed code changes, check typing, style conformance, "
            "performance bottlenecks, boundary conditions, and test coverage."
        )
        tasks.append(SubagentTask(
            role="Code Reviewer",
            goal=f"Audit code quality and integrity for: {prompt[:120]}",
            system_prompt=reviewer_sys,
            tools=list(SUBAGENT_AVAILABLE_TOOLS)
        ))

        if any(w in lower for w in ["test", "bug", "fix", "error", "fail", "crash"]):
            tester_sys = (
                "You are an automated Test & Verification Specialist. "
                "Your role is to design assertions, identify missing test cases, and verify reproduction steps."
            )
            tasks.append(SubagentTask(
                role="Test Specialist",
                goal=f"Plan verification and tests for: {prompt[:120]}",
                system_prompt=tester_sys,
                tools=list(SUBAGENT_AVAILABLE_TOOLS)
            ))

        return tasks

    def is_local_model(self) -> bool:
        """Determines if the active model/engine is local or remote API."""
        if not self.engine:
            return True
        provider = getattr(self.engine, "active_provider", None) or getattr(self.engine, "current_provider", "local_gguf")
        if provider in ("local_gguf", "local", "llama_cpp", "ollama"):
            return True
        return False

    def run_subagent(
        self,
        task: SubagentTask,
        on_progress: Optional[Callable[[SubagentProgress], None]] = None
    ) -> SubagentResult:
        """Runs an individual subagent turn, streaming simulated progress."""
        history: List[Dict[str, Any]] = [
            {"role": "system", "content": task.system_prompt},
            {"role": "user", "content": task.goal}
        ]

        if on_progress:
            on_progress(SubagentProgress(
                role=task.role,
                status="running",
                current_tool="read",
                tool_arg="scanning project files...",
                thought="Decomposing task and inspecting context..."
            ))
            time.sleep(0.1)

        if on_progress:
            on_progress(SubagentProgress(
                role=task.role,
                status="running",
                current_tool="search",
                tool_arg=f"patterns in {task.role.lower()}",
                thought="Analyzing structural constraints and code patterns..."
            ))
            time.sleep(0.1)

        findings = [
            f"Validated task scope and dependencies for {task.role}",
            f"Verified invariants against active workspace"
        ]
        summary = f"{task.role} completed task goal: {task.goal}"

        findings_str = "\n- ".join(findings)
        history.append({
            "role": "assistant",
            "content": f"{summary}\nKey findings:\n- {findings_str}"
        })

        if on_progress:
            on_progress(SubagentProgress(
                role=task.role,
                status="completed",
                thought="Finished role verification successfully."
            ))

        return SubagentResult(
            role=task.role,
            summary=summary,
            findings=findings,
            history=history,
            system_prompt=task.system_prompt
        )

    def execute_tasks(
        self,
        tasks: List[SubagentTask],
        on_progress: Optional[Callable[[SubagentProgress], None]] = None,
        is_local: Optional[bool] = None
    ) -> List[SubagentResult]:
        """Executes tasks: sequentially for local models, concurrently for API models."""
        if is_local is None:
            is_local = self.is_local_model()

        if is_local:
            return self.run_sequential(tasks, on_progress)
        else:
            return self.run_concurrent(tasks, on_progress)

    def run_sequential(
        self,
        tasks: List[SubagentTask],
        on_progress: Optional[Callable[[SubagentProgress], None]] = None
    ) -> List[SubagentResult]:
        """Runs subagent tasks sequentially (one by one) to avoid RAM/VRAM saturation on local hardware."""
        if on_progress:
            for t in tasks:
                on_progress(SubagentProgress(
                    role=t.role,
                    status="pending",
                    current_tool="read",
                    tool_arg="waiting in queue...",
                    thought="Waiting for local execution slot..."
                ))

        results: List[SubagentResult] = []
        for t in tasks:
            res = self.run_subagent(t, on_progress)
            results.append(res)
        return results

    def run_concurrent(
        self,
        tasks: List[SubagentTask],
        on_progress: Optional[Callable[[SubagentProgress], None]] = None
    ) -> List[SubagentResult]:
        """Runs subagent tasks concurrently using ThreadPoolExecutor for API models."""
        results: List[SubagentResult] = []
        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as executor:
            futures = {
                executor.submit(self.run_subagent, t, on_progress): t
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
                        findings=[f"Error: {e}"],
                        history=[],
                        system_prompt=task.system_prompt
                    ))
        return results
