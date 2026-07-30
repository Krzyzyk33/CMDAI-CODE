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
            subagents = [{"name": "SubAgent", "task": str(prompt)}]
        else:
            return "Error: No sub-agents specified. You must provide the 'subagents' parameter."

    if isinstance(subagents, str):
        subagents = [{"name": "SubAgent", "task": subagents}]
    elif isinstance(subagents, dict):
        subagents = [subagents]

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
    all_edited_files = set()
    for r in results:
        sym = "✓" if r.status == "done" else "✗"
        files_str = f" [edited: {', '.join(r.files_edited)}]" if r.files_edited else ""
        note_val = str(r.note or "Task completed.")
        note_str = f"\n    Note: {note_val}" if note_val else ""
        parts.append(f"  - Subagent '{r.name}' [{sym}]{files_str}{note_str}")
        if hasattr(r, "files_edited") and r.files_edited:
            for fe in r.files_edited:
                all_edited_files.add(os.path.abspath(fe))

    subagent_data_list = []
    for r, cfg in zip(results, configs):
        subagent_data_list.append({
            "name": r.name,
            "task": cfg.task,
            "thinking_level": cfg.thinking_level,
            "status": r.status,
            "note": str(r.note or ""),
            "files_edited": list(r.files_edited),
        })

    if agent_instance:
        if hasattr(agent_instance, "_last_subagent_edited_files"):
            agent_instance._last_subagent_edited_files = all_edited_files
        agent_instance._fable_tested = True
        if getattr(agent_instance, "context", None):
            agent_instance.context._last_subagents_data = subagent_data_list

    user_prompt_summary = ""
    last_thinking_summary = ""

    if agent_instance and getattr(agent_instance, "context", None):
        messages = getattr(agent_instance.context, "messages", [])
        for m in reversed(messages):
            if not user_prompt_summary and m.get("role") == "user" and m.get("content"):
                user_prompt_summary = str(m.get("content")).strip()
            if not last_thinking_summary and m.get("role") == "assistant" and m.get("thinking"):
                last_thinking_summary = str(m.get("thinking")).strip()
            if user_prompt_summary and last_thinking_summary:
                break

    recap_lines = []
    if user_prompt_summary:
        user_clean = user_prompt_summary.replace("\n", " ").strip()
        if len(user_clean) > 300:
            user_clean = user_clean[:300] + "..."
        recap_lines.append(f"  - User Goal: {user_clean}")
    if last_thinking_summary:
        think_clean = last_thinking_summary.replace("\n", " ").strip()
        if len(think_clean) > 400:
            think_clean = think_clean[:400] + "..."
        recap_lines.append(f"  - Lead Thinking Before Subagents: {think_clean}")

    context_recap_block = ""
    if recap_lines:
        context_recap_block = "\n\n[CONTEXT RECAP BEFORE SUBAGENTS]:\n" + "\n".join(recap_lines)

    summary = (
        f"● Subagents execution completed ({done_count}/{len(results)} successful):\n"
        + "\n".join(parts)
        + context_recap_block
        + "\n\n[LEAD AGENT DIRECTIVE]: Subagents have completed their delegated tasks. "
        "Review their notes, your previous thinking, and the original user goal above. As the Lead Agent, you MUST now run tools "
        "(e.g. run_tests, read_file, edit_file, commands) to verify their work and execute any "
        "remaining steps before finalizing your turn. Do NOT end with a plain text response if "
        "tasks or verifications remain."
    )
    return summary
