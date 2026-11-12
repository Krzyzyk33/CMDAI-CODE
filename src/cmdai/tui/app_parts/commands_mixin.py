import datetime
import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static, TextArea

from ...agent.runner import AgentRunner
from ...agent.tools import AgentContext
from ...core.branding import (
    CMDAI_PARTS as _CMDAI_PARTS,
    CODE_PARTS as _CODE_PARTS,
    LOGO_HERO as _LOGO_HERO,
    get_hero_logo as _get_hero_logo,
)
from ...core.capabilities import (
    ModelCapability,
    clean_model_name,
    cycle_order,
    forget_uncached_capabilities,
    get_model_capability,
    normalize_level,
)
from ...core.engine import SimpleGGUFLoader
from ...core.providers import PROVIDERS_CATALOG, stream_chat_completion
from ...core.resource_limits import (
    build_summarization_payload,
    detect_conversation_language,
    estimate_model_memory,
    estimate_token_count,
    format_compacted_handoff,
    get_dynamic_context_limit,
    is_summarization_error_text,
    should_summarize,
)
from ...core.server import BackgroundHTTPServer
from ...core.sessions import SessionManager, transcript_from_messages
from ...core.settings import get_settings
from ...core.notifier import send_desktop_notification
from ...core.exporter import export_session_to_html
from ...core.releases import is_newer as _is_newer
from ...core.providers import TOOL_COMPLETE_PATTERNS, is_stream_tool_or_turn_complete
from ..modals.actions import ActionMenuModal
from ..modals.approval import EditInspectModal
from ..modals.ask import AskModal
from ..modals.commit import CommitModal
from ..modals.context import ContextInspectorModal
from ..modals.diff import DiffModal
from ..modals.files import FilePickerModal
from ..modals.plan import PlanModal
from ..modals.help import COMMANDS_DOC, HelpModal, InfoModal
from ..modals.models import ModelSelectModal
from ..modals.thinking import ThinkingModal
from ..modals.params import ParamsModal
from ..modals.prompts import SystemPromptModal
from ..modals.sessions import SessionsModal
from ..modals.settings import LoaderModal, SettingsModal
from ..modals.stats import StatsModal
from ..modals.releases import ReleasesModal, UpdateNoticeModal
from ..styles import SWIFT_CSS
from ..widgets import (
    ApprovalBar,
    ApprovalDiffPreview,
    AssistantTurnCard,
    AutoExpandingInput,
    ClickableMessage,
    HintRow,
    ThinkingBlock,
    TodoPreviewBar,
    ToolBlock,
    UserMessageCard,
    SessionTabsBar,
    TimelineRail,
    TimelineDot,
    copy_text_to_clipboard,
)
from ...mcp.client import sanitize_name

# Logo lives in core/branding.py so the CLI can print it without Textual.
CMDAI_PARTS = _CMDAI_PARTS
CODE_PARTS = _CODE_PARTS
get_hero_logo = _get_hero_logo
LOGO_HERO = _LOGO_HERO



class CommandsMixin:
    """Mixin for CMDAICodeTUI."""

    def _cmd_background_tasks(self, arg: str = "") -> None:
        from ..modals.background_tasks import BackgroundTasksModal
        self.push_screen(BackgroundTasksModal())


    def _handle_submit(self, prompt: str, from_hero: bool = False) -> None:
        prompt = prompt.strip()
        if not prompt:
            return

        # `@path` is the explicit way to put a file in context. Ordinary
        # prose that happens to look like a filename is left alone.
        self._pin_mentions(prompt)

        if prompt.startswith("!") or prompt.startswith("$ ") or prompt.startswith("/cmd ") or prompt.startswith("/run "):
            if prompt.startswith("!"):
                cmd = prompt[1:].strip()
            elif prompt.startswith("$ "):
                cmd = prompt[2:].strip()
            elif prompt.startswith("/cmd "):
                cmd = prompt[5:].strip()
            else:
                cmd = prompt[5:].strip()
            self._hide_hints()
            self.execute_terminal_command(cmd)
            return

        if prompt.startswith("/"):
            self._hide_hints()
            cmd = prompt.split(maxsplit=1)[0].lower()
            modal_cmds = (
                "/models", "/system", "/sessions", "/stats", "/modelinfo",
                "/settings", "/new", "/params", "/help", "/loader",
                "/exit", "/tools", "/status",
                "/diff", "/commit", "/todo", "/bg",
                "/ctx", "/add", "/mode", "/thinking",
                "/cd", "/changelog", "/update", "/updatesettings", "/mcp"
            )
            if cmd in modal_cmds:
                pass
            elif not self.in_chat_mode:
                self._switch_to_chat()
            if self._dispatch_command(prompt):
                return

        if self.is_generating:
            return

        self._hide_hints()
        if from_hero or not self.in_chat_mode:
            self._switch_to_chat()

        now_ts = time.time()
        now_str = time.strftime("%H:%M", time.localtime(now_ts))
        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            self._turn_counter += 1
            user_msg = UserMessageCard(prompt, turn_id=self._turn_counter, time_str=now_str)
            chat_scroll.mount(user_msg)
            chat_scroll.scroll_end(animate=False)
        except Exception:
            pass

        self.messages.append({"role": "user", "content": prompt, "turn_id": self._turn_counter})
        self._record_user_turn(prompt, self._turn_counter, time_str=now_str, timestamp=now_ts)
        self._active_view_turn_idx = None
        self._refresh_timeline_rail()
        bad_titles = ("new session", "untitled", "untitled session", "session", "tool", "mock", "tool mock")
        if not self.session_title.strip() or self.session_title.strip().lower() in bad_titles or self.session_title.startswith("Session session_"):
            from ...core.sessions import clean_title_from_prompt
            self.session_title = clean_title_from_prompt(prompt)
            self._session_named_by_model = False
            self._update_terminal_title()
            if self.current_provider != "local_gguf":
                self._maybe_auto_name_session()
        self._save_current_session()
        self._refresh_session_tabs()
        self.start_generation(prompt)


    def handle_submit(self, text: str) -> None:
        self._handle_submit(text, from_hero=(not self.in_chat_mode))


    def _dispatch_command(self, raw: str) -> bool:
        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "/help": self._cmd_help,
            "/mode": self._cmd_mode,
            "/thinking": self._cmd_thinking,
            "/new": self._cmd_new,
            "/branch": self._cmd_branch,
            "/git": self._cmd_git,
            "/commit": self._cmd_commit,
            "/revert": self._cmd_revert,
            "/diff": self._cmd_diff,
            "/editor": self._cmd_editor,
            "/todo": self._cmd_plan,
            "/plan": self._cmd_plan,
            "/bg": self._cmd_background_tasks,
            "/add": self._cmd_add,
            "/drop": self._cmd_drop,
            "/cd": self._cmd_cd,
            "/models": self._cmd_models,
            "/modelinfo": self._cmd_modelinfo,
            "/tools": self._cmd_tools,
            "/status": self._cmd_status,
            "/sessions": self._cmd_sessions,
            "/system": self._cmd_system,
            "/params": self._cmd_params,
            "/settings": self._cmd_settings,
            "/summarize": self._cmd_summarize,
            "/ctx": self._cmd_context,
            "/stats": self._cmd_stats,
            "/changelog": self._cmd_releases,
            "/update": self._cmd_update,
            "/updatesettings": self._cmd_update_settings,
            "/mcp": self._cmd_mcp,
            "/loader": self._cmd_loader,
            "/exit": self._cmd_quit,
        }
        handler = handlers.get(cmd)
        if handler is None:
            self.notify(f"Unknown command: {cmd} — type /help for the list", title="Command", severity="warning", timeout=3.0)
            return True
        handler(arg)
        return True


    def _cmd_quit(self, arg: str = "") -> None:
        self.action_quit_app()


    def _cmd_help(self, arg: str = "") -> None:
        self.push_screen(HelpModal(), self._on_help_command)


    def _cmd_mode(self, arg: str = "") -> None:
        arg = (arg or "").strip().lower()
        if not arg:
            self.cycle_mode()
            return
        if arg == "ask":
            arg = "code"
        if arg in ("auto", "plan", "code"):
            self.agent_mode = arg
            self.settings.config.setdefault("agent", {})["mode"] = self.agent_mode
            self.settings.save_config()
            self.agent_ctx.mode = self.agent_mode
            self._update_meta_bars()
            self.notify(f"Switched to {self.agent_mode.upper()} mode", title="Agent Mode", timeout=2.0)
        else:
            self.notify("Supported modes: auto, plan, code", title="Agent Mode", severity="warning", timeout=3.0)


    def _cmd_thinking(self, arg: str = "") -> None:
        """`/thinking` - pick a level, or set one by name.

        No argument opens the picker, which is the same shape as `/models`: a
        list you arrow through and take. A named level sets it directly, in any
        spelling, so `/thinking xHigh` and `/thinking max` are the same request
        as far as the model is concerned.
        """
        cap = self._capability()
        levels = cycle_order(cap.levels) if (cap and cap.thinking) else []
        if not levels:
            self.notify(
                f"{self.current_model_id} does not reason", severity="warning", timeout=3.0
            )
            return

        arg = (arg or "").strip()
        if not arg:
            def on_picked(level: Optional[str]) -> None:
                if level:
                    self._cmd_thinking(level)

            self.push_screen(
                ThinkingModal(self.thinking_level, self.current_model_id,
                              self.current_provider, cap=cap),
                on_picked,
            )
            return

        arg = normalize_level(arg)
        if arg in levels:
            self.thinking_level = arg
            self._thinking_invoked = True
            self.settings.config.setdefault("generation", {})["reasoning_level"] = arg
            self.settings.save_config()
            self._update_meta_bars()
            self.notify(f"Thinking set to: {arg}", timeout=2.0)
        else:
            self.notify(f"Thinking levels: {', '.join(levels)}", severity="warning", timeout=3.0)


    def _on_help_command(self, cmd: Optional[str]) -> None:
        if not cmd:
            return
        modal_cmds = ("/models", "/system", "/sessions", "/stats", "/modelinfo",
                      "/new", "/params", "/help", "/loader", "/settings",
                      "/tools", "/status", "/diff", "/commit",
                      "/todo", "/bg", "/ctx", "/add", "/mode", "/thinking",
                      "/changelog", "/update", "/updatesettings", "/mcp", "/exit")
        if not self.in_chat_mode and cmd not in modal_cmds:
            self._switch_to_chat()
        self._dispatch_command(cmd)


    def _cmd_new(self, arg: str = "") -> None:
        if not self.messages and not self._transcript and (not self.session_title or self.session_title == "New session"):
            # Already on an empty new session - don't duplicate
            try:
                if not self.in_chat_mode:
                    self.query_one("#hero-prompt-input", AutoExpandingInput).focus()
                else:
                    self.query_one("#prompt-input", AutoExpandingInput).focus()
            except Exception:
                pass
            return
        if self.messages:
            self._save_current_session()
        self.messages = []
        self._transcript = []
        self.current_session_id = f"session_{int(time.time())}"
        self.session_title = ""
        self._session_named_by_model = False
        self.stats = {
            "tokens_in": 0,
            "tokens_out": 0,
            "tok_per_sec": 0.0,
            "tools_run": 0,
        }
        self._current_assistant_card = None
        try:
            self.query_one("#chat-scroll", VerticalScroll).remove_children()
        except Exception:
            pass
        self._switch_to_hero()
        self._update_terminal_title()
        if hasattr(self, "_open_session_ids"):
            if self.current_session_id not in self._open_session_ids:
                self._open_session_ids.append(self.current_session_id)
        self._refresh_session_tabs()


    def reset_session(self) -> None:
        self._cmd_new()


    def _cmd_models(self, arg: str = "") -> None:
        self.push_screen(
            ModelSelectModal(self.current_provider, self.current_model_id),
            self._on_model_selected,
        )


    def _cmd_sessions(self, arg: str = "") -> None:
        try:
            self._save_current_session()
        except Exception:
            pass
        self.push_screen(SessionsModal(workdir=self.workdir), self._on_session_selected)


    def _cmd_system(self, arg: str = "") -> None:
        self.push_screen(SystemPromptModal(), self._on_prompt_selected)


    def _cmd_params(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if not arg:
            self.push_screen(ParamsModal(self.current_model_id, self.current_provider), lambda _: None)
            return
        parts = arg.split(maxsplit=1)
        name = parts[0].lower()
        param_specs = {
            "temperature": ("temperature", float, 0.0, 2.0),
            "temp": ("temperature", float, 0.0, 2.0),
            "top_p": ("top_p", float, 0.0, 1.0),
            "top-p": ("top_p", float, 0.0, 1.0),
            "max_tokens": ("max_tokens", int, 1, 32768),
            "tokens": ("max_tokens", int, 1, 32768),
            "repetition_penalty": ("repetition_penalty", float, 1.0, 2.0),
            "rep": ("repetition_penalty", float, 1.0, 2.0),
        }
        spec = param_specs.get(name)
        if spec is None:
            self._feedback(f"Unknown parameter: [b]{name}[/] — one of: temperature, top_p, max_tokens, repetition_penalty")
            return
        key, cast, lo, hi = spec
        gen = self.settings.config.setdefault("generation", {})
        if len(parts) < 2 or not parts[1].strip():
            self._feedback(f"[b #58a6ff]•[/] {key}: {gen.get(key, 0.6)}")
            return
        val_str = parts[1].strip()
        try:
            val = cast(val_str)
        except ValueError:
            self._feedback(f"Invalid value for {key}: [b]{val_str}[/]")
            return
        if val < lo or val > hi:
            self._feedback(f"{key} must be between {lo} and {hi}")
            return
        old = gen.get(key, 0.6)
        gen[key] = val
        self.settings.save_config()
        self._feedback(f"[b #58a6ff]•[/] {key}: {old} → {val}")


    def _cmd_settings(self, arg: str = "") -> None:
        prev_loader = str(self.settings.config.get("active_loader", "cpu") or "cpu").strip().lower()

        def _on_settings_closed(_result: Any = None) -> None:
            new_loader = str(self.settings.config.get("active_loader", "cpu") or "cpu").strip().lower()
            if new_loader != prev_loader:
                self._on_loader_selected(new_loader)

        self.push_screen(SettingsModal(), _on_settings_closed)


    def _cmd_summarize(self, arg: str = "") -> None:
        if self.is_generating:
            self.notify("Generation in progress — stop it first (Esc).", title="Summarize", severity="warning", timeout=3.0)
            return
        if not self.messages:
            self.notify("Nothing to summarize.", title="Summarize", severity="information", timeout=2.5)
            return
        if not self.in_chat_mode:
            self._switch_to_chat()
        self.trigger_summarize()


    def _cmd_stats(self, arg: str = "") -> None:
        self.push_screen(StatsModal(self.stats), lambda _: None)


    def _cmd_update_settings(self, arg: str = "") -> None:
        """/updatesettings -> automatic updates on, off, and how often."""
        from ..modals.update_settings import UpdateSettingsModal

        self.push_screen(UpdateSettingsModal(), self._on_update_setting)


    def _on_update_setting(self, action: Optional[str]) -> None:
        if action == "check":
            self._check_for_updates()
        elif action == "update":
            self._cmd_update("")


    def _check_for_updates(self, silent_if_current: bool = False) -> None:
        """Look for a newer release and say so, without pulling.

        Runs on a thread because it reaches the network. Only reports when the
        installed release is behind what GitHub has, so a check on an up-to-date
        installation says nothing at all.
        """
        from ...core.releases import get_bundled_release, get_display_version, get_release_list, get_releases

        def _work() -> None:
            try:
                fetched, _src = get_releases(refresh=True)
                if not fetched:
                    return
                newest = get_release_list(fetched)
                if not newest:
                    return
                latest_tag = str(newest[0].get("tag", ""))
                current_tag = str(get_display_version() or get_bundled_release().get("tag", ""))
                if current_tag and not _is_newer(latest_tag, current_tag):
                    if not silent_if_current:
                        self.call_from_thread(self.notify, "CMDAI CODE is up to date.", timeout=2.5)
                    return
                self.call_from_thread(
                    self.notify,
                    f"CMDAI CODE {latest_tag} is available - run [b]/update[/b] to install it.",
                    timeout=6.0,
                )
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()


    def _maybe_auto_update_check(self) -> None:
        """Startup check, gated on the user's own settings."""
        try:
            from ..modals.update_settings import is_check_due
            if is_check_due():
                self._check_for_updates(silent_if_current=True)
        except Exception:
            pass


    def _cmd_releases(self, arg: str = "") -> None:
        self.push_screen(ReleasesModal(), lambda _: None)


    def _cmd_update(self, arg: str = "") -> None:
        """/update -> the same update `cmdai code update` runs.

        Both go through core.updater.run_update, so they cannot drift apart.
        The only difference is who draws the steps: the terminal animator on the
        CLI, a progress window here. Nothing on this path may print - escape
        codes on stdout land inside the rendered frame.
        """
        from ...core.releases import write_pending_notice
        from ...core.updater import run_update, resolve_app_root
        from ..modals.update import TuiReporter, UpdateProgressModal

        if getattr(self, "_update_running", False):
            self.notify("An update is already running.", severity="warning", timeout=3.0)
            return
        if self.is_generating:
            self.notify("Finish the current turn before updating.", severity="warning", timeout=3.0)
            return

        app_root = resolve_app_root(getattr(self.settings, "base_dir", None))
        modal = UpdateProgressModal()
        self._update_running = True
        self.push_screen(modal, lambda _: None)

        def _run() -> None:
            reporter = TuiReporter(self, modal)
            try:
                result = run_update(app_root, reporter=reporter,
                                    on_updated=write_pending_notice)
            except Exception as e:
                reporter.message(f"[#f85149]Update failed: {e}[/]")
                self.call_from_thread(self._finish_update, None, str(e))
                return
            self.call_from_thread(self._finish_update, result, "")

        threading.Thread(target=_run, daemon=True).start()


    def _finish_update(self, result: Any, error: str) -> None:
        """Report the outcome once the updater's thread is done."""
        self._update_running = False
        if error:
            self.notify(f"Update failed: {error}", severity="error", timeout=6.0)
            return
        if result is None or not getattr(result, "ok", False):
            self.notify("Update failed. See the window for details.",
                        severity="error", timeout=6.0)
            return
        if result.changed:
            self.notify(f"{result.summary} - restart CMDAI CODE to run the new code.",
                        timeout=8.0)
        else:
            self.notify(result.summary, timeout=4.0)


    def show_user_actions(self, turn_id: int) -> None:
        from ..modals.actions import ActionMenuModal
        self.push_screen(ActionMenuModal(), lambda act: self._on_user_action(act, turn_id))


    def show_assistant_actions(self, turn_id: int) -> None:
        # Disabled: clicking model response does not show action menu (only user cards do)
        return


    def _restore_prompt_to_input(self, text: str) -> None:
        """Puts text back into the prompt input field and focuses it."""
        if not text:
            return
        try:
            target_input = None
            if self.in_chat_mode:
                try:
                    target_input = self.query_one("#prompt-input", AutoExpandingInput)
                except Exception:
                    pass
            if target_input is None:
                try:
                    target_input = self.query_one("#hero-prompt-input", AutoExpandingInput)
                except Exception:
                    try:
                        target_input = self.query_one("#prompt-input", AutoExpandingInput)
                    except Exception:
                        pass

            if target_input is not None:
                target_input.text = text
                lines = text.splitlines()
                last_row = max(0, len(lines) - 1)
                last_col = len(lines[-1]) if lines else 0
                target_input.cursor_location = (last_row, last_col)
                if hasattr(target_input, "_update_auto_height"):
                    target_input._update_auto_height()
                self.set_focus(target_input)
        except Exception:
            pass


    def _on_user_action(self, action: object, turn_id: int) -> None:
        if not action or int(turn_id) <= 0:
            return
        if action == "copy":
            try:
                text = next((str(m.get("content", "")) for m in self.messages if m.get("role") == "user" and int(m.get("turn_id") or -1) == int(turn_id)), "")
                if not text:
                    for t in self._transcript:
                        if isinstance(t, dict) and t.get("kind") == "user" and int(t.get("turn_id") or -1) == int(turn_id):
                            text = str(t.get("prompt", "") or t.get("content", "")).strip()
                            break
                if text and copy_text_to_clipboard(text):
                    self.notify("User message copied.", timeout=2.0)
            except Exception:
                pass
            return
        if action in ("revert", "delete"):
            if action == "revert":
                user_prompt = ""
                for m in self.messages:
                    if m.get("role") == "user" and int(m.get("turn_id") or -1) == int(turn_id):
                        user_prompt = str(m.get("content", "")).strip()
                        break
                if not user_prompt:
                    for t in self._transcript:
                        if isinstance(t, dict) and t.get("kind") == "user" and int(t.get("turn_id") or -1) == int(turn_id):
                            user_prompt = str(t.get("prompt", "") or t.get("content", "")).strip()
                            break
                if not user_prompt:
                    try:
                        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
                        for child in chat_scroll.children:
                            if isinstance(child, UserMessageCard) and int(getattr(child, "turn_id", -1)) == int(turn_id):
                                user_prompt = getattr(child, "prompt", "")
                                break
                    except Exception:
                        pass

                reverted = self._revert_files(from_turn=int(turn_id))
                self._delete_turn(int(turn_id), keep_user=False, notify=False)
                if user_prompt:
                    self._restore_prompt_to_input(user_prompt)
                files_info = f" ({len(reverted)} file(s))" if reverted else ""
                self.notify(f"Reverted turn {turn_id}{files_info} — prompt restored to input.", timeout=2.5)
            else:
                self._delete_turn(int(turn_id), keep_user=False)


    def _maybe_show_update_notice(self) -> None:
        """Show what changed, once, on the first launch after the version moved.

        Two ways in. The flag written by `cmdai code update` is authoritative:
        it proves an update actually ran. The version comparison catches
        everything the flag misses - a `git pull` by hand, a tag switch, another
        installer - because it only compares against the last recorded launch.
        """
        try:
            from ...core.releases import (
                clear_pending_notice,
                detect_version_change,
                get_display_version,
                mark_version_seen,
                read_pending_notice,
                resolve_release_for_version,
            )

            pending = read_pending_notice()
            if pending:
                clear_pending_notice()
                version = pending.get("version", "")
                mark_version_seen(version)
            else:
                change = detect_version_change(get_display_version())
                if not change:
                    return
                version = change.get("version", "")

            release = resolve_release_for_version(version)
            if not release:
                # Nothing bundled and no way to say what changed. Stay quiet
                # rather than opening an empty window.
                return
            self.push_screen(UpdateNoticeModal(version=version, release=release))
        except Exception:
            pass


    def _build_base_system_prompt(self, vision: Optional[bool] = None) -> str:
        if vision is None:
            vision = self._model_supports_vision()
        tools_list = [
            "- <tool:ls path=\".\" /> : List files in directory",
            "- <tool:read path=\"file.py\" lines=\"1-50\" /> : Read file contents",
            "- <tool:glob pattern=\"*.py\" dir=\".\" /> : Find files matching glob",
            "- <tool:search query=\"text\" path=\".\" /> : Search text across files",
            "- <tool:write path=\"file.py\">content</tool:write> : Create or overwrite file",
            "- <tool:edit path=\"file.py\"><old>exact_code</old><new>replacement_code</new></tool:edit> : Edit code",
            "- <tool:command>shell_command</tool:command> : Run terminal command. To run in background: <tool:command background=\"true\">cmd</tool:command>",
            "- <tool:web query=\"...\" /> or <tool:web url=\"...\" /> : Web search or page fetch",
            "- <tool:scratch action=\"add|done|clear\">step note</tool:scratch> : Update TODO queue",
            "- <tool:ask question=\"Question to user\" options=\"Option 1|Option 2|Option 3\" /> : Prompt user for choice/confirmation",
            "- <tool:subagent name=\"Code Reviewer\" system=\"Inspect project files...\">task</tool:subagent> : Spawn subagent. To run in background: <tool:subagent background=\"true\" name=\"...\">task</tool:subagent>",
            "- <tool:mcp /> : List MCP servers. Then <tool:mcp open server=\"NAME\" /> reveals that server's tools; call them as <tool:mcp__NAME__TOOL>{\"arg\":\"v\"}</tool:mcp__NAME__TOOL>",
        ]
        if vision:
            tools_list.append("- <tool:screenshot target=\"active_window\" /> : Capture screen or active window to PNG")
            tools_list.append("- <tool:vision image_path=\"file.png\" question=\"what to inspect\" /> : Inspect and analyze an image using vision")

        tools_str = "\n".join(tools_list)

        workdir_val = getattr(self, "workdir", ".")
        return (
            "You are CMDAI CODE, an autonomous terminal AI programming agent running on Windows.\n"
            f"Current working directory: {workdir_val}\n\n"
            "CRITICAL INSTRUCTIONS FOR CODE AGENT BEHAVIOR:\n"
            "1. You are an AUTONOMOUS CODE AGENT. NEVER tell the user to inspect files, run commands, or check directory structure themselves. YOU MUST DO IT YOURSELF using the XML tools immediately.\n"
            "2. When the user asks you to check, build, edit, or debug a project, DO NOT ask permission or tell the user to prepare the project — immediately run <tool:ls path=\".\" /> or <tool:read path=\"file\" /> to inspect it.\n"
            "3. Answer in the same language as the user.\n"
            "4. When you emit a tool call, the system will execute it and return the result inside <tool_response name=\"...\">...</tool_response>. You will then see the output and continue working until the task is complete.\n"
            "5. If a tool call fails (e.g. <tool:edit> fails with \"don't match\"), DO NOT GIVE UP or stop! You MUST immediately inspect the file using <tool:read>, locate the exact content, and retry <tool:edit> or use <tool:write>. The turn is only complete when all changes are properly applied.\n"
            "6. STRICT TOOL WHITELIST: You may ONLY use the tools listed below. NEVER invent or hallucinate non-existent tools (e.g. <tool:Experimental_image_generate>, <tool:download>, <tool:fetch>). To generate graphics, write SVG files directly with <tool:write>. To download images/files, execute curl or PowerShell via <tool:command>.\n\n"
            "AVAILABLE XML TOOLS:\n"
            f"{tools_str}\n"
        )


    def _model_supports_vision(self) -> bool:
        """Whether the model currently loaded can accept images."""
        try:
            return bool(getattr(self._capability(), "vision", False))
        except Exception:
            return False


    def _cmd_mcp(self, arg: str = "") -> None:
        parts = (arg or "").split()
        if self.mcp_client is None:
            self._feedback("[dim]MCP unavailable.[/dim]")
            return
        if len(parts) >= 2 and parts[0] == "restart":
            ok = self.mcp_client.restart(parts[1])
            self._feedback(f"[b #58a6ff]/mcp restart {parts[1]}:[/] {'connected' if ok else 'failed'}")
            return
        if len(parts) >= 2 and parts[0] == "logs":
            # The registry key is sanitized, so a server configured as
            # "my-server" is stored as "my_server". Looking up the raw
            # argument meant `/mcp logs my-server` always reported
            # "unknown server".
            key = sanitize_name(parts[1])
            srv = self.mcp_client.servers.get(key)
            if srv is None:
                known = ", ".join(self.mcp_client.server_names()) or "(none)"
                self._feedback(f"[b #58a6ff]/mcp logs:[/] [warn]no server named "
                               f"'{parts[1]}'[/]. Known: {known}")
                return
            logs = srv.stderr_log[-20:] or ["(stderr is empty)"]
            self._feedback("[b #58a6ff]/mcp logs:[/]\n" + "\n".join(logs))
            return
        # Bare /mcp opens a window. It is listed among the modal commands, so
        # printing a line of text here was the only entry in that list that
        # did not behave like the rest.
        from ..modals.mcp_servers import MCPServerModal
        self.push_screen(MCPServerModal(self.mcp_client, self.mcp_client.config_file()))


    def _subagent_target(self) -> Tuple[str, str]:
        """Which provider and model a subagent runs on.

        Always uses the same provider and model as the main agent so subagents
        share the exact active configuration.
        """
        return (self.current_provider, self.current_model_id)


    def _cmd_modelinfo(self, arg: str = "") -> None:
        self.push_screen(InfoModal("Model info", self._build_modelinfo_body()))


    def _cmd_loader(self, arg: str = "") -> None:
        arg = (arg or "").strip().lower()
        if arg in ("cpu", "cuda", "vulkan"):
            self._on_loader_selected(arg)
        else:
            if arg:
                self.notify(
                    f"Unsupported loader '{arg}'. Choose cpu, cuda, or vulkan.",
                    title="Loader",
                    severity="warning",
                    timeout=3.5,
                )
            self.push_screen(LoaderModal(), self._on_loader_selected)


    def _find_model_file(self, model_id: str) -> str:
        try:
            from ...core.resource_limits import find_local_model_file
            m_dir = getattr(getattr(self, "gguf_loader", None), "models_dir", "models")
            return find_local_model_file(model_id, models_dir=m_dir, workdir=getattr(self, "workdir", ""))
        except Exception:
            return ""


    def _resolve_context_limit(self) -> Tuple[int, bool, str]:
        try:
            from ...core.resource_limits import resolve_context_limit
            m_dir = getattr(getattr(self, "gguf_loader", None), "models_dir", "models")
            api_key = ""
            try:
                api_key = self.settings.get_api_key(self.current_provider)
            except Exception:
                api_key = ""
            return resolve_context_limit(
                self.current_model_id,
                self.current_provider,
                model_path=self._find_model_file(self.current_model_id) or None,
                api_key=api_key,
                models_dir=m_dir,
                workdir=getattr(self, "workdir", ""),
            )
        except Exception:
            return 8192, False, "fallback"


    def _dynamic_limit(self) -> int:
        limit, _, _ = self._resolve_context_limit()
        return limit


    def _build_modelinfo_body(self) -> str:
        import psutil
        from ...core.capabilities import budget_for, clamp_effort, normalize_level
        from ...core.resource_limits import (
            estimate_kv_cache_bytes_per_token,
            read_gguf_metadata,
        )

        vm = psutil.virtual_memory()
        total_ram_gb = vm.total / (1024**3)
        avail_ram_gb = vm.available / (1024**3)

        model_file = self._find_model_file(self.current_model_id) if (
            self.current_provider == "local_gguf"
        ) else ""
        if model_file and os.path.exists(model_file):
            meta = read_gguf_metadata(model_file)
            model_size_gb = float(meta.get("file_size_gb", 0.0) or 0.0)
            ram_after_model = max(0.5, avail_ram_gb - model_size_gb)
            cache_budget_70 = ram_after_model * 0.70
            bytes_per_tok = estimate_kv_cache_bytes_per_token(meta)
            tokens_70_ram = int(cache_budget_70 * (1024**3) / bytes_per_tok)
        else:
            cache_budget_70 = max(0.5, (avail_ram_gb * 0.70))
            tokens_70_ram = int(cache_budget_70 * 128_000)

        dyn_limit, known, _ = self._resolve_context_limit()
        cap = self._capability()
        gen = self.settings.config.get("generation", {})
        active_reasoning = normalize_level(gen.get("reasoning_level")) or (
            cap.default_level if cap else ""
        )

        lines = [
            f"[dim]name[/]         [b white]{self.current_model_id}[/]",
            f"[dim]provider[/]     {self.current_provider}",
            f"[dim]loader[/]       {self.settings.config.get('active_loader', 'cpu')}",
            f"[dim]code agent[/]   {'[b #3fb950]ON[/]' if self.settings.config.get('agent', {}).get('enabled', True) else '[dim]OFF[/]'}",
        ]

        gguf = getattr(self, "gguf_loader", None)
        if gguf and getattr(gguf, "current_model_path", None):
            actual_loader = getattr(gguf, "current_loader", None) or self.settings.config.get("active_loader", "cpu")
            lines += [
                f"[dim]engine[/]       GGUF Engine ({actual_loader})",
                f"[dim]file path[/]    {gguf.current_model_path}",
            ]

        lines.append("\n[bold white]Thinking & Reasoning Support:[/]")
        if cap and cap.thinking:
            # `budget_for`, not `BUDGET_MAP[level]`. This line read the fixed
            # ladder directly, so a local model was shown `32,768` next to a
            # level whose request carried a sixth of that.
            budget = budget_for(
                self.current_provider, active_reasoning, cap.context_limit
            )
            lines += [
                f"  [b #58a6ff]•[/] [dim]thinking[/]     [b #3fb950]Supported[/]",
                f"  [b #58a6ff]•[/] [dim]levels[/]       {', '.join(cycle_order(cap.levels))}",
                f"  [b #58a6ff]•[/] [dim]active level[/] [b white]{active_reasoning}[/]",
                f"  [b #58a6ff]•[/] [dim]budget[/]       [b #bc8cff]{budget:,} tokens[/]",
                f"  [b #58a6ff]•[/] [dim]api effort[/]   {clamp_effort(active_reasoning)}",
                f"  [b #58a6ff]•[/] [dim]param style[/]  {cap.param_style}",
            ]
            if cap.param_style == "variant":
                # A variant is a name the server recognises. No token count is
                # sent with it, so printing a budget implies one is.
                lines.append(
                    "  [dim]• the level goes out as a variant name; "
                    "no token budget is sent[/]"
                )
        else:
            lines.append("  [dim]• thinking:     Not supported for this model[/dim]")

        lines.append("\n[bold white]Context & Hardware Safety (70% RAM Rule):[/]")
        safe = f"[b #58a6ff]{dyn_limit:,} tokens[/]" if known else (
            f"[b #58a6ff]{dyn_limit:,}[/] [dim]fallback - nobody published this window[/]"
        )
        lines += [
            f"  [b #58a6ff]•[/] [dim]safe context[/] {safe}",
            f"  [b #58a6ff]•[/] [dim]system ram[/]   {total_ram_gb:.1f} GB total · {avail_ram_gb:.1f} GB free",
            f"  [b #58a6ff]•[/] [dim]70% cache[/]    {cache_budget_70:.1f} GB safe headroom (~{tokens_70_ram:,} tokens max)",
        ]

        lines.append("\n[bold white]Generation Parameters:[/]")
        lines.append(
            f"  [b #58a6ff]•[/] temp {gen.get('temperature', 0.6)} · "
            f"top_p {gen.get('top_p', 0.95)} · "
            f"max_tokens {gen.get('max_tokens', 2048)}"
        )
        return "\n".join(lines)


    def _cmd_tools(self, arg: str = "") -> None:
        from ..modals.tools import ToolsModal
        self.push_screen(ToolsModal())


    def _cmd_status(self, arg: str = "") -> None:
        self.push_screen(InfoModal("Workspace Status", self._build_status_body()))


    @staticmethod
    def _is_silent_tool_call(tool_name: str, target: str = "") -> bool:
        """True for tool calls that run but should not draw a card.

        Only one case: a bare `<tool:mcp />`, which asks which MCP servers
        exist. It does no work, it fires on nearly every MCP turn, and the
        answer is one keystroke away in the /mcp window. Drawing a card for it
        buried the real tool calls.

        Deliberately not silent:
          * `<tool:mcp open server="X" />` - returns that server's tool
            schemas, the step the model actually needs to see happen
          * `<tool:mcp__server__tool>` - a real call
        """
        name = str(tool_name or "").strip().lower()
        return name in ("mcp", "mcps") and not str(target or "").strip()


    def _build_status_body(self) -> str:
        agent_on = self.settings.config.get("agent", {}).get("enabled", True)
        cap = self._capability()
        lines = [
            f"[dim]Workspace:[/]    [b white]{self.workdir}[/]",
            f"[dim]Active Model:[/] [b #58a6ff]{self.current_model_id}[/]",
            f"[dim]Provider:[/]     {self.current_provider}",
            f"[dim]Loader:[/]       {self.settings.config.get('active_loader', 'cpu')}",
            f"[dim]Code Agent:[/]   {'[b #3fb950]ENABLED[/]' if agent_on else '[dim]DISABLED[/]'}",
            f"[dim]Current View:[/] {'Chat view' if self.in_chat_mode else 'Hero view'}",
            f"[dim]History:[/]      {len(self.messages)} turns",
            f"[dim]Generating:[/]   {'[b #f0883e]IN PROGRESS[/]' if self.is_generating else '[dim]IDLE[/]'}",
        ]

        # Whether this model reasons is detected, so the line is a fact about
        # the model rather than a preference. Ctrl+T walks what is listed here.
        if cap and cap.thinking:
            levels = cycle_order(cap.levels)
            lines.append(
                f"[dim]Thinking:[/]     [b #58a6ff]{self.thinking_level or cap.default_level}[/]"
                f" [dim]({'/'.join(levels)} - Ctrl+T)[/]"
            )
        else:
            lines.append("[dim]Thinking:[/]     [dim]not available for this model[/]")

        if hasattr(self, "stats"):
            lines.append(f"[dim]Total Tokens:[/] {self.stats.get('total_tokens', 0)}")
            lines.append(f"[dim]Tool Calls:[/]   {self.stats.get('tool_calls', 0)}")
        return "\n".join(lines)


    def _cmd_init(self, arg: str = "") -> None:
        if self.is_generating:
            self.notify("Generation in progress — wait or press Esc first.", title="Init", severity="warning", timeout=3.0)
            return
        if not self.in_chat_mode:
            self._switch_to_chat()
        target = f" ({arg})" if arg else ""
        prompt = f"Inspect project structure{target} and create AGENTS.md with architecture overview, file hierarchy, project instructions, and next steps."
        self._handle_submit(prompt, from_hero=False)


    def _cmd_run(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if arg:
            self.execute_terminal_command(arg)
        else:
            self._feedback("Usage: [b]/cmd <command>[/] (e.g. [dim]/cmd dir[/] or [dim]/cmd git status[/])")


    def _spawn_terminal_window(self, script_path: str, title: str, *args: str) -> None:
        import subprocess
        clean_args = [os.path.normpath(str(a)).rstrip("\\") for a in args if str(a).strip()]
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        subprocess.Popen(
            [sys.executable, script_path, *clean_args],
            creationflags=flags,
            cwd=self.workdir,
        )


    def _cmd_checkpoints(self, arg: str = "") -> None:
        from ..modals.checkpoints import CheckpointsModal
        self.push_screen(CheckpointsModal(workdir=self.workdir))


    def _git_window(self, view: str = "diff") -> None:
        """Open the git branches modal or single-file diff in the upgraded editor."""
        if view == "diff":
            self._open_diff_window()
            return
        from ..modals.git_modal import GitBranchesModal
        self.push_screen(GitBranchesModal(workdir=self.workdir))


    def _new_subagent_block(self, card: Any, role: str, goal: str, system: str, index: int, total: int, subagent_id: str = "") -> Any:
        """Create one chat marker for a subagent and label it with its role."""
        def _create():
            try:
                card.finish_thinking()
            except Exception:
                pass
            blk = card.add_tool("subagent", "starting")
            if blk is not None:
                if subagent_id:
                    blk.subagent_id = subagent_id
                blk.subagent_role = role
                blk.subagent_goal = goal
                blk.subagent_system = system
                blk.subagent_index = index
                blk.subagent_total = total
                blk.subagent_live_tool = "starting"
                blk.subagent_started = time.monotonic()
                blk.update_header()
            return blk

        try:
            if threading.current_thread() is threading.main_thread():
                block = _create()
            else:
                block = self.call_from_thread(_create)
        except Exception:
            return None
        if block is None:
            return None
        try:
            if subagent_id:
                block.subagent_id = subagent_id
            block.subagent_role = role
            block.subagent_goal = goal
            block.subagent_system = system
            block.subagent_index = index
            block.subagent_total = total
            if threading.current_thread() is threading.main_thread():
                self._scroll_chat_to_end_if_enabled()
            else:
                self.call_from_thread(self._scroll_chat_to_end_if_enabled)
        except Exception:
            pass
        return block


    def _open_subagent_chat(self, block: Any) -> None:
        """Push the subagent chat for one marker, with siblings for arrow nav."""
        try:
            from ..modals.subagent_chat import SubagentChatModal
        except Exception as e:
            self.notify(f"Cannot load subagent chat: {e}", severity="error")
            return
        try:
            card = next((a for a in getattr(block, "ancestors", []) if isinstance(a, AssistantTurnCard)), None)
            if card is None:
                card = self._current_assistant_card
            siblings = [b for b in getattr(card, "tool_blocks", []) if getattr(b, "subagent_role", "") or (getattr(b, "tool_name", "") or "").lower() in ("subagent", "task")] if card else []
            if not siblings or block not in siblings:
                siblings = [block]
            try:
                idx = siblings.index(block) + 1
            except ValueError:
                idx = 1
            from ...agent.tasks import BackgroundTaskManager
            _bg_mgr_chat = BackgroundTaskManager.get_instance()
            agents = []
            for b in siblings:
                b_sid = getattr(b, "subagent_id", "")
                b_info = _bg_mgr_chat.get_subagent(b_sid) if b_sid else None
                res_d = getattr(b, "result_data", {}) or {}
                if isinstance(res_d, dict) and not b_info and res_d.get("subagent_id"):
                    b_info = _bg_mgr_chat.get_subagent(res_d["subagent_id"])
                evs = list(getattr(b, "subagent_events", []) or [])
                nts = list(getattr(b, "subagent_notes", []) or [])
                fnds = list(getattr(b, "subagent_findings", []) or [])
                hsty = list(getattr(b, "subagent_history", []) or [])
                summ = str(res_d.get("summary", "") or (b_info.get("result", "") if b_info else "") or "")
                if b_info and isinstance(b_info, dict):
                    if b_info.get("events") and len(b_info["events"]) > len(evs):
                        evs = list(b_info["events"])
                    if b_info.get("notes") and len(b_info["notes"]) > len(nts):
                        nts = list(b_info["notes"])
                    if b_info.get("findings") and len(b_info["findings"]) > len(fnds):
                        fnds = list(b_info["findings"])
                    if b_info.get("history") and len(b_info["history"]) > len(hsty):
                        hsty = list(b_info["history"])
                agents.append({
                    "role": getattr(b, "subagent_role", "") or getattr(b, "target", "") or "Subagent",
                    "goal": getattr(b, "subagent_goal", "") or (b_info.get("task") if b_info else "") or "",
                    "system_prompt": getattr(b, "subagent_system", "") or "",
                    "summary": summ,
                    "history": hsty,
                    "findings": fnds,
                    "notes": nts,
                    "events": evs,
                    "status": getattr(b, "subagent_status", "running" if getattr(b, "tool_running", False) else "completed"),
                    "elapsed": float(getattr(b, "subagent_elapsed", 0.0) or (b_info.get("elapsed", 0.0) if b_info else 0.0)),
                    "tokens": int(getattr(b, "subagent_tokens", 0) or 0),
                    "error": getattr(b, "error_msg", "") if getattr(b, "has_error", False) else "",
                    "thinking": str(getattr(b, "thinking", "") or (b_info.get("thinking") if b_info else "") or ""),
                })
            running = bool(getattr(block, "tool_running", False))
            self.push_screen(SubagentChatModal(
                role=getattr(block, "subagent_role", "") or getattr(block, "target", "") or "Subagent",
                goal=getattr(block, "subagent_goal", "") or "",
                system_prompt=getattr(block, "subagent_system", "") or "",
                history=list(getattr(block, "subagent_history", []) or []),
                findings=list(getattr(block, "subagent_findings", []) or []),
                agent_index=idx,
                agent_total=len(agents),
                agents=agents,
                live_agent=block if running else None,
                thinking=str(getattr(block, "thinking", "") or ""),
            ))
        except Exception as e:
            self.notify(f"Could not open subagent view: {e}", severity="error")


    def _cmd_git(self, arg: str = "") -> None:
        """/git -> the git window: changed files, diff and the commit bar."""
        self._git_window("diff")


    def _cmd_branches(self, arg: str = "") -> None:
        """/branches -> the same window, straight into the branch list."""
        self._git_window("git")


    def _cmd_branch(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if not arg:
            self._cmd_branches()
            return
        import subprocess
        try:
            res = subprocess.run(["git", "checkout", "-b", arg], cwd=self.workdir, capture_output=True, text=True)
            if res.returncode == 0:
                self.notify(f"Created and checked out branch: {arg}", severity="information")
            else:
                self.notify(f"Git: {res.stderr.strip() or res.stdout.strip()}", severity="warning")
        except Exception as e:
            self.notify(f"Failed to create branch: {e}", severity="error")


    def _cmd_fork(self, arg: str = "") -> None:
        self._cmd_branch(arg)


    def _cmd_diff(self, arg: str = "") -> None:
        """/diff [file] -> open the upgraded editor in single-file diff mode."""
        self._open_diff_window(arg)


    def _on_diff_closed(self, action: Any) -> None:
        if action == "commit":
            self.push_screen(CommitModal(self.workdir), self._on_commit_done)


    def _cmd_editor(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        target_path = os.path.join(self.workdir, arg) if arg else self.workdir
        try:
            editor_py = self._get_editor_main_path()
            self._spawn_terminal_window(editor_py, "CMDAI CODE Editor", target_path)
        except Exception as e:
            self.notify(f"Failed to launch editor: {e}", severity="error")


    def _cmd_revert(self, arg: str = "") -> None:
        """/revert or /undo: revert files and remove user + model messages/cards from revert point onwards."""
        if self.is_generating:
            self.notify("Generation in progress — stop it first (Esc).", severity="warning", timeout=2.5)
            return

        arg = (arg or "").strip()
        target_turn = -1
        if arg.isdigit():
            target_turn = int(arg)
        else:
            # Revert the last user turn if no turn number is passed
            for m in reversed(self.messages):
                if m.get("role") == "user":
                    target_turn = int(m.get("turn_id") or -1)
                    break

        if target_turn > 0:
            user_prompt = ""
            for m in self.messages:
                if m.get("role") == "user" and int(m.get("turn_id") or -1) == int(target_turn):
                    user_prompt = str(m.get("content", "")).strip()
                    break
            reverted = self._revert_files(from_turn=target_turn)
            self._delete_turn(target_turn, keep_user=False, notify=False)
            if user_prompt:
                self._restore_prompt_to_input(user_prompt)
            if reverted:
                self.notify(f"Reverted turn {target_turn} and {len(reverted)} file(s).", timeout=2.5)
            else:
                self.notify(f"Reverted turn {target_turn} (no file modifications).", timeout=2.0)
        else:
            reverted = self._revert_files(from_turn=self._turn_counter if self._turn_counter > 0 else -1)
            if self._turn_counter > 0:
                user_prompt = ""
                for m in self.messages:
                    if m.get("role") == "user" and int(m.get("turn_id") or -1) == int(self._turn_counter):
                        user_prompt = str(m.get("content", "")).strip()
                        break
                self._delete_turn(self._turn_counter, keep_user=False, notify=False)
                if user_prompt:
                    self._restore_prompt_to_input(user_prompt)
            if reverted:
                self.notify(f"Reverted {len(reverted)} file(s).", timeout=2.5)
            else:
                self.notify("Nothing to revert.", timeout=2.0)


    def _revert_session_files(self, arg: str = "") -> None:
        self._cmd_revert(arg)


    def _cmd_test(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if arg:
            test_cmd = arg
        elif os.path.exists(os.path.join(self.workdir, "pytest.ini")) or os.path.exists(os.path.join(self.workdir, "tests")):
            test_cmd = "python -m pytest"
        elif os.path.exists(os.path.join(self.workdir, "package.json")):
            test_cmd = "npm test"
        elif os.path.exists(os.path.join(self.workdir, "Cargo.toml")):
            test_cmd = "cargo test"
        elif os.path.exists(os.path.join(self.workdir, "go.mod")):
            test_cmd = "go test ./..."
        else:
            test_cmd = "python -m unittest discover"

        self.notify(f"Running test suite: {test_cmd}", severity="information")
        self.execute_terminal_command(test_cmd)


    def _cmd_commit(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        self.push_screen(CommitModal(self.workdir, suggested_msg=arg), self._on_commit_done)


    def _on_commit_done(self, msg: Optional[str]) -> None:
        if msg:
            self._feedback(f"[b #3fb950]✔ Committed successfully:[/] [white]{msg}[/]")


    def _cmd_review(self, arg: str = "") -> None:
        if self.is_generating:
            self.notify("Generation in progress — wait or press Esc first.", severity="warning")
            return
        if not self.in_chat_mode:
            self._switch_to_chat()
        target = f" ({arg})" if arg else ""
        prompt = f"Perform a comprehensive code review of recent changes (git diff){target}: check security, potential bugs, performance, and code quality."
        self._handle_submit(prompt, from_hero=False)


    def _cmd_plan(self, arg: str = "") -> None:
        current_tasks = []
        for step in (getattr(self.agent_ctx, "todo_list", None) or []):
            if isinstance(step, dict):
                current_tasks.append((bool(step.get("done")), str(step.get("text", "")).strip()))
            else:
                current_tasks.append((False, str(step).strip()))
        current_tasks = [t for t in current_tasks if t[1]]

        def _on_plan_changed(new_tasks):
            todo_steps = [{"text": t[1], "done": t[0]} for t in new_tasks]
            self.agent_ctx.todo_list = todo_steps
            self._on_todo_updated([t[1] for t in new_tasks if not t[0]])

        self.push_screen(PlanModal(tasks=current_tasks, on_change=_on_plan_changed, workdir=self.workdir), self._on_plan_closed)


    def _on_plan_closed(self, result: Any) -> None:
        if isinstance(result, list):
            todo_steps = [{"text": t[1], "done": t[0]} for t in result]
            self.agent_ctx.todo_list = todo_steps
            self._on_todo_updated([t[1] for t in result if not t[0]])


    def _plan_steps(self) -> List[Dict[str, Any]]:
        """The agent's checklist as `{"text", "done"}` rows."""
        rows: List[Dict[str, Any]] = []
        for step in (getattr(self.agent_ctx, "todo_list", None) or []):
            if isinstance(step, dict):
                rows.append({
                    "text": str(step.get("text", "")).strip(),
                    "done": bool(step.get("done")),
                })
            else:
                rows.append({"text": str(step).strip(), "done": False})
        return [r for r in rows if r["text"]]


    def _save_plan_to_file(self) -> int:
        """No-op: tasks/plans are purely managed in memory/session without .md files."""
        return 0


    def _persist_plan_after_turn(self) -> int:
        """No-op: tasks/plans are purely managed in memory/session without .md files."""
        return 0


    def _load_plan_from_file(self) -> int:
        """No-op: tasks/plans are purely managed in memory/session without .md files."""
        return 0


    def _cmd_implement(self, arg: str = "") -> None:
        """`/implement` - switch to code mode and execute the plan checklist.

        The opencode way round: plan first, build after. The checklist made in
        PLAN mode stays in the agent context, so the agent already knows what
        the work order is - it only needs a mode where writing is allowed.
        """
        if self.is_generating:
            self.notify(
                "Generation in progress - stop it first (Esc).",
                severity="warning",
                timeout=2.5,
            )
            return

        pending = [s for s in self._plan_steps() if not s.get("done")]
        if not pending:
            # A restart empties the context, not the file.
            self._load_plan_from_file()
            pending = [s for s in self._plan_steps() if not s.get("done")]
        if not pending:
            self.notify(
                "No plan to implement. Use /mode plan, let the agent make a plan, then /implement.",
                severity="warning",
                timeout=3.5,
            )
            return

        # The same switch /mode code does, so the choice survives a restart.
        self.agent_mode = "code"
        self.settings.config.setdefault("agent", {})["mode"] = self.agent_mode
        self.settings.save_config()
        self.agent_ctx.mode = self.agent_mode
        self._update_meta_bars()

        # The checklist lives in the context, not in the prompt, so the steps go
        # into the message itself. Without them a plan resumed from the file
        # would be invisible to the model - the todo tool only reports the list
        # back when it is called. Indices are the ones `tool_scratch` uses
        # (0-based), because that is what "done" takes.
        indexed = "\n".join(
            f"{i}. {s['text']}"
            for i, s in enumerate(self._plan_steps())
            if not s.get("done")
        )
        extra = f"\nDodatkowe wytyczne: {arg.strip()}" if arg.strip() else ""
        self._handle_submit(
            "Zaimplementuj plan z checklisty krok po kroku:\n"
            f"{indexed}\n"
            "Po ukończeniu każdego kroku oznacz go przez "
            "<tool:todo action=\"done\" index=\"N\" />, gdzie N to numer kroku "
            "z listy powyżej. Na końcu zweryfikuj wynik."
            f"{extra}",
            from_hero=False,
        )


    def _cmd_debug(self, arg: str = "") -> None:
        from ...agent.verifier import scan_project_bugs
        report = scan_project_bugs(self.workdir)
        scanned = report.get("scanned_files", 0)
        errors = report.get("errors", {})
        if not errors:
            self._feedback(f"[b #3fb950]✔ Debug Syntax Check Passed![/] Scanned [b]{scanned}[/] files with [b #3fb950]0 errors[/].")
        else:
            lines = [f"[b #f85149]⚠️ Syntax Errors Detected[/] ({len(errors)} file(s) with errors out of {scanned} scanned):\n"]
            for fpath, errs in errors.items():
                lines.append(f"[b white]• {fpath}[/]")
                for e in errs:
                    lines.append(f"  [#f85149]{e}[/]")
            self._feedback("\n".join(lines))


    def _cmd_index(self, arg: str = "") -> None:
        try:
            from ...indexer.ast_indexer import ProjectIndexer
            indexer = ProjectIndexer(self.workdir)
            stats = indexer.index_all()
            self.notify("Workspace indexed successfully.", title="Index", timeout=2.5)
        except Exception as e:
            self.notify(f"Indexing failed: {e}", title="Index", severity="error", timeout=4.0)


    def _cmd_context(self, arg: str = "") -> None:
        max_ctx = self._dynamic_limit()
        def _on_context_close(updated_pinned: Optional[Dict[str, str]] = None) -> None:
            if updated_pinned is not None:
                self.pinned_files = updated_pinned
                cnt = len(self.pinned_files)
                self.notify(f"Context updated ({cnt} file{'s' if cnt != 1 else ''} pinned)", timeout=2.5)
        self.push_screen(
            ContextInspectorModal(
                model_id=self.current_model_id,
                system_prompt=self.system_prompt,
                messages=self.messages,
                pinned_files=dict(self.pinned_files),
                max_context=max_ctx,
                provider=self.current_provider,
                workdir=self.workdir,
                session_stats=dict(self.stats or {}),
            ),
            _on_context_close,
        )


    def _cmd_export(self, arg: str = "") -> None:
        target_path = arg.strip() if arg else ""
        if not target_path:
            exports_dir = os.path.join(self.workdir, "exports")
            os.makedirs(exports_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            target_path = os.path.join(exports_dir, f"session_{ts}.html")
        elif not os.path.isabs(target_path):
            target_path = os.path.join(self.workdir, target_path)

        try:
            out_file = export_session_to_html(
                session_id=self.current_session_id or "default",
                messages=self.messages,
                stats=self.stats,
                model_id=self.current_model_id,
                workdir=self.workdir,
                output_path=target_path,
            )
            rel = os.path.relpath(out_file, self.workdir)
            if self.is_running:
                self._feedback(f"[b #3fb950]Session exported successfully:[/] [b white]{rel}[/]")
                self.notify(f"Exported to {rel}", timeout=3.5)
            send_desktop_notification("CMDAI CODE", f"Session exported: {rel}")
        except Exception as e:
            if self.is_running:
                self._feedback(f"[#f85149]Failed to export session:[/] {e}")


    def _cmd_add(self, arg: str = "") -> None:
        arg = (arg or "").strip()
        if arg and os.path.isfile(os.path.join(self.workdir, arg)):
            self._add_file_to_context(arg)
        else:
            try:
                target_dir = os.path.normpath(self.workdir)
                if arg and os.path.isdir(os.path.join(self.workdir, arg)):
                    target_dir = os.path.normpath(os.path.join(self.workdir, arg))
                os.startfile(target_dir)
                self._feedback(f"[b #58a6ff]•[/] Opened Windows Explorer in: [b white]{target_dir}[/]")
            except Exception as e:
                self.notify(f"Failed to open Windows Explorer: {e}", severity="error")


    def _on_file_selected_for_add(self, rel_path: Optional[str]) -> None:
        if rel_path:
            self._add_file_to_context(rel_path)


    def _add_file_to_context(self, rel_path: str) -> None:
        full_p = os.path.join(self.workdir, rel_path) if not os.path.isabs(rel_path) else rel_path
        if not os.path.exists(full_p):
            self._feedback(f"[#f85149]File not found:[/] {rel_path}")
            return

        try:
            with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            clean_rel = os.path.relpath(full_p, self.workdir).replace("\\", "/")
            self.pinned_files[clean_rel] = content
            lines = len(content.splitlines())
            self._feedback(f"[b #3fb950]+ Pinned to context:[/] [b white]{clean_rel}[/] [dim]({lines} lines)[/dim]")
        except Exception as e:
            self._feedback(f"[#f85149]Error reading file:[/] {e}")


    def _cmd_drop(self, arg: str = "") -> None:
        arg = (arg or "").strip()

        if not self.pinned_files:
            self._feedback("[dim]No files currently pinned in context.[/dim]")
            return

        if not arg or arg.lower() == "all":
            cnt = len(self.pinned_files)
            self.pinned_files.clear()
            self._feedback(f"[b #f0883e]- Unpinned all {cnt} files from context.[/]")
            return

        matched = [k for k in self.pinned_files if arg.lower() in k.lower()]
        if matched:
            for k in matched:
                del self.pinned_files[k]
            self._feedback(f"[b #f0883e]- Unpinned from context:[/] {', '.join(matched)}")
        else:
            self._feedback(f"[dim]File not pinned:[/] {arg} (pinned: {', '.join(self.pinned_files.keys())})")


    def _cmd_cd(self, arg: str = "") -> None:
        target = (arg or "").strip()
        if not target:
            self.notify(f"{self.workdir}\nUsage: /cd <path>", title="Working Directory", timeout=3.0)
            return

        if (target.startswith('"') and target.endswith('"')) or (target.startswith("'") and target.endswith("'")):
            target = target[1:-1].strip()

        target = os.path.expanduser(target)
        target = os.path.expandvars(target)

        if not os.path.isabs(target):
            new_path = os.path.abspath(os.path.join(self.workdir, target))
        else:
            new_path = os.path.abspath(target)

        if not os.path.exists(new_path):
            self.notify(f"No such directory: {target}", title="Working Directory", severity="error", timeout=3.0)
            return

        if not os.path.isdir(new_path):
            self.notify(f"Not a directory: {target}", title="Working Directory", severity="error", timeout=3.0)
            return

        self.workdir = new_path
        try:
            os.chdir(new_path)
        except Exception:
            pass

        self.session_manager = SessionManager(project_dir=self.workdir)

        
        if getattr(self, "agent_ctx", None):
            self.agent_ctx.workdir = self.workdir
            self.agent_ctx.current_cwd = self.workdir
        if getattr(self, "agent_runner", None):
            self.agent_runner.workdir = self.workdir

        if hasattr(self, "system_prompt") and "Current working directory:" in self.system_prompt:
            self.system_prompt = re.sub(
                r"Current working directory: [^\n]+",
                lambda _: f"Current working directory: {self.workdir}",
                self.system_prompt,
            )

        self._update_meta_bars()

        self.notify(f"Changed to: {self.workdir}", title="Working Directory", timeout=2.5)


    def execute_command(self, cmd_text: str) -> None:
        self._dispatch_command(cmd_text)


    def execute_terminal_command(self, cmd_line: str) -> None:
        cmd_line = cmd_line.strip()
        if not cmd_line or self.is_generating:
            return

        if not self.in_chat_mode:
            self._switch_to_chat()
        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            self._turn_counter += 1
            user_msg = UserMessageCard(f"!{cmd_line}", turn_id=self._turn_counter)
            chat_scroll.mount(user_msg)
            card = AssistantTurnCard("Terminal", turn_id=self._turn_counter)
            chat_scroll.mount(card)
            chat_scroll.scroll_end(animate=False)
            self._run_terminal_worker(card, cmd_line)
        except Exception:
            pass


    @work(thread=True)
    def _run_terminal_worker(self, card: AssistantTurnCard, cmd_line: str) -> None:
        from ...agent.tools import tool_command
        self.call_from_thread(card.add_tool, "command", cmd_line)
        res = tool_command(self.agent_ctx, cmd_line)
        self.call_from_thread(card.finish_tool, res)
        self.call_from_thread(card.finish, 0.1, 0, 0.0)


    def _on_loader_selected(self, loader: Optional[str]) -> None:
        if loader:
            norm = str(loader).strip().lower()
            self.settings.config["active_loader"] = norm
            self.settings.save_config()
            reloaded_note = ""
            if self.gguf_loader.llm is not None:
                self.gguf_loader.unload()
                reloaded_note = " (model unloaded — will reload on next turn)"
            self.notify(
                f"Active GGUF loader set to {norm.upper()}{reloaded_note}.",
                title="Loader",
                severity="information",
                timeout=3.0,
            )


    def _on_model_selected(self, result: Tuple[str, str]) -> None:
        if result and result[0] and result[1]:
            old_model = self.current_model_id
            self.current_provider, self.current_model_id = result
            self.settings.config["default_provider"] = self.current_provider
            self.settings.config["default_model"] = self.current_model_id
            self.settings.save_config()
            self.update_meta_bars()
            if old_model != self.current_model_id and self.gguf_loader.llm is not None:
                self.gguf_loader.unload()
            self._check_and_warn_model_memory(self.current_provider, self.current_model_id)


    def _check_and_warn_model_memory(self, provider: str, model_id: str) -> None:
        if provider != "local_gguf":
            return
        found_path = self._find_model_file(model_id) or None
        if not found_path:
            return

        try:
            mem = estimate_model_memory(found_path)
            req = mem.get("total_required_gb", 0)
            avail = mem.get("system_available_gb", 0)
            total = mem.get("system_total_gb", 0)
            fits = mem.get("fits_comfortably", True)

            if not fits or req > avail:
                self.notify(
                    f"{model_id}: ~{req} GB required vs {avail}/{total} GB available. Risk of slow swap.",
                    title="Memory Warning",
                    severity="warning",
                    timeout=5.5,
                )
        except Exception:
            pass


    def _handle_agent_ask(self, question: str, options: List[str]) -> str:
        """Ask the user something from a worker thread and wait for the answer.

        The wait is bounded. An unbounded `evt.wait()` here hangs the whole
        agent loop when the modal never reports back, and the app looks frozen
        with no error anywhere. Three ways that happens, all reproduced:

          * `call_from_thread` is a no-op because the event loop is busy
          * `push_screen` raises while the screen is being mounted
          * the screen mounts but its dismiss callback never fires

        In every case the tool call now fails loudly with a message the model
        can act on, instead of the app going silent forever. Returning "" is
        deliberate: the gate treats that as "declined", so the safe outcome
        (no tool runs) is also the one that keeps the turn moving.
        """
        evt = threading.Event()
        res = [""]
        failures: List[str] = []

        def _on_modal_dismiss(val: str) -> None:
            res[0] = "" if val is None else str(val)
            evt.set()

        def _open_modal() -> None:
            try:
                self.push_screen(AskModal(question, options=options), _on_modal_dismiss)
            except Exception as exc:
                # Without this the exception surfaces on the Textual side and
                # this thread waits for an event nobody will ever set.
                failures.append(f"could not open the prompt: {exc}")
                evt.set()

        try:
            self.call_from_thread(_open_modal)
        except Exception as exc:
            failures.append(f"could not reach the UI thread: {exc}")
            evt.set()

        if not evt.wait(timeout=self.ASK_TIMEOUT):
            failures.append(f"no answer within {self.ASK_TIMEOUT:.0f}s")

        if failures:
            detail = "; ".join(failures)
            message = (f"Agent question went unanswered ({detail}). "
                       f"Treated as declined.")
            # Guarded: reporting the failure uses the same channel that just
            # failed, so an unguarded notify here would raise inside the worker
            # and lose the one message that explains what went wrong. When the
            # channel is dead entirely, print instead - a line in the terminal
            # beats silence.
            try:
                self.call_from_thread(self.notify, message,
                                      title="Timeout", timeout=6.0)
            except Exception:
                try:
                    print(f"[CMDAI] {message}", file=sys.stderr)
                except Exception:
                    pass
        return res[0]


    def _on_prompt_selected(self, result: Any) -> None:
        if not result:
            return
        if isinstance(result, dict):
            p_text = result.get("text") or result.get("prompt", "")
            p_name = result.get("name", "Custom")
        else:
            p_text = str(result)
            p_name = "Custom"

        if p_text:
            if "<tool:" not in p_text:
                self.system_prompt = (
                    f"{p_text}\n\n"
                    f"Current working directory: {self.workdir}\n\n"
                    "CRITICAL INSTRUCTIONS FOR CODE AGENT BEHAVIOR:\n"
                    "1. You are an AUTONOMOUS CODE AGENT. NEVER tell the user to inspect files, run commands, or check directory structure themselves. YOU MUST DO IT YOURSELF using the XML tools immediately.\n"
                    "2. When the user asks you to check, build, edit, or debug a project, DO NOT ask permission or tell the user to prepare the project — immediately run <tool:ls path=\".\" /> or <tool:read path=\"file\" /> to inspect it.\n"
                    "3. Answer in the same language as the user.\n"
                    "4. When you emit a tool call, the system will execute it and return the result inside <tool_response name=\"...\">...</tool_response>. You will then see the output and continue working until the task is complete.\n"
                    "5. If a tool call fails (e.g. <tool:edit> fails with \"don't match\"), DO NOT GIVE UP or stop! You MUST immediately inspect the file using <tool:read>, locate the exact content, and retry <tool:edit> or use <tool:write>. The turn is only complete when all changes are properly applied.\n\n"
                    "AVAILABLE XML TOOLS:\n"
                    "- <tool:ls path=\".\" /> : List files in directory\n"
                    "- <tool:read path=\"file.py\" lines=\"1-50\" /> : Read file contents\n"
                    "- <tool:glob pattern=\"*.py\" dir=\".\" /> : Find files matching glob\n"
                    "- <tool:search query=\"text\" path=\".\" /> : Search text across files\n"
                    "- <tool:write path=\"file.py\">content</tool:write> : Create or overwrite file\n"
                    "- <tool:edit path=\"file.py\"><old>exact_code</old><new>replacement_code</new></tool:edit> : Edit code\n"
                    "- <tool:command>shell_command</tool:command> : Run terminal command\n"
                    "- <tool:web>url_or_query</tool:web> : Web fetch or search\n"
                    "- <tool:scratch action=\"add|done|clear\">step note</tool:scratch> : Update TODO queue\n"
                    "- <tool:subagent name=\"Code Reviewer\" system=\"Inspect project files using ls and read, then provide a detailed evaluation.\">task description</tool:subagent> : Spawn an autonomous subagent\n"
                    "- <tool:ask question=\"Question to user\" options=\"Option 1|Option 2|Option 3\" /> : Prompt user for choice/confirmation\n"
                )
            else:
                self.system_prompt = p_text

            self.settings.config["active_system_prompt"] = p_name
            self.settings.save_config()

            self.notify(f"Active system prompt: {p_name}", title="System Prompt", timeout=2.5)

