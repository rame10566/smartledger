"""
Integration System Simulated MCP Server

Simulates the integration middleware (MuleSoft/Boomi/IBM IIB style data mover) that
sits between source systems (CRM, Portal, Mobile, LOS) and the LLAS system-of-record.

The Integration System is a DATA MOVER — it does only basic format/syntax validation.
It has no business rule awareness and no cross-system visibility. This is the root cause
of data integrity issues in capital finance: changes reach LLAS without proper validation.

SmartLedger intercepts at this boundary: when the Integration System publishes an event,
the SmartLedger agent validates it before the change is applied to LLAS.

Tools:
  - submit_contact_update(contract_id, source_system, changes, source_ref)    → integration_ref
  - submit_payment_update(contract_id, source_system, changes, source_ref)    → integration_ref
  - submit_insurance_update(contract_id, source_system, changes, source_ref)  → integration_ref
  - submit_llas_sync(contract_id, source_system, sync_payload)                → integration_ref
  - get_integration_status(integration_ref)                                   → status

Port: 8022
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
from shared.models.common import EventType, SourceSystem

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("integration-sim", settings.log_level)
logger = get_logger(__name__)

STREAM_KEY = "smartledger:events"
SCHEMA_VERSION = "1.0"

# ─── Module-level state ───────────────────────────────────────────────────────

_redis: aioredis.Redis | None = None
_submissions: dict[str, dict[str, Any]] = {}  # integration_ref → submission record


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _redis
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await _redis.ping()
        logger.info("integration_redis_connected")
    except Exception as e:
        logger.warning("integration_redis_unavailable", error=str(e))
    logger.info("integration_sim_started")
    yield
    if _redis:
        await _redis.aclose()
    logger.info("integration_sim_shutdown")


mcp = FastMCP(
    name="simulated-integration",
    instructions=(
        "Simulated Integration System (data mover middleware). "
        "Receives data change requests from CRM, Portal, Mobile, and LOS. "
        "Performs only basic format/syntax checks — no business rules. "
        "Publishes events to SmartLedger for validation before LLAS is updated."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _basic_format_check(contract_id: str, changes: dict) -> list[str]:
    """
    Integration System level check — format and syntax only.
    No business rules, no cross-system awareness.
    """
    errors = []
    if not contract_id or not str(contract_id).strip():
        errors.append("contract_id is required")
    if not changes:
        errors.append("changes dict is required and cannot be empty")
    return errors


async def _publish_integration_event(
    event_type: str,
    contract_id: str,
    source_system: str,
    integration_ref: str,
    changes: dict,
    source_ref: str,
) -> str | None:
    """Publish an integration event to the SmartLedger event bus."""
    if not _redis:
        logger.warning("redis_not_available_event_dropped", integration_ref=integration_ref)
        return None

    payload: dict[str, Any] = {
        "contract_id":      contract_id,
        "source_system":    source_system,
        "integration_ref":  integration_ref,
        "source_ref":       source_ref,
        "changes":          changes,
        "submitted_at":     datetime.now(timezone.utc).isoformat(),
    }

    message: dict[str, str] = {
        "event_id":       str(uuid.uuid4()),
        "event_type":     event_type,
        "source_system":  SourceSystem.INTEGRATION_SYSTEM,
        "contract_id":    contract_id,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "correlation_id": integration_ref,
        "schema_version": SCHEMA_VERSION,
        "payload":        json.dumps(payload),
    }

    try:
        entry_id = await _redis.xadd(STREAM_KEY, message)
        logger.info(
            "integration_event_published",
            event_type=event_type,
            contract_id=contract_id,
            integration_ref=integration_ref,
            source_system=source_system,
        )
        return entry_id
    except Exception as e:
        logger.error("integration_event_publish_failed", error=str(e))
        return None


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def submit_contact_update(
    contract_id: str,
    source_system: str,
    changes: dict,
    source_ref: str = "",
) -> dict:
    """
    Submit a customer contact/address update from a source system.

    Called by CRM (when completing a contact-update SR), Customer Portal,
    or Mobile App when a customer updates their address or contact details.

    The Integration System does basic format checking only. SmartLedger validates
    the change and audits it before LLAS is updated.

    Args:
        contract_id:   the contract whose customer profile is being updated
        source_system: crm | customer_portal | mobile_app | oracle_los | salesforce_los
        changes:       dict with any of: address, contact (partial updates allowed)
        source_ref:    reference from the source system (SR number, session ID, etc.)

    Returns: {success, integration_ref, status}
    """
    errors = _basic_format_check(contract_id, changes)
    if errors:
        return {"success": False, "errors": errors}

    integration_ref = f"INT-{uuid.uuid4().hex[:12].upper()}"
    entry_id = await _publish_integration_event(
        event_type=EventType.INTEGRATION_CONTACT_UPDATE,
        contract_id=contract_id,
        source_system=source_system,
        integration_ref=integration_ref,
        changes=changes,
        source_ref=source_ref,
    )

    _submissions[integration_ref] = {
        "integration_ref": integration_ref,
        "event_type":      EventType.INTEGRATION_CONTACT_UPDATE,
        "contract_id":     contract_id,
        "source_system":   source_system,
        "source_ref":      source_ref,
        "status":          "pending_validation" if entry_id else "publish_failed",
        "submitted_at":    datetime.now(timezone.utc).isoformat(),
    }

    return {
        "success":         bool(entry_id),
        "integration_ref": integration_ref,
        "status":          "pending_validation",
        "note":            "SmartLedger will validate before LLAS is updated",
    }


@mcp.tool()
async def submit_payment_update(
    contract_id: str,
    source_system: str,
    changes: dict,
    source_ref: str = "",
) -> dict:
    """
    Submit a payment method update from a source system.

    Called by CRM (payment-update SR), Customer Portal, or Mobile App
    when a customer changes their ACH account or payment date.

    Args:
        contract_id:   the contract whose payment info is being updated
        source_system: source system name
        changes:       dict with payment_info fields (method, bank_account_last4, payment_date, etc.)
        source_ref:    reference from source system

    Returns: {success, integration_ref, status}
    """
    errors = _basic_format_check(contract_id, changes)
    if errors:
        return {"success": False, "errors": errors}

    integration_ref = f"INT-{uuid.uuid4().hex[:12].upper()}"
    entry_id = await _publish_integration_event(
        event_type=EventType.INTEGRATION_PAYMENT_UPDATE,
        contract_id=contract_id,
        source_system=source_system,
        integration_ref=integration_ref,
        changes=changes,
        source_ref=source_ref,
    )

    _submissions[integration_ref] = {
        "integration_ref": integration_ref,
        "event_type":      EventType.INTEGRATION_PAYMENT_UPDATE,
        "contract_id":     contract_id,
        "source_system":   source_system,
        "source_ref":      source_ref,
        "status":          "pending_validation" if entry_id else "publish_failed",
        "submitted_at":    datetime.now(timezone.utc).isoformat(),
    }

    return {
        "success":         bool(entry_id),
        "integration_ref": integration_ref,
        "status":          "pending_validation",
        "note":            "SmartLedger will validate before LLAS is updated",
    }


@mcp.tool()
async def submit_insurance_update(
    contract_id: str,
    source_system: str,
    changes: dict,
    source_ref: str = "",
) -> dict:
    """
    Submit an insurance record update from a source system.

    Args:
        contract_id:   the contract whose insurance info is being updated
        source_system: source system name
        changes:       dict with insurance fields (carrier, policy_number, expiry, etc.)
        source_ref:    reference from source system

    Returns: {success, integration_ref, status}
    """
    errors = _basic_format_check(contract_id, changes)
    if errors:
        return {"success": False, "errors": errors}

    integration_ref = f"INT-{uuid.uuid4().hex[:12].upper()}"
    entry_id = await _publish_integration_event(
        event_type=EventType.INTEGRATION_INSURANCE_UPDATE,
        contract_id=contract_id,
        source_system=source_system,
        integration_ref=integration_ref,
        changes=changes,
        source_ref=source_ref,
    )

    _submissions[integration_ref] = {
        "integration_ref": integration_ref,
        "event_type":      EventType.INTEGRATION_INSURANCE_UPDATE,
        "contract_id":     contract_id,
        "source_system":   source_system,
        "source_ref":      source_ref,
        "status":          "pending_validation" if entry_id else "publish_failed",
        "submitted_at":    datetime.now(timezone.utc).isoformat(),
    }

    return {
        "success":         bool(entry_id),
        "integration_ref": integration_ref,
        "status":          "pending_validation",
        "note":            "SmartLedger will validate before LLAS is updated",
    }


@mcp.tool()
async def submit_llas_sync(
    contract_id: str,
    source_system: str,
    sync_payload: dict,
) -> dict:
    """
    Submit a full LLAS sync from an LOS system.

    Called by Oracle LOS or Salesforce LOS when syncing contract master data
    to LLAS (e.g. after a contract amendment). SmartLedger validates for
    staleness — if the LOS data is older than the last validated ledger record,
    it is quarantined as STALE_LOS_SYNC.

    Args:
        contract_id:   the contract being synced
        source_system: oracle_los | salesforce_los
        sync_payload:  full or partial contract data from the LOS

    Returns: {success, integration_ref, status}
    """
    if not contract_id:
        return {"success": False, "errors": ["contract_id is required"]}

    integration_ref = f"INT-{uuid.uuid4().hex[:12].upper()}"
    entry_id = await _publish_integration_event(
        event_type=EventType.INTEGRATION_LLAS_SYNC,
        contract_id=contract_id,
        source_system=source_system,
        integration_ref=integration_ref,
        changes=sync_payload,
        source_ref=f"LOS-SYNC-{contract_id}",
    )

    _submissions[integration_ref] = {
        "integration_ref": integration_ref,
        "event_type":      EventType.INTEGRATION_LLAS_SYNC,
        "contract_id":     contract_id,
        "source_system":   source_system,
        "status":          "pending_validation" if entry_id else "publish_failed",
        "submitted_at":    datetime.now(timezone.utc).isoformat(),
    }

    return {
        "success":         bool(entry_id),
        "integration_ref": integration_ref,
        "status":          "pending_validation",
        "note":            "SmartLedger will validate for staleness before LLAS is updated",
    }


@mcp.tool()
async def get_integration_status(integration_ref: str) -> dict:
    """
    Return the current status of an integration submission.

    Status values:
      pending_validation  — event published, SmartLedger not yet processed
      validated           — SmartLedger validated, LLAS was updated
      quarantined         — SmartLedger rejected, LLAS not updated
      conflict            — competing update detected, pending LLAS Admin resolution
      resolved            — LLAS Admin selected authoritative value, LLAS updated
      publish_failed      — event could not be published to the bus

    Note: the status is updated by the agent via the update_integration_status tool.
    """
    submission = _submissions.get(integration_ref)
    if not submission:
        return {"found": False, "integration_ref": integration_ref}
    return {"found": True, **submission}


@mcp.tool()
async def update_integration_status(integration_ref: str, status: str, detail: str = "") -> dict:
    """
    Update the status of an integration submission.
    Called by the SmartLedger agent after processing the event.

    Args:
        integration_ref: the integration reference to update
        status:          new status (validated | quarantined | conflict | resolved)
        detail:          optional detail message

    Returns: {success}
    """
    submission = _submissions.get(integration_ref)
    if not submission:
        return {"success": False, "reason": f"integration_ref '{integration_ref}' not found"}

    submission["status"] = status
    submission["updated_at"] = datetime.now(timezone.utc).isoformat()
    if detail:
        submission["detail"] = detail

    logger.info(
        "integration_status_updated",
        integration_ref=integration_ref,
        status=status,
    )
    return {"success": True, "integration_ref": integration_ref, "status": status}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8022)
