import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.subagent_runner import robust_json_parse, parse_tool_calls


def test_robust_pipe_quote_format():
    raw_call = '<|tool_call>call:read_file{path:<|"|>src/CMDAI CODE.md<|"|>}<tool_call|>'
    parsed = parse_tool_calls(raw_call)
    assert parsed is not None
    assert len(parsed) == 1
    assert parsed[0]["function"]["name"] == "read_file"
    assert parsed[0]["function"]["arguments"] == {"path": "src/CMDAI CODE.md"}


def test_unquoted_keys_format():
    raw_call = 'call:create_file{path: "src/block_type.py", content: "class BlockType: pass"}'
    parsed = parse_tool_calls(raw_call)
    assert parsed is not None
    assert len(parsed) == 1
    assert parsed[0]["function"]["name"] == "create_file"
    assert parsed[0]["function"]["arguments"]["path"] == "src/block_type.py"


def test_single_quoted_format():
    raw_call = "call:edit_file{'path': 'src/main.py', 'old_str': 'a', 'new_str': 'b'}"
    parsed = parse_tool_calls(raw_call)
    assert parsed is not None
    assert len(parsed) == 1
    assert parsed[0]["function"]["name"] == "edit_file"
    assert parsed[0]["function"]["arguments"]["path"] == "src/main.py"
