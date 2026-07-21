import os
import json
from typing import List, Dict, Any


def wakeup_subagents(
    subagents: List[Dict[str, Any]] = None,
    prompt: str = None,
    agent_instance=None,
    cwd=None,
) -> str:
    if not agent_instance or not getattr(agent_instance, "model", None):
        return "Error: Cannot spawn sub-agents. Model instance not available."

    if not subagents:
        if prompt is not None:
            return "Error: You passed 'prompt' instead of 'subagents'. The wakeup_subagents tool REQUIRES a 'subagents' parameter which must be a JSON array of objects, e.g. [{'name': 'Backend', 'task': '...'}]. Please correct your tool call and try again."
        return "Error: No sub-agents specified. You must provide the 'subagents' parameter."

    from ..subagent_runner import (
        run_subagents_sequential,
        SubAgentConfig,
        SubAgentResult,
    )

    configs = []
    for sa in subagents:
        if not isinstance(sa, dict):
            continue
        name = sa.get("name") or "UnnamedAgent"
        task = sa.get("task") or ""
        thinking_level = sa.get("thinking_level") or ""
        context_files = sa.get("context_files") or []
        configs.append(
            SubAgentConfig(
                name=name,
                task=task,
                thinking_level=thinking_level,
                context_files=context_files,
            )
        )

    results = run_subagents_sequential(configs, agent_instance, cwd or ".")

    done_count = sum(1 for r in results if r.status == "done")
    parts = []
    for r in results:
        sym = "✓" if r.status == "done" else "✗"
        files = f"({', '.join(r.files_edited)})" if r.files_edited else ""
        parts.append(f"{r.name}{sym}{files}")
    return f"● Subagents ({done_count}/{len(results)} ok): {', '.join(parts)}"
