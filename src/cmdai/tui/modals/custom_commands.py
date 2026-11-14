"""Modal for /custom: manage .cmdai/commands/*.md and .cmdai/hooks.json (styled like /ctx, /help, /sessions)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ...core.custom_extensions import (
    add_post_tool_hook,
    list_custom_commands,
    load_hooks,
    save_custom_command,
)


class CustomCommandsModal(ModalScreen):
    """Modal for creating/inspecting custom slash commands and PostToolUse hooks."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("1", "action_manual_cmd", "New command", show=False),
        Binding("2", "action_manual_hook", "New hook", show=False),
        Binding("3", "action_ai_gen", "Generate with AI", show=False),
    ]

    DEFAULT_CSS = """
    CustomCommandsModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }
    #custom-list {
        width: 100%;
        height: auto;
        max-height: 14;
        background: #0d1117;
        border: none;
        padding: 0;
        margin: 0 0 1 0;
    }
    #custom-form {
        width: 100%;
        height: auto;
        display: none;
        margin: 0 0 1 0;
    }
    #custom-form.visible {
        display: block;
    }
    #custom-form-hint {
        color: #8b949e;
        margin: 0 0 1 0;
    }
    #custom-body {
        width: 100%;
        height: 6;
        background: #161b22;
        border: none;
        color: #e6edf3;
        margin: 0 0 1 0;
    }
    #custom-form-actions {
        height: 1;
        width: 100%;
    }
    .custom-action-link {
        width: auto;
        height: 1;
        padding: 0 2 0 0;
        color: #58a6ff;
    }
    .custom-action-link:hover {
        text-style: bold underline;
    }
    #custom-status {
        height: 1;
        color: #8b949e;
    }
    """

    def __init__(self, workdir: str) -> None:
        super().__init__()
        self.workdir = workdir
        self._mode: str = ""  # "manual_cmd" | "manual_hook" | "ai_cmd"

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static("Custom Commands & Hooks", id="modal-title")
                yield Static("[dim]Esc[/]", id="modal-esc")
            yield OptionList(id="custom-list")
            with Vertical(id="custom-form"):
                yield Static("", id="custom-form-hint")
                yield Input(placeholder="Name (e.g. review) or matcher (e.g. edit|write)", id="custom-name-input")
                yield TextArea("", id="custom-body")
                with Horizontal(id="custom-form-actions"):
                    yield Static("[bold #7ee787]Save / Run[/]", id="act-submit", classes="custom-action-link")
                    yield Static("[dim]Cancel[/]", id="act-cancel", classes="custom-action-link")
            yield Static("[dim].cmdai/commands/*.md  •  .cmdai/hooks.json[/dim]", id="custom-status")
            with Horizontal(id="modal-footer"):
                yield Static("[dim]Enter select  •  1 command  •  2 hook  •  3 AI generate[/dim]", id="modal-footer-left")
                yield Static("[dim]Esc close[/dim]", id="modal-footer-right")

    def on_mount(self) -> None:
        self._refresh_list()
        try:
            ol = self.query_one("#custom-list", OptionList)
            if ol.option_count > 0:
                ol.highlighted = 0
            ol.focus()
        except Exception:
            pass

    def _refresh_list(self) -> None:
        ol = self.query_one("#custom-list", OptionList)
        ol.clear_options()
        ol.add_option(Option("+ Create manually: New command (.cmdai/commands/*.md)", id="__new_cmd__"))
        ol.add_option(Option("+ Create manually: New PostToolUse hook (.cmdai/hooks.json)", id="__new_hook__"))
        ol.add_option(Option("✦ Generate with AI model (describe what you need)", id="__ai_gen__"))
        cmds = list_custom_commands(self.workdir)
        hooks = load_hooks(self.workdir).get("PostToolUse", [])
        if cmds or hooks:
            ol.add_option(Option("────────────────────────────────────────────────────────", id="__sep__", disabled=True))
        for c in cmds:
            ol.add_option(Option(f"{c['name']}  —  {c['description']}", id=f"cmd:{c['slug']}"))
        for idx, h in enumerate(hooks):
            ol.add_option(Option(f"Hook [{h.get('matcher', '*')}]  →  {h.get('command', '')}", id=f"hook:{idx}"))

    def action_dismiss_modal(self) -> None:
        form = self.query_one("#custom-form", Vertical)
        if form.has_class("visible"):
            form.remove_class("visible")
            try:
                self.query_one("#custom-list", OptionList).focus()
            except Exception:
                pass
            return
        self.dismiss(None)

    def action_action_manual_cmd(self) -> None:
        self._open_form(
            "manual_cmd",
            "[bold #e6edf3]New command[/] [dim](.cmdai/commands/<name>.md)[/]",
            "Command name, e.g. review",
            "# Code review\nInspect repository changes for bugs and regressions: $ARGUMENTS",
        )

    def action_action_manual_hook(self) -> None:
        self._open_form(
            "manual_hook",
            "[bold #e6edf3]New PostToolUse hook[/] [dim](.cmdai/hooks.json)[/]",
            "Tool matcher, e.g. edit|write",
            "pytest -q",
        )

    def action_action_ai_gen(self) -> None:
        self._open_form(
            "ai_cmd",
            "[bold #e6edf3]Generate with AI model[/] [dim](describe a command or hook)[/]",
            "",
            "Write a /review command for security checks or a post-edit hook running pytest",
        )

    def _open_form(self, mode: str, hint: str, name_ph: str, body_text: str) -> None:
        self._mode = mode
        form = self.query_one("#custom-form", Vertical)
        form.add_class("visible")
        self.query_one("#custom-form-hint", Static).update(hint)
        name_inp = self.query_one("#custom-name-input", Input)
        name_inp.placeholder = name_ph
        name_inp.value = ""
        body_ta = self.query_one("#custom-body", TextArea)
        body_ta.text = body_text
        if mode == "ai_cmd":
            name_inp.display = False
            body_ta.focus()
        else:
            name_inp.display = True
            name_inp.focus()

    def _submit_form(self) -> None:
        name_val = self.query_one("#custom-name-input", Input).value.strip()
        body_val = self.query_one("#custom-body", TextArea).text.strip()
        if not body_val:
            self.query_one("#custom-status", Static).update("[#f85149]Enter content before saving.[/] ")
            return
        if self._mode == "manual_cmd":
            saved = save_custom_command(self.workdir, name_val or "custom", body_val)
            self.query_one("#custom-status", Static).update(f"[#7ee787]Saved command:[/] {saved}")
            self.query_one("#custom-form", Vertical).remove_class("visible")
            self._refresh_list()
            self.query_one("#custom-list", OptionList).focus()
        elif self._mode == "manual_hook":
            saved = add_post_tool_hook(self.workdir, name_val or "edit|write", body_val)
            self.query_one("#custom-status", Static).update(f"[#7ee787]Saved hook:[/] {saved}")
            self.query_one("#custom-form", Vertical).remove_class("visible")
            self._refresh_list()
            self.query_one("#custom-list", OptionList).focus()
        elif self._mode == "ai_cmd":
            kind = "hook" if "hook" in body_val.lower() else "command"
            self.dismiss({"action": "ai_generate", "kind": kind, "description": body_val})

    @on(OptionList.OptionSelected, "#custom-list")
    def _on_list_selected(self, event: OptionList.OptionSelected) -> None:
        oid = str(event.option.id or "")
        if oid == "__new_cmd__":
            self.action_action_manual_cmd()
        elif oid == "__new_hook__":
            self.action_action_manual_hook()
        elif oid == "__ai_gen__":
            self.action_action_ai_gen()
        elif oid.startswith("cmd:"):
            slug = oid.split(":", 1)[1]
            self.query_one("#custom-status", Static).update(
                f"[#7ee787]Command /{slug} ready[/] [dim]— type /{slug} in chat to run[/]"
            )
        elif oid.startswith("hook:"):
            self.query_one("#custom-status", Static).update(
                "[#7ee787]PostToolUse hook active[/] [dim]— runs automatically after matching tools[/]"
            )

    def on_click(self, event) -> None:
        wid = getattr(getattr(event, "widget", None), "id", "")
        if wid == "act-submit":
            self._submit_form()
        elif wid == "act-cancel":
            self.query_one("#custom-form", Vertical).remove_class("visible")
            try:
                self.query_one("#custom-list", OptionList).focus()
            except Exception:
                pass
        elif wid == "modal-esc":
            self.dismiss(None)
