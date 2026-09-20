import html
import os
import re
import time
from typing import Any, Dict, List, Optional


HTML_EXPORTER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CMDAI CODE — Session {session_id}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-body: #090d13;
            --bg-card: #121820;
            --bg-subtle: #182230;
            --bg-code: #0d1117;
            --border: #263345;
            --border-highlight: #388bfd;
            --text-main: #f0f6fc;
            --text-muted: #8b949e;
            --accent-blue: #58a6ff;
            --accent-green: #3fb950;
            --accent-purple: #bc8cff;
            --accent-orange: #f0883e;
            --accent-red: #f85149;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-body);
            color: var(--text-main);
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            line-height: 1.6;
            padding: 32px 16px;
        }}
        .container {{
            max-width: 1020px;
            margin: 0 auto;
        }}
        header {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            gap: 16px;
        }}
        .brand {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .brand-icon {{
            font-family: 'Fira Code', monospace;
            font-size: 24px;
            color: var(--accent-blue);
            font-weight: bold;
        }}
        .brand-title {{
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.5px;
        }}
        .meta-tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 10px;
        }}
        .tag {{
            font-family: 'Fira Code', monospace;
            font-size: 11px;
            padding: 4px 10px;
            border-radius: 6px;
            background: var(--bg-subtle);
            border: 1px solid var(--border);
            color: var(--text-muted);
        }}
        .tag-active {{
            color: var(--accent-blue);
            border-color: rgba(88, 166, 255, 0.4);
        }}
        .stats-badge {{
            display: flex;
            gap: 16px;
            background: var(--bg-subtle);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px 16px;
        }}
        .stat-item {{
            display: flex;
            flex-direction: column;
            align-items: flex-end;
        }}
        .stat-val {{
            font-family: 'Fira Code', monospace;
            font-weight: bold;
            font-size: 15px;
            color: var(--text-main);
        }}
        .stat-label {{
            font-size: 10px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .timeline {{
            display: flex;
            flex-direction: column;
            gap: 20px;
        }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            position: relative;
        }}
        .user-card {{
            border-left: 3px solid var(--accent-blue);
        }}
        .assistant-card {{
            border-left: 3px solid var(--accent-green);
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }}
        .card-sender {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-weight: 600;
            font-size: 14px;
        }}
        .user-badge {{ color: var(--accent-blue); }}
        .assistant-badge {{ color: var(--accent-green); font-family: 'Fira Code', monospace; }}
        .card-time {{
            font-size: 11px;
            color: var(--text-muted);
            font-family: 'Fira Code', monospace;
        }}
        .card-body {{
            font-size: 14px;
            color: var(--text-main);
            word-break: break-word;
        }}
        /* Thinking Block */
        details.thinking-block {{
            background: var(--bg-subtle);
            border: 1px solid var(--border);
            border-radius: 8px;
            margin: 12px 0;
            overflow: hidden;
        }}
        details.thinking-block summary {{
            cursor: pointer;
            padding: 10px 14px;
            font-family: 'Fira Code', monospace;
            font-size: 12px;
            color: var(--text-muted);
            user-select: none;
            outline: none;
        }}
        details.thinking-block summary:hover {{
            color: var(--text-main);
        }}
        .thinking-content {{
            padding: 12px 16px;
            border-top: 1px solid var(--border);
            font-size: 13px;
            color: #adbac7;
            background: #0d1117;
            white-space: pre-wrap;
            font-family: 'Fira Code', monospace;
            max-height: 400px;
            overflow-y: auto;
        }}
        /* Tool Block */
        details.tool-block {{
            background: #111620;
            border: 1px solid #1f2a38;
            border-radius: 8px;
            margin: 10px 0;
        }}
        details.tool-block summary {{
            cursor: pointer;
            padding: 8px 12px;
            font-family: 'Fira Code', monospace;
            font-size: 12px;
            color: #8b949e;
        }}
        .tool-content {{
            padding: 10px 14px;
            border-top: 1px solid #1f2a38;
            background: #090d13;
            font-family: 'Fira Code', monospace;
            font-size: 12px;
            white-space: pre-wrap;
            overflow-x: auto;
            color: #7ee787;
        }}
        pre code {{
            display: block;
            background: var(--bg-code);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px;
            font-family: 'Fira Code', monospace;
            font-size: 13px;
            overflow-x: auto;
            margin: 12px 0;
            color: #e6edf3;
        }}
        code:not(pre code) {{
            font-family: 'Fira Code', monospace;
            font-size: 12px;
            background: var(--bg-subtle);
            padding: 2px 6px;
            border-radius: 4px;
            color: var(--accent-orange);
        }}
        footer {{
            text-align: center;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid var(--border);
            font-size: 12px;
            color: var(--text-muted);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <div class="brand">
                    <span class="brand-icon">⌬</span>
                    <span class="brand-title">CMDAI CODE</span>
                </div>
                <div class="meta-tags">
                    <span class="tag tag-active">Session: {session_id}</span>
                    <span class="tag">Model: {model_id}</span>
                    <span class="tag">Workspace: {workdir}</span>
                    <span class="tag">Date: {timestamp}</span>
                </div>
            </div>
            <div class="stats-badge">
                <div class="stat-item">
                    <span class="stat-val">{tokens_in:,}</span>
                    <span class="stat-label">Tokens In</span>
                </div>
                <div class="stat-item">
                    <span class="stat-val">{tokens_out:,}</span>
                    <span class="stat-label">Tokens Out</span>
                </div>
                <div class="stat-item">
                    <span class="stat-val">{speed:.1f} t/s</span>
                    <span class="stat-label">Avg Speed</span>
                </div>
                <div class="stat-item">
                    <span class="stat-val">{tools_count}</span>
                    <span class="stat-label">Tools Run</span>
                </div>
            </div>
        </header>

        <main class="timeline">
            {timeline_cards}
        </main>

        <footer>
            Exported by <strong>CMDAI CODE</strong> — Autonomous AI Coding Agent · {timestamp}
        </footer>
    </div>
</body>
</html>
"""


def _format_markdown_simple(text: str) -> str:
    escaped = html.escape(text)

    def _code_block(match):
        code_body = match.group(2)
        return f'<pre><code>{code_body.strip()}</code></pre>'

    escaped = re.sub(r"```(\w+)?\n([\s\S]*?)```", _code_block, escaped)

    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)

    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)

    parts = escaped.split("<pre>")
    out = []
    for i, part in enumerate(parts):
        if i == 0:
            out.append(part.replace("\n", "<br>"))
        else:
            sub = part.split("</pre>")
            out.append("<pre>" + sub[0] + "</pre>" + (sub[1].replace("\n", "<br>") if len(sub) > 1 else ""))
    return "".join(out)


def export_session_to_html(
    session_id: str,
    messages: List[Dict[str, Any]],
    stats: Dict[str, Any],
    model_id: str,
    workdir: str,
    output_path: Optional[str] = None,
) -> str:
    if not output_path:
        out_dir = os.path.join(workdir, "exports")
        os.makedirs(out_dir, exist_ok=True)
        safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_")) or "session"
        output_path = os.path.join(out_dir, f"cmdai_export_{safe_id}_{int(time.time())}.html")
    else:
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)

    cards = []
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    for idx, msg in enumerate(messages, 1):
        role = msg.get("role", "user")
        raw_content = str(msg.get("content", ""))

        if role == "user":
            formatted_body = _format_markdown_simple(raw_content)
            cards.append(f"""
            <div class="card user-card">
                <div class="card-header">
                    <div class="card-sender user-badge">
                        <span>●</span> User
                    </div>
                    <div class="card-time">Turn #{idx}</div>
                </div>
                <div class="card-body">
                    {formatted_body}
                </div>
            </div>
            """)
        elif role == "assistant":
            thinking_html = ""
            thinking = msg.get("thinking") or ""
            if not thinking and "<think>" in raw_content:
                m = re.search(r"<think>([\s\S]*?)</think>", raw_content)
                if m:
                    thinking = m.group(1)
                    raw_content = re.sub(r"<think>[\s\S]*?</think>", "", raw_content)

            if thinking:
                thinking_html = f"""
                <details class="thinking-block">
                    <summary>✻ Thought process (click to inspect)</summary>
                    <div class="thinking-content">{html.escape(thinking.strip())}</div>
                </details>
                """

            tools_html = ""
            tools = msg.get("tools") or []
            if tools:
                tool_items = []
                for t in tools:
                    t_name = html.escape(str(t.get("name", "tool")))
                    t_arg = html.escape(str(t.get("target", "")))
                    t_out = html.escape(str(t.get("output", "")))
                    tool_items.append(f"""
                    <details class="tool-block">
                        <summary>● tool:{t_name} <span style="color:#e6edf3">{t_arg}</span></summary>
                        <div class="tool-content">{t_out}</div>
                    </details>
                    """)
                tools_html = "".join(tool_items)

            formatted_body = _format_markdown_simple(raw_content)
            cards.append(f"""
            <div class="card assistant-card">
                <div class="card-header">
                    <div class="card-sender assistant-badge">
                        <span>⌬</span> {html.escape(model_id or "Assistant")}
                    </div>
                    <div class="card-time">Turn #{idx}</div>
                </div>
                {thinking_html}
                {tools_html}
                <div class="card-body">
                    {formatted_body}
                </div>
            </div>
            """)

    rendered_html = HTML_EXPORTER_TEMPLATE.format(
        session_id=html.escape(session_id),
        model_id=html.escape(model_id),
        workdir=html.escape(workdir),
        timestamp=now_str,
        tokens_in=stats.get("tokens_in", 0),
        tokens_out=stats.get("tokens_out", 0),
        speed=stats.get("tok_per_sec", 0.0),
        tools_count=stats.get("tools_run", 0),
        timeline_cards="\n".join(cards) if cards else "<div class='card'><div class='card-body'>No messages in session.</div></div>",
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    return os.path.abspath(output_path)
