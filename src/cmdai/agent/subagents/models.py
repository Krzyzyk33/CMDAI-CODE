from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class SubagentTask:
    role: str
    goal: str
    system_prompt: str
    tools: List[str] = field(default_factory=lambda: ["read", "search", "glob", "scratch"])
    context: Dict[str, Any] = field(default_factory=dict)
    # Optional notes the subagent writes back for the lead agent via
    # <tool:agent_note text="..." />. Collected on the task so the executor
    # closure can append to them without another channel.
    notes: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class SubagentProgress:
    role: str
    status: str
    current_tool: Optional[str] = None
    tool_arg: Optional[str] = None
    thought: Optional[str] = None
    tokens_count: int = 0
    goal: str = ""
    system_prompt: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    note: str = ""
    # Ordered tool activity as real dicts, so the TUI can rebuild the session
    # with the same ToolBlock widgets the main chat uses instead of parsing the
    # stringified "[tool:x result]" history back into shapes.
    events: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    missing: str = ""
    example: str = ""
    success: bool = True

@dataclass
class SubagentResult:
    role: str
    summary: str
    artifacts: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    notes: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    # Wall-clock seconds this subagent ran, measured by the orchestrator so the
    # TUI does not have to guess from when a widget happened to be created.
    elapsed: float = 0.0
    # Explicit outcome flag. The lead agent used to infer failure by sniffing
    # the summary text, which reported "no model backend" as a success.
    success: bool = True
