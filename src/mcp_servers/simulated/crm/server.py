"""
CRM Simulated MCP Server

Simulates a CRM system used by customer service agents. When a customer calls in,
agents create Service Requests (SRs) to track the work. When an SR is completed,
the CRM submits the data change to LLAS via the Integration System.

The Integration System is the boundary SmartLedger intercepts — the CRM has no
awareness of validation rules or LLAS state.

Tools:
  - create_service_request(contract_id, sr_type, requested_changes, customer_id) → SR ref
  - get_service_request(sr_id)                                                    → SR details
  - complete_service_request(sr_id)                                               → integration_ref
  - list_service_requests(contract_id?, status?)                                  → SR list
  - get_customer(customer_id)                                                     → customer record

SR types: CONTACT_UPDATE | PAYMENT_UPDATE | INSURANCE_UPDATE | COBORROWER_UPDATE

Port: 8013
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.config import get_settings
from shared.logging import configure_logging, get_logger
from shared.mcp_caller import MCPCallError, call_mcp_tool

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("crm-sim", settings.log_level)
logger = get_logger(__name__)

VALID_SR_TYPES = ("CONTACT_UPDATE", "PAYMENT_UPDATE", "INSURANCE_UPDATE", "COBORROWER_UPDATE")

# ─── Module-level state ───────────────────────────────────────────────────────

_service_requests: dict[str, dict[str, Any]] = {}

# ─── Seed data ────────────────────────────────────────────────────────────────

_CUSTOMERS: dict[str, dict[str, Any]] = {
    "CUST-001": {
        "customer_id":  "CUST-001",
        "first_name":   "James",
        "last_name":    "Carter",
        "email":        "james.carter@example.com",
        "phone":        "214-555-0101",
        "contracts":    ["ORC-2024-001"],
    },
    "CUST-002": {
        "customer_id":  "CUST-002",
        "first_name":   "Maria",
        "last_name":    "Gonzalez",
        "email":        "maria.gonzalez@example.com",
        "phone":        "512-555-0202",
        "contracts":    ["ORC-2024-002"],
    },
}


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    logger.info("crm_sim_started", customer_count=len(_CUSTOMERS))
    yield
    logger.info("crm_sim_shutdown")


mcp = FastMCP(
    name="simulated-crm",
    instructions=(
        "Simulated CRM system. Customer service agents create and manage Service Requests "
        "when customers call in. When an SR is completed, the data change is submitted "
        "to LLAS via the Integration System (which SmartLedger intercepts for validation)."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def create_service_request(
    contract_id: str,
    sr_type: str,
    requested_changes: dict,
    customer_id: str = "",
) -> dict:
    """
    Create a Service Request (SR) for a customer-initiated data change.

    An SR tracks a customer request from intake through completion. When the
    agent completes the SR, the CRM submits the change to LLAS via the
    Integration System.

    Args:
        contract_id:       the contract the SR is against
        sr_type:           CONTACT_UPDATE | PAYMENT_UPDATE | INSURANCE_UPDATE | COBORROWER_UPDATE
        requested_changes: dict of proposed changes (address, contact, payment_info, or insurance)
        customer_id:       optional customer ID for lookup

    Returns: {success, sr_id, status}
    """
    if sr_type not in VALID_SR_TYPES:
        return {
            "success": False,
            "reason":  f"sr_type must be one of {VALID_SR_TYPES}",
        }
    if not contract_id:
        return {"success": False, "reason": "contract_id is required"}
    if not requested_changes:
        return {"success": False, "reason": "requested_changes cannot be empty"}

    year  = datetime.now(timezone.utc).year
    sr_id = f"SR-{year}-{uuid.uuid4().hex[:4].upper()}"
    now   = datetime.now(timezone.utc).isoformat()

    sr: dict[str, Any] = {
        "sr_id":             sr_id,
        "contract_id":       contract_id,
        "customer_id":       customer_id,
        "sr_type":           sr_type,
        "requested_changes": requested_changes,
        "status":            "open",
        "created_at":        now,
        "updated_at":        now,
        "integration_ref":   None,
    }
    _service_requests[sr_id] = sr

    logger.info(
        "crm_sr_created",
        sr_id=sr_id,
        contract_id=contract_id,
        sr_type=sr_type,
    )
    return {"success": True, "sr_id": sr_id, "status": "open"}


@mcp.tool()
async def get_service_request(sr_id: str) -> dict:
    """Return the details of a Service Request."""
    sr = _service_requests.get(sr_id)
    if not sr:
        return {"found": False, "sr_id": sr_id}
    return {"found": True, **sr}


@mcp.tool()
async def complete_service_request(sr_id: str) -> dict:
    """
    Complete a Service Request by submitting the data change to LLAS via
    the Integration System.

    The CRM calls the Integration System MCP with the requested changes.
    The Integration System publishes the event to SmartLedger's event bus.
    SmartLedger validates and audits the change before LLAS is updated.

    Returns: {success, sr_id, integration_ref, status}
    """
    sr = _service_requests.get(sr_id)
    if not sr:
        return {"found": False, "sr_id": sr_id}
    if sr["status"] != "open":
        return {
            "success": False,
            "sr_id":   sr_id,
            "reason":  f"SR is already '{sr['status']}' — cannot complete",
        }

    # Map SR type to Integration System tool name
    _sr_type_to_tool = {
        "CONTACT_UPDATE":   "submit_contact_update",
        "PAYMENT_UPDATE":   "submit_payment_update",
        "INSURANCE_UPDATE": "submit_insurance_update",
        "COBORROWER_UPDATE": "submit_contact_update",  # coborrower is a contact update
    }
    tool_name = _sr_type_to_tool.get(sr["sr_type"], "submit_contact_update")

    # Wrap changes under the appropriate key for the integration system
    sr_type = sr["sr_type"]
    raw_changes = sr["requested_changes"]
    if sr_type == "PAYMENT_UPDATE":
        changes = {"payment_info": raw_changes} if "payment_info" not in raw_changes else raw_changes
    elif sr_type == "INSURANCE_UPDATE":
        changes = {"insurance": raw_changes} if "insurance" not in raw_changes else raw_changes
    else:
        # CONTACT_UPDATE and COBORROWER_UPDATE — pass as-is
        changes = raw_changes

    try:
        result = await call_mcp_tool(
            url=settings.mcp_integration_url,
            tool_name=tool_name,
            arguments={
                "contract_id":   sr["contract_id"],
                "source_system": "crm",
                "changes":       changes,
                "source_ref":    sr_id,
            },
        )
    except MCPCallError as e:
        logger.error("crm_integration_call_failed", sr_id=sr_id, error=str(e))
        return {
            "success": False,
            "sr_id":   sr_id,
            "reason":  f"Integration System unavailable: {e}",
        }

    integration_ref = result.get("integration_ref", "") if isinstance(result, dict) else ""
    sr["status"] = "completed"
    sr["integration_ref"] = integration_ref
    sr["updated_at"] = datetime.now(timezone.utc).isoformat()

    logger.info(
        "crm_sr_completed",
        sr_id=sr_id,
        contract_id=sr["contract_id"],
        integration_ref=integration_ref,
    )
    return {
        "success":         True,
        "sr_id":           sr_id,
        "integration_ref": integration_ref,
        "status":          "completed",
        "note":            "SmartLedger will validate before LLAS is updated",
    }


@mcp.tool()
async def list_service_requests(
    contract_id: str | None = None,
    status: str | None = None,
) -> list:
    """
    Return Service Requests, optionally filtered by contract_id and/or status.

    Args:
        contract_id: filter to a specific contract (optional)
        status:      filter by status: open | completed (optional)
    """
    srs = list(_service_requests.values())
    if contract_id:
        srs = [s for s in srs if s["contract_id"] == contract_id]
    if status:
        srs = [s for s in srs if s["status"] == status]
    return sorted(srs, key=lambda s: s.get("created_at", ""), reverse=True)


@mcp.tool()
async def get_customer(customer_id: str) -> dict:
    """Return a customer record from the CRM."""
    customer = _CUSTOMERS.get(customer_id)
    if not customer:
        return {"found": False, "customer_id": customer_id}
    return {"found": True, **customer}


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "crm"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8013)
