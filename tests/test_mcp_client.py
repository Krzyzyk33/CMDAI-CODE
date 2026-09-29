"""MCP client tests driven against the real SDK fixture server.

The fixture runs in its own virtualenv (``tools/mcp-fixture-venv``) on
the official SDK, so these assertions check this client's reading of the
spec against a real implementation. If that venv is missing the
integration tests skip rather than fail, and the pure-logic tests still
run.

Pure unit tests (annotation classification, naming, config parsing) are
kept in the same file and never skip.
"""

import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from cmdai.mcp.client import (  # noqa: E402
    MCPClient,
    MCPServer,
    MCPToolError,
    PERM_DESTRUCTIVE,
    PERM_READ,
    PERM_WRITE,
    PROTOCOL_VERSION,
    block_texts,
    classify_tool,
    image_blocks,
    qualified_tool_name,
    sanitize_name,
)

FIXTURE = os.path.join(ROOT, "tools", "mcp_fixture_server.py")
FIXTURE_VENV_PY = os.path.join(ROOT, "tools", "mcp-fixture-venv", "Scripts", "python.exe")


def _fixture_python() -> str:
    """The venv interpreter if the fixture venv exists, else system python."""
    if os.path.isfile(FIXTURE_VENV_PY):
        return FIXTURE_VENV_PY
    return sys.executable


needs_fixture = pytest.mark.skipif(
    not os.path.isfile(FIXTURE),
    reason="fixture server missing: tools/mcp_fixture_server.py",
)


def _server(**kw) -> MCPServer:
    params = dict(command=_fixture_python(), args=[FIXTURE], timeout=20.0)
    params.update(kw)
    return MCPServer("xph-fixture", **params)


@pytest.fixture
def connected():
    server = _server()
    if not server.connect():
        pytest.skip(f"fixture did not connect: {server.error}")
    yield server
    server.stop()


# ------------------------------------------------------------ pure logic


class TestClassification:
    def test_read_only(self):
        assert classify_tool({"annotations": {"readOnlyHint": True}}) == PERM_READ

    def test_write(self):
        assert classify_tool({"annotations": {"readOnlyHint": False}}) == PERM_WRITE

    def test_destructive_wins_over_read_only(self):
        tool = {"annotations": {"readOnlyHint": True, "destructiveHint": True}}
        assert classify_tool(tool) == PERM_DESTRUCTIVE

    def test_missing_annotations_is_dangerous(self):
        assert classify_tool({}) == PERM_DESTRUCTIVE

    def test_empty_annotations_is_dangerous(self):
        assert classify_tool({"annotations": {}}) == PERM_DESTRUCTIVE

    def test_wrong_type_annotations_is_dangerous(self):
        assert classify_tool({"annotations": "read-only trust me"}) == PERM_DESTRUCTIVE


class TestNaming:
    def test_server_and_tool_boundary_is_unambiguous(self):
        # The old <server>_<tool> scheme made these two collide.
        a = qualified_tool_name("github", "list_prs")
        b = qualified_tool_name("github_list", "prs")
        assert a != b

    def test_prefix_and_case(self):
        assert qualified_tool_name("GitHub", "ListPRs").startswith("mcp__")

    def test_hyphen_becomes_underscore(self):
        # Not cosmetic: the prompt tool parser is <tool:(\\w+)> and \\w does
        # not include "-", so a hyphen in the name would break the tag.
        name = qualified_tool_name("xph-docs", "read")
        assert "-" not in name
        assert name == "mcp__xph_docs__read"

    def test_sanitize_collapses_runs(self):
        assert sanitize_name("  My--Server!! ") == "my_server"

    def test_sanitize_never_empty(self):
        assert sanitize_name("!!!") == "server"


class TestBlockTexts:
    def test_flattens_text(self):
        result = {"content": [{"type": "text", "text": "hello"}]}
        assert block_texts(result["content"]) == ["hello"]

    def test_describes_image_without_dumping_base64(self):
        import base64
        payload = base64.b64encode(b"x" * 5000).decode()
        block = {"type": "image", "data": payload, "mimeType": "image/png"}
        text = "\n".join(block_texts([block]))
        assert "image/png" in text
        assert len(text) < 200, "base64 must not be inlined into the prompt"

    def test_nested_resource(self):
        block = {"type": "resource", "resource": {"uri": "file:///a", "text": "body"}}
        assert block_texts([block]) == ["body"]

    def test_none_content(self):
        assert block_texts(None) == []


class TestImageBlocks:
    def test_decodes(self):
        import base64
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
        block = {"type": "image", "data": base64.b64encode(png).decode(),
                 "mimeType": "image/png"}
        out = image_blocks({"content": [block]})
        assert len(out) == 1 and out[0]["data"] == png

    def test_skips_bad_base64(self):
        assert image_blocks({"content": [{"type": "image", "data": "!!!not base64!!!"}]}) == []

    def test_skips_non_image(self):
        assert image_blocks({"content": [{"type": "text", "text": "hi"}]}) == []


# ---------------------------------------------------------- config layer


class TestConfig:
    def test_reads_claude_desktop_wrapper(self, tmp_path):
        path = tmp_path / "mcp_config.json"
        path.write_text(json.dumps({"mcpServers": {
            "GitHub": {"command": "npx", "args": ["-y", "server"]},
        }}), encoding="utf-8")
        client = MCPClient(config_path=str(path))
        cfg = client.load_config()
        assert "github" in cfg
        assert cfg["github"]["args"] == ["-y", "server"]

    def test_reads_bare_map(self, tmp_path):
        path = tmp_path / "mcp_config.json"
        path.write_text(json.dumps({
            "docs": {"command": "xph-docs", "args": ["serve"]},
        }), encoding="utf-8")
        assert "docs" in MCPClient(config_path=str(path)).load_config()

    def test_missing_file_is_not_an_error(self, tmp_path):
        client = MCPClient(config_path=str(tmp_path / "nope.json"))
        assert client.load_config() == {}

    def test_corrupt_file_is_not_an_error(self, tmp_path):
        path = tmp_path / "mcp_config.json"
        path.write_text("{not json", encoding="utf-8")
        assert MCPClient(config_path=str(path)).load_config() == {}

    def test_env_is_carried_through(self, tmp_path):
        path = tmp_path / "mcp_config.json"
        path.write_text(json.dumps({"mcpServers": {
            "s": {"command": "c", "env": {"TOKEN": "${MY_TOKEN}"}},
        }}), encoding="utf-8")
        client = MCPClient(config_path=str(path))
        client.configure()
        assert client.servers["s"].env["TOKEN"] == "${MY_TOKEN}"

    def test_url_without_type_is_http_not_stdio(self, tmp_path):
        # Claude Code treats this as a config error; we treat it as http,
        # which is the intent, and no longer silently launch a stdio server.
        path = tmp_path / "mcp_config.json"
        path.write_text(json.dumps({"mcpServers": {
            "s": {"url": "http://127.0.0.1:9/mcp"},
        }}), encoding="utf-8")
        client = MCPClient(config_path=str(path))
        client.configure()
        assert client.servers["s"].transport == "http"

    def test_disabled_server_is_configured_but_not_started(self, tmp_path):
        path = tmp_path / "mcp_config.json"
        path.write_text(json.dumps({"mcpServers": {
            "s": {"command": "c", "enabled": False},
        }}), encoding="utf-8")
        client = MCPClient(config_path=str(path))
        client.configure()
        assert "s" in client.servers
        assert client.servers["s"].enabled is False

    def test_configured_server_is_listed_even_when_not_connected(self, tmp_path):
        path = tmp_path / "mcp_config.json"
        path.write_text(json.dumps({"mcpServers": {"s": {"command": "c"}}}), encoding="utf-8")
        client = MCPClient(config_path=str(path))
        client.configure()
        assert len(client.status_lines()) == 1


# --------------------------------------------------------- integration


@needs_fixture
class TestHandshake:
    def test_connects_and_agrees_on_version(self, connected):
        assert connected.status == "connected"
        assert connected.protocol_version == PROTOCOL_VERSION

    def test_records_server_info(self, connected):
        assert connected.server_info.get("name") == "xph-fixture"

    def test_records_capabilities(self, connected):
        assert "tools" in connected.capabilities

    def test_lists_the_expected_tools(self, connected):
        names = {t["name"] for t in connected.tools}
        assert {"read_document", "create_document", "delete_document",
                "unannotated_thing", "fail_tool"} <= names

    def test_annotations_arrive_as_camel_case(self, connected):
        tool = next(t for t in connected.tools if t["name"] == "read_document")
        assert tool["annotations"]["readOnlyHint"] is True

    def test_status_line_mentions_permission_split(self, connected):
        line = connected.status_lines()[0]
        assert "connected" in line and "read" in line


@needs_fixture
class TestPagination:
    def test_follows_next_cursor(self):
        env = dict(os.environ, MCP_FIXTURE_EXTRA_TOOLS="120")
        server = _server(env=env)
        # 9 built in + 120 synthetic spans more than one page.
        if not server.connect():
            pytest.skip(f"fixture did not connect: {server.error}")
        try:
            assert len(server.tools) >= 129, f"got {len(server.tools)}, pagination lost tools"
        finally:
            server.stop()


@needs_fixture
class TestCalls:
    def test_read_only_call(self, connected):
        result = connected.call_tool("read_document", {"doc_id": "alpha"})
        assert "alpha" in "\n".join(block_texts(result.get("content")))

    def test_structured_list(self, connected):
        result = connected.call_tool("list_documents", {})
        assert block_texts(result.get("content"))

    def test_unannotated_tool_still_callable(self, connected):
        # The gate is a permission question, not a hard block.
        result = connected.call_tool("unannotated_thing", {"x": "1"})
        assert "unannotated" in "\n".join(block_texts(result.get("content")))

    def test_image_result(self, connected):
        result = connected.call_tool("echo_image", {})
        assert image_blocks(result), "image content block was dropped"

    def test_unknown_tool_is_never_silent_success(self, connected):
        # The spec files "unknown tool" under protocol errors, but real
        # servers disagree: this fixture answers isError with a text block.
        # Both must surface as a raised error rather than a result the
        # model could mistake for work completed.
        from cmdai.mcp.client import MCPError
        with pytest.raises((MCPError, MCPToolError)) as caught:
            connected.call_tool("no_such_tool", {})
        assert "no_such_tool" in str(caught.value).lower() or "unknown" in str(caught.value).lower()

    def test_is_error_is_not_reported_as_success(self, connected):
        # The old client returned {"success": True} here, which is how a
        # model ends up claiming a failed action worked.
        with pytest.raises(MCPToolError):
            connected.call_tool("fail_tool", {})


@needs_fixture
class TestTimeoutAndCleanup:
    def test_hung_server_does_not_block(self, connected):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            connected.call_tool("hang", {"seconds": 30}, timeout=1.5)
        elapsed = time.monotonic() - started
        assert elapsed < 10, f"timeout did not fire promptly ({elapsed:.1f}s)"

    def test_client_is_usable_after_a_timeout(self, connected):
        with pytest.raises(TimeoutError):
            connected.call_tool("hang", {"seconds": 30}, timeout=1.0)
        # A stuck request must not poison the rest of the session.
        result = connected.call_tool("read_document", {"doc_id": "beta"})
        assert "beta" in "\n".join(block_texts(result.get("content")))

    def test_stop_leaves_no_process_behind(self):
        server = _server()
        if not server.connect():
            pytest.skip("fixture did not connect")
        pid = server.proc.pid
        server.stop()
        deadline = time.monotonic() + 8
        alive = True
        while time.monotonic() < deadline:
            alive = _pid_alive(pid)
            if not alive:
                break
            time.sleep(0.2)
        assert not alive, f"server process {pid} survived stop()"

    def test_stop_is_safe_to_call_twice(self):
        server = _server()
        if not server.connect():
            pytest.skip("fixture did not connect")
        server.stop()
        server.stop()
        assert server.status == "disconnected"

    def test_connect_failure_reports_instead_of_hanging(self):
        server = MCPServer("bad", command="cmdai-definitely-not-a-real-binary",
                           args=[], timeout=5.0)
        started = time.monotonic()
        assert server.connect() is False
        assert time.monotonic() - started < 20
        assert server.status == "error" and server.error


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=15,
        )
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ------------------------------------------------------------- registry


@needs_fixture
class TestRegistry:
    def test_qualified_names_are_exposed(self, connected):
        client = MCPClient()
        client.servers["xph_fixture"] = connected
        tools = client.dynamic_tools()
        assert "mcp__xph_fixture__read_document" in tools
        assert tools["mcp__xph_fixture__read_document"] == ("xph_fixture", "read_document")

    def test_permission_lookup_by_qualified_name(self, connected):
        client = MCPClient()
        client.servers["xph_fixture"] = connected
        assert client.permission_for("mcp__xph_fixture__read_document") == PERM_READ
        assert client.permission_for("mcp__xph_fixture__create_document") == PERM_WRITE
        assert client.permission_for("mcp__xph_fixture__delete_document") == PERM_DESTRUCTIVE
        assert client.permission_for("mcp__xph_fixture__unannotated_thing") == PERM_DESTRUCTIVE

    def test_unknown_server_is_dangerous_by_default(self, connected):
        client = MCPClient()
        client.servers["xph_fixture"] = connected
        assert client.permission_for("mcp__ghost__anything") == PERM_DESTRUCTIVE

    def test_non_mcp_name_is_never_asked_about(self, connected):
        client = MCPClient()
        client.servers["xph_fixture"] = connected
        # Native tools like screenshot/vision are outside the MCP gate.
        assert client.permission_for("screenshot") == PERM_DESTRUCTIVE

    def test_tool_definitions_carry_schemas_and_permissions(self, connected):
        client = MCPClient()
        client.servers["xph_fixture"] = connected
        defs = client.tool_definitions()
        by_name = {d["name"]: d for d in defs}
        read = by_name["mcp__xph_fixture__read_document"]
        assert read["permission"] == PERM_READ
        assert read["inputSchema"]["type"] == "object"
        assert read["description"]

    def test_definitions_can_be_restricted_to_open_servers(self, connected):
        client = MCPClient()
        client.servers["xph_fixture"] = connected
        assert client.tool_definitions(only_servers=["someone_else"]) == []

    def test_call_tool_reports_not_connected(self):
        client = MCPClient()
        out = client.call_tool("ghost", "x", {})
        assert out["success"] is False and "not connected" in out["error"]
