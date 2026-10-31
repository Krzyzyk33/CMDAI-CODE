"""Project Scope Guard & Single-File Diff Engine for CMDAI CODE.

Prevents git/file scans from escaping into the user's home directory or root
filesystem (e.g., C:\\Users\\<user>, .VirtualBox, .aws, .antigravity) and computes
accurate diffs for individual files (including untracked/newly created files).
"""

import difflib
import os
import subprocess
from typing import Dict, List, Optional, Tuple

GLOBAL_DOT_EXCLUSIONS = {
    ".virtualbox",
    ".antigravity",
    ".aws",
    ".bun",
    ".cache",
    ".cargo",
    ".claude",
    ".claude.json",
    ".cmdai-editor-layout.json",
    ".cmdai-editor-settings.json",
    ".cmdai_code",
    ".cmdai_prompt_history.json",
    ".codeium",
    ".codex",
    ".conda",
    ".config",
    ".copilot",
    ".gemini",
    ".gnupg",
    ".ipython",
    ".jupyter",
    ".kube",
    ".local",
    ".m2",
    ".matplotlib",
    ".npm",
    ".nuget",
    ".ollama",
    ".pki",
    ".pyenv",
    ".rustup",
    ".ssh",
    ".templateengine",
    ".thumbnails",
    ".vscode",
    ".vscode-insiders",
    "appdata",
    "ntuser.dat",
}


def _norm_case_path(path: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(path))).rstrip("\\/")
    except Exception:
        return str(path).strip().lower().rstrip("\\/")


def is_unsafe_global_dir(path: str) -> bool:
    """Return True if path is a drive root, OS directory, or user home root."""
    if not path:
        return True
    norm = _norm_case_path(path)
    if not norm:
        return True

    # Drive root like 'c:' or '/'
    drive, tail = os.path.splitdrive(norm)
    if not tail or tail in ("\\", "/"):
        return True

    home = _norm_case_path(os.path.expanduser("~"))
    if norm == home:
        return True

    users_parent = _norm_case_path(os.path.dirname(os.path.expanduser("~")))
    if norm == users_parent:
        return True

    unsafe_system_dirs = {
        _norm_case_path(r"C:\Windows"),
        _norm_case_path(r"C:\Program Files"),
        _norm_case_path(r"C:\Program Files (x86)"),
        _norm_case_path(r"C:\ProgramData"),
        "/root",
        "/home",
        "/users",
        "/etc",
        "/usr",
        "/var",
    }
    if norm in unsafe_system_dirs:
        return True

    return False


def get_safe_git_root(workdir: str) -> Optional[str]:
    """Return git toplevel path ONLY if it is a safe project directory (not ~ or drive root)."""
    if not workdir or is_unsafe_global_dir(workdir):
        return None
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
        if res.returncode != 0:
            return None
        top = res.stdout.strip()
        if not top or is_unsafe_global_dir(top):
            return None
        return os.path.abspath(top)
    except Exception:
        return None


def is_ignored_user_dotfile(rel_path: str) -> bool:
    """Filter out home-directory dotfiles/folders if any ever appear."""
    if not rel_path:
        return True
    clean = rel_path.replace("\\", "/").strip("/")
    first_part = clean.split("/")[0].lower()
    return first_part in GLOBAL_DOT_EXCLUSIONS


def list_safe_changed_files(workdir: str) -> List[Tuple[str, str]]:
    """Return list of (rel_path, git_status) strictly within safe project scope."""
    abs_work = os.path.abspath(workdir or ".")
    git_root = get_safe_git_root(abs_work)
    if not git_root:
        return []

    results: List[Tuple[str, str]] = []
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "--", "."],
            cwd=abs_work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
        )
        if res.returncode != 0:
            return []

        for line in res.stdout.splitlines():
            if len(line) < 4:
                continue
            status = line[:2].strip() or "M"
            raw_path = line[3:].strip().strip('"')
            if " -> " in raw_path:
                raw_path = raw_path.split(" -> ")[-1].strip().strip('"')
            # git status --porcelain outputs paths relative to git_root
            full_p = os.path.normpath(os.path.join(git_root, raw_path))
            # Ensure file is inside abs_work
            try:
                rel_to_work = os.path.relpath(full_p, abs_work)
            except ValueError:
                continue
            if rel_to_work.startswith(".."):
                continue
            if is_ignored_user_dotfile(rel_to_work):
                continue
            results.append((rel_to_work.replace("\\", "/"), status))
    except Exception:
        pass
    return results


def resolve_diff_target_file(
    workdir: str,
    arg: str = "",
    recent_files: Optional[List[str]] = None,
) -> Optional[str]:
    """Resolve the single target file to open in the editor diff view.

    Priority:
    1. Explicit `arg` passed to `/diff <file>`
    2. Most recently modified file from `recent_files` (current session/turn)
    3. First modified file in safe project git status (if workdir is a safe project)
    """
    abs_work = os.path.abspath(workdir or ".")
    clean_arg = (arg or "").strip().strip('"').strip("'")

    if clean_arg:
        cand = clean_arg if os.path.isabs(clean_arg) else os.path.join(abs_work, clean_arg)
        cand = os.path.abspath(cand)
        if os.path.isfile(cand):
            return cand
        # Even if deleted on disk, check if it's a relative path inside a safe project
        if not is_unsafe_global_dir(abs_work) and not os.path.isdir(cand):
            return cand
        return None

    if recent_files:
        for rf in reversed(recent_files):
            if not rf:
                continue
            cand = rf if os.path.isabs(rf) else os.path.join(abs_work, rf)
            cand = os.path.abspath(cand)
            if os.path.isfile(cand) and not is_ignored_user_dotfile(os.path.basename(cand)):
                return cand

    changed = list_safe_changed_files(abs_work)
    for rel_p, _st in changed:
        full_p = os.path.abspath(os.path.join(abs_work, rel_p))
        if os.path.isfile(full_p):
            return full_p

    return None


def compute_single_file_diff(
    filepath: str,
    workdir: Optional[str] = None,
    baseline_text: Optional[str] = None,
) -> Dict[str, object]:
    """Compute unified diff, original content, current content, and stats for a single file."""
    abs_file = os.path.abspath(filepath)
    base_dir = os.path.abspath(workdir) if workdir else os.path.dirname(abs_file)
    try:
        rel_display = os.path.relpath(abs_file, base_dir).replace("\\", "/")
        if rel_display.startswith(".."):
            rel_display = os.path.basename(abs_file)
    except ValueError:
        rel_display = os.path.basename(abs_file)

    current_exists = os.path.isfile(abs_file)
    current_text = ""
    if current_exists:
        try:
            with open(abs_file, "r", encoding="utf-8", errors="replace") as f:
                current_text = f.read()
        except Exception as e:
            return {
                "filepath": abs_file,
                "relpath": rel_display,
                "status": "E",
                "added": 0,
                "deleted": 0,
                "original_text": "",
                "current_text": "",
                "diff_lines": [f"Cannot read file: {e}"],
                "diff_text": f"Cannot read file: {e}",
            }

    original_text: Optional[str] = baseline_text
    status = "M"

    if original_text is None:
        git_root = get_safe_git_root(os.path.dirname(abs_file))
        if git_root:
            try:
                rel_git = os.path.relpath(abs_file, git_root).replace("\\", "/")
                show_res = subprocess.run(
                    ["git", "show", f"HEAD:{rel_git}"],
                    cwd=git_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=4,
                )
                if show_res.returncode == 0:
                    original_text = show_res.stdout
                    status = "M" if current_exists else "D"
                else:
                    # Untracked / new file in git
                    original_text = ""
                    status = "A" if current_exists else "U"
            except Exception:
                original_text = ""
                status = "A" if current_exists else "U"
        else:
            # Not in a git repo: compare against empty if new or show current file content
            original_text = ""
            status = "A" if current_exists else "U"

    orig_lines = original_text.splitlines(keepends=False)
    curr_lines = current_text.splitlines(keepends=False)

    if orig_lines == curr_lines:
        return {
            "filepath": abs_file,
            "relpath": rel_display,
            "status": "CLEAN",
            "added": 0,
            "deleted": 0,
            "original_text": original_text,
            "current_text": current_text,
            "diff_lines": [],
            "diff_text": "",
        }

    diff_iter = difflib.unified_diff(
        orig_lines,
        curr_lines,
        fromfile=f"a/{rel_display}",
        tofile=f"b/{rel_display}",
        lineterm="",
        n=3,
    )
    diff_lines = list(diff_iter)
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    deleted = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))

    return {
        "filepath": abs_file,
        "relpath": rel_display,
        "status": status,
        "added": added,
        "deleted": deleted,
        "original_text": original_text,
        "current_text": current_text,
        "diff_lines": diff_lines,
        "diff_text": "\n".join(diff_lines),
    }
