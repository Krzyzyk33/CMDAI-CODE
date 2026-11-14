"""Project-local custom slash commands (.cmdai/commands/*.md) and lifecycle hooks (.cmdai/hooks.json)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


COMMANDS_REL_DIR = os.path.join(".cmdai", "commands")
HOOKS_REL_PATH = os.path.join(".cmdai", "hooks.json")


def _safe_command_slug(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or "").strip().lstrip("/")).strip("-").lower()
    return clean or "custom-cmd"


def list_custom_commands(workdir: str) -> List[Dict[str, str]]:
    """Return custom commands discovered in `<workdir>/.cmdai/commands/*.md`."""
    cmd_dir = Path(workdir) / ".cmdai" / "commands"
    if not cmd_dir.is_dir():
        return []
    out: List[Dict[str, str]] = []
    for p in sorted(cmd_dir.glob("*.md")):
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        first_line = next((ln.strip().lstrip("#").strip() for ln in body.splitlines() if ln.strip()), "")
        out.append({
            "name": "/" + p.stem.lower(),
            "slug": p.stem.lower(),
            "path": str(p),
            "description": first_line[:80] or "Custom command",
            "body": body,
        })
    return out


def get_custom_command_body(workdir: str, cmd_name: str, arg: str = "") -> Optional[str]:
    """Load and expand a custom slash command (`$ARGUMENTS` placeholder supported)."""
    slug = _safe_command_slug(cmd_name)
    p = Path(workdir) / ".cmdai" / "commands" / f"{slug}.md"
    if not p.is_file():
        return None
    body = p.read_text(encoding="utf-8", errors="replace").strip()
    if "$ARGUMENTS" in body:
        return body.replace("$ARGUMENTS", arg or "")
    return f"{body}\n\n{arg}".strip() if arg else body


def save_custom_command(workdir: str, name: str, content: str) -> str:
    """Save a custom command markdown file under `.cmdai/commands/<slug>.md`."""
    slug = _safe_command_slug(name)
    cmd_dir = Path(workdir) / ".cmdai" / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    target = cmd_dir / f"{slug}.md"
    target.write_text(content.rstrip() + "\n", encoding="utf-8")
    return str(target)


def load_hooks(workdir: str) -> Dict[str, Any]:
    """Load `.cmdai/hooks.json`, returning a normalized dict with `PostToolUse`."""
    p = Path(workdir) / ".cmdai" / "hooks.json"
    if not p.is_file():
        return {"PostToolUse": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("PostToolUse", [])
            return data
    except Exception:
        pass
    return {"PostToolUse": []}


def save_hooks(workdir: str, hooks_data: Dict[str, Any]) -> str:
    """Persist `.cmdai/hooks.json`."""
    p = Path(workdir) / ".cmdai" / "hooks.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hooks_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(p)


def add_post_tool_hook(workdir: str, matcher: str, command: str) -> str:
    """Append a `PostToolUse` hook rule and save `.cmdai/hooks.json`."""
    data = load_hooks(workdir)
    hooks_list = data.setdefault("PostToolUse", [])
    hooks_list.append({
        "matcher": (matcher or "edit|write").strip(),
        "command": command.strip(),
    })
    return save_hooks(workdir, data)


def run_post_tool_hooks(
    workdir: str,
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
) -> List[Dict[str, Any]]:
    """Run matching `PostToolUse` hooks for `tool_name` and return execution summaries."""
    hooks = load_hooks(workdir).get("PostToolUse", [])
    if not hooks or not isinstance(hooks, list):
        return []
    tl = (tool_name or "").strip().lower()
    results: List[Dict[str, Any]] = []
    for item in hooks:
        if not isinstance(item, dict):
            continue
        matcher = str(item.get("matcher", "*")).strip()
        cmd = str(item.get("command", "")).strip()
        if not cmd:
            continue
        try:
            if matcher != "*" and not re.fullmatch(matcher, tl, flags=re.IGNORECASE):
                continue
        except re.error:
            if matcher.lower() != tl:
                continue
        env = os.environ.copy()
        env["CMDAI_TOOL_NAME"] = tl
        if args and isinstance(args, dict):
            env["CMDAI_TOOL_PATH"] = str(args.get("path", "") or "")
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            results.append({
                "command": cmd,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-1000:],
                "stderr": (proc.stderr or "")[-1000:],
            })
        except Exception as exc:
            results.append({
                "command": cmd,
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
            })
    return results


def build_ai_extension_prompt(kind: str, description: str) -> str:
    """Create a prompt instructing the agent to generate a custom command or hook."""
    if kind == "hook":
        return (
            "Create or update `.cmdai/hooks.json` in the current project with a `PostToolUse` hook.\n"
            'Schema: `{"PostToolUse": [{"matcher": "edit|write", "command": "..."}]}`.\n'
            f"User requirement: {description}\n"
            "Write the valid JSON file now and briefly confirm what was created."
        )
    return (
        "Create a custom slash command markdown file under `.cmdai/commands/<name>.md` in the current project.\n"
        "Use a short lowercase filename slug (e.g., `review.md` for `/review`) and `$ARGUMENTS` if parameters apply.\n"
        f"User requirement: {description}\n"
        "Write the `.md` file now and tell the user the exact `/command` name."
    )
