"""Tests: capability vision gating, screenshot tool, mcps fake server. Run: python tools/test_mcp_vision.py"""
import json
import os
import subprocess
import sys
import tempfile
import time

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_ROOT, "src"))

FAKE_SERVER = r'''
import json, sys
TOOLS = [{"name": "echo_text", "description": "echo", "inputSchema": {"type": "object"}}]
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    rid, method, params = msg.get("id"), msg.get("method"), msg.get("params", {})
    if method == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"ok": True}}) + "\n"); sys.stdout.flush()
    elif method == "tools/list":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}) + "\n"); sys.stdout.flush()
    elif method == "tools/call":
        sys.stderr.write("called %s\n" % params.get("name")); sys.stderr.flush()
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "echo:" + json.dumps(params.get("arguments", {}))}]}}) + "\n"); sys.stdout.flush()
'''

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name, extra)
    if not cond:
        fails.append(name)


from cmdai.core import capabilities as C
C._MEM_CACHE.clear()
exp = {
    "Qwable-9B-Claude-Fable-5-Q4_K_M.gguf": (True, False),
    "Qwen3-0.6B-UD-Q4_K_XL.gguf": (True, False),
    "gemma-4-E4B-it-Q4_0.gguf": (False, False),
    "qwen2.5-0.5b-instruct-q4_k_m.gguf": (False, False),
}
for f, (ethink, evision) in exp.items():
    c = C.get_model_capability(f, provider_id="local_gguf", model_path=os.path.join("models", f))
    check(f"cap {f}", c.thinking == ethink and c.vision == evision and c.confidence == "high",
          f"think={c.thinking} vision={c.vision} conf={c.confidence}")

from cmdai.agent.runner import get_agent_system_prompt
p_off = get_agent_system_prompt(".", "auto", vision=False)
p_on = get_agent_system_prompt(".", "auto", vision=True)
check("prompt hides vision", "tool:screenshot" not in p_off and "tool:vision" not in p_off)
check("prompt shows vision", "tool:screenshot" in p_on and "tool:vision" in p_on)
check("prompt always mcps", "tool:mcps" in p_off)

from cmdai.agent.tools import AgentContext, tool_screenshot, tool_vision
ctx = AgentContext(workdir=APP_ROOT)
res = tool_screenshot(ctx, "active_window")
check("screenshot keys", set(("success",)).issubset(res.keys()), str({k: (v if k != "path" else "...") for k, v in res.items()}))
if res.get("success"):
    check("screenshot size limit", res["size_bytes"] <= 2_000_000, str(res["size_bytes"]))
    v = tool_vision(ctx, res["path"], "what is this?")
    check("vision queues", v.get("success") and len(ctx.pending_images) == 1)
    v2 = tool_vision(ctx, os.path.join("models", "nope.png"))
    check("vision missing file", not v2.get("success"))
else:
    print("SKIP vision (screenshot unavailable headless):", res.get("error"))

from cmdai.mcp.client import MCPClient, sanitize_name
check("sanitize", sanitize_name("vision-tools") == "vision_tools")
tmp = tempfile.mkdtemp()
srv_py = os.path.join(tmp, "fake_mcp.py")
open(srv_py, "w").write(FAKE_SERVER)
client = MCPClient()
srv_name = "fake-srv"
from cmdai.mcp.client import MCPServer
srv = MCPServer("fake_srv", sys.executable, [srv_py])
client.servers["fake_srv"] = srv
check("mcp connect", srv.connect())
check("mcp tools listed", any(t.get("name") == "echo_text" for t in srv.tools))
check("mcp dynamic names", client.dynamic_tools() == {"fake_srv_echo_text": ("fake_srv", "echo_text")})
call = client.call_tool("fake_srv", "echo_text", {"a": 1})
check("mcp call", call.get("success") and "echo" in json.dumps(call))
check("mcp logs", any("echo_text" in l for l in srv.stderr_log))
check("mcp restart", client.restart("fake-srv"))
check("mcp status lines", any("fake_srv" in l and "connected" in l for l in client.status_lines()))

from cmdai.agent.runner import AgentRunner
runner = AgentRunner(ctx, mcp_client=client)
m = runner._mcps_status() if hasattr(runner, "_mcps_status") else {}
check("runner mcps", m.get("success") and runner._mcp_unlocked)
runner._mcp_unlocked = False
check("runner dynamic locked", runner._dynamic_mcp_tool("fake_srv_echo_text") is None)
runner._mcp_unlocked = True
check("runner dynamic unlocked", runner._dynamic_mcp_tool("fake_srv_echo_text") == ("fake_srv", "echo_text"))
srv.stop()

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL OK")
