import os
import sys
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexer import RegexLexer
from pygments.token import Keyword, Text, String

COMMANDS = {
    "/progress": "show the current progress of the AI Assistant plan",
    "/auto": "switch to automatic mode (without asking for file permissions)",
    "/clear": "clear the entire chat history and the console screen buffer",
    "/code": "switch to coding mode (asks for permission before editing)",
    "/cd": "change current working directory",
    "/commit": "commit all current modifications to the git repository",
    "/compact": "summarize the conversation so far to save tokens",
    "/diff": "show local changes against the git repository",
    "/ide": "display the current connection status with your IDE environment",
    "/init": "scan the entire repository and build a file knowledge base",
    "/llama": "change the Llama engine (e.g. llama cpp, llama vulcan, llama diffusion)",
    "/model": "switch the currently used artificial intelligence model to another one",
    "/plan": "switch to planning mode (only reads files and plans)",
    "/quit": "terminate the program and close the terminal window",
    "/review": "toggle auto-reflection mode to double-check generated code",
    "/runsubagents": "execute background tasks manually using a plan-first flow",
    "/sessions": "manage contextual sessions and revert to an older state",
    "/subagents": "manage background subagents and assign models for background tasks",
    "/undo": "stash the recent code modifications to revert them"
}


class CMDAILexer(RegexLexer):
    tokens = {
        'root': [
            (r'^/runsubagents\b', Keyword.Special),
            (r'@\S+', Keyword.Type),
            (r'".*?"', String),
            (r'.', Text),
        ]
    }


class CMDAICompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if "@" in text:
            prefix = text.split("@")[-1].lower()
            cwd = os.getcwd()
            skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "appdata", ".gemini", ".cmdai_code"}
            matches_count = 0
            try:
                for root, dirs, files in os.walk(cwd):
                    dirs[:] = [d for d in dirs if d.lower() not in skip_dirs and not d.startswith(".")]
                    for f in files:
                        if f.startswith("."):
                            continue
                        rel_path = os.path.relpath(os.path.join(root, f), cwd).replace("\\", "/")
                        if prefix in f.lower() or prefix in rel_path.lower():
                            yield Completion(rel_path, start_position=-len(prefix))
                            matches_count += 1
                            if matches_count >= 15:
                                return
            except Exception:
                pass


class InputHandler:
    def _get_matches(self, text):
        is_slash = text.startswith("/")
        is_file = "@" in text and not is_slash
        if is_slash:
            word = text.lstrip()
            matches = [(cmd, desc) for cmd, desc in COMMANDS.items() if cmd.startswith(word)]
            if not matches and word in COMMANDS:
                matches = [(word, COMMANDS[word])]
            return matches
        elif is_file:
            prefix = text.split("@")[-1].lower()
            matches = []
            cwd = os.getcwd()
            skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "appdata", ".gemini", ".cmdai_code"}
            seen = set()
            try:
                for root, dirs, files in os.walk(cwd):
                    dirs[:] = [d for d in dirs if d.lower() not in skip_dirs and not d.startswith(".")]
                    for f in files:
                        if f.startswith("."):
                            continue
                        if prefix in f.lower() or prefix in os.path.join(root, f).lower():
                            rel_path = os.path.relpath(os.path.join(root, f), cwd).replace("\\", "/")
                            item = "@" + rel_path
                            if item not in seen:
                                seen.add(item)
                                matches.append((item, "File"))
                                if len(matches) >= 15:
                                    break
                    if len(matches) >= 15:
                        break
            except Exception:
                pass
            return matches[:15]
        return []

    def __init__(self, history_file="~/.cmdai_code/history", thinking_idx=1):
        self.history_file = os.path.expanduser(history_file)
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)

        self.bindings = KeyBindings()
        self.mode_index = 0
        self.modes = ["code", "auto", "plan"]
        self.thinking_expanded = True
        self.thinking_levels = [
            ("○ Low", 512, "Skipped (max 1 line per file)", "No sub-trees", "No forced actions"),
            ("◐ Medium", 1024, "2 fields: UNDERSTAND + PLAN", "No sub-trees", "Search callers (yes), Build (no)"),
            ("◑ High", 2048, "4 fields: UND. + OPTIONS + CHOICE + PLAN", "Sub-trees rarely", "Search callers (yes), Build (yes)"),
            ("◕ Ultra", 4096, "6 fields (with RISK)", "Sub-trees always", "Everything + Risk identification"),
            ("● Extreme", 8192, "6 fields + Re-validation at the end", "Sub and sub-sub always", "Tests after build + separate re-validation step")
        ]
        self.thinking_idx = thinking_idx
        self.pending_typeahead = ""
        self._cached_width = 0
        self._cached_top = ""
        self._cached_engine = ""

        self.cmd_index = 0
        self.cmd_scroll = 0

        from prompt_toolkit.filters import Condition
        from prompt_toolkit.application import get_app

        @Condition
        def is_dropdown_active():
            try:
                text = get_app().current_buffer.text
                return bool(text and (text.startswith("/") or ("@" in text and not text.startswith("/"))))
            except Exception:
                return False

        @self.bindings.add('s-tab')
        def _(event):
            self.mode_index = (self.mode_index + 1) % len(self.modes)
            event.app.invalidate()

        @self.bindings.add('c-e')
        def _(event):
            self.thinking_expanded = not self.thinking_expanded

        @self.bindings.add('c-t')
        def _(event):
            self.thinking_idx = (self.thinking_idx + 1) % len(self.thinking_levels)
            event.app.invalidate()

        @self.bindings.add('tab', filter=is_dropdown_active)
        def _(event):
            text = event.app.current_buffer.text
            matches = self._get_matches(text)
            if matches and 0 <= self.cmd_index < len(matches):
                val = matches[self.cmd_index][0]
                if "@" in text and not text.startswith("/"):
                    idx = text.rfind("@")
                    event.app.current_buffer.text = text[:idx] + val + " "
                else:
                    event.app.current_buffer.text = val + " "
                event.app.current_buffer.cursor_position = len(event.app.current_buffer.text)

        @self.bindings.add('up', filter=is_dropdown_active)
        def _(event):
            text = event.app.current_buffer.text
            matches = self._get_matches(text)
            if matches:
                self.cmd_index = max(0, self.cmd_index - 1)
                event.app.invalidate()

        @self.bindings.add('down', filter=is_dropdown_active)
        def _(event):
            text = event.app.current_buffer.text
            matches = self._get_matches(text)
            if matches:
                self.cmd_index = min(len(matches) - 1, self.cmd_index + 1)
                event.app.invalidate()

        @self.bindings.add('enter', filter=is_dropdown_active)
        def _(event):
            text = event.app.current_buffer.text
            matches = self._get_matches(text)

            if text.lstrip() in COMMANDS:
                event.app.current_buffer.validate_and_handle()
                return

            if matches and 0 <= self.cmd_index < len(matches):
                val = matches[self.cmd_index][0]
                if "@" in text and not text.startswith("/"):
                    idx = text.rfind("@")
                    event.app.current_buffer.text = text[:idx] + val + " "
                else:
                    event.app.current_buffer.text = val + " "
                event.app.current_buffer.cursor_position = len(event.app.current_buffer.text)
            else:
                event.app.current_buffer.validate_and_handle()

        @self.bindings.add('enter', filter=~is_dropdown_active)
        def _(event):
            event.app.current_buffer.validate_and_handle()

        @self.bindings.add('escape', 'enter')
        def _(event):
            event.app.current_buffer.insert_text('\n')

        @self.bindings.add('c-v')
        def _(event):
            try:
                import ctypes
                ctypes.windll.user32.OpenClipboard(0)
                handle = ctypes.windll.user32.GetClipboardData(13)
                if handle:
                    ptr = ctypes.windll.kernel32.GlobalLock(handle)
                    data = ctypes.c_wchar_p(ptr).value
                    ctypes.windll.kernel32.GlobalUnlock(handle)
                    if data:
                        event.app.current_buffer.insert_text(data)
                ctypes.windll.user32.CloseClipboard()
            except Exception:
                try:
                    ctypes.windll.user32.CloseClipboard()
                except:
                    pass

        self.session = PromptSession(
            history=FileHistory(self.history_file),
            completer=CMDAICompleter(),
            lexer=PygmentsLexer(CMDAILexer),
            key_bindings=self.bindings,
            style=Style.from_dict({
                'prompt': 'white bold',
                'bottom-toolbar': 'default',
                'pygments.keyword.special': 'bg:#00aaaa fg:#ffffff bold',
                'pygments.keyword.type': '#ffffff',
            }),
            complete_style=CompleteStyle.READLINE_LIKE,
            complete_while_typing=False,
            erase_when_done=True
        )

    def get_input(self, model_name: str = "model", tokens: int = 0, max_tokens: int = 128000) -> str:
        import shutil
        import json, os

        new_width = shutil.get_terminal_size().columns - 2
        if new_width != self._cached_width:
            self._cached_width = new_width
            self._cached_top = "╭" + "─" * (new_width - 2) + "╮"

        width = self._cached_width
        top = self._cached_top

        engine_short = self._cached_engine
        state_file = os.path.expanduser("~/.cmdai_code/state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    eng = json.load(f).get("llama_engine", "llama cpp").replace("llama ", "")
                    if eng != self._cached_engine:
                        self._cached_engine = eng
                        engine_short = eng
            except:
                pass

        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.layout.processors import Processor, Transformation
        from prompt_toolkit.utils import get_cwidth

        handler = self
        _model_name = model_name
        _tokens = tokens
        _max_tokens = max_tokens
        _engine_short = engine_short

        class WrappingBottomProcessor(Processor):
            def apply_transformation(self, ti):
                term_width = shutil.get_terminal_size().columns - 1
                W = width - 5
                if W < 10:
                    return Transformation(ti.fragments)

                new_fragments = []
                if ti.lineno > 0:
                    new_fragments.append(('fg:white', '│   '))

                current_line_len = 0

                for style, text in ti.fragments:
                    for char in text:
                        if char == '\n':
                            pad = W - current_line_len
                            if pad > 0:
                                new_fragments.append(('', ' ' * pad))
                            new_fragments.append(('fg:white', '│'))
                            new_fragments.append(('', ' ' * (term_width - width)))
                            new_fragments.append(('fg:white', '│   '))
                            current_line_len = 0
                            continue

                        cw = get_cwidth(char)
                        if current_line_len + cw > W:
                            pad = W - current_line_len
                            if pad > 0:
                                new_fragments.append(('', ' ' * pad))
                            new_fragments.append(('fg:white', '│'))
                            new_fragments.append(('', ' ' * (term_width - width)))
                            new_fragments.append(('fg:white', '│   '))
                            current_line_len = 0

                        new_fragments.append((style, char))
                        current_line_len += cw

                pad = W - current_line_len
                if pad > 0:
                    new_fragments.append(('', ' ' * pad))
                new_fragments.append(('fg:white', '│'))

                if ti.lineno == ti.document.line_count - 1:
                    new_fragments.append(('', ' ' * (term_width - width)))

                    bottom_len = width
                    if bottom_len > 2:
                        new_fragments.append(('fg:white', "╰" + "─" * (bottom_len - 2) + "╯"))

                    new_fragments.append(('', ' ' * (term_width - width)))
                    mode_sym = {"code": "⏵ code", "auto": "⏵⏵ auto", "plan": "⏸ plan"}[handler.modes[handler.mode_index]]
                    think_name = handler.thinking_levels[handler.thinking_idx][0]

                    pct = (_tokens / _max_tokens) * 100 if _max_tokens > 0 else 0
                    bar_len = 10
                    filled_len = min(bar_len, max(0, int((pct / 100) * bar_len)))
                    bar = "█" * filled_len + "░" * (bar_len - filled_len)

                    tokens_disp = f"{_tokens}" if _tokens < 1000 else f"{_tokens//1000}k"
                    max_tok_val = 128000 if _max_tokens > 1000000 else _max_tokens
                    max_tokens_disp = f"{max_tok_val//1000}k" if max_tok_val >= 1000 else f"{max_tok_val}"

                    pct_str = f"{_model_name}: ctx [{bar}] {pct:.0f}% ({tokens_disp}/{max_tokens_disp})"
                    pct_style = 'fg:red' if pct >= 80 else ('fg:white' if pct >= 50 else 'fg:gray')

                    new_fragments.append(('fg:gray', f"  {mode_sym} ·  {_engine_short} ·  {think_name} ·  "))
                    new_fragments.append((pct_style, pct_str))

                return Transformation(new_fragments)

        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        sys.stderr.flush()

        self.session.reserve_space_for_menu = 0

        _last_prompt_text = [None]
        _last_prompt_html = [None]

        def get_prompt():
            text = self.session.default_buffer.text
            if text == _last_prompt_text[0] and _last_prompt_html[0] is not None:
                return _last_prompt_html[0]
            _last_prompt_text[0] = text

            lines = []
            is_slash = text.startswith("/")
            is_file = "@" in text and not is_slash

            if is_slash or is_file:
                matches = self._get_matches(text)

                if matches:
                    self.cmd_index = min(self.cmd_index, len(matches) - 1)
                    if self.cmd_index < self.cmd_scroll:
                        self.cmd_scroll = self.cmd_index
                    elif self.cmd_index >= self.cmd_scroll + 4:
                        self.cmd_scroll = self.cmd_index - 3

                visible_matches = matches[self.cmd_scroll : self.cmd_scroll + 4]

                for i, (cmd, desc) in enumerate(visible_matches):
                    actual_idx = self.cmd_scroll + i
                    avail_width = width - 20
                    if len(desc) > avail_width:
                        desc = desc[:avail_width - 3] + "..."

                    if actual_idx == self.cmd_index:
                        lines.append(f"<style bg='#333333' fg='#ffffff'>  {cmd.ljust(15)} {desc} </style>")
                    else:
                        lines.append(f"<style fg='gray'>  {cmd.ljust(15)} {desc} </style>")
            else:
                self.cmd_index = 0
                self.cmd_scroll = 0

            if lines:
                empty_lines = max(0, 4 - len(lines))
                prompt_str = "\n" * empty_lines + "\n".join(lines) + "\n"
            else:
                prompt_str = "\n\n\n\n"

            prompt_str += f"<style fg='white'>{top}</style>\n<style fg='white'>│ </style><b>&gt; </b>"
            result = HTML(prompt_str)
            _last_prompt_html[0] = result
            return result

        _last_prompt_text[0] = None
        _last_prompt_html[0] = None

        def get_continuation(width, line_number, is_soft_wrap):
            return []

        default_text = self.pending_typeahead
        self.pending_typeahead = ""
        try:
            res = self.session.prompt(
                get_prompt,
                multiline=True,
                prompt_continuation=get_continuation,
                reserve_space_for_menu=0,
                input_processors=[WrappingBottomProcessor()],
                default=default_text
            )
            return res
        except KeyboardInterrupt:
            return ""
        except EOFError:
            return "/quit"
        except Exception as e:
            if type(e).__name__ == "NoConsoleScreenBufferError":
                return input("> ")
            raise

    def get_mode(self) -> str:
        return self.modes[self.mode_index]
