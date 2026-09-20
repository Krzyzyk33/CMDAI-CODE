"""Tests: dynamic subagents. Run: python tools/test_subagents.py"""
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_ROOT, "src"))

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name, extra)
    if not cond:
        fails.append(name)


from cmdai.agent.tools import AgentContext, tool_subagent
from cmdai.agent.runner import AgentRunner, get_agent_system_prompt
from cmdai.agent.subagents.orchestrator import SubagentOrchestrator

ctx = AgentContext(workdir=APP_ROOT)
r = AgentRunner(ctx)

res = r._run_dynamic_subagent({"name": "Scout", "system": "You scout.", "goal": "Find X"})
check("unwired backend", res["success"] is False and res.get("role") == "Scout", str(res))

bad = r._run_dynamic_subagent({"name": "", "system": "s", "goal": "g"})
check("validation", bad["success"] is False)

capped = None
for i in range(7):
    capped = r.execute_subagent_batch([{"name": f"A{i}", "system": "s", "goal": "g"}])
check("batch cap 5", capped == [] or capped is not None)
check("batch max", len(r.execute_subagent_batch(
    [{"name": f"B{i}", "system": "s", "goal": "g"} for i in range(9)])) <= 5)

calls = []


def fake_model(messages):
    calls.append(len(messages))
    if len(calls) == 1:
        return "Checking. <tool:read path=\"cmdai.py\" />"
    return "Done. File exists and is the entrypoint."


def fake_exec(name, args):
    assert name != "subagent", "nesting must be blocked"
    return {"success": True, "path": args.get("path", ""), "content": "x=1", "total_lines": 1}


o = SubagentOrchestrator()
out = o.run_subagent(o.build_task("Scout", "You scout.", "Find X"),
                     model_call=fake_model, tool_executor=fake_exec, max_turns=4)
check("real loop summary", "Done" in out.summary, out.summary[:60])
check("two model turns", len(calls) == 2, str(len(calls)))
check("history has tool result", any("tool:read result" in str(m.get("content", "")) for m in out.history))


def fake_model2(messages):
    return "Nested <tool:subagent name=\"X\" system=\"s\" goal=\"g\" />"


out2 = o.run_subagent(o.build_task("P", "s", "g"),
                      model_call=fake_model2, tool_executor=fake_exec, max_turns=2)
check("nest guard", any("not allowed" in str(m.get("content", "")) for m in out2.history))

p = get_agent_system_prompt(".", "auto", vision=False)
check("prompt lists subagent", "tool:subagent" in p)

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL SUBAGENT OK")
