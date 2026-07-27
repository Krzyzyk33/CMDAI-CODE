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
from .planning import save_plan, todo_done, submit_plan
from .bugs import run_bugs
from .subagents import wakeup_subagents
from .question_ui import execute_answer_tool


def execute_tool(
    name: str,
    args: Dict[str, Any],
    restricted_dir: Optional[str] = None,
    agent_instance=None,
) -> str:
    if "__json_error__" in args:
        return "Error: " + args["__json_error__"]
    import os

    if "path" not in args or not args["path"]:
        for k in ["file", "filename", "file_path", "filepath", "target", "target_file", "file_name", "path_to_file", "p", "doc", "uri"]:
            if k in args and args[k]:
                args["path"] = args[k]
                break
    if "query" not in args or not args["query"]:
        for k in ["pattern", "search", "term", "text", "q", "query_text", "keyword"]:
            if k in args and args[k]:
                args["query"] = args[k]
                break
    for k in ["path", "pattern"]:
        if k in args and isinstance(args[k], str):
            p = args[k].strip()
            if p in ("/", "\\", "C:\\", "C:/", "D:\\", "D:/") or p == os.path.splitdrive(p)[0] + os.sep:
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
            return f"Error: IDE isolation is active. Access denied to path {args['path']} - outside current project directory."

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
        "search_web": search_web,
        "save_plan": save_plan,
        "todo_done": todo_done,
        "submit_plan": submit_plan,
        "bugs": run_bugs,
        "wakeup_subagents": wakeup_subagents,
        "answer": execute_answer_tool,
        "ask_question": execute_answer_tool,
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
        if not args or "missing" in str(e).lower():
            missing_arg = "path" if "path" in str(e) else "required arguments"
            return f"Error executing '{name}': Missing {missing_arg}. Please provide the required parameters (e.g. {name}(path='...'))"
        return f"Error executing '{name}': Invalid arguments ({e})"
    except Exception as e:
        return f"Error executing '{name}': {e}"

TOOLS_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "replace_lines",
            "description": "Replaces lines from start_line to end_line with new_content. EXAMPLE CALL: {\"path\": \"src/main.py\", \"start_line\": 10, \"end_line\": 15, \"new_content\": \"def new_func():\\n    pass\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path to edit."},
                    "start_line": {"type": "integer", "description": "Starting line number (1-indexed)."},
                    "end_line": {"type": "integer", "description": "Ending line number (1-indexed, inclusive)."},
                    "new_content": {"type": "string", "description": "Replacement text for the specified lines."},
                },
                "required": ["path", "start_line", "end_line", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "Appends text to the end of a file. EXAMPLE CALL: {\"path\": \"src/config.py\", \"content\": \"\\nEXTRA_SETTING = True\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path to append to."},
                    "content": {"type": "string", "description": "Text content to append."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads the contents of a file. EXAMPLE CALL: {\"path\": \"src/main.py\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Creates a new file with specified content. EXAMPLE CALL: {\"path\": \"src/utils.py\", \"content\": \"def helper():\\n    pass\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path for the new file."},
                    "content": {"type": "string", "description": "File content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replaces old_str with new_str in a file. EXAMPLE CALL: {\"path\": \"src/agent.py\", \"old_str\": \"foo = 1\", \"new_str\": \"foo = 2\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path."},
                    "old_str": {"type": "string", "description": "Exact text to replace."},
                    "new_str": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Overwrites an entire file. EXAMPLE CALL: {\"path\": \"src/schema.py\", \"content\": \"# full content\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path."},
                    "content": {"type": "string", "description": "Entire file content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Deletes a file or directory. EXAMPLE CALL: {\"path\": \"src/temp.py\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or directory path to delete."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commands",
            "description": "Runs a shell command/script. EXAMPLE CALL: {\"command\": \"python -m pytest\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."},
                    "timeout": {"type": "integer", "description": "Optional timeout in seconds."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search for regex pattern across files. EXAMPLE CALL: {\"pattern\": \"def get_user\", \"path\": \"src\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex or string pattern to find."},
                    "path": {"type": "string", "description": "Directory or file path to search."},
                    "glob_pattern": {"type": "string", "description": "Optional file filter pattern (e.g. '*.py')."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "List files matching a glob pattern (e.g. '*.py' or 'src/**/*.ts'). CRITICAL: Calling glob() with empty arguments {} is STRICTLY FORBIDDEN and will raise an error. You MUST ALWAYS provide the 'pattern' argument. EXAMPLE CALL: {\"pattern\": \"src/**/*.py\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "REQUIRED glob pattern (e.g. '*.py' or '**/*.js'). MUST NOT be empty."
                    }
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List contents of a directory. EXAMPLE CALL: {\"path\": \"src\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list (default '.')."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Searches the web for documentation or solutions. EXAMPLE CALL: {\"query\": \"python prompt_toolkit win32 border\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query terms."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_plan",
            "description": "Saves your execution plan to plan.md. EXAMPLE CALL: {\"content\": \"# Plan\\n1. Step 1\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Markdown plan content to save."}
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_done",
            "description": "Marks a step as done in plan.md by replacing [ ] with [x]. EXAMPLE CALL: {\"step_number\": 1}",
            "parameters": {
                "type": "object",
                "properties": {
                    "step_number": {"type": "integer", "description": "1-based step index to mark as completed."}
                },
                "required": ["step_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_plan",
            "description": "Submit an architectural plan before executing changes. EXAMPLE CALL: {\"architecture_details\": \"Clean architecture with 3 modules\", \"steps_list\": [\"1. Create models\", \"2. Build API\"]}",
            "parameters": {
                "type": "object",
                "properties": {
                    "architecture_details": {"type": "string", "description": "Overview of structural design and patterns."},
                    "steps_list": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                        "description": "Sequential execution steps."
                    },
                },
                "required": ["architecture_details", "steps_list"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_search",
            "description": "Searches project files for symbol definitions or usages. EXAMPLE CALL: {\"query\": \"def get_system_message\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term or symbol name.",
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
            "description": "Runs project unit tests. EXAMPLE CALL: {\"files\": [\"tests/test_agent.py\"]}",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of file paths to test.",
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
            "description": "Scans the entire project for Python syntax errors and returns a list of syntax issues. Takes no arguments. EXAMPLE CALL: {}",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wakeup_subagents",
            "description": "Wake up one or more subagents to run tasks. ALWAYS use native JSON function calling! EXAMPLE CALL: {\"subagents\": [{\"name\": \"CodeArchitect\", \"task\": \"Refactor module boundaries\"}]}",
            "parameters": {
                "type": "object",
                "properties": {
                    "subagents": {
                        "type": "array",
                        "minItems": 1,
                        "description": "List of subagents to spawn.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Role or name of the subagent (e.g. 'CodeArchitect').",
                                },
                                "task": {
                                    "type": "string",
                                    "description": "Detailed task description for the subagent.",
                                },
                                "thinking_level": {
                                    "type": "string",
                                    "description": "Requested thinking level ('low', 'medium', 'high', 'ultra', 'extreme', 'auto').",
                                },
                                "context_files": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of relevant files for this subagent.",
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
    {
        "type": "function",
        "function": {
            "name": "answer",
            "description": "Ask the user interactive multi-choice questions via a terminal wizard popup window. Use this tool whenever you need clarification, user preferences, or architectural decisions. EXAMPLE CALL: {\"questions\": [{\"question\": \"Which DB engine?\", \"description\": \"Select main datastore\", \"options\": [{\"label\": \"PostgreSQL\", \"description\": \"Relational RDBMS\"}, {\"label\": \"MongoDB\", \"description\": \"NoSQL Document Store\"}]}]}",
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "minItems": 1,
                        "description": "Non-empty list of questions to ask the user. Must contain at least 1 question.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "Clear title of the question (e.g. 'What architecture pattern should we use?')."
                                },
                                "description": {
                                    "type": "string",
                                    "description": "REQUIRED context subtitle explaining the question scope (e.g. 'Select structural pattern for modules')."
                                },
                                "options": {
                                    "type": "array",
                                    "minItems": 2,
                                    "description": "List of choices for the question. Must contain at least 2 options.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                "type": "string",
                                                "description": "Short option title (e.g. 'Clean Architecture')."
                                            },
                                            "description": {
                                                "type": "string",
                                                "description": "REQUIRED subtitle explaining what this option does or its impact."
                                            }
                                        },
                                        "required": ["label", "description"]
                                    }
                                }
                            },
                            "required": ["question", "description", "options"]
                        }
                    }
                },
                "required": ["questions"]
            }
        }
    },
]

TOOLS_SUMMARY = "Tools available: " + ", ".join(d["function"]["name"] for d in TOOLS_DEFINITIONS) + "."
