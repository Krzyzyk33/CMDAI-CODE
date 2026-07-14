import os
import sys
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.shortcuts import CompleteStyle
COMMANDS = {
    "/progress": "show the current progress of the AI Assistant plan",
    "/auto": "switch to automatic mode (without asking for file permissions)",
    "/clear": "clear the entire chat history and the console screen buffer",
    "/code": "switch to coding mode (asks for permission before editing)",
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
    "/sessions": "manage contextual sessions and revert to an older state",
    "/subagents": "manage background subagents and assign models for background tasks",
    "/undo": "stash the recent code modifications to revert them"
}
class CMDAICompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        
        if "@" in text:
                                    
            prefix = text.split("@")[-1]
            try:
                dirname = os.path.dirname(prefix) or "."
                basename = os.path.basename(prefix)
                for f in os.listdir(dirname):
                    if f.startswith(basename):
                        path = os.path.join(dirname, f).replace("\\", "/")
                        if path.startswith("./"):
                            path = path[2:]
                        yield Completion(path, start_position=-len(basename))
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
            prefix = text.split("@")[-1]
            matches = []
            try:
                import os
                dirname = os.path.dirname(prefix) or "."
                basename = os.path.basename(prefix)
                for f in os.listdir(dirname):
                    if f.startswith(basename) and not f.startswith(".cmdai_code_project"):
                        full_path = os.path.join(dirname, f)
                        if os.path.isfile(full_path):
                            path = full_path.replace("\\", "/")
                            if path.startswith("./"): path = path[2:]
                            matches.append(("@" + path, "File"))
            except Exception:
                pass
            return matches
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
        
        self.cmd_index = 0
        self.cmd_scroll = 0
        
        from prompt_toolkit.filters import Condition
        
        @Condition
        def is_dropdown_active():
            text = self.session.default_buffer.text if hasattr(self, 'session') else ""
            return text.startswith("/") or ("@" in text and not text.startswith("/"))

        def get_matches(text):
            if text.startswith("/"):
                word = text.lstrip()
                m = [cmd for cmd in COMMANDS.keys() if cmd.startswith(word)]
                if not m and word in COMMANDS:
                    m = [word]
                return m
            elif "@" in text:
                prefix = text.split("@")[-1]
                m = []
                import os
                try:
                    dirname = os.path.dirname(prefix) or "."
                    basename = os.path.basename(prefix)
                    for f in os.listdir(dirname):
                        if f.startswith(basename):
                            path = os.path.join(dirname, f).replace("\\\\", "/")
                            if path.startswith("./"): path = path[2:]
                            m.append("@" + path)
                except Exception:
                    pass
                return m
            return []
        
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
                    parts = text.split("@")
                    parts[-1] = val[1:]
                    event.app.current_buffer.text = "@".join(parts) + " "
                else:
                    event.app.current_buffer.text = val + " "
                event.app.current_buffer.cursor_position = len(event.app.current_buffer.text)
        @self.bindings.add('up', filter=is_dropdown_active)
        def _(event):
            text = event.app.current_buffer.text
            matches = self._get_matches(text)
            if matches:
                self.cmd_index = max(0, self.cmd_index - 1)
                
        @self.bindings.add('down', filter=is_dropdown_active)
        def _(event):
            text = event.app.current_buffer.text
            matches = self._get_matches(text)
            if matches:
                self.cmd_index = min(len(matches) - 1, self.cmd_index + 1)
                
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
                    parts = text.split("@")
                    parts[-1] = val[1:]
                    event.app.current_buffer.text = "@".join(parts) + " "
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
            key_bindings=self.bindings,
            style=Style.from_dict({
                'prompt': 'white bold',
                'bottom-toolbar': 'default',
            }),
            complete_style=CompleteStyle.READLINE_LIKE,
            complete_while_typing=False,
            erase_when_done=True
        )
    def get_input(self, model_name: str = "model", tokens: int = 0, max_tokens: int = 128000) -> str:
        import shutil
        import json, os
        engine_short = "cpp"
        state_file = os.path.expanduser("~/.cmdai_code/state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    engine_short = json.load(f).get("llama_engine", "llama cpp").replace("llama ", "")
            except:
                pass
                
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit import print_formatted_text
        from prompt_toolkit.layout.processors import Processor, Transformation
        from prompt_toolkit.utils import get_cwidth
        
        width = shutil.get_terminal_size().columns - 2
        top = "╭" + "─" * (width - 2) + "╮"
        
        handler = self
        
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
                        current_bottom = "╰" + "─" * (bottom_len - 2) + "╯"
                        new_fragments.append(('fg:white', current_bottom))
                        
                    new_fragments.append(('', ' ' * (term_width - width)))
                    mode_sym = {"code": "⏵ code", "auto": "⏵⏵ auto", "plan": "⏸ plan"}[handler.modes[handler.mode_index]]
                    think_name = handler.thinking_levels[handler.thinking_idx][0]
                    
                    pct = (tokens / max_tokens) * 100 if max_tokens > 0 else 0
                    bar_len = 10
                    filled_len = int((pct / 100) * bar_len)
                    filled_len = min(bar_len, max(0, filled_len))
                    bar = "█" * filled_len + "░" * (bar_len - filled_len)
                    
                    pct_str = f"{model_name}: ctx [{bar}] {pct:.0f}% ({tokens}/{max_tokens})"
                    pct_style = 'fg:red' if pct >= 80 else ('fg:white' if pct >= 50 else 'fg:gray')
                    
                    status_left = f"  {mode_sym} ·  {engine_short} ·  {think_name} ·  "
                    new_fragments.append(('fg:gray', status_left))
                    new_fragments.append((pct_style, pct_str))
                    
                return Transformation(new_fragments)
        
                                                                                 
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        sys.stderr.flush()
        
        self.session.reserve_space_for_menu = 0
            
        def get_prompt():
            text = self.session.default_buffer.text
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
                        desc = desc[:avail_width-3] + "..."
                        
                    if actual_idx == self.cmd_index:
                        lines.append(f"<style bg='#333333' fg='#ffffff'>  {cmd.ljust(15)} {desc} </style>")
                    else:
                        lines.append(f"<style fg='gray'>  {cmd.ljust(15)} {desc} </style>")
            else:
                self.cmd_index = 0
                self.cmd_scroll = 0
                    
            empty_lines = 2 - len(lines)
            if empty_lines < 0:
                empty_lines = 0
                
            prompt_str = "\n" * empty_lines
            if lines:
                prompt_str += "\n".join(lines) + "\n"
                
            prompt_str += f"<style fg='white'>{top}</style>\n<style fg='white'>│ </style><b>&gt; </b>"
            return HTML(prompt_str)
            
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
