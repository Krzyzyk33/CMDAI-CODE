"""Headless (non-TUI) CLI runner for CMDAI CODE (`cmdai -p ...`)."""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..agent.runner import AgentRunner, _strip_xml_tool_calls_clean, get_agent_system_prompt
from ..agent.tools import AgentContext
from .providers import stream_chat_completion
from .sessions import SessionManager, clean_title_from_prompt
from .settings import get_settings


def combine_prompt_and_stdin(prompt: str, stdin_text: str) -> str:
    """Combine `-p` prompt text and piped stdin input cleanly."""
    p = (prompt or "").strip()
    s = (stdin_text or "").strip()
    if p and s:
        return f"{p}\n\n<stdin>\n{s}\n</stdin>"
    return p or s


def run_headless(
    prompt: str,
    workdir: str = ".",
    provider: str = "",
    model: str = "",
    continue_session: bool = False,
    output_format: str = "text",
    max_turns: int = 12,
    completion_fn: Optional[Callable[..., Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    """Execute a single headless agent run and return a structured result dict."""
    abs_workdir = os.path.abspath(workdir or ".")
    settings = get_settings()
    prov = provider or settings.config.get("default_provider", "local_gguf")
    mod = model or settings.config.get("default_model", "")
    api_key = settings.get_api_key(prov)
    gen_params = settings.config.get("generation_params", {})

    sm = SessionManager()
    session_id = str(uuid.uuid4())[:8]
    messages: List[Dict[str, str]] = []

    if continue_session:
        recent = sm.list_sessions(current_dir=abs_workdir)
        if recent:
            latest_id = recent[0].get("id", "")
            loaded = sm.load_session(latest_id) if latest_id else None
            if loaded and isinstance(loaded.get("messages"), list):
                session_id = latest_id
                messages = [
                    m for m in loaded["messages"]
                    if isinstance(m, dict) and m.get("role") in ("user", "assistant", "system")
                ]

    sys_prompt = get_agent_system_prompt(workdir=abs_workdir, mode="auto", vision=False)
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": sys_prompt})
    else:
        messages[0] = {"role": "system", "content": sys_prompt}

    messages.append({"role": "user", "content": prompt})

    ctx = AgentContext(workdir=abs_workdir)
    ctx.mode = "auto"
    runner = AgentRunner(ctx=ctx)

    executed_tools: List[Dict[str, Any]] = []
    final_text = ""
    error_msg: Optional[str] = None

    def _call_llm(msgs: List[Dict[str, str]]) -> Tuple[str, str]:
        if completion_fn is not None:
            return completion_fn(prov, mod, msgs)
        if prov == "local_gguf":
            from .engine import SimpleGGUFLoader
            loader = SimpleGGUFLoader(settings.get_models_dir())
            return loader.stream_chat(
                messages=msgs,
                temperature=gen_params.get("temperature", 0.3),
                max_tokens=gen_params.get("max_tokens", 4096),
                model_filename=mod,
            )
        return stream_chat_completion(
            provider_id=prov,
            model_id=mod,
            messages=msgs,
            api_key=api_key,
            generation_params=gen_params,
        )

    try:
        for _turn in range(max_turns):
            resp_text, _thinking = _call_llm(messages)
            tool_calls = runner.parse_xml_tool_calls(resp_text)
            clean_text = _strip_xml_tool_calls_clean(resp_text).strip()
            if clean_text:
                final_text = clean_text
            messages.append({"role": "assistant", "content": resp_text})

            if not tool_calls:
                break

            tool_results_parts: List[str] = []
            for t_name, t_args in tool_calls:
                res = runner.execute_tool(t_name, t_args, ctx=ctx, ask=False, notify=False)
                executed_tools.append({
                    "tool": t_name,
                    "args": t_args,
                    "ok": bool(res.get("ok", True)) if isinstance(res, dict) else True,
                })
                res_json = json.dumps(res, ensure_ascii=False) if isinstance(res, dict) else str(res)
                tool_results_parts.append(f'<tool_result name="{t_name}">\n{res_json}\n</tool_result>')

            messages.append({"role": "user", "content": "\n\n".join(tool_results_parts)})

        sm.save_session(
            session_id=session_id,
            messages=messages,
            model_id=mod or prov,
            title=clean_title_from_prompt(prompt),
            workdir=abs_workdir,
            agent_mode="auto",
        )
    except Exception as exc:
        error_msg = str(exc)

    result = {
        "ok": error_msg is None,
        "session_id": session_id,
        "provider": prov,
        "model": mod,
        "response": final_text,
        "tool_calls": executed_tools,
        "error": error_msg,
    }
    return result


def print_headless_result(result: Dict[str, Any], output_format: str = "text") -> int:
    """Print headless result to stdout/stderr and return exit code."""
    fmt = (output_format or "text").strip().lower()
    if fmt == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("response"):
            print(result["response"])
        if result.get("error"):
            print(f"Error: {result['error']}", file=sys.stderr)
    return 0 if result.get("ok") else 1
