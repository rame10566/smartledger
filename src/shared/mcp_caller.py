"""
Shared MCP tool caller for simulated servers.

Simulated MCP servers (CRM, Portal, Mobile, LOS) use this when they need to
call the Integration System MCP during tool execution. Mirrors the pattern
from agent/core/mcp_client.py but lives in shared/ so any server can import it.
"""

import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class MCPCallError(Exception):
    pass


async def call_mcp_tool(url: str, tool_name: str, arguments: dict[str, Any]) -> Any:
    """
    Call a tool on an MCP server and return the parsed result.
    Opens a fresh connection per call (suitable for low-frequency simulator calls).
    """
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

        if result.isError:
            text = _extract_text(result.content)
            raise MCPCallError(f"MCP tool '{tool_name}' on '{url}' error: {text}")

        return _parse_result(result.content)

    except MCPCallError:
        raise
    except Exception as e:
        raise MCPCallError(f"Failed to call '{tool_name}' on '{url}': {e}") from e


def _extract_text(content: list | None) -> str:
    if not content:
        return "(no content)"
    parts = [item.text for item in content if hasattr(item, "text")]
    return " | ".join(parts) if parts else str(content)


def _parse_result(content: list | None) -> Any:
    if not content:
        return None
    text_items = [item.text for item in content if hasattr(item, "text")]
    if not text_items:
        return None
    if len(text_items) == 1:
        try:
            return json.loads(text_items[0])
        except json.JSONDecodeError:
            return text_items[0]
    results = []
    for text in text_items:
        try:
            results.append(json.loads(text))
        except json.JSONDecodeError:
            results.append(text)
    return results
