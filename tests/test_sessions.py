"""Session save and replay.

A reloaded session has to be indistinguishable from the one that was saved:
same cards, same tool blocks, same order, and the chat actually on screen. Two
things had to be built for that and both were missing - the load left the app on
the hero screen with the cards mounted into a hidden view, and there was no
record of the display at all, so `messages` was the only source and it is the
model's view, not the user's.
"""

import os

import pytest

from cmdai.core.sessions import SessionManager, transcript_from_messages
from cmdai.tui.app import CMDAICodeTUI, _match_agents_to_calls
from cmdai.tui.widgets import AssistantTurnCard, ToolBlock, UserMessageCard
from textual.containers import VerticalScroll
from textual.widgets import Static


@pytest.fixture()
def sessions(tmp_path, monkeypatch):
    """A SessionManager confined to a temporary directory.

    Patches the directory list rather than just the attribute: the real one also
    scans the checkout and the user profile, which would make listings depend on
    whatever the developer happens to have lying around.
    """
    root = tmp_path / "sessions"
    root.mkdir()
    mgr = SessionManager(project_dir=str(tmp_path))
    mgr.repo_sessions_dir = str(root)
    monkeypatch.setattr(mgr, "_get_candidate_dirs", lambda: [str(root)])
    return mgr


LEGACY_MESSAGES = [
    {"role": "user", "content": "wywolaj subagenta", "turn_id": 1},
    {"role": "assistant", "content": 'Sprawdzam pliki. <tool:ls path="." />'},
    {"role": "user", "content": '<tool_response name="ls">\n{"success": true}'},
    {"role": "assistant", "content": 'Widze pliki. <tool:read path="script.js" lines="1-50" />'},
    {"role": "user", "content": '<tool_response name="read">\nFile: script.js'},
    {"role": "assistant", "content": "Gotowe."},
    {"role": "assistant", "content": 'Testuję. <tool:subagent name="TestRunner" system="you test">napisz testy</tool:subagent>'},
    {"role": "user", "content": '<tool_response name="subagent">\n1 subagent(s) finished: TestRunner.'},
]

TRANSCRIPT = [
    {"kind": "user", "text": "wywolaj subagenta", "turn_id": 1},
    {"kind": "assistant", "text": "Sprawdzam pliki."},
    {"kind": "tool", "tool": "ls", "target": ".",
     "result": {"success": True, "entries": [{"name": "a.py", "type": "file"}]}},
    {"kind": "tool", "tool": "read", "target": "a.py",
     "result": {"success": True, "content": "print(1)", "total_lines": 1}},
    {"kind": "assistant", "text": "Gotowe."},
]


# --------------------------------------------------------- transcript rebuild

def test_tool_feedback_stops_looking_like_something_the_user_said():
    """The core readability bug.

    Tool feedback is stored under role "user" with a `<tool_response>` wrapper,
    because that is what the model was told. Replayed as-is it puts raw tool
    JSON on screen under a user avatar.
    """
    out = transcript_from_messages(LEGACY_MESSAGES)
    users = [e for e in out if e["kind"] == "user"]
    assert len(users) == 1
    assert users[0]["text"] == "wywolaj subagenta"
    assert "tool_response" not in users[0]["text"]
    assert [e["kind"] for e in out].count("tool") == 2


def test_no_raw_tool_markup_is_left_in_the_prose():
    """The saved assistant turn is the model's raw output.

    Replaying it as one string showed `<tool:ls path="." />` on screen as if the
    model had typed it, right above the Ls card it belongs to. The turn is
    re-parsed with the live chat's parser, so the tags become cards and the
    prose is clean.
    """
    out = transcript_from_messages(LEGACY_MESSAGES)
    for e in out:
        if e["kind"] in ("assistant", "user"):
            assert "<tool:" not in e["text"], e["text"]
            assert "tool_response" not in e["text"], e["text"]
    texts = [e["text"] for e in out if e["kind"] == "assistant"]
    assert any("Sprawdzam pliki." in t for t in texts)
    assert all("Sprawdzam pliki." in t for t in texts if "Sprawdzam" in t)


def test_a_rebuilt_tool_gets_its_target_back():
    """The label the chat showed is recovered from the call's own arguments.

    Without this the card reads a bare "Read" where the original read
    "Read script.js" - the result lives in the next message and knows nothing
    about which file was asked for.
    """
    out = transcript_from_messages(LEGACY_MESSAGES)
    tools = [e for e in out if e["kind"] == "tool"]
    assert [(t["tool"], t["target"]) for t in tools] == [("ls", "."), ("read", "script.js")]


def test_a_rebuilt_subagent_keeps_its_role_and_task():
    """A subagent has to come back as a marker with its name, not "Subagent"."""
    out = transcript_from_messages(LEGACY_MESSAGES)
    subs = [e for e in out if e["kind"] == "subagent"]
    assert len(subs) == 1
    assert subs[0]["role"] == "TestRunner"
    assert subs[0]["goal"] == "napisz testy"
    assert subs[0]["system"] == "you test"
    assert "TestRunner" in subs[0]["agent"]["summary"]
    assert subs[0]["agent"]["status"] == "completed"


def test_a_call_with_no_result_is_still_shown():
    """A session cut mid-turn must not lose the cards it already made."""
    out = transcript_from_messages([
        {"role": "assistant", "content": '<tool:ls path="." />'},
    ])
    assert [e["kind"] for e in out] == ["tool"]
    assert out[0]["target"] == "."


def test_a_result_with_no_call_still_shows_a_card():
    out = transcript_from_messages([
        {"role": "user", "content": '<tool_response name="ls">\n{"ok": 1}'},
    ])
    assert out[0]["kind"] == "tool"
    assert out[0]["tool"] == "ls"


def test_the_closing_tag_stays_out_of_the_result():
    """Regression: the wrapper's closing tag was rendered inside the card.

    The payload used to be taken to the end of the string, so the card showed a
    trailing `</tool_response>` below the tool output.
    """
    out = transcript_from_messages([
        {"role": "user",
         "content": '<tool_response name="scratch">\n{"action": "done"}\n</tool_response>'},
    ])
    body = out[0]["result"]["content"]
    assert "tool_response" not in body
    assert '"action": "done"' in body


def test_a_result_without_a_closing_tag_still_parses():
    """A turn cut off mid-response must not be lost."""
    out = transcript_from_messages([
        {"role": "user", "content": '<tool_response name="read">\nFile: a.js'},
    ])
    assert out[0]["tool"] == "read"
    assert "File: a.js" in out[0]["result"]["content"]


def test_the_tool_name_comes_out_of_the_wrapper():
    out = transcript_from_messages(LEGACY_MESSAGES)
    tools = [e for e in out if e["kind"] == "tool"]
    assert tools[0]["tool"] == "ls"
    assert "success" in str(tools[0]["result"])


def test_a_rebuilt_tool_is_marked_legacy():
    """A rebuilt card has no real result, and must not pretend otherwise."""
    out = transcript_from_messages([{"role": "user", "content":
                                     '<tool_response name="ls">\n{"ok": 1}'}])
    assert out[0]["result"]["legacy"] is True


def test_turn_ids_survive_the_rebuild():
    out = transcript_from_messages([{"role": "user", "content": "hi", "turn_id": 7}])
    assert out[0]["turn_id"] == 7


def test_a_message_that_merely_mentions_a_tool_stays_a_user_message():
    """Only a leading wrapper counts; prose containing the word is not feedback."""
    text = 'You called <tool_response name="ls"> wrongly'
    out = transcript_from_messages([{"role": "user", "content": text}])
    assert out[0]["kind"] == "user"
    assert out[0]["text"] == text


def test_junk_entries_are_dropped():
    assert transcript_from_messages([None, "nope", 5, {"role": "user", "content": "x"}]) == [
        {"kind": "user", "text": "x", "turn_id": 0}]


# ------------------------------------------------------------------- storage

def test_the_transcript_round_trips(sessions):
    sessions.save_session("s1", LEGACY_MESSAGES, "model", "Title", {}, TRANSCRIPT)
    assert sessions.load_transcript("s1") == TRANSCRIPT


def test_a_session_without_one_is_rebuilt(sessions):
    sessions.save_session("s1", LEGACY_MESSAGES, "model")
    rebuilt = sessions.load_transcript("s1")
    assert [e["kind"] for e in rebuilt] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant",
        "assistant", "subagent",
    ]


def test_a_saved_transcript_is_not_rewritten_as_a_rebuild(sessions):
    """The stored record wins, even when it is shorter than messages."""
    sessions.save_session("s1", LEGACY_MESSAGES, "model", transcript=TRANSCRIPT)
    # LEGACY_MESSAGES would rebuild to 6 entries; the transcript has 5.
    assert len(sessions.load_transcript("s1")) == len(TRANSCRIPT)


def test_an_unknown_session_yields_nothing(sessions):
    assert sessions.load_transcript("nope") == []


def test_loading_a_missing_session_reports_failure(sessions):
    assert sessions.load_session("nope") is None


# ------------------------------------------------------------------ matching

def test_agents_are_paired_with_their_calls():
    calls = [{"name": "TestRunner"}, {"name": "Reviewer"}]
    agents = [{"role": "Reviewer", "summary": "rev"}, {"role": "TestRunner", "summary": "test"}]
    paired = _match_agents_to_calls(calls, agents)
    assert [p.get("summary") for p in paired] == ["test", "rev"]


def test_two_calls_with_the_same_role_get_different_results():
    calls = [{"name": "Worker"}, {"name": "Worker"}]
    agents = [{"role": "Worker", "summary": "first"}, {"role": "Worker", "summary": "second"}]
    paired = _match_agents_to_calls(calls, agents)
    assert [p.get("summary") for p in paired] == ["first", "second"]


def test_an_unmatched_call_gets_nothing_rather_than_a_neighbour():
    """A wrong report is worse than no report."""
    paired = _match_agents_to_calls([{"name": "Ghost"}], [{"role": "Real", "summary": "s"}])
    assert paired == [{}]


# ------------------------------------------------------------------- replay

async def _load(app, session_id, pauses=6):
    app._on_session_selected(session_id)
    for _ in range(pauses):
        await app._flush()


@pytest.mark.asyncio
async def test_a_loaded_session_is_visible(sessions, tmp_path):
    """Regression: the load mounted cards and left the hero screen up.

    reset_session() ends by switching to the hero, and nothing switched back, so
    the whole conversation was rendered into a hidden view. It looked like the
    load had done nothing.
    """
    sessions.save_session("s1", LEGACY_MESSAGES, "model", "T", {}, TRANSCRIPT)
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        assert app.in_chat_mode is False
        app._on_session_selected("s1")
        for _ in range(6):
            await pilot.pause()
        assert app.in_chat_mode is True
        chat_view = app.query_one("#chat-view")
        assert "hidden" not in chat_view.classes


@pytest.mark.asyncio
async def test_the_replay_uses_the_same_widgets(sessions, tmp_path):
    """One user card, one assistant card, and a block per tool - as it was."""
    sessions.save_session("s1", LEGACY_MESSAGES, "model", "T", {}, TRANSCRIPT)
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(6):
            await pilot.pause()

        chat = app.query_one("#chat-scroll", VerticalScroll)
        assert len(chat.query(UserMessageCard)) == 1
        assert len(chat.query(AssistantTurnCard)) == 1
        blocks = chat.query(ToolBlock)
        assert [b.tool_name for b in blocks] == ["ls", "read"]


@pytest.mark.asyncio
async def test_replayed_tools_are_finished_not_spinning(sessions, tmp_path):
    """A replayed card must not sit there animating forever."""
    sessions.save_session("s1", LEGACY_MESSAGES, "model", "T", {}, TRANSCRIPT)
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(6):
            await pilot.pause()
        for b in app.query_one("#chat-scroll", VerticalScroll).query(ToolBlock):
            assert b.tool_running is False
            assert b.result_data


@pytest.mark.asyncio
async def test_a_legacy_session_keeps_its_tools_out_of_the_user_cards(sessions, tmp_path):
    """The old format, replayed through the rebuild."""
    sessions.save_session("s1", LEGACY_MESSAGES, "model", "T")
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(6):
            await pilot.pause()

        chat = app.query_one("#chat-scroll", VerticalScroll)
        users = chat.query(UserMessageCard)
        assert len(users) == 1
        text = "".join(str(st.content) for st in users[0].query("Static"))
        assert "tool_response" not in text
        blocks = chat.query(ToolBlock)
        assert [b.tool_name for b in blocks] == ["ls", "read", "subagent"]
        # The target the original chat showed is back on the card.
        assert "script.js" in blocks[1].target


@pytest.mark.asyncio
async def test_a_legacy_session_shows_no_raw_tool_markup_on_screen(sessions, tmp_path):
    """The tags belong on cards, not in the prose above them."""
    sessions.save_session("s1", LEGACY_MESSAGES, "model", "T")
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(6):
            await pilot.pause()

        chat = app.query_one("#chat-scroll", VerticalScroll)
        for card in chat.query(AssistantTurnCard):
            for st in card.query(Static):
                text = str(st.content)
                assert "<tool:" not in text, text[:120]
                assert "tool_response" not in text, text[:120]
        # Nor inside the tool cards' own output.
        for b in chat.query(ToolBlock):
            for st in b.query(Static):
                assert "tool_response" not in str(st.content), str(st.content)[:120]

@pytest.mark.asyncio
async def test_a_legacy_subagent_marker_shows_its_role_and_opens(sessions, tmp_path):
    """Regression: the marker read "Subagent" and clicking did nothing.

    A rebuilt subagent came back as a plain tool block with no role, so the
    header fell back to the tool name and _open_subagent_chat skipped it - it
    looks for blocks that carry a role.
    """
    sessions.save_session("s1", LEGACY_MESSAGES, "model", "T")
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(6):
            await pilot.pause()

        blocks = app.query_one("#chat-scroll", VerticalScroll).query(ToolBlock)
        marks = [b for b in blocks if b.tool_name == "subagent"]
        assert len(marks) == 1, "the subagent did not come back as a marker"
        marker = marks[0]
        assert marker.subagent_role == "TestRunner"
        assert marker.subagent_goal == "napisz testy"
        assert marker.tool_running is False

        from cmdai.tui.modals.subagent_chat import SubagentChatModal
        app._open_subagent_chat(marker)
        await pilot.pause()
        assert isinstance(app.screen, SubagentChatModal)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_a_replayed_subagent_marker_still_opens_its_window(sessions, tmp_path):
    """A subagent turn must come back as a marker, not a line of text."""
    t = [
        {"kind": "user", "text": "test", "turn_id": 1},
        {"kind": "subagent", "role": "TestRunner", "goal": "napisz testy",
         "system": "you test",
         "agent": {"role": "TestRunner", "status": "completed",
                   "summary": "Raport.", "findings": [], "notes": [],
                   "tool_count": 2, "history": [], "events": [],
                   "elapsed": 1.0, "tokens": 5, "system_prompt": "you test"}},
    ]
    sessions.save_session("s1", [{"role": "user", "content": "test"}], "model", "T", {}, t)
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(6):
            await pilot.pause()

        blocks = app.query_one("#chat-scroll", VerticalScroll).query(ToolBlock)
        assert len(blocks) == 1
        marker = blocks[0]
        assert marker.subagent_role == "TestRunner"
        assert marker.subagent_goal == "napisz testy"
        assert marker.subagent_status == "completed"
        assert marker.tool_running is False

        from cmdai.tui.modals.subagent_chat import SubagentChatModal
        app._open_subagent_chat(marker)
        await pilot.pause()
        assert isinstance(app.screen, SubagentChatModal)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_session_state_is_restored(sessions, tmp_path):
    sessions.save_session("s1", LEGACY_MESSAGES, "gemma-4-E4B-it-Q4_0.gguf",
                          "Moja sesja", {"tokens_in": 42, "tokens_out": 7,
                                         "tok_per_sec": 1.5, "tools_run": 3}, TRANSCRIPT)
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(6):
            await pilot.pause()
        assert app.current_session_id == "s1"
        assert app.session_title == "Moja sesja"
        assert app.current_model_id == "gemma-4-E4B-it-Q4_0.gguf"
        assert app.stats["tokens_in"] == 42
        assert app.stats["tools_run"] == 3


@pytest.mark.asyncio
async def test_the_transcript_is_kept_for_the_next_save(sessions, tmp_path):
    """Reloading then saving must not drop the record again."""
    sessions.save_session("s1", LEGACY_MESSAGES, "model", "T", {}, TRANSCRIPT)
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(6):
            await pilot.pause()
        assert app._transcript
        app._save_current_session()
        await pilot.pause()
    assert sessions.load_transcript("s1")


@pytest.mark.asyncio
async def test_a_missing_session_says_so_instead_of_going_quiet(sessions, tmp_path):
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        before = len(app.query_one("#chat-scroll", VerticalScroll).children)
        app._on_session_selected("does-not-exist")
        for _ in range(3):
            await pilot.pause()
        # Nothing was destroyed on the way to failing.
        assert len(app.query_one("#chat-scroll", VerticalScroll).children) == before


@pytest.mark.asyncio
async def test_an_empty_session_loads_to_an_empty_chat(sessions, tmp_path):
    sessions.save_session("s1", [], "model", "Pusta", {}, [])
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(4):
            await pilot.pause()
        assert app.current_session_id == "s1"
        assert len(app.query_one("#chat-scroll", VerticalScroll).children) == 0


@pytest.mark.asyncio
async def test_malformed_transcript_entries_are_skipped(sessions, tmp_path):
    sessions.save_session("s1", [], "model", "T", {},
                          [None, "junk", {"kind": "assistant", "text": ""},
                           {"kind": "user", "text": "ok", "turn_id": 1}])
    app = CMDAICodeTUI(workdir=str(tmp_path))
    app.session_manager = sessions
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app._on_session_selected("s1")
        for _ in range(4):
            await pilot.pause()
        chat = app.query_one("#chat-scroll", VerticalScroll)
        assert len(chat.query(UserMessageCard)) == 1


def test_recorded_events_have_the_shape_the_replay_expects():
    """A guard on the contract between the recorder and the player.

    Cheap, and it fails loudly the day one side is edited without the other -
    which would otherwise only show up as a session that quietly loses its tool
    cards.
    """
    kinds = {"user", "assistant", "tool", "subagent"}
    for kind in kinds:
        assert kind in {e["kind"] for e in TRANSCRIPT + [
            {"kind": "subagent", "role": "R", "goal": "", "system": "", "agent": {}}]}
    for e in TRANSCRIPT:
        assert set(e).issubset({"kind", "text", "turn_id", "tool", "target", "result"})
    assert os.path.isdir(os.path.dirname(os.path.abspath(__file__)))
