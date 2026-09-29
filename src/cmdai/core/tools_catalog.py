"""Single source of truth for the XML agent tools reference.

Used by the /tools window and kept next to the system prompt so the visible
reference and the model-facing description stay in sync.
"""

from __future__ import annotations

from typing import List, NamedTuple


class ToolDoc(NamedTuple):
    name: str
    summary: str
    syntax: str
    example: str


TOOL_DOCS: List[ToolDoc] = [
    ToolDoc(
        "read",
        "Read a full file or a line range",
        "<read><path>FILE</path><lines>START-END</lines></read>",
        "<read><path>src/main.py</path><lines>1-50</lines></read>",
    ),
    ToolDoc(
        "edit",
        "Replace an exact snippet inside a file",
        "<edit><path>FILE</path><old>OLD</old><new>NEW</new></edit>",
        "<edit><path>app.py</path><old>def f():</old><new>def f(): pass</new></edit>",
    ),
    ToolDoc(
        "write",
        "Create a new file or overwrite it with content",
        "<write><path>FILE</path><content>BODY</content></write>",
        "<write><path>src/new.py</path><content>print(1)</content></write>",
    ),
    ToolDoc(
        "ls",
        "List files and directories in the workspace",
        "<ls><path>DIR</path></ls>",
        "<ls><path>.</path></ls>",
    ),
    ToolDoc(
        "glob",
        "Find files matching a pattern",
        "<glob><pattern>PATTERN</pattern></glob>",
        "<glob><pattern>**/*.py</pattern></glob>",
    ),
    ToolDoc(
        "search",
        "Grep a regex across files",
        "<search><pattern>REGEX</pattern><path>DIR</path></search>",
        "<search><pattern>def test</pattern><path>tests</path></search>",
    ),
    ToolDoc(
        "command",
        "Run a shell command in the workspace",
        "<command>SHELL</command>",
        "<command>python -m pytest</command>",
    ),
    ToolDoc(
        "fetch",
        "Fetch a web page over HTTP GET",
        "<fetch><url>URL</url></fetch>",
        "<fetch><url>https://api.github.com</url></fetch>",
    ),
    ToolDoc(
        "todo",
        "Manage the task checklist (add / done / clear)",
        "<todo><action>ACTION</action><task>TASK</task></todo>",
        "<todo><action>add</action><task>Implement feature</task></todo>",
    ),
    ToolDoc(
        "subagent",
        "Delegate a task to an autonomous subagent. Use name for the role shown in "
        "the chat, system for the prompt you generate, and the element body for the "
        "task. Emit several in one turn to run a batch: one after another on a local "
        "model, all at once on an API model.",
        "<tool:subagent name=\"Role\" system=\"prompt for the subagent\">the task</tool:subagent>",
        "<tool:subagent name=\"Code Architect\" system=\"You are the autonomous Code "
        "Architect subagent.\">Merge the duplicate dialog CSS rules.</tool:subagent>",
    ),
    ToolDoc(
        "agent_note",
        "Leave an optional note for the lead agent. Subagents only: it is handed to "
        "the lead agent verbatim once the batch finishes.",
        "<tool:agent_note text=\"NOTE\" />",
        "<tool:agent_note text=\"plan.py line 74 has an unexpected indent\" />",
    ),
]

TOOL_DOCS_BY_NAME = {t.name: t for t in TOOL_DOCS}
