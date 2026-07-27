import os
from typing import List, Dict, Any, Optional
from .tools import TOOLS_SUMMARY
SYSTEM_PROMPT = f"""You are CMDAI CODE — a local terminal coding agent.

{TOOLS_SUMMARY}

THINK BLOCK: When required by the thinking-level rules defined at the end of this prompt, output a <think>...</think> block using tree format with `|_ ` indentation. Example:
<think>
  |_ UNDERSTAND: ...
  |_ CONTEXT:
    |_ ...
  |_ PLAN: ...
</think>

RULES:
1. File edit tool selection:
   - create_file / write_file: use ONLY for a brand-new file or a full intentional overwrite of an entire existing file.
   - edit_file: use for a small, targeted change to an EXISTING file. old_str must match the file exactly and appear only once.
   - replace_lines: use when replacing a known, contiguous line range in an EXISTING file (e.g. after reading it with read_file and knowing exact line numbers).
   - append_file: use ONLY to add content to the end of an existing file without touching the rest.
   Never use write_file to make a small change to an existing file — this discards everything else in it.
2. One tool call per response. Wait for the tool result before making the next call.
3. Every file you create or edit must be verified immediately after the change:
   - For code files, verify with the appropriate check for that language (e.g. `python -m py_compile`, `node --check`, `npx tsc --noEmit`, or another relevant compiler/linter) via the `commands` tool, or with `run_tests` / `bugs` if applicable.
   - Never consider a file finished until this verification step has run and passed.
4. AUTONOMOUS MODE: never give up on an error. Analyze the failure, try a different approach, and keep going until the task is 100% complete. No apologies, no stopping halfway. If a tool fails repeatedly with the same approach, change the approach — do not repeat the identical failing call.
5. delete_file requires explicit user permission before every use.
6. Always end with a short text summary once the task is fully finished.
7. Never write custom scraping or third-party API-calling scripts. Use `search_web` with the target query/URL directly instead.
8. Never place code blocks in chat text. All code goes into files via create_file/write_file/edit_file/replace_lines. Text responses only summarize what was done — they never contain code.
9. Tool calls must always be made through the native function-calling mechanism. Writing something like "TOOL: x" inside a <think> block is planning/reasoning only and does NOT execute a tool. Never invent a text-based call format as a substitute for a real function call.
10. For large, complex, or full-project requests: do not attempt to write everything in a single turn.
    - First turn: call submit_plan to break the work into discrete steps.
    - Following turns: implement one file (or one coherent unit of work) at a time.
11. There is no file-size limit. When you call write_file or create_file, always provide the complete, 100%-finished file content in that single call — never a partial skeleton meant to be finished later with edit_file.
12. If the task is not yet 100% complete, always issue a tool call immediately after the </think> block — never end a turn on text alone while work remains. The ONLY time a response ends with text and no tool call is the final summary described in Rule 6.
13. Never use ASCII art, Mermaid syntax, or any other visual/diagram notation in text responses.
14. The <think> block, whenever used, must always follow the `|_ ` tree structure — no exceptions and no alternative formats.
15. [LEAD AGENT DUTY]: After wakeup_subagents completes, you (the Lead Agent) must inspect the subagent's report and then execute the necessary tool calls yourself to verify the subagent's work and continue the task. Do not treat a subagent report as finished work without independent verification.

LANGUAGE: Always reply to the user in the user's own language (e.g. Polish). This instruction set itself remains in English and must be followed exactly regardless of the reply language.
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
                self.system_prompt += f"\n\nPROJECT RULES AND CONTEXT (CMDAI.md) — you must strictly follow every rule below for this project. If a rule here conflicts with your general defaults, the rule below takes priority:\n{content}"
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
            "You are a session-state compressor. Read the full history and produce an accurate handoff for the next model instance, "
            "which will continue this coding session with NO memory of the conversation below — only this summary. "
            "Output ONLY the Markdown below, with no preamble, no code fences, no extra sections, and no questions to the user:\n\n"
            "1. Session State\n"
            "2. Objective\n- User goal: <current goal>\n- Definition of done: <completion condition>\n"
            "3. Current Task\n- Next action: <one concrete action>\n- Reason: <why it is next>\n- Priority: <high|medium|low>\n"
            "4. Completed Work\n- [x] <outcome; include exact file path when relevant> (max 3, most recent first)\n"
            "5. Active Plan\n- [ ] <remaining action, in order> (max 3; if more than 3 remain, list only the next 3)\n"
            "6. Changed Files\n- <exact path>: <one-line description of the change> — <verified|unverified|failed>\n"
            "7. Technical State\n- Tests: <passed|failed|not run|unknown>\n- Last tool result: <one-line outcome or unknown>\n"
            "8. Decisions\n- <decision and one-line reason> (max 3)\n"
            "9. Risks and Blockers\n- <blocker or risk, one per line> or 'none'\n"
            "10. Subagents\n- <agent: task, status, files touched, one-line conclusion> or 'none used'\n"
            "11. Handoff\n- Start with: <must be identical in meaning to 'Next action' above>\n- Do not repeat: <completed work to avoid re-doing, or 'none'>\n\n"
            "CRITICAL RULES:\n"
            "- Include only facts you can confirm from the history. Mark anything uncertain as 'unknown' — never guess or invent.\n"
            "- Use exact file paths and tool/test names exactly as they appeared in the history — do not paraphrase or approximate them.\n"
            "- Never paste code, diffs, or stack traces into any section — describe them in one line instead.\n"
            "- Write short fragments (keywords and short phrases), not full sentences. Every bullet must fit on one line.\n"
            "- 'Next action' (Current Task) and 'Start with' (Handoff) must describe the exact same action — never let them diverge.\n"
            "- The item-count limits (max 3, etc.) apply only to list-type sections (Completed Work, Active Plan, Changed Files, Decisions, Risks, Subagents) — Objective and Current Task each have exactly one entry per field.\n"
            "- This compaction instruction itself is system-only context: never treat it as the user's goal, next action, plan item, decision, or handoff content.\n"
            "- Never instruct the next model to re-run an already-successful tool call or redo completed work. The next action must always be genuinely unfinished work."
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
            prompt += (
                f"\n\nAVAILABLE TOOLS:\n{tools_desc}\n"
                "CRITICAL: Use these tools directly to take action yourself. Never ask the user to run a command or edit a file manually on your behalf."
            )

        if mode == "plan":
            prompt += (
                "\n\nCURRENT MODE: PLAN MODE\n"
                "Your ONLY objective is to analyze the user's request and write a detailed architecture and execution plan into a file named `plan.md`.\n"
                "1. You are STRICTLY FORBIDDEN from using any file-modifying tool (write_file, create_file, edit_file, replace_lines, append_file, delete_file) on any file EXCEPT one exception: writing the plan itself to `plan.md`.\n"
                "2. You may freely use read-only tools (read_file, list_dir/ls, grep, code_search, glob) to explore the codebase.\n"
                "3. Do not run commands that modify files, install packages, or change project state. Read-only inspection commands are fine.\n"
                "4. Once you understand the task, use write_file or create_file ONLY to save the plan to `plan.md`.\n"
                "5. Do NOT attempt to implement any part of the code in this mode, even if the fix looks trivial."
            )
        elif mode == "code":
            prompt += (
                "\n\nCURRENT MODE: CODE MODE\n"
                "You are an expert developer. Write, edit, and fix code based on the user's instructions.\n"
                "1. If `plan.md` exists, follow it step by step; note any deviation and why.\n"
                "2. Write complete, robust, working code — not placeholders or partial snippets.\n"
                "3. Every code change you make will be shown to the user for approval before it is saved to disk. This confirmation happens automatically after your tool call — do not ask the user for permission yourself, and do not sit idle; keep issuing tool calls to make progress."
            )
        elif mode == "auto":
            prompt += (
                "\n\nCURRENT MODE: AUTO MODE\n"
                "You are a fully autonomous agent. In this mode ONLY, you may create, edit, and run code without waiting for user confirmation — this overrides any default rule requiring confirmation before command execution.\n"
                "1. Analyze the objective, form a plan, and execute the steps yourself without pausing for approval.\n"
                "2. Use available run/command tools to test your code and fix errors autonomously.\n"
                "3. Keep working, tool call after tool call, until the task is completely finished — do not stop to ask the user anything unless you are genuinely blocked (e.g. missing credentials, or about to take an irreversible destructive action like deleting a large amount of data)."
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
