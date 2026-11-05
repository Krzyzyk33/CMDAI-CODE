import os
from typing import Dict, Any, List, Optional
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..widgets import AssistantTurnCard, UserMessageCard

# The model-name spinner, so the task label animates the same way rather than
# inventing a second set of glyphs. It lives in app.py, which cannot be
# imported from here without a cycle, so the two values are repeated - and the
# test below fails if app.py's ever change.
META_MARKERS = ["⌬", "✻"]

SUBAGENT_CHAT_CSS = """
SubagentChatModal {
    width: 100%;
    height: 100%;
    min-width: 100%;
    min-height: 100%;
    align: left top;
    background: #000000;
    color: #c9d1d9;
    padding: 0;
    margin: 0;
    overflow: hidden hidden;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
    scrollbar-gutter: auto;
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
    overflow: hidden hidden;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}

#subagent-chat-scroll {
    width: 100%;
    height: 1fr;
    margin: 0;
    padding: 0 0 0 2;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
    scrollbar-gutter: auto;
}

.subagent-agent-box {
    width: 100%;
    height: auto;
    background: transparent;
    border: none;
    padding: 1 0 1 2;
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

.subagent-sibling-hint {
    color: #484f58;
}

/* Task dock: sits at the bottom, matches #bottom-dock layout in the chat. */
#subagent-task-dock {
    dock: bottom;
    height: auto;
    width: 100%;
    background: #000000;
    padding: 1 0 0 2;
    margin: 0;
}

/* Task card: looks like #input-card, expanding naturally with task height. */
#subagent-task-card {
    height: auto;
    max-height: 10;
    width: 100%;
    background: #161b22;
    border: none;
    padding: 1 3 1 3;
    margin: 0;
}

#subagent-task-header-row {
    width: 100%;
    height: 1;
    margin: 0 0 1 0;
    padding: 0;
}

#subagent-task-header {
    width: 1fr;
    color: #8b949e;
    text-style: bold;
}

#subagent-task-meta {
    width: auto;
    color: #8b949e;
    text-align: right;
    content-align: right middle;
    padding: 0;
    margin: 0;
}

#subagent-task-body {
    width: 100%;
    height: auto;
    min-height: 1;
    max-height: 8;
    overflow-y: auto;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
    padding: 0;
    margin: 0;
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
        Binding("ctrl+b", "send_to_background", "Tło", show=False),
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
        self._live_notes_done = 0  # progress notes already emitted
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
                "summary": str((getattr(b, "result_data", {}) or {}).get("summary", "") or ""),
            }
        return self.agents[self.agent_index - 1]

    def compose(self) -> ComposeResult:
        with Vertical(id="subagent-modal-dialog"):
            with VerticalScroll(id="subagent-chat-scroll"):
                # Real assistant card: tool blocks, text commentary, thinking, and formatted responses
                yield AssistantTurnCard("Subagent", has_thinking=True, classes="subagent-card", id="subagent-assistant-card")
            # The task dock at the bottom, matching #bottom-dock layout
            with Vertical(id="subagent-task-dock"):
                with Horizontal(id="subagent-jump-bar"):
                    yield Static("", id="subagent-jump-spacer")
                    yield Static("", id="subagent-jump-to-new")
                with Vertical(id="subagent-task-card"):
                    with Horizontal(id="subagent-task-header-row"):
                        yield Static("TASK", id="subagent-task-header")
                        yield Static("[dim]esc[/dim]", id="subagent-task-meta")
                    yield Static("", id="subagent-task-body")

    def on_mount(self) -> None:
        self._spin_idx = 0
        self._spin_timer = self.set_interval(0.35, self._advance_spinner)
        # After the first refresh the card is mounted, so its blocks are appended
        # in call order instead of queueing up unordered.
        self.call_after_refresh(self._render_agent)
        if self.live_agent is not None:
            self._refresh_timer = self.set_interval(0.35, self._refresh_live)

    def _advance_spinner(self) -> None:
        """Animate the TASK header label while the shown subagent is working."""
        cur = self._current()
        if not cur:
            return
        running = str(cur.get("status", "")).lower() in ("running", "starting", "working")
        if not running:
            return
        try:
            marker = META_MARKERS[self._spin_idx % len(META_MARKERS)]
            self._spin_idx += 1
            is_bg = bool(getattr(self.live_agent, "is_backgrounded", False))
            if not is_bg:
                try:
                    from ...agent.tasks import BackgroundTaskManager
                    is_bg = BackgroundTaskManager.get_instance().has_active_background()
                except Exception:
                    is_bg = False
            color = "#f59e0b" if is_bg else "#58a6ff"
            self.query_one("#subagent-task-header", Static).update(
                f"[b {color}]{marker}[/][b]  TASK[/]"
            )
        except Exception:
            pass

    # ---------------------------------------------------------------- build

    @staticmethod
    def _items(cur: Dict[str, Any]) -> List[Any]:
        """Flatten a subagent session into chronological order: prose commentary and
        tools interleaved, preserving ALL tools and ALL text generated by the subagent.
        """
        from ...agent.runner import _parse_only

        raw_events = list(cur.get("events") or [])
        events: List[Dict[str, Any]] = []
        has_typed_events = False
        for ev in raw_events:
            if not isinstance(ev, dict):
                continue
            if "sub_event" in ev:
                sev = ev.get("sub_event")
                if sev in ("tool_called", "tool_done"):
                    t_n = str(ev.get("tool") or "").strip()
                    t_t = str(ev.get("target") or "").strip()
                    t_o = ev.get("output", "")
                    events.append({
                        "tool": t_n,
                        "target": t_t,
                        "result": {"success": bool(ev.get("ok", True)), "content": t_o},
                        "ok": bool(ev.get("ok", True)),
                    })
                elif sev == "text_delta":
                    t_txt = str(ev.get("text") or "").strip()
                    if t_txt and not t_txt.startswith("{"):
                        has_typed_events = True
                        events.append({"type": "text", "content": t_txt})
                continue
            if ev.get("type") == "text":
                t_txt = str(ev.get("content", "") or "").strip()
                if t_txt and not t_txt.startswith("{") and not t_txt.startswith("sub_event"):
                    has_typed_events = True
                    events.append(ev)
                continue
            if ev.get("type") == "thinking":
                t_think = str(ev.get("content", "") or "").strip()
                if t_think:
                    has_typed_events = True
                    events.append(ev)
                continue
            t_name = str(ev.get("tool", "") or ev.get("name") or "").strip().lower()
            if not t_name or t_name in ("subagent", "spawn_subagent"):
                continue
            t_tgt = str(ev.get("target", "") or "").splitlines()[0].strip() if ev.get("target") else ""
            if t_name == "tool" and not t_tgt:
                continue
            events.append(ev)

        # If events contain interleaved typed events (from _sub_model_call / server-side stream),
        # respect that exact chronological order!
        if has_typed_events:
            items: List[Any] = []
            for ev in events:
                if ev.get("type") == "text":
                    t_str = str(ev.get("content", "") or "").strip()
                    if t_str:
                        items.append(("text", t_str))
                else:
                    items.append(("tool", ev))
            return items

        items: List[Any] = []
        history = cur.get("history") or []
        cursor = 0
        for i, msg in enumerate(history):
            if msg.get("role") != "assistant":
                continue
            content = str(msg.get("content", "") or "")
            parsed_segs = _parse_only(content)
            has_tool = any(kind == "tool" for kind, _ in parsed_segs)
            for kind, payload in parsed_segs:
                if kind == "text":
                    txt = str(payload or "").strip()
                    if txt:
                        items.append(("text", txt))
                    continue
                if kind == "tool":
                    if cursor < len(events):
                        items.append(("tool", events[cursor]))
                        cursor += 1
                    else:
                        t_name, t_attrs = payload if isinstance(payload, tuple) else (str(payload), {})
                        arg_sum = str(t_attrs.get("path") or t_attrs.get("query") or t_attrs.get("cmd") or t_attrs.get("pattern") or t_attrs.get("text") or t_attrs.get("note") or "")[:80]
                        items.append(("tool", {
                            "tool": t_name,
                            "target": arg_sum,
                            "args": dict(t_attrs or {}),
                            "ok": True,
                        }))

            if not has_tool and cursor < len(events):
                if i + 1 < len(history) and history[i + 1].get("role") == "user":
                    user_msg = str(history[i + 1].get("content", "") or "")
                    if "[tool:" in user_msg:
                        count_in_user = user_msg.count("[tool:")
                        for _ in range(count_in_user):
                            if cursor < len(events):
                                items.append(("tool", events[cursor]))
                                cursor += 1

        # Append any remaining executed tools that were run without XML tags
        for ev in events[cursor:]:
            items.append(("tool", ev))
        return items

    def _emit_items(self, card: Any, items: List[Any], start: int) -> None:
        """Append items[start:] to the card, mounting text blocks and tool blocks."""
        for kind, payload in items[start:]:
            if kind == "thinking":
                think_text = str(payload or "").strip()
                if think_text:
                    tb = card.start_thinking()
                    if tb is not None:
                        tb.thinking_text = think_text
                        tb.finish()
                continue
            if kind == "text":
                txt_clean = str(payload or "").strip()
                if not txt_clean:
                    continue
                card.append_text_block(txt_clean)
                continue
            ev = payload
            tool = str(ev.get("tool", "") or ev.get("name") or "").strip()
            raw_tgt = str(ev.get("target", "") or "")
            first_tgt = raw_tgt.splitlines()[0].strip() if raw_tgt else ""
            if not tool or (tool.lower() == "tool" and not first_tgt):
                continue
            if tool in ("subagent", "spawn_subagent"):
                continue

            res = ev.get("result")
            if not isinstance(res, dict):
                res = {"success": bool(ev.get("ok", True)), "content": str(res or "")}
            else:
                res = dict(res)
            if "\n" in raw_tgt and not res.get("content"):
                res["content"] = raw_tgt.split("\n", 1)[1].lstrip("\r\n")

            block = card.add_tool(tool, first_tgt)
            if block is not None:
                block.finish(res)

    def _build_card(self, cur: Dict[str, Any], live: bool = False) -> Any:
        """Replay a finished subagent session into a real AssistantTurnCard.

        Used for a finished subagent and whenever the shown subagent changes.
        A running one goes through _advance_live instead, which appends rather
        than rebuilding.
        """
        card = self.query_one("#subagent-assistant-card", AssistantTurnCard)
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

        # Mount thinking block if subagent has thinking
        think_val = str(cur.get("thinking") or getattr(self.live_agent, "thinking", "") or "").strip()
        is_thinking = "thinking" in str(getattr(self.live_agent, "subagent_live_tool", "")).lower() or bool(think_val)
        if is_thinking or think_val:
            tb = card.start_thinking()
            if tb is not None:
                if think_val:
                    tb.thinking_text = think_val
                tb.update_header(live=live)
                if not live:
                    tb.finish()

        # The full replay uses the same item list the live path appends from,
        # so a finished subagent and a running one read identically.
        self._live_done = 0
        self._live_notes_done = 0
        self._live_todo_open = False
        self._live_summary = ""
        self._live_footed = False
        self._emitted_keys = set()
        self._last_emitted_tool_sig = None
        items = self._items(cur)
        self._emit_items(card, items, 0)
        self._live_done = len(items)

        # Progress notes: only append note if it wasn't already emitted as an agent_note tool
        emitted_notes = set()
        for k, p in items:
            if k == "tool" and isinstance(p, dict):
                t_name = str(p.get("tool", "")).lower()
                if t_name == "agent_note":
                    note_val = str((p.get("args") or {}).get("text") or p.get("target") or "").strip()
                    if note_val:
                        emitted_notes.add(note_val)

        notes = [str(n or "").strip() for n in (cur.get("notes") or [])]
        notes = [n for n in notes if n and n not in emitted_notes]
        tool_kw = ("read", "edit", "write", "glob", "search", "shell", "command", "terminal", "bash", "ls", "scratch", "todo")
        for note in notes:
            words = note.split(None, 1)
            t_cand = words[0].lower() if words else ""
            if t_cand in tool_kw:
                t_arg = words[1].strip() if len(words) > 1 else ""
                tb = card.add_tool(t_cand, t_arg)
                if tb is not None:
                    tb.finish({"result": "completed"})
            else:
                card.append_text_block(note)
        self._live_notes_done = len(notes)

        # The report the lead agent reads, as plain closing text.
        summary = str(cur.get("summary", "")).strip()
        last_text_item = next((p.strip() for k, p in reversed(items) if k == "text" and isinstance(p, str)), "")
        if summary and summary != last_text_item:
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
            if tool_and_target and tool_and_target.lower() not in ("starting", "working", "thinking", "processing…", "processing..."):
                tool = tool_and_target.split(" ", 1)[0]
                target = tool_and_target.split(" ", 1)[1] if " " in tool_and_target else ""
                first_tgt = target.splitlines()[0].strip() if target else ""
                if tool.lower() in ("thinking", "processing") or getattr(self, "_last_emitted_tool_sig", None) == (tool.lower(), first_tgt):
                    card.clear_generating_tool()
                else:
                    card.set_generating_tool(tool, target)
            else:
                card.clear_generating_tool()
        else:
            card.finish(
                elapsed=elapsed if elapsed >= 0.05 else None,
                tokens=tokens,
                tok_s=(tokens / elapsed) if (tokens and elapsed >= 0.05) else 0.0,
            )
        try:
            if self.agent_total > 1:
                meta_lbl = f"[dim]Agent {self.agent_index} of {self.agent_total} · esc[/dim]"
            else:
                meta_lbl = "[dim]esc[/dim]"
            self.query_one("#subagent-task-meta", Static).update(meta_lbl)
        except Exception:
            pass
        task_b = str(cur.get("goal", "") or "").strip()
        if task_b.startswith("{") and any(k in task_b for k in ("sub_event", "goal", "task", "target")):
            try:
                import json as _json
                p = _json.loads(task_b)
                if isinstance(p, dict):
                    task_b = str(p.get("goal") or p.get("task") or p.get("target") or "").strip()
            except Exception:
                pass
        try:
            self.query_one("#subagent-task-body", Static).update(
                task_b or "No task description.")
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
        """The task the lead agent wrote, in the TASK dock."""
        task = str(cur.get("goal", "") or "").strip()
        if task.startswith("{") and any(k in task for k in ("sub_event", "goal", "task", "target")):
            try:
                import json as _json
                p = _json.loads(task)
                if isinstance(p, dict):
                    task = str(p.get("goal") or p.get("task") or p.get("target") or "").strip()
            except Exception:
                pass
        if not task or task == self._task_shown:
            return
        self._task_shown = task
        try:
            self.query_one("#subagent-task-body", Static).update(task)
        except Exception:
            pass

    def _advance_live(self, cur: Dict[str, Any], running: bool) -> None:
        """Append whatever the subagent produced since the last tick."""
        card = self.query_one("#subagent-assistant-card", AssistantTurnCard)
        card.model_name = str(cur.get("role", "Subagent"))
        self._show_task(cur)

        # Update live thinking
        live_think = str(getattr(self.live_agent, "thinking", "") or cur.get("thinking") or "").strip()
        is_thinking = "thinking" in str(getattr(self.live_agent, "subagent_live_tool", "")).lower() or bool(live_think)
        if running and is_thinking:
            tb = card.start_thinking()
            if tb is not None:
                if live_think:
                    tb.thinking_text = live_think
                tb.update_header(live=True)
        elif not running and card.thinking_block is not None and not card.thinking_block.is_finished:
            card.finish_thinking()

        items = self._items(cur)
        if len(items) > self._live_done:
            self._emit_items(card, items, self._live_done)
            self._live_done = len(items)

        # Update results on any currently running tool blocks as results arrive
        raw_events = list(cur.get("events") or [])
        tool_blocks = list(getattr(card, "tool_blocks", []) or [])
        for i, ev in enumerate(raw_events):
            if i < len(tool_blocks):
                tb = tool_blocks[i]
                if getattr(tb, "tool_running", False):
                    res = ev.get("result")
                    if res is not None:
                        tb.finish(res if isinstance(res, dict) else {"content": str(res)})

        notes = [str(n or "").strip() for n in (cur.get("notes") or [])]
        notes = [n for n in notes if n and not n.startswith("{") and not n.startswith("sub_event")]
        if len(notes) > self._live_notes_done:
            tool_kw = ("read", "edit", "write", "glob", "search", "shell", "command", "terminal", "bash", "ls", "scratch", "todo")
            for note in notes[self._live_notes_done:]:
                words = note.split(None, 1)
                t_cand = words[0].lower() if words else ""
                if t_cand in tool_kw:
                    t_arg = words[1].strip() if len(words) > 1 else ""
                    tb = card.add_tool(t_cand, t_arg)
                    if tb is not None:
                        tb.finish({"result": "completed"})
                else:
                    card.append_text_block(note)
            self._live_notes_done = len(notes)

        # The report, once, when there is one. A live run has none until the
        # subagent wraps up.
        summary = str(cur.get("summary", "") or "").strip()
        last_text_item = next((p.strip() for k, p in reversed(items) if k == "text" and isinstance(p, str)), "")
        if summary and summary != self._live_summary and summary != last_text_item:
            self._live_summary = summary
            card.append_text_block(summary)

        if running:
            # One animated line at the bottom, same as the lead agent. The
            # tools above it stay still. No "Thinking" line: between tools the
            # subagent has nothing to show, and an empty pulsing row there read
            # as a tool that did not work.
            tool_and_target = str(getattr(self.live_agent, "subagent_live_tool", "") or "").strip()
            if tool_and_target and tool_and_target.lower() not in ("starting", "working", "thinking", "processing…", "processing..."):
                tool = tool_and_target.split(" ", 1)[0]
                target = tool_and_target.split(" ", 1)[1] if " " in tool_and_target else ""
                first_tgt = target.splitlines()[0].strip() if target else ""
                # If this tool was already emitted and finished, do not show it as generating!
                last_block = card.tool_blocks[-1] if getattr(card, "tool_blocks", None) else None
                if last_block and not getattr(last_block, "tool_running", False):
                    last_name = (getattr(last_block, "tool_name", "") or "").lower()
                    last_tgt = str(getattr(last_block, "target", "") or "").strip()
                    first_norm = first_tgt.lower().replace("'", '"').strip()
                    last_norm = last_tgt.lower().replace("'", '"').strip()
                    if (last_name in (tool.lower(), "terminal", "shell", "bash", "command")
                            and (not first_tgt or first_norm in last_norm or last_norm in first_norm)):
                        card.clear_generating_tool()
                        return
                if tool.lower() in ("thinking", "processing") or getattr(self, "_last_emitted_tool_sig", None) == (tool.lower(), first_tgt):
                    card.clear_generating_tool()
                else:
                    card.set_generating_tool(tool, target)
            else:
                card.clear_generating_tool()
            return

        card.clear_generating_tool()
        if not self._live_footed:
            self._live_footed = True
            status = str(cur.get("status", "completed")).lower()
            summary = str(cur.get("summary", "")).strip()
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
            if self._is_scroll_at_bottom():
                try:
                    cs = self.query_one("#subagent-chat-scroll", VerticalScroll)
                    cs.scroll_end(animate=False)
                except Exception:
                    pass
                self._set_subagent_follow_hint(False)
            else:
                self._set_subagent_follow_hint(True)
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

    def action_send_to_background(self) -> None:
        if self.live_agent is not None:
            try:
                self.live_agent.mark_backgrounded()
            except Exception:
                pass
        try:
            self.app.action_send_to_background()
        except Exception:
            pass
        self._advance_spinner()

    def _is_scroll_at_bottom(self) -> bool:
        try:
            cs = self.query_one("#subagent-chat-scroll", VerticalScroll)
            return (cs.scroll_y + cs.size.height) >= (cs.max_scroll_y - 1)
        except Exception:
            return True

    def _set_subagent_follow_hint(self, visible: bool) -> None:
        try:
            bar = self.query_one("#subagent-jump-bar")
            bar.styles.display = "block" if visible else "none"
            self.query_one("#subagent-jump-to-new", Static).update(
                "[b #58a6ff]more below[/] [b #58a6ff]↓[/]" if visible else ""
            )
        except Exception:
            pass

    @on(events.Click, "#subagent-jump-to-new")
    def on_subagent_jump_click(self) -> None:
        try:
            cs = self.query_one("#subagent-chat-scroll", VerticalScroll)
            cs.scroll_end(animate=False)
            self._set_subagent_follow_hint(False)
        except Exception:
            pass

    @on(events.MouseScrollUp, "#subagent-chat-scroll")
    def on_subagent_scroll_up(self) -> None:
        self.call_after_refresh(lambda: self._set_subagent_follow_hint(not self._is_scroll_at_bottom()))

    @on(events.MouseScrollDown, "#subagent-chat-scroll")
    def on_subagent_scroll_down(self) -> None:
        self.call_after_refresh(lambda: self._set_subagent_follow_hint(not self._is_scroll_at_bottom()))

    def action_dismiss_modal(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.stop()
            self._refresh_timer = None
        self.dismiss()
