import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional


def is_tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def run_file_diagnostics(file_path: str, workdir: Optional[str] = None) -> List[Dict[str, Any]]:
    if not os.path.exists(file_path):
        return []

    ext = os.path.splitext(file_path)[1].lower()
    cwd = workdir or os.path.dirname(os.path.abspath(file_path))
    diagnostics: List[Dict[str, Any]] = []

    if ext == ".py":
        if is_tool_available("ruff"):
            try:
                res = subprocess.run(
                    ["ruff", "check", "--output-format=concise", file_path],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in res.stdout.splitlines():
                    clean = line.strip()
                    if clean and ":" in clean:
                        diagnostics.append({"source": "ruff", "message": clean, "severity": "error" if "E" in clean or "F" in clean else "warning"})
            except Exception:
                pass
        elif is_tool_available("pyright"):
            try:
                res = subprocess.run(
                    ["pyright", "--outputjson", file_path],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                import json
                data = json.loads(res.stdout or "{}")
                for d in data.get("generalDiagnostics", []):
                    diagnostics.append({
                        "source": "pyright",
                        "message": f"Line {d.get('range', {}).get('start', {}).get('line', 0) + 1}: {d.get('message', '')}",
                        "severity": d.get("severity", "error"),
                    })
            except Exception:
                pass

    elif ext in (".ts", ".tsx", ".js", ".jsx"):
        if is_tool_available("tsc"):
            try:
                res = subprocess.run(
                    ["tsc", "--noEmit", file_path],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                for line in res.stdout.splitlines():
                    clean = line.strip()
                    if "error TS" in clean:
                        diagnostics.append({"source": "tsc", "message": clean, "severity": "error"})
            except Exception:
                pass

    return diagnostics


def format_diagnostics_summary(diagnostics: List[Dict[str, Any]]) -> str:
    if not diagnostics:
        return ""
    lines = ["[LSP DIAGNOSTICS DETECTED]"]
    for d in diagnostics[:5]:
        lines.append(f"• [{d.get('source', 'lsp')}] {d.get('message', '')}")
    if len(diagnostics) > 5:
        lines.append(f"... and {len(diagnostics) - 5} more diagnostic warnings.")
    return "\n".join(lines)
