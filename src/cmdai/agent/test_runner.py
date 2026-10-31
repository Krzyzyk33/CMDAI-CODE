import os
import subprocess
import json
import glob
from typing import Any, Dict, Optional


def detect_test_runner(workdir: str, target_file: Optional[str] = None) -> Optional[Dict[str, Any]]:
    abs_workdir = os.path.abspath(workdir)

    tests_dir = os.path.join(abs_workdir, "tests")
    has_tests_dir = os.path.isdir(tests_dir)
    py_test_files = glob.glob(os.path.join(abs_workdir, "test_*.py")) + glob.glob(os.path.join(abs_workdir, "*_test.py"))
    if has_tests_dir:
        py_test_files += glob.glob(os.path.join(tests_dir, "test_*.py"))

    if has_tests_dir or py_test_files or os.path.exists(os.path.join(abs_workdir, "pytest.ini")) or os.path.exists(os.path.join(abs_workdir, "pyproject.toml")):
        specific_test = None
        if target_file:
            base = os.path.basename(target_file)
            name_no_ext = os.path.splitext(base)[0]
            candidates = [
                os.path.join(abs_workdir, f"test_{base}"),
                os.path.join(abs_workdir, f"{name_no_ext}_test.py"),
                os.path.join(tests_dir, f"test_{base}"),
                os.path.join(tests_dir, f"{name_no_ext}_test.py"),
            ]
            for c in candidates:
                if os.path.isfile(c):
                    specific_test = c
                    break

        if specific_test:
            return {
                "runner": "pytest_specific",
                "cmd": ["python", "-m", "pytest", specific_test, "-q", "--tb=short"],
                "target": specific_test,
            }
        else:
            return {
                "runner": "pytest",
                "cmd": ["python", "-m", "pytest", "-q", "--tb=short"],
                "target": "all",
            }

    pkg_json = os.path.join(abs_workdir, "package.json")
    if os.path.exists(pkg_json):
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "scripts" in data and "test" in data["scripts"]:
                return {
                    "runner": "npm_test",
                    "cmd": ["npm", "test"],
                    "target": "npm",
                }
        except Exception:
            pass

    if os.path.exists(os.path.join(abs_workdir, "Cargo.toml")):
        return {
            "runner": "cargo_test",
            "cmd": ["cargo", "test"],
            "target": "cargo",
        }

    if os.path.exists(os.path.join(abs_workdir, "go.mod")):
        return {
            "runner": "go_test",
            "cmd": ["go", "test", "./..."],
            "target": "go",
        }

    return None


def run_project_tests(workdir: str, target_file: Optional[str] = None, timeout: int = 20) -> Dict[str, Any]:
    runner_info = detect_test_runner(workdir, target_file)
    if not runner_info:
        return {"has_tests": False, "passed": True, "summary": "No test suite detected in workspace."}

    cmd = runner_info["cmd"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        passed = (proc.returncode == 0)
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        combined = (stdout + "\n" + stderr).strip()

        if len(combined) > 1800:
            combined = combined[:900] + "\n\n... [truncated] ...\n\n" + combined[-900:]

        summary = f"Tests {'PASSED' if passed else 'FAILED'} (exit code {proc.returncode})"
        return {
            "has_tests": True,
            "runner": runner_info["runner"],
            "passed": passed,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output": combined,
            "summary": summary,
        }
    except subprocess.TimeoutExpired:
        return {
            "has_tests": True,
            "runner": runner_info["runner"],
            "passed": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Tests timed out after {timeout} seconds.",
            "output": f"Tests timed out after {timeout} seconds.",
            "summary": f"Tests TIMED OUT after {timeout}s",
        }
    except Exception as e:
        return {
            "has_tests": True,
            "runner": runner_info["runner"],
            "passed": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "output": str(e),
            "summary": f"Failed to execute tests: {e}",
        }
