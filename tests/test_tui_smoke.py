"""Headless Textual smoke tests for CMDAICodeTUI (must pass with zero visual/behavioral regressions)."""

from pathlib import Path
import pytest

from cmdai.tui.app import CMDAICodeTUI
from cmdai.tui.widgets import (
    AssistantTurnCard,
    AutoExpandingInput,
    ToolBlock,
    UserMessageCard,
)


def test_widget_exports_importable() -> None:
    assert UserMessageCard is not None
    assert AssistantTurnCard is not None
    assert ToolBlock is not None
    assert AutoExpandingInput is not None


def test_tui_bindings_preserved() -> None:
    keys = {b.key for b in CMDAICodeTUI.BINDINGS}
    assert {"ctrl+c", "escape", "shift+tab", "ctrl+t"}.issubset(keys)


@pytest.mark.asyncio
async def test_tui_mounts_and_shows_hero(tmp_path: Path) -> None:
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#hero-prompt-input") is not None
        assert app.query_one("#prompt-input") is not None
        assert app.query_one("#chat-scroll") is not None


@pytest.mark.asyncio
async def test_tui_slash_hints_appear_in_hero(tmp_path: Path) -> None:
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        hero_input = app.query_one("#hero-prompt-input", AutoExpandingInput)
        hero_input.focus()
        await pilot.press("/")
        await pilot.pause()
        hints = app.query_one("#hero-cmd-hints")
        assert len(hints.children) > 0
        assert hints.styles.display == "block"


@pytest.mark.asyncio
async def test_tui_switch_to_chat_view(tmp_path: Path) -> None:
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._switch_to_chat()
        await pilot.pause()
        assert "hidden" in app.query_one("#hero-view").classes
        assert "hidden" not in app.query_one("#chat-view").classes


@pytest.mark.asyncio
async def test_tui_help_modal(tmp_path: Path) -> None:
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._cmd_help()
        await pilot.pause()
        assert len(app.screen_stack) > 1
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_tui_models_and_settings_modals(tmp_path: Path) -> None:
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._cmd_models()
        await pilot.pause()
        assert len(app.screen_stack) > 1
        await pilot.press("escape")
        await pilot.pause()

        app._cmd_settings()
        await pilot.pause()
        assert len(app.screen_stack) > 1
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_tui_extra_modals_open_and_close(tmp_path: Path) -> None:
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        for cmd in (app._cmd_stats, app._cmd_system, app._cmd_params, app._cmd_sessions, app._cmd_context):
            cmd()
            await pilot.pause()
            assert len(app.screen_stack) > 1
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_tui_tools_modal_and_mode_cycle(tmp_path: Path) -> None:
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        initial_mode = app.agent_mode
        app.cycle_mode()
        assert app.agent_mode != initial_mode

        app._cmd_tools()
        await pilot.pause()
        assert len(app.screen_stack) > 1
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_tui_at_mention_shows_file_hints(tmp_path: Path) -> None:
    (tmp_path / "sample_file.py").write_text("print('hi')\n", encoding="utf-8")
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        hero_input = app.query_one("#hero-prompt-input", AutoExpandingInput)
        hero_input.focus()
        await pilot.press("@")
        await pilot.pause()
        hints = app.query_one("#hero-cmd-hints")
        assert len(app._hint_matches) > 0
        assert len(hints.children) > 0
        assert hints.styles.display == "block"


def test_editor_main_path_exists(tmp_path: Path) -> None:
    import os
    app = CMDAICodeTUI(workdir=str(tmp_path))
    assert os.path.isfile(app._get_editor_main_path())


@pytest.mark.asyncio
async def test_todo_block_is_expanded_after_finish(tmp_path: Path) -> None:
    app = CMDAICodeTUI(workdir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tb = ToolBlock("todo", "")
        await app.query_one("#chat-scroll").mount(tb)
        tb.finish({"visible_steps": [{"text": "Step 1", "done": True}]})
        await pilot.pause()
        assert tb.is_expanded is True
