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

if __name__ == "__main__":
    test_subagent_tools_exclude_wakeup_subagents()
