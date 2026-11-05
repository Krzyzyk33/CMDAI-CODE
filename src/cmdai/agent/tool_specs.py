"""Centralized Tool Specifications and JSON Schemas for CMDAI.

Single source of truth for tool calling:
- Generates JSON Schema tool definitions for native tool-calling APIs (OpenAI, Anthropic, Gemini, OpenRouter)
- Generates XML prompts for local/GGUF models
- Provides unified parsing and conversion between native tool calls and XML representations.
"""

import json
import re
from typing import Any, Dict, List, Optional


TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "read",
        "description": "Read contents of a file in the workspace, with optional line range.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file"},
                "lines": {"type": "string", "description": "Optional line range, e.g. '1-100'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": "Create a new file or completely overwrite an existing file with the provided content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file to create or overwrite"},
                "content": {"type": "string", "description": "Complete file content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit",
        "description": "Surgically edit a file by replacing an exact snippet of existing text with new replacement text.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file to modify"},
                "old": {"type": "string", "description": "Exact existing text block to be replaced"},
                "new": {"type": "string", "description": "New replacement text block"},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "ls",
        "description": "List directory contents including files and subdirectories.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (defaults to '.')"},
            },
        },
    },
    {
        "name": "glob",
        "description": "Find files matching a glob pattern.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "File search pattern (e.g. '*.py')"},
                "dir": {"type": "string", "description": "Directory to search within (defaults to '.')"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "search",
        "description": "Search for a text pattern or regular expression across workspace files.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text or regex"},
                "path": {"type": "string", "description": "Directory or file to search within (defaults to '.')"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "code_search",
        "description": "Search code symbols, functions, or classes using AST / indexing.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Symbol, function, or class name to find"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "command",
        "description": "Execute a shell or terminal command in the workspace directory. Set background=true to run long commands asynchronously in the background.",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Command line string to execute"},
                "background": {"type": "boolean", "description": "Optional: set true to run in background without blocking"},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "web",
        "description": "Search the web or retrieve documentation from a URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query or URL"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "bugs",
        "description": "Run static analysis diagnostics, syntax verifications, and linter checks on workspace files.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "todo",
        "description": "Manage autonomous task checklist: 'set' replaces checklist, 'add' appends, 'done' marks complete.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "done", "set"], "description": "Action type"},
                "note": {"type": "string", "description": "Checklist step description"},
                "todos": {"type": "string", "description": "Pipe-separated steps for 'set' action, e.g. 'step1|step2'"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "subagent",
        "description": "Spawn an autonomous subagent for parallel or isolated task delegation.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short role for the subagent (e.g. 'Tester')"},
                "system": {"type": "string", "description": "System instructions for the subagent"},
                "task": {"type": "string", "description": "Task description to execute"},
                "background": {"type": "boolean", "description": "Optional: set true to run the subagent in the background"},
            },
            "required": ["name", "task"],
        },
    },
]


def get_native_tool_schemas() -> List[Dict[str, Any]]:
    """Return list of standard JSON Schema tool objects formatted for OpenAI/OpenRouter/Anthropic API calls."""
    schemas = []
    for tool in TOOL_DEFINITIONS:
        schemas.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        })
    return schemas


def format_tool_call_as_xml(tool_name: str, args: Dict[str, Any]) -> str:
    """Format tool call arguments into standard CMDAI XML tag for unified handling."""
    name = tool_name.lower().strip()
    if name == "write":
        path = args.get("path", "")
        content = args.get("content", "")
        return f'<tool:write path="{path}">{content}</tool:write>'
    elif name == "edit":
        path = args.get("path", "")
        old = args.get("old", "")
        new = args.get("new", "")
        return f'<tool:edit path="{path}" old="{old}">{new}</tool:edit>'
    elif name == "read":
        path = args.get("path", "")
        lines = args.get("lines")
        if lines:
            return f'<tool:read path="{path}" lines="{lines}" />'
        return f'<tool:read path="{path}" />'
    elif name == "command":
        cmd = args.get("cmd") or args.get("command", "")
        bg = str(args.get("background", "")).lower() in ("true", "1", "yes")
        bg_attr = ' background="true"' if bg else ""
        return f'<tool:command cmd="{cmd}"{bg_attr} />'
    elif name == "todo":
        action = args.get("action", "add")
        if action == "set":
            todos = args.get("todos", "")
            return f'<tool:todo action="set" todos="{todos}" />'
        elif action == "done":
            note = args.get("note", "")
            return f'<tool:todo action="done" note="{note}" />'
        else:
            note = args.get("note", "")
            return f'<tool:todo action="add" note="{note}" />'
    elif name == "subagent":
        s_name = args.get("name", "Worker")
        system = args.get("system", "")
        task = args.get("task", "")
        bg = str(args.get("background", "")).lower() in ("true", "1", "yes")
        bg_attr = ' background="true"' if bg else ""
        if system:
            return f'<tool:subagent name="{s_name}" system="{system}"{bg_attr}>{task}</tool:subagent>'
        return f'<tool:subagent name="{s_name}"{bg_attr}>{task}</tool:subagent>'
    else:
        # Generic attribute formatting
        attr_strs = [f'{k}="{v}"' for k, v in args.items() if isinstance(v, (str, int, float, bool))]
        attrs = " " + " ".join(attr_strs) if attr_strs else ""
        return f'<tool:{name}{attrs} />'
