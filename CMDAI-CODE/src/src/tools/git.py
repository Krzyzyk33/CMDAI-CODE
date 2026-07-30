import os
import subprocess
import shutil

def run_git_command(args: list, cwd: str) -> str:
    if not shutil.which("git"):
        return "Brak środowiska Git (nie znaleziono w PATH)."
    try:
        # Use shell=True on Windows
        result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, shell=(os.name == 'nt'))
        if result.returncode != 0:
            return f"Git error: {result.stderr.strip()}"
        return result.stdout.strip() or "No output."
    except Exception as e:
        return f"Failed to execute git command: {e}"

def git_diff(cwd: str) -> str:
    return run_git_command(["diff"], cwd)

def git_commit(msg: str, cwd: str) -> str:
    run_git_command(["add", "."], cwd)
    return run_git_command(["commit", "-m", msg], cwd)

def git_undo(cwd: str) -> str:
    return run_git_command(["stash", "push", "-m", "cmdai_undo"], cwd)
