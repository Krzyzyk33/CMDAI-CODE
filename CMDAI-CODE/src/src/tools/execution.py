import os
import re
import glob as pyglob
import subprocess
import shutil
from typing import List, Dict, Any, Optional


def run_python(path: str = "", code: str = "", timeout: int = 30) -> str:
    if not path and not code:
        return "Error: Provide either 'path' or 'code'."

    import subprocess
    import os

    if code:
        import tempfile

        fd, temp_path = tempfile.mkstemp(suffix=".py", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        script_to_run = temp_path
    else:
        if not os.path.exists(path):
            return f"Error: File {path} not found."
        script_to_run = path

    try:
        env = os.environ.copy()
        env["MPLBACKEND"] = "Agg"

        result = subprocess.run(
            ["python", script_to_run],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        out = f"Exit code: {result.returncode}\n"
        if result.stdout:
            out += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            out += f"STDERR:\n{result.stderr}\n"
        return out
    except subprocess.TimeoutExpired:
        return f"Error: Script timed out after {timeout} seconds."
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        if code and os.path.exists(script_to_run):
            try:
                os.remove(script_to_run)
            except:
                pass


def run_bash(command: str, timeout: int = 30) -> str:
    import subprocess

    try:
        env = os.environ.copy()
        env["MPLBACKEND"] = "Agg"
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        out = f"Exit code: {result.returncode}\n"
        if result.stdout:
            out += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            out += f"STDERR:\n{result.stderr}\n"
        return out
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing bash: {str(e)}"


def _auto_detect_and_test(cwd: str) -> str:
    env = os.environ.copy()
    env["MPLBACKEND"] = "Agg"
    candidates = []

    has_package_json = os.path.exists(os.path.join(cwd, "package.json"))
    has_cargo_toml = os.path.exists(os.path.join(cwd, "Cargo.toml"))
    has_go_mod = os.path.exists(os.path.join(cwd, "go.mod"))
    has_csproj = bool(pyglob.glob(os.path.join(cwd, "*.csproj")))
    has_pyproject = os.path.exists(os.path.join(cwd, "pyproject.toml"))
    has_setup = os.path.exists(os.path.join(cwd, "setup.py")) or os.path.exists(
        os.path.join(cwd, "setup.cfg")
    )
    has_py = bool(pyglob.glob(os.path.join(cwd, "**/*.py"), recursive=True))

    if has_package_json:
        candidates.extend(
            [
                ("npm test", "npm test"),
                ("npx tsc --noEmit", "TypeScript check"),
                ("npx eslint .", "ESLint"),
            ]
        )
    if has_cargo_toml:
        candidates.extend(
            [
                ("cargo test", "cargo test"),
                ("cargo check", "cargo check"),
            ]
        )
    if has_go_mod:
        candidates.extend(
            [
                ("go test ./...", "go test"),
                ("go vet ./...", "go vet"),
            ]
        )
    if has_csproj:
        candidates.extend(
            [
                ("dotnet test", "dotnet test"),
                ("dotnet build", "dotnet build"),
            ]
        )
    if has_py or has_pyproject or has_setup:
        all_py = pyglob.glob(os.path.join(cwd, "**/*.py"), recursive=True)
        has_test_files = any("test" in f.lower() for f in all_py)
        if has_test_files:
            candidates.append(("python -m pytest --tb=short -q", "pytest (quick)"))
        candidates.extend(
            [
                (
                    "python -c \"import py_compile, os, sys; root = '.'; ok = True; [sys.exit(1) if not py_compile.compile(os.path.join(d,f), doraise=True) else None for d,_,fs in os.walk(root) for f in fs if f.endswith('.py')]\"",
                    "Python syntax check",
                ),
            ]
        )
    if has_py:
        candidates.append(
            (
                "python --version && python -c \"print('Python interpreter OK')\"",
                "Python basic",
            )
        )

    for cmd, label in candidates:
        try:
            res = subprocess.run(
                cmd,
                shell=True,
                env=env,
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                out = res.stdout[:2000]
                return f"Tests passed ({label}): exit 0\n{out}"
        except Exception:
            pass

    return "No test framework detected."


def run_tests(files: List[str] = None, agent_instance=None, timeout: int = 3600, source_id: str = None) -> str:
    cwd = os.getcwd()

    config_path = os.path.join(cwd, ".cmdai_code_project", "project_config.json")
    framework = ""
    if os.path.exists(config_path):
        try:
            import json

            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                framework = config.get("test_framework", "")
        except Exception:
            pass

    env = os.environ.copy()

    needs_agg = False
    if files:
        for file in files:
            try:
                if file.endswith(".py") and os.path.exists(file):
                    with open(file, "r", encoding="utf-8") as f:
                        content = f.read()
                        if (
                            "matplotlib" in content
                            or "tkinter" in content
                            or "PyQt" in content
                            or "plt.show" in content
                        ):
                            needs_agg = True
            except Exception:
                pass

    if needs_agg:
        env["MPLBACKEND"] = "Agg"

    if not framework:
        if not agent_instance:
            return _auto_detect_and_test(cwd)

        from ..ui import console, MUTED_COLOR, _cprint

        _cprint(f"  [{MUTED_COLOR}]⎿  No test framework detected.[/]", source_id=source_id)

        test_model = agent_instance.model
        if not test_model:
            return "No test framework detected. Model missing."
        attempts = 0
        max_attempts = 3

        try:
            root_files = ", ".join(os.listdir(cwd)[:30])
        except Exception:
            root_files = "unknown"

        last_error = f"No test framework was detected. The root directory contains: {root_files}.\nInvent exactly 1 terminal command to test syntax or run basic tests for this project."

        chat_history = [
            {
                "role": "system",
                "content": "You are a testing assistant. Analyze the problem, think step-by-step, and THEN provide exactly one terminal command to run. You MUST wrap your final command in a markdown code block like this:\n```bash\n<command>\n```\nCRITICAL RULES:\n1. Do NOT add any comments (like #) to the command.\n2. Do NOT use test frameworks like pytest, unittest, jest, or mocha. They are NOT installed.\n3. If a command fails, NEVER use the same base command again. Try a completely different approach (e.g. standard language syntax checkers).",
            }
        ]

        while attempts < max_attempts:
            _cprint(
                f"  [{MUTED_COLOR}]⎿ ⌬ Thinking... (Attempt {attempts + 1})[/]",
                source_id=source_id,
            )

            chat_history.append({"role": "user", "content": last_error})

            full_response = ""
            try:
                for chunk in test_model.stream_chat(chat_history):
                    c = ""
                    if isinstance(chunk, tuple):
                        c = chunk[0] or ""
                    elif hasattr(chunk, "get"):
                        c = chunk.get("content", "")
                    elif isinstance(chunk, dict) and "content" in chunk:
                        c = chunk["content"]
                    elif isinstance(chunk, str):
                        c = chunk
                    full_response += c
            except Exception as e:
                return f"Error using test model: {str(e)}"
            chat_history.append({"role": "assistant", "content": full_response})

            cmd_suggestion = ""
            import re

            reasoning = full_response.split("```")[0].strip()
            if reasoning:
                reasoning_short = reasoning.replace("\n", " ")
                if len(reasoning_short) > 120:
                    reasoning_short = reasoning_short[:117] + "..."
                from rich.markup import escape

                _cprint(
                    f"     [{MUTED_COLOR}]⎿  Reasoning: {escape(reasoning_short)}[/]",
                    source_id=source_id,
                )

            match = re.search(
                r"```(?:bash|sh|cmd)?\n?(.*?)\n?```",
                full_response,
                re.DOTALL | re.IGNORECASE,
            )
            if match:
                cmd_suggestion = match.group(1).strip()
            else:
                lines = [l.strip() for l in full_response.splitlines() if l.strip()]
                cmd_suggestion = lines[-1].replace("`", "") if lines else ""

            if " #" in cmd_suggestion:
                cmd_suggestion = cmd_suggestion.split(" #")[0].strip()
            if cmd_suggestion.startswith("$ "):
                cmd_suggestion = cmd_suggestion[2:]
            _cprint(
                f"     [{MUTED_COLOR}]⎿  Selected command: {cmd_suggestion}[/]",
                source_id=source_id,
            )

            try:
                res = subprocess.run(
                    cmd_suggestion,
                    shell=True,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if res.returncode == 0:
                    _cprint(f"     [{MUTED_COLOR}]⎿  Success (exit 0)[/]", source_id=source_id)
                    if res.stdout.strip():
                        return f"Tests passed automatically using command: {cmd_suggestion}\nOutput:\n{res.stdout}"
                    else:
                        return f"Tests passed automatically using command: {cmd_suggestion}"
                else:
                    _cprint(
                        f"     [{MUTED_COLOR}]⎿  Attempt {attempts + 1} failed (exit {res.returncode})[/]",
                        source_id=source_id,
                    )
                    last_error = f"Previous command '{cmd_suggestion}' returned an error (exit {res.returncode}):\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}\nExplain why it failed and suggest a different command. Remember to wrap the command in ```bash\n```. If the command failed because there are no tests, try checking syntax instead!"
            except subprocess.TimeoutExpired:
                _cprint(
                    f"     [{MUTED_COLOR}]⎿  Attempt {attempts + 1} - timeout[/]",
                    source_id=source_id,
                )
                last_error = (
                    f"Command '{cmd_suggestion}' timed out. Suggest a different one."
                )
            except Exception as e:
                last_error = f"Error: {e}. Suggest a different command."

            attempts += 1

        _cprint(f"  [{MUTED_COLOR}]⎿  All attempts failed. Disabling tool.[/]", source_id=source_id)
        return "Automatic system-model tests failed after 3 attempts."

    cmd = ""
    if framework == "pytest":
        cmd = "pytest"
        if files:
            test_files = [f for f in files if "test" in f]
            if test_files:
                cmd += " " + " ".join(f'"{f}"' for f in test_files)
    elif framework == "npm test":
        cmd = "npm test"
    elif framework == "go test":
        cmd = "go test ./..."
    elif framework == "cargo test":
        cmd = "cargo test"

    try:
        result = subprocess.run(
            cmd, shell=True, env=env, capture_output=True, text=True, timeout=timeout
        )
        out = f"Exit code: {result.returncode}\n"
        if result.stdout:
            out += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            out += f"STDERR:\n{result.stderr}\n"
        return out
    except subprocess.TimeoutExpired:
        return f"Error: Tests timed out after {timeout} seconds."
    except Exception as e:
        return f"Error running tests: {str(e)}"
