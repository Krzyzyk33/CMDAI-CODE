import os
import sys
import json
import re
from dataclasses import dataclass, field
from typing import List, Literal

from .ui import console, MUTED_COLOR, ACCENT_COLOR, SUCCESS_COLOR, ERROR_COLOR, _cprint
from .ui import ThinkingTree, SearchSpinner
from .ui import print_tool_call, print_tool_result, print_code_panel, print_diff


MAX_ITERATIONS = 1000


@dataclass
class SubAgentConfig:
    name: str
    task: str
    thinking_level: str = ""
    context_files: List[str] = field(default_factory=list)


@dataclass
class SubAgentResult:
    status: Literal["done", "failed", "budget_exceeded"]
    note: str
    files_edited: List[str]
    name: str


class SubAgentContext:
    def __init__(
        self,
        system_prompt: str,
        task_prompt: str,
        session_id: str = None,
        subagent_name: str = None,
        cmdai_code_dir: str = None,
        source_id: str = None,
        context_limit: int = 8192,
        parent_context=None,
    ):
        self.system_prompt = str(system_prompt or "")
        self.session_id = session_id
        self.subagent_name = str(subagent_name or "")
        self.cmdai_code_dir = cmdai_code_dir
        self.source_id = source_id
        self.context_limit = context_limit
        self.parent_context = parent_context
        self.compaction_threshold = 7000
        self.messages: list = [
            {"role": "user", "content": str(task_prompt or "")},
        ]
        self._compacted = False
        self.save_history()

        if self.parent_context and hasattr(self.parent_context, "messages"):
            import time
            self.parent_context.messages.append({
                "role": "subagent",
                "subagent_name": self.subagent_name,
                "subagent_role": "user",
                "content": str(task_prompt or ""),
                "timestamp": time.time()
            })
            if hasattr(self.parent_context, "save_history"):
                self.parent_context.save_history()

    def save_history(self):
        if self.parent_context and hasattr(self.parent_context, "save_history"):
            self.parent_context.save_history()

    def get_token_count(self, include_tool_defs: bool = True) -> int:
        count = 0
        tool_defs = build_subagent_tool_definitions() if include_tool_defs else []
        active_msgs = self.get_messages(tool_defs)
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            for m in active_msgs:
                if m.get("content"):
                    count += len(enc.encode(str(m.get("content"))))
                if m.get("thinking"):
                    count += len(enc.encode(str(m.get("thinking"))))
                if m.get("tool_calls"):
                    count += len(enc.encode(str(m.get("tool_calls"))))
        except ImportError:
            for m in active_msgs:
                if m.get("content"):
                    count += int(len(str(m.get("content"))) // 3.5)
                if m.get("thinking"):
                    count += int(len(str(m.get("thinking"))) // 3.5)
                if m.get("tool_calls"):
                    count += int(len(str(m.get("tool_calls"))) // 3.5)
        return int(count)

    def add_assistant_message(
        self, content: str, tool_calls=None, thinking: str = None
    ):
        import time
        m = {"role": "assistant", "content": str(content if content is not None else "")}
        if tool_calls:
            m["tool_calls"] = tool_calls
        if thinking:
            m["thinking"] = str(thinking)
        self.messages.append(m)
        self.save_history()

        if self.parent_context and hasattr(self.parent_context, "messages"):
            sub_m = {
                "role": "subagent",
                "subagent_name": self.subagent_name,
                "subagent_role": "assistant",
                "content": str(content if content is not None else ""),
                "timestamp": time.time()
            }
            if tool_calls:
                sub_m["tool_calls"] = tool_calls
            if thinking:
                sub_m["thinking"] = str(thinking)
            self.parent_context.messages.append(sub_m)
            if hasattr(self.parent_context, "save_history"):
                self.parent_context.save_history()

    def add_tool_message(self, content: str, tool_call_id: str = "call_mock"):
        import time
        self.messages.append(
            {"role": "tool", "content": str(content), "tool_call_id": tool_call_id}
        )
        self.messages.append(
            {"role": "user", "content": f"<tool_response>\n{content}\n</tool_response>"}
        )
        self.save_history()

        if self.parent_context and hasattr(self.parent_context, "messages"):
            self.parent_context.messages.append({
                "role": "subagent",
                "subagent_name": self.subagent_name,
                "subagent_role": "tool",
                "content": str(content),
                "tool_call_id": tool_call_id,
                "timestamp": time.time()
            })
            self.parent_context.messages.append({
                "role": "subagent",
                "subagent_name": self.subagent_name,
                "subagent_role": "user",
                "content": f"<tool_response>\n{content}\n</tool_response>",
                "timestamp": time.time()
            })
            if hasattr(self.parent_context, "save_history"):
                self.parent_context.save_history()

    def add_user_message(self, content: str):
        import time
        self.messages.append({"role": "user", "content": str(content)})
        self.save_history()

        if self.parent_context and hasattr(self.parent_context, "messages"):
            self.parent_context.messages.append({
                "role": "subagent",
                "subagent_name": self.subagent_name,
                "subagent_role": "user",
                "content": str(content),
                "timestamp": time.time()
            })
            if hasattr(self.parent_context, "save_history"):
                self.parent_context.save_history()

    def get_messages(self, tool_defs: list) -> list:
        sys_content = str(self.system_prompt or "")
        tool_desc = ""
        for t in tool_defs:
            f = t["function"]
            args_desc = []
            if "parameters" in f and "properties" in f["parameters"]:
                for arg_name, arg_info in f["parameters"]["properties"].items():
                    args_desc.append(f"{arg_name} ({arg_info.get('type', 'any')})")
            tool_desc += f"- {f['name']}({', '.join(args_desc)}): {f['description']}\n"

        sys_content += f"\n\nAVAILABLE TOOLS:\n{tool_desc}"

        if self._compacted:
            sys_content += "\n\n[NOTE: Context was compacted earlier. Continue working based on the conversation summary in system prompt.]"

        raw_msgs = [m for m in self.messages if not m.get("hidden")]
        if self._compacted:
            keep_n = 4 if len(raw_msgs) > 4 else len(raw_msgs)
            sliced = raw_msgs[-keep_n:]
            sanitized = []
            for m in sliced:
                m_copy = dict(m)
                c = m_copy.get("content", "")
                if c and len(c) > 1500:
                    m_copy["content"] = str(c)[:1500] + "... [truncated]"
                th = m_copy.get("thinking", "")
                if th and len(th) > 1000:
                    m_copy["thinking"] = str(th)[:1000] + "... [truncated]"
                sanitized.append(m_copy)
            raw_msgs = sanitized

        return [{"role": "system", "content": sys_content}] + raw_msgs

    def needs_compaction(self) -> bool:
        return self.get_token_count(include_tool_defs=True) >= self.compaction_threshold

    def _load_compaction_model(self, fallback, ctx_size=16384):
        import glob, os
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys_dir = os.path.join(app_dir, "systemmodels")
        if os.path.exists(sys_dir):
            sys_models = glob.glob(os.path.join(sys_dir, "*.gguf"))
            if sys_models:
                try:
                    from .llama import LlamaModel
                    return LlamaModel(sys_models[0], n_ctx=ctx_size, n_gpu_layers=8), True
                except Exception:
                    pass
        return fallback, False

    def _sanitize_summary_text(self, raw_text: str) -> str:
        if not raw_text or not raw_text.strip():
            return ""
        cleaned = raw_text.strip()
        import re, json
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

    def _fallback_summary(self) -> str:
        recent = []
        for msg in self.messages[-6:]:
            content = msg.get("content", "")
            if len(content) > 500:
                content = content[:500] + "..."
            recent.append(f"- {msg.get('role', 'unknown')}: {content}")
        return (
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

    def trigger_compaction(self, model):
        if not self.messages:
            return

        compaction_prompt = (
            "You are a session-state compressor. Read the history and produce an accurate handoff for the next model. "
            "Use EXACTLY this English Markdown format, without code fences or additional sections:\n\n"
            "1. Session State\n"
            "2.  Objective\n- User goal: <current goal>\n- Definition of done: <completion condition>\n"
            "3. Current Task\n- Next action: <one concrete action>\n- Reason: <why it is next>\n- Priority: <high|medium|low>\n"
            "4. Completed Work\n- [x] <outcome; include file when useful>\n"
            "5. Active Plan\n- [ ] <up to 3 remaining actions in order>\n"
            "6. Changed Files\n- <path>: <change, important API, and verification status>\n"
            "7. Technical State\n- Tests: <latest result or unknown>\n- Last tool result: <relevant outcome or unknown>\n"
            "8. Decisions\n- <decision and reason>\n"
            "9. Risks and Blockers\n- <blocker, risk, or none>\n"
            "10. Subagents\n- <agent: task, status, files, conclusion>\n"
            "11. Handoff\n- Start with: <next action>\n- Do not repeat: <completed work or none>\n\n"
            "CRITICAL: Include only confirmed facts. Mark missing facts as unknown. Keep at most 3 items per section and stay below 1200 tokens. "
            "The compaction request is system-only: never make it the user goal, next action, plan, decision, or handoff. "
            "Never say to execute a tool response or repeat an already successful tool call. The next action must be unfinished user work."
        )
        compaction_sys = "You are a memory-compression AI. Read the history and output ONLY a summary in the exact requested format. Do NOT use tools."

        comp_model, is_sys = self._load_compaction_model(model)
        model_name = "system model" if is_sys else None

        tree = ThinkingTree(
            expanded=True,
            simulate=False,
            title="Summarizing",
            model_name=model_name,
            source_id=self.source_id,
        )

        max_retries = 1
        retry_count = 0
        current_messages = self.messages[:]
        response_text = ""

        while retry_count < max_retries:
            sum_messages = (
                [{"role": "system", "content": compaction_sys}]
                + current_messages
                + [{"role": "user", "content": compaction_prompt}]
            )
            response_text = ""
            line_buffer = ""
            try:
                for content, thinking, _ in comp_model.stream_chat(
                    sum_messages, reasoning_budget=0
                ):
                    chunk = content or thinking or ""
                    if chunk:
                        response_text += chunk
                        line_buffer += chunk
                        while "\n" in line_buffer:
                            line, line_buffer = line_buffer.split("\n", 1)
                            cp = line.strip()
                            if cp and not cp.startswith("```") and not cp.startswith("{") and not cp.startswith("}") and not cp.startswith('"summary"'):
                                tree.add_line(cp)
                if line_buffer.strip() and not line_buffer.strip().startswith("```") and not line_buffer.strip().startswith("{") and not line_buffer.strip().startswith("}"):
                    tree.add_line(line_buffer.strip())
                break
            except Exception as e:
                err_str = str(e).lower()
                if any(w in err_str for w in ["exceed", "context", "token", "capacity"]):
                    retry_count += 1
                    if retry_count < max_retries:
                        cut_idx = max(1, int(len(current_messages) * 0.3))
                        current_messages = current_messages[cut_idx:]
                        continue
                response_text = self._fallback_summary()
                break

        if comp_model is not model:
            try:
                if hasattr(comp_model, "_llm"):
                    comp_model._llm = None
                if hasattr(comp_model, "unload"):
                    comp_model.unload()
                import gc
                gc.collect()
            except Exception:
                pass

        if not response_text.strip():
            response_text = self._fallback_summary()

        response_text = self._sanitize_summary_text(response_text)
        tree.lines = [l for l in response_text.splitlines() if l.strip()]
        if self.source_id:
            from .ui import MUTED_COLOR, console
            console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")
        tree.print_tree()

        summary_text = (
            "[COMPRESSED SESSION CONTEXT]\n"
            f"{response_text.strip()}\n\n"
            "Continue from here. This is a summary of the previous conversation after context compaction."
        )
        self.system_prompt = str(self.system_prompt or "").split(
            "[COMPRESSED SESSION CONTEXT]"
        )[0].strip()
        self.system_prompt += f"\n\n{summary_text}"

        self._compacted = True
        self.save_history()

    def _emergency_compact(self):
        self._compacted = True
        self.save_history()


def build_subagent_tool_definitions():
    from .tools import TOOLS_DEFINITIONS

    subagent_tools = []
    for t in TOOLS_DEFINITIONS:
        if t["function"]["name"] == "wakeup_subagents":
            continue
        subagent_tools.append(t)
    subagent_tools.append(
        {
            "type": "function",
            "function": {
                "name": "finish_task",
                "description": "Call this when the assigned task is COMPLETE. The 'note' parameter will be sent as a message back to the main agent. Summarize what you successfully accomplished and any parts you failed or skipped.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "note": {
                            "type": "string",
                            "description": "A message for the main agent detailing what you succeeded at, what failed, and any important context.",
                        }
                    },
                    "required": ["note"],
                },
            },
        }
    )
    return subagent_tools


def _clear_vram(model=None, model_name=None):
    if model is not None:
        try:
            if hasattr(model, "unload"):
                model.unload()
        except Exception:
            pass
        try:
            if hasattr(model, "_llm") and getattr(model, "_llm", None) is not None:
                model._llm = None
        except Exception:
            pass
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    for port in [8000, 1234, 11434]:
        try:
            import requests
            url = f"http://localhost:{port}/api/unload"
            if model_name:
                import os
                url += f"?model_name={os.path.basename(model_name)}"
            requests.post(url, timeout=2)
        except Exception:
            pass


class FileEditTracker:
    def __init__(self):
        self.edited: set = set()

    def wrap(self, tool_fn):
        def wrapped(name: str, args: dict, *a, **kw):
            result = tool_fn(name, args, *a, **kw)
            if name in (
                "write_file",
                "edit_file",
                "apply_patch",
                "replace_lines",
                "append_file",
                "create_file",
            ):
                path = args.get("path")
                if path:
                    self.edited.add(path)
            return result

        return wrapped


def _extract_tool_call_block(text: str):
    start = text.find("<|tool_call>")
    if start == -1:
        return None
    end = text.find("<tool_call|>", start)
    if end == -1:
        end = text.find("<|tool_call|>", start)
    block = text[start:]
    if end != -1:
        block = text[start : end + len("<tool_call|>")]
    name_match = re.match(
        r"<\|tool_call>\s*(?:call:)?\s*([a-zA-Z0-9_-]+)\s*",
        block,
    )
    if not name_match:
        return None
    func_name = name_match.group(1)
    args_start = name_match.end()
    raw_args = block[args_start:]
    args_end = raw_args.rfind("}")
    if args_end == -1:
        args_str = raw_args
        if args_str.endswith("<tool_call|>"):
            args_str = args_str[: -len("<tool_call|>")]
    else:
        args_str = raw_args[: args_end + 1]
    if not args_str.strip():
        return None
    return {"name": func_name, "arguments": args_str}


def _parse_pipe_quote_format(args_str: str) -> dict:
    s = args_str.strip()
    if s.startswith("{"):
        s = s[1:].strip()
        if s.endswith("}"):
            s = s[:-1].strip()
    else:
        return None

    pairs = []
    depth = 0
    current = ""
    i = 0
    while i < len(s):
        if s[i : i + 5] == '<|"|>':
            depth ^= 1
            current += s[i : i + 5]
            i += 5
        elif s[i] == "," and depth == 0:
            pairs.append(current)
            current = ""
            i += 1
        else:
            current += s[i]
            i += 1
    if current.strip():
        pairs.append(current)

    result = {}
    for pair in pairs:
        pair = pair.strip()
        if not pair:
            continue
        colon_pos = -1
        depth = 0
        for ci, ch in enumerate(pair):
            if pair[ci : ci + 5] == '<|"|>':
                depth ^= 1
            elif ch == ":" and depth == 0:
                colon_pos = ci
                break
        if colon_pos == -1:
            continue
        key = pair[:colon_pos].strip()
        value = pair[colon_pos + 1 :].strip()
        if value.startswith('<|"|>') and value.endswith('<|"|>'):
            value = value[5:-5]
        elif value.startswith('<|"|>'):
            value = value[5:]
        elif value.endswith('<|"|>'):
            value = value[:-5]
        result[key] = value

    return result


def robust_json_parse(s):
    """Attempt to parse a JSON string with various fallback strategies."""
    if isinstance(s, dict):
        return s
    if not s or not isinstance(s, str):
        return {}
    s_clean = re.sub(r"</?[a-zA-Z0-9_\|]+>", "", s).strip()
    s_clean = s_clean.replace('<|"|>', '"').replace('<|', '').replace('|>', '')
    try:
        res = json.loads(s_clean)
        if isinstance(res, dict):
            return res
    except Exception:
        pass
    m = re.search(r"\{.*\}", s_clean, re.DOTALL)
    if m:
        s_clean = m.group(0)
    try:
        res = json.loads(s_clean)
        if isinstance(res, dict):
            return res
    except Exception:
        pass
    s_fixed = re.sub(r'([{,]\s*)([a-zA-Z0-9_]+)\s*:', r'\1"\2":', s_clean)
    s_fixed = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', s_fixed)
    s_fixed = re.sub(r',\s*([\}\]])', r'\1', s_fixed)
    try:
        res = json.loads(s_fixed)
        if isinstance(res, dict):
            return res
    except Exception:
        pass
    try:
        res = ast.literal_eval(s_clean)
        if isinstance(res, dict):
            return res
    except Exception:
        pass
    kv_res = {}
    kv_matches = re.findall(r'([a-zA-Z0-9_]+)\s*:\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,\}]+))', s_clean)
    for k, v1, v2, v3 in kv_matches:
        kv_res[k] = v1 or v2 or v3
    return kv_res


def parse_tool_calls(full_content: str) -> list:
    if not full_content or not full_content.strip():
        return None

    # 0. call:tool_name{args} format (robust parsing with unquoted keys, single quotes, etc.)
    call_matches = re.findall(r"call:([a-zA-Z0-9_]+)\s*(\{.*)", full_content, re.DOTALL)
    if call_matches:
        mock_calls = []
        for fn_name, fn_args_raw in call_matches:
            parsed_args = robust_json_parse(fn_args_raw)
            if parsed_args or fn_args_raw.strip().startswith("{"):
                mock_calls.append({
                    "function": {
                        "name": fn_name,
                        "arguments": parsed_args
                    }
                })
        if mock_calls:
            return mock_calls

    # 1. ```json ... ``` blocks
    json_blocks = re.findall(r"```json\s*(.*?)\s*```", full_content, re.DOTALL)
    if not json_blocks:
        json_blocks = re.findall(r"<json>\s*(.*?)\s*</json>", full_content, re.DOTALL)
    # 2. <|tool_call> format (text-based tool calling with <|"|> quotes)
    if not json_blocks:
        tc_block = _extract_tool_call_block(full_content)
        if tc_block:
            parsed_args = _parse_pipe_quote_format(tc_block["arguments"])
            if parsed_args is not None:
                return [
                    {
                        "function": {
                            "name": tc_block["name"],
                            "arguments": parsed_args,
                        }
                    }
                ]
            return None
    # 3. <execute_tool>name args</execute_tool> format
    if not json_blocks:
        exec_match = re.search(
            r"<execute_tool>\s*(.*?)\s*</execute_tool>", full_content, re.DOTALL
        )
        if exec_match:
            raw = exec_match.group(1).strip()
            parts = raw.split(None, 1)
            if parts:
                tool_name = parts[0]
                args_str = parts[1] if len(parts) > 1 else ""
                parsed_args = {}
                if args_str:
                    try:
                        parsed_args = json.loads(args_str)
                    except json.JSONDecodeError:
                        pass
                return [{"function": {"name": tool_name, "arguments": parsed_args}}]
    # 4. <tool_call>{"name":..., "arguments":{...}}</tool_call> format (with or without </tool_call>)
    if not json_blocks:
        tc_tag_match = re.search(
            r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", full_content, re.DOTALL
        )
        if tc_tag_match:
            raw = tc_tag_match.group(1).strip()
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if json_match:
                raw = json_match.group(0)
            try:
                parsed = json.loads(raw)
                if (
                    isinstance(parsed, dict)
                    and "name" in parsed
                    and "arguments" in parsed
                ):
                    return [{"function": parsed}]
            except json.JSONDecodeError:
                pass
    # 5. Raw JSON {"name":..., "arguments":...}
    if not json_blocks:
        idx = full_content.find("{")
        if idx != -1:
            raw = full_content[idx:]
            if "name" in raw and "arguments" in raw:
                json_blocks = [raw]

    if json_blocks:
        calls = []
        for block in json_blocks:
            block = block.strip()
            try:
                parsed = json.loads(block)
                if (
                    isinstance(parsed, dict)
                    and "name" in parsed
                    and "arguments" in parsed
                ):
                    calls.append({"function": parsed})
            except json.JSONDecodeError:
                pass
        return calls if calls else None

    return None


def run_subagent(
    config: SubAgentConfig, agent_instance, cwd: str, source_id: str = None
) -> SubAgentResult:
    from .tools import execute_tool

    model = getattr(agent_instance, "model", None)
    if not model:
        return SubAgentResult(
            status="failed",
            note="Error: Model not available on agent_instance.",
            files_edited=[],
            name=config.name,
        )

    # Unload model memory BEFORE each subagent to start fresh
    _clear_vram(model=model)

    try:
        result = _run_subagent_inner(config, agent_instance, cwd, source_id, model)
    except Exception as e:
        import traceback

        result = SubAgentResult(
            status="failed",
            note=f"Internal error in subagent '{config.name}': {e}\n{traceback.format_exc()}",
            files_edited=[],
            name=config.name,
        )

    # Unload model memory AFTER each subagent to free VRAM
    _clear_vram(model=model)
    return result


def _run_subagent_inner(
    config: SubAgentConfig, agent_instance, cwd: str, source_id: str, model
) -> SubAgentResult:
    from .tools import execute_tool

    # NOTE: The main Llama.cpp handle (model) is reused here to save VRAM and load time.
    # This is completely safe because subagents are executed strictly SEQUENTIALLY.
    # If you ever change this to run subagents in parallel (e.g., ThreadPoolExecutor),
    # this shared handle will cause race conditions and prompt-eval bottlenecks.
    # In a parallel architecture, you would need separate model handles or a request queue.

    task_prompt = str(config.task or "")
    if config.context_files:
        for fp in config.context_files:
            if os.path.isfile(fp):
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        content = f.read()
                    task_prompt += f"\n\n--- {fp} ---\n{content}\n"
                except Exception:
                    pass

    tool_defs = build_subagent_tool_definitions()
    tracker = FileEditTracker()
    tracked_execute = tracker.wrap(execute_tool)

    base_sys_prompt = str(getattr(getattr(agent_instance, "context", None), "system_prompt", "") or "")

    level_map = {
        "low": (0, 512),
        "medium": (1, 1024),
        "high": (2, 2048),
        "ultra": (3, 4096),
        "extreme": (4, 8192),
    }
    lvl = config.thinking_level.lower() if config.thinking_level else "medium"
    level_idx, token_budget = level_map.get(lvl, (1, 1024))

    from .prompts import get_thinking_desc

    thinking_desc = str(get_thinking_desc(level_idx, token_budget) or "")

    sys_prompt = (
        base_sys_prompt
        + "\n\n[SUBAGENT RULES]\n"
        + (
            "- You are a sub-agent assigned to a specific task.\n"
            "- Use tools to read files, search code, edit files, and run commands.\n"
            "- When the task is COMPLETED, call finish_task().\n"
            "- Do NOT call finish_task() until the task is actually done and verified.\n"
            "- Your finish_task note MUST be a message to the main agent in English detailing what you succeeded at, what failed, and any context.\n"
            "- Never use wakeup_subagents tool – it is not available to you.\n"
            "- Write ALL responses and notes in English.\n"
        )
        + "\n"
        + thinking_desc
    )

    session_id = None
    cmdai_code_dir = None
    if getattr(agent_instance, "context", None) and getattr(
        agent_instance.context, "session_manager", None
    ):
        if getattr(agent_instance.context.session_manager, "current_state", None):
            session_id = getattr(
                agent_instance.context.session_manager.current_state, "session_id", None
            )
        cmdai_code_dir = getattr(
            agent_instance.context.session_manager, "cmdai_code_dir", None
        )

    ctx_limit = getattr(model, "get_context_limit", lambda: 8192)()
    MAX_SUBAGENT_CTX = 8192
    if ctx_limit > MAX_SUBAGENT_CTX:
        ctx_limit = MAX_SUBAGENT_CTX
    ctx = SubAgentContext(
        sys_prompt,
        task_prompt,
        session_id=session_id,
        subagent_name=config.name,
        cmdai_code_dir=cmdai_code_dir,
        source_id=source_id,
        context_limit=ctx_limit,
        parent_context=getattr(agent_instance, "context", None),
    )

    iteration = 0
    did_print_content = False
    ctx_oom_retries = 0
    no_tool_retries = 0
    _last_agent_text = ""

    while True:
        iteration += 1

        if ctx.needs_compaction():
            ctx.trigger_compaction(model)

        if iteration > 1 and did_print_content:
            console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")

        did_print_content = False

        lvl_display = str(config.thinking_level or "auto").lower()
        tree = ThinkingTree(
            expanded=True,
            title="Thinking",
            model_name=f"{config.name} · {lvl_display}",
            source_id=source_id,
        )
        if iteration > 1:
            tree._printed_tree = True
        low_thinking_placeholder = "NEXT_ACTION: Selecting the next tool."
        if str(config.thinking_level or "").lower() == "low":
            tree.add_line(low_thinking_placeholder)
        tree.start()

        full_content = ""
        full_thinking = ""
        tool_calls = None
        thinking_buffer = ""
        tool_detected = False
        printed_idx = 0
        json_spinner = None

        try:
            budget_map = {
                "Low": 512,
                "Medium": 1024,
                "High": 2048,
                "Ultra": 4096,
                "Extreme": 8192,
            }
            budget = budget_map.get(config.thinking_level, 1024)

            stream = model.stream_chat(
                ctx.get_messages(tool_defs), tools=tool_defs, reasoning_budget=budget
            )
            for chunk_content, thinking, tc in stream:
                if chunk_content:
                    full_content += chunk_content
                    if json_spinner:
                        json_spinner.update(full_content)
                    if not tool_detected:
                        json_start_idx = -1
                        triggers = [
                            "```json",
                            "<json>",
                            "<|tool_call>",
                            "<tool_call>",
                            "<execute_tool>",
                            "execute_tool",
                            "<execute",
                            "call:",
                            '{"name"',
                            '{ "name"',
                            '"name":',
                            "<|tool_call",
                        ]
                        for trg in triggers:
                            if trg in full_content:
                                json_start_idx = full_content.find(trg)
                                break
                        if json_start_idx == -1:
                            raw = full_content.lstrip()
                            if raw.startswith("{") or raw.startswith("`") or raw.startswith("<"):
                                json_start_idx = full_content.find(raw[0])

                        if json_start_idx != -1:
                            tool_detected = True
                            chunk_to_print = full_content[printed_idx:json_start_idx]
                            chunk_to_print = re.sub(r'[\s\}\]`>]*$', '', chunk_to_print)
                            if chunk_to_print.strip():
                                if tree.live.is_started:
                                    tree.stop()
                            if not json_spinner:
                                from .ui import LiveToolStream

                                json_spinner = LiveToolStream(source_id=source_id)
                                json_spinner.start()
                            json_spinner.update(full_content[json_start_idx:])
                            printed_idx = json_start_idx
                        else:
                            tail_len = 0
                            for partial in ["<", "<|", "<|tool", "<ex", "<exec", "<execute", "<execute_tool", "call", "`", "``", "```", "{"]:
                                if full_content.endswith(partial):
                                    tail_len = max(tail_len, len(partial))
                            safe_end = len(full_content) - tail_len
                            if safe_end > printed_idx:
                                chunk_to_print = full_content[printed_idx:safe_end]
                                if chunk_to_print:
                                    if tree.live.is_started:
                                        tree.stop()
                                printed_idx = safe_end
                if thinking:
                    full_thinking += thinking
                    if tree.lines == [low_thinking_placeholder]:
                        tree.lines.clear()
                    for char in thinking:
                        thinking_buffer += char
                        if char == "\n":
                            if thinking_buffer.strip():
                                tree.add_line(thinking_buffer.rstrip("\n"))
                                try:
                                    if tree.live.is_started:
                                        tree.live.refresh()
                                except Exception:
                                    pass
                            thinking_buffer = ""
                if tc:
                    tool_calls = tc
        except Exception as e:
            err_str = str(e).lower()
            if json_spinner:
                json_spinner.stop()
                json_spinner = None
            if tree.live.is_started:
                tree.stop()
            if any(w in err_str for w in ["exceed", "context window", "token"]) and ctx_oom_retries < 3:
                ctx_oom_retries += 1
                ctx.trigger_compaction(model)
                continue
            import traceback
            full_err = f"Error in subagent '{config.name}': {e}\n{traceback.format_exc()}"
            return SubAgentResult(
                status="failed",
                note=full_err,
                files_edited=sorted(tracker.edited),
                name=config.name,
            )
        finally:
            if json_spinner:
                json_spinner.stop()
            if thinking_buffer.strip():
                tree.add_line(thinking_buffer.strip())
            if not full_thinking and full_content:
                think_match = re.search(r"<think>(.*?)(?:</think>|$)", full_content, flags=re.DOTALL | re.IGNORECASE)
                if think_match:
                    full_thinking = think_match.group(1).strip()
            if (not tree.lines or tree.lines == [low_thinking_placeholder]) and full_thinking:
                tree.lines.clear()
                for line in full_thinking.splitlines():
                    if line.strip():
                        tree.add_line(line.strip())
            if tree.live.is_started:
                tree.stop()
            tree.print_tree()
            if tree.lines:
                did_print_content = True

        if not tool_calls:
            tool_calls = parse_tool_calls(full_content)

        if tool_calls:
            seen = set()
            unique = []
            for tc in tool_calls:
                func = tc.get("function", {})
                sig = json.dumps(
                    {"n": func.get("name"), "a": func.get("arguments")}, sort_keys=True
                )
                if sig not in seen:
                    seen.add(sig)
                    unique.append(tc)
            tool_calls = unique
        else:
            if full_content.strip():
                did_print_content = True
                _last_agent_text = full_content.strip()[:1000]
                console.print()
            if no_tool_retries < 2:
                no_tool_retries += 1
                ctx.add_assistant_message(full_content, thinking=full_thinking)
                ctx.add_user_message("SYSTEM DIRECTIVE: You did not issue a tool call. If your assigned task is COMPLETE, you MUST call finish_task(note=...). If work remains, issue a tool call now.")
                continue
            break

        ctx.add_assistant_message(
            full_content, tool_calls=tool_calls, thinking=full_thinking
        )

        for tc in tool_calls:
            did_print_content = True
            func = tc.get("function", {})
            name = func.get("name", "")
            if name == "finish_task":
                args_str = func.get("arguments", "{}")
                if isinstance(args_str, str):
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = args_str
                return SubAgentResult(
                    status="done",
                    note=args.get("note") or "Task completed.",
                    files_edited=sorted(tracker.edited),
                    name=config.name,
                )

        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "{}")

            if isinstance(args_str, str):
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    args = {"__json_error__": "Invalid JSON format in arguments"}
            else:
                args = args_str

            if not name or name == "syntax_error":
                print_tool_call(
                    "Invalid tool call",
                    "incomplete or malformed JSON",
                    source_id=source_id,
                )
                print_tool_result(
                    "Tool call rejected: invalid JSON arguments.",
                    source_id=source_id,
                )
                continue
            if name in ("finish_task", "wakeup_subagents"):
                continue
            if "path" not in args:
                for k in ["file", "filename", "file_path", "filepath"]:
                    if k in args:
                        args["path"] = args[k]
                        break
            if "query" not in args:
                for k in ["pattern", "search", "term", "text", "q"]:
                    if k in args:
                        args["query"] = args[k]
                        break
            if "pattern" not in args:
                for k in ["query", "search", "term", "glob_pattern"]:
                    if k in args:
                        args["pattern"] = args[k]
                        break
            for k in ["path", "pattern"]:
                if k in args and isinstance(args[k], str):
                    p = args[k].strip()
                    while (p.startswith('"') and p.endswith('"')) or (p.startswith("'") and p.endswith("'")):
                        p = p[1:-1].strip()
                    if p == "/" or p == "\\":
                        p = "."
                    elif (
                        (p.startswith("/") or p.startswith("\\"))
                        and not p.startswith("//")
                        and not p.startswith("\\\\")
                    ):
                        p = "." + p
                    args[k] = p
            if args.get("path") in (".", ".\\", "./") and cwd:
                args["path"] = cwd
            arg_summary = ""
            if "path" in args:
                p = args["path"].replace("\\", "/")
                parts = p.split("/")
                if len(parts) > 3:
                    arg_summary = ".../" + "/".join(parts[-3:])
                else:
                    arg_summary = p
            elif "command" in args:
                arg_summary = args["command"]
            elif "pattern" in args:
                arg_summary = f'"{args["pattern"]}"'
            elif "query" in args:
                arg_summary = f'"{args["query"]}"'
            elif "subagents" in args:
                arg_summary = ""
            else:
                arg_summary = (
                    str(args.get(list(args.keys())[0], ""))[:60] if args else ""
                )

            spinner = None

            if name in ("grep", "search_web", "code_search"):
                spinner = SearchSpinner(
                    args.get("query", args.get("pattern", "")),
                    tool_name=name,
                    source_id=source_id,
                )
                spinner.start()
            else:
                print_tool_call(name, arg_summary, source_id=source_id)

            if name in ("write_file", "create_file", "append_file"):
                print_code_panel(
                    os.path.abspath(args.get("path", "file")),
                    args.get("content", ""),
                    source_id=source_id,
                )
            elif name == "replace_lines":
                print_code_panel(
                    os.path.abspath(args.get("path", "file")),
                    args.get("new_content", ""),
                    source_id=source_id,
                )
            elif name == "edit_file":
                print_diff(
                    args.get("path", "file"),
                    args.get("old_str", ""),
                    args.get("new_str", ""),
                    source_id=source_id,
                )
            elif name in ("bash", "commands"):
                print_code_panel(
                    "Terminal",
                    args.get("command", ""),
                    lexer_override="bash",
                    source_id=source_id,
                )

            import copy

            execute_args = copy.deepcopy(args)
            if name == "run_tests":
                execute_args["source_id"] = source_id
            result = tracked_execute(
                name, execute_args, agent_instance=agent_instance, restricted_dir=cwd
            )

            if spinner:
                line_count = len(
                    [
                        l
                        for l in str(result).splitlines()
                        if l.strip() and "Results for" not in l
                    ]
                )
                spinner.stop(
                    f"{line_count} results"
                    if "Error" not in str(result)
                    else "Error",
                    details=str(result),
                )

            if name in ("bash", "commands"):
                if "Exit code: 0" in str(result):
                    print_tool_result("Command successful", source_id=source_id)
                else:
                    print_tool_result("Command failed", source_id=source_id)
            elif name in ("read_file", "read"):
                lines = len(str(result).splitlines())
                print_tool_result(f"Read {lines} lines", source_id=source_id)
            elif name in ("edit_file", "replace_lines", "append_file"):
                if "Error" not in str(result) and "No changes" not in str(result):
                    path_arg = (
                        args.get("path")
                        or args.get("target_file")
                        or args.get("file")
                        or args.get("filename")
                        or args.get("file_path")
                        or args.get("target")
                        or "file"
                    )
                    added = len(args.get("new_str", args.get("content", "")).splitlines())
                    removed = len(args.get("old_str", "").splitlines())
                    print_tool_result(
                        f"Edited {path_arg} ([green]+{added}[/] / [red]-{removed}[/])",
                        source_id=source_id,
                    )
            elif name in ("write_file", "create_file"):
                path_arg = (
                    args.get("path")
                    or args.get("target_file")
                    or args.get("file")
                    or args.get("filename")
                    or args.get("file_path")
                    or args.get("target")
                    or "file"
                )
                added = len(args.get("content", "").splitlines())
                print_tool_result(
                    f"Created/Overwritten {path_arg} ({added} lines)",
                    source_id=source_id,
                )
            elif name in ("ls", "glob"):  # code_search has spinner
                res_str = str(result)
                if "Error" not in res_str:
                    print_code_panel(
                        f"Result ({name})",
                        res_str,
                        lexer_override="text",
                        show_line_numbers=False,
                        source_id=source_id,
                    )
                else:
                    if name not in ("code_search", "grep", "search_web"):
                        print_tool_result(res_str, source_id=source_id, escape_text=True)
            elif name == "bugs":
                res_str = str(result)
                for line in res_str.splitlines():
                    print_tool_result(line, source_id=source_id, escape_text=True)
            elif name == "run_tests":
                if "exit 0" in str(result) or "Tests passed" in str(result):
                    print_tool_result("Tests passed", source_id=source_id)
                else:
                    print_tool_result(
                        str(result)[:120].replace("\n", " "),
                        source_id=source_id,
                        escape_text=True,
                    )
            elif name in ("answer", "ask_question"):
                from .ui import print_answer_summary
                print_answer_summary(str(result), source_id=source_id)
            else:
                res_str = str(result)
                if "Error" in res_str or "Traceback" in res_str:
                    for line in res_str.splitlines():
                        print_tool_result(line, source_id=source_id, escape_text=True)
                else:
                    print_tool_result(
                        res_str[:120].replace("\n", " ") + "..."
                        if len(res_str) > 120
                        else res_str.replace("\n", " "),
                        source_id=source_id,
                        escape_text=True,
                    )

            tool_result_str = str(result)
            if len(tool_result_str) > 3000:
                tool_result_str = tool_result_str[:3000] + f"\n... [truncated {len(tool_result_str) - 3000} chars]"
            ctx.add_tool_message(tool_result_str, tc.get("id") or "call_mock")

    note = ""
    if _last_agent_text and _last_agent_text.strip():
        note = _last_agent_text.strip()[:500]
    elif full_thinking and full_thinking.strip():
        clean_th = [l.strip() for l in full_thinking.splitlines() if l.strip() and not l.strip().startswith("|_")]
        if clean_th:
            note = clean_th[-1][:300]

    if not note:
        note = f"Zadanie '{config.name}' zostało zweryfikowane i przeanalizowane."

    return SubAgentResult(
        status="done",
        note=note,
        files_edited=sorted(tracker.edited),
        name=config.name,
    )



def run_subagents_sequential(
    configs: List[SubAgentConfig], agent_instance, cwd: str
) -> List[SubAgentResult]:
    model = getattr(agent_instance, "model", None)
    from .ui import ACCENT_COLOR
    from rich.markup import escape as esc
    import textwrap

    for cfg in configs:
        role_clean = esc(cfg.task.replace("\n", " ").strip())
        role_str = f" - Role: {role_clean}" if role_clean else ""
        lvl_val = esc(str(cfg.thinking_level or "auto").lower())
        lvl_str = f" (thinking: {lvl_val})"
        console.print(
            f"  [{MUTED_COLOR}]|_ {esc(cfg.name)}{role_str}{lvl_str}[/{MUTED_COLOR}]"
        )

    import shutil
    raw_cols = shutil.get_terminal_size(fallback=(100, 24)).columns
    term_width = max(80, (raw_cols if raw_cols >= 80 else 100) - 15)

    for i, cfg in enumerate(configs):
        _clear_vram(model=model)

        console.print(f"\n[{MUTED_COLOR}]╭ {esc(cfg.name)}[/{MUTED_COLOR}]")
        task_clean = cfg.task.replace("\n", " ").strip() if cfg.task else "Subagent task execution"
        task_lines = textwrap.wrap(task_clean, width=term_width) or [task_clean]
        for idx, tl in enumerate(task_lines):
            prefix = "Role: " if idx == 0 else "      "
            console.print(
                f"[{MUTED_COLOR}]│   [dim]{prefix}{esc(tl)}[/dim][/{MUTED_COLOR}]"
            )

        think_lvl = str(cfg.thinking_level or "auto").lower()
        console.print(
            f"[{MUTED_COLOR}]│   [dim]Thinking level: {esc(think_lvl)}[/dim][/{MUTED_COLOR}]"
        )
        console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")
        console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")
        import sys
        sys.stdout.flush()

        result = run_subagent(cfg, agent_instance, cwd, source_id=cfg.name)

        status_icon = "✓" if result.status == "done" else "✗"
        status_color = SUCCESS_COLOR if result.status == "done" else ERROR_COLOR
        console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")
        console.print(
            f"[{MUTED_COLOR}]│ ● Agent Note[/{MUTED_COLOR}]"
        )
        note_str = str(getattr(result, "note", "") or "")
        for note_line in note_str.splitlines():
            wrapped = textwrap.wrap(note_line, width=term_width)
            for j, wl in enumerate(wrapped):
                prefix = f"|_ [{status_color}]{status_icon}[/{status_color}] " if j == 0 else "|_    "
                escaped = esc(wl)
                console.print(
                    f"[{MUTED_COLOR}]│   {prefix}{escaped}[/{MUTED_COLOR}]"
                )

        if result.files_edited:
            files_escaped = [esc(f) for f in result.files_edited]
            console.print(
                f"[{MUTED_COLOR}]│   [dim]Files: {', '.join(files_escaped)}[/dim][/{MUTED_COLOR}]"
            )
        console.print(f"[{MUTED_COLOR}]╰[/{MUTED_COLOR}]")

        results.append(result)

    console.print(f"  [{MUTED_COLOR}]● Subagents[/{MUTED_COLOR}]")
    for r, cfg in zip(results, configs):
        status_sym = "✓" if r.status == "done" else "✗"
        status_color = SUCCESS_COLOR if r.status == "done" else ERROR_COLOR
        files_str = f" ({', '.join(esc(f) for f in r.files_edited)})" if r.files_edited else ""
        role_clean = esc(cfg.task.replace("\n", " ").strip())
        if len(role_clean) > 60:
            role_clean = role_clean[:60] + "..."
        role_str = f" - Role: {role_clean}" if role_clean else ""
        lvl_val = esc(cfg.thinking_level or "auto")
        lvl_str = f" (thinking: {lvl_val})"
        console.print(
            f"  [{MUTED_COLOR}]|_ {esc(r.name)}{role_str}{lvl_str} [{status_color}]{status_sym}[/{status_color}]{files_str}[/{MUTED_COLOR}]"
        )
    _clear_vram(model=model)
    return results
