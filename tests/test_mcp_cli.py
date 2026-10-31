"""`cmdai mcp` command-line tests.

The CLI is the only way to add a server, on purpose: a model that can
register its own tool endpoints is a model that can be talked into loading
something nobody reviewed. These tests pin that the commands work and
that they never quietly mangle the server's own arguments.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from cmdai import cli  # noqa: E402
from cmdai.mcp.client import MCPClient  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the CLI at a throwaway config file and capture its output.

    handle_mcp resolves the config as config_file(APP_ROOT), so redirecting
    APP_ROOT is enough to isolate it - no patching of MCPClient internals,
    which would let the tests pass while the real path logic rotted.
    """
    monkeypatch.setattr(cli, "APP_ROOT", str(tmp_path))
    config = tmp_path / "mcp_config.json"

    import contextlib
    import io

    def run(*argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.handle_mcp(list(argv))
        return code, buffer.getvalue()

    run.config = config
    return run


def read(config):
    return json.loads(config.read_text(encoding="utf-8"))


class TestList:
    def test_no_config_yet(self, sandbox):
        code, out = sandbox("list")
        assert code == 0
        assert "No MCP config yet" in out
        assert "cmdai mcp add" in out

    def test_empty_config(self, sandbox):
        sandbox.config.write_text('{"mcpServers": {}}', encoding="utf-8")
        code, out = sandbox("list")
        assert code == 0 and "empty" in out.lower()

    def test_shows_transport_and_target(self, sandbox):
        sandbox.config.write_text(json.dumps({"mcpServers": {
            "a": {"command": "x", "args": ["--flag"]},
            "b": {"type": "http", "url": "http://127.0.0.1:1/mcp"},
        }}), encoding="utf-8")
        code, out = sandbox("list")
        assert code == 0
        assert "stdio: x --flag" in out
        assert "http://127.0.0.1:1/mcp" in out

    def test_shows_disabled_and_no_autostart(self, sandbox):
        sandbox.config.write_text(json.dumps({"mcpServers": {
            "a": {"command": "x", "enabled": False, "autostart": False},
        }}), encoding="utf-8")
        code, out = sandbox("list")
        assert "[disabled]" in out and "[no autostart]" in out


class TestAdd:
    def test_stdio_server(self, sandbox):
        code, out = sandbox("add", "docs", "python", "-m", "server")
        assert code == 0
        entry = read(sandbox.config)["mcpServers"]["docs"]
        assert entry == {"command": "python", "args": ["-m", "server"]}

    def test_server_own_flags_are_preserved(self, sandbox):
        # The bug this pins: stripping every token starting with "-" ate the
        # server's own options, so this command silently lost "-c".
        code, _ = sandbox("add", "s", "python", "-c", "print(1)")
        assert code == 0
        entry = read(sandbox.config)["mcpServers"]["s"]
        assert entry["args"] == ["-c", "print(1)"]

    def test_flag_values_that_look_like_flags_survive(self, sandbox):
        code, _ = sandbox("add", "s", "server", "--query", "--weird-value")
        assert code == 0
        assert read(sandbox.config)["mcpServers"]["s"]["args"] == ["--query", "--weird-value"]

    def test_http_server(self, sandbox):
        code, out = sandbox("add", "docs", "--url", "http://127.0.0.1:8765/mcp")
        assert code == 0
        entry = read(sandbox.config)["mcpServers"]["docs"]
        assert entry == {"type": "http", "url": "http://127.0.0.1:8765/mcp"}

    def test_name_is_sanitised_and_said_so(self, sandbox):
        code, out = sandbox("add", "my-server", "cmd")
        assert code == 0
        assert "my_server" in read(sandbox.config)["mcpServers"]
        assert "my_server" in out, "the user must be told the stored name differs"

    def test_env_pairs(self, sandbox):
        code, _ = sandbox("add", "gh", "docker", "run", "--env", "TOKEN=${GH_TOKEN}")
        assert code == 0
        entry = read(sandbox.config)["mcpServers"]["gh"]
        assert entry["env"] == {"TOKEN": "${GH_TOKEN}"}

    def test_multiple_env_pairs(self, sandbox):
        code, _ = sandbox("add", "gh", "cmd", "--env", "A=${A}", "--env", "B=${B}")
        entry = read(sandbox.config)["mcpServers"]["gh"]
        assert entry["env"] == {"A": "${A}", "B": "${B}"}

    def test_no_autostart_flag(self, sandbox):
        code, _ = sandbox("add", "slow", "cmd", "--no-autostart")
        assert read(sandbox.config)["mcpServers"]["slow"]["autostart"] is False

    def test_disabled_flag(self, sandbox):
        code, _ = sandbox("add", "off", "cmd", "--disabled")
        assert read(sandbox.config)["mcpServers"]["off"]["enabled"] is False

    def test_duplicate_is_refused(self, sandbox):
        sandbox("add", "docs", "cmd")
        code, out = sandbox("add", "docs", "other")
        assert code == 2 and "already exists" in out

    def test_missing_command(self, sandbox):
        code, out = sandbox("add", "docs")
        assert code == 2 and "Usage" in out

    def test_url_without_value(self, sandbox):
        code, out = sandbox("add", "docs", "--url")
        assert code == 2 and "--url needs a value" in out

    def test_malformed_env(self, sandbox):
        code, out = sandbox("add", "s", "cmd", "--env", "NOEQUALS")
        assert code == 2 and "KEY=value" in out

    def test_url_and_command_together_refused(self, sandbox):
        code, out = sandbox("add", "s", "cmd", "--url", "http://x/mcp")
        assert code == 2 and "not both" in out

    def test_unusable_name_refused(self, sandbox):
        code, out = sandbox("add", "!!!", "cmd")
        assert code == 2 and "not a usable" in out


class TestRemoveEnableDisable:
    def _with_one(self, sandbox, **extra):
        entry = {"command": "cmd"}
        entry.update(extra)
        sandbox.config.write_text(json.dumps({"mcpServers": {"docs": entry}}), encoding="utf-8")

    def test_remove(self, sandbox):
        self._with_one(sandbox)
        code, out = sandbox("remove", "docs")
        assert code == 0 and "docs" not in read(sandbox.config)["mcpServers"]

    def test_remove_missing(self, sandbox):
        sandbox.config.write_text('{"mcpServers": {}}', encoding="utf-8")
        code, out = sandbox("remove", "ghost")
        assert code == 2 and "No server named" in out

    def test_remove_without_name(self, sandbox):
        code, out = sandbox("remove")
        assert code == 2 and "Usage" in out

    def test_disable_then_enable(self, sandbox):
        self._with_one(sandbox)
        assert sandbox("disable", "docs")[0] == 0
        assert read(sandbox.config)["mcpServers"]["docs"]["enabled"] is False
        assert sandbox("enable", "docs")[0] == 0
        assert read(sandbox.config)["mcpServers"]["docs"]["enabled"] is True

    def test_disable_preserves_other_fields(self, sandbox):
        self._with_one(sandbox, args=["--x"], env={"T": "${T}"})
        sandbox("disable", "docs")
        entry = read(sandbox.config)["mcpServers"]["docs"]
        assert entry["args"] == ["--x"] and entry["env"] == {"T": "${T}"}

    def test_hyphenated_name_resolves_to_its_sanitised_key(self, sandbox):
        self._with_one(sandbox)
        # Config stores "my_server"; the user types "my-server".
        sandbox.config.write_text(json.dumps({"mcpServers": {"my_server": {"command": "c"}}}),
                                  encoding="utf-8")
        code, out = sandbox("disable", "my-server")
        assert code == 0, out
        assert read(sandbox.config)["mcpServers"]["my_server"]["enabled"] is False


class TestUnknownSubcommand:
    def test_lists_valid_options(self, sandbox):
        code, out = sandbox("frobnicate")
        assert code == 2
        assert "list" in out and "add" in out and "remove" in out


class TestExampleFileIsUsable:
    def test_example_parses_and_has_no_comment_keys_as_servers(self):
        path = os.path.join(ROOT, "mcp_config.example.json")
        assert os.path.isfile(path), "CHANGELOG promises this file and it must exist"
        client = MCPClient(config_path=path)
        servers = client.load_config()
        assert servers, "the example should show at least one real server"
        for name in servers:
            assert not name.startswith("_comment"), f"{name} is an annotation, not a server"

    def test_example_never_contains_a_literal_secret(self):
        """Structural check on the parsed file, not a text scan.

        A text scan for TOKEN/KEY trips over docker's "-e GITHUB_TOKEN"
        pass-through, which is the opposite of a leak. What matters is that
        no env *value* is a literal.
        """
        path = os.path.join(ROOT, "mcp_config.example.json")
        data = json.loads(open(path, "r", encoding="utf-8").read())
        offenders = []
        for name, entry in (data.get("mcpServers") or {}).items():
            if not isinstance(entry, dict):
                continue
            for key, value in (entry.get("env") or {}).items():
                value = str(value)
                if value and "${" not in value:
                    offenders.append(f"{name}.env.{key}={value}")
        assert not offenders, f"literal credentials in the example: {offenders}"

    def test_example_documents_both_transports(self):
        path = os.path.join(ROOT, "mcp_config.example.json")
        data = json.loads(open(path, "r", encoding="utf-8").read())
        servers = data["mcpServers"]
        assert any("command" in s for s in servers.values() if isinstance(s, dict))
        assert any("url" in s for s in servers.values() if isinstance(s, dict))

    def test_config_file_is_gitignored(self):
        # A tracked mcp_config.json would publish every token in it.
        import subprocess
        out = subprocess.run(["git", "check-ignore", "mcp_config.json"],
                             cwd=ROOT, capture_output=True, text=True)
        assert out.returncode == 0, "mcp_config.json must be ignored by git"
