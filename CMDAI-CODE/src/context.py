import os
from typing import List, Dict, Any, Optional
SYSTEM_PROMPT = """You are CMDAI CODE — local terminal coding agent. Tools: read/create/edit/search/delete files, run bash/powershell (user must confirm execution).

THINK BLOCK (see thinking-level rules at end): if required, output <think>...</think> using tree format with `|_ ` indentation, e.g.:
<think>
  |_ UNDERSTAND: ...
  |_ CONTEXT:
    |_ ...
  |_ PLAN: ...
</think>

RULES:
1. write/create = new file or full overwrite. edit = existing file only, old_str must be exact+unique.
2. ONE tool call per response. Wait for result before next call.
3. .py files you create/edit: always verify with run_python.
4. AUTONOMOUS MODE: never give up on error — analyze, try alternate approach, keep going until task 100% done. No apologies, no stopping halfway. If a tool fails repeatedly, change approach, don't repeat it.
5. delete_file only with explicit user permission.
6. Always end with a short text summary after finishing.
7. Never write scraping/API scripts — use search_web with target URL directly.
8. Never put code blocks in chat text — always save code via write_file/create_file; text response only summarizes.
9. Tool execution = native JSON function call only. Writing "TOOL: x" in <think> is planning only, not execution — never invent your own call format.
10. Large/complex/full-project requests: don't write everything in one turn. First turn = submit_plan to break into steps; implement files one by one in later turns.
11. No file-size limit: write_file always gets the 100% complete file, never a skeleton, never followed by edit_file to finish it.
12. If the task is not yet 100% complete, always call a tool immediately after </think> — never stop at just text. Only stop at text (without tool calls) when providing the final summary (Rule 6).
13. No ascii/mermaid/visual diagrams in text ever.
14. <think> block always uses `|_ ` tree structure, no exceptions.
15. [LEAD AGENT DUTY]: When wakeup_subagents finishes, as the Lead Agent you MUST inspect subagent reports and execute tools to verify and continue work.

AVAILABLE TOOLS:
- replace_lines(path (string), start_line (integer), end_line (integer), new_content (string)): Replaces lines from start_line to end_line with new_content.
- append_file(path (string), content (string)): Appends text to the end of a file.
- read_file(path (string)): Reads the contents of a file.
- create_file(path (string), content (string)): Creates a new file.
- edit_file(path (string), old_str (string), new_str (string)): Replaces old_str with new_str in a file.
- write_file(path (string), content (string)): Overwrites an entire file.
- delete_file(path (string)): Deletes a file or directory.
- commands(command (string), timeout (integer)): Runs a shell command/script.
- grep(pattern (string), path (string), glob_pattern (string)): Search for regex pattern.
- glob(pattern (string)): List files matching a glob.
- ls(path (string)): List contents of a directory.
- todo_write(items (array)): Writes tasks.
- search_web(query (string)): Searches the web.
- save_plan(content (string)): Saves your execution plan to plan.md.
- mark_plan_step_done(step_number (integer)): Marks a step as done in the plan.md file by replacing [ ] with [x].
- submit_plan(architecture_details (string), steps_list (array)): Submit an architectural plan before executing changes. Required on Extreme level.
- code_search(query (string), path (string)): Smart searches the project files using index or regex pattern. Use this for quickly finding definitions and uses of functions, classes, and variables.
- run_tests(files (array)): Runs tests for the project using the detected test framework. Pass a list of modified files if applicable to limit tests. If no framework is setup, this tool will fail and instruct you to use 'commands' to run your own native syntax checks (e.g. node -c, npx tsc, python -m py_compile, etc.).
- bugs(): Scans the entire project for syntax errors and returns a list of them. Does not accept any parameters.
- wakeup_subagents(prompt (string)): Delegates a task to a background sub-agent. This requires the user to have selected a subagent model via /subagents.
Always reply to the user in their own language (e.g. Polish), but follow all English instructions strictly.
"""
class ContextManager:
    def __init__(self, cwd: str = ".", session_id: str = None):
        self.cwd = os.path.abspath(cwd)
        self.messages: List[Dict[str, str]] = []
        self.system_prompt = SYSTEM_PROMPT
        
        from .session import SessionManager
        self.session_manager = SessionManager(cwd)
        
        if session_id:
            self.session_manager.load_state(session_id)
        else:
            import uuid
            self.session_manager.load_state(uuid.uuid4().hex[:8])
        
        path = os.path.join(self.session_manager.cmdai_code_dir, f"session_{self.session_manager.current_state.session_id}_history.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.messages = __import__("json").load(f)
            except Exception:
                self.messages = []
        else:
            self.messages = []
            
    def rename_session(self, new_id: str):
        self.session_manager.rename_session(new_id)
        self.save_history()

    def append_transcript(self, msg: dict):
        pass

    def save_history(self):
        import json, os
        base_path = os.path.join(self.session_manager.cmdai_code_dir, f"session_{self.session_manager.current_state.session_id}")
        json_path = f"{base_path}_history.json"
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_history(self, session_id: str):
        self.session_manager.load_state(session_id)
        path = __import__("os").path.join(self.session_manager.cmdai_code_dir, f"session_{self.session_manager.current_state.session_id}_history.json")
        import json
        if __import__("os").path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.messages = json.load(f)
            except Exception:
                self.messages = []
        else:
            self.messages = []

    def _load_project_context(self):
        cmdai_file = __import__("os").path.join(self.cwd, "CMDAI.md")
        if __import__("os").path.exists(cmdai_file):
            with open(cmdai_file, "r", encoding="utf-8") as f:
                content = f.read()
                self.system_prompt += f"\n\nPROJECT RULES AND CONTEXT (CMDAI.md) - You must STRICTLY adhere to these rules:\n{content}"
    def _load_app_state(self) -> Dict[str, Any]:
        import json
        state_file = os.path.expanduser("~/.cmdai_code/state.json")
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _resolve_local_model_path(self, model_ref: str) -> Optional[str]:
        if not model_ref:
            return None

        expanded = os.path.expanduser(model_ref)
        candidates = []
        if os.path.isabs(expanded):
            candidates.append(expanded)
        else:
            candidates.append(os.path.abspath(expanded))
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidates.append(os.path.join(app_dir, "systemmodels", expanded))
            candidates.append(os.path.join(app_dir, "models", expanded))

        for candidate in candidates:
            if os.path.exists(candidate) and candidate.lower().endswith(".gguf"):
                return candidate
        return None

    def _build_compaction_model(self, fallback_model):
        from .ui import console, MUTED_COLOR

        state = self._load_app_state()
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys_dir = os.path.join(app_dir, "systemmodels")
        model_ref = None
        
                                                                   
        if os.path.exists(sys_dir):
            import glob
            sys_models = glob.glob(os.path.join(sys_dir, "*.gguf"))
            if sys_models:
                needed_ctx = max(8192, self.get_token_count() + 4000)
                model_ref = {"path": sys_models[0], "n_ctx": needed_ctx, "n_gpu_layers": 8}
                
                                                                                       
        if not model_ref:
            model_ref = state.get("compaction_model")
        
        if not model_ref:
            return fallback_model, False

        try:
            if isinstance(model_ref, dict):
                local_path = self._resolve_local_model_path(
                    model_ref.get("path") or model_ref.get("model_path") or ""
                )
                if local_path:
                    from .llama import LlamaModel
                    base_ctx = int(model_ref.get("n_ctx", state.get("compaction_n_ctx", 8192)))
                    n_ctx = max(base_ctx, self.get_token_count() + 4000)
                    n_gpu_layers = int(model_ref.get("n_gpu_layers", state.get("compaction_n_gpu_layers", 8)))
                    return LlamaModel(local_path, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers), True

                if model_ref.get("name") and model_ref.get("base_url") is not None:
                    from .api_model import OpenAIAPIModel
                    return OpenAIAPIModel(
                        model_name=model_ref["name"],
                        api_key=model_ref.get("api_key", ""),
                        base_url=model_ref.get("base_url", ""),
                        provider_id=model_ref.get("provider")
                    ), True

            if isinstance(model_ref, str):
                local_path = self._resolve_local_model_path(model_ref)
                if local_path:
                    from .llama import LlamaModel
                    base_ctx = int(state.get("compaction_n_ctx", 8192))
                    n_ctx = max(base_ctx, self.get_token_count() + 4000)
                    n_gpu_layers = int(state.get("compaction_n_gpu_layers", 8))
                    return LlamaModel(local_path, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers), True

                for api_model in state.get("api_models", []):
                    if api_model.get("name") == model_ref:
                        from .api_model import OpenAIAPIModel
                        return OpenAIAPIModel(
                            model_name=api_model["name"],
                            api_key=api_model.get("api_key", ""),
                            base_url=api_model.get("base_url", ""),
                            provider_id=api_model.get("provider")
                        ), True

            console.print(f"[yellow][Compaction model not found: {model_ref}. Using current model.][/]")
        except Exception as e:
            console.print(f"[yellow][Could not load compaction model: {e}. Using current model.][/]")

        return fallback_model, False

    def get_token_count(self) -> int:
        count = 0
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            for m in self.messages:
                count += len(enc.encode(m.get("content", "")))
            count += len(enc.encode(self.system_prompt))
        except ImportError:
            # Fallback to smart heuristic (avg 3.5 chars per token)
            for m in self.messages:
                count += len(m.get("content", "")) // 3.5
            count += len(self.system_prompt) // 3.5
        return int(count)

    def _fallback_summary(self) -> str:
        recent = []
        for msg in self.messages[-8:]:
            content = msg.get("content", "")
            if len(content) > 800:
                content = content[:800] + "..."
            recent.append(f"- {msg.get('role', 'unknown')}: {content}")

        return (
            "# Session State\n"
            "## Objective\n- User goal: Continue the latest user request.\n- Definition of done: unknown\n"
            "## Current Task\n- Next action: Review recent activity and continue the latest request.\n- Reason: Semantic summarization was unavailable.\n- Priority: high\n"
            "## Completed Work\n- [x] Recent activity was preserved below.\n"
            "## Active Plan\n- [ ] Continue from the latest user request.\n"
            "## Changed Files\n- unknown: no reliable changelog available.\n"
            "## Technical State\n- Tests: unknown\n- Last tool result: unknown\n"
            "## Decisions\n- Preserve recent activity without inferring missing facts.\n"
            "## Risks and Blockers\n- Fallback summary; verify facts before editing.\n"
            "## Subagents\n- unknown\n"
            "## Handoff\n- Start with: inspect recent activity.\n- Do not repeat: unknown\n"
            "## Recent Activity\n" + "\n".join(recent)
        )

    def _sanitize_summary_text(self, raw_text: str) -> str:
        if not raw_text or not raw_text.strip():
            return ""
        cleaned = raw_text.strip()
        import re
        if "```" in cleaned:
            cleaned = re.sub(r"```(?:json|markdown)?", "", cleaned).strip()
        
        if (cleaned.startswith("{") and cleaned.endswith("}")) or '"summary"' in cleaned or '"goal"' in cleaned:
            try:
                m = re.search(r'\{.*\}', cleaned, re.DOTALL)
                json_str = m.group(0) if m else cleaned
                data = json.loads(json_str)
                lines = []
                goal = data.get("goal") or data.get("summary") or "Continue session."
                lines.append(f"Goal: {goal}")
                
                decisions = data.get("decisions")
                lines.append("Decisions:")
                if isinstance(decisions, list) and decisions:
                    for d in decisions:
                        lines.append(f"- {d}")
                elif isinstance(decisions, str) and decisions.strip():
                    lines.append(f"- {decisions.strip()}")
                else:
                    lines.append("- Continue development.")
                    
                files = data.get("files")
                lines.append("Files:")
                if isinstance(files, dict) and files:
                    for fpath, desc in files.items():
                        lines.append(f"- {fpath}: {desc}")
                elif isinstance(files, list) and files:
                    for fitem in files:
                        lines.append(f"- {fitem}")
                else:
                    lines.append("- plan.md: Project plan updated.")

                plan = data.get("plan")
                lines.append("Plan:")
                if isinstance(plan, list) and plan:
                    for pitem in plan:
                        lines.append(f"{pitem}" if str(pitem).startswith("[") else f"- {pitem}")
                elif isinstance(plan, str) and plan.strip():
                    lines.append(plan.strip())
                else:
                    lines.append("[ ] Continue implementation.")

                issues = data.get("issues")
                lines.append("Issues:")
                if isinstance(issues, list) and len(issues) > 0:
                    for idx in issues:
                        lines.append(f"- {idx}")
                else:
                    lines.append("- None")

                constraints = data.get("constraints")
                lines.append("Constraints:")
                if isinstance(constraints, str) and constraints.strip():
                    lines.append(f"- {constraints.strip()}")
                elif isinstance(constraints, list) and constraints:
                    for c in constraints:
                        lines.append(f"- {c}")
                else:
                    lines.append("- Follow modular architecture.")

                return "\n".join(lines)
            except Exception:
                pass
                
        return cleaned

    def trigger_compaction(self, model):
        from .ui import console, MUTED_COLOR, ThinkingTree
        if not self.messages:
            return

        compaction_prompt = (
            "You are a session-state compressor. Read the history and produce an accurate handoff for the next model. "
            "Use EXACTLY this English Markdown format, without code fences or additional sections:\n\n"
            "# Session State\n"
            "## Objective\n- User goal: <current goal>\n- Definition of done: <completion condition>\n"
            "## Current Task\n- Next action: <one concrete action>\n- Reason: <why it is next>\n- Priority: <high|medium|low>\n"
            "## Completed Work\n- [x] <outcome; include file when useful>\n"
            "## Active Plan\n- [ ] <up to 3 remaining actions in order>\n"
            "## Changed Files\n- <path>: <change, important API, and verification status>\n"
            "## Technical State\n- Tests: <latest result or unknown>\n- Last tool result: <relevant outcome or unknown>\n"
            "## Decisions\n- <decision and reason>\n"
            "## Risks and Blockers\n- <blocker, risk, or none>\n"
            "## Subagents\n- <agent: task, status, files, conclusion>\n"
            "## Handoff\n- Start with: <next action>\n- Do not repeat: <completed work or none>\n\n"
            "CRITICAL: Include only confirmed facts. Mark missing facts as unknown. Keep at most 3 items per section and stay below 1200 tokens. "
            "The compaction request is system-only: never make it the user goal, next action, plan, decision, or handoff. "
            "Never say to execute a tool response or repeat an already successful tool call. The next action must be unfinished user work."
        )
        compaction_sys = "You are a memory-compression AI. You must read the history and output ONLY a summary in the exact requested format. You DO NOT use tools. Do NOT generate JSON tool calls under any circumstances."
        messages = [{"role": "system", "content": compaction_sys}] + self.messages + [{"role": "user", "content": compaction_prompt}]

        compaction_model_instance, should_release = self._build_compaction_model(model)
        main_model_released = False
        if should_release and compaction_model_instance is not model:
            try:
                if hasattr(model, "unload"):
                    model.unload()
                if hasattr(model, "_llm") and getattr(model, "_llm", None) is not None:
                    model._llm = None
                    import gc
                    gc.collect()
                main_model_released = True
            except Exception:
                pass

        model_name_str = getattr(compaction_model_instance, "model_name", "system model")
        import os
        model_name_str = os.path.basename(model_name_str)

        from .ui import console, MUTED_COLOR, ThinkingTree
        tree = ThinkingTree(expanded=True, simulate=False, title="Summarizing", model_name=model_name_str)

        max_retries = 1
        retry_count = 0
        current_messages = self.messages[:]

        while retry_count < max_retries:
            messages = [{"role": "system", "content": compaction_sys}] + current_messages + [{"role": "user", "content": compaction_prompt}]
            response_text = ""
            current_line = ""
            try:
                for content, thinking, _ in compaction_model_instance.stream_chat(messages, reasoning_budget=0):
                    chunk = content or thinking or ""
                    if chunk:
                        if len(response_text) > 9000:
                            break
                        response_text += chunk
                        current_line += chunk
                        if '\n' in current_line:
                            parts = current_line.split('\n')
                            for p in parts[:-1]:
                                cp = p.strip()
                                if cp and not cp.startswith("```") and not cp.startswith("{") and not cp.startswith("}") and not cp.startswith('"summary"'):
                                    tree.add_line(cp)
                            current_line = parts[-1]
                if current_line.strip() and not current_line.strip().startswith("```") and not current_line.strip().startswith("{") and not current_line.strip().startswith("}"):
                    tree.add_line(current_line)
                break
            except Exception as e:
                err_str = str(e).lower()
                if "exceed" in err_str or "context" in err_str or "token" in err_str or "capacity" in err_str:
                    retry_count += 1

                tree.print_tree()
                console.print(f"[yellow][Compaction failed: {e}. Using emergency summary.][/]")
                response_text = self._fallback_summary()
                break

        response_text = self._sanitize_summary_text(response_text.strip() or self._fallback_summary())
        tree.lines = [l for l in response_text.splitlines() if l.strip()]
        tree.print_tree()
        if should_release:
            try:
                if hasattr(compaction_model_instance, "_llm"):
                    compaction_model_instance._llm = None
                    import gc
                    gc.collect()
                if hasattr(compaction_model_instance, "unload"):
                    compaction_model_instance.unload()
            except Exception:
                pass

        response_text = response_text.strip() or self._fallback_summary()

        from .session import SessionState
        self.session_manager.current_state = SessionState.from_markdown(response_text, self.session_manager.current_state.session_id)
        self.session_manager.save_state()
        self.latest_summary = self.session_manager.current_state.to_prompt() or response_text

        # Reset messages history to clear token debt and reset counter
        last_user_msg = None
        for m in reversed(self.messages):
            if m.get("role") == "user":
                last_user_msg = m
                break
        if last_user_msg:
            self.messages = [last_user_msg]
        else:
            self.messages = []

        self.save_history()

        tokens_count = self.get_token_count()
        console.print(f"[{MUTED_COLOR}][Context compacted: ~{tokens_count} tokens summary saved directly to system prompt. History.json preserved. Token counter reset.][/]")
        if main_model_released:
            console.print(f"[{MUTED_COLOR}][Main model will reload on the next turn.][/]")
    def get_system_message(self, tools_desc: str, mode: str, thinking_desc: str) -> Dict[str, str]:
        prompt = self.system_prompt
        
                                       
        if tools_desc:
            prompt += f"\n\nAVAILABLE TOOLS:\n{tools_desc}\nCRITICAL: Use the tools directly. DO NOT ask the user to run commands for you."
                                                
        if mode == "plan":
            prompt += (
                "\n\n*** CRITICAL: YOU ARE IN PLAN MODE ***\n"
                "Your ONLY objective is to analyze the user's request and write a detailed architecture and execution plan into a file named `plan.md`.\n"
                "1. YOU ARE STRICTLY FORBIDDEN from creating or editing any code files (.py, .js, .html, etc.).\n"
                "2. You may use read tools (read_file, list_dir, grep) to explore the codebase.\n"
                "3. Once you understand the task, use write_file/create_file ONLY to save your plan to `plan.md`.\n"
                "4. Do NOT attempt to implement the code yourself in this mode."
            )
        elif mode == "code":
            prompt += (
                "\n\n*** CURRENT MODE: CODE MODE ***\n"
                "You are an expert developer. You must write, edit, and fix code based on the user's instructions.\n"
                "1. If a `plan.md` exists, follow it step by step.\n"
                "2. When writing code, write complete, robust, and clean code.\n"
                "3. The user will be asked to accept your code changes before they are saved to disk."
            )
        elif mode == "auto":
            prompt += (
                "\n\n*** CURRENT MODE: AUTO MODE ***\n"
                "You are a fully autonomous AI agent. You have permission to create, edit, and run code without user confirmation.\n"
                "1. Analyze the objective, plan your steps, and execute them automatically.\n"
                "2. You can use bash to test your code and fix errors autonomously.\n"
                "3. Continue working until the task is completely finished."
            )
                                   
        plan_file = os.path.join(self.cwd, "plan.md")
        if os.path.exists(plan_file):
            try:
                with open(plan_file, "r", encoding="utf-8") as f:
                    plan_content = f.read()
                    prompt += f"\n\nCURRENT PLAN INJECTED (plan.md):\n{plan_content}\nAlways refer to this plan when deciding what to do next."
            except Exception:
                pass
                
                                   
        session_context = getattr(self, "latest_summary", None) or self.session_manager.current_state.to_prompt()
        if session_context:
            prompt += f"\n\n[LATEST COMPRESSED SESSION CONTEXT]\n{session_context}"

        if thinking_desc:
            prompt += (
                f"\n\n*** THINKING LEVEL REQUIREMENTS ***\n"
                f"{thinking_desc}\n\n"
                "CRITICAL INSTRUCTION: Read the rules above. IF your current thinking level requires thoughts, your output MUST begin with a <think>...</think> block analyzing the problem according to the requirements above.\n"
                "If you are instructed to use Text-Based Tool Calling, you MUST output your tool call as a JSON block immediately after the </think> tag, like this:\n"
                "```json\n{\n  \"name\": \"tool_name\",\n  \"arguments\": { ... }\n}\n```\n"
                "Do NOT output native API tool calls if they are disabled. Always write <think> before your json block!\n"
                "EXTREMELY IMPORTANT: YOU MUST ALWAYS USE A NATIVE JSON TOOL CALL IN YOUR RESPONSE. NEVER RESPOND WITH JUST TEXT IF THE USER ASKS YOU TO DO SOMETHING!"
            )
        return {"role": "system", "content": prompt}

    def get_messages(self, tools_desc: str = "", thinking_desc: str = "") -> list:
        msgs = []
        mode = getattr(self.session_manager.current_state, "mode", "auto")
        system_msg = self.get_system_message(tools_desc, mode, thinking_desc)
        msgs.append(system_msg)

        has_summary = bool(getattr(self, "latest_summary", None) or self.session_manager.current_state.to_prompt())
        raw_msgs = [m for m in self.messages if not m.get("hidden")]

        if has_summary and self.get_token_count() >= 7000 and len(raw_msgs) > 6:
            raw_msgs = raw_msgs[-6:]

        for m in raw_msgs:
            if m.get("role") == "tool":
                msgs.append({
                    "role": "user",
                    "content": f"<tool_response>\n{m.get('content', '')}\n</tool_response>",
                    "timestamp": m.get("timestamp")
                })
            else:
                msgs.append(m)
        return msgs

    def add_user_message(self, msg: str, attached_files: list = None):
        import time
        msg_obj = {"role": "user", "content": msg, "timestamp": time.time()}
        if attached_files:
            msg_obj["attached_files"] = attached_files
        self.messages.append(msg_obj)
        self.append_transcript(msg_obj)
        self.save_history()

    def add_assistant_message(self, msg: str, tool_calls=None, thinking: str = None):
        import time
        m = {"role": "assistant", "content": str(msg if msg is not None else ""), "timestamp": time.time()}
        if thinking:
            m["thinking"] = str(thinking)
        if tool_calls:
            formatted_tc = []
            import json
            for tc in tool_calls:
                func = tc.get("function", {})
                formatted_tc.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": func.get("name", "unknown"),
                        "arguments": func.get("arguments", "{}")
                    }
                })
            m["tool_calls"] = formatted_tc
        self.messages.append(m)
        self.append_transcript(m)
        self.save_history()

    def add_tool_message(self, tool_call_id: str, name: str, content: str, subagents: list = None):
        import time
        subagent_data = subagents or getattr(self, "_last_subagents_data", None)
        m = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": str(content),
            "timestamp": time.time(),
        }
        if subagent_data:
            m["subagents"] = subagent_data
            self._last_subagents_data = None

        self.messages.append(m)
        self.append_transcript(m)
        self.save_history()

    def clear(self):
        self.messages = []
        self.save_history()
