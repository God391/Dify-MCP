from __future__ import annotations

import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_server_lists_guarded_tools() -> None:
    async def run() -> None:
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "dify_mcp"])
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
        names = {tool.name for tool in result.tools}
        assert names == {
            "dify_connection_check",
            "workflow_validate",
            "workflow_get_draft",
            "workflow_patch_draft_preview",
            "workflow_browser_bridge_patch_preview",
            "workflow_browser_bridge_patch_apply_prepare",
            "workflow_browser_bridge_patch_verify",
            "workflow_generate_dsl",
            "workflow_create_draft_apply",
            "workflow_update_draft_apply",
            "workflow_patch_draft_apply",
        }

    anyio.run(run)
