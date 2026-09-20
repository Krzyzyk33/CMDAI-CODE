import glob as py_glob
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import requests

from .verifier import verify_code_syntax
from .test_runner import run_project_tests


class AgentContext:
    def __init__(self, workdir: str = "."):
        self.workdir = os.path.abspath(workdir)
        self.todo_list: List[str] = []
        self.mode: str = "auto"
        self.pending_images: List[Dict[str, str]] = []

    def resolve_path(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.normpath(os.path.join(self.workdir, path))

    def rel_path(self, path: str) -> str:
        try:
            return os.path.relpath(path, self.workdir)
        except Exception:
            return path


def tool_read(ctx: AgentContext, path: str, lines: Optional[str] = None) -> Dict[str, Any]:
    full_path = ctx.resolve_path(path)
    if not os.path.exists(full_path):
        return {"success": False, "error": f"File not found: {path}"}

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        total = len(all_lines)
        if lines:
            parts = lines.split("-")
            start = max(1, int(parts[0]))
            end = min(total, int(parts[1])) if len(parts) > 1 else start
            selected = all_lines[start - 1 : end]
            content = "".join(selected)
            return {
                "success": True,
                "path": ctx.rel_path(full_path),
                "content": content,
                "line_range": f"{start}-{end}",
                "total_lines": total,
            }
        else:
            return {
                "success": True,
                "path": ctx.rel_path(full_path),
                "content": "".join(all_lines),
                "total_lines": total,
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_edit(
    ctx: AgentContext, path: str, old: str, new: str
) -> Dict[str, Any]:
    full_path = ctx.resolve_path(path)
    if not os.path.exists(full_path):
        return {"success": False, "error": f"File not found: {path}"}

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if old not in content:
            return {
                "success": False,
                "error": f"Target chunk <old> does not match file content in '{path}' (don't match). Ensure exact matching lines and indentation, or use <tool:read> first.",
            }

        new_content = content.replace(old, new, 1)

        old_lines = content.splitlines()
        replaced_lines = new_content.splitlines()

        char_idx = content.find(old)
        start_line_num = content[:char_idx].count("\n") + 1

        old_chunk_lines = old.splitlines()
        new_chunk_lines = new.splitlines()

        diff_entries: List[Tuple[int, str, str]] = []

        ctx_before_start = max(1, start_line_num - 2)
        for i in range(ctx_before_start, start_line_num):
            if i <= len(old_lines):
                diff_entries.append((i, "ctx", old_lines[i - 1]))

        for idx, line in enumerate(old_chunk_lines):
            diff_entries.append((start_line_num + idx, "del", line))

        for idx, line in enumerate(new_chunk_lines):
            diff_entries.append((start_line_num + idx, "add", line))

        after_start = start_line_num + len(old_chunk_lines)
        ctx_after_end = min(len(old_lines), after_start + 2)
        for i in range(after_start, ctx_after_end + 1):
            if i <= len(old_lines):
                diff_entries.append((i, "ctx", old_lines[i - 1]))

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        is_valid, verify_err = verify_code_syntax(full_path, new_content)
        if not is_valid:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {
                "success": False,
                "error": f"Syntax verification failed: {verify_err}",
                "path": ctx.rel_path(full_path),
                "verification": "FAILED",
                "verification_error": verify_err,
            }

        test_res = run_project_tests(ctx.workdir, target_file=full_path)
        test_status = ""
        if test_res.get("has_tests"):
            if test_res["passed"]:
                test_status = f"[AUTO-TEST: PASSED] {test_res.get('summary', '')}"
            else:
                test_status = (
                    f"⚠️ [AUTO-TEST: FAILURE DETECTED]\n"
                    f"Runner: {test_res.get('runner', 'tests')}\n"
                    f"{test_res.get('output', '')}\n\n"
                    "CRITICAL AUTONOMOUS REPAIR DIRECTIVE: The code edit caused test failures above. "
                    "Analyze the error output, locate the bug, and immediately use <tool:edit> or <tool:write> to repair the code until tests pass!"
                )

        res_dict = {
            "success": True,
            "path": ctx.rel_path(full_path),
            "diff_entries": diff_entries,
            "verification": "OK",
            "verification_error": "",
            "test_result": test_res,
        }
        if test_status:
            res_dict["test_status"] = test_status
        return res_dict
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_write(ctx: AgentContext, path: str, content: str) -> Dict[str, Any]:
    full_path = ctx.resolve_path(path)
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

        is_valid, verify_err = verify_code_syntax(full_path, content)
        if not is_valid:
            return {
                "success": False,
                "error": f"Syntax verification failed: {verify_err}",
                "path": ctx.rel_path(full_path),
                "verification": "FAILED",
                "verification_error": verify_err,
            }

        test_res = run_project_tests(ctx.workdir, target_file=full_path)
        test_status = ""
        if test_res.get("has_tests"):
            if test_res["passed"]:
                test_status = f"[AUTO-TEST: PASSED] {test_res.get('summary', '')}"
            else:
                test_status = (
                    f"⚠️ [AUTO-TEST: FAILURE DETECTED]\n"
                    f"Runner: {test_res.get('runner', 'tests')}\n"
                    f"{test_res.get('output', '')}\n\n"
                    "CRITICAL AUTONOMOUS REPAIR DIRECTIVE: The written file caused test failures above. "
                    "Analyze the error output, locate the bug, and immediately use <tool:edit> or <tool:write> to repair the code until tests pass!"
                )

        res_dict = {
            "success": True,
            "path": ctx.rel_path(full_path),
            "lines": len(content.splitlines()),
            "content": content,
            "verification": "OK",
            "verification_error": "",
            "test_result": test_res,
        }
        if test_status:
            res_dict["test_status"] = test_status
        return res_dict

    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_ls(ctx: AgentContext, path: str = ".") -> Dict[str, Any]:
    full_path = ctx.resolve_path(path)
    if not os.path.exists(full_path):
        return {"success": False, "error": f"Path not found: {path}"}

    IGNORED_SUBDIRS = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".cmdai_code_project", ".gemini"}

    try:
        raw_items = []
        with os.scandir(full_path) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    size = None if is_dir else entry.stat(follow_symlinks=False).st_size
                except Exception:
                    is_dir = False
                    size = None
                raw_items.append((entry, is_dir, size))

        raw_items.sort(key=lambda x: (not x[1], x[0].name.lower()))

        entries = []
        for entry, is_dir, size in raw_items[:60]:
            children = []
            if is_dir and entry.name not in IGNORED_SUBDIRS:
                try:
                    with os.scandir(entry.path) as sub_it:
                        for sub in sub_it:
                            sub_is_dir = sub.is_dir(follow_symlinks=False)
                            children.append({
                                "name": sub.name + ("/" if sub_is_dir else ""),
                                "is_dir": sub_is_dir,
                            })
                            if len(children) >= 4:
                                break
                except Exception:
                    pass

            entries.append({
                "name": entry.name,
                "type": "dir" if is_dir else "file",
                "children": children,
                "size": size,
            })

        return {
            "success": True,
            "path": ctx.rel_path(full_path),
            "entries": entries,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_glob(ctx: AgentContext, pattern: str = "*", dir: str = ".") -> Dict[str, Any]:
    full_dir = ctx.resolve_path(dir)
    search_pattern = os.path.join(full_dir, pattern)
    try:
        matches = py_glob.glob(search_pattern, recursive=True)
        rel_matches = [ctx.rel_path(m) for m in matches[:100]]
        return {
            "success": True,
            "pattern": pattern,
            "matches": rel_matches,
            "count": len(rel_matches),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_search(ctx: AgentContext, query: str, path: str = ".") -> Dict[str, Any]:
    full_path = ctx.resolve_path(path)
    results = []
    try:
        for root, _, files in os.walk(full_path):
            if any(ign in root for ign in [".git", "node_modules", ".venv", "__pycache__"]):
                continue
            for f in files:
                f_path = os.path.join(root, f)
                try:
                    with open(f_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                        for line_num, line in enumerate(file_obj, 1):
                            if query in line:
                                results.append({
                                    "file": ctx.rel_path(f_path),
                                    "line": line_num,
                                    "content": line.strip(),
                                })
                                if len(results) >= 50:
                                    break
                except Exception:
                    continue
                if len(results) >= 50:
                    break
        return {"success": True, "query": query, "results": results}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_command(ctx: AgentContext, cmd: str, timeout: int = 30) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=ctx.workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return {
            "success": proc.returncode == 0,
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "cmd": cmd, "error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "cmd": cmd, "error": str(e)}


def tool_web(ctx: AgentContext, url_or_query: str) -> Dict[str, Any]:
    target = url_or_query.strip()
    if target.startswith("http://") or target.startswith("https://"):
        try:
            resp = requests.get(
                target,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=3.5,
            )
            html = resp.text
            html = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<style.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()
            return {"success": True, "url": target, "content": text[:4000]}
        except Exception as e:
            return {"success": False, "url": target, "error": str(e)}
    else:
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            with DDGS(timeout=4) as ddgs:
                results = list(ddgs.text(target, max_results=3))

            if not results:
                return {
                    "success": True,
                    "query": target,
                    "content": f"No web search results found for: \"{target}\"",
                    "results": [],
                }

            formatted_results = []
            output_parts = [f"Web search results for: \"{target}\":\n"]
            for i, res in enumerate(results, 1):
                title = res.get("title", "No title").strip()
                href = res.get("href", "").strip()
                raw_body = res.get("body") or ""
                clean_body = re.sub(r"\s+", " ", raw_body).strip()
                if len(clean_body) > 220:
                    truncated = clean_body[:220].rsplit(" ", 1)[0].rstrip(".,;:-") + "..."
                else:
                    truncated = clean_body
                formatted_results.append({"title": title, "url": href, "snippet": truncated})
                output_parts.append(f"{i}. {title}\n   Link: {href}\n   Snippet: {truncated}\n")

            return {
                "success": True,
                "query": target,
                "content": "\n".join(output_parts),
                "results": formatted_results,
            }
        except Exception as e:
            return {"success": False, "query": target, "error": f"Web search timeout or error: {str(e)}"}


def tool_bugs(ctx: AgentContext) -> Dict[str, Any]:
    from .verifier import scan_project_bugs
    report = scan_project_bugs(ctx.workdir)
    return {
        "success": True,
        "scanned_files": report["scanned_files"],
        "errors": report["errors"],
    }


def tool_code_search(ctx: AgentContext, query: str) -> Dict[str, Any]:
    try:
        from ..indexer.ast_indexer import ProjectIndexer
        indexer = ProjectIndexer(ctx.workdir)
        results = indexer.search(query, limit=20)
        if results:
            return {
                "success": True,
                "query": query,
                "symbols": results,
                "count": len(results),
            }
    except Exception:
        pass
    return tool_search(ctx, query)


def tool_scratch(
    ctx: AgentContext,
    action: str,
    note: str = "",
    index: Optional[int] = None,
    new_text: str = "",
    todos: Optional[str] = None,
) -> Dict[str, Any]:
    action_lower = action.lower().strip()
    if action_lower in ("add", "push"):
        if note:
            parts = re.split(r'(?:\r?\n|\s+)(?=\d+[\.\)]\s+)', note.strip())
            if len(parts) > 1:
                for p in parts:
                    clean_p = p.strip().rstrip(".;")
                    clean_p = re.sub(r'^(?:plan\s*(?:prac)?|todo|zadania)\s*:\s*', '', clean_p, flags=re.IGNORECASE).strip()
                    if clean_p:
                        ctx.todo_list.append({"text": clean_p, "done": False})
            else:
                ctx.todo_list.append({"text": note.strip(), "done": False})
    elif action_lower in ("set", "init"):
        raw_todos = todos or note
        if raw_todos:
            items = [t.strip() for t in re.split(r"[|\n]", raw_todos) if t.strip()]
            ctx.todo_list = [{"text": it, "done": False} for it in items]
    elif action_lower in ("edit", "update"):
        target_idx = index if index is not None else 0
        if 0 <= target_idx < len(ctx.todo_list):
            ctx.todo_list[target_idx]["text"] = new_text or note
        elif note and new_text:
            for item in ctx.todo_list:
                if note.lower() in item["text"].lower():
                    item["text"] = new_text
                    break
    elif action_lower in ("done", "complete"):
        target_idx = index if index is not None else 0
        if 0 <= target_idx < len(ctx.todo_list):
            ctx.todo_list[target_idx]["done"] = True
        elif note:
            for item in ctx.todo_list:
                if note.lower() in item["text"].lower():
                    item["done"] = True
                    break
    elif action_lower in ("remove", "delete"):
        target_idx = index if index is not None else 0
        if 0 <= target_idx < len(ctx.todo_list):
            ctx.todo_list.pop(target_idx)
    elif action_lower in ("clear", "reset"):
        ctx.todo_list.clear()

    return {
        "success": True,
        "action": action,
        "visible_steps": ctx.todo_list,
        "total_remaining": len([t for t in ctx.todo_list if not t.get("done")]),
    }


tool_todo = tool_scratch


SCREENSHOT_MAX_BYTES = 2_000_000
SCREENSHOT_MAX_DIM = 1920


def _screenshot_dir() -> str:
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "cmdai_screenshots")
    os.makedirs(d, exist_ok=True)
    return d


def _find_window_rect(title_sub: str = "") -> Optional[Dict[str, int]]:
    if os.name != "nt":
        return None
    try:
        import win32gui
    except ImportError:
        return None
    needle = (title_sub or "").strip().lower()
    found: Dict[str, int] = {}

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            text = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return
        if needle and needle not in text.lower():
            return
        try:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
        except Exception:
            return
        if r - l > 50 and b - t > 50 and not found:
            found.update({"left": l, "top": t, "width": r - l, "height": b - t})

    try:
        if not needle:
            import win32gui as _wg
            hwnd = _wg.GetForegroundWindow()
            l, t, r, b = _wg.GetWindowRect(hwnd)
            if r - l > 50 and b - t > 50:
                return {"left": l, "top": t, "width": r - l, "height": b - t}
            return None
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return None
    return found or None


def tool_screenshot(ctx: AgentContext, target: str = "active_window") -> Dict[str, Any]:
    import uuid
    target = (target or "active_window").strip()
    rect = _find_window_rect("" if target.lower() == "active_window" else target)
    try:
        import mss
    except ImportError:
        return {"success": False, "error": "mss package not installed (pip install mss)"}
    try:
        out = os.path.join(_screenshot_dir(), f"{uuid.uuid4().hex}.png")
        with mss.mss() as sct:
            monitor = {"left": rect["left"], "top": rect["top"], "width": rect["width"], "height": rect["height"]} if rect else sct.monitors[0]
            shot = sct.grab(monitor)
            try:
                from PIL import Image
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.thumbnail((SCREENSHOT_MAX_DIM, SCREENSHOT_MAX_DIM))
                img.save(out, "PNG")
            except ImportError:
                import mss.tools
                mss.tools.to_png(shot.rgb, shot.size, output=out)
        size = os.path.getsize(out)
        if size > SCREENSHOT_MAX_BYTES:
            try:
                from PIL import Image
                img = Image.open(out)
                img.thumbnail((1280, 1280))
                img.save(out, "PNG", optimize=True)
                size = os.path.getsize(out)
            except ImportError:
                pass
        return {"success": True, "path": out, "size_bytes": size, "target": target}
    except Exception as e:
        return {"success": False, "error": f"screenshot failed: {e}"}


def tool_vision(ctx: AgentContext, image_path: str = "", question: str = "") -> Dict[str, Any]:
    p = (image_path or "").strip()
    if not p or not os.path.exists(p):
        return {"success": False, "error": f"image not found: {image_path}"}
    if os.path.getsize(p) > SCREENSHOT_MAX_BYTES * 2:
        return {"success": False, "error": "image too large (>4MB)"}
    entry = {"path": os.path.abspath(p), "question": (question or "").strip()}
    ctx.pending_images.append(entry)
    return {"success": True, "path": entry["path"], "question": entry["question"],
            "note": "image queued, it will be attached to the next model request"}
