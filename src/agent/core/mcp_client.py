"""
MCP Client Helper

Thin wrapper for calling tools on MCP servers.
Uses per-call HTTP connections (simple and reliable for POC throughput).

Each call_tool():
  1. Opens a streamable-HTTP connection to the target server
  2. Creates a ClientSession and initialises it
  3. Calls the tool with the provided arguments
  4. Parses and returns the result dict
  5. Closes the connection

For Phase 1+ use a persistent connection pool instead.
"""

import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class MCPCallError(Exception):
    """Raised when an MCP tool call returns isError=True or fails to connect."""
    pass


async def call_tool(url: str, tool_name: str, arguments: dict[str, Any]) -> Any:
    """
    Call a tool on an MCP server and return the parsed result.

    Args:
        url:       Full base URL of the MCP server, e.g. "http://localhost:8001"
        tool_name: Name of the tool to call
        arguments: Dict of tool arguments (matches the tool's parameter schema)

    Returns:
        The parsed result (dict, list, str, etc.) from the tool's return value.

    Raises:
        MCPCallError: If the server returns an error or is unreachable.
    """
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

        if result.isError:
            # Extract error text from content
            error_text = _extract_text(result.content)
            raise MCPCallError(
                f"MCP tool '{tool_name}' on '{url}' returned an error: {error_text}"
            )

        parsed = _parse_result(result.content)
        logger.info(
            "mcp_tool_called",
            url=url,
            tool=tool_name,
        )
        return parsed

    except MCPCallError:
        raise
    except Exception as e:
        logger.error("mcp_tool_call_failed", url=url, tool=tool_name, error=str(e))
        raise MCPCallError(
            f"Failed to call '{tool_name}' on '{url}': {e}"
        ) from e


def _extract_text(content: list | None) -> str:
    """Extract raw text from MCP content items."""
    if not content:
        return "(no content)"
    parts = []
    for item in content:
        if hasattr(item, "text"):
            parts.append(item.text)
    return " | ".join(parts) if parts else str(content)


def _parse_result(content: list | None) -> Any:
    """
    Parse MCP tool result content.

    FastMCP serialises tool return values as JSON text in a TextContent item.
    Try JSON parse first; fall back to raw text if not valid JSON.
    """
    if not content:
        return None

    text = _extract_text(content)
    if not text or text == "(no content)":
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# ── Convenience callers (typed by server) ────────────────────────────────────
# These helpers make call sites read clearly:
#   result = await oracle_los.originate_contract(data)
# instead of:
#   result = await call_tool(settings.mcp_oracle_los_url, "originate_contract", data)


class _ServerProxy:
    """Thin proxy that routes tool calls to a specific MCP server URL."""

    def __init__(self, url_attr: str) -> None:
        self._url_attr = url_attr

    def _url(self) -> str:
        return getattr(settings, self._url_attr)

    async def __call__(self, tool_name: str, **kwargs: Any) -> Any:
        return await call_tool(self._url(), tool_name, kwargs)


class OracleLOSClient:
    """MCP client for the Oracle LOS simulator (port 8010)."""
    _url = lambda self: settings.mcp_oracle_los_url

    async def originate_contract(self, contract_data: dict) -> dict:
        return await call_tool(self._url(), "originate_contract",
                               {"contract_data": contract_data})

    async def get_contract(self, contract_id: str) -> dict:
        return await call_tool(self._url(), "get_contract",
                               {"contract_id": contract_id})

    async def get_contracts(self, filters: dict | None = None) -> list:
        return await call_tool(self._url(), "get_contracts",
                               {"filters": filters})


class LLASClient:
    """MCP client for the LLAS simulator (port 8012)."""
    _url = lambda self: settings.mcp_llas_url

    async def get_account(self, contract_id: str) -> dict:
        return await call_tool(self._url(), "get_account",
                               {"contract_id": contract_id})

    async def create_account(self, contract_id: str, account_data: dict) -> dict:
        return await call_tool(self._url(), "create_account",
                               {"contract_id": contract_id, "account_data": account_data})

    async def get_payment_history(self, contract_id: str, limit: int = 12) -> dict:
        return await call_tool(self._url(), "get_payment_history",
                               {"contract_id": contract_id, "limit": limit})


class ValidationClient:
    """MCP client for the Validation Engine (port 8001)."""
    _url = lambda self: settings.mcp_validation_url

    async def validate_event(self, request: dict) -> dict:
        return await call_tool(self._url(), "validate_event",
                               {"request": request})

    async def get_quarantined(self, contract_id: str | None = None) -> list:
        return await call_tool(self._url(), "get_quarantined",
                               {"contract_id": contract_id})

    async def approve_override(self, event_id: str, reason: str, reviewer: str) -> dict:
        return await call_tool(self._url(), "approve_override",
                               {"event_id": event_id, "reason": reason,
                                "reviewer": reviewer})


class LedgerClient:
    """MCP client for the Ledger MCP (port 8002)."""
    _url = lambda self: settings.mcp_ledger_url

    async def write_record(self, record: dict, proof_token: str) -> dict:
        return await call_tool(self._url(), "write_record",
                               {"record": record, "proof_token": proof_token})

    async def execute_state_transition(
        self,
        contract_id: str,
        new_state: str,
        trigger_event_id: str,
        saga_id: str | None = None,
    ) -> dict:
        return await call_tool(self._url(), "execute_state_transition", {
            "contract_id": contract_id,
            "new_state": new_state,
            "trigger_event_id": trigger_event_id,
            "saga_id": saga_id,
        })

    async def get_contract_lifecycle(self, contract_id: str) -> dict:
        return await call_tool(self._url(), "get_contract_lifecycle",
                               {"contract_id": contract_id})

    async def get_state(self, contract_id: str) -> dict:
        return await call_tool(self._url(), "get_state",
                               {"contract_id": contract_id})

    async def get_audit_trail(self, contract_id: str) -> list:
        return await call_tool(self._url(), "get_audit_trail",
                               {"contract_id": contract_id})


# ── Module-level singleton clients ────────────────────────────────────────────

oracle_los = OracleLOSClient()
llas = LLASClient()
validation = ValidationClient()
ledger = LedgerClient()
