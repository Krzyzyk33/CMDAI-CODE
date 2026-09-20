from typing import Dict, Any, List, Optional

TOOL_DEFINITIONS = {
    "read": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"}
        },
        "required": ["path"]
    },
    "write": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        },
        "required": ["path", "content"]
    },
    "edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_str": {"type": "string"},
            "new_str": {"type": "string"}
        },
        "required": ["path", "old_str", "new_str"]
    },
    "ls": {
        "type": "object",
        "properties": {
            "path": {"type": "string"}
        }
    },
    "glob": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"}
        },
        "required": ["pattern"]
    },
    "search": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"}
        },
        "required": ["query"]
    },
    "command": {
        "type": "object",
        "properties": {
            "cmd": {"type": "string"}
        },
        "required": ["cmd"]
    },
    "scratch": {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "name": {"type": "string"},
            "content": {"type": "string"}
        },
        "required": ["action"]
    },
    "ask": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {"type": "array"}
        },
        "required": ["question"]
    },
    "web": {
        "type": "object",
        "properties": {
            "url": {"type": "string"}
        },
        "required": ["url"]
    }
}

class ToolGrammarGenerator:

    @staticmethod
    def generate_gbnf(tools: Optional[List[str]] = None) -> str:
        selected_tools = tools or list(TOOL_DEFINITIONS.keys())
        lines = [
            "# GBNF grammar for CMDAI CODE tool calls",
            "root ::= ws tool_call ws",
            'tool_call ::= "{" ws "\"name\"" ws ":" ws tool_name ws "," ws "\"arguments\"" ws ":" ws tool_args ws "}"',
        ]
        tool_names_str = " | ".join(f'\"{t}\"' for t in selected_tools)
        lines.append(f"tool_name ::= {tool_names_str}")
        lines.append('tool_args ::= "{" ws (string ws ":" ws value (ws "," ws string ws ":" ws value)*)? ws "}"')
        lines.append('value ::= string | number | boolean | "null" | array | object')
        lines.append('object ::= "{" ws (string ws ":" ws value (ws "," ws string ws ":" ws value)*)? ws "}"')
        lines.append('array ::= "[" ws (value (ws "," ws value)*)? ws "]"')
        lines.append('string ::= "\"" ([^"\\\r\n] | escape)* "\""')
        lines.append('escape ::= "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])')
        lines.append('number ::= ("-"? ([0-9] | [1-9] [0-9]*)) ("." [0-9]+)? ([eE] [-+]? [0-9]+)?')
        lines.append('boolean ::= "true" | "false"')
        lines.append('ws ::= [ \t\n\r]*')
        return "\n".join(lines)
