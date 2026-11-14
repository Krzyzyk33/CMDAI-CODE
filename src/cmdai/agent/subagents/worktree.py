"""Isolated git worktree helpers for parallel subagents."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class WorktreeInfo:
    repo_dir: str
    path: str
    branch: str


def is_git_repo(path: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0 and proc.stdout.strip().lower() == "true"
    except Exception:
        return False


def create_worktree(repo_dir: str, label: str = "agent") -> WorktreeInfo:
    """Create an isolated git worktree under `<repo_dir>/.cmdai/worktrees/`."""
    abs_repo = os.path.abspath(repo_dir)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (label or "agent").lower()).strip("-") or "agent"
    uid = uuid.uuid4().hex[:6]
    wt_root = Path(abs_repo) / ".cmdai" / "worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    wt_path = str(wt_root / f"{slug}-{uid}")
    branch = f"cmdai-wt/{slug}-{uid}"

    proc = subprocess.run(
        ["git", "-C", abs_repo, "worktree", "add", "-b", branch, wt_path],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {(proc.stderr or proc.stdout).strip()}")
    return WorktreeInfo(repo_dir=abs_repo, path=wt_path, branch=branch)


def worktree_changed_files(wt: WorktreeInfo) -> List[str]:
    """Return list of modified/untracked files inside `wt.path`."""
    proc = subprocess.run(
        ["git", "-C", wt.path, "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return []
    files: List[str] = []
    for line in proc.stdout.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip())
    return files


def remove_worktree(wt: WorktreeInfo, delete_branch: bool = True) -> None:
    """Remove the git worktree and optionally its temporary branch."""
    subprocess.run(
        ["git", "-C", wt.repo_dir, "worktree", "remove", "--force", wt.path],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if delete_branch and wt.branch:
        subprocess.run(
            ["git", "-C", wt.repo_dir, "branch", "-D", wt.branch],
            capture_output=True,
            text=True,
            timeout=10,
        )
