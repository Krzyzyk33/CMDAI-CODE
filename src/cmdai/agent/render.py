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
    display_name: str = "",
    elapsed_str: str = "",
    elapsed_s: Optional[float] = None,
) -> Text:
    if elapsed_s is not None and not elapsed_str:
        elapsed_str = f"{elapsed_s:.1f}s"
    text = Text()

    raw_tool = str(tool_name or "")
    if raw_tool.lower().startswith("tool:"):
        raw_tool = raw_tool[5:]
    tl = raw_tool.lower()
    if display_name:
        # Subagents render as their own role instead of the literal tool name,
        # so the chat line reads "<glyph> <role> <tool> <arg>".
        t_name = str(display_name).strip() or tool_name.capitalize()
        summary = target_summary
        if summary:
            ws_len = len(summary) - len(summary.lstrip())
            lead_ws = summary[:ws_len]
            rest = summary.lstrip()
            rest_low = rest.lower()
            for dup_prefix, clean_prefix in (
                ("read read file ", "read "),
                ("read reading file ", "read "),
                ("read read ", "read "),
                ("read reading ", "read "),
                ("read file ", "read "),
                ("edit edit file ", "edit "),
                ("edit editing file ", "edit "),
                ("edit edit ", "edit "),
                ("edit file ", "edit "),
                ("write write file ", "write "),
                ("write writing file ", "write "),
                ("write write ", "write "),
                ("write file ", "write "),
            ):
                if rest_low.startswith(dup_prefix):
                    rest = clean_prefix + rest[len(dup_prefix):].strip()
                    break
            summary = lead_ws + rest
    elif tl in ("scratch", "todo", "tasks"):
        t_name = "Todo"
        summary = ""
    elif tl in ("bugs", "tool_bugs", "debug", "scan_bugs") or "syntax error" in str(target_summary).lower() or "scanning project" in str(target_summary).lower():
        t_name = "Bugs"
        summary = ""
    elif tl.startswith("mcp__"):
        parts = tl[5:].split("__", 1)
        if len(parts) == 2:
            server_part, tool_part = parts
            t_name = f"MCP: {server_part} → {tool_part}"
        else:
            t_name = f"MCP: {tl[5:]}"
        summary = target_summary or ""
    elif tl in ("mcp", "mcps"):
        t_name = "MCP"
        summary = target_summary or ""
    elif tl in ("summarizing", "summarize", "compact", "context_compact"):
        t_name = "Summarizing"
        summary = target_summary or ""
    elif tl in ("agent_note", "note"):
        t_name = "Agent note"
        summary = target_summary
    elif tl == "ask":
        t_name = "Ask"
        if target_summary and "question" in target_summary.lower():
            summary = target_summary
        else:
            summary = ""
    elif tl in ("command", "shell", "bash", "terminal", "sh", "cmd", "run_command", "exec"):
        s_raw = str(target_summary or "").strip()
        is_bg = "background" in s_raw.lower()
        t_name = "Terminal background" if is_bg else "Terminal"
        summary = s_raw
        if is_bg:
            summary = summary.replace("(background)", "").replace("background", "").strip()
        if summary:
            s_stripped = summary.strip()
            s_low = s_stripped.lower()
            for prefix in ("run command ", "running command ", "execute command ", "executing command ", "terminal ", "shell ", "command ", "bash "):
                if s_low.startswith(prefix):
                    summary = s_stripped[len(prefix):].strip()
                    break
    elif tl == "ls":
        t_name = "Ls"
        summary = target_summary
        if summary:
            s_stripped = summary.strip()
            s_low = s_stripped.lower()
            for prefix in ("list files ", "listing files ", "ls "):
                if s_low.startswith(prefix):
                    summary = s_stripped[len(prefix):].strip()
                    break
    elif tl in ("web", "fetch", "webfetch", "web_fetch"):
        t_name = "Webfetch"
        summary = target_summary or ""
        if summary:
            import re as _re
            url_m = _re.search(r"https?://[^\s'\"`\\]+", str(summary))
            if url_m:
                summary = url_m.group(0)
    elif tl in ("execute", "eval", "evaluate") or (target_summary and str(target_summary).strip().startswith('{"code":')):
        import re as _re
        raw_s = str(target_summary or "")
        url_m = _re.search(r"https?://[^\s'\"`\\]+", raw_s)
        if ("fetch" in raw_s.lower() or "http" in raw_s.lower()) and url_m:
            t_name = "Webfetch"
            summary = url_m.group(0)
        elif any(k in raw_s.lower() for k in ("child_process", "exec(", "spawn(", "execsync", "bash", "cmd")):
            t_name = "Terminal"
            cmd_m = _re.search(r"(?:exec|spawn|execSync)\(\s*['\"]([^'\"]+)['\"]", raw_s)
            summary = cmd_m.group(1) if cmd_m else "command"
        else:
            t_name = "Script"
            summary = ""
        is_error = True
    else:
        t_name = tool_name.capitalize()
        summary = target_summary
        if t_name.lower() in ("execute", "eval", "evaluate"):
            t_name = "Script"
            is_error = True
            summary = ""
        if summary:
            s_stripped = summary.strip()
            if s_stripped.startswith("(in "):
                summary = ""
                s_stripped = ""
            s_low = s_stripped.lower()
            if tl == "read":
                for prefix in ("read file ", "reading file ", "read ", "reading "):
                    if s_low.startswith(prefix):
                        summary = s_stripped[len(prefix):].strip()
                        break
            elif tl in ("edit", "write"):
                for prefix in (f"{tl} file ", f"{tl}ing file ", f"{tl} "):
                    if s_low.startswith(prefix):
                        summary = s_stripped[len(prefix):].strip()
                        break
            elif s_low.startswith(f"{tl} "):
                summary = s_stripped[len(tl) + 1:].strip()

    TIMED_TOOLS = {
        "write", "file_write", "edit", "file_edit",
        "terminal", "command", "shell", "bash", "sh", "cmd", "run_command", "exec",
        "subagent", "task",
    }
    # Only show time if elapsed_str is non-empty, and for finished tools avoid showing a misleading "0.0s"
    is_zero_sec = (elapsed_str.strip() == "0.0s" or (elapsed_s is not None and elapsed_s < 0.05))
    should_show_time = bool(elapsed_str) and (tl in TIMED_TOOLS or bool(display_name)) and (is_running or not is_zero_sec)

    if is_error:
        glyph = "○  " if is_expanded else "●  "
        text.append(glyph, style="bold #f85149")
        text.append(f"{t_name}", style="bold #f85149")
        if summary and str(summary).strip().lower() not in ("failed", "execution failed"):
            text.append(f" {summary}", style="#ff7b72")
        text.append(" [failed]", style="dim #f85149")
        if should_show_time:
            text.append(f"  {elapsed_str}", style="dim #f85149")
        return text

    is_sum = tl in ("summarizing", "summarize", "compact", "context_compact")
    if is_running:
        glyph = spinner_frame or ("⌬" if is_sum else "●")
        # Full and empty circle animation: '●' and '○' in bright active #58a6ff
        g_style = "bold #58a6ff"
        text.append(f"{glyph}  ", style=g_style)
        text.append(f"{t_name}", style="bold #e6edf3")
        if summary:
            text.append(f" {summary}", style="#c9d1d9")
        if should_show_time:
            text.append(f"  {elapsed_str}", style="#58a6ff")
    elif is_expanded:
        glyph = "⌬  " if is_sum else "○  "
        text.append(glyph, style="bold #58a6ff" if is_sum else "#8b949e")
        if summary:
            text.append(f"{t_name} ", style="bold #8b949e")
            text.append(summary, style="#8b949e")
        else:
            text.append(f"{t_name}", style="bold #8b949e")
        if should_show_time:
            text.append(f"  {elapsed_str}", style="dim #8b949e")
    else:
        glyph = "⌬  " if is_sum else "●  "
        text.append(glyph, style="bold #58a6ff" if is_sum else "#8b949e")
        if summary:
            text.append(f"{t_name} ", style="bold #8b949e")
            text.append(summary, style="#8b949e")
        else:
            text.append(f"{t_name}", style="bold #8b949e")
        if should_show_time:
            text.append(f"  {elapsed_str}", style="dim #8b949e")

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
    out_s = (stdout or "").strip()
    err_s = (stderr or "").strip()

    # If stdout merely echos the command line invocation itself, strip that echo
    if cmd and cmd.strip():
        c_clean = cmd.strip()
        if out_s == c_clean:
            out_s = ""
        elif out_s.startswith(c_clean) and len(out_s) > len(c_clean):
            # If the output starts with the command line followed by newline, remove that echo
            rest = out_s[len(c_clean):].lstrip("\r\n")
            out_s = rest.strip()

    combined = out_s
    if err_s:
        combined = (combined + "\n" + err_s) if combined else err_s

    lines = combined.splitlines() if combined else []
    if lines:
        for line in lines[:50]:
            clean_l = str(line).replace("\t", "    ")
            output.append(f"  {clean_l}\n", style="#e6edf3")
        if len(lines) > 50:
            output.append(f"  ... [{len(lines) - 50} lines truncated]\n", style="dim #8b949e")
    else:
        output.append("  (No output)\n", style="dim #8b949e")
    return output


