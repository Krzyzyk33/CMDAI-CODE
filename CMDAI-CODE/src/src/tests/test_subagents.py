import sys
import os

# Add src to sys.path if not there
src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

def test_subagent_tools_exclude_wakeup_subagents():
    from src.subagent_runner import build_subagent_tool_definitions
    
    tools = build_subagent_tool_definitions()
    
    # Check that wakeup_subagents is NOT in the tools list
    for t in tools:
        assert t["function"]["name"] != "wakeup_subagents", "wakeup_subagents MUST NOT be available to subagents!"
        
    # Check that finish_task IS in the tools list
    finish_task_found = any(t["function"]["name"] == "finish_task" for t in tools)
    assert finish_task_found, "finish_task MUST be available to subagents!"
    
    print("test_subagent_tools_exclude_wakeup_subagents passed.")


def test_wakeup_subagents_return_format_and_lead_directive(monkeypatch):
    from src.tools.subagents import wakeup_subagents
    from src.subagent_runner import SubAgentResult

    class MockAgent:
        def __init__(self):
            self.model = object()
            self._last_subagent_edited_files = set()

    def mock_run_subagents_sequential(configs, agent_instance, cwd):
        return [
            SubAgentResult(
                status="done",
                note="Completed backend database migration.",
                files_edited=["src/db.py"],
                name="BackendAgent",
            )
        ]

    import src.subagent_runner as subagent_runner_module

    monkeypatch.setattr(
        subagent_runner_module,
        "run_subagents_sequential",
        mock_run_subagents_sequential,
    )

    agent = MockAgent()
    subagent_list = [{"name": "BackendAgent", "task": "Do migration"}]

    result = wakeup_subagents(subagents=subagent_list, agent_instance=agent)

    assert "Subagent 'BackendAgent' [✓]" in result
    assert "edited: src/db.py" in result
    assert "Note: Completed backend database migration." in result
    assert "[LEAD AGENT DIRECTIVE]" in result
    assert "As the Lead Agent, you MUST now run tools" in result
    assert any("db.py" in f for f in agent._last_subagent_edited_files)
    print("test_wakeup_subagents_return_format_and_lead_directive passed.")


def test_lead_agent_duty_rule():
    from src.context import SYSTEM_PROMPT

    assert "LEAD AGENT DUTY" in SYSTEM_PROMPT
    assert "When wakeup_subagents finishes, as the Lead Agent you MUST inspect subagent reports" in SYSTEM_PROMPT
    print("test_lead_agent_duty_rule passed.")


def test_lead_agent_can_run_tools_after_subagents(monkeypatch):
    from src.tools.subagents import wakeup_subagents
    from src.subagent_runner import SubAgentResult

    class MockAgent:
        def __init__(self):
            self.model = object()
            self._last_subagent_edited_files = set()
            self._fable_tested = False

    def mock_run_subagents_sequential(configs, agent_instance, cwd):
        return [
            SubAgentResult(
                status="done",
                note="Subagent finished.",
                files_edited=["src/refactored.py"],
                name="Worker",
            )
        ]

    import src.subagent_runner as subagent_runner_module

    monkeypatch.setattr(
        subagent_runner_module,
        "run_subagents_sequential",
        mock_run_subagents_sequential,
    )

    agent = MockAgent()
    wakeup_subagents(subagents=[{"name": "Worker", "task": "refactor"}], agent_instance=agent)

    assert agent._fable_tested is True, "_fable_tested MUST be True after subagents finish so Lead Agent can run tools without premature loop breaks!"
    print("test_lead_agent_can_run_tools_after_subagents passed.")


if __name__ == "__main__":
    test_subagent_tools_exclude_wakeup_subagents()
    print("All subagent tests passed successfully!")


