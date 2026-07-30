import os
import glob
import json
from .llama import LlamaModel
from .context import ContextManager
from .agent import Agent
from .ui import print_header, print_user_msg, console
from .input import InputHandler
from .ide import IDEServer

STATE_FILE = os.path.expanduser("~/.cmdai_code/state.json")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            import shutil

            backup_file = STATE_FILE + ".backup"
            shutil.copy2(STATE_FILE, backup_file)
            from .ui import console

            console.print(
                f"[red]ERROR: state.json is corrupted (e.g. bad JSON syntax). Backup created at: {backup_file}[/red]"
            )
            return {}
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_default_model():
    state = load_state()
    model_type = state.get("model_type", "local")
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    models_dir = os.path.join(app_dir, "models")
    system_models_dir = os.path.join(app_dir, "systemmodels")
    local_models = glob.glob(os.path.join(models_dir, "*.gguf"))
    system_models = glob.glob(os.path.join(system_models_dir, "*.gguf"))
    all_local_models = local_models + system_models

    if model_type == "api":
        active_model = state.get("active_api_model")
        if active_model and isinstance(active_model, dict):
            return "api", active_model

    saved_model = state.get("model_path")
    if saved_model:
        saved_model_path = os.path.expanduser(saved_model)
        if os.path.exists(saved_model_path):
            return "local", saved_model_path
        for local_model in all_local_models:
            if os.path.basename(local_model) == os.path.basename(saved_model):
                return "local", local_model

    if local_models:
        return "local", local_models[-1]

    active_model = state.get("active_api_model")
import os
import glob
import json
from .llama import LlamaModel
from .context import ContextManager
from .agent import Agent
from .ui import print_header, print_user_msg, console
from .input import InputHandler
from .ide import IDEServer

STATE_FILE = os.path.expanduser("~/.cmdai_code/state.json")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            import shutil

            backup_file = STATE_FILE + ".backup"
            shutil.copy2(STATE_FILE, backup_file)
            from .ui import console

            console.print(
                f"[red]ERROR: state.json is corrupted (e.g. bad JSON syntax). Backup created at: {backup_file}[/red]"
            )
            return {}
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_default_model():
    state = load_state()
    model_type = state.get("model_type", "local")
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    models_dir = os.path.join(app_dir, "models")
    system_models_dir = os.path.join(app_dir, "systemmodels")
    local_models = glob.glob(os.path.join(models_dir, "*.gguf"))
    system_models = glob.glob(os.path.join(system_models_dir, "*.gguf"))
    all_local_models = local_models + system_models

    if model_type == "api":
        active_model = state.get("active_api_model")
        if active_model and isinstance(active_model, dict):
            return "api", active_model

    saved_model = state.get("model_path")
    if saved_model:
        saved_model_path = os.path.expanduser(saved_model)
        if os.path.exists(saved_model_path):
            return "local", saved_model_path
        for local_model in all_local_models:
            if os.path.basename(local_model) == os.path.basename(saved_model):
                return "local", local_model

    if local_models:
        return "local", local_models[-1]

    active_model = state.get("active_api_model")
    if active_model and isinstance(active_model, dict):
        console.print(
            "[yellow]No local .gguf models found; starting with saved API model.[/yellow]"
        )
        return "api", active_model

    api_models = state.get("api_models", [])
    if api_models:
        console.print(
            "[yellow]No local .gguf models found; starting with first saved API model.[/yellow]"
        )
        return "api", api_models[0]

    if system_models:
        console.print(
            "[yellow]No main .gguf models found in models/; starting with system model fallback.[/yellow]"
        )
        return "local", system_models[-1]

    console.print(
        "[red]No local .gguf models found in models/ or systemmodels/, and no API model is configured.[/red]"
    )
    console.print(
        "[yellow]Add a .gguf model, or run /model after configuring an API model in ~/.cmdai_code/state.json.[/yellow]"
    )
    exit(1)


def print_chat_history(context):
    import re
    import textwrap
    import json
    import os
    from rich.markdown import Markdown
    from .ui import (
        console,
        ACCENT_COLOR,
        MUTED_COLOR,
        SUCCESS_COLOR,
        ERROR_COLOR,
        print_tool_call,
        print_tool_result,
        print_user_msg,
        print_code_panel,
        print_diff,
        print_answer_summary,
    )
    from rich.markup import escape
    from .tools import get_arg_summary, get_tool_result_summary

    def print_subagent_header(subagent):
        wrap_width = max(40, console.width - 14)
        name = escape(str(subagent.get("name", "Subagent")))
        task = str(subagent.get("task", "") or subagent.get("role", "")).replace("\n", " ").strip()
        if not task:
            task = "Subagent task execution"
        level = escape(str(subagent.get("thinking_level", "auto") or "auto").lower())
        console.print(f"\n[{MUTED_COLOR}]╭ {name}[/{MUTED_COLOR}]")
        lines = textwrap.wrap(task, width=wrap_width) or [task]
        for index, line in enumerate(lines):
            label = "Role: " if index == 0 else "      "
            console.print(f"[{MUTED_COLOR}]│   [dim]{label}{escape(line)}[/dim][/{MUTED_COLOR}]")
        console.print(f"[{MUTED_COLOR}]│   [dim]Thinking level: {level}[/dim][/{MUTED_COLOR}]")
        console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")

    def print_subagent_footer(subagent):
        wrap_width = max(40, console.width - 14)
        status = subagent.get("status", "done")
        icon = "✓" if status == "done" else "✗"
        icon_color = SUCCESS_COLOR if status == "done" else ERROR_COLOR
        console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")
        console.print(f"[{MUTED_COLOR}]│ ● Agent Note[/{MUTED_COLOR}]")
        note = str(subagent.get("note", "") or "Task completed.")
        for note_line in note.splitlines() or [note]:
            for index, line in enumerate(textwrap.wrap(note_line, width=wrap_width) or [""]):
                prefix = f"|_ [{icon_color}]{icon}[/{icon_color}] " if index == 0 else "|_    "
                console.print(f"[{MUTED_COLOR}]│   {prefix}{escape(line)}[/{MUTED_COLOR}]")
        files = subagent.get("files_edited", [])
        if files:
            console.print(f"[{MUTED_COLOR}]│   [dim]Files: {', '.join(escape(str(path)) for path in files)}[/dim][/{MUTED_COLOR}]")
        console.print(f"[{MUTED_COLOR}]╰[/{MUTED_COLOR}]")

    def _replay_subagent_history(history_messages: list, source_id: str, thinking_level: str = "high"):
        tool_queue = []
        did_print_output = False
        for msg in history_messages:
            role = msg.get("role")
            content = msg.get("content", "")
            is_tool_res = False

            if role == "assistant":
                think_text = msg.get("thinking", "")
                if not think_text and content:
                    think_match = re.search(r"<think>(.*?)(?:</think>|$)", str(content), flags=re.DOTALL | re.IGNORECASE)
                    if think_match:
                        think_text = think_match.group(1).strip()
                if think_text:
                    from .ui import ThinkingTree
                    tt = ThinkingTree(
                        expanded=True,
                        simulate=False,
                        title="Thinking",
                        model_name=f"{source_id} · {thinking_level}",
                        source_id=source_id,
                    )
                    tt.lines = [t for t in think_text.splitlines() if t.strip()]
                    tt.print_tree()
                    did_print_output = True
                if msg.get("tool_calls"):
                    for tc in msg.get("tool_calls"):
                        tool_queue.append(tc)
            elif role == "tool":
                is_tool_res = True
                res_str = str(content).strip()
            elif role == "user" and content.startswith("<tool_response>"):
                continue

            if is_tool_res and tool_queue:
                tc = tool_queue.pop(0)
                name = tc.get("function", {}).get("name", "unknown")
                args = tc.get("function", {}).get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}

                arg_summary = get_arg_summary(name, args) if isinstance(args, dict) else ""
                print_tool_call(name, arg_summary, source_id=source_id)
                did_print_output = True

                name_lower = name.lower()
                if name_lower in ["finish_task", "wakeup_subagents"]:
                    continue

                if name_lower in ["write_file", "create_file"]:
                    if "Error" in res_str or "Traceback" in res_str:
                        for line in res_str.splitlines():
                            if line.strip():
                                print_tool_result(line, source_id=source_id, escape_text=True)
                    else:
                        path_arg = args.get("path") or args.get("target_file") or args.get("file") or "file"
                        content_arg = args.get("content") or args.get("code") or ""
                        print_code_panel(os.path.abspath(path_arg), content_arg, source_id=source_id)
                elif name_lower in ["edit_file", "replace_lines", "append_file"]:
                    if "Error" not in res_str and "No changes" not in res_str:
                        path_arg = args.get("path", "file")
                        added = len(args.get("new_str", args.get("content", "")).splitlines())
                        removed = len(args.get("old_str", "").splitlines())
                        print_tool_result(f"Edited {path_arg} ([green]+{added}[/] / [red]-{removed}[/])", source_id=source_id)
                elif name_lower in ["read_file", "read", "view_file"]:
                    if "Error" in res_str or "Traceback" in res_str:
                        print_tool_result(res_str, source_id=source_id, escape_text=True)
                    else:
                        lines = len(res_str.splitlines())
                        path_arg = (
                            args.get("path")
                            or args.get("target_file")
                            or args.get("file")
                            or args.get("filename")
                            or args.get("file_path")
                            or args.get("target")
                            or "file"
                        )
                        print_tool_result(f"Read {lines} lines ({path_arg})", source_id=source_id)
                elif name_lower in ["bash", "commands"]:
                    if "Exit code: 0" in res_str:
                        print_tool_result("Command successful", source_id=source_id)
                    else:
                        print_tool_result("Command failed", source_id=source_id)
                elif name_lower in ["ls", "glob", "code_search", "run_grep"]:
                    if "Error" not in res_str:
                        print_code_panel(f"Result ({name})", res_str, lexer_override="text", show_line_numbers=False, source_id=source_id)
                    else:
                        print_tool_result(res_str, source_id=source_id, escape_text=True)
                elif name_lower in ["answer", "ask_question"]:
                    print_answer_summary(res_str, source_id=source_id)
                else:
                    if "Error" in res_str or "Traceback" in res_str:
                        for line in res_str.splitlines():
                            if line.strip():
                                print_tool_result(line, source_id=source_id, escape_text=True)
                    else:
                        result_summary = get_tool_result_summary(name, args, res_str)
                        if result_summary is not None:
                            print_tool_result(result_summary, source_id=source_id)
                        else:
                            short = res_str[:120].replace("\n", " ") + "..." if len(res_str) > 120 else res_str.replace("\n", " ")
                            print_tool_result(short, source_id=source_id, escape_text=True)

    subagent_tool_queues = {}
    started_subagent_headers = set()
    printed_subagent_footers = set()
    subagent_notes = {}
    subagent_tasks = {}
    subagent_files_edited = {}
    tool_queue = []

    def flush_subagent_footers():
        for sa in list(started_subagent_headers):
            if sa not in printed_subagent_footers:
                printed_subagent_footers.add(sa)
                note = subagent_notes.get(sa, "Task completed.")
                files = subagent_files_edited.get(sa, [])
                print_subagent_footer({"name": sa, "note": note, "files_edited": files, "status": "done"})

    for msg in context.messages:
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "subagent":
            sa_name = msg.get("subagent_name", "Subagent")
            sa_role = msg.get("subagent_role", "assistant")

            if sa_role == "user":
                if content and not content.startswith("<tool_response>"):
                    subagent_tasks[sa_name] = content
                continue

            if sa_name not in started_subagent_headers:
                started_subagent_headers.add(sa_name)
                task_str = subagent_tasks.get(sa_name, "")
                print_subagent_header({"name": sa_name, "task": task_str, "thinking_level": "high"})

            if sa_role == "assistant":
                if content and not content.startswith("<tool_response>"):
                    subagent_notes[sa_name] = content.strip()
                think_text = msg.get("thinking", "")
                if not think_text and content:
                    think_match = re.search(r"<think>(.*?)(?:</think>|$)", str(content), flags=re.DOTALL | re.IGNORECASE)
                    if think_match:
                        think_text = think_match.group(1).strip()
                if think_text:
                    from .ui import ThinkingTree
                    tt = ThinkingTree(
                        expanded=True,
                        simulate=False,
                        title="Thinking",
                        model_name=f"{sa_name} · high",
                        source_id=sa_name,
                    )
                    tt.lines = [t for t in think_text.splitlines() if t.strip()]
                    tt.print_tree()
                if msg.get("tool_calls"):
                    if sa_name not in subagent_tool_queues:
                        subagent_tool_queues[sa_name] = []
                    for tc in msg.get("tool_calls"):
                        subagent_tool_queues[sa_name].append(tc)
            elif sa_role == "tool":
                res_str = str(content).strip()
                if sa_name in subagent_tool_queues and subagent_tool_queues[sa_name]:
                    tc = subagent_tool_queues[sa_name].pop(0)
                    name = tc.get("function", {}).get("name", "unknown")
                    args = tc.get("function", {}).get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}

                    name_lower = name.lower()
                    if name_lower == "finish_task":
                        printed_subagent_footers.add(sa_name)
                        note = args.get("note", "Task completed.") if isinstance(args, dict) else "Task completed."
                        files = subagent_files_edited.get(sa_name, [])
                        print_subagent_footer({"name": sa_name, "note": note, "files_edited": files, "status": "done"})
                    else:
                        arg_summary = get_arg_summary(name, args) if isinstance(args, dict) else ""
                        print_tool_call(name, arg_summary, source_id=sa_name)

                        if name_lower in ["write_file", "create_file"]:
                            path_arg = args.get("path") or args.get("target_file") or args.get("file") or "file"
                            if sa_name not in subagent_files_edited:
                                subagent_files_edited[sa_name] = []
                            if path_arg not in subagent_files_edited[sa_name]:
                                subagent_files_edited[sa_name].append(path_arg)

                            if "Error" in res_str or "Traceback" in res_str:
                                for line in res_str.splitlines():
                                    if line.strip():
                                        print_tool_result(line, source_id=sa_name, escape_text=True)
                            else:
                                content_arg = args.get("content") or args.get("code") or ""
                                print_code_panel(os.path.abspath(path_arg), content_arg, source_id=sa_name)
                        elif name_lower in ["edit_file", "replace_lines", "append_file"]:
                            path_arg = args.get("path") or args.get("target_file") or args.get("file") or "file"
                            if sa_name not in subagent_files_edited:
                                subagent_files_edited[sa_name] = []
                            if path_arg not in subagent_files_edited[sa_name]:
                                subagent_files_edited[sa_name].append(path_arg)

                            if "Error" not in res_str and "No changes" not in res_str:
                                added = len(args.get("new_str", args.get("content", "")).splitlines())
                                removed = len(args.get("old_str", "").splitlines())
                                print_tool_result(f"Edited {path_arg} ([green]+{added}[/] / [red]-{removed}[/])", source_id=sa_name)
                        elif name_lower in ["read_file", "read", "view_file"]:
                            if "Error" in res_str or "Traceback" in res_str:
                                print_tool_result(res_str, source_id=sa_name, escape_text=True)
                            else:
                                lines = len(res_str.splitlines())
                                path_arg = args.get("path") or args.get("target_file") or args.get("file") or "file"
                                print_tool_result(f"Read {lines} lines ({path_arg})", source_id=sa_name)
                        elif name_lower in ["bash", "commands"]:
                            if "Exit code: 0" in res_str:
                                print_tool_result("Command successful", source_id=sa_name)
                            else:
                                print_tool_result("Command failed", source_id=sa_name)
                        elif name_lower in ["ls", "glob"]:
                            if "Error" not in res_str:
                                print_code_panel(f"Result ({name})", res_str, lexer_override="text", show_line_numbers=False, source_id=sa_name)
                            else:
                                print_tool_result(res_str, source_id=sa_name, escape_text=True)
                        elif name_lower in ["search_web", "web_search", "grep", "run_grep", "code_search"]:
                            if "Error" in res_str or "Traceback" in res_str:
                                print_tool_result("Error", source_id=sa_name)
                            else:
                                line_count = len([l for l in res_str.splitlines() if l.strip() and "Results for" not in l])
                                print_tool_result(f"{line_count} results", source_id=sa_name)
                        elif name_lower in ["answer", "ask_question"]:
                            print_answer_summary(res_str, source_id=sa_name)
                        else:
                            if "Error" in res_str or "Traceback" in res_str:
                                for line in res_str.splitlines():
                                    if line.strip():
                                        print_tool_result(line, source_id=sa_name, escape_text=True)
                            else:
                                result_summary = get_tool_result_summary(name, args, res_str)
                                if result_summary is not None:
                                    print_tool_result(result_summary, source_id=sa_name)
                                else:
                                    short = res_str[:120].replace("\n", " ") + "..." if len(res_str) > 120 else res_str.replace("\n", " ")
                                    print_tool_result(short, source_id=sa_name, escape_text=True)
            continue

        flush_subagent_footers()
        is_tool_res = False
        res_str = ""

        if role == "tool":
            is_tool_res = True
            res_str = content.strip()
        elif role == "user" and content.startswith("<tool_response>"):
            continue

        if is_tool_res:
            if tool_queue:
                tc = tool_queue.pop(0)
                name = tc.get("function", {}).get("name", "unknown")
                args = tc.get("function", {}).get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}

                arg_summary = get_arg_summary(name, args) if isinstance(args, dict) else ""
                print_tool_call(name, arg_summary)

                name_lower = name.lower()
                if name_lower in ["read_file", "read", "view_file"]:
                    if "Error" in res_str or "Traceback" in res_str:
                        for line in res_str.splitlines():
                            if line.strip():
                                print_tool_result(line, escape_text=True)
                    else:
                        lines = len(res_str.splitlines())
                        path_arg = (
                            args.get("path")
                            or args.get("target_file")
                            or args.get("file")
                            or args.get("filename")
                            or args.get("file_path")
                            or args.get("target")
                            or "file"
                        )
                        print_tool_result(f"Read {lines} lines ({path_arg})")
                elif name_lower in ["write_file", "create_file"]:
                    print_code_panel(
                        os.path.abspath(args.get("path", "file")),
                        args.get("content", ""),
                    )
                elif name_lower == "edit_file":
                    print_diff(
                        args.get("path", "file"),
                        args.get("old_str", ""),
                        args.get("new_str", ""),
                    )
                elif name_lower in ["bash", "commands"]:
                    print_code_panel(
                        "Terminal", args.get("command", ""), lexer_override="bash"
                    )
                elif name_lower == "run_python":
                    if "code" in args:
                        print_code_panel(
                            "Python Run (Code)", args["code"], lexer_override="python",
                        )
                    elif "script_path" in args:
                        print_code_panel(
                            "Python Run (Script)", args["script_path"], lexer_override="text",
                        )
                elif name_lower in ["ls", "glob"]:
                    if "Error" not in res_str:
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
                elif name_lower in ["search_web", "web_search", "grep", "run_grep", "code_search"]:
                    if "Error" in res_str or "Traceback" in res_str:
                        print_tool_result("Error")
                    else:
                        line_count = len([l for l in res_str.splitlines() if l.strip() and "Results for" not in l])
                        print_tool_result(f"{line_count} results")
                elif name_lower in ["answer", "ask_question"]:
                    print_answer_summary(res_str)
                elif name_lower in ["bugs", "run_bugs"]:
                    for line in res_str.splitlines():
                        from rich.markup import escape
                        line_esc = escape(line)
                        stripped = line_esc.strip()
                        if stripped.startswith("|_"):
                            console.print(f"[bright_black]    {stripped}[/bright_black]", highlight=False)
                        else:
                            console.print(f"[bright_black]  |_ {line_esc}[/bright_black]", highlight=False)
                elif name_lower in ["tests", "run_tests"]:
                    for line in res_str.splitlines():
                        print_tool_result(line, escape_text=True)
                elif name_lower == "wakeup_subagents":
                    if res_str.startswith("Error:"):
                        print_tool_result(res_str, escape_text=True)
                    else:
                        subagents_data = []
                        if isinstance(args, dict) and "subagents" in args:
                            subagents_data = args["subagents"]
                        elif isinstance(msg, dict) and "subagents" in msg:
                            subagents_data = msg["subagents"]
                        if subagents_data:
                            for sa in subagents_data:
                                raw_sa_name = str(sa.get("name", "Subagent"))
                                subagent_tasks[raw_sa_name] = sa.get("task", "")
                                sa_name = escape(raw_sa_name)
                                sa_task = str(sa.get("task", "")).replace("\n", " ").strip()
                                role_str = f" - Role: {escape(sa_task)}" if sa_task else ""
                                lvl_val = escape(str(sa.get("thinking_level", "auto") or "auto").lower())
                                lvl_str = f" (thinking: {lvl_val})"
                                console.print(f"  [{MUTED_COLOR}]|_ {sa_name}{role_str}{lvl_str}[/{MUTED_COLOR}]")
                elif name_lower in ["bash", "commands"]:
                    if "Exit code: 0" in res_str:
                        print_tool_result("Command successful")
                    else:
                        print_tool_result("Command failed")
                else:
                    if "Error" in res_str or "Traceback" in res_str:
                        for line in res_str.splitlines():
                            if line.strip():
                                print_tool_result(line, escape_text=True)
                    else:
                        result_summary = get_tool_result_summary(name, args, res_str)
                        if result_summary is not None:
                            print_tool_result(result_summary)
                        else:
                            short = res_str[:120].replace("\n", " ") + "..." if len(res_str) > 120 else res_str.replace("\n", " ")
                            print_tool_result(short, escape_text=True)
        elif role == "user":
            if (
                "[COMPRESSED SESSION CONTEXT]" in content
                or content.startswith("System Error:")
                or content.startswith("SYSTEM DIRECTIVE:")
                or content.startswith("SYSTEM ERROR:")
            ):
                if "[COMPRESSED SESSION CONTEXT]" in content:
                    m = re.search(
                        r"\[COMPRESSED SESSION CONTEXT\]\r?\n(.*?)\r?\n\r?\nKontynuuj od tego miejsca",
                        content,
                        re.DOTALL,
                    )
                    if m:
                        from .ui import ThinkingTree

                        tree = ThinkingTree(
                            expanded=True, simulate=False, title="Summarizing"
                        )
                        for line in m.group(1).strip().splitlines():
                            tree.add_line(line)
                        tree.print_tree()
                    else:
                        from .ui import MUTED_COLOR

                        console.print(f"[{MUTED_COLOR}]  ⎿  [Compacted context loaded][/]")
                continue
            clean_content = content.split("\n\n[USER ATTACHED FILE:")[0].strip()
            print_user_msg(clean_content)
            attached = msg.get("attached_files")
            if attached:
                from .ui import MUTED_COLOR
                for af in attached:
                    console.print(f"[{MUTED_COLOR}]  |_ Attached file: {af}[/]")
        elif role == "assistant":
            if "tool_calls" in msg and msg["tool_calls"]:
                tool_queue.extend(msg["tool_calls"])
            text = msg.get("content", "")
            think_text = msg.get("thinking", "")

            think_match = re.search(
                r"<think>(.*?)(?:</think>|$)", text, flags=re.DOTALL | re.IGNORECASE
            )
            if think_match:
                think_text = think_match.group(1)
                text = re.sub(
                    r"<think>.*?(?:</think>|$)",
                    "",
                    text,
                    flags=re.DOTALL | re.IGNORECASE,
                )

            if think_text:
                from .ui import ThinkingTree

                tt = ThinkingTree(
                    expanded=True,
                    simulate=False,
                    title="Thinking",
                    model_name=msg.get("model", ""),
                )
                tt.lines = [t for t in think_text.splitlines() if t.strip()]
                tt.print_tree()

            text = re.sub(
                r'```(?:json)?\s*\{.*?"name"\s*:.*?\}\s*```', "", text, flags=re.DOTALL
            )
            text = re.sub(
                r'\{\s*"name"\s*:.*?"arguments"\s*:.*?\}', "", text, flags=re.DOTALL
            )
            text = re.sub(r"<\|?tool_call\|?>.*?(?:</tool_call>|<tool_call\|>|<\|tool_call\|>|$)", "", text, flags=re.DOTALL)
            text = re.sub(r"call:[a-zA-Z0-9_]+\s*\{.*?\}(?:<\|?tool_call\|?>)?", "", text, flags=re.DOTALL)
            if text:
                console.print(Markdown(text))

    flush_subagent_footers()


def main():
    console.clear()
    m_type, m_val = get_default_model()

    if m_type == "api":
        from .api_model import OpenAIAPIModel

        model_path = m_val["name"]
        model = OpenAIAPIModel(
            model_name=m_val["name"],
            api_key=m_val["api_key"],
            base_url=m_val["base_url"],
            provider_id=m_val.get("provider"),
        )
    else:
        model_path = m_val
        saved_state = load_state()
        n_gpu_layers = saved_state.get("n_gpu_layers", -1)
        model = LlamaModel(model_path, n_ctx=8192, n_gpu_layers=n_gpu_layers)
    context = ContextManager()
    agent = Agent(model, context)

    saved_state = load_state()
    input_handler = InputHandler(thinking_idx=saved_state.get("thinking_idx", 1))

    ide_server = IDEServer()
    ide_server.start()

    import sys

    # Restore saved cwd from state if available (from /cd command)
    saved_cwd = saved_state.get("cwd")
    if saved_cwd and os.path.isdir(saved_cwd):
        try:
            os.chdir(saved_cwd)
        except Exception:
            pass

    if len(sys.argv) > 1 and sys.argv[1].strip():
        arg_path = sys.argv[1].strip().strip('"\'')
        if os.path.exists(arg_path):
            if os.path.isfile(arg_path):
                arg_dir = os.path.dirname(os.path.abspath(arg_path))
            else:
                arg_dir = os.path.abspath(arg_path)
            try:
                os.chdir(arg_dir)
            except Exception:
                pass

    cwd = os.getcwd()

    os.system("cls" if os.name == "nt" else "clear")
    print_header(
        os.path.basename(model_path)
        if isinstance(model_path, str)
        else model_path.get("name", "Unknown API Model"),
        cwd,
    )
    mode = input_handler.get_mode()

    try:
        import sys

        src_dir = os.path.dirname(os.path.abspath(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

        from indexer.scan import full_or_incremental_scan
        import threading

        threading.Thread(
            target=full_or_incremental_scan, args=(cwd,), daemon=True
        ).start()
    except Exception as e:
        pass

    while True:
        tokens = context.get_token_count()
        sys.stdout.flush()
        sys.stderr.flush()
        user_input = input_handler.get_input(
            os.path.basename(model_path), tokens, model.get_context_limit()
        )
        user_input = user_input.strip()
        original_user_input = user_input
        mode = input_handler.get_mode()
        hide_prompt = False

        current_state = load_state()
        if current_state.get("thinking_idx") != input_handler.thinking_idx:
            current_state["thinking_idx"] = input_handler.thinking_idx
            save_state(current_state)

        if user_input in ["/quit", "/exit"]:
            break
        elif user_input == "/clear":
            context.clear()
            os.system("cls" if os.name == "nt" else "clear")
            console.clear()
            print_header(
                os.path.basename(model_path)
                if isinstance(model_path, str)
                else model_path.get("name", "Unknown API Model"),
                cwd,
            )
            continue
        elif user_input == "/diff":
            from .tools.git import git_diff
            from .ui import print_code_panel

            diff_output = git_diff(cwd)
            print_code_panel("Git Diff", diff_output, lexer_override="diff")
            continue
        elif user_input == "/undo":
            from .tools.git import git_undo

            res = git_undo(cwd)
            console.print(f"\n● [white]/undo[/white]")
            from .ui import MUTED_COLOR

            console.print(
                f"[{MUTED_COLOR}]  ⎿  Undo executed (stashed changes): {res}[/]"
            )
            continue
        elif user_input.startswith("/commit"):
            from .tools.git import git_commit

            msg = user_input[7:].strip() or "Automated commit by CMDAI CODE"
            res = git_commit(msg, cwd)
            console.print(f"\n● [white]/commit[/white]")
            from .ui import MUTED_COLOR

            console.print(f"[{MUTED_COLOR}]  ⎿  Committed successfully: {res}[/]")
            continue
        elif user_input.startswith("/cd ") or user_input == "/cd":
            target_dir = user_input[3:].strip()
            if not target_dir:
                target_dir = os.path.expanduser("~")
            else:
                target_dir = os.path.abspath(os.path.expanduser(target_dir))
            
            console.print(f"\n● [white]/cd {target_dir}[/white]")
            from .ui import MUTED_COLOR
            if os.path.exists(target_dir) and os.path.isdir(target_dir):
                os.chdir(target_dir)
                cwd = target_dir
                state = load_state()
                state["cwd"] = cwd
                save_state(state)
                console.print(f"[{MUTED_COLOR}]  ⎿  Changed working directory to: {cwd}[/]")
            else:
                console.print(f"[{MUTED_COLOR}]  ⎿  Error: Directory '{target_dir}' does not exist.[/]")
            continue
        elif user_input == "/compact":
            context.trigger_compaction(model)
        elif user_input == "/review":
            agent.auto_review = not agent.auto_review
            stan = "ENABLED" if agent.auto_review else "DISABLED"
            console.print(
                f"\n[magenta]🔍 Auto-Reflection (self-correction) mode was {stan}.[/magenta]"
            )
        elif user_input.startswith("/sessions"):
            while True:
                sm = context.session_manager
                sessions = sm.get_all_sessions()

                from .session_picker import run_session_picker

                res = run_session_picker(sessions, sm.current_state.session_id)

                if res["action"] == "cancel":
                    break

                if res["action"] == "new":
                    import questionary

                    new_id = questionary.text(
                        "Enter name for the new session (enter to cancel):"
                    ).ask()
                    if new_id and new_id.strip():
                        new_id = new_id.strip()
                        context.load_history(new_id)
                        os.system("cls" if os.name == "nt" else "clear")
                        console.clear()
                        print_header(
                            os.path.basename(model_path)
                            if isinstance(model_path, str)
                            else model_path.get("name", "Unknown API Model"),
                            cwd,
                        )
                        print_chat_history(context)
                        console.print(f"\n● [white]/sessions[/white]")
                        from .ui import MUTED_COLOR

                        console.print(
                            f"[{MUTED_COLOR}]  ⎿  Utworzono i wczytano: {new_id} ({len(context.messages)} wiadomości)[/]"
                        )
                    break
                elif res["action"] == "delete":
                    del_id = res["value"]
                    sm.delete_session(del_id)
                    console.print(f"\n● [white]/sessions[/white]")
                    from .ui import MUTED_COLOR

                    console.print(
                        f"[{MUTED_COLOR}]  ⎿  Pomyślnie usunięto sesję: {del_id}[/]"
                    )

                    if del_id == sm.current_state.session_id:
                        context.clear()
                        console.print(
                            f"[{MUTED_COLOR}]  ⎿  Usunięto aktywną sesję. Rozpoczynanie nowej.[/]"
                        )

                elif res["action"] == "load":
                    s_id = res["value"]
                    context.load_history(s_id)
                    os.system("cls" if os.name == "nt" else "clear")
                    console.clear()
                    print_header(
                        os.path.basename(model_path)
                        if isinstance(model_path, str)
                        else model_path.get("name", "Unknown API Model"),
                        cwd,
                    )
                    print_chat_history(context)
                    console.print(f"\n● [white]/sessions[/white]")
                    from .ui import MUTED_COLOR

                    console.print(
                        f"[{MUTED_COLOR}]  ⎿  Wczytano: {s_id} ({len(context.messages)} wiadomości)[/]"
                    )
                    break
            continue
        elif user_input == "/subagents":
            from .model_picker import run_model_picker

            s_tmp = load_state()
            old_model_raw = s_tmp.get("subagent_model")
            old_model_name = (
                old_model_raw.get("name", "None")
                if isinstance(old_model_raw, dict)
                else (
                    __import__("os").path.basename(old_model_raw)
                    if old_model_raw
                    else "None"
                )
            )
            res = run_model_picker(s_tmp, mode="subagents")

            if res["action"] in ["load_local", "load_api"]:
                s = load_state()
                s["subagent_model"] = res["value"]
                save_state(s)
                model_name = (
                    res["value"].get("name", "Unknown API Model")
                    if isinstance(res["value"], dict)
                    else os.path.basename(res["value"])
                )
                console.print("\n● [white]/subagents[/white]")
                from .ui import MUTED_COLOR

                console.print(
                    f"[{MUTED_COLOR}]  |_ {old_model_name} -> {model_name}[/]"
                )
            elif res["action"] == "set_thinking":
                s = load_state()
                old_think = s.get("subagent_thinking", "Auto")
                s["subagent_thinking"] = res["value"]
                save_state(s)
                console.print("\n● [white]/subagents[/white]")
                from .ui import MUTED_COLOR

                console.print(
                    f"[{MUTED_COLOR}]  |_ Thinking Level: {old_think} -> {res['value']}[/]"
                )
            else:
                console.print("[yellow]Subagent model selection cancelled.[/yellow]")
            continue
        elif user_input.startswith("/runsubagents"):
            prompt_text = user_input[len("/runsubagents") :].strip()
            if not prompt_text:
                console.print(
                    "[red]Proszę podać opis zadania dla subagentów, np. /runsubagents Zrób refactor pliku X.[/red]"
                )
                continue

            from .ui import MUTED_COLOR

            console.print(
                f"\n● [white on cyan]/runsubagents[/white on cyan] {prompt_text}"
            )
            try:
                py_files = []
                for root, dirs, files in os.walk(cwd):
                    if ".git" in dirs:
                        dirs.remove(".git")
                    if "__pycache__" in dirs:
                        dirs.remove("__pycache__")
                    for file in files:
                        if file.endswith(".py") or file.endswith(".md"):
                            py_files.append(file)
                console.print(
                    f"[{MUTED_COLOR}]  ⎿  Scanned repository. Found {len(py_files)} files (knowledge base built).[/]"
                )
            except Exception as e:
                console.print(f"[{MUTED_COLOR}]  |_ Scan error: {e}[/]")

            s_curr = load_state()
            think_level = s_curr.get("subagent_thinking", "Auto")
            think_instruction = f" Dla subagentów ustaw pole 'thinking_level' na '{think_level}' (zgodnie z wyborem użytkownika). Jeśli polecenie to 'Auto', MUSISZ samodzielnie dobrać optymalny poziom (WYBIERZ JEDNO Z: Low, Medium, High, Ultra, Extreme) dla każdego subagenta osobno zależnie od jego zadania. NIE WOLNO CI wpisać słowa 'Auto' jako wartości 'thinking_level', zawsze musi to być konkretny poziom!"

            user_input = f"Przeanalizuj zadanie i użyj narzędzia `wakeup_subagents` aby wydelegować je do 1-4 sub-agentów. Pamiętaj, aby jako argument przekazać tablicę `subagents` (zawierającą obiekty z `name`, `task` oraz ewentualnie `thinking_level`).{think_instruction} Nie używaj argumentu 'prompt'. Zadanie: {prompt_text}"
            hide_prompt = True
        elif user_input == "/ide":
            console.print(f"\n● [white]/ide[/white]")
            in_ide = (
                os.environ.get("TERM_PROGRAM") in ["vscode", "JetBrains-JediTerm"]
                or "VSCODE_PID" in os.environ
                or "TERMINAL_EMULATOR" in os.environ
            )
            if not in_ide:
                from .ui import MUTED_COLOR

                console.print(
                    f"[{MUTED_COLOR}]  ⎿  [red]Błąd: Uruchom CMD AI wewnątrz terminala zintegrowanego (np. VS Code).[/red][/]"
                )
            else:
                context.ide_mode = True
                from .ui import MUTED_COLOR

                console.print(
                    f"[{MUTED_COLOR}]  ⎿  IDE Server connected (port {ide_server.port}). Environment isolation active.[/]"
                )
            continue
        elif user_input == "/auto":
            input_handler.mode_index = input_handler.modes.index("auto")
            console.print(f"\n● [white]/auto[/white]")
            from .ui import MUTED_COLOR

            console.print(f"[{MUTED_COLOR}]  ⎿  Mode changed to: auto[/]")
            continue
        elif user_input == "/code":
            input_handler.mode_index = input_handler.modes.index("code")
            console.print(f"\n● [white]/code[/white]")
            from .ui import MUTED_COLOR

            console.print(f"[{MUTED_COLOR}]  ⎿  Mode changed to: code[/]")
            continue
        elif user_input in ["/progress", "/progres"]:
            if context.session_manager.current_state.current_plan:
                plan = context.session_manager.current_state.current_plan
                done = sum(1 for _, is_done in plan if is_done)
                percent = int((done / len(plan)) * 100) if len(plan) > 0 else 100
                bar_length = 20
                filled = (
                    int(bar_length * done / len(plan)) if len(plan) > 0 else bar_length
                )
                bar = "█" * filled + "░" * (bar_length - filled)
                console.print(f"\n● [white]/progress[/white]")
                from .ui import MUTED_COLOR

                console.print(
                    f"[{MUTED_COLOR}]  ⎿  {bar} {percent}% ({done}/{len(plan)} steps)[/]"
                )
            else:
                console.print(f"\n● [white]/progress[/white]")
                from .ui import MUTED_COLOR

                console.print(f"[{MUTED_COLOR}]  ⎿  No active plan.[/]")
            continue
        elif user_input == "/plan":
            input_handler.mode_index = input_handler.modes.index("plan")
            console.print(f"\n● [white]/plan[/white]")
            from .ui import MUTED_COLOR

            console.print(f"[{MUTED_COLOR}]  ⎿  Mode changed to: plan[/]")
            continue
        elif user_input == "/llama":
            from .model_picker import create_picker_app
            import importlib.util
            import subprocess

            state = load_state()
            current_engine = state.get("llama_engine", "llama cpp")

            installed = []
            if importlib.util.find_spec("llama_cpp"):
                detected = False
                try:
                    import io
                    import llama_cpp

                    fd = sys.stderr.fileno()
                    old_stderr = os.dup(fd)

                    capture_file = os.path.join(cwd, "stderr_capture.txt")
                    with open(capture_file, "w") as f:
                        os.dup2(f.fileno(), fd)
                        llama_cpp.llama_print_system_info()

                    os.dup2(old_stderr, fd)
                    os.close(old_stderr)

                    with open(capture_file, "r") as f:
                        stderr_out = f.read().lower()

                    try:
                        os.remove(capture_file)
                    except:
                        pass

                    if "vulkan" in stderr_out:
                        installed.append("llama vulcan")
                    else:
                        installed.append("llama cpp")
                    detected = True
                except:
                    pass

                if not detected:
                    if current_engine in ["llama cpp", "llama vulcan"]:
                        installed.append(current_engine)
                    else:
                        installed.append("llama cpp")

            if not installed:
                installed = ["No installed engines"]

            tabs = ["Installed", "Installation"]

            available_to_install = [
                e for e in ["llama cpp", "llama vulcan"] if e not in installed
            ]
            if not available_to_install:
                available_to_install = ["All installed"]

            options = {0: installed + ["Cancel"], 1: available_to_install + ["Cancel"]}

            res = create_picker_app(tabs, options, start_tab=0)

            if res["action"] == "select" and res["value"] not in [
                "Cancel",
                "No installed engines",
                "All installed",
            ]:
                selected = res["value"]
                if res["tab"] == "Installation":
                    console.print(
                        f"\n[yellow]⏳ Starting engine installation: {selected}...[/yellow]"
                    )

                    cmd = ""
                    if selected == "llama cpp":
                        cmd = "pip install llama-cpp-python --force-reinstall --no-cache-dir"
                    elif selected == "llama vulcan":
                        if os.name == "nt":
                            cmd = "set CMAKE_ARGS=-DGGML_VULCAN=1 && pip install llama-cpp-python --force-reinstall --no-cache-dir"
                        else:
                            cmd = 'CMAKE_ARGS="-DGGML_VULCAN=1" pip install llama-cpp-python --force-reinstall --no-cache-dir'

                    if cmd:
                        console.print(f"[cyan]Executing: {cmd}[/cyan]")
                        subprocess.run(cmd, shell=True)

                    state["llama_engine"] = selected
                    save_state(state)
                    console.print(
                        f"[green]✅ Llama engine has been installed and set to: {selected}[/green]"
                    )
                    import time

                    time.sleep(2)
                elif res["tab"] == "Installed":
                    state["llama_engine"] = selected
                    save_state(state)
                    console.print(f"[green]✅ Llama engine set to: {selected}[/green]")
                    import time

                    time.sleep(1)

            os.system("cls" if os.name == "nt" else "clear")
            print_header(os.path.basename(model_path), cwd)
            continue
        elif user_input == "/model":
            from .model_picker import run_model_picker

            while True:
                state = load_state()
                result = run_model_picker(state)

                if result["action"] == "add_api":
                    from .model_picker import run_provider_picker

                    sub_res = run_provider_picker(mode="api")
                    if sub_res["action"] == "cancel":
                        continue

                    provider = sub_res["provider"]
                    api_key = console.input(
                        f"\n[bold]Enter API key for {provider.upper()}: [/bold]"
                    )

                    if api_key:
                        if "api_keys" not in state:
                            state["api_keys"] = {}
                        state["api_keys"][provider] = api_key
                        save_state(state)
                        console.print(
                            f"[green]Saved API key for {provider.upper()}.[/green]"
                        )
                        import time

                        time.sleep(1)
                    continue

                elif result["action"] == "edit_api":
                    from .manager_ui import run_api_keys_manager

                    run_api_keys_manager(state, save_state)
                    continue

                elif result["action"] == "edit_models":
                    from .manager_ui import run_models_manager

                    run_models_manager(state, save_state)
                    continue

                elif result["action"] == "add_model":
                    from .model_picker import run_provider_picker

                    sub_res = run_provider_picker(mode="model")
                    if sub_res["action"] == "cancel":
                        continue

                    provider = sub_res["provider"]
                    api_keys = state.get("api_keys", {})
                    if provider not in api_keys and provider != "localllmapi":
                        console.print(
                            f"\n[red]Missing API key for {provider.upper()}! Please select 'Add API key' first.[/red]"
                        )
                        import time

                        time.sleep(2)
                        continue

                    model_name = console.input(
                        f"\n[bold]Enter model name for {provider.upper()}: [/bold]"
                    )
                    if model_name:
                        if provider == "localllmapi":
                            base_url = console.input(
                                f"\n[bold]Enter Base URL for local API (e.g. http://127.0.0.1:1234/v1): [/bold]"
                            )
                            if not base_url:
                                continue
                            api_key_val = console.input(
                                f"\n[bold]Enter API Key (press Enter for 'not-needed'): [/bold]"
                            )
                            if not api_key_val:
                                api_key_val = "not-needed"
                        else:
                            api_key_val = api_keys[provider]
                            from src.providers import get_provider

                            provider_module = get_provider(provider)
                            if provider_module:
                                base_url = provider_module.BASE_URL
                            else:
                                base_url = "https://api.openai.com/v1"

                        new_api_model = {
                            "name": model_name,
                            "api_key": api_key_val,
                            "base_url": base_url,
                            "provider": provider,
                        }
                        if "api_models" not in state:
                            state["api_models"] = []
                        state["api_models"].append(new_api_model)
                        save_state(state)
                        result["action"] = "load_api"
                        result["value"] = new_api_model
                        break
                    else:
                        continue

                elif result["action"] in ["cancel", "load_local", "load_api"]:
                    break

            if result["action"] == "cancel":
                continue

            if result["action"] == "load_local":
                new_model_path = result["value"]
                state["model_path"] = new_model_path
                state["model_type"] = "local"
                save_state(state)

                console.print(f"\n● [white]/model[/white]")
                from .ui import MUTED_COLOR

                console.print(
                    f"[{MUTED_COLOR}]  ⎿  Wczytano lokalny: {os.path.basename(new_model_path)}[/]"
                )

                del agent.model
                del model

                model_path = new_model_path
                n_gpu_layers = state.get("n_gpu_layers", -1)
                model = LlamaModel(model_path, n_ctx=8192, n_gpu_layers=n_gpu_layers)
                agent.model = model

            elif result["action"] == "load_api":
                m_val = result["value"]
                state["model_type"] = "api"
                state["active_api_model"] = m_val
                save_state(state)
                console.print(f"\n● [white]/model[/white]")
                from .ui import MUTED_COLOR

                provider_name = m_val.get("provider", "").upper()
                console.print(
                    f"[{MUTED_COLOR}]  |_ Model: {m_val['name']} [{provider_name}][/{MUTED_COLOR}]"
                )

                try:
                    del agent.model
                    del model
                except Exception:
                    pass

                from .api_model import OpenAIAPIModel

                model_path = m_val["name"]
                model = OpenAIAPIModel(
                    model_name=m_val["name"],
                    api_key=m_val["api_key"],
                    base_url=m_val["base_url"],
                    provider_id=m_val.get("provider"),
                )
                agent.model = model
            continue
        elif user_input == "/init":
            console.print(f"\n● [white]/init[/white]")
            from .ui import MUTED_COLOR

            try:
                py_files = []
                for root, dirs, files in os.walk(cwd):
                    if ".git" in dirs:
                        dirs.remove(".git")
                    if "__pycache__" in dirs:
                        dirs.remove("__pycache__")
                    for file in files:
                        if file.endswith(".py") or file.endswith(".md"):
                            py_files.append(file)
                console.print(
                    f"[{MUTED_COLOR}]  ⎿  Scanned repository. Found {len(py_files)} files (knowledge base built).[/]"
                )
            except Exception as e:
                console.print(f"[{MUTED_COLOR}]  ⎿  Scan error: {e}[/]")
            user_input = "Carefully review all markdown files (.md) in the project (especially CMDAI.md and plan.md). Analyze them and immediately begin executing any plans or instructions found within them."
            hide_prompt = True
        elif user_input.startswith("/runsubagents"):
            task_prompt = user_input.replace("/runsubagents", "").strip() or "Kontynuuj budowę i rozwój aplikacji."
            console.print(f"\n● [white]/runsubagents[/white]")
            from .ui import MUTED_COLOR
            console.print(f"[{MUTED_COLOR}]  ⎿ Uruchamianie subagentów dla zadania: {task_prompt}[/]")
            user_input = f"Użyj narzędzia wakeup_subagents, aby wywołać subagenta do wykonania zadania: {task_prompt}"
            hide_prompt = True
        elif user_input.startswith("/"):
            console.print(f"Unknown command or not implemented: {user_input}")
            continue

        ide_ctx = ide_server.get_ide_context()
        if ide_ctx:
            user_input += f"\n\n[IDE Context]\n{ide_ctx}"

        if not hide_prompt:
            print_user_msg(user_input)
        agent.handle_user_input(user_input, mode, input_handler)

        import sys

        sys.stdout.flush()
        sys.stderr.flush()

        limit = model.get_context_limit()
        if context.get_token_count() >= limit * 0.9:
            context.trigger_compaction(model)


if __name__ == "__main__":
    main()
