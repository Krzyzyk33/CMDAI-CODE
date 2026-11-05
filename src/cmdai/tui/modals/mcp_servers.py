"""MCP servers window.

`/mcp` is listed among the modal commands but used to print a line of text,
so it was the only entry in that list that did not open a window.

Two views, one list widget. The list view is a short, scannable table of
servers. Enter opens the options view for the highlighted one, where
everything about that server can be changed. Escape goes back.

Why the split: the first version put the whole server in the row and then
repeated it in a pane underneath. The row had to hold a Windows error
message, so it overflowed and got truncated mid-word, and the pane showed
the same name and status a second time. Neither view had room to say
anything useful. Now the row says only what is true at a glance, the line
at the bottom carries the one piece of text worth reading, and the details
live behind Enter where they do not compete with the list.

The window is in English. It sits among English modal commands, its own
status words are English, and a footer half in Polish made it look like two
programs sharing a border.

Everything a user can change about a server is a switch or an action on the
options view. A setting that exists in mcp_config.json but cannot be reached
from the window is a setting the window does not really have.
"""

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

# (glyph, colour) per status. The glyphs are the vocabulary:
# filled = live, ring = configured but not running, cross = failed.
STATUS: Dict[str, Tuple[str, str]] = {
    "connected": ("●", "#3fb950"),      # filled circle
    "connecting": ("◐", "#d29922"),     # half circle
    "error": ("✕", "#f85149"),          # cross
    "disabled": ("○", "#6e7681"),       # empty ring
    "disconnected": ("○", "#8b949e"),   # empty ring
}
DEFAULT_STATUS = ("○", "#8b949e")

MAX_ERROR = 90
BOTTOM_MAX = 220


def status_of(server, enabled: bool = True) -> Tuple[str, str]:
    if not enabled:
        return STATUS["disabled"]
    return STATUS.get(getattr(server, "status", ""), DEFAULT_STATUS)


def one_line(text: str, limit: int = MAX_ERROR) -> str:
    """First line, collapsed, bounded.

    A connection error is a Python traceback with newlines in it. Dropped
    into a list row it wrapped across four lines and blew the layout apart.
    """
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def permission_counts(server) -> Dict[str, int]:
    from ...mcp.client import PERM_DESTRUCTIVE, PERM_READ, PERM_WRITE, classify_tool
    counts: Dict[str, int] = {PERM_READ: 0, PERM_WRITE: 0, PERM_DESTRUCTIVE: 0}
    for tool in server.tools:
        kind = classify_tool(tool)
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def explain(error: str) -> str:
    """A plain-English reading of the common connection failures.

    Windows reports a missing executable as
    "[WinError 2] Nie można odnaleźć określonego pliku", which says nothing
    about what to do about it. The original text is kept after the dash:
    discarding it would lose whatever detail is actually in there, and a
    failed server is exactly when the detail matters.
    """
    raw = str(error or "")
    low = raw.lower()
    if "winerror 2" in low or "no such file or directory" in low:
        return ("command not found - check the command and that it is on PATH "
                "- " + one_line(raw, BOTTOM_MAX))
    if "connection refused" in low or "err_connection_refused" in low:
        return ("nothing is listening on that address - is it running? "
                "- " + one_line(raw, BOTTOM_MAX))
    if "timed out" in low or "timeout" in low:
        return ("no answer in time - it may still be starting up - "
                + one_line(raw, BOTTOM_MAX))
    return one_line(raw, BOTTOM_MAX)


class MCPServerModal(ModalScreen[None]):
    """Configured MCP servers, and everything you can do to one of them."""

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        # No "enter" binding on purpose. OptionList turns Enter into
        # OptionSelected, which _on_select handles. Binding enter as well
        # gave the same key two paths to the same action, and an action
        # that can run twice is worse than one that runs once.
        Binding("up", "cursor_up", "", show=False),
        Binding("down", "cursor_down", "", show=False),
        Binding("k", "cursor_up", "", show=False),
        Binding("j", "cursor_down", "", show=False),
        Binding("e", "toggle_enabled", "Enable", show=False),
        Binding("r", "restart", "Restart", show=False),
        Binding("l", "logs", "Logs", show=False),
        Binding("n", "add", "Add", show=False),
        Binding("a", "toggle_hidden", "Hidden", show=False),
    ]

    def __init__(self, client: Any, config_path: str = "", **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.config_path = config_path
        # Everything the user configured is shown, including what is off.
        # Hiding the disabled ones made this window a dead end: a server
        # switched off could not be seen, selected, or switched back on.
        self.show_disabled = True
        # "list" or "options": which of the two the single list widget is
        # showing, and which server the options belong to.
        self.view = "list"
        self.subject: Optional[str] = None
        # Read `enabled` off the server, not off a fresh dict: a server
        # configured with "enabled": false came back as enabled here, so it
        # showed up in the list despite being off.
        self._enabled = {
            name: bool(getattr(server, "enabled", True))
            for name, server in client.servers.items()
        }

    # ------------------------------------------------------------- rows

    def is_disabled(self, name: str) -> bool:
        """Whether this window currently believes the server is switched off.

        Reads the window's own state, not the server's, because the two can
        disagree for the moment after a failed save - and the window is what
        the user is looking at.
        """
        return not self._enabled.get(name, True)

    def _visible(self) -> List[str]:
        names = list(self.client.server_names())
        if self.show_disabled:
            return sorted(names)
        return sorted(n for n in names if self._enabled.get(n, True))

    def _rows(self) -> List[Option]:
        """The server list.

        One line, and only what is true at a glance. The error message used
        to go here too, and Windows produces them in the language of the OS,
        so the row either wrapped or was truncated in the middle of a word
        with a red wall of text over the selected row's highlight.
        """
        options: List[Option] = []
        for name in self._visible():
            server = self.client.servers.get(name)
            if server is None:
                continue
            enabled = self._enabled.get(name, True)
            glyph, colour = status_of(server, enabled)

            line = Text()
            line.append(glyph + " ", style=colour)
            line.append(f"{name:<22}", style="bold #c9d1d9" if enabled else "#6e7681")

            if not enabled:
                line.append("off", style="#6e7681")
            elif server.status == "connected":
                line.append("connected", style=colour)
                if server.tools:
                    counts = permission_counts(server)
                    line.append(f"  {len(server.tools)} tools", style="#8b949e")
                    parts = []
                    for key in ("read", "write", "destructive"):
                        if counts[key]:
                            parts.append(f"{counts[key]} {key}")
                    if parts:
                        line.append("  (" + ", ".join(parts) + ")", style="#7ee787")
                else:
                    line.append("  no tools", style="#6e7681")
            elif server.status == "error":
                # Just the word. The reason is one line at the bottom.
                line.append("failed", style=colour)
            elif server.status == "connecting":
                line.append("connecting", style=colour)
            else:
                line.append("not running", style="#6e7681")
            options.append(Option(line, id=name))
        return options

    # -------------------------------------------------------- option rows

    def _option_rows(self, name: str) -> List[Option]:
        """Everything you can change about one server, as a navigable list.

        A switch shows its value on the right, so the row itself already
        answers "is this on?" without being activated.
        """
        server = self.client.servers.get(name)
        if server is None:
            return []
        enabled = self._enabled.get(name, True)
        can_write = self.client.add_server is not None

        def toggle(icon: str, label: str, value: bool, colour: str,
                   act: str, note: str = "") -> Option:
            line = Text()
            line.append(icon + " ", style="#8b949e")
            line.append(f"{label:<20}", style="#c9d1d9")
            line.append("on" if value else "off",
                        style=colour if value else "#6e7681")
            if note:
                line.append("  " + note, style="#6e7681")
            return Option(line, id=act)

        return [
            toggle("●", "Enabled", enabled,
                   "#3fb950" if enabled else "#6e7681", "enabled",
                   "" if enabled else "no connection, no tools"),
            toggle("◆", "Start at launch", bool(getattr(server, "autostart", True)),
                   "#3fb950", "autostart"),
            Option(Text("↻  Restart", style="#c9d1d9"), id="restart"),
            Option(Text("▤  Logs", style="#c9d1d9"), id="logs"),
            Option(Text("✎  Rename", style="#c9d1d9" if can_write else "#6e7681"),
                   id="rename"),
            Option(Text("✕  Remove", style="#f85149" if can_write else "#6e7681"),
                   id="remove"),
        ]

    # --------------------------------------------------------- bottom line

    def _status_line(self) -> Tuple[str, str]:
        """The one line worth reading, at the bottom of the window.

        In the list view this is the highlighted server's error, which is
        what the user moved onto and what they need to read. In the options
        view it is the full picture of that server. Either way it is at the
        bottom, and it is the only place in the window where prose appears.
        """
        name = self.subject if self.view == "options" else self._selected()
        if not name:
            return "", "#6e7681"
        server = self.client.servers.get(name)
        if server is None:
            return "", "#6e7681"
        if self.is_disabled(name):
            return ("off - no connection and no tools. This is a setting, "
                    "not a failure. Enter to change it.", "#8b949e")
        if server.error:
            return explain(server.error), "#f85149"
        if self.view == "list" and server.status == "connected" and server.tools:
            counts = permission_counts(server)
            return (f"{len(server.tools)} tools: {counts['read']} read, "
                    f"{counts['write']} write, {counts['destructive']} destructive. "
                    f"Enter to open this server.", "#8b949e")
        target = server.url or " ".join([server.command] + list(server.args))
        return f"{server.transport} - {one_line(target, 70)}", "#8b949e"

    def _detail(self, name: str) -> Text:
        """Full picture of one server, for the bottom line and the tests."""
        server = self.client.servers.get(name)
        body = Text()
        if server is None:
            return body
        enabled = self._enabled.get(name, True)
        glyph, colour = status_of(server, enabled)

        info = server.server_info or {}
        if info:
            label = " ".join(
                str(info.get(k, "")) for k in ("name", "version")).strip()
            if label:
                body.append(label + "\n", style="#6e7681")
        if server.url:
            body.append("url  ", style="#6e7681")
            body.append(server.url + "\n", style="#c9d1d9")
        if server.command:
            body.append("cmd  ", style="#6e7681")
            body.append(" ".join([server.command] + list(server.args)) + "\n",
                        style="#c9d1d9")
        if server.env:
            body.append("env  ", style="#6e7681")
            body.append(", ".join(sorted(server.env)), style="#7ee787")

        if not enabled:
            body.append("\noff\n", style="bold #6e7681")
            body.append("No connection and no tools. A setting, not a failure. "
                        "Toggle Enabled above.\n", style="#8b949e")
        elif server.error:
            body.append("\nerror\n", style="bold #6e7681")
            body.append(one_line(server.error, 400) + "\n", style="#f85149")

        if server.status == "connected" and server.tools:
            body.append("\ntools\n", style="bold #6e7681")
            from ...mcp.client import classify_tool
            colour_of = {"read": "#7ee787", "write": "#d29922",
                         "destructive": "#f85149"}
            for tool in server.tools:
                raw = str(tool.get("name") or "")
                if not raw:
                    continue
                kind = classify_tool(tool)
                body.append(f"  {raw:<26}", style="#c9d1d9")
                body.append(kind + "\n", style=colour_of.get(kind, "#8b949e"))
        return body

    # ------------------------------------------------------------- footer

    def _footer(self) -> str:
        if self.client.load_error:
            return "[warn]config unreadable:[/] " + one_line(self.client.load_error, 80)
        if self.view == "options":
            return "[dim]↑↓[/] move  [dim]enter[/] change  [dim]esc[/] back to list"
        if not self.client.server_names():
            return "[dim]n[/] add the first server  [dim]esc[/] close"
        bits = ["[dim]↑↓[/] move  [dim]enter[/] open  [dim]esc[/] close"]
        hidden = len(self.client.server_names()) - len(self._visible())
        if hidden:
            bits.append(f"[dim]a[/] show {hidden} hidden")
        else:
            bits.append("[dim]a[/] hide off")
        return "  ".join(bits)

    def _title(self) -> str:
        if self.view == "options" and self.subject:
            server = self.client.servers.get(self.subject)
            if server is None:
                return f"[bold white]{self.subject}[/]"
            glyph, colour = status_of(server, self._enabled.get(self.subject, True))
            # The name, and nothing else. The command, the url, the version
            # and the error are in the bottom line where there is room.
            return f"[bold white]{self.subject}[/]  [{colour}]{glyph}[/]"
        return "[bold white]MCP servers[/]"

    # ----------------------------------------------------------- compose

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="mcp-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(self._title(), id="modal-title")
                yield Static("[dim]esc back[/]" if self.view == "options"
                             else "[dim]esc[/]", id="modal-esc")
            rows = self._rows()
            if rows:
                yield OptionList(*rows, id="modal-list")
            else:
                yield Static("[dim]no servers configured[/]", id="mcp-empty")
            yield Static("", id="mcp-status")
            yield Static(self._footer(), id="mcp-footer")

    def on_mount(self) -> None:
        if self._rows():
            try:
                self.query_one("#modal-list", OptionList).focus()
                self._refresh_status()
            except Exception:
                pass

    # ----------------------------------------------------------- helpers

    def _listing(self) -> Optional[OptionList]:
        try:
            return self.query_one("#modal-list", OptionList)
        except Exception:
            return None

    def _selected(self) -> Optional[str]:
        """The highlighted row's id.

        Falls back to the first row when nothing is highlighted. OptionList
        starts with highlighted=None until it has been navigated, and every
        action then did nothing at all on a freshly opened window - which is
        what "enter does not work" looks like from the outside.
        """
        listing = self._listing()
        if listing is None or not listing.option_count:
            return None
        index = listing.highlighted
        if index is None:
            index = 0
            listing.highlighted = 0
        try:
            return listing.get_option_at_index(index).id
        except Exception:
            return None

    def _refresh_status(self) -> None:
        try:
            text, colour = self._status_line()
            self.query_one("#mcp-status", Static).update(
                Text(text, style=colour) if text else Text(""))
        except Exception:
            pass
        try:
            self.query_one("#mcp-footer", Static).update(self._footer())
        except Exception:
            pass
        try:
            # The header names which server the options belong to. Built
            # once in compose() it stayed "MCP servers" after Enter, so the
            # options list had nothing saying which server it was for.
            self.query_one("#modal-title", Static).update(self._title())
            self.query_one("#modal-esc", Static).update(
                "[dim]esc back[/]" if self.view == "options" else "[dim]esc[/]")
        except Exception:
            pass

    def _redraw(self) -> None:
        """Rebuild whichever view is showing, keeping the highlight."""
        listing = self._listing()
        if listing is None:
            return
        keep = self._selected()
        rows = self._option_rows(self.subject) if self.view == "options" else self._rows()
        if self.view == "options" and not rows:
            self.view = "list"
            self.subject = None
            rows = self._rows()
        listing.clear_options()
        for row in rows:
            listing.add_option(row)
        if rows:
            ids = [r.id for r in rows]
            listing.highlighted = ids.index(keep) if keep in ids else 0
        self._refresh_status()

    # -------------------------------------------------------------- views

    def action_open(self) -> None:
        """Enter: list -> options, options -> run whatever is highlighted."""
        if self.view == "options":
            self._run(self._selected() or "")
            return
        name = self._selected()
        if not name:
            return
        self.view = "options"
        self.subject = name
        self._redraw()
        self._push_detail()

    def action_back(self) -> None:
        """Escape: options -> list, list -> close."""
        if self.view == "options":
            self.view = "list"
            self.subject = None
            self._redraw()
            return
        self.dismiss(None)

    def _push_detail(self) -> None:
        """Show the server's full picture at the bottom, in options view."""
        try:
            detail = self._detail(self.subject or "")
        except Exception:
            return
        try:
            self.query_one("#mcp-status", Static).update(detail)
        except Exception:
            pass

    # ------------------------------------------------------- navigation

    def _move(self, delta: int) -> None:
        listing = self._listing()
        if listing is None or not listing.option_count:
            return
        # Wraps at both ends. A list that stops at the edge reads as broken
        # on the first press, and this window is short enough that the user
        # hits the edge constantly.
        current = listing.highlighted if listing.highlighted is not None else 0
        listing.highlighted = (current + delta) % listing.option_count
        # Refreshed here rather than only from OptionHighlighted. Setting
        # .highlighted does not always post that message, and the bottom
        # line describing the wrong server while the cursor sits on another
        # is worse than not having one.
        if self.view == "list":
            self._refresh_status()

    def action_cursor_down(self) -> None:
        self._move(1)

    def action_cursor_up(self) -> None:
        self._move(-1)

    # ----------------------------------------------------------- actions

    def _run(self, action: str) -> None:
        """Apply whatever the highlighted option row is."""
        handlers: Dict[str, Callable[[], None]] = {
            "enabled": self.action_toggle_enabled,
            "autostart": self.action_toggle_autostart,
            "restart": self.action_restart,
            "logs": self.action_logs,
            "rename": self.action_rename,
            "remove": self.action_remove,
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()

    def _target(self) -> Optional[str]:
        """The server an action applies to.

        In the options view that is the one the window was opened for, not
        whatever the cursor happens to be on, so a key pressed after the
        list scrolled cannot act on the wrong server.
        """
        if self.view == "options":
            return self.subject
        return self._selected()

    def _set(self, key: str, value: bool) -> bool:
        name = self._target()
        if not name:
            return False
        setter = getattr(self.client, "set_option", None)
        if setter is None:
            self.notify("This client is read-only - use cmdai mcp to change settings")
            return False
        if not setter(name, key, value):
            self.notify(f"Could not save {key} for {name} - "
                        f"the config was not written")
            return False
        if key == "enabled":
            self._enabled[name] = value
        return True

    def action_toggle_enabled(self) -> None:
        name = self._target()
        if not name:
            return
        turning_on = self.is_disabled(name)
        if not self._set("enabled", turning_on):
            return
        if turning_on:
            # Enabling has to connect, or "on" is a label on something that
            # stays dark until the next launch, and the user has no way to
            # tell those apart. On a thread: spawning can block for as long
            # as the process takes, and a frozen window looks like a hung app.
            threading.Thread(
                target=self.client.restart, args=(name,),
                daemon=True, name=f"mcp-enable-{name}",
            ).start()
            self.notify(f"{name} on - connecting")
        else:
            self.notify(f"{name} off")
        self._redraw()

    def action_toggle_autostart(self) -> None:
        name = self._target()
        if not name:
            return
        getter = getattr(self.client, "get_option", None)
        current = bool(getter(name, "autostart", True)) if getter else True
        if not self._set("autostart", not current):
            return
        self.notify(f"{name}: start at launch {'off' if current else 'on'}")
        self._redraw()

    def action_restart(self) -> None:
        name = self._target()
        if not name:
            return
        # Restarting a server that is off is a request to do the thing the
        # user just said not to do.
        if self.is_disabled(name):
            self.notify(f"{name} is off - turn Enabled on first")
            return
        threading.Thread(
            target=self.client.restart, args=(name,),
            daemon=True, name=f"mcp-restart-{name}",
        ).start()
        self.notify(f"{name}: restarting")
        self._redraw()

    def action_logs(self) -> None:
        name = self._target()
        if not name:
            return
        server = self.client.servers.get(name)
        lines = (server.stderr_log[-25:] if server else []) or ["(stderr empty)"]
        try:
            detail = self._detail(name)
            detail.append("\nlog\n", style="bold #6e7681")
            for line in lines:
                detail.append(one_line(line, 400) + "\n", style="#8b949e")
            self.query_one("#mcp-status", Static).update(detail)
        except Exception:
            pass

    def action_rename(self) -> None:
        name = self._target()
        if not name:
            return
        if getattr(self.client, "rename_server", None) is None:
            self.notify("This client is read-only - rename with cmdai mcp")
            return
        try:
            self.app.push_screen(_PromptScreen("Rename server", name),
                                 lambda value: self._do_rename(name, value))
        except Exception:
            self.notify("Could not open the rename prompt")

    def _do_rename(self, name: str, value: Optional[str]) -> None:
        if not value:
            return
        ok, result = self.client.rename_server(name, value)
        if not ok:
            self.notify(f"Could not rename {name} - "
                        f"{result or 'a server of that name already exists'}")
            return
        if name in self._enabled:
            self._enabled[result] = self._enabled.pop(name)
        if self.subject == name:
            self.subject = result
        self.notify(f"renamed to {result}")
        self._redraw()

    def action_remove(self) -> None:
        name = self._target()
        if not name:
            return
        if getattr(self.client, "remove_server", None) is None:
            self.notify("This client is read-only - remove with cmdai mcp")
            return
        # Textual has no confirm(). Pushing a screen rather than blocking
        # keeps the window responsive, which a modal.raise_error or a
        # blocking wait would not.
        try:
            self.app.push_screen(
                _ConfirmScreen(
                    "Remove server",
                    f"Remove '{name}' from mcp_config.json?\n\n"
                    f"There is no undo. The file is edited in place.",
                    "Remove",
                ),
                lambda ok: self._do_remove(name, bool(ok)))
        except Exception:
            self.notify("Could not ask for confirmation - nothing was removed")

    def _do_remove(self, name: str, confirmed: bool) -> None:
        if not confirmed:
            self.notify("not removed")
            return
        if not self.client.remove_server(name):
            self.notify(f"Could not remove {name} - the config was not written")
            return
        self._enabled.pop(name, None)
        if self.subject == name:
            self.view = "list"
            self.subject = None
        self.notify(f"{name} removed")
        self._redraw()

    def action_toggle_hidden(self) -> None:
        self.show_disabled = not self.show_disabled
        self._redraw()

    def action_add(self) -> None:
        if self.client.add_server is None:
            self.notify("Adding needs a restart. Use: cmdai mcp add …")
            return
        self.app.push_screen(MCPAddModal(self.client), self._on_added)

    def _on_added(self, result: Optional[str]) -> None:
        if not result:
            return
        self._enabled[result] = True
        self.notify(f"added {result}")
        self._redraw()

    @on(OptionList.OptionHighlighted)
    def _on_highlight(self, _event) -> None:
        # In the options view the bottom line holds the server's picture, and
        # moving through options must not wipe it.
        if self.view == "options":
            return
        self._refresh_status()

    @on(OptionList.OptionSelected)
    def _on_select(self, _event) -> None:
        self.action_open()


class MCPAddModal(ModalScreen[Optional[str]]):
    """Add one server. Returns the new name, or None if cancelled.

    Polish, because this is a form someone fills in once, sitting next to
    the server list it belongs to, and the rest of the modal commands are
    English labels rather than sentences. A form this size does not need a
    second language.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Anuluj", priority=True),
        Binding("ctrl+s", "save", "Zapisz", show=True),
    ]

    def __init__(self, client: Any, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self._type = "stdio"

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="mcp-add-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("[bold white]Dodaj serwer MCP[/]", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            with Vertical(id="mcp-add-form"):
                yield Static("[dim]nazwa[/]", classes="mcp-add-label")
                yield Input(placeholder="xph-docs", id="f-name")
                yield Static("[dim]typ[/]", classes="mcp-add-label")
                # Click to switch between the two transports. Two fields for
                # one choice, because a stdio server needs a command and an
                # HTTP one needs an address, and asking for both and then
                # ignoring one is worse than showing one at a time.
                yield Static("stdio  ·  HTTP", id="f-type")
                yield Static("[dim]polecenie  (stdio)[/]", classes="mcp-add-label")
                yield Input(placeholder="python -m moj_serwer", id="f-cmd")
                yield Static("[dim]albo  adres  (http)[/]", classes="mcp-add-label")
                yield Input(placeholder="http://127.0.0.1:8765/mcp", id="f-url")
                yield Static("[dim]zmienne środowiskowe[/]", classes="mcp-add-label")
                yield Input(placeholder="TOKEN=${GITHUB_TOKEN}", id="f-env")
            yield Static("[dim]ctrl+s zapisz[/]", id="mcp-add-actions")
            yield Static("", id="mcp-add-hint")
            yield Static("", id="mcp-add-error")

    def on_mount(self) -> None:
        try:
            self.query_one("#f-type", Static).on_click = self._toggle_type
            self.query_one("#f-cmd", Input).disabled = False
            self.query_one("#f-url", Input).disabled = True
            self.query_one("#f-name", Input).focus()
        except Exception:
            pass

    def _toggle_type(self) -> None:
        self._type = "http" if self._type == "stdio" else "stdio"
        try:
            self.query_one("#f-type", Static).update(f"[b]{self._type.upper()}[/]")
            # Disable the field this transport does not use, rather than
            # validating it later: a greyed field cannot be filled in by
            # accident and cannot then hold a stale value.
            self.query_one("#f-cmd", Input).disabled = self._type != "stdio"
            self.query_one("#f-url", Input).disabled = self._type != "http"
        except Exception:
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        from ...mcp.client import sanitize_name
        raw = self.query_one("#f-name", Input).value.strip()
        if not raw:
            self._fail("nazwa jest wymagana")
            return
        # sanitize_name falls back to "server" when nothing survives, so
        # that is the signal that the name was punctuation only.
        if sanitize_name(raw) == "server":
            self._fail(f"'{raw}' nie nadaje się na nazwę")
            return

        entry: Dict[str, Any] = {}
        if self._type == "http":
            url = self.query_one("#f-url", Input).value.strip()
            if not url:
                self._fail("adres jest wymagany dla HTTP")
                return
            entry["type"] = "http"
            entry["url"] = url
        else:
            command = self.query_one("#f-cmd", Input).value.strip()
            if not command:
                self._fail("polecenie jest wymagane dla stdio")
                return
            head, _, tail = command.partition(" ")
            entry["command"] = head
            entry["args"] = tail.split() if tail else []

        env_text = self.query_one("#f-env", Input).value.strip()
        if env_text:
            env: Dict[str, str] = {}
            for pair in env_text.split(","):
                key, sep, value = pair.partition("=")
                if sep:
                    env[key.strip()] = value.strip()
            entry["env"] = env

        name = sanitize_name(raw)
        add = self.client.add_server
        if add is None or not add(name, entry):
            self._fail("nie udało się zapisać - sprawdź uprawnienia do pliku")
            return
        self.dismiss(name)

    def _fail(self, message: str) -> None:
        try:
            self.query_one("#mcp-add-error", Static).update(f"[#f85149]{message}[/]")
        except Exception:
            pass


class _ConfirmScreen(ModalScreen[bool]):
    """Yes or no. Returns True only on an explicit yes.

    Enter does not confirm. Everywhere else in this window Enter means "do
    the thing on the highlighted row", so carrying that habit over to a
    dialog that deletes a config file means a reflex destroys it. The
    confirm key is spelled out in the hint line instead.
    """

    BINDINGS = [
        Binding("escape", "no", "No", priority=True),
        Binding("left", "no", "No", show=False),
        Binding("right", "yes", "Yes", show=False),
        Binding("y", "yes", "Yes", show=False),
        Binding("n", "no", "No", show=False),
    ]

    def __init__(self, title: str, message: str, confirm_label: str = "Remove",
                 **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._message = message
        self._label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="mcp-confirm-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"[bold white]{self._title}[/]", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            with Vertical(id="mcp-confirm-body"):
                yield Static(self._message, id="mcp-confirm-text")
                yield Static(
                    f"[b]{self._label}[/]  ← y or →        [dim]cancel[/]  ← esc",
                    id="mcp-confirm-hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class _PromptScreen(ModalScreen[Optional[str]]):
    """One-line text prompt. Returns the text, or None if cancelled."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("enter", "submit", "OK", priority=True),
    ]

    def __init__(self, title: str, value: str = "", **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog", classes="mcp-add-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(f"[bold white]{self._title}[/]", id="modal-title")
                yield Static("[dim]esc[/]", id="modal-esc")
            yield Input(value=self._value, id="mcp-rename-input")

    def on_mount(self) -> None:
        try:
            self.query_one("#mcp-rename-input", Input).focus()
        except Exception:
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        try:
            value = self.query_one("#mcp-rename-input", Input).value.strip()
            self.dismiss(value or None)
        except Exception:
            self.dismiss(None)