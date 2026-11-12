"""Tests for XML tool call parsing and formatting."""

from pathlib import Path
import pytest

from cmdai.agent.runner import AgentRunner
from cmdai.agent.tools import AgentContext
from cmdai.agent.tool_specs import format_tool_call_as_xml, get_native_tool_schemas


@pytest.fixture
def runner(tmp_path: Path) -> AgentRunner:
    ctx = AgentContext(workdir=str(tmp_path))
    return AgentRunner(ctx=ctx)


def test_parse_single_read_tool(runner: AgentRunner) -> None:
    text = 'Let me check.\n<tool:read path="src/main.py" start="1" end="50" />'
    calls = runner.parse_xml_tool_calls(text)
    assert len(calls) == 1
    name, args = calls[0]
    assert name == "read"
    assert args["path"] == "src/main.py"
    assert args["start"] == "1"
    assert args["end"] == "50"


def test_parse_multiple_tools_chronological(runner: AgentRunner) -> None:
    text = (
        "First I'll list files:\n"
        '<tool:ls path="." />\n'
        "Now I'll search:\n"
        '<tool:search query="CMDAICodeTUI" path="src" />\n'
    )
    segments = runner.parse_chronological_segments(text)
    kinds = [k for k, _ in segments]
    assert kinds.count("tool") == 2
    tools = [d for k, d in segments if k == "tool"]
    assert tools[0][0] == "ls"
    assert tools[1][0] == "search"
    assert tools[1][1]["query"] == "CMDAICodeTUI"


def test_parse_write_tool_with_body_content(runner: AgentRunner) -> None:
    text = (
        '<tool:write path="hello.py">\n'
        '<content>print("hello <world>")\n</content>\n'
        "</tool:write>"
    )
    calls = runner.parse_xml_tool_calls(text)
    assert len(calls) == 1
    name, args = calls[0]
    assert name == "write"
    assert args["path"] == "hello.py"
    assert 'print("hello <world>")' in args["content"]


def test_parse_edit_tool_with_old_new(runner: AgentRunner) -> None:
    text = (
        '<tool:edit path="app.py">\n'
        "<old>x = 1</old>\n"
        "<new>x = 2</new>\n"
        "</tool:edit>"
    )
    calls = runner.parse_xml_tool_calls(text)
    assert len(calls) == 1
    name, args = calls[0]
    assert name == "edit"
    assert args["path"] == "app.py"
    assert args["old"] == "x = 1"
    assert args["new"] == "x = 2"


def test_parse_plain_text_returns_no_tools(runner: AgentRunner) -> None:
    calls = runner.parse_xml_tool_calls("Just a normal answer with <b>HTML</b>.")
    assert calls == []


def test_format_tool_call_as_xml_roundtrip(runner: AgentRunner) -> None:
    xml = format_tool_call_as_xml("read", {"path": "README.md", "start": "1"})
    calls = runner.parse_xml_tool_calls(xml)
    assert len(calls) == 1
    assert calls[0][0] == "read"
    assert calls[0][1]["path"] == "README.md"


def test_native_tool_schemas_non_empty() -> None:
    schemas = get_native_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert {"read", "write", "edit", "ls", "command"}.issubset(names)
