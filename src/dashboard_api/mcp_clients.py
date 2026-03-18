"""
Dashboard API — MCP client helpers

Thin wrappers around the shared MCP call_tool utility.
Kept here (rather than imported from agent.core.mcp_client) so the
dashboard-api Docker image does not need the full agent package.

Clients defined here:
  ledger     → Ledger MCP (port 8002)
  validation → Validation Engine (port 8001)
  reporting  → Reporting MCP (port 8004)
  llas       → LLAS simulator (port 8012)
"""

import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from shared.config import get_settings
from shared.logging import get_logger

settings = get_settings()
logger   = get_logger(__name__)


class MCPCallError(Exception):
    pass


def _as_list(result: Any) -> list:
    """
    Normalise the output of a tool that is declared to return list[T].

    FastMCP serialises list[dict] as one TextContent per element, so:
      • 0 elements → content=[]    → _call_tool returns None
      • 1 element  → 1 TextContent → _call_tool returns the dict directly
      • N elements → N TextContent → _call_tool returns list[dict] (correct)

    This helper coerces all three cases to a plain list.
    """
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return [result]  # single-element list


async def _call_tool(url: str, tool_name: str, arguments: dict[str, Any]) -> Any:
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

        if result.isError:
            parts = [item.text for item in (result.content or []) if hasattr(item, "text")]
            raise MCPCallError(f"MCP tool '{tool_name}' returned error: {' | '.join(parts)}")

        content = result.content
        if not content:
            return None

        text_items = [item.text for item in content if hasattr(item, "text")]
        if not text_items:
            return None

        if len(text_items) == 1:
            # Normal single-value response
            try:
                return json.loads(text_items[0])
            except json.JSONDecodeError:
                return text_items[0]

        # Multiple TextContent items → FastMCP serialised a list[dict]
        # (one TextContent element per list item)
        results = []
        for text in text_items:
            try:
                results.append(json.loads(text))
            except json.JSONDecodeError:
                results.append(text)
        return results

    except MCPCallError:
        raise
    except Exception as e:
        raise MCPCallError(f"Failed to call '{tool_name}' on '{url}': {e}") from e


# ── Ledger client ─────────────────────────────────────────────────────────────

class _LedgerClient:
    def _url(self): return settings.mcp_ledger_url

    async def get_contract_lifecycle(self, contract_id: str) -> dict:
        return await _call_tool(self._url(), "get_contract_lifecycle",
                                {"contract_id": contract_id})

    async def get_state(self, contract_id: str) -> dict:
        return await _call_tool(self._url(), "get_state",
                                {"contract_id": contract_id})

    async def get_audit_trail(self, contract_id: str) -> list:
        return _as_list(await _call_tool(self._url(), "get_audit_trail",
                                         {"contract_id": contract_id}))


# ── Validation client ─────────────────────────────────────────────────────────

class _ValidationClient:
    def _url(self): return settings.mcp_validation_url

    async def get_quarantined(self, contract_id: str | None = None) -> list:
        return _as_list(await _call_tool(self._url(), "get_quarantined",
                                          {"contract_id": contract_id}))

    async def get_conflicts(self, contract_id: str | None = None) -> list:
        return _as_list(await _call_tool(self._url(), "get_conflicts",
                                          {"contract_id": contract_id}))

    async def resolve_conflict(
        self,
        conflict_pair_id: str,
        winning_event_id: str,
        admin_id: str,
        reason: str,
    ) -> dict:
        return await _call_tool(self._url(), "resolve_conflict", {
            "conflict_pair_id": conflict_pair_id,
            "winning_event_id": winning_event_id,
            "admin_id":         admin_id,
            "reason":           reason,
        })


# ── Reporting client ──────────────────────────────────────────────────────────

class _ReportingClient:
    def _url(self): return settings.mcp_reporting_url

    async def generate_report(self, report_type: str,
                              filters: dict | None = None,
                              requested_by: str = "dashboard") -> dict:
        return await _call_tool(self._url(), "generate_report",
                                {"report_type": report_type,
                                 "filters": filters or {},
                                 "requested_by": requested_by})

    async def list_reports(self, limit: int = 20,
                           report_type: str | None = None) -> list:
        args: dict[str, Any] = {"limit": limit}
        if report_type:
            args["report_type"] = report_type
        return _as_list(await _call_tool(self._url(), "list_reports", args))

    async def get_report(self, report_id: str) -> dict:
        return await _call_tool(self._url(), "get_report", {"report_id": report_id})

    async def export_report(self, report_id: str, format: str = "json") -> dict:  # noqa: A002
        return await _call_tool(self._url(), "export_report",
                                {"report_id": report_id, "format": format})


# ── LLAS client ───────────────────────────────────────────────────────────────

class _LLASClient:
    def _url(self): return settings.mcp_llas_url

    async def get_customer_profile(self, contract_id: str) -> dict:
        return await _call_tool(self._url(), "get_customer_profile",
                                {"contract_id": contract_id})


# ── Module-level singletons ───────────────────────────────────────────────────

ledger     = _LedgerClient()
validation = _ValidationClient()
reporting  = _ReportingClient()
llas       = _LLASClient()
