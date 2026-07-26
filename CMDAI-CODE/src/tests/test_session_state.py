from src.session import SessionState


def test_structured_session_state_round_trip():
    summary = """# Session State
## Objective
- User goal: Restore a saved chat.
- Definition of done: The transcript is rendered.
## Handoff
- Start with: Render the saved transcript.
- Do not repeat: unknown
"""

    state = SessionState.from_markdown(summary, "saved-chat")

    assert state.session_id == "saved-chat"
    assert state.goal == "Restore a saved chat."
    assert "## Objective" in state.to_prompt()
    assert "## Handoff" in state.to_prompt()
