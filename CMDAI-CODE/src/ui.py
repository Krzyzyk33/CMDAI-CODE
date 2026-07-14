from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.spinner import Spinner
from rich.live import Live
import time
import sys
console = Console()
                                       
ACCENT_COLOR = "white"         
MUTED_COLOR = "gray50"
SUCCESS_COLOR = "green"
ERROR_COLOR = "red"
def print_header(model_name: str, cwd: str):
    from rich import box
    import json, os
    engine_short = "cpp"
    state_file = os.path.expanduser("~/.cmdai_code/state.json")
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
            cwd = state.get("cwd", cwd)
            engine_str = state.get("llama_engine", "")
            if "vulcan" in engine_str.lower():
                engine_short = "vulcan"
            elif "diffusion" in engine_str.lower():
                engine_short = "diffusion"
            elif "cpp" in engine_str.lower():
                engine_short = "cpp"
    except:
        pass

    header_text = Text()
    header_text.append(f"✻ CMDAI CODE  ·   {model_name}  ·   {engine_short}\n", style=ACCENT_COLOR)
    header_text.append(f"cwd: {cwd}   /help · /review · @plik · ⇧Tab", style=MUTED_COLOR)
    
    panel = Panel(header_text, border_style=ACCENT_COLOR, padding=(0, 2), box=box.ROUNDED)
    console.print(panel)
def print_user_msg(msg: str):
    from rich.markup import escape
    console.print(f"\n\n[bold]> {escape(msg)}[/bold]")
def print_agent_msg(msg: str):
    console.print(f"[{ACCENT_COLOR}]✻[/] {msg}")
    
def print_tool_call(tool_name: str, arg_summary: str):
    name_lower = tool_name.lower()
    if name_lower in ["read_file", "read"]:
        console.print(f"\n[bold]● Read: {arg_summary}[/bold]")
    elif name_lower in ["edit_file", "edit", "append_file", "replace_lines"]:
        console.print(f"\n[bold]● Edit: {arg_summary}[/bold]")
    elif name_lower in ["write_file", "create_file", "write", "create"]:
        console.print(f"\n[bold]● New file: {arg_summary}[/bold]")
    elif name_lower == "todo_write":
        console.print(f"\n[bold]● Task Plan[/bold]")
    elif name_lower in ["bash", "commands"]:
        console.print(f"\n[bold]● Command: {arg_summary}[/bold]")
    elif name_lower == "run_python":
        console.print(f"\n[bold]● Python Script: {arg_summary}[/bold]")
    elif name_lower == "delete_file":
        console.print(f"\n[bold]● Delete: {arg_summary}[/bold]")
    elif name_lower in ["ls", "list_dir"]:
        console.print(f"\n[bold]● Ls: {arg_summary}[/bold]")
    elif name_lower == "glob":
        console.print(f"\n[bold]● Glob: {arg_summary}[/bold]")
    elif name_lower in ["run_tests", "tests"]:
        console.print("\n[bold]● Tests:[/bold]")
    elif name_lower == "code_search":
        console.print(f"\n[bold]● Code Search: {arg_summary}[/bold]")
    elif name_lower == "bugs":
        console.print("\n[bold]● Bugs:[/bold]")
    elif name_lower == "search_web":
        console.print(f"\n[bold]● Web Search: {arg_summary}[/bold]")
    else:
        console.print(f"\n[bold]● Tool ({tool_name}): {arg_summary}[/bold]")
    import sys
    sys.stdout.flush()
def print_tool_result(result_summary: str, escape_text: bool = False):
    if escape_text:
        from rich.markup import escape
        result_summary = escape(result_summary)
    console.print(f"[bright_black]  ⎿  {result_summary}[/bright_black]", highlight=False)
from rich.syntax import Syntax
def print_diff(path: str, old_str: str, new_str: str):
    import difflib
    import os
    import re
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
    
    start_line = 1
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            idx = content.find(old_str)
            if idx != -1:
                start_line = content[:idx].count("\n") + 1
    except Exception:
        pass

    old_lines = old_str.splitlines() if old_str else []
    new_lines = new_str.splitlines() if new_str else []
    diff_lines = list(difflib.unified_diff(old_lines, new_lines, lineterm='', n=3))
    
    if len(diff_lines) > 2:
        diff_lines = diff_lines[2:]
        
    if not diff_lines:
        print_tool_result(f"No changes detected in {path}")
        return

    table = Table(show_header=False, box=None, padding=(0, 0), collapse_padding=True, expand=True)
    table.add_column("Line", width=5)
    table.add_column("Code", ratio=1)

    current_old_line = start_line
    current_new_line = start_line
    last_old_line = start_line - 1
    
    for line in diff_lines:
        if line.startswith("@@"):
            m = re.search(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if m:
                new_old_line = start_line - 1 + int(m.group(1))
                if last_old_line != start_line - 1 and new_old_line > last_old_line + 1:
                    gap = new_old_line - last_old_line - 1
                    table.add_row("", Text(f" ... {gap} hidden lines ...", style="bright_black"))
                current_old_line = new_old_line
                current_new_line = start_line - 1 + int(m.group(2))
                last_old_line = current_old_line - 1
            continue
        elif line.startswith("+"):
            table.add_row(
                Text(f" {current_new_line:>3} ", style="bright_black on #1a331a"), 
                Text(f" {line[1:]}", style="default on #1a331a")
            )
            current_new_line += 1
            last_old_line = current_old_line - 1
        elif line.startswith("-"):
            table.add_row(
                Text(f" {current_old_line:>3} ", style="bright_black on #331a1a"), 
                Text(f" {line[1:]}", style="default on #331a1a")
            )
            current_old_line += 1
            last_old_line = current_old_line - 1
        else:
            table.add_row(
                Text(f" {current_new_line:>3} ", style="bright_black"), 
                Text(f" {line[1:]}", style="default")
            )
            current_old_line += 1
            current_new_line += 1
            last_old_line = current_old_line - 1

    panel = Panel(table, title=f" {path} ", title_align="left", border_style=ACCENT_COLOR, padding=(0, 2))
    console.print(panel)
    import sys
    sys.stdout.flush()
def print_code_panel(path: str, content: str, lexer_override: str = None, show_line_numbers: bool = True, start_line: int = 1):
    lexer = lexer_override or "text"
    if not lexer_override:
        if path.endswith(".py"): lexer = "python"
        elif path.endswith(".js") or path.endswith(".ts"): lexer = "javascript"
        elif path.endswith(".html"): lexer = "html"
        elif path.endswith(".css"): lexer = "css"
        elif path.endswith(".go"): lexer = "go"
        elif path.endswith(".json"): lexer = "json"
    
    p = path.replace("\\", "/")
    parts = p.split("/")
    display_title = ".../" + "/".join(parts[-3:]) if len(parts) > 3 else p
    
    lines = content.splitlines()
    if lexer == "text" and not show_line_numbers:
        display_content = content
        subtitle = None
        from rich.text import Text
        from rich.markup import escape
        safe_content = display_content.replace("[red]●[/red]", "___RED_DOT___")
        escaped_content = escape(safe_content)
        final_markup = escaped_content.replace("___RED_DOT___", "[red]●[/red]")
        renderable = Text.from_markup(final_markup)
    else:
        display_content = "\n".join(lines[:1000])
        subtitle = f"[gray50]... (and {len(lines)-1000} more lines)[/gray50]" if len(lines) > 1000 else None
        renderable = Syntax(display_content, lexer, theme="monokai", line_numbers=show_line_numbers, start_line=start_line, word_wrap=True)
        
    panel = Panel(renderable, title=f"[bold]{display_title}[/bold]", title_align="left", subtitle=subtitle, subtitle_align="right", border_style=ACCENT_COLOR)
    console.print(panel)
    import sys
    sys.stdout.flush()
import rich.spinner
rich.spinner.SPINNERS["claude"] = {"interval": 120, "frames": ["✻", "✽", "✶", "✢"]}
def format_time(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m = s // 60
    sec = s % 60
    return f"{m}m {sec}s"
class ThinkingTree:
    def __init__(self, expanded=True, simulate=False, title="Thinking", model_name=None):
        self.expanded = expanded
        self.simulate = simulate
        self.title = title
        self.model_name = model_name
        self.lines = []
        self.spinner_frames = ["⌬", "✻"]
        self.frame_idx = 0
        self.fake_thoughts = [
            "Analyzing prompt and context...",
            "Verifying available system tools...",
            "Designing solution architecture...",
            "Checking syntax and dependencies...",
            "Optimizing logic...",
            "Preparing code implementation..."
        ]
        self.fake_added = 0
        
        self.start_time = time.time()
        self.live = Live(get_renderable=self.render, refresh_per_second=5, transient=False)
        
    def start(self):
        self.start_time = time.time()
        self.live.start()
        
    def add_line(self, line: str):
        self.simulate = False
        lstripped = line.lstrip()
        spaces = len(line) - len(lstripped)
        
        import re
                                                                    
        if re.match(r'^(-\s|\*\s|\d+\.\s|[a-zA-Z]\.\s)', lstripped) and spaces < 2:
            spaces = 2
        elif re.match(r'^\d+\.\d+\.\s', lstripped) and spaces < 4:
            spaces = 4
            
        if re.match(r'^(-\s|\*\s)', lstripped):
            content = lstripped[2:].strip()
        else:
            content = lstripped.strip()
            
        self.lines.append(" " * spaces + content)
            
    def render(self):
        self.frame_idx = (self.frame_idx + 1) % len(self.spinner_frames)
        sym = self.spinner_frames[self.frame_idx]
        elapsed_sec = time.time() - self.start_time
        
        if self.simulate and self.fake_added < len(self.fake_thoughts):
            if int(elapsed_sec) > (self.fake_added + 1) * 12:
                self.lines.append(self.fake_thoughts[self.fake_added])
                self.fake_added += 1
        
        t = Text()
        model_str = f" · {self.model_name}" if self.model_name else ""
        t.append(f"\n{sym} {self.title}... ({format_time(elapsed_sec)}{model_str})\n", style=ACCENT_COLOR)
        if self.expanded:
            for line in self.lines:
                spaces = len(line) - len(line.lstrip())
                indent = " " * spaces
                clean_line = line.strip()
                if clean_line.startswith("|_"):
                    clean_line = clean_line[2:].strip()
                t.append(f"{indent}  |_ {clean_line}\n", style="gray50")
        return t
        
    def update(self):
        pass                                                           
        
    def stop(self):
        self.live.stop()
        import sys
        sys.stdout.flush()
        print("", end="", flush=True)
        
    def print_tree(self):
        if not self.lines: return
        
        elapsed_sec = time.time() - self.start_time
        model_str = f" · {self.model_name}" if self.model_name else ""
        console.print(f"\n[{ACCENT_COLOR}]✻ {self.title}... ({format_time(elapsed_sec)}{model_str})[/]")
        if self.expanded:
            for line in self.lines:
                spaces = len(line) - len(line.lstrip())
                indent = " " * spaces
                clean_line = line.strip()
                if clean_line.startswith("|_"):
                    clean_line = clean_line[2:].strip()
                console.print(f"{indent}  [{MUTED_COLOR}]|_ {clean_line}[/]")
        console.print("")
def print_turn_done(elapsed: float, tokens: int, tool_count: int):
    console.print(f"\n[{ACCENT_COLOR}]✔ Done[/] ({format_time(elapsed)} · ⛁ {tokens} tokens · {tool_count} tool calls)")
class LiveToolStream:
    def __init__(self):
        self.start_time = time.time()
        self.frames = ["○", "●"]
        self.content = ""
        self.tool_name = "Generating tool"
        self.live = Live(get_renderable=self.render, refresh_per_second=10, transient=True)
        
    def start(self):
        self.start_time = time.time()
        self.live.start()
        
    def update(self, raw_json_buffer: str):
        self.content = raw_json_buffer
        if '"name":' in raw_json_buffer:
            import re
            m = re.search(r'"name"\s*:\s*"([^"]+)"', raw_json_buffer)
            if m:
                self.tool_name = m.group(1)
        
    def render(self):
        from rich.console import Group
        from rich.panel import Panel
        elapsed_sec = time.time() - self.start_time
        frame_idx = int(elapsed_sec * 4) % len(self.frames)
        sym = self.frames[frame_idx]
        
        t = Text()
        t.append(f"\n{sym} {self.tool_name}... ({format_time(elapsed_sec)})\n", style="white bold")
        
        import re
        m = re.search(r'"(?:code|content|command|file_content)"\s*:\s*"(.*)', self.content, re.DOTALL)
        if m:
            preview = m.group(1)
                                                                   
            preview = preview.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
            
                                                           
            if preview.endswith('"\n}'): preview = preview[:-3]
            elif preview.endswith('"}'): preview = preview[:-2]
            elif preview.endswith('"'): preview = preview[:-1]
            
            lines = preview.splitlines()
            if len(lines) > 20:
                preview = "\n".join(lines[-20:])
                
            path_m = re.search(r'"(?:path|TargetFile|file_path|file)"\s*:\s*"([^"]+)"', self.content)
            title = f"[bold white]{path_m.group(1)}[/bold white]" if path_m else ""
            
            from rich import box
            panel = Panel(preview, border_style="white", box=box.ROUNDED, title=title, title_align="left")
            return Group(t, panel)
            
        return t
        
    def stop(self):
        self.live.stop()

class SearchSpinner:
    def __init__(self, query: str, is_web: bool = False):
        self.query = query
        self.is_web = is_web
        self.start_time = time.time()
        self.frames = ["⌕", "🌐"] if is_web else ["⌕"]
        self.live = Live(get_renderable=self.render, refresh_per_second=10, transient=True)
        
    def start(self):
        self.live.start()
        
    def render(self):
        elapsed = time.time() - self.start_time
        if self.is_web:
            frame_idx = int(elapsed / 0.6) % 2
            icon = self.frames[frame_idx]
            text = f"Web search: \"{self.query}\"..."
        else:
            icon = "⌕"
            text = f"Scanning: \"{self.query}\"..."
        
        t = Text()
        t.append(f"\n{icon} {text}", style="bold")
        return t
        
    def stop(self, result_summary: str, details: str = ""):
        self.live.stop()
        final_icon = "🌐" if self.is_web else "⌕"
        text = f"Web search: \"{self.query}\"" if self.is_web else f"Scanning: \"{self.query}\""
        
        console.print(f"\n[bold]{final_icon} {text}[/bold]")
        console.print(f"  [{MUTED_COLOR}]|_ {result_summary}[/]")
        if details:
            lines = [l for l in details.splitlines() if l.strip() and "Results for" not in l]
            if self.is_web:
                lines = [l for l in lines if "Link:" in l or l.startswith("http")]
            for line in lines[:5]:
                console.print(f"    [{MUTED_COLOR}]|_ {line}[/]")
            if len(lines) > 5:
                console.print(f"    [{MUTED_COLOR}]|_ ... and {len(lines) - 5} more[/]")
        
def render_todo(items: list, checked_indices: list):
    for i, item in enumerate(items):
        if i in checked_indices:
            console.print(f"[{SUCCESS_COLOR}]☑ {item}[/]")
        else:
            console.print(f"[{MUTED_COLOR}]☐ {item}[/]")

def prompt_toolkit_select(title: str, choices: list) -> str:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import Window, HSplit
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.styles import Style

    selected_index = 0
    result = None

    bindings = KeyBindings()

    @bindings.add("q")
    @bindings.add("c-c")
    def _(event):
        event.app.exit()

    @bindings.add("up")
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(choices)

    @bindings.add("down")
    def _(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(choices)

    @bindings.add("enter")
    def _(event):
        nonlocal result
        result = choices[selected_index][1]
        event.app.exit()

    def get_text():
        lines = [("", f"\n  {title}\n\n")]
        for i, (label, val) in enumerate(choices):
            if i == selected_index:
                lines.append(("class:selected", f"  > {label}\n"))
            else:
                lines.append(("class:unselected", f"    {label}\n"))
        lines.append(("class:help", "\n  (Strzałki: nawigacja, Enter: wybór)\n"))
        return lines

    style = Style.from_dict({
        "selected": "fg:#00ffff bold",
        "unselected": "fg:#aaaaaa",
        "help": "fg:#666666 italic"
    })

    app = Application(
        layout=Layout(HSplit([Window(content=FormattedTextControl(get_text))])),
        key_bindings=bindings,
        style=style,
        full_screen=False
    )
    app.run()
    return result
