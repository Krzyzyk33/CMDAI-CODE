"""Reference MCP server used to test the hand-written client.

Runs on the official SDK inside ``tools/mcp-fixture-venv`` (a separate
virtualenv, so the main environment keeps pydantic 2.6.1 untouched).
The point of using the SDK here is independence: when our client's
behaviour is asserted against this server, a bug in our reading of the
spec shows up as a disagreement with a real implementation rather than
as two copies of the same mistake agreeing with each other.

The tool set is chosen to exercise the permission gate, not to be
useful:

  read_document      readOnlyHint=True            - auto allowed
  list_documents     readOnlyHint=True            - auto allowed
  create_document    readOnlyHint=False           - asks once per server
  edit_document      readOnlyHint=False           - asks once per server
  delete_document    destructiveHint=True         - asks every time
  unannotated_thing  no annotations at all        - must be treated as
                                                  non-read-only and
                                                  potentially destructive
  fail_tool          raises                       - must surface as isError
  echo_image         returns image content        - phase 4
  hang               never returns                - timeout test

Set MCP_FIXTURE_EXTRA_TOOLS=N to append N synthetic tools, which is how
tools/list pagination gets exercised.
"""

import os
import sys

from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, ToolAnnotations

server = MCPServer(
    name="xph-fixture",
    version="0.0.1",
    instructions="Test fixture. Not a real product.",
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


@server.tool(
    name="read_document",
    description="Read a document by id. Read-only.",
    annotations=READ_ONLY,
)
def read_document(doc_id: str) -> str:
    return f"# {doc_id}\n\nfixture content for {doc_id}"


@server.tool(
    name="list_documents",
    description="List all documents. Read-only.",
    annotations=READ_ONLY,
)
def list_documents() -> list[str]:
    return ["alpha", "beta"]


@server.tool(
    name="create_document",
    description="Create a new document. Writes.",
    annotations=WRITE,
)
def create_document(title: str, body: str = "") -> str:
    return f"created:{title}:{len(body)}"


@server.tool(
    name="edit_document",
    description="Edit an existing document. Writes.",
    annotations=WRITE,
)
def edit_document(doc_id: str, new_text: str) -> str:
    return f"edited:{doc_id}:{len(new_text)}"


@server.tool(
    name="delete_document",
    description="Delete a document permanently. Cannot be undone.",
    annotations=DESTRUCTIVE,
)
def delete_document(doc_id: str) -> str:
    return f"deleted:{doc_id}"


@server.tool(
    name="unannotated_thing",
    description="Delivers no annotations whatsoever, so the client must "
                "assume non-read-only and potentially destructive.",
)
def unannotated_thing(x: str = "") -> str:
    return f"unannotated:{x}"


@server.tool(
    name="fail_tool",
    description="Always fails, to check isError handling.",
    annotations=READ_ONLY,
)
def fail_tool() -> str:
    raise ValueError("fixture failure on purpose")


@server.tool(
    name="echo_image",
    description="Returns a tiny PNG so image content handling can be tested.",
    annotations=READ_ONLY,
)
def echo_image() -> list:
    # 1x1 transparent PNG.
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000a49444154789c6300010000050001"
        "0d0a2db40000000049454e44ae426082"
    )
    return [ImageContent(type="image", data=__import__("base64").b64encode(png).decode(), mimeType="image/png")]


@server.tool(
    name="hang",
    description="Never returns, so per-request timeout can be tested.",
    annotations=READ_ONLY,
)
def hang(seconds: int = 120) -> str:
    import time

    time.sleep(seconds)
    return "eventually"


def _add_synthetic(count: int) -> None:
    for i in range(count):

        @server.tool(
            name=f"synthetic_{i:03d}",
            description=f"Synthetic tool number {i}, for pagination tests.",
            annotations=READ_ONLY,
        )
        def _synthetic(index: int = i) -> str:
            return f"synthetic:{index}"


if __name__ == "__main__":
    extra = int(os.environ.get("MCP_FIXTURE_EXTRA_TOOLS", "0") or 0)
    if extra > 0:
        _add_synthetic(extra)
    server.run(transport="stdio")
