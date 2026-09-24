from typing import Any, Dict, List, Optional
from rich.text import Text


from rich.table import Table


def render_tool_header(
    tool_name: str,
    target_summary: str,
    is_expanded: bool = False,
    is_running: bool = False,
    spinner_frame: str = "",
    is_error: bool = False,
) -> Text:
    text = Text()

    tl = tool_name.lower()
    if tl in ("scratch", "todo", "tasks"):
        t_name = "Todo"
        summary = ""
    elif tl in ("bugs", "tool_bugs", "debug", "scan_bugs") or "syntax error" in str(target_summary).lower() or "scanning project" in str(target_summary).lower():
        t_name = "Bugs"
        summary = ""
    elif tl in ("summarizing", "summarize", "compact", "context_compact"):
        t_name = "Summarizing"
        summary = target_summary or ""
    elif tl == "ask":
        t_name = "Ask"
        if target_summary and "question" in target_summary.lower():
            summary = target_summary
        else:
            summary = ""
    else:
        t_name = tool_name.capitalize()
        summary = target_summary

    if is_error:
        glyph = "○  " if is_expanded else "●  "
        text.append(glyph, style="bold #f85149")
        text.append(f"{t_name}", style="bold #f85149")
        if summary and str(summary).strip().lower() not in ("failed", "execution failed"):
            text.append(f" {summary}", style="#ff7b72")
        text.append(" [failed]", style="dim #f85149")
        return text

    is_sum = tl in ("summarizing", "summarize", "compact", "context_compact")
    if is_running:
        glyph = spinner_frame or ("⌬" if is_sum else "●")
        text.append(f"{glyph}  ", style="bold #58a6ff" if is_sum else "bold #8b949e")
        text.append(f"{t_name}", style="bold #8b949e")
        if summary:
            text.append(f" {summary}", style="#c9d1d9")
    elif is_expanded:
        glyph = "⌬  " if is_sum else "○  "
        text.append(glyph, style="bold #58a6ff" if is_sum else "#8b949e")
        if summary:
            text.append(f"{t_name} ", style="bold #8b949e")
            text.append(summary, style="#8b949e")
        else:
            text.append(f"{t_name}", style="bold #8b949e")
    else:
        glyph = "⌬  " if is_sum else "●  "
        text.append(glyph, style="bold #58a6ff" if is_sum else "#8b949e")
        if summary:
            text.append(f"{t_name} ", style="bold #8b949e")
            text.append(summary, style="#8b949e")
        else:
            text.append(f"{t_name}", style="bold #8b949e")

    return text


def render_edit_diff_opencode(diff_entries: List[tuple], target_file: str = "") -> Text:
    output = Text(no_wrap=True)
    if target_file:
        base_name = target_file.split(" (")[0].strip()
        output.append(f"  {base_name}\n", style="bold #58a6ff")

    if not diff_entries:
        output.append("  (No changes detected)\n", style="dim #8b949e")
        return output

    for entry in diff_entries:
        if len(entry) < 3:
            continue
        line_num, entry_type, content = entry[0], entry[1], entry[2]
        c = str(content).replace("\t", "    ")
        line_pad = f"  {line_num:>4}    "

        if entry_type == "del":
            output.append(line_pad, style="#8c3238")
            output.append(c if c else " ", style="#e6edf3 on #3d1418")
            output.append("\n")
        elif entry_type == "add":
            output.append(line_pad, style="#236e37")
            output.append(c if c else " ", style="#e6edf3 on #133a1b")
            output.append("\n")
        else:
            output.append(line_pad, style="#484f58")
            output.append(f"{c}\n", style="#8b949e")

    return output


def render_edit_diff_side_by_side(diff_entries: List[tuple], target_file: str = "") -> Text:
    return render_edit_diff_opencode(diff_entries, target_file)




def render_command_output(stdout: str, stderr: str, cmd: str = "") -> Text:
    output = Text()
    if cmd:
        output.append("  $ ", style="bold #58a6ff")
        output.append(f"{cmd}\n", style="bold #e6edf3")

    lines = (stdout + ("\n" + stderr if stderr else "")).splitlines()
    for line in lines[:30]:
        output.append(f"    {line}\n", style="#8b949e")
    if len(lines) > 30:
        output.append(f"    ... [{len(lines) - 30} lines truncated]\n", style="dim #8b949e")
    return output
