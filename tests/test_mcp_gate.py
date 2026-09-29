"""Permission gate tests: <tool:mcp />, <tool:mcp open>, and the runtime gate.

The gate is runner logic, not protocol, so most of these drive it with a
stub client. That keeps them fast and lets each permission branch be
forced deliberately, which a real server will not do on demand. A few
tests at the end run the whole path against the SDK fixture.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from cmdai.agent.runner import AgentRunner  # noqa: E402
from cmdai.agent.tools import AgentContext  # noqa: E402
from cmdai.mcp.client import (  # noqa: E402
    PERM_DESTRUCTIVE,
    PERM_READ,
    PERM_WRITE,
    TOOL_NAME_PREFIX,
    classify_tool,
    qualified_tool_name,
)

FIXTURE = os.path.join(ROOT, "tools", "mcp_fixture_server.py")
FIXTURE_VENV_PY = os.path.join(ROOT, "tools", "mcp-fixture-venv", "Scripts", "python.exe")


# ------------------------------------------------------------- stub client


def _tool(name, annotations=None, description="", props=None, required=None):
    schema = {"type": "object", "properties": props or {}}
    if required:
        schema["required"] = required
    tool = {"name": name, "description": description, "inputSchema": schema}
    if annotations is not None:
        tool["annotations"] = annotations
    return tool


class StubServer:
    def __init__(self, name, tools, status="connected", error=""):
        self.name = name
        self.tools = tools
        self.status = status
        self.error = error
        self.calls = []
        self.stderr_log = []

    def status_lines(self):
        return [f"+  {self.name:<18} {self.status:<13} {len(self.tools)} tools"]


class StubClient:
    """Implements just the surface the runner touches."""

    def __init__(self, servers):
        self.servers = servers
        self.calls = []
        self.default_result = {
            "success": True, "server": "", "tool": "",
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }

    def server_names(self):
        return sorted(self.servers)

    def status_lines(self):
        return [self.servers[n].status_lines()[0] for n in sorted(self.servers)]

    def dynamic_tools(self):
        out = {}
        for name, server in self.servers.items():
            if server.status != "connected":
                continue
            for tool in server.tools:
                real = tool["name"]
                out[qualified_tool_name(name, real).lower()] = (name, real)
        return out

    def permission_for(self, qualified):
        key = qualified.lower()
        if not key.startswith(TOOL_NAME_PREFIX):
            return PERM_DESTRUCTIVE
        server_key, _, tool_part = key[len(TOOL_NAME_PREFIX):].partition("__")
        server = self.servers.get(server_key)
        if server is None:
            return PERM_DESTRUCTIVE
        for tool in server.tools:
            if tool["name"] == tool_part:
                return classify_tool(tool)
        return PERM_DESTRUCTIVE

    def tool_definitions(self, only_servers=None):
        out = []
        for name, server in self.servers.items():
            if only_servers is not None and name not in only_servers:
                continue
            if server.status != "connected":
                continue
            for tool in server.tools:
                out.append({
                    "name": qualified_tool_name(name, tool["name"]),
                    "description": tool.get("description", ""),
                    "inputSchema": tool.get("inputSchema", {}),
                    "permission": classify_tool(tool),
                    "server": name,
                })
        return out

    def call_tool(self, server, tool, args):
        self.calls.append((server, tool, dict(args)))
        self.servers[server].calls.append((tool, dict(args)))
        return dict(self.default_result, server=server, tool=tool)


def make_client(**tools_by_server):
    servers = {name: StubServer(name, tools) for name, tools in tools_by_server.items()}
    return StubClient(servers)


def make_runner(client, answers=None, vision=False):
    ctx = AgentContext(workdir=".")
    asked = []

    def ask_handler(question, options):
        asked.append((question, list(options)))
        if answers is None:
            return "No"
        return answers.pop(0) if answers else "No"

    runner = AgentRunner(
        ctx=ctx,
        mcp_client=client,
        ask_handler=ask_handler,
        vision_capable=(lambda: vision),
    )
    return runner, asked


DOCS_TOOLS = [
    _tool("read_document", {"readOnlyHint": True}, "Read one.",
          {"id": {"type": "string"}}, required=["id"]),
    _tool("create_document", {"readOnlyHint": False}, "Create.",
          {"title": {"type": "string"}}, required=["title"]),
    _tool("delete_document", {"destructiveHint": True, "readOnlyHint": False},
          "Delete.", {"id": {"type": "string"}}, required=["id"]),
    _tool("bare_tool", None, "No annotations at all.", {}),
]


@pytest.fixture
def docs():
    return make_client(docs=DOCS_TOOLS)


# ------------------------------------------------------------------ listing


class TestListStep:
    def test_listing_needs_no_arguments(self, docs):
        runner, asked = make_runner(docs)
        out = runner.execute_tool("mcp", {}, notify=False)
        assert out["success"] and "docs" in "\n".join(out["servers"])
        assert asked == [], "listing must not prompt"

    def test_listing_when_nothing_configured(self):
        runner, _ = make_runner(make_client())
        out = runner.execute_tool("mcp", {}, notify=False)
        assert out["success"] and "cmdai mcp add" in out["note"]

    def test_listing_tells_the_model_how_to_open(self, docs):
        runner, _ = make_runner(docs)
        out = runner.execute_tool("mcp", {}, notify=False)
        assert "open server" in out["note"]


class TestOpenStep:
    def test_open_returns_schemas_and_call_syntax(self, docs):
        runner, _ = make_runner(docs)
        out = runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        assert out["success"]
        text = out["schema_text"]
        assert "mcp__docs__read_document" in text
        assert "<tool:mcp__docs__read_document>" in text
        assert "perm: read" in text and "perm: write" in text
        assert "perm: destructive" in text

    def test_open_shows_required_arguments(self, docs):
        runner, _ = make_runner(docs)
        out = runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        assert "args: id" in out["schema_text"]

    def test_open_does_not_prompt(self, docs):
        runner, asked = make_runner(docs)
        runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        assert asked == []

    def test_open_unknown_server_lists_what_exists(self, docs):
        runner, _ = make_runner(docs)
        out = runner.execute_tool("mcp", {"server": "ghost"}, notify=False)
        assert out["success"] is False
        assert "docs" in out["error"]

    def test_open_disconnected_server_reports_why(self):
        client = make_client(docs=DOCS_TOOLS)
        client.servers["docs"].status = "error"
        client.servers["docs"].error = "boom"
        runner, _ = make_runner(client)
        out = runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        assert out["success"] is False and "boom" in out["error"]


# -------------------------------------------------------------------- gate


class TestPermissionGate:
    def _open(self, runner):
        return runner.execute_tool("mcp", {"server": "docs"}, notify=False)

    def test_read_tool_never_asks(self, docs):
        runner, asked = make_runner(docs, answers=["No"] * 5)
        self._open(runner)
        out = runner.execute_tool("mcp__docs__read_document", {"id": "a"}, notify=False)
        assert out["success"] is True
        assert asked == [], "a read tool must not prompt"
        assert docs.servers["docs"].calls[-1] == ("read_document", {"id": "a"})

    def test_write_tool_asks_first(self, docs):
        runner, asked = make_runner(docs, answers=["Yes"])
        self._open(runner)
        out = runner.execute_tool("mcp__docs__create_document", {"title": "t"}, notify=False)
        assert out["success"] is True
        assert len(asked) == 1
        assert "writes data" in asked[0][0]

    def test_declining_blocks_the_call(self, docs):
        runner, asked = make_runner(docs, answers=["No"])
        self._open(runner)
        out = runner.execute_tool("mcp__docs__create_document", {"title": "t"}, notify=False)
        assert out["success"] is False
        assert "declined" in out["error"].lower()
        assert docs.servers["docs"].calls == [], "a declined write must not reach the server"

    def test_always_grants_for_the_whole_session(self, docs):
        runner, asked = make_runner(docs, answers=["Always"])
        self._open(runner)
        first = runner.execute_tool("mcp__docs__create_document", {"title": "1"}, notify=False)
        second = runner.execute_tool("mcp__docs__create_document", {"title": "2"}, notify=False)
        assert first["success"] and second["success"]
        assert len(asked) == 1, "the second write must not ask again"

    def test_yes_is_not_stored(self, docs):
        runner, asked = make_runner(docs, answers=["Yes", "Yes"])
        self._open(runner)
        runner.execute_tool("mcp__docs__create_document", {"title": "1"}, notify=False)
        runner.execute_tool("mcp__docs__create_document", {"title": "2"}, notify=False)
        assert len(asked) == 2, "a one-off Yes must not become a standing grant"

    def test_grant_does_not_leak_between_servers(self):
        client = make_client(docs=DOCS_TOOLS, other=[
            _tool("create_document", {"readOnlyHint": False}, "Create.", {}),
        ])
        runner, asked = make_runner(client, answers=["Always", "Always"])
        runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        runner.execute_tool("mcp__docs__create_document", {"title": "1"}, notify=False)
        runner.execute_tool("mcp", {"server": "other"}, notify=False)
        out = runner.execute_tool("mcp__other__create_document", {"title": "2"}, notify=False)
        assert out["success"] is True
        assert len(asked) == 2, "consent granted for one server must not cover another"


class TestDestructiveAlwaysAsks:
    def _open(self, runner):
        return runner.execute_tool("mcp", {"server": "docs"}, notify=False)

    def test_destructive_asks_even_after_always_grant(self, docs):
        runner, asked = make_runner(docs, answers=["Always", "Yes", "Yes"])
        self._open(runner)
        runner.execute_tool("mcp__docs__create_document", {"title": "1"}, notify=False)
        out = runner.execute_tool("mcp__docs__delete_document", {"id": "x"}, notify=False)
        assert out["success"] is True
        assert len(asked) == 2, "destructive must still ask after a standing grant"
        assert "destructive" in asked[1][0]

    def test_destructive_asks_every_single_time(self, docs):
        runner, asked = make_runner(docs, answers=["Yes", "Yes", "Yes"])
        self._open(runner)
        runner.execute_tool("mcp__docs__delete_document", {"id": "a"}, notify=False)
        runner.execute_tool("mcp__docs__delete_document", {"id": "b"}, notify=False)
        assert len(asked) == 2, "no standing consent is possible for deletion"

    def test_declining_destructive_blocks(self, docs):
        runner, asked = make_runner(docs, answers=["No"])
        self._open(runner)
        out = runner.execute_tool("mcp__docs__delete_document", {"id": "a"}, notify=False)
        assert out["success"] is False
        assert docs.servers["docs"].calls == []

    def test_unannotated_tool_behaves_destructively(self, docs):
        # The spec says annotations are untrusted and says nothing about their
        # absence. Treating a missing annotation as harmless is how an
        # unreviewed destructive server gets wired in and run unattended.
        runner, asked = make_runner(docs, answers=["Yes", "Yes"])
        self._open(runner)
        first = runner.execute_tool("mcp__docs__bare_tool", {}, notify=False)
        second = runner.execute_tool("mcp__docs__bare_tool", {}, notify=False)
        assert first["success"] and second["success"]
        assert len(asked) == 2, "an unannotated tool must ask every time"


class TestGateRequiresOpening:
    def test_calling_before_open_is_refused_with_a_hint(self, docs):
        runner, asked = make_runner(docs, answers=["Yes"])
        out = runner.execute_tool("mcp__docs__read_document", {"id": "a"}, notify=False)
        assert out["success"] is False
        assert "not open" in out["error"]
        assert "mcp open" in out["error"]
        assert asked == [], "a refused call must not prompt for permission"
        assert docs.servers["docs"].calls == []

    def test_unknown_mcp_name_is_refused_with_a_hint(self, docs):
        runner, _ = make_runner(docs)
        runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        out = runner.execute_tool("mcp__docs__imagined_tool", {}, notify=False)
        assert out["success"] is False
        assert "Do not guess" in out["error"]


class TestNativeToolsAreNotGated:
    def test_screenshot_is_not_an_mcp_entry(self, docs):
        runner, _ = make_runner(docs)
        runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        assert "screenshot" not in runner.mcp.dynamic_tools()

    def test_vision_is_not_an_mcp_entry(self, docs):
        runner, _ = make_runner(docs)
        assert "vision" not in runner.mcp.dynamic_tools()


# ----------------------------------------------------------------- prompt


class TestPromptPrefixStability:
    def test_system_prompt_does_not_change_when_a_server_opens(self, docs):
        """The reason schemas travel in the transcript, not the prompt.

        llama.cpp keys its prompt cache on the prefix. If opening a server
        rewrote the system prompt, every open would invalidate the cache and
        every turn after it would pay full prefill on a local model.
        """
        from cmdai.agent.runner import get_agent_system_prompt
        before = get_agent_system_prompt(workdir=".", mode="auto")
        runner, _ = make_runner(docs)
        runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        after = get_agent_system_prompt(workdir=".", mode="auto")
        assert before == after

    def test_prompt_describes_only_the_two_step_protocol(self):
        from cmdai.agent.runner import get_agent_system_prompt
        prompt = get_agent_system_prompt(workdir=".", mode="auto")
        assert "<tool:mcp />" in prompt
        assert 'mcp open server="NAME"' in prompt
        assert "mcps" not in prompt
        # The prompt may show the shape of a call, but never a real server or
        # tool name: those are what the cache prefix must not depend on.
        import re
        concrete = re.findall(r"mcp__[a-z0-9_]+__(?!NAME|TOOL)[a-z0-9_]+", prompt)
        assert not concrete, f"concrete MCP tool names leaked into the prompt: {concrete}"


# ------------------------------------------------------------- arguments


class TestArgumentHandling:
    def test_json_body_becomes_arguments(self, docs):
        runner, _ = make_runner(docs, answers=["Yes"])
        runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        runner.execute_tool("mcp__docs__create_document", {"title": "hello"}, notify=False)
        assert docs.servers["docs"].calls[-1] == ("create_document", {"title": "hello"})

    def test_malformed_json_is_explained_not_swallowed(self, docs):
        runner, _ = make_runner(docs)
        runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        out = runner.execute_tool("mcp__docs__create_document",
                                  {"__json_error__": "Arguments for X are not valid JSON"}, notify=False)
        assert out["success"] is False
        assert "not valid JSON" in out["error"]
        assert docs.servers["docs"].calls == []

    def test_missing_required_argument_is_caught_before_the_call(self):
        client = make_client(strict=DOCS_TOOLS)
        runner, _ = make_runner(client, answers=["Always"])
        runner.execute_tool("mcp", {"server": "strict"}, notify=False)
        out = runner.execute_tool("mcp__strict__read_document", {}, notify=False)
        assert out["success"] is False
        assert "Invalid arguments" in out["error"]
        assert client.calls == [], "invalid arguments must not reach the server"

    def test_wrong_argument_type_is_caught(self):
        client = make_client(strict=DOCS_TOOLS)
        runner, _ = make_runner(client, answers=["Always"])
        runner.execute_tool("mcp", {"server": "strict"}, notify=False)
        out = runner.execute_tool("mcp__strict__read_document", {"id": 42}, notify=False)
        assert out["success"] is False
        assert client.calls == []

    def test_extra_keys_are_allowed_because_the_schema_allows_them(self):
        # Worth pinning: JSON Schema permits unknown properties unless the
        # schema says additionalProperties: false. A test that assumed
        # otherwise would be asserting a behaviour we never implemented.
        client = make_client(strict=DOCS_TOOLS)
        runner, _ = make_runner(client, answers=["Always"])
        runner.execute_tool("mcp", {"server": "strict"}, notify=False)
        out = runner.execute_tool("mcp__strict__read_document",
                                  {"id": "a", "extra": True}, notify=False)
        assert out["success"] is True
        assert client.calls[-1][2]["extra"] is True or client.calls[-1][2]["extra"] == "true"

    def test_validation_message_names_the_field(self):
        client = make_client(strict=DOCS_TOOLS)
        runner, _ = make_runner(client, answers=["Always"])
        runner.execute_tool("mcp", {"server": "strict"}, notify=False)
        out = runner.execute_tool("mcp__strict__read_document", {}, notify=False)
        assert out["success"] is False and "id" in out["error"]


# ----------------------------------------------------------------- images


class TestImages:
    def _client_returning_image(self):
        client = make_client(docs=DOCS_TOOLS)
        client.default_result = {
            "success": True, "server": "docs", "tool": "echo_image",
            "result": {"content": [{
                "type": "image",
                "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                "mimeType": "image/png",
            }]},
        }
        return client

    def test_text_only_model_is_told_not_shown(self):
        client = self._client_returning_image()
        runner, _ = make_runner(client, vision=False)
        runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        out = runner.execute_tool("mcp__docs__read_document", {"id": "a"}, notify=False)
        assert out["success"] is True
        assert "not vision-capable" in out["text"]
        assert "images" not in out

    def test_vision_model_gets_the_image_queued(self):
        client = self._client_returning_image()
        runner, _ = make_runner(client, vision=True)
        runner.execute_tool("mcp", {"server": "docs"}, notify=False)
        out = runner.execute_tool("mcp__docs__read_document", {"id": "a"}, notify=False)
        assert out.get("images"), "image should have been saved and queued"
        assert os.path.isfile(out["images"][0])
        assert runner.ctx.pending_images, "must reuse the vision queue"
        for path in out["images"]:
            try:
                os.remove(path)
            except OSError:
                pass


# ------------------------------------------------------- end to end


needs_fixture = pytest.mark.skipif(
    not os.path.isfile(FIXTURE) or not os.path.isfile(FIXTURE_VENV_PY),
    reason="MCP fixture venv not built",
)


@needs_fixture
class TestAgainstRealFixture:
    def _runner(self):
        from cmdai.mcp.client import MCPServer
        server = MCPServer("xph-fixture", command=FIXTURE_VENV_PY,
                           args=[FIXTURE], timeout=20.0)
        if not server.connect():
            pytest.skip(f"fixture did not connect: {server.error}")
        client = make_client(xph_fixture=[dict(t) for t in server.tools])
        # Route the stub's calls at the real server.
        def call_tool(srv, tool, args):
            result = server.call_tool(tool, args)
            return {"success": True, "server": srv, "tool": tool, "result": result}
        client.call_tool = call_tool
        runner, asked = make_runner(client, answers=["Always", "Yes", "Yes", "Yes"])
        return runner, asked, server, client

    def test_full_drill_down(self):
        runner, asked, server, client = self._runner()
        try:
            listing = runner.execute_tool("mcp", {}, notify=False)
            assert listing["success"]

            opened = runner.execute_tool("mcp", {"server": "xph_fixture"}, notify=False)
            assert opened["success"]
            assert "mcp__xph_fixture__read_document" in opened["schema_text"]
            assert "perm: read" in opened["schema_text"]

            read = runner.execute_tool("mcp__xph_fixture__read_document",
                                       {"doc_id": "alpha"}, notify=False)
            assert read["success"], read.get("error")
            assert "alpha" in read["text"]
            assert asked == [], "a read must not prompt even against a real server"
        finally:
            server.stop()

    def test_write_asks_then_runs_against_real_server(self):
        runner, asked, server, client = self._runner()
        try:
            runner.execute_tool("mcp", {"server": "xph_fixture"}, notify=False)
            out = runner.execute_tool("mcp__xph_fixture__create_document",
                                      {"title": "Plan q4", "body": "x"}, notify=False)
            assert out["success"], out.get("error")
            assert len(asked) == 1
            assert "created:Plan q4" in out["text"]
        finally:
            server.stop()

    def test_is_error_from_real_server_is_not_success(self):
        runner, asked, server, client = self._runner()
        try:
            runner.execute_tool("mcp", {"server": "xph_fixture"}, notify=False)
            out = runner.execute_tool("mcp__xph_fixture__fail_tool", {}, notify=False)
            # The exact wording is the server's business. What matters is
            # that a failing tool never comes back looking like it worked.
            assert out["success"] is False
            assert "fail_tool" in out["error"]
        finally:
            server.stop()
