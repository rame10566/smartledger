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


def _as_list(result: Any) -> list:
    """
    Normalise the output of a tool declared to return list[T].

    FastMCP serialises list[dict] as one TextContent per element, so:
      • 0 elements → content=[]    → _parse_result returns None
      • 1 element  → 1 TextContent → _parse_result returns the dict directly
      • N elements → N TextContent → _parse_result returns list[dict] (correct)

    This helper coerces all three cases to a plain list.
    """
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return [result]


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

    FastMCP serialises tool return values as JSON text in TextContent items.
    When a tool returns list[dict], FastMCP emits one TextContent per element.
    We detect that case (multiple items) and rebuild the list by parsing each
    item individually.  Single-item responses are parsed as before.
    """
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

    # Multiple TextContent items → FastMCP serialised a list[dict] (one item per element)
    results = []
    for text in text_items:
        try:
            results.append(json.loads(text))
        except json.JSONDecodeError:
            results.append(text)
    return results


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
        return _as_list(await call_tool(self._url(), "get_contracts",
                                        {"filters": filters}))


class LLASClient:
    """MCP client for the LLAS simulator (port 8012)."""
    _url = lambda self: settings.mcp_llas_url

    async def get_account(self, contract_id: str) -> dict:
        return await call_tool(self._url(), "get_account",
                               {"contract_id": contract_id})

    async def get_balance(self, contract_id: str) -> dict:
        return await call_tool(self._url(), "get_balance",
                               {"contract_id": contract_id})

    async def create_account(self, contract_id: str, account_data: dict) -> dict:
        return await call_tool(self._url(), "create_account",
                               {"contract_id": contract_id, "account_data": account_data})

    async def post_payment(self, contract_id: str, payment_data: dict) -> dict:
        return await call_tool(self._url(), "post_payment",
                               {"contract_id": contract_id, "payment_data": payment_data})

    async def get_payment_history(self, contract_id: str, limit: int = 12) -> dict:
        return await call_tool(self._url(), "get_payment_history",
                               {"contract_id": contract_id, "limit": limit})

    async def get_customer_profile(self, contract_id: str) -> dict:
        return await call_tool(self._url(), "get_customer_profile",
                               {"contract_id": contract_id})

    async def update_customer_profile(
        self,
        contract_id: str,
        changes: dict,
        validated_by: str = "smartledger",
        source_system: str = "",
    ) -> dict:
        return await call_tool(self._url(), "update_customer_profile", {
            "contract_id":   contract_id,
            "changes":       changes,
            "validated_by":  validated_by,
            "source_system": source_system,
        })

    async def get_payment_info(self, contract_id: str) -> dict:
        return await call_tool(self._url(), "get_payment_info",
                               {"contract_id": contract_id})


class ValidationClient:
    """MCP client for the Validation Engine (port 8001)."""
    _url = lambda self: settings.mcp_validation_url

    async def validate_event(self, request: dict) -> dict:
        return await call_tool(self._url(), "validate_event",
                               {"request": request})

    async def get_quarantined(self, contract_id: str | None = None) -> list:
        return _as_list(await call_tool(self._url(), "get_quarantined",
                                        {"contract_id": contract_id}))

    async def get_conflicts(self, contract_id: str | None = None) -> list:
        return _as_list(await call_tool(self._url(), "get_conflicts",
                                        {"contract_id": contract_id}))

    async def resolve_conflict(
        self,
        conflict_pair_id: str,
        winning_event_id: str,
        admin_id: str,
        reason: str,
    ) -> dict:
        return await call_tool(self._url(), "resolve_conflict", {
            "conflict_pair_id": conflict_pair_id,
            "winning_event_id": winning_event_id,
            "admin_id":         admin_id,
            "reason":           reason,
        })


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
        return _as_list(await call_tool(self._url(), "get_audit_trail",
                                        {"contract_id": contract_id}))


class PaymentClient:
    """MCP client for the Payment simulator (port 8014)."""
    _url = lambda self: settings.mcp_payment_url

    async def submit_payment(
        self,
        contract_id: str,
        amount: float,
        payment_method: str,
        reference: str = "",
        correlation_id: str = "",
    ) -> dict:
        return await call_tool(self._url(), "submit_payment", {
            "contract_id":    contract_id,
            "amount":         amount,
            "payment_method": payment_method,
            "reference":      reference,
            "correlation_id": correlation_id,
        })

    async def get_payment(self, payment_id: str) -> dict:
        return await call_tool(self._url(), "get_payment",
                               {"payment_id": payment_id})

    async def get_payments_for_contract(self, contract_id: str, limit: int = 10) -> dict:
        return await call_tool(self._url(), "get_payments_for_contract",
                               {"contract_id": contract_id, "limit": limit})


class SemanticAIClient:
    """MCP client for the Semantic AI Engine (port 8003)."""
    _url = lambda self: settings.mcp_semantic_ai_url

    async def extract_contract_fields(self, document_text: str, document_id: str = "") -> dict:
        return await call_tool(self._url(), "extract_contract_fields", {
            "document_text": document_text,
            "document_id":   document_id,
        })

    async def get_extraction_result(self, extraction_id: str) -> dict:
        return await call_tool(self._url(), "get_extraction_result",
                               {"extraction_id": extraction_id})

    async def submit_for_review(self, extraction_id: str, reason: str = "") -> dict:
        return await call_tool(self._url(), "submit_for_review",
                               {"extraction_id": extraction_id, "reason": reason})

    async def list_review_queue(self) -> list:
        return _as_list(await call_tool(self._url(), "list_review_queue", {}))


class ReportingClient:
    """MCP client for the Reporting MCP server (port 8004)."""
    _url = lambda self: settings.mcp_reporting_url

    async def generate_report(
        self,
        report_type: str,
        filters: dict | None = None,
        requested_by: str = "dashboard",
    ) -> dict:
        return await call_tool(self._url(), "generate_report", {
            "report_type":  report_type,
            "filters":      filters or {},
            "requested_by": requested_by,
        })

    async def list_reports(
        self,
        limit: int = 20,
        report_type: str | None = None,
    ) -> list:
        args: dict[str, Any] = {"limit": limit}
        if report_type:
            args["report_type"] = report_type
        return _as_list(await call_tool(self._url(), "list_reports", args))

    async def get_report(self, report_id: str) -> dict:
        return await call_tool(self._url(), "get_report", {"report_id": report_id})

    async def export_report(self, report_id: str, format: str = "json") -> dict:  # noqa: A002
        return await call_tool(self._url(), "export_report",
                               {"report_id": report_id, "format": format})


class RulesEngineClient:
    """MCP client for the Rules Engine simulator (port 8020)."""
    _url = lambda self: settings.mcp_rules_engine_url

    async def evaluate_eligibility(self, application: dict) -> dict:
        return await call_tool(self._url(), "evaluate_eligibility",
                               {"application": application})

    async def get_credit_tier(self, credit_score: int) -> dict:
        return await call_tool(self._url(), "get_credit_tier",
                               {"credit_score": credit_score})

    async def get_rule_set(self, contract_type: str = "loan") -> dict:
        return await call_tool(self._url(), "get_rule_set",
                               {"contract_type": contract_type})


class PricingEngineClient:
    """MCP client for the Pricing Engine simulator (port 8021)."""
    _url = lambda self: settings.mcp_pricing_engine_url

    async def calculate_rate(self, request: dict) -> dict:
        return await call_tool(self._url(), "calculate_rate",
                               {"request": request})

    async def calculate_payment(self, request: dict) -> dict:
        return await call_tool(self._url(), "calculate_payment",
                               {"request": request})

    async def get_rate_card(self, contract_type: str = "loan") -> dict:
        return await call_tool(self._url(), "get_rate_card",
                               {"contract_type": contract_type})

    async def get_pricing_factors(self) -> dict:
        return await call_tool(self._url(), "get_pricing_factors", {})


class IntegrationSystemClient:
    """MCP client for the Integration System simulator (port 8022)."""
    _url = lambda self: settings.mcp_integration_url

    async def submit_contact_update(
        self,
        contract_id: str,
        source_system: str,
        changes: dict,
        source_ref: str = "",
    ) -> dict:
        return await call_tool(self._url(), "submit_contact_update", {
            "contract_id":   contract_id,
            "source_system": source_system,
            "changes":       changes,
            "source_ref":    source_ref,
        })

    async def submit_payment_update(
        self,
        contract_id: str,
        source_system: str,
        changes: dict,
        source_ref: str = "",
    ) -> dict:
        return await call_tool(self._url(), "submit_payment_update", {
            "contract_id":   contract_id,
            "source_system": source_system,
            "changes":       changes,
            "source_ref":    source_ref,
        })

    async def update_integration_status(
        self,
        integration_ref: str,
        status: str,
        detail: str = "",
    ) -> dict:
        return await call_tool(self._url(), "update_integration_status", {
            "integration_ref": integration_ref,
            "status":          status,
            "detail":          detail,
        })

    async def get_integration_status(self, integration_ref: str) -> dict:
        return await call_tool(self._url(), "get_integration_status",
                               {"integration_ref": integration_ref})


# ── Module-level singleton clients ────────────────────────────────────────────

oracle_los          = OracleLOSClient()
llas                = LLASClient()
validation          = ValidationClient()
ledger              = LedgerClient()
payment             = PaymentClient()
semantic_ai         = SemanticAIClient()
reporting           = ReportingClient()
rules_engine        = RulesEngineClient()
pricing_engine      = PricingEngineClient()
integration_system  = IntegrationSystemClient()
