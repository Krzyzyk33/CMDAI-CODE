import os
import json
from .llama import LlamaModel
from .context import ContextManager
from .ui import (
    print_user_msg,
    print_agent_msg,
    print_tool_call,
    print_tool_result,
    print_diff,
    print_code_panel,
    ThinkingTree,
    console,
    MUTED_COLOR,
)
from .tools import TOOLS_DEFINITIONS, execute_tool


class Agent:
    def __init__(self, model: LlamaModel, context: ContextManager):
        self.model = model
        self.context = context
        self.max_iterations = 25
        self.auto_review = False

    def get_tool_desc(self) -> str:
        desc = ""
        for t in TOOLS_DEFINITIONS:
            f = t["function"]
            args_desc = []
            if "parameters" in f and "properties" in f["parameters"]:
                for arg_name, arg_info in f["parameters"]["properties"].items():
                    args_desc.append(f"{arg_name} ({arg_info.get('type', 'any')})")
            args_str = ", ".join(args_desc)
            desc += f"- {f['name']}({args_str}): {f['description']}\n"
        return desc

    def handle_user_input(self, user_msg: str, mode: str, input_handler):
        import time
        import os
        import re
        from .ui import print_turn_done

        words = [
            w
            for w in set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{3,}\b", user_msg))
            if w.lower()
            not in {
                "this",
                "that",
                "with",
                "what",
                "how",
                "why",
                "where",
                "when",
                "can",
                "you",
                "please",
                "make",
                "sure",
                "add",
                "remove",
                "update",
                "fix",
                "bug",
                "error",
                "file",
                "code",
                "the",
                "and",
                "for",
            }
        ]

        auto_context_msg = ""
        at_files = re.findall(r"@([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)", user_msg)
        if at_files:
            for af in at_files:
                af_path = os.path.abspath(af) if not os.path.isabs(af) else af
                if os.path.isfile(af_path):
                    try:
                        with open(af_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        lines = content.splitlines()
                        if len(lines) > 300:
                            content = "\n".join(lines[:300]) + f"\n... [truncated {len(lines)-300} lines]"
                        auto_context_msg += f"\n\n[USER ATTACHED FILE: {af}]\n{content}\n"
                        console.print(f"[{MUTED_COLOR}]  |_ Attached file: {af}[/]")
                    except Exception:
                        pass
        if words:
            from .indexer.scan import get_project_dir
            from .indexer.symbol_db import SymbolDB

            try:
                db_path = os.path.join(get_project_dir(os.getcwd()), "index.db")
                if os.path.exists(db_path):
                    db = SymbolDB(db_path)
                    found_files = set()

                    query = " OR ".join(words[:5])
                    results = db.search_symbols(query)

                    for r in results:
                        found_files.add(r["filepath"])
                        if len(found_files) >= 3:
                            break

                    if found_files:
                        auto_context_msg = (
                            "\n\n[AUTO-CONTEXT: relevant files found via indexer]\n"
                        )
                        for fpath in found_files:
                            try:
                                with open(fpath, "r", encoding="utf-8") as f:
                                    content = f.read()
                                    lines = content.splitlines()
                                    if len(lines) > 200:
                                        content = (
                                            "\n".join(lines[:200]) + "\n... (truncated)"
                                        )
                                    auto_context_msg += (
                                        f"\n--- {fpath} ---\n{content}\n"
                                    )
                            except Exception:
                                pass

                        console.print(
                            f"[{MUTED_COLOR}]  |_ Auto-Context: attached {len(found_files)} files based on keywords.[/]"
                        )
            except Exception:
                pass

        self.context.add_user_message(
            user_msg + auto_context_msg,
            attached_files=at_files if 'at_files' in locals() and at_files else None,
        )

        self._has_reflected = False
        turn_start = time.time()
        start_tokens = self.context.get_token_count()
        total_tools = 0
        iteration = 0
        modified_files = set()
        self._fable_tested = False
        while iteration < self.max_iterations:
            if self.context.get_token_count() >= 7000:
                self.context.trigger_compaction(self.model)

            iteration += 1
            tree = ThinkingTree(expanded=input_handler.thinking_expanded)
            if iteration > 1:
                tree._printed_tree = True

            level_info = input_handler.thinking_levels[input_handler.thinking_idx]
            level_idx = input_handler.thinking_idx
            low_thinking_placeholder = "NEXT_ACTION: Selecting the next tool."
            if level_idx == 0:
                tree.add_line(low_thinking_placeholder)

            def set_low_next_action(tool_name: str, path: str = ""):
                if level_idx != 0 or tree.lines != [low_thinking_placeholder]:
                    return
                verbs = {
                    "read_file": "Reading",
                    "write_file": "Creating",
                    "create_file": "Creating",
                    "edit_file": "Editing",
                    "replace_lines": "Editing",
                    "append_file": "Appending to",
                    "delete_file": "Deleting",
                    "glob": "Searching files with",
                    "code_search": "Searching code for",
                    "wakeup_subagents": "Delegating work to subagents",
                    "run_tests": "Running tests for",
                }
                label = verbs.get(tool_name.lower(), f"Running {tool_name}")
                target = f" {path}" if path else ""
                tree.lines = [f"NEXT_ACTION: {label}{target}."]

            if level_idx == 0:
                thinking_desc = "IF you generate thoughts, you MUST wrap them inside <think> and </think> tags.\nTHINKING BEHAVIOR - LOW:\nAt the beginning of EVERY turn, generate exactly one short THINK block, then immediately act:\n<think>\n  |_ NEXT_ACTION: <one concrete next tool action, max 12 words>\n</think>\n\nDo not add UNDERSTAND, CONTEXT, OPTIONS, CHOICE, PLAN, or sub-trees. Do not generate another THINK after a successful action. Only after an error, write one line: `|_ ISSUE: <short error and next action>`, then call the next tool. Do not perform additional 'just in case' actions (build, search) unless the user asked for it.\nCRITICAL: ALWAYS respond to the user in their own language (e.g., Polish)."
            elif level_idx == 1:
                thinking_desc = "IF you generate thoughts, you MUST wrap them inside <think> and </think> tags.\nTHINKING BEHAVIOR - MEDIUM:\nAt the beginning of the turn, generate a concise THINK strictly inside <think> and </think> tags:\n<think>\n  |_ UNDERSTAND: <what the task is about>\n  |_ PLAN: <short list of steps (max 3)>\n  |_ NEXT_ACTION: <what you will do next, or 'none' if task is fully complete>\n</think>\n\nDo not generate sub-trees.\nAfter reading/searching, DO NOT generate THINK, unless the result differs drastically from expectations.\nAfter modifying actions (Edit/Write/Bash), you may generate a short THINK (1-2 lines) if the plan needs updating.\n\nForced actions: Search all callers after changing a function signature (ACTION: Search). Do not build the project unless requested.\nCRITICAL: ALWAYS respond to the user in their own language (e.g., Polish)."
            elif level_idx == 2:
                thinking_desc = "IF you generate thoughts, you MUST wrap them inside <think> and </think> tags.\nTHINKING BEHAVIOR - HIGH:\nAt the beginning of the turn, generate a structured THINK strictly inside <think> and </think> tags:\n<think>\n  |_ UNDERSTAND: <what the task is about>\n  |_ OPTIONS: <option A> | <option B> (only for complex or ambiguous problems)\n  |_ CHOICE: <chosen option + brief justification>\n  |_ PLAN: <numbered list of steps>\n  |_ NEXT_ACTION: <what you will do next, or 'none' if task is fully complete>\n</think>\n\nUse sub-trees (indented lists) only for significant architectural decisions.\nAfter key actions, generate a short THINK confirming status:\n<think>\n  |_ CONFIRMATION: <status + next step>\n  |_ NEXT_ACTION: <what you will do next, or 'none' if task is fully complete>\n</think>\n\nForced actions: ALWAYS search callers after changing a function signature. ALWAYS run a build after a block of related code edits.\nCRITICAL: ALWAYS respond to the user in their own language (e.g., Polish)."
            elif level_idx == 3:
                thinking_desc = "IF you generate thoughts, you MUST wrap them inside <think> and </think> tags.\nTHINKING BEHAVIOR - ULTRA:\nAt the beginning of EACH turn, generate a THINK strictly inside <think> and </think> tags:\n<think>\n  |_ UNDERSTAND: <what the task is about>\n  |_ CONTEXT: <what you already know from previous actions>\n  |_ OPTIONS: <option A> | <option B>\n  |_ CHOICE: <chosen option + justification>\n  |_ RISK: <potential problem + mitigation>\n  |_ PLAN: <numbered list of steps>\n\nFor EACH PLAN point that has more than one execution method, write a sub-tree:\n    |_ <plan point>\n      |_ > option 1\n      |_ > option 2\n      |_ > choice + justification\n  |_ NEXT_ACTION: <what you will do next, or 'none' if task is fully complete>\n</think>\n\nAfter EACH action, generate a new THINK:\n<think>\n  |_ CONFIRMATION: <did the result match expectations? what next?>\n  |_ NEXT_ACTION: <what you will do next, or 'none' if task is fully complete>\n</think>\n\nForced actions: ALWAYS search callers after changing a signature. ALWAYS run a build after editing code.\nCRITICAL: ALWAYS respond to the user in their own language (e.g., Polish)."
            elif level_idx == 4:
                thinking_desc = "IF you generate thoughts, you MUST wrap them inside <think> and </think> tags.\nTHINKING BEHAVIOR - EXTREME:\nAt the beginning of EACH turn, analyze the entire project. Generate an exhaustive THINK strictly inside <think> and </think> tags:\n<think>\n  |_ UNDERSTAND: <what the task is about on a project scale>\n  |_ CONTEXT: <conclusions from logs, file structures, and architecture>\n  |_ OPTIONS: <extensive list of options and paths>\n  |_ CHOICE: <final choice + impact on other modules>\n  |_ RISK: <detailed list of edge cases, security, and performance>\n  |_ PLAN: <very detailed list of steps>\n\nFor EACH PLAN point, you must write a multi-level sub-tree. In the first iteration, you MUST use the 'submit_plan' tool.\n  |_ NEXT_ACTION: <what you will do next, or 'none' if task is fully complete>\n</think>\n\nAfter EACH action, perform a deep evaluation:\n<think>\n  |_ EVALUATION: <detailed analysis of the returned result, error diagnosis, plan corrections>\n  |_ NEXT_ACTION: <what you will do next, or 'none' if task is fully complete>\n</think>\n\nForced actions: ALWAYS search all dependencies. ALWAYS run build and unit tests after EACH file edit. Perform a re-validation procedure at the end.\nCRITICAL: ALWAYS respond to the user in their own language (e.g., Polish)."

            if mode == "plan":
                thinking_desc += "\n\nCRITICAL: You are in PLAN MODE. You can ONLY use read_file, list_dir, glob, grep, save_plan, and submit_plan. You do NOT have write_file, edit_file, delete_file, bash, code_search, search_web, replace_lines, append_file, run_python, bugs, run_tests, wakeup_subagents, or any other modifying tool. You CANNOT modify code or execute commands."
                allowed_tools = [
                    "read_file",
                    "list_dir",
                    "glob",
                    "grep",
                    "save_plan",
                    "submit_plan",
                    "answer",
                    "ask_question",
                ]
                filtered_tools = [
                    t
                    for t in TOOLS_DEFINITIONS
                    if t["function"]["name"] in allowed_tools
                ]
            else:
                thinking_desc += f"\n\nCRITICAL: For THIS CURRENT TURN, your THINKING TOKEN BUDGET is exactly {level_info[1]} tokens. If you feel you have only 10% of your thinking capacity left, you MUST IMMEDIATELY stop planning, close the <think> block, and output the JSON tool call! Never run out of tokens before generating the tool.\nCRITICAL: NEVER write 'TOOL: None' unless you are absolutely 100% finished with the user's task and have no code left to write. If the user asks for code, you MUST use a tool (e.g. write_file)!\nCRITICAL: Writing 'TOOL: <name>' in your thought block is NOT enough. You MUST actually trigger the native JSON function calling mechanism immediately after closing the <think> block!\nCRITICAL: IF YOU DO NOT KNOW something (a function name, file path, how a library works, how to fix a specific error), DO NOT GUESS! ALWAYS search before taking action.\nCRITICAL Tool Usage Guide:\n1) 'read_file': use ONLY to read the content of a specific file.\n2) 'glob': use to search for FILE PATHS by name or pattern.\n3) 'code_search': use ONLY to search for text/symbols/functions INSIDE files.\nDO NOT pass file paths as the query into 'code_search'! It searches file contents, not paths.\n4) 'bugs': use to scan the project for syntax errors and retrieve line numbers for broken files marked with a red dot.\n5) 'replace_lines': use to precisely edit fragments by providing start_line and end_line. ALWAYS use this instead of write_file for editing existing files!\n6) 'append_file': use to quickly add new code to the very end of a file.\nNEVER use write_file to modify an existing file! write_file is ONLY for completely new files.\n7) NEVER add any comments (e.g., lines starting with #) to the code you write or edit. The code MUST be completely clean and free of any annotations or comments."
                disallowed_tools = ["save_plan"]
                if level_idx >= 3 and iteration == 1:
                    allowed = [
                        "read_file",
                        "list_dir",
                        "grep",
                        "search_web",
                        "submit_plan",
                        "wakeup_subagents",
                        "answer",
                        "ask_question",
                    ]
                    filtered_tools = [
                        t for t in TOOLS_DEFINITIONS if t["function"]["name"] in allowed
                    ]
                else:
                    if level_idx < 3:
                        disallowed_tools.append("submit_plan")
                    filtered_tools = [
                        t
                        for t in TOOLS_DEFINITIONS
                        if t["function"]["name"] not in disallowed_tools
                    ]

            messages = self.context.get_messages(
                self.get_tool_desc(), thinking_desc=thinking_desc
            )

            use_native_tools = level_idx == 0
            if not use_native_tools:
                messages[0]["content"] += (
                    "\n\nCRITICAL: You are configured to use TEXT-BASED TOOL CALLING. You MUST NOT use native function calling. Output your tool call as a JSON markdown block after your <think> block."
                )
                passed_tools = None
            else:
                passed_tools = filtered_tools

            try:
                grammar_path = None
                if use_native_tools and passed_tools:
                    import os

                    grammar_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "grammars",
                        "tool_call.gbnf",
                    )

                if hasattr(self.model, "llm"):
                    _ = self.model.llm

                kwargs = {}
                if grammar_path and type(self.model).__name__ == "LlamaModel":
                    kwargs["grammar_path"] = grammar_path

                stream = self.model.stream_chat(
                    messages,
                    tools=passed_tools,
                    reasoning_budget=level_info[1],
                    **kwargs,
                )
            except Exception as e:
                if tree.live.is_started:
                    tree.stop()
                console.print(
                    f"\n[red bold]Model loading error: {e}[/red bold]\n[yellow]Type /model to switch to a valid model![/yellow]"
                )
                return

            full_content = ""
            full_thinking = ""
            tool_calls = None
            thinking_newline = True

            in_thinking_tree = False
            thinking_buffer = ""

            plan_finished = False
            line_buffer = ""
            first_line_checked = False

            tool_detected = False
            printed_idx = 0
            content_to_print = ""
            tree_printed = False
            json_spinner = None

            try:
                esc_count = 0
                import os

                if os.name == "nt":
                    import msvcrt

                for content, thinking, tc in stream:
                    if os.name == "nt" and msvcrt.kbhit():
                        while msvcrt.kbhit():
                            key = msvcrt.getch()
                            if key == b"\x1b":
                                esc_count += 1
                            elif key in (b"\x00", b"\xe0"):
                                if msvcrt.kbhit():
                                    msvcrt.getch()
                                esc_count = 0
                            elif key == b"\x08":
                                input_handler.pending_typeahead = (
                                    input_handler.pending_typeahead[:-1]
                                )
                                esc_count = 0
                            elif key in (b"\r", b"\n", b"\t"):
                                esc_count = 0
                            else:
                                esc_count = 0
                                if 0x20 <= key[0] <= 0x7E:
                                    input_handler.pending_typeahead += key.decode(
                                        "ascii", errors="ignore"
                                    )
                        if esc_count >= 2:
                            from rich.console import Console

                            Console().print(
                                "\n\n[red bold][Generowanie przerwane przez klawisz ESC][/red bold]\n"
                            )
                            break

                    if content:
                        full_content += content
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
                                chunk_to_print = full_content[
                                    printed_idx:json_start_idx
                                ]
                                chunk_to_print = re.sub(r'[\s\}\]`>]*$', '', chunk_to_print)
                                if chunk_to_print:
                                    if tree.live.is_started:
                                        tree.stop()
                                        console.print()
                                    import sys

                                    console.print(chunk_to_print, end="", markup=False)
                                    sys.stdout.flush()
                                printed_idx = json_start_idx

                                if tree.live.is_started:
                                    tree.stop()
                                    console.print()
                                elif tree.lines == [low_thinking_placeholder]:
                                    m_tool = re.search(r'"name"\s*:\s*"([^\"]+)"', full_content[json_start_idx:])
                                    set_low_next_action(m_tool.group(1) if m_tool else "tool")
                                    tree.print_tree()
                                if not json_spinner:
                                    from .ui import LiveToolStream

                                    json_spinner = LiveToolStream()
                                    json_spinner.start()
                                if json_spinner:
                                    json_spinner.update(full_content[json_start_idx:])
                            else:
                                if json_spinner:
                                    json_spinner.stop()
                                    json_spinner = None
                                tail_len = 0
                                for partial in ["<", "<|", "<|tool", "<|tool_call", "<ex", "<exec", "<execute", "<execute_tool", "call", "`", "``", "```", "{"]:
                                    if full_content.endswith(partial):
                                        tail_len = max(tail_len, len(partial))
                                safe_end = len(full_content) - tail_len
                                if safe_end > printed_idx:
                                    chunk = full_content[printed_idx:safe_end]
                                    if chunk:
                                        should_hide = (
                                            bool(modified_files) and not self._fable_tested
                                        )
                                        if not should_hide:
                                            if tree.live.is_started:
                                                tree.stop()
                                                console.print()
                                            import sys

                                            console.print(chunk, end="", markup=False)
                                            sys.stdout.flush()
                                        printed_idx = safe_end

                    if thinking:
                        full_thinking += thinking
                        if tree.lines == [low_thinking_placeholder]:
                            tree.lines.clear()
                        if not tree.live.is_started:
                            tree.start()
                        for char in thinking:
                            thinking_buffer += char
                            if char == "\n":
                                if thinking_buffer.strip():
                                    tree.add_line(thinking_buffer.rstrip("\n"))
                                    try:
                                        tree.live.refresh()
                                    except Exception:
                                        pass
                                thinking_buffer = ""

                    if tc:
                        tool_detected = True
                        tool_calls = tc
                        if tree.live.is_started:
                            tree.stop()
                            console.print()
                        elif tree.lines == [low_thinking_placeholder]:
                            func = tc[0].get("function", {}) if isinstance(tc, list) and tc else {}
                            raw_args = func.get("arguments", {})
                            if isinstance(raw_args, str):
                                try:
                                    raw_args = json.loads(raw_args)
                                except Exception:
                                    raw_args = {}
                            path = raw_args.get("path", raw_args.get("file", "")) if isinstance(raw_args, dict) else ""
                            set_low_next_action(func.get("name", "tool"), str(path or ""))
                            tree.print_tree()
                        if not json_spinner:
                            from .ui import LiveToolStream

                            json_spinner = LiveToolStream()
                            json_spinner.start()
                        if json_spinner:
                            json_spinner.update(full_content)

                if not tool_detected and printed_idx < len(full_content):
                    content_to_print += full_content[printed_idx:]
                    printed_idx = len(full_content)

            except Exception as e:
                error_str = str(e)
                if "exceed context window" in error_str:
                    console.print(
                        f"\n[red bold]Error: Context memory overflow (too much text): {error_str}[/red bold]\n[yellow]Clear history by typing /clear, or shorten your last prompt.[/yellow]"
                    )
                else:
                    console.print(
                        f"\n[red bold]Critical engine error during generation: {error_str}[/red bold]\n[yellow]Most likely out of VRAM (Out Of Memory) or model format is not fully supported.[/yellow]"
                    )
                return
            finally:
                if json_spinner:
                    json_spinner.stop()
                if thinking_buffer.strip():
                    tree.add_line(thinking_buffer.strip())
                # Stop the live spinner but DON'T print the tree yet.
                # We defer printing until after the retry check below,
                # so that failed attempts (thinking-only, no action) don't
                # print a duplicate ThinkingTree before the retry.
                if tree.live.is_started:
                    tree.live.stop()

            if not tool_calls:
                full_content_str = full_content
                import re, json, ast

                def robust_json_parse(s):
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

                mock_calls = []
                call_matches = re.findall(r"call:([a-zA-Z0-9_]+)\s*(\{.*)", full_content_str, re.DOTALL)
                if call_matches:
                    for fn_name, fn_args_raw in call_matches:
                        parsed_args = robust_json_parse(fn_args_raw)
                        if parsed_args or fn_args_raw.strip().startswith("{"):
                            mock_calls.append({
                                "id": "call_mock",
                                "type": "function",
                                "function": {
                                    "name": fn_name,
                                    "arguments": parsed_args
                                }
                            })
                    if mock_calls:
                        tool_calls = mock_calls

                if not tool_calls:
                    tc_tag_blocks = re.findall(r"<\|?tool_call\|?>\s*(.*?)\s*(?:</tool_call>|<tool_call\|>|<\|tool_call\|>|$)", full_content_str, re.DOTALL)
                    if tc_tag_blocks:
                        for block in tc_tag_blocks:
                            parsed = robust_json_parse(block.strip())
                            if isinstance(parsed, dict) and "name" in parsed:
                                fn_name = parsed["name"]
                                args = parsed.get("arguments", parsed.get("args", {}))
                                if isinstance(args, str):
                                    args = robust_json_parse(args)
                                mock_calls.append({
                                    "id": "call_mock",
                                    "type": "function",
                                    "function": {"name": fn_name, "arguments": args}
                                })
                        if mock_calls:
                            tool_calls = mock_calls

                if not tool_calls:
                    json_blocks = re.findall(r"```json\s*(.*?)\s*```", full_content_str, re.DOTALL)
                    if not json_blocks:
                        json_blocks = re.findall(r"<json>\s*(.*?)\s*</json>", full_content_str, re.DOTALL)
                    if not json_blocks:
                        idx = full_content_str.find("{")
                        if idx != -1:
                            json_blocks = [full_content_str[idx:].strip()]
                    if json_blocks:
                        for b in json_blocks:
                            parsed = robust_json_parse(b)
                            if isinstance(parsed, dict) and "name" in parsed:
                                fn_name = parsed["name"]
                                args = parsed.get("arguments", parsed.get("args", {}))
                                if isinstance(args, str):
                                    args = robust_json_parse(args)
                                mock_calls.append({
                                    "id": "call_mock",
                                    "type": "function",
                                    "function": {"name": fn_name, "arguments": args}
                                })
                        if mock_calls:
                            tool_calls = mock_calls

            if tool_calls:
                unique_calls = []
                seen_sigs = set()
                for tc in tool_calls:
                    func = tc.get("function", {})
                    sig = json.dumps(
                        {"name": func.get("name"), "args": func.get("arguments")},
                        sort_keys=True,
                    )
                    if sig not in seen_sigs:
                        seen_sigs.add(sig)
                        unique_calls.append(tc)
                tool_calls = unique_calls

            self.self_correction_loops = getattr(self, "self_correction_loops", 0)

            if not full_content.strip() and not tool_calls:
                self.self_correction_loops += 1
                if self.self_correction_loops >= 5:
                    console.print(
                        "\n[red]⚠️ Self-correction loop interrupted (reached limit of 5 attempts).[/red]"
                    )
                    break
                # Mark the tree as printed so it won't produce duplicate output on retry
                tree._printed_tree = True
                tree._is_stopped = True
                tree.lines.clear()
                if not full_thinking.strip():
                    console.print(
                        "\n[yellow]⚠️ Model generated completely empty response. Retrying (self-correction)...[/yellow]"
                    )
                    self.context.add_user_message(
                        "System Error: You returned an empty response. Remember to generate a <think> block (if required) and take action or provide a text response."
                    )
                else:
                    self.context.trigger_compaction(self.model)
                    self.context.add_user_message(
                        "System Error: You generated a very long <think> block and stopped abruptly because you ran out of tokens! I have just compressed your context history to give you more free space. You MUST now use a tool (e.g. write_file) immediately to continue your work! Limit your thinking to 1 sentence and output the tool call."
                    )
                continue

            # Print the thinking tree only for successful responses
            # (not retried attempts that produced only thinking and no action).
            tree.stop()

            if (
                level_idx > 0
                and not full_thinking.strip()
                and not ("<think>" in full_content)
            ):
                console.print(
                    "\n[dim yellow]?? Warning: Model ignored the <think> structure, but the application continues...[/dim yellow]"
                )

            import re

            sid = self.context.session_manager.current_state.session_id
            is_new_session = sid in ("default", "None", None) or (
                isinstance(sid, str) and bool(re.match(r"^[0-9a-f]{8}$", sid))
            )

            if is_new_session:
                understand_text = ""
                if "UNDERSTAND:" in full_thinking:
                    understand_match = re.search(
                        r"UNDERSTAND:\s*(.*?)(?:\n\s*\|_|</think>|$)",
                        full_thinking,
                        re.IGNORECASE | re.DOTALL,
                    )
                    if understand_match:
                        understand_text = understand_match.group(1).strip()

                if not understand_text:
                    last_msg = next(
                        (
                            m["content"]
                            for m in reversed(self.context.messages)
                            if m["role"] == "user"
                        ),
                        "",
                    )
                    understand_text = last_msg[:500]

                if understand_text:
                    self.rename_session_from_understanding(understand_text)

            if not tool_calls:
                import re

                tool_match = re.search(
                    r"NEXT_ACTION:\s*(.*)", full_thinking, re.IGNORECASE
                )
                if (
                    tool_match
                    and tool_match.group(1).strip().lower() != "none"
                    and tool_match.group(1).strip().lower() != "'none'"
                ):
                    self.self_correction_loops += 1
                    if self.self_correction_loops >= 5:
                        console.print(
                            "\n[red]⚠️ Self-correction loop interrupted (reached limit of 5 attempts).[/red]"
                        )
                        break
                    action_text = tool_match.group(1).strip()
                    action_text = tool_match.group(1).strip()

                    reason = "Nieznany powód (całkowity brak struktury)."
                    if "```" in full_content and "{" not in full_content:
                        reason = "Model wygenerował kod jako czysty blok Markdown, całkowicie ignorując wymóg użycia narzędzia JSON."
                    elif "name" in full_content and "arguments" not in full_content:
                        reason = "Model pominął wymaganą klamrę 'arguments', próbując podać parametry bezpośrednio w głównym drzewie JSON."
                    elif "{" in full_content and '"name"' not in full_content:
                        reason = "Model stworzył JSON, ale prawdopodobnie pominął cudzysłowy wokół słowa 'name' lub użył apostrofów."
                    elif "<think>" in full_content and full_content.rfind(
                        "{"
                    ) < full_content.rfind("</think>"):
                        reason = "Model umieścił obiekt JSON wewnątrz bloku <think>, zamiast wywołać go po zakończeniu myślenia."
                    elif full_content.count("{") > full_content.count("}"):
                        reason = "Kod został tak gwałtownie ucięty, że nawet system Rescue Mode nie był w stanie poskładać uszkodzonych klamer."

                    self.context.add_assistant_message(full_content)
                    self.context.add_user_message(
                        f'Błąd Systemowy: Zaplanowałeś akcję w tagu <think>: \'{action_text}\', ale zawiodłeś. Zamiast wygenerować poprawne natywne wywołanie narzędzia, najprawdopodobniej zapomniałeś o wymaganej strukturze (klucz \'arguments\') lub wypisałeś surowy tekst. Twój JSON musi w 100% wyglądać tak: {{\n  "name": "nazwa_narzedzia",\n  "arguments": {{\n    "parametr": "wartosc"\n  }}\n}}\nPopraw swój błąd i wygeneruj poprawne wywołanie narzędzia już teraz.'
                    )
                    continue

                if modified_files and not self._fable_tested:
                    self._fable_tested = True
                    import sys

                    sys_path_added = False
                    if "src" not in sys.path:
                        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                        sys_path_added = True

                    from tools.execution import run_tests

                    if not getattr(self, "test_tool_printed", False):
                        print_tool_call("tests", "Running tests on modified files")
                        self.test_tool_printed = True

                    test_results = run_tests(list(modified_files))

                    if sys_path_added:
                        sys.path.pop(0)

                    if "Exit code: 0" in test_results and "Error" not in test_results:
                        console.print(
                            f"  [{MUTED_COLOR}]⎿  attempt {getattr(self, 'fix_attempts', 0) + 1}/3...[/]"
                        )
                        console.print("  [green]⎿  done[/green]")
                        self.test_tool_printed = False
                        if full_content:
                            console.print(full_content)
                        self.context.add_assistant_message(full_content)
                        break
                    elif (
                        "ℹ No test framework detected — syntax check only (OK)"
                        in test_results
                    ):
                        console.print(
                            f"  [{MUTED_COLOR}]⎿  attempt {getattr(self, 'fix_attempts', 0) + 1}/3...[/]"
                        )
                        console.print("  [green]⎿  done[/green]")
                        self.test_tool_printed = False
                        if full_content:
                            console.print(full_content)
                        self.context.add_assistant_message(full_content)
                        break
                    else:
                        self.fix_attempts = getattr(self, "fix_attempts", 0) + 1
                        console.print(
                            f"  [{MUTED_COLOR}]⎿  attempt {self.fix_attempts}/3...[/]"
                        )
                        if self.fix_attempts > 3:
                            console.print("  [red]⎿  canceled[/red]")
                            self.test_tool_printed = False
                            self.context.add_assistant_message(full_content)
                            break
                        else:
                            self.context.add_assistant_message(full_content)
                            self.context.add_user_message(
                                f"System Error (Auto-Test): Tests for modified files failed. Ignore previous successes. You MUST fix these errors:\n\n{test_results}\n\nUse tools (e.g. code_search, edit_file) to diagnose and fix the issue."
                            )
                            continue

                self.context.add_assistant_message(full_content)
                break

            self.context.add_assistant_message(
                full_content, tool_calls=tool_calls, thinking=full_thinking
            )
            total_tools += len(tool_calls)

            sig = json.dumps(
                [
                    {
                        "name": tc["function"]["name"],
                        "args": tc["function"].get("arguments", "{}"),
                    }
                    for tc in tool_calls
                ]
            )
            if getattr(self, "last_tool_sig", None) == sig:
                self.consecutive_identical_calls = (
                    getattr(self, "consecutive_identical_calls", 0) + 1
                )
            else:
                self.consecutive_identical_calls = 0
                self.last_tool_sig = sig

            if self.consecutive_identical_calls == 9:
                console.print(
                    "\n[red]⚠️ Loop detected (model repeated the same faulty tool 10 times). Forcing change of approach...[/red]"
                )
                self.context.add_user_message(
                    "System Error: You are stuck in a loop. You repeated the EXACT SAME TOOL CALL 10 times and it failed. DO NOT USE THIS EXACT TOOL CALL AGAIN. You must change your approach, use a different tool, or fix the arguments."
                )
                continue
            elif self.consecutive_identical_calls >= 11:
                console.print(
                    "\n[red]⚠️ Auto-execution stopped: Fatal loop detected.[/red]"
                )
                self.context.add_user_message(
                    "System: Fatal loop detected. Execution paused."
                )
                break

            for tc in tool_calls:
                func = tc["function"]
                name = func.get("name", "")
                name_lower = name.lower()
                args_str = func.get("arguments", "{}")

                if not name:
                    print_tool_call("Invalid tool call", "incomplete or malformed JSON")
                    console.print(
                        "\n[red]⚠ Interrupted tool call (truncated by token limit).[/red]"
                    )
                    self.context.add_tool_message(
                        tc.get("id", "unknown"),
                        "unknown",
                        "System Error: Your tool call was incomplete because you hit the max_tokens limit. Keep your thinking block much shorter and try again.",
                    )
                    continue

                if isinstance(args_str, dict):
                    args = args_str
                    args_str = json.dumps(args)
                else:
                    try:
                        args = json.loads(args_str)
                    except Exception:
                        args = {"__json_error__": "Invalid JSON format in arguments"}

                if "path" not in args:
                    for k in ["file", "filename", "file_path", "filepath"]:
                        if k in args:
                            args["path"] = args[k]
                            break
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
                elif "step_number" in args:
                    arg_summary = str(args["step_number"])
                elif "questions" in args:
                    qs = args["questions"]
                    if isinstance(qs, list) and len(qs) > 0:
                        first_q = qs[0].get("question", "") if isinstance(qs[0], dict) else str(qs[0])
                        if len(qs) > 1:
                            arg_summary = f'"{first_q}" (+{len(qs)-1} more)'
                        else:
                            arg_summary = f'"{first_q}"'
                    else:
                        arg_summary = "questions"

                spinner = None
                if name_lower in ["grep", "search_web"]:
                    from .ui import SearchSpinner

                    spinner = SearchSpinner(
                        args.get("query", args.get("pattern", "")), tool_name=name
                    )
                    spinner.start()
                if not spinner:
                    print_tool_call(name, arg_summary)

                is_md_in_plan = (
                    mode == "plan"
                    and name in ["write_file", "create_file", "edit_file"]
                    and args.get("path", "").endswith(".md")
                )

                if (
                    mode == "plan"
                    and name
                    in [
                        "write_file",
                        "edit_file",
                        "delete_file",
                        "bash",
                        "commands",
                        "create_file",
                        "run_python",
                    ]
                    and not is_md_in_plan
                ):
                    result = "Error: Tool execution blocked in 'plan' mode. You can only create/edit .md files (e.g. plan.md)."
                    if spinner:
                        spinner.stop("Error")
                    else:
                        print_tool_result(result)
                else:
                    is_modifying = name in [
                        "write_file",
                        "edit_file",
                        "delete_file",
                        "create_file",
                        "bash",
                        "commands",
                        "run_python",
                    ]
                    is_dangerous = name in ["bash", "commands", "run_python"]

                    def _get_arg_path(a: dict, default: str = "file") -> str:
                        if not isinstance(a, dict):
                            return default
                        for k in ("path", "target_file", "file", "filename", "file_path", "target", "file_name", "filepath", "p", "doc", "uri"):
                            v = a.get(k)
                            if v and isinstance(v, str) and v.strip():
                                return v.strip()
                        return default

                    if name_lower in [
                        "write_file",
                        "edit_file",
                        "create_file",
                        "replace_lines",
                        "append_file",
                    ]:
                        p = _get_arg_path(args)
                        content_to_print = args.get(
                            "content", args.get("new_str", args.get("new_content", ""))
                        )
                        if p.endswith(".py") or p.endswith(".js"):
                            modified_files.add(os.path.abspath(p))

                    if name_lower in ["write_file", "create_file", "append_file"]:
                        from .ui import LiveCodePanel

                        path = os.path.abspath(_get_arg_path(args))
                        content = args.get("content", "")
                        panel = LiveCodePanel(path)
                        panel.start()
                        for line in content.splitlines():
                            panel.add_line(line)
                        panel.stop()
                    elif name_lower == "replace_lines":
                        print_code_panel(
                            os.path.abspath(_get_arg_path(args)),
                            args.get("new_content", ""),
                        )
                    elif name_lower == "edit_file":
                        print_diff(
                            _get_arg_path(args),
                            args.get("old_str", ""),
                            args.get("new_str", ""),
                        )
                    elif name_lower == "bash":
                        cmd_str = args.get("command", "").strip()
                        if not cmd_str:
                            for k, v in args.items():
                                if isinstance(v, str) and v.strip():
                                    cmd_str = v.strip()
                                    args["command"] = cmd_str
                                    break

                        if not cmd_str:
                            err_msg = "System Error: You returned an empty command. You must provide the command in the 'command' argument."
                            print_tool_result("Empty command - auto-rejected")
                            self.context.add_tool_message(tc["id"], name, err_msg)
                            continue
                        print_code_panel("Terminal", cmd_str, lexer_override="bash")
                    elif name_lower == "run_python":
                        if "code" in args:
                            print_code_panel(
                                "Python Run (Code)",
                                args["code"],
                                lexer_override="python",
                            )
                        else:
                            try:
                                with open(
                                    args.get("path", ""), "r", encoding="utf-8"
                                ) as f:
                                    file_content = f.read()
                                print_code_panel(
                                    f"Python Run ({args.get('path')})",
                                    file_content,
                                    lexer_override="python",
                                )
                            except:
                                print_code_panel(
                                    "Python Run",
                                    f"python {args.get('path', 'script.py')}",
                                    lexer_override="bash",
                                )

                    requires_prompt = False
                    if mode != "auto" and not is_md_in_plan and is_modifying:
                        requires_prompt = True

                    ans = "y"
                    if requires_prompt:
                        from .ui import prompt_toolkit_select

                        try:
                            choice = prompt_toolkit_select(
                                "Action requires approval:",
                                [
                                    ("Approve (y)", "y"),
                                    ("Reject (n)", "n"),
                                    ("Always allow (a)", "a"),
                                ],
                            )
                        except Exception:
                            choice = (
                                input("Accept? (y)es (n)o (a)lways: ").strip().lower()
                            )
                        ans = choice if choice else "n"

                        if ans == "a":
                            mode = "auto"
                            try:
                                input_handler.mode_index = input_handler.modes.index(
                                    "auto"
                                )
                            except:
                                pass

                    if ans == "n":
                        reason = input(
                            "  ❯ Enter reason for rejection (optional): "
                        ).strip()
                        if reason:
                            result = f"User rejected the operation. Reason: {reason}\nPlease rethink and try a different approach."
                        else:
                            result = "User rejected the operation. Please rethink and try a different approach."
                        if spinner:
                            spinner.stop("Rejected")
                        else:
                            print_tool_result("Rejected. Model will try again.")
                    elif name_lower == "syntax_error":
                        if spinner:
                            spinner.stop("Truncated Code")
                        result = "System Error: Your JSON tool call was severely truncated or malformed (JSONDecodeError). THE FILE WAS NOT CREATED YET! Do not use 'edit_file' on a file you failed to create. If you hit the token limit, try writing the file using 'write_file' again but maybe in smaller chunks."
                    else:
                        import copy

                        execute_args = copy.deepcopy(args)
                        result = execute_tool(
                            name_lower,
                            execute_args,
                            agent_instance=self,
                            restricted_dir=os.getcwd()
                            if getattr(self.context, "ide_mode", False)
                            else None,
                        )

                        if name_lower in [
                            "write_file",
                            "create_file",
                            "edit_file",
                            "replace_lines",
                            "append_file",
                        ] and "Error" not in str(result):
                            p = args.get("path", "")
                            if p.endswith(".py") or p.endswith(".js"):
                                try:
                                    with open(
                                        os.path.abspath(p), "r", encoding="utf-8"
                                    ) as f:
                                        current_code = f.read()

                                    from verifier import verify_code

                                    task_ctx = ""
                                    if len(self.context.messages) > 1:
                                        task_ctx = self.context.messages[1].get(
                                            "content", ""
                                        )[:1000]

                                    v_res = verify_code(
                                        self.model,
                                        os.path.abspath(p),
                                        current_code,
                                        task_ctx,
                                    )
                                    if v_res.get("status") == "NEEDS_FIX":
                                        result = (
                                            str(result)
                                            + f"\n\n[SELF-CHECK WARNING] Potential issues detected in code. You must review/fix them (self-verify attempt 1/1):\n{v_res.get('issues')}"
                                        )
                                except Exception as e:
                                    pass

                        if "rescued" in tc.get("id", ""):
                            result = (
                                str(result)
                                + "\n\n[SYSTEM WARNING]: Your JSON tool call was cut off (JSONDecodeError). The file was forcefully saved up to the point it was cut off. Please use 'read_file' or 'edit_file' to inspect the end of the file, fix the broken syntax, and continue writing the remaining code."
                            )

                        if spinner:
                            lines = len(
                                [
                                    l
                                    for l in str(result).splitlines()
                                    if l.strip() and "Results for" not in l
                                ]
                            )
                            summary = (
                                f"{lines} results"
                                if "Error" not in str(result)
                                else "Error"
                            )
                            spinner.stop(summary, details=str(result))
                        elif name_lower in ["read_file", "read"]:
                            if "Error" in str(result):
                                print_tool_result(str(result))
                            else:
                                path_arg = _get_arg_path(args, "")
                                lines = len(str(result).splitlines())
                                file_str = f" ({path_arg})" if path_arg else ""
                                print_tool_result(f"Read {lines} lines{file_str}")
                        elif name_lower in ["edit_file", "replace_lines", "append_file"]:
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
                                f"Edited {path_arg} ([green]+{added}[/] / [red]-{removed}[/])"
                            )
                        elif name_lower in ["write_file", "create_file"]:
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
                                f"Created/Overwritten {path_arg} ({added} lines)"
                            )
                        elif name_lower in ["bash", "commands"]:
                            if "Exit code: 0" in str(result):
                                print_tool_result("Command successful")
                            else:
                                print_tool_result("Command failed")
                        elif name_lower in ["ls", "glob", "code_search"]:
                            res_str = str(result)
                            if "Error" not in res_str:
                                if name_lower == "code_search":
                                    from rich.markup import escape

                                    for line in res_str.splitlines():
                                        if line.strip():
                                            console.print(f"  [{MUTED_COLOR}]|_ {escape(line)}[/{MUTED_COLOR}]")
                                else:
                                    title_suffix = ""
                                    if name_lower == "ls":
                                        p = args.get("path", ".")
                                        if not p or p == "." or p == "/" or p == "\\":
                                            p = os.path.basename(os.path.abspath("."))
                                        title_suffix = f" {p}"
                                    elif name_lower == "glob" and "pattern" in args:
                                        title_suffix = f" {args['pattern']}"
                                    print_code_panel(
                                        f"Result ({name}{title_suffix})",
                                        res_str,
                                        lexer_override="text",
                                        show_line_numbers=False,
                                    )
                            else:
                                print_tool_result(res_str)
                        elif name_lower == "bugs":
                            res_str = str(result)
                            for line in res_str.splitlines():
                                if line.strip():
                                    console.print(f"  [{MUTED_COLOR}]|_ {line}[/{MUTED_COLOR}]")
                        elif name_lower in ["answer", "ask_question"]:
                            res_str = str(result)
                            from rich.markup import escape
                            if "error" in res_str.lower() or "cancelled" in res_str.lower():
                                print_tool_result(res_str)
                            else:
                                lines = res_str.splitlines()
                                for idx, line in enumerate(lines):
                                    if line.strip():
                                        if idx == 0:
                                            console.print(f"  [{MUTED_COLOR}]|_ {escape(line)}[/{MUTED_COLOR}]")
                                        else:
                                            console.print(f"     [{MUTED_COLOR}]{escape(line)}[/{MUTED_COLOR}]")
                        elif name_lower == "wakeup_subagents":
                            res_str = str(result)
                            if res_str.startswith("Error:"):
                                print_tool_result(res_str, escape_text=True)
                        elif name_lower in ["answer", "ask_question"]:
                            res_str = str(result)
                            lines = res_str.splitlines()
                            if lines:
                                print_tool_result(lines[0], escape_text=True)
                                for line in lines[1:]:
                                    from rich.markup import escape
                                    console.print(f"[bright_black]        {escape(line)}[/bright_black]", highlight=False)
                        else:
                            res_str = str(result)
                            print_tool_result(
                                res_str[:60].replace("\n", " ") + "..."
                                if len(res_str) > 60
                                else res_str.replace("\n", " "),
                                escape_text=True,
                            )

                self.context.add_tool_message(tc["id"], name, str(result))

        print_turn_done(
            time.time() - turn_start,
            self.context.get_token_count() - start_tokens,
            total_tools,
        )

    def rename_session_from_understanding(self, understand_text: str):
        from .ui import console, MUTED_COLOR
        import re

        compaction_model_instance, should_release = (
            self.context._build_compaction_model(self.model)
        )

        prompt = (
            "Read the following understanding of a coding task and generate a short, descriptive name for this coding session.\n\n"
            "FORMAT RULES:\n"
            "- 2 to 5 words.\n"
            "- Plain words separated by single spaces (e.g. 'fix login redirect bug'). Do NOT use snake_case, kebab-case, camelCase, or underscores/hyphens as separators.\n"
            "- No punctuation anywhere (no periods, commas, colons, quotes, or trailing symbols).\n"
            "- No markdown, no code fences, no labels like 'Name:' — output only the name itself.\n"
            "- Same language as the 'Understanding' text below.\n"
            "- Be specific to this task, not generic. Avoid words like 'task', 'session', 'coding', 'fix code' unless no more specific term applies.\n"
            "- If the understanding is too vague or empty to name specifically, output exactly: general coding session\n\n"
            f"Understanding: {understand_text}\n\n"
            "Output only the name, nothing else."
        )
        sys_msg = "You generate short titles. Only output the title."
        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ]

        title = ""
        try:
            for content, thinking, _ in compaction_model_instance.stream_chat(
                messages, reasoning_budget=0
            ):
                chunk = content or thinking or ""
                title += chunk
                if len(title) > 50:
                    break
        except Exception:
            pass

        if should_release:
            del compaction_model_instance

        title = re.sub(r'[<>:"/\\|?*\r\n]', "", title).strip()
        title = re.sub(r"\s+", "_", title).lower()
        title = re.sub(r"[^a-zA-Z0-9_a-ząćęłńóśźżA-Z0-9ĄĆĘŁŃÓŚŹŻ]+", "_", title)
        title = title.strip("_")[:40].strip()

        if not title or title.startswith("cmdai_code"):
            fallback = understand_text[:30].strip()
            fallback = re.sub(r"[^a-zA-Z0-9_a-ząćęłńóśźżA-Z0-9ĄĆĘŁŃÓŚŹŻ]+", "_", fallback).strip("_").lower()
            if fallback:
                title = fallback[:30]

        if title:
            self.context.rename_session(title)
            console.print(f"[{MUTED_COLOR}]●[/] [bold]session name:[/bold] {title}")
