from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.spinner import Spinner
from rich.live import Live
import time
import sys
import shutil


def _set_title():
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("cmdai code")
    except Exception:
        try:
            sys.stdout.write("\033]0;cmdai code\007")
        except Exception:
            pass


_set_title()
console = Console()


class PrefixWrapper:
    def __init__(self, renderable, prefix_text="│ ", prefix_style="grey50"):
        if isinstance(renderable, str):
            from rich.text import Text

            self.renderable = Text.from_markup(renderable)
        else:
            self.renderable = renderable
        self.prefix_text = prefix_text
        self.prefix_style = prefix_style

    def __rich_console__(self, console, options):
        from rich.segment import Segment

        prefix_width = len(self.prefix_text)
        narrowed_options = options.update_width(options.max_width - prefix_width)
        lines = list(console.render_lines(self.renderable, narrowed_options))

        style = console.get_style(self.prefix_style)
        left = Segment(self.prefix_text, style)
        nl = Segment("\n")

        for i, line in enumerate(lines):
            yield left
            yield from line
            if i < len(lines) - 1:
                yield nl


def _cprint(renderable, source_id=None, **kwargs):
    if source_id:
        renderable = PrefixWrapper(renderable)
    console.print(renderable, **kwargs)


ACCENT_COLOR = "white"
MUTED_COLOR = "gray50"
SUCCESS_COLOR = "green"
ERROR_COLOR = "red"


def _set_title():
    try:
        import ctypes, os
        ctypes.windll.kernel32.SetConsoleTitleW("cmdai code")
    except Exception:
        try:
            import sys
            sys.stdout.write("\033]0;cmdai code\007")
        except Exception:
            pass

def print_header(model_name: str, cwd: str):
    _set_title()
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
    header_text.append(
        f"✻ CMDAI CODE  ·   {model_name}  ·   {engine_short}\n", style=ACCENT_COLOR
    )
    header_text.append(
        f"cwd: {cwd}   /help · /review · @plik · ⇧Tab", style=MUTED_COLOR
    )

    panel = Panel(
        header_text, border_style=ACCENT_COLOR, padding=(0, 2), box=box.ROUNDED
    )
    console.print(panel)


def print_user_msg(msg: str):
    from rich.markup import escape

    console.print(f"\n\n[bold]> {escape(msg)}[/bold]")


def print_agent_msg(msg: str):
    console.print(f"[{ACCENT_COLOR}]✻[/] {msg}")


def print_tool_call(tool_name: str, arg_summary: str, source_id: str = None):
    from rich.markup import escape

    arg_summary = escape(arg_summary)
    name_lower = tool_name.lower()
    if name_lower in ["read_file", "read"]:
        _cprint(f"\n[bold]● Read: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower in ["edit_file", "edit", "append_file", "replace_lines"]:
        _cprint(f"\n[bold]● Edit: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower in ["write_file", "create_file", "write", "create"]:
        _cprint(f"\n[bold]● New file: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower in ["todo_write", "save_plan", "mark_plan_step_done"]:
        _cprint(f"\n[bold]● Plan: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower in ["bash", "commands", "run_bash"]:
        _cprint(f"\n[bold]● Command: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower in ["run_python", "python"]:
        _cprint(f"\n[bold]● Python Script: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower == "delete_file":
        _cprint(f"\n[bold]● Delete: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower in ["ls", "list_dir"]:
        _cprint(f"\n[bold]● Ls: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower == "glob":
        _cprint(f"\n[bold]● Glob: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower in ["run_tests", "tests"]:
        _cprint("\n[bold]● Tests:[/bold]", source_id=source_id)
    elif name_lower in ["code_search", "grep"]:
        _cprint(f"\n[bold]● Code Search: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower in ["search_web", "web"]:
        _cprint(f"\n[bold]● Web Search: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower in ["wakeup_subagents"]:
        _cprint(f"\n[bold]● Waking Subagents[/bold]", source_id=source_id)
    elif name_lower in ["bugs", "run_bugs"]:
        _cprint("\n[bold]● Bugs:[/bold]", source_id=source_id)
    elif name_lower == "search_web":
        _cprint(f"\n[bold]⚙ Web Search: {arg_summary}[/bold]", source_id=source_id)
    elif name_lower == "wakeup_subagents":
        _cprint(f"\n[{ACCENT_COLOR}]● Subagents[/]", source_id=source_id)
    else:
        _cprint(
            f"\n[bold]● Tool ({tool_name}): {arg_summary}[/bold]", source_id=source_id
        )
    import sys

    sys.stdout.flush()


def print_tool_result(
    result_summary: str, escape_text: bool = False, source_id: str = None
):
    if escape_text:
        from rich.markup import escape

        result_summary = escape(result_summary)
    _cprint(
        f"[bright_black]  ⎿  {result_summary}[/bright_black]",
        source_id=source_id,
        highlight=False,
    )


from rich.syntax import Syntax


def print_diff(path: str, old_str: str, new_str: str, source_id: str = None):
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
    diff_lines = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=3))

    if len(diff_lines) > 2:
        diff_lines = diff_lines[2:]

    if not diff_lines:
        print_tool_result(f"No changes detected in {path}", source_id=source_id)
        return

    table = Table(
        show_header=False, box=None, padding=(0, 0), collapse_padding=True, expand=True
    )
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
                    table.add_row(
                        "", Text(f" ... {gap} hidden lines ...", style="bright_black")
                    )
                current_old_line = new_old_line
                current_new_line = start_line - 1 + int(m.group(2))
                last_old_line = current_old_line - 1
            continue
        elif line.startswith("+"):
            table.add_row(
                Text(f" {current_new_line:>3} ", style="bright_black on #1a331a"),
                Text(f" {line[1:]}", style="default on #1a331a"),
            )
            current_new_line += 1
            last_old_line = current_old_line - 1
        elif line.startswith("-"):
            table.add_row(
                Text(f" {current_old_line:>3} ", style="bright_black on #331a1a"),
                Text(f" {line[1:]}", style="default on #331a1a"),
            )
            current_old_line += 1
            last_old_line = current_old_line - 1
        else:
            table.add_row(
                Text(f" {current_new_line:>3} ", style="bright_black"),
                Text(f" {line[1:]}", style="default"),
            )
            current_old_line += 1
            current_new_line += 1
            last_old_line = current_old_line - 1

    panel = Panel(
        table,
        title=f" {path} ",
        title_align="left",
        border_style=ACCENT_COLOR,
        padding=(0, 2),
    )
    _cprint(panel, source_id=source_id)
    import sys

    sys.stdout.flush()


def print_code_panel(
    path: str,
    content: str,
    lexer_override: str = None,
    show_line_numbers: bool = True,
    start_line: int = 1,
    source_id: str = None,
):
    lexer = lexer_override or "text"
    if not lexer_override:
        if path.endswith(".py"):
            lexer = "python"
        elif path.endswith(".js") or path.endswith(".ts"):
            lexer = "javascript"
        elif path.endswith(".html"):
            lexer = "html"
        elif path.endswith(".css"):
            lexer = "css"
        elif path.endswith(".go"):
            lexer = "go"
        elif path.endswith(".json"):
            lexer = "json"

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
        subtitle = (
            f"[gray50]... (and {len(lines) - 1000} more lines)[/gray50]"
            if len(lines) > 1000
            else None
        )
        renderable = Syntax(
            display_content,
            lexer,
            theme="monokai",
            line_numbers=show_line_numbers,
            start_line=start_line,
            word_wrap=True,
        )

    panel = Panel(
        renderable,
        title=f"[bold]{display_title}[/bold]",
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
        border_style=ACCENT_COLOR,
    )
    _cprint(panel, source_id=source_id)
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
    def __init__(
        self,
        expanded=True,
        simulate=False,
        title="Thinking",
        model_name=None,
        source_id=None,
        tree_prefix="",
    ):
        self.expanded = expanded
        self.tree_prefix = tree_prefix
        self.simulate = simulate
        self.title = title
        self.model_name = model_name
        self.source_id = source_id
        self.lines = []
        self.spinner_frames = (
            ["●", "○", "•"] if self.title == "Subagents" else ["⌬", "✻"]
        )
        self.frame_idx = 0
        self.fake_thoughts = [
            "Analyzing prompt and context...",
            "Verifying available system tools...",
            "Designing solution architecture...",
            "Checking syntax and dependencies...",
            "Optimizing logic...",
            "Preparing code implementation...",
        ]
        self.fake_added = 0

        self.start_time = time.time()
        self.live = Live(
            get_renderable=self.render, refresh_per_second=5, transient=False
        )

    def start(self):
        self.start_time = time.time()
        self.live.start()

    def add_line(self, line: str):
        self.simulate = False
        lstripped = line.lstrip()
        spaces = len(line) - len(lstripped)

        import re

        if re.match(r"^(-\s|\*\s|\d+\.\s|[a-zA-Z]\.\s)", lstripped) and spaces < 2:
            spaces = 2
        elif re.match(r"^\d+\.\d+\.\s", lstripped) and spaces < 4:
            spaces = 4

        if re.match(r"^(-\s|\*\s)", lstripped):
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
        prefix = "\n"
        model_str = f" · {self.model_name}" if self.model_name else ""
        if self.title == "Subagents":
            t.append(
                f"{prefix}{self.tree_prefix}{sym} {self.title}\n", style=ACCENT_COLOR
            )
        else:
            t.append(
                f"{prefix}{self.tree_prefix}{sym} {self.title}... ({format_time(elapsed_sec)}{model_str})\n",
                style=ACCENT_COLOR,
            )
        if self.expanded:
            pass  # removed empty line
            for line in self.lines:
                spaces = len(line) - len(line.lstrip())
                indent = " " * spaces
                clean_line = line.strip()
                if clean_line.startswith("|_"):
                    clean_line = clean_line[2:].strip()
                t.append(f"{indent}  |_ {clean_line}\n", style="gray50")
        if self.source_id:
            return PrefixWrapper(t)
        return t

    def update(self):
        pass

    def stop(self):
        self.live.stop()
        import sys

        sys.stdout.flush()
        print("", end="", flush=True)

    def print_tree(self):
        if not self.lines:
            return

        from rich.markup import escape

        elapsed_sec = time.time() - self.start_time
        prefix = "\n" if self.source_id is None else ""
        model_str = f" · {self.model_name}" if self.model_name else ""
        if self.title == "Subagents":
            _cprint(
                f"{prefix}[{ACCENT_COLOR}]{self.tree_prefix}● {self.title}[/]",
                source_id=self.source_id,
            )
        else:
            _cprint(
                f"{prefix}[{ACCENT_COLOR}]{self.tree_prefix}✻ {self.title}... ({format_time(elapsed_sec)}{model_str})[/]",
                source_id=self.source_id,
            )
        if self.expanded:
            pass  # removed empty line
            for line in self.lines:
                spaces = len(line) - len(line.lstrip())
                indent = " " * spaces
                clean_line = line.strip()
                if clean_line.startswith("|_"):
                    clean_line = clean_line[2:].strip()
                _cprint(
                    f"{indent}  [{MUTED_COLOR}]|_ {escape(clean_line)}[/]",
                    source_id=self.source_id,
                )
        _cprint("", source_id=self.source_id)


def print_turn_done(elapsed: float, tokens: int, tool_count: int):
    console.print(
        f"\n[{ACCENT_COLOR}]✔ Done[/] ({format_time(elapsed)} · ⛁ {tokens} tokens · {tool_count} tool calls)"
    )


class LiveToolStream:
    def __init__(self, source_id=None):
        self.source_id = source_id
        self.start_time = time.time()
        self.frames = ["○", "●"]
        self.content = ""
        self.tool_name = "Generating tool"
        self.live = Live(
            get_renderable=self.render, refresh_per_second=10, transient=True
        )

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
                if self.tool_name == "wakeup_subagents":
                    self.tool_name = "Subagents"
                    self.frames = ["●"]

    def render(self):
        from rich.console import Group
        from rich.panel import Panel

        elapsed_sec = time.time() - self.start_time
        frame_idx = int(elapsed_sec * 4) % len(self.frames)
        sym = self.frames[frame_idx]

        t = Text()
        if self.tool_name == "Subagents":
            t.append(
                f"\n{sym} {self.tool_name}... ({format_time(elapsed_sec)})\n",
                style=ACCENT_COLOR,
            )
        else:
            t.append(
                f"\n{sym} {self.tool_name}... ({format_time(elapsed_sec)})\n",
                style="white bold",
            )

        if self.tool_name == "Subagents":
            import re

            blocks = re.split(r'\{\s*"name"\s*:\s*', self.content)
            if len(blocks) > 1 and "wakeup_subagents" in blocks[1]:
                blocks = blocks[2:]
            else:
                blocks = blocks[1:]

            tree_text = ""
            if blocks:
                for block in blocks:
                    m_name = re.match(r'"([^"]+)"', block)
                    if not m_name:
                        continue
                    name = m_name.group(1)

                    role = ""
                    m_task = re.search(r'"task"\s*:\s*"([^"]+)"', block)
                    if m_task:
                        role = m_task.group(1)

                    lvl = ""
                    m_lvl = re.search(r'"thinking_level"\s*:\s*"([^"]+)"', block)
                    if m_lvl:
                        lvl = m_lvl.group(1)

                    role_str = (
                        f" - Role: {role.replace(chr(92) + 'n', ' ')}" if role else ""
                    )
                    lvl_str = f" ({lvl})" if lvl else ""

                    tree_text += f"  [{MUTED_COLOR}]|_ {name}{role_str}{lvl_str}[/{MUTED_COLOR}]\n"
            else:
                tree_text += (
                    f"  [{MUTED_COLOR}]|_ Planning subagents...[/{MUTED_COLOR}]\n"
                )

            t.append(Text.from_markup(tree_text))
            if self.source_id:
                return PrefixWrapper(t)
            return t

        import re

        m = re.search(
            r'"(?:code|content|command|file_content)"\s*:\s*"(.*)',
            self.content,
            re.DOTALL,
        )
        if m:
            preview = m.group(1)

            preview = (
                preview.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
            )

            if preview.endswith('"\n}'):
                preview = preview[:-3]
            elif preview.endswith('"}'):
                preview = preview[:-2]
            elif preview.endswith('"'):
                preview = preview[:-1]

            lines = preview.splitlines()
            if len(lines) > 20:
                preview = "\n".join(lines[-20:])

            path_m = re.search(
                r'"(?:path|TargetFile|file_path|file)"\s*:\s*"([^"]+)"', self.content
            )
            title = f"[bold white]{path_m.group(1)}[/bold white]" if path_m else ""

            from rich import box

            panel = Panel(
                preview,
                border_style="white",
                box=box.ROUNDED,
                title=title,
                title_align="left",
            )
            res = Group(t, panel)
            if self.source_id:
                return PrefixWrapper(res)
            return res

        if self.source_id:
            return PrefixWrapper(t)
        return t

    def stop(self):
        self.live.stop()


class SearchSpinner:
    def __init__(self, query: str, tool_name: str = "Search", source_id: str = None):
        self.query = query
        self.tool_name = tool_name
        self.is_web = "web" in tool_name.lower()
        self.source_id = source_id
        self.start_time = time.time()
        self.frames = ["●", "🌐"] if self.is_web else ["●"]
        self.live = Live(
            get_renderable=self.render, refresh_per_second=10, transient=True
        )

    def start(self):
        self.live.start()

    def render(self):
        elapsed = time.time() - self.start_time
        if self.is_web:
            frame_idx = int(elapsed / 0.6) % 2
            icon = self.frames[frame_idx]
            text = f'Web search: "{self.query}"...'
        else:
            icon = "●"
            clean_name = self.tool_name.replace("_", " ").title()
            text = f'{clean_name}: "{self.query}"...'

        t = Text()
        t.append(f"\n{icon} {text}", style="bold")
        if self.source_id:
            return PrefixWrapper(t)
        return t

    def stop(self, result_summary: str, details: str = ""):
        self.live.stop()
        from rich.markup import escape

        final_icon = "🌐" if self.is_web else "●"
        clean_name = self.tool_name.replace("_", " ").title()
        text = (
            f'Web search: "{escape(self.query)}"'
            if self.is_web
            else f'{clean_name}: "{escape(self.query)}"'
        )

        _cprint(f"\n[bold]{final_icon} {text}[/bold]", source_id=self.source_id)
        _cprint(
            f"  [{MUTED_COLOR}]|_ {escape(result_summary)}[/]", source_id=self.source_id
        )
        if details:
            lines = [
                l for l in details.splitlines() if l.strip() and "Results for" not in l
            ]
            if self.is_web:
                lines = [l for l in lines if "Link:" in l or l.startswith("http")]
            for line in lines[:5]:
                _cprint(
                    f"    [{MUTED_COLOR}]|_ {escape(line)}[/]", source_id=self.source_id
                )
            if len(lines) > 5:
                _cprint(
                    f"    [{MUTED_COLOR}]|_ ... and {len(lines) - 5} more[/]",
                    source_id=self.source_id,
                )


def render_todo(items: list, checked_indices: list):
    from rich.markup import escape

    for i, item in enumerate(items):
        if i in checked_indices:
            console.print(f"[{SUCCESS_COLOR}]☑ {escape(item)}[/]")
        else:
            console.print(f"[{MUTED_COLOR}]☐ {escape(item)}[/]")


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

    style = Style.from_dict(
        {
            "selected": "fg:#00ffff bold",
            "unselected": "fg:#aaaaaa",
            "help": "fg:#666666 italic",
        }
    )

    app = Application(
        layout=Layout(HSplit([Window(content=FormattedTextControl(get_text))])),
        key_bindings=bindings,
        style=style,
        full_screen=False,
    )
    app.run()
    return result
