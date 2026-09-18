from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class SubagentTask:
    role: str
    goal: str
    system_prompt: str
    tools: List[str] = field(default_factory=lambda: ["read", "search", "glob", "scratch"])
    context: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SubagentProgress:
    role: str
    status: str
    current_tool: Optional[str] = None
    tool_arg: Optional[str] = None
    thought: Optional[str] = None
    tokens_count: int = 0

@dataclass
class SubagentResult:
    role: str
    summary: str
    artifacts: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
