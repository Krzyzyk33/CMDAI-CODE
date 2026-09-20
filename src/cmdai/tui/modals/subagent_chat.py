import os
from typing import Dict, Any, List, Optional
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

SUBAGENT_CHAT_CSS = """
SubagentChatModal {
    align: center middle;
    background: rgba(0, 0, 0, 0.70);
    color: #c9d1d9;
}

#subagent-modal-dialog {
    width: 82;
    max-width: 90%;
    height: 80%;
    max-height: 85%;
    background: #0d1117;
    border: none;
    padding: 0;
}

#subagent-chat-scroll {
    width: 100%;
    height: 1fr;
    padding: 1 2;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

.subagent-user-box {
    width: 100%;
    height: auto;
    background: #161b22;
    border: none;
    padding: 1 3;
    margin: 1 0;
    color: #e6edf3;
}

.subagent-agent-box {
    width: 100%;
    height: auto;
    background: transparent;
    border: none;
    padding: 1 2;
    margin: 1 0;
    color: #e6edf3;
}

.subagent-agent-header {
    height: 1;
    width: 100%;
    text-align: center;
    content-align: center middle;
    padding: 0;
    margin: 0 0 1 0;
    background: transparent;
}

.subagent-agent-footer {
    height: 1;
    width: 100%;
    margin-top: 1;
    padding: 0;
    color: #8b949e;
}

#subagent-system-prompt-dock {
    height: auto;
    max-height: 8;
    background: #161b22;
    border-top: solid #21262d;
    padding: 1 2;
}

#subagent-system-header-row {
    width: 100%;
    height: 1;
    margin-bottom: 1;
}

#subagent-system-header {
    width: 1fr;
    color: #8b949e;
    text-style: bold;
}

#subagent-system-meta {
    width: auto;
    color: #8b949e;
}

#subagent-system-body {
    width: 100%;
    height: auto;
    color: #c9d1d9;
}
"""

class SubagentChatModal(ModalScreen[None]):
    """Normal-agent-looking read-only chat for one subagent, with left/right
    navigation between sibling subagents and the system prompt dock."""

    CSS = SUBAGENT_CHAT_CSS

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("ctrl+q", "dismiss_modal", "Close"),
        Binding("left", "prev_agent", "Prev", show=False),
        Binding("right", "next_agent", "Next", show=False),
    ]

    def __init__(
        self,
        role: str,
        goal: str,
        system_prompt: str,
        history: Optional[List[Dict[str, Any]]] = None,
        findings: Optional[List[str]] = None,
        agent_index: int = 1,
        agent_total: int = 1,
        agents: Optional[List[Dict[str, Any]]] = None,
        live_agent: Optional[Any] = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.agents: List[Dict[str, Any]] = list(agents) if agents else [{
            "role": role, "goal": goal, "system_prompt": system_prompt,
            "history": history or [], "findings": findings or [],
            "status": "completed",
        }]
        self.agent_index = max(1, min(agent_index, len(self.agents)))
        self.agent_total = len(self.agents)
        self.live_agent = live_agent
        self._refresh_timer = None

    def _current(self) -> Dict[str, Any]:
        if self.live_agent is not None:
            b = self.live_agent
            return {
                "role": getattr(b, "role", "Subagent"),
                "goal": getattr(b, "goal", ""),
                "system_prompt": getattr(b, "system_prompt", ""),
                "history": list(getattr(b, "history", []) or []),
                "findings": list(getattr(b, "findings", []) or []),
                "elapsed": float(getattr(b, "elapsed", 0.0) or 0.0),
                "tokens": int(getattr(b, "sub_tokens", 0) or 0),
                "error": getattr(b, "error_msg", "") if getattr(b, "has_error", False) else "",
                "status": getattr(b, "status", "running"),
            }
        return self.agents[self.agent_index - 1]

    def compose(self) -> ComposeResult:
        with Vertical(id="subagent-modal-dialog"):
            with VerticalScroll(id="subagent-chat-scroll"):
                with Vertical(classes="subagent-agent-box"):
                    yield Static("", id="subagent-agent-header", classes="subagent-agent-header")
                    yield Static("", id="subagent-agent-body", classes="card-body")
                    yield Static("", id="subagent-agent-footer", classes="subagent-agent-footer")
            with Vertical(id="subagent-system-prompt-dock"):
                with Horizontal(id="subagent-system-header-row"):
                    yield Static("TASK & SYSTEM PROMPT", id="subagent-system-header")
                    yield Static("", id="subagent-system-meta")
                yield Static("", id="subagent-system-body")

    def on_mount(self) -> None:
        self._render_agent()
        if self.live_agent is not None:
            self._refresh_timer = self.set_interval(0.2, self._render_agent)

    def _render_agent(self) -> None:
        cur = self._current()
        role = str(cur.get("role", "Subagent"))
        try:
            header = self.query_one("#subagent-agent-header", Static)
            t = Text()
            t.append("⌬  ", style="bold #58a6ff")
            t.append(role, style="bold #e6edf3")
            t.justify = "center"
            header.update(t)
        except Exception:
            pass
        try:
            body = self.query_one("#subagent-agent-body", Static)
            hist = cur.get("history") or []
            t = Text()
            has_rendered = False
            import re as _re
            for msg in hist:
                m_role = msg.get("role")
                content = str(msg.get("content", "")).strip()
                if not content or m_role == "system":
                    continue
                if m_role == "assistant":
                    # Render outgoing XML tool calls before removing them from
                    # prose. Previously they were stripped, making a working
                    # subagent appear to have no normal agent tools.
                    calls = list(_re.finditer(r'<tool:(\w+)\s*([^>]*?)/?\s*>', content, _re.IGNORECASE | _re.DOTALL))
                    for call in calls:
                        tool_name = call.group(1)
                        attrs = _re.sub(r'\s+', ' ', call.group(2)).strip().rstrip('/')
                        t.append(f"  ◈ {tool_name}", style="bold #58a6ff")
                        if attrs:
                            t.append(f"  {attrs[:120]}", style="#8b949e")
                        t.append("\n")
                        has_rendered = True
                    clean = _re.sub(r'<(?:tool:?\w*|\|tool_call\w*|tool_call\w*)[^>]*?(/?>|>.*?</(?:tool:\w+|\|tool_call|tool_call)>)', '', content, flags=_re.DOTALL | _re.IGNORECASE)
                    clean = _re.sub(r'</?(?:tool:\w+|\|tool_call|tool_call)[^>]*>', '', clean, flags=_re.IGNORECASE).strip()
                    if clean:
                        has_rendered = True
                        for line in clean.splitlines():
                            t.append(f"{line}\n", style="#e6edf3")
                        t.append("\n")
                elif m_role == "user":
                    if content.startswith("[tool:"):
                        has_rendered = True
                        lines = content.splitlines()
                        hdr = lines[0]
                        tool_match = _re.match(r"\[tool:(\w+)\s+result\]", hdr, _re.IGNORECASE)
                        tool_lbl = tool_match.group(1) if tool_match else hdr.strip("[]")
                        t.append(f"  ● {tool_lbl}\n", style="bold #8b949e")
                        for bl in lines[1:10]:
                            t.append(f"    {bl}\n", style="#8b949e")
                        if len(lines) > 10:
                            t.append(f"    ... [{len(lines)-10} lines hidden]\n", style="dim #484f58")
                        t.append("\n")
                    elif content != str(cur.get("goal", "")).strip():
                        has_rendered = True
                        t.append(f"{content}\n\n", style="#8b949e")
            if cur.get("error"):
                t.append("Error:\n", style="bold #f85149")
                t.append(f"  {cur['error']}\n\n", style="#ff7b72")
            findings = cur.get("findings") or []
            if findings:
                t.append("Key findings:\n", style="bold #e6edf3")
                for f in findings:
                    t.append(f"  • {f}\n", style="#8b949e")
                t.append("\n")
            elif not has_rendered and not cur.get("error"):
                t.append("_No output yet._\n", style="dim #8b949e")
            body.update(t)
        except Exception:
            pass
        try:
            elapsed = float(cur.get("elapsed", 0.0) or 0.0)
            tokens = int(cur.get("tokens", 0) or 0)
            is_err = bool(cur.get("error"))
            if is_err:
                foot = "[b #f85149]●[/]  [bold #f85149]Failed[/]"
            else:
                foot = "[b #58a6ff]⌬[/]  [bold #e6edf3]Done[/]"
            if elapsed > 0:
                foot += f"  [dim]·  {elapsed:.1f}s[/dim]"
            if tokens > 0:
                foot += f"  [dim]·  {tokens} tokens[/dim]"
                if elapsed > 0:
                    foot += f"  [dim]({tokens / elapsed:.1f} tok/s)[/dim]"
            self.query_one("#subagent-agent-footer", Static).update(foot)
        except Exception:
            pass
        try:
            self.query_one("#subagent-system-meta", Static).update(
                f"[dim]Agent {self.agent_index} of {self.agent_total} · esc exit[/dim]")
            goal = str(cur.get("goal", "")).strip()
            sys_p = str(cur.get("system_prompt", "")).strip()
            task_text = f"Goal: {goal}" if goal else ""
            if sys_p:
                task_text = f"{task_text}\nSystem: {sys_p}" if task_text else f"System: {sys_p}"
            self.query_one("#subagent-system-body", Static).update(task_text or "No task description.")
        except Exception:
            pass

    def action_prev_agent(self) -> None:
        if len(self.agents) > 1:
            self.agent_index = (self.agent_index - 2) % len(self.agents) + 1
            self._render_agent()

    def action_next_agent(self) -> None:
        if len(self.agents) > 1:
            self.agent_index = self.agent_index % len(self.agents) + 1
            self._render_agent()

    def action_dismiss_modal(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.stop()
            self._refresh_timer = None
        self.dismiss()
