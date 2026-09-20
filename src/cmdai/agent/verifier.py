import ast
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple


def verify_code_syntax(filepath: str, content: str) -> Tuple[bool, str]:
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".py", ".pyw"):
        try:
            ast.parse(content, filename=filepath)
            if os.path.exists(filepath):
                try:
                    from .lsp_client import run_file_diagnostics, format_diagnostics_summary
                    diags = run_file_diagnostics(filepath)
                    error_diags = [d for d in diags if d.get("severity") == "error"]
                    if error_diags:
                        return False, format_diagnostics_summary(error_diags)
                except Exception:
                    pass
            return True, ""
        except SyntaxError as e:
            lineno = e.lineno or 1
            offset = e.offset or 0
            text_line = (e.text or "").strip()
            msg = e.msg or "Syntax error"
            detail = f"SyntaxError in {filepath} at line {lineno} (col {offset}): {msg}"
            if text_line:
                detail += f"\n  -> {text_line}"
            return False, detail
        except Exception as e:
            return False, f"Verification failed for {filepath}: {str(e)}"


    elif ext == ".json":
        try:
            json.loads(content)
            return True, ""
        except json.JSONDecodeError as e:
            return False, f"JSONDecodeError in {filepath} at line {e.lineno} (col {e.colno}): {e.msg}"
        except Exception as e:
            return False, f"JSON verification failed: {str(e)}"

    elif ext == ".toml":
        try:
            try:
                import tomllib
                tomllib.loads(content)
                return True, ""
            except ImportError:
                import tomli
                tomli.loads(content)
                return True, ""
        except Exception as e:
            return False, f"TOML syntax error in {filepath}: {str(e)}"

    elif ext in (".js", ".ts", ".jsx", ".tsx", ".css", ".html"):
        cleaned = re.sub(r'(["\'])(?:(?=(\\?))\2.)*?\1', '', content)
        cleaned = re.sub(r'//.*', '', cleaned)
        cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
        
        stack = []
        pairs = {')': '(', ']': '[', '}': '{'}
        for i, char in enumerate(cleaned):
            if char in "([{":
                stack.append((char, i))
            elif char in ")]}":
                if not stack or stack[-1][0] != pairs[char]:
                    return False, f"Bracket mismatch in {filepath}: unexpected '{char}'"
                stack.pop()
        if stack:
            unmatched = stack[-1][0]
            return False, f"Bracket mismatch in {filepath}: unclosed '{unmatched}'"
        return True, ""

    return True, ""


def scan_project_bugs(workdir: str = ".") -> Dict[str, Any]:
    abs_workdir = os.path.abspath(workdir)
    errors_by_file: Dict[str, List[str]] = {}
    scanned_count = 0

    ignored_dirs = {
        ".git", ".svn", "node_modules", "venv", ".venv", "env",
        "__pycache__", ".pytest_cache", ".cmdai_code_project",
        "dist", "build", "target", ".idea", ".vscode"
    }

    for root, dirs, files in os.walk(abs_workdir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]

        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in (".py", ".json", ".toml"):
                continue

            filepath = os.path.join(root, filename)
            scanned_count += 1
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                
                is_valid, err_msg = verify_code_syntax(filepath, content)
                if not is_valid:
                    rel_p = os.path.relpath(filepath, abs_workdir)
                    errors_by_file[rel_p] = [err_msg]
            except Exception as e:
                rel_p = os.path.relpath(filepath, abs_workdir)
                errors_by_file[rel_p] = [f"File read error: {str(e)}"]

    return {
        "scanned_files": scanned_count,
        "error_files": len(errors_by_file),
        "errors": errors_by_file,
    }
