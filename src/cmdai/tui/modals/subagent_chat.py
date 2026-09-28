import os
from typing import Dict, Any, List, Optional
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..widgets import AssistantTurnCard

SUBAGENT_CHAT_CSS = """
SubagentChatModal {
    align: left top;
    background: #000000;
    color: #c9d1d9;
}

/* Fullscreen, frameless, no margin - mirrors #chat-scroll in styles.py so the
   window reads as the normal chat. */
#subagent-modal-dialog {
    width: 100%;
    height: 100%;
    max-width: 100%;
    max-height: 100%;
    background: #000000;
    border: none;
    padding: 0;
    margin: 0;
}

#subagent-chat-scroll {
    width: 100%;
    height: 1fr;
    margin: 0;
    padding: 0 2;
    scrollbar-size-vertical: 0;
    scrollbar-gutter: auto;
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

.subagent-card {
    width: 100%;
    height: auto;
    background: transparent;
    border: none;
    margin: 0;
    padding: 0;
}

/* No bottom panel: the window is the normal chat, so the card fills it. The
   sibling position lives in the header instead, next to the subagent name. */
.subagent-sibling-hint {
    color: #484f58;
}

/* Task dock: flush to the left, right and bottom edge, no margin, same
   padding shape as #bottom-dock in the chat. Tall enough that the task reads
   as a panel instead of a squeezed strip. */
#subagent-task-dock {
    height: auto;
    min-height: 7;
    max-height: 14;
    background: #161b22;
    border: none;
    border-top: solid #21262d;
    padding: 1 3 1 3;
    margin: 0;
}

#subagent-task-header-row {
    width: 100%;
    height: 1;
    margin: 0 0 1 0;
}

#subagent-task-header {
    width: 1fr;
    color: #8b949e;
    text-style: bold;
}

#subagent-task-meta {
    width: auto;
    color: #8b949e;
}

#subagent-task-body {
    width: 100%;
    height: auto;
    color: #e6edf3;
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
        # Incremental state for the live window. The window grows the way the
        # lead agent's card grows: new prose and new tools are appended, never
        # rebuilt. That matters because the old path wiped the card and
        # remounted every block five times a second, so finished tools were
        # destroyed and recreated and the whole window flickered while only one
        # tool was actually running.
        self._live_done = 0        # items already emitted
        self._live_todo_open = False   # a <tool:todo> run is still open
        self._live_summary = ""    # closing report already appended
        self._live_footed = False  # card.finish() already called
        self._task_shown = ""      # task text already in the dock

    def _current(self) -> Dict[str, Any]:
        if self.live_agent is not None:
            b = self.live_agent
            return {
                "role": getattr(b, "subagent_role", "") or getattr(b, "role", "Subagent"),
                "goal": getattr(b, "subagent_goal", "") or getattr(b, "goal", ""),
                "system_prompt": getattr(b, "subagent_system", "") or getattr(b, "system_prompt", ""),
                "history": list(getattr(b, "subagent_history", []) or getattr(b, "history", []) or []),
                "findings": list(getattr(b, "subagent_findings", []) or getattr(b, "findings", []) or []),
                "notes": list(getattr(b, "subagent_notes", []) or []),
                "events": list(getattr(b, "subagent_events", []) or []),
                "elapsed": float(getattr(b, "subagent_elapsed", 0.0) or 0.0),
                "tokens": int(getattr(b, "subagent_tokens", 0) or 0),
                "error": getattr(b, "error_msg", "") if getattr(b, "has_error", False) else "",
                "status": getattr(b, "subagent_status", "") or getattr(b, "status", "running"),
            }
        return self.agents[self.agent_index - 1]

    def compose(self) -> ComposeResult:
        with Vertical(id="subagent-modal-dialog"):
            with VerticalScroll(id="subagent-chat-scroll"):
                # The real assistant card, not a lookalike: same widgets, same
                # ToolBlock click-to-expand, same footer as the lead agent chat.
                yield AssistantTurnCard("Subagent", has_thinking=False, classes="subagent-card")
            # The task the lead agent wrote, full width, flush to the bottom.
            with Vertical(id="subagent-task-dock"):
                with Horizontal(id="subagent-task-header-row"):
                    yield Static("TASK", id="subagent-task-header")
                    yield Static("", id="subagent-task-meta")
                yield Static("", id="subagent-task-body")

    def on_mount(self) -> None:
        # After the first refresh the card is mounted, so its blocks are appended
        # in call order instead of queueing up unordered.
        self.call_after_refresh(self._render_agent)
        if self.live_agent is not None:
            self._refresh_timer = self.set_interval(0.2, self._refresh_live)

    # ---------------------------------------------------------------- build

    @staticmethod
    def _items(cur: Dict[str, Any]) -> List[Any]:
        """Flatten a subagent session into the order it produced: prose and
        tools interleaved.

        Uses the one parser the lead agent and the orchestrator use, so the
        window understands exactly the syntax the model was scored on. Its own
        regexes here only matched <tool:...>: a Gemma turn came through as
        `call:tool:ls path="." />` with the <|tool_call> prefix half stripped
        and a dangling "/>", i.e. raw protocol on screen as if it were prose.

        Walk the history so a sentence stays above the tools it introduced.
        Each assistant message carries its own tool calls, so the number of
        calls in it says how many events to attach after the prose. Events the
        history has not caught up with yet are appended at the end, because a
        live run records an event once the tool returned, which is after the
        model already asked for it.

        Only appends happen as a run proceeds, so the prefix of this list is
        stable - which is what lets the live path emit only the tail.
        """
        from ...agent.runner import _parse_only

        items: List[Any] = []
        events = list(cur.get("events") or [])
        cursor = 0
        for msg in cur.get("history") or []:
            # "user" turns are "[tool:x result]" feedback and the goal; both are
            # already carried by the structured events and the task dock.
            if msg.get("role") != "assistant":
                continue
            for kind, payload in _parse_only(str(msg.get("content", "") or "")):
                if kind == "text":
                    if payload:
                        items.append(("text", payload))
                    continue
                if cursor >= len(events):
                    break
                items.append(("tool", events[cursor]))
                cursor += 1
        for ev in events[cursor:]:
            items.append(("tool", ev))
        return items

    def _emit_items(self, card: Any, items: List[Any], start: int) -> None:
        """Append items[start:] to the card, finishing every tool as it lands.

        A block is finished the moment it is created, so it is born with a
        static marker and never spins. Only the separate live-tool line at the
        bottom of the card animates, exactly like the lead agent's.
        """
        # A subagent that keeps its checklist up to date calls <tool:todo> over
        # and over. Showing one marker per call filled the window with duplicate
        # "Todo" lines, so only the first of a run survives.
        todo_open = self._live_todo_open
        for kind, payload in items[start:]:
            if kind == "text":
                card.append_text_block(payload)
                continue
            ev = payload
            tool = str(ev.get("tool", "") or "tool")
            if tool in ("todo", "tasks", "scratch"):
                if todo_open:
                    continue
                todo_open = True
            else:
                todo_open = False
            block = card.add_tool(tool, str(ev.get("target", "") or ""))
            res = ev.get("result")
            if not isinstance(res, dict):
                res = {"success": bool(ev.get("ok", True)), "content": str(res or "")}
            block.finish(res)
        self._live_todo_open = todo_open

    def _build_card(self, cur: Dict[str, Any], live: bool = False) -> Any:
        """Replay a finished subagent session into a real AssistantTurnCard.

        Used for a finished subagent and whenever the shown subagent changes.
        A running one goes through _advance_live instead, which appends rather
        than rebuilding.
        """
        card = self.query_one(AssistantTurnCard)
        card.model_name = str(cur.get("role", "Subagent"))
        # Wipe whatever the previously shown subagent left behind.
        try:
            card.flow_container.remove_children()
        except Exception:
            pass
        for attr in ("tool_blocks", "body_widgets"):
            if hasattr(card, attr):
                try:
                    setattr(card, attr, [])
                except Exception:
                    pass
        card.current_body_widget = None
        card.raw_text = ""
        try:
            card.update_header()
        except Exception:
            pass

        # The full replay uses the same item list the live path appends from,
        # so a finished subagent and a running one read identically.
        self._live_done = 0
        self._live_todo_open = False
        self._live_summary = ""
        self._live_footed = False
        items = self._items(cur)
        self._emit_items(card, items, 0)
        self._live_done = len(items)

        # The report the lead agent reads, as plain closing text.
        summary = str(cur.get("summary", "")).strip()
        if summary:
            card.append_text_block(summary)

        status = str(cur.get("status", "completed")).lower()
        elapsed = float(cur.get("elapsed", 0.0) or 0.0)
        tokens = int(cur.get("tokens", 0) or 0)
        if status == "failed" or cur.get("error"):
            card.append_text_block(str(cur.get("error") or summary or "Subagent failed."))
        if live:
            # Still working: keep the card generating so the footer reads
            # "Running" with the tool it is on, exactly like the lead agent.
            tool_and_target = str(getattr(self.live_agent, "subagent_live_tool", "") or "").strip()
            if tool_and_target and tool_and_target != "starting":
                tool = tool_and_target.split(" ", 1)[0]
                target = tool_and_target.split(" ", 1)[1] if " " in tool_and_target else ""
                card.set_generating_tool(tool, target)
            else:
                card.set_generating_tool("thinking", "…")
        else:
            card.finish(
                elapsed=elapsed if elapsed >= 0.05 else None,
                tokens=tokens,
                tok_s=(tokens / elapsed) if (tokens and elapsed >= 0.05) else 0.0,
            )
        if self.agent_total > 1:
            # Sibling position sits in the task dock, as in the mock, so the
            # header keeps the exact shape the lead agent chat has.
            try:
                self.query_one("#subagent-task-meta", Static).update(
                    f"[dim]Agent {self.agent_index} of {self.agent_total} · esc exit[/dim]")
            except Exception:
                pass
        # The task is the text the lead agent wrote for this subagent, shown
        # raw. The generated system prompt stays out of the way.
        try:
            self.query_one("#subagent-task-body", Static).update(
                str(cur.get("goal", "") or "").strip() or "No task description.")
        except Exception:
            pass
        return card

    def _render_agent(self) -> None:
        live = self.live_agent is not None and bool(getattr(self.live_agent, "tool_running", False))
        try:
            self._build_card(self._current(), live=live)
        except Exception:
            pass

    def _show_task(self, cur: Dict[str, Any]) -> None:
        """The task the lead agent wrote, raw, in the dock.

        Refreshed on every tick while the subagent runs: the goal reaches the
        marker before the model has even answered, but a window opened during
        the very first turn can still get a first, empty version of it.
        """
        task = str(cur.get("goal", "") or "").strip()
        if not task or task == self._task_shown:
            return
        self._task_shown = task
        try:
            self.query_one("#subagent-task-body", Static).update(task)
        except Exception:
            pass

    def _advance_live(self, cur: Dict[str, Any], running: bool) -> None:
        """Append whatever the subagent produced since the last tick.

        This is the whole point: nothing already on screen is touched. The
        lead agent's card grows the same way - add_tool appends, finish_tool
        closes a block, set_generating_tool repaints one line at the bottom.
        Rebuilding here is what made the window flicker.
        """
        card = self.query_one(AssistantTurnCard)
        card.model_name = str(cur.get("role", "Subagent"))
        self._show_task(cur)

        items = self._items(cur)
        if len(items) > self._live_done:
            self._emit_items(card, items, self._live_done)
            self._live_done = len(items)

        # The report, once, when there is one. A live run has none until the
        # subagent wraps up.
        summary = str(cur.get("summary", "") or "").strip()
        if summary and summary != self._live_summary:
            self._live_summary = summary
            card.append_text_block(summary)

        if running:
            # One animated line at the bottom, same as the lead agent. The
            # tools above it stay still. No "Thinking" line: between tools the
            # subagent has nothing to show, and an empty pulsing row there read
            # as a tool that did not work.
            tool_and_target = str(getattr(self.live_agent, "subagent_live_tool", "") or "").strip()
            if tool_and_target and tool_and_target != "starting":
                tool = tool_and_target.split(" ", 1)[0]
                target = tool_and_target.split(" ", 1)[1] if " " in tool_and_target else ""
                card.set_generating_tool(tool, target)
            else:
                card.clear_generating_tool()
            return

        if not self._live_footed:
            self._live_footed = True
            status = str(cur.get("status", "completed")).lower()
            if status == "failed" or cur.get("error"):
                card.append_text_block(str(cur.get("error") or summary or "Subagent failed."))
            elapsed = float(cur.get("elapsed", 0.0) or 0.0)
            tokens = int(cur.get("tokens", 0) or 0)
            card.finish(
                elapsed=elapsed if elapsed >= 0.05 else None,
                tokens=tokens,
                tok_s=(tokens / elapsed) if (tokens and elapsed >= 0.05) else 0.0,
            )

    def _refresh_live(self) -> None:
        """While the subagent runs, mirror its progress into a real card."""
        b = self.live_agent
        if b is None:
            return
        running = bool(getattr(b, "tool_running", False))
        if not running and self._refresh_timer:
            # It just finished: one last pass, then stop polling.
            self._refresh_timer.stop()
            self._refresh_timer = None
        try:
            self._advance_live(self._current(), running)
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
