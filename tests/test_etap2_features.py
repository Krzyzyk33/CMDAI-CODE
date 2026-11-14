"""Tests for Etap 2 features: headless CLI, /custom commands & hooks, git worktrees, AST repo map, prompt caching."""

from pathlib import Path
import subprocess
import pytest

from cmdai.agent.subagents.worktree import create_worktree, is_git_repo, remove_worktree, worktree_changed_files
from cmdai.core.custom_extensions import (
    add_post_tool_hook,
    build_ai_extension_prompt,
    get_custom_command_body,
    list_custom_commands,
    run_post_tool_hooks,
    save_custom_command,
)
from cmdai.core.headless import combine_prompt_and_stdin, run_headless
from cmdai.core.providers import apply_prompt_cache_hints
from cmdai.indexer.repo_map import build_repo_map
from cmdai.tui.app import CMDAICodeTUI


def test_combine_prompt_and_stdin() -> None:
    assert combine_prompt_and_stdin("review", "diff text") == "review\n\n<stdin>\ndiff text\n</stdin>"
    assert combine_prompt_and_stdin("", "only stdin") == "only stdin"
    assert combine_prompt_and_stdin("only prompt", "") == "only prompt"


def test_run_headless_with_xml_tool_loop(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("world\n", encoding="utf-8")
    turns = iter([
        ('<tool:read path="hello.txt" />', ""),
        ("The file says world.", ""),
    ])

    def fake_llm(prov, mod, msgs):
        return next(turns)

    res = run_headless(
        prompt="Read hello.txt",
        workdir=str(tmp_path),
        provider="openai",
        model="gpt-4o",
        completion_fn=fake_llm,
    )
    assert res["ok"] is True
    assert res["response"] == "The file says world."
    assert len(res["tool_calls"]) == 1
    assert res["tool_calls"][0]["tool"] == "read"


def test_custom_commands_and_hooks(tmp_path: Path) -> None:
    save_custom_command(str(tmp_path), "review", "# Review\nCheck: $ARGUMENTS")
    cmds = list_custom_commands(str(tmp_path))
    assert len(cmds) == 1
    assert cmds[0]["name"] == "/review"
    assert get_custom_command_body(str(tmp_path), "/review", "auth.py") == "# Review\nCheck: auth.py"

    add_post_tool_hook(str(tmp_path), "edit|write", 'python -c "print(\'hook_ok\')"')
    hook_res = run_post_tool_hooks(str(tmp_path), "edit", {"path": "x.py"})
    assert len(hook_res) == 1
    assert hook_res[0]["returncode"] == 0
    assert "hook_ok" in hook_res[0]["stdout"]

    assert ".cmdai/commands" in build_ai_extension_prompt("command", "lint check")
    assert ".cmdai/hooks.json" in build_ai_extension_prompt("hook", "run pytest")


def test_git_worktree_lifecycle(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)

    assert is_git_repo(str(tmp_path)) is True
    wt = create_worktree(str(tmp_path), "worker1")
    assert Path(wt.path).is_dir()
    (Path(wt.path) / "app.py").write_text("x = 2\n", encoding="utf-8")
    changed = worktree_changed_files(wt)
    assert "app.py" in changed
    remove_worktree(wt, delete_branch=True)
    assert not Path(wt.path).exists()


def test_repo_map_and_prompt_cache_hints(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        "class Engine:\n    def run(self, x: int) -> int:\n        return x\n",
        encoding="utf-8",
    )
    rmap1 = build_repo_map(str(tmp_path), max_tokens=500)
    rmap2 = build_repo_map(str(tmp_path), max_tokens=500)
    assert rmap1 == rmap2
    assert "mod.py" in rmap1
    assert "class Engine" in rmap1
    assert "def run(self, x: int) -> int" in rmap1

    local_payload = {"messages": [{"role": "system", "content": "sys"}]}
    apply_prompt_cache_hints("localllmapi", "http://127.0.0.1:8080/v1", local_payload)
    assert local_payload["cache_prompt"] is True

    ant_payload = {"messages": [{"role": "system", "content": "sys"}]}
    apply_prompt_cache_hints("anthropic", "https://api.anthropic.com/v1", ant_payload)
    assert ant_payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_tui_custom_and_map_modals(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._handle_submit("/custom", from_hero=True)
        await pilot.pause()
        assert len(app.screen_stack) > 1
        assert not app.in_chat_mode
        # Select first option (+ Create manually: New command) via Enter — must not crash
        await pilot.press("enter")
        await pilot.pause()
        modal = app.screen_stack[-1]
        assert modal.query_one("#custom-form").has_class("visible")
        modal.query_one("#custom-name-input").value = "demo"
        modal._submit_form()
        await pilot.pause()
        assert (tmp_path / ".cmdai" / "commands" / "demo.md").exists()

        # Also test keybindings 2 (hook) and 3 (AI) + Esc back to list
        await pilot.press("2")
        await pilot.pause()
        assert modal.query_one("#custom-form").has_class("visible")
        await pilot.press("escape")
        await pilot.pause()
        assert not modal.query_one("#custom-form").has_class("visible")

        await pilot.press("3")
        await pilot.pause()
        assert modal.query_one("#custom-form").has_class("visible")
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        spawned: list = []
        app._spawn_terminal_window = lambda script, title, *a: spawned.append((script, title, a))
        app._handle_submit("/map", from_hero=True)
        await pilot.pause()
        assert not app.in_chat_mode
        assert len(spawned) == 1
        assert spawned[0][0].endswith("map_app.py")


@pytest.mark.asyncio
async def test_map_app_renders_graph_and_cycles_nodes(tmp_path: Path) -> None:
    from cmdai.indexer.repo_map import build_repo_graph
    from cmdai.tui.map_app import CMDAIMapApp

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("def compute():\n    return 42\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from pkg import core\ndef run():\n    return core.compute()\n", encoding="utf-8")

    graph = build_repo_graph(str(tmp_path))
    assert len(graph["nodes"]) >= 3
    assert any(k == "import" for _, _, k in graph["edges"])

    map_app = CMDAIMapApp(workdir=str(tmp_path))
    async with map_app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n", "n", "f", "r")
        await pilot.pause()
        await pilot.press("escape")


def test_cli_code_help_shows_headless_flags() -> None:
    import sys
    repo_root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, str(repo_root / "cmdai.py"), "code", "help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    assert "--prompt" in proc.stdout
    assert "--continue" in proc.stdout
    assert "--output-format" in proc.stdout


def test_match_agents_to_calls_available_in_worker_mixin() -> None:
    from cmdai.tui.app_parts.worker_mixin import _match_agents_to_calls
    paired = _match_agents_to_calls(
        [{"name": "Reviewer"}],
        [{"role": "Reviewer", "summary": "ok"}],
    )
    assert paired == [{"role": "Reviewer", "summary": "ok"}]


@pytest.mark.asyncio
async def test_local_model_blocks_background_subagent_allows_command(tmp_path: Path) -> None:
    from cmdai.tui.widgets import AssistantTurnCard, ToolBlock

    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.current_provider = "local_gguf"
        card = AssistantTurnCard("local")
        await app.query_one("#chat-scroll").mount(card)
        app._current_assistant_card = card

        sub_block = ToolBlock("subagent", "working")
        sub_block.subagent_role = "Reviewer"
        sub_block.tool_running = True
        await card.flow_container.mount(sub_block)
        card.tool_blocks.append(sub_block)

        called = {"bg": 0}
        class DummyRunner:
            def request_background(self):
                called["bg"] += 1
                return True
        app.agent_runner = DummyRunner()

        app.action_send_to_background()
        assert called["bg"] == 0
        assert sub_block.is_backgrounded is False

        sub_block.tool_running = False
        cmd_block = ToolBlock("command", "pytest")
        cmd_block.tool_running = True
        await card.flow_container.mount(cmd_block)
        card.tool_blocks.append(cmd_block)

        app.action_send_to_background()
        assert called["bg"] == 1
        assert cmd_block.is_backgrounded is True
