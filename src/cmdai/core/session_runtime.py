"""SessionRuntime for CMDAI CODE.

Encapsulates autonomous agent session state, message history, transcripts,
tool execution loop, and provides headless execution (e.g. `cmdai run <prompt>`)
independent of the Textual UI.
"""

import os
import sys
import time
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from .settings import SettingsManager
from ..agent.tools import AgentContext
from ..agent.runner import AgentRunner, get_agent_system_prompt
from ..agent.tool_specs import get_native_tool_schemas
from .providers import stream_chat_completion


class SessionRuntime:
    """Manages an autonomous agent session, its state, and execution loop."""

    def __init__(
        self,
        workdir: str = ".",
        provider: str = "",
        model: str = "",
        mode: str = "auto",
        api_key: str = "",
        settings_manager: Optional[SettingsManager] = None,
        ask_handler: Optional[Callable[[str, List[str], Optional[Dict[str, Any]]], str]] = None,
    ):
        self.workdir = os.path.abspath(workdir)
        self.settings = settings_manager or SettingsManager(self.workdir)

        cfg = self.settings.config
        self.current_provider = provider or cfg.get("default_provider", "openrouter")
        self.current_model_id = model or cfg.get("default_model", "")
        self.mode = mode or cfg.get("agent", {}).get("mode", "auto")
        self.api_key = api_key or cfg.get("api_keys", {}).get(self.current_provider, "")

        self.messages: List[Dict[str, Any]] = []
        self.transcript: List[Dict[str, Any]] = []
        self.abort_requested = False

        self.agent_ctx = AgentContext(workdir=self.workdir)
        self.agent_ctx.mode = self.mode
        self.agent_ctx.permissions = cfg.get("permissions")

        self.agent_runner = AgentRunner(
            ctx=self.agent_ctx,
            ask_handler=ask_handler,
        )

    def run_turn(
        self,
        user_prompt: str,
        on_token: Optional[Callable[[str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, str], None]] = None,
        on_tool_finish: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        max_steps: int = 15,
    ) -> str:
        """Run a full autonomous agent turn for the given prompt."""
        self.abort_requested = False
        self.messages.append({"role": "user", "content": user_prompt})
        self.transcript.append({"kind": "user", "text": user_prompt})

        gen_params = self.settings.config.get("generation", {})
        final_answer = ""

        for step in range(max_steps):
            if self.abort_requested:
                break

            # Build system prompt with tools and AGENTS.md rules
            sys_prompt = get_agent_system_prompt(self.workdir, mode=self.mode)
            payload_messages = [{"role": "system", "content": sys_prompt}] + self.messages

            native_tools = get_native_tool_schemas() if self.current_provider != "local_gguf" else None

            step_chunks: List[str] = []

            def _step_token(token: str):
                step_chunks.append(token)
                if on_token:
                    on_token(token)

            try:
                resp, think = stream_chat_completion(
                    provider_id=self.current_provider,
                    model_id=self.current_model_id,
                    messages=payload_messages,
                    api_key=self.api_key,
                    generation_params=gen_params,
                    tools=native_tools,
                    on_token=_step_token,
                    on_thinking=on_thinking,
                    is_aborted=lambda: self.abort_requested,
                )
            except Exception as e:
                err_msg = f"Error during model completion: {e}"
                if on_token:
                    on_token(f"\n{err_msg}\n")
                return err_msg

            full_resp = "".join(step_chunks) or resp or ""
            self.messages.append({"role": "assistant", "content": full_resp})
            self.transcript.append({"kind": "assistant", "text": full_resp})
            final_answer = full_resp

            # Check if assistant invoked tools
            tool_calls = self.agent_runner.find_all_xml_tool_calls(full_resp)
            if not tool_calls:
                # No more tools called -> turn completed!
                break

            for t_name, t_args in tool_calls:
                if self.abort_requested:
                    break
                if on_tool_start:
                    on_tool_start(t_name, str(t_args))

                tool_res = self.agent_runner.execute_tool(t_name, t_args)

                if on_tool_finish:
                    on_tool_finish(t_name, str(t_args), tool_res)

                formatted_res = self.agent_runner.format_tool_result_for_llm(t_name, tool_res)
                self.messages.append({"role": "user", "content": formatted_res})
                self.transcript.append({"kind": "tool_result", "name": t_name, "result": tool_res})

        return final_answer
