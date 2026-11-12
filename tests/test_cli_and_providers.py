"""Tests for CLI entrypoint, provider catalog, permissions, and settings."""

from pathlib import Path
import subprocess
import sys

from cmdai.agent.runner import AgentRunner
from cmdai.agent.tools import AgentContext
from cmdai.core.providers import PROVIDERS_CATALOG
from cmdai.core.settings import SettingsManager


def test_cli_help_exits_zero() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, str(repo_root / "cmdai.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    assert "cmdai" in proc.stdout.lower()


def test_providers_catalog_contains_core_providers() -> None:
    for pid in ("local_gguf", "localllmapi", "anthropic", "openai", "gemini", "ollama"):
        assert pid in PROVIDERS_CATALOG
        assert PROVIDERS_CATALOG[pid].id == pid


def test_auto_mode_never_asks_for_any_tool(tmp_path: Path) -> None:
    runner = AgentRunner(ctx=AgentContext(workdir=str(tmp_path)))
    for tool_name, args in (
        ("read", {"path": "foo.py"}),
        ("write", {"path": "foo.py", "content": "x"}),
        ("edit", {"path": "foo.py", "old": "a", "new": "b"}),
        ("command", {"cmd": "pytest"}),
    ):
        assert runner.check_tool_permission(tool_name, args, "auto") == "allow"


def test_plan_mode_denies_mutating_tools(tmp_path: Path) -> None:
    runner = AgentRunner(ctx=AgentContext(workdir=str(tmp_path)))
    assert runner.check_tool_permission("read", {"path": "foo.py"}, "plan") == "allow"
    assert runner.check_tool_permission("write", {"path": "foo.py"}, "plan") == "deny"
    assert runner.check_tool_permission("edit", {"path": "foo.py"}, "plan") == "deny"
    assert runner.check_tool_permission("command", {"cmd": "ls"}, "plan") == "deny"


def test_code_mode_asks_for_command_only(tmp_path: Path) -> None:
    runner = AgentRunner(ctx=AgentContext(workdir=str(tmp_path)))
    runner.permissions = None
    assert runner.check_tool_permission("write", {"path": "foo.py"}, "code") == "allow"
    assert runner.check_tool_permission("command", {"cmd": "ls"}, "code") == "ask"


def test_settings_manager_roundtrip(tmp_path: Path) -> None:
    mgr = SettingsManager(str(tmp_path))
    mgr.config.setdefault("agent", {})["mode"] = "auto"
    mgr.save_config()
    mgr2 = SettingsManager(str(tmp_path))
    assert mgr2.config["agent"]["mode"] == "auto"
