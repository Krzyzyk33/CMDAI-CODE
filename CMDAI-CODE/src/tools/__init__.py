import os
from typing import List, Dict, Any, Optional
from .filesystem import (
    read_file,
    create_file,
    edit_file,
    write_file,
    delete_file,
    replace_lines,
    append_file,
    run_ls,
    run_glob,
)
from .execution import run_python, run_bash, run_tests
from .search import run_grep, search_web, code_search
from .planning import save_plan, mark_plan_step_done, submit_plan, todo_write
from .bugs import run_bugs
from .subagents import wakeup_subagents


def execute_tool(
    name: str,
    args: Dict[str, Any],
    restricted_dir: Optional[str] = None,
    agent_instance=None,
) -> str:
    if "__json_error__" in args:
        return "Error: " + args["__json_error__"]
    import os

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
    for k in ["path", "pattern"]:
        if k in args and isinstance(args[k], str):
            p = args[k].strip()
            if p == "/" or p == "\\":
                args[k] = "."
            elif (
                (p.startswith("/") or p.startswith("\\"))
                and not p.startswith("//")
                and not p.startswith("\\\\")
            ):
                args[k] = "." + p

    if restricted_dir and "path" in args:
        target_path = os.path.abspath(args["path"])
        safe_dir = os.path.abspath(restricted_dir)
        if not target_path.startswith(safe_dir):
            return f"Error: IDE isolation is active. Odmowa dostępu do ścieżki {args['path']} - wykracza poza aktualny projekt."

    tools_map = {
        "read_file": read_file,
        "create_file": create_file,
        "edit_file": edit_file,
        "replace_lines": replace_lines,
        "append_file": append_file,
        "write_file": write_file,
        "delete_file": delete_file,
        "commands": run_bash,
        "run_tests": run_tests,
        "run_grep": run_grep,
        "code_search": code_search,
        "glob": run_glob,
        "ls": run_ls,
        "todo_write": todo_write,
        "search_web": search_web,
        "save_plan": save_plan,
        "mark_plan_step_done": mark_plan_step_done,
        "submit_plan": submit_plan,
        "bugs": run_bugs,
        "wakeup_subagents": wakeup_subagents,
    }

    func = tools_map.get(name)
    if not func:
        return f"Error: Unknown tool '{name}'."

    if name == "run_tests" and agent_instance is not None:
        args["agent_instance"] = agent_instance

    if name == "wakeup_subagents":
        if agent_instance is None:
            return "Error: wakeup_subagents cannot be used by subagents."
        if not getattr(agent_instance, "model", None):
            return "Error: wakeup_subagents cannot be used. Model not loaded."
        args["agent_instance"] = agent_instance
        args["cwd"] = restricted_dir

    try:
        import inspect

        sig = inspect.signature(func)
        filtered_args = {k: v for k, v in args.items() if k in sig.parameters}
        return func(**filtered_args)
    except TypeError as e:
        if not args:
            return f"Error executing {name}: You called this tool without any arguments! Please provide the required arguments defined in the tool schema."
        return f"Error executing {name}: Invalid arguments. {str(e)}"
    except Exception as e:
        from rich.markup import escape
        return f"Error executing {name}: {escape(str(e))}"


TOOLS_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "replace_lines",
            "description": "Replaces lines from start_line to end_line with new_content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "new_content": {"type": "string"},
                },
                "required": ["path", "start_line", "end_line", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "Appends text to the end of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads the contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Creates a new file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replaces old_str with new_str in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Overwrites an entire file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Deletes a file or directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commands",
            "description": "Runs a shell command/script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search for regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob_pattern": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "List files matching a glob.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List contents of a directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Writes tasks.",
            "parameters": {
                "type": "object",
                "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Searches the web.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_plan",
            "description": "Saves your execution plan to plan.md.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_plan_step_done",
            "description": "Marks a step as done in the plan.md file by replacing [ ] with [x].",
            "parameters": {
                "type": "object",
                "properties": {"step_number": {"type": "integer"}},
                "required": ["step_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_plan",
            "description": "Submit an architectural plan before executing changes. Required on Extreme level.",
            "parameters": {
                "type": "object",
                "properties": {
                    "architecture_details": {"type": "string"},
                    "steps_list": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["architecture_details", "steps_list"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_search",
            "description": "Smart searches the project files using index or regex pattern. Use this for quickly finding definitions and uses of functions, classes, and variables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search term or pattern.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory path to search in (default '.').",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Runs tests for the project using the detected test framework. Pass a list of modified files if applicable to limit tests. If no framework is configured, automatically detects project type and tries common test/syntax commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of absolute paths to test.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 30).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bugs",
            "description": "Skanuje cały projekt w poszukiwaniu błędów składniowych i zwraca ich listę. Nie przyjmuje żadnych parametrów.",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wakeup_subagents",
            "description": "Delegates a task to a background sub-agent. This requires the user to have selected a subagent model via /subagents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subagents": {
                        "type": "array",
                        "description": "List of subagents to spawn. Recommended 1-4.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Name of the subagent role, e.g. BackendDeveloper",
                                },
                                "task": {
                                    "type": "string",
                                    "description": "Specific task for this subagent",
                                },
                                "thinking_level": {
                                    "type": "string",
                                    "description": "Requested thinking level for this subagent (e.g. Low, Medium, High). Leave empty for default.",
                                },
                                "context_files": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of relevant files for this subagent",
                                },
                            },
                            "required": ["name", "task"],
                        },
                    }
                },
                "required": ["subagents"],
            },
        },
    },
]
