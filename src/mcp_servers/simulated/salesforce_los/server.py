"""
Salesforce LOS Simulated MCP Server

Simulates the Salesforce Loan Origination System (the target system in the
Oracle LOS → Salesforce LOS migration). Mirrors Oracle LOS capabilities and
adds sync_to_llas for pushing customer data to LLAS via the Integration System.

Tools:
  - originate_contract(contract_data)    → store contract + publish contract.originated
  - get_contract(contract_id)            → return contract from Salesforce LOS
  - get_contracts(filters?)              → list contracts
  - sync_to_llas(contract_id)            → push current contract data to LLAS via Integration System

Port: 8011
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.config import get_settings
from shared.logging import configure_logging, get_logger
from shared.mcp_caller import MCPCallError, call_mcp_tool
from shared.models.common import EventType, SourceSystem

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("salesforce-los", settings.log_level)
logger = get_logger(__name__)

STREAM_KEY = "smartledger:events"
SCHEMA_VERSION = "1.0"

# ─── Module-level state ───────────────────────────────────────────────────────

_contracts: dict[str, dict[str, Any]] = {}
_redis: aioredis.Redis | None = None

# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _redis
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await _redis.ping()
        logger.info("salesforce_los_redis_connected")
    except Exception as e:
        logger.warning("salesforce_los_redis_unavailable", error=str(e))
    logger.info("salesforce_los_started")
    yield
    if _redis:
        await _redis.aclose()
    logger.info("salesforce_los_shutdown")


mcp = FastMCP(
    name="simulated-salesforce-los",
    instructions=(
        "Simulated Salesforce LOS. Target system in the Oracle LOS migration. "
        "Mirrors Oracle LOS contract origination and adds LLAS sync capability."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def originate_contract(contract_data: dict) -> dict:
    """
    Originate a contract in Salesforce LOS and publish contract.originated event.

    Args:
        contract_data: full contract payload (same schema as Oracle LOS)

    Returns: {success, contract_id, stream_entry_id}
    """
    contract_id = contract_data.get("contract_id") or f"SF-{datetime.now().year}-{uuid.uuid4().hex[:6].upper()}"
    contract_data["contract_id"] = contract_id
    contract_data["created_at"] = datetime.now(timezone.utc).isoformat()
    contract_data["source_system"] = SourceSystem.SALESFORCE_LOS
    _contracts[contract_id] = contract_data

    # Sync LLAS account through the Integration System BEFORE publishing
    # contract.originated. SmartLedger validates the sync, writes a customer_update
    # ledger record, and creates the LLAS account. Per-contract Redis lock
    # serializes the two events so the account exists by the time origination
    # validation runs (RULE-XSYS-LLAS-PRESENT).
    financial_terms = contract_data.get("financial_terms", {})
    sync_payload: dict[str, Any] = {
        "contract_type":     contract_data.get("contract_type"),
        "amount_financed":   financial_terms.get("amount_financed"),
        "monthly_payment":   financial_terms.get("monthly_payment"),
        "term_months":       financial_terms.get("term_months"),
        "first_payment_date": contract_data.get("first_payment_date"),
        "origination_date":  contract_data.get("origination_date"),
        "dealer_id":         contract_data.get("dealer_id"),
        "los_updated_at":    contract_data["created_at"],
    }
    sync_payload = {k: v for k, v in sync_payload.items() if v is not None}
    try:
        await call_mcp_tool(
            url=settings.mcp_integration_url,
            tool_name="submit_llas_sync",
            arguments={
                "contract_id":   contract_id,
                "source_system": SourceSystem.SALESFORCE_LOS,
                "sync_payload":  sync_payload,
            },
        )
    except MCPCallError as e:
        logger.warning(
            "salesforce_los_initial_llas_sync_failed",
            contract_id=contract_id,
            error=str(e),
        )

    event_id = str(uuid.uuid4())
    message: dict[str, str] = {
        "event_id":       event_id,
        "event_type":     EventType.CONTRACT_ORIGINATED,
        "source_system":  SourceSystem.SALESFORCE_LOS,
        "contract_id":    contract_id,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "correlation_id": str(uuid.uuid4()),
        "schema_version": SCHEMA_VERSION,
        "payload":        json.dumps(contract_data),
    }

    entry_id = None
    if _redis:
        try:
            entry_id = await _redis.xadd(STREAM_KEY, message)
            logger.info("salesforce_los_contract_originated", contract_id=contract_id)
        except Exception as e:
            logger.error("salesforce_los_publish_failed", error=str(e))

    return {
        "success":         True,
        "contract_id":     contract_id,
        "stream_entry_id": entry_id,
    }


@mcp.tool()
async def get_contract(contract_id: str) -> dict:
    """Return a contract from Salesforce LOS."""
    contract = _contracts.get(contract_id)
    if not contract:
        return {"found": False, "contract_id": contract_id}
    return {"found": True, **contract}


@mcp.tool()
async def get_contracts(filters: dict | None = None) -> list:
    """List all contracts in Salesforce LOS, optionally filtered."""
    contracts = list(_contracts.values())
    if filters:
        status = filters.get("status")
        if status:
            contracts = [c for c in contracts if c.get("status") == status]
    return contracts


@mcp.tool()
async def sync_to_llas(contract_id: str) -> dict:
    """
    Sync current Salesforce LOS contract data to LLAS via the Integration System.

    SmartLedger validates for staleness — if this LOS data is older than the last
    validated ledger record, it will be quarantined as STALE_LOS_SYNC.

    Args:
        contract_id: the contract to sync

    Returns: {success, integration_ref, status}
    """
    contract = _contracts.get(contract_id)
    if not contract:
        return {"success": False, "reason": f"Contract '{contract_id}' not found in Salesforce LOS"}

    sync_payload = {
        "address": contract.get("customer", {}).get("address"),
        "contact": {
            "first_name": contract.get("customer", {}).get("first_name"),
            "last_name":  contract.get("customer", {}).get("last_name"),
            "phone":      contract.get("customer", {}).get("phone"),
            "email":      contract.get("customer", {}).get("email"),
        },
        "los_updated_at": contract.get("updated_at", contract.get("origination_date")),
    }
    sync_payload = {k: v for k, v in sync_payload.items() if v is not None}

    try:
        result = await call_mcp_tool(
            url=settings.mcp_integration_url,
            tool_name="submit_llas_sync",
            arguments={
                "contract_id":   contract_id,
                "source_system": SourceSystem.SALESFORCE_LOS,
                "sync_payload":  sync_payload,
            },
        )
    except MCPCallError as e:
        logger.error("salesforce_los_sync_failed", contract_id=contract_id, error=str(e))
        return {"success": False, "reason": f"Integration System unavailable: {e}"}

    integration_ref = result.get("integration_ref", "") if isinstance(result, dict) else ""
    logger.info("salesforce_los_sync_submitted", contract_id=contract_id, integration_ref=integration_ref)
    return {
        "success":         bool(integration_ref),
        "integration_ref": integration_ref,
        "status":          "pending_validation",
    }


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "salesforce_los"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8011)
