"""
Mobile App Simulated MCP Server

Simulates the mobile application used by customers to manage their auto loan/lease.
Customers can view their dashboard and submit payments from their phone.

Tools:
  - get_dashboard(customer_id)                         → compact account view for mobile
  - submit_payment(customer_id, contract_id, amount, payment_method) → mobile payment
  - get_notifications(customer_id)                     → pending alerts for the customer

Payments are published as payment.received events with source_system=mobile_app.
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
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
configure_logging("mobile-app-sim", settings.log_level)
logger = get_logger(__name__)

STREAM_KEY = "smartledger:events"
SCHEMA_VERSION = "1.0"
VALID_PAYMENT_METHODS = ("ach", "debit_card", "credit_card", "apple_pay", "google_pay")

# ─── Module-level state ───────────────────────────────────────────────────────

_redis: aioredis.Redis | None = None
_notifications: dict[str, list[dict[str, Any]]] = {}

# ─── Seed data ────────────────────────────────────────────────────────────────

_MOBILE_USERS: dict[str, dict[str, Any]] = {
    "CUST-001": {"customer_id": "CUST-001", "first_name": "James", "contracts": ["ORC-2024-001"]},
    "CUST-002": {"customer_id": "CUST-002", "first_name": "Maria", "contracts": ["ORC-2024-002"]},
}

_ACCOUNT_SNAPSHOTS: dict[str, dict[str, Any]] = {
    "ORC-2024-001": {
        "current_balance": 26980.50, "next_payment_due": "2026-04-01",
        "next_payment_amount": 487.50, "days_past_due": 0, "status": "active",
    },
    "ORC-2024-002": {
        "current_balance": 18200.00, "next_payment_due": "2026-04-15",
        "next_payment_amount": 349.00, "days_past_due": 0, "status": "active",
    },
}

_SEED_NOTIFICATIONS: dict[str, list[dict[str, Any]]] = {
    "CUST-001": [
        {"id": "NOTIF-001", "type": "payment_reminder", "message": "Payment of $487.50 due on 2026-04-01", "read": False},
    ],
    "CUST-002": [
        {"id": "NOTIF-002", "type": "payment_reminder", "message": "Payment of $349.00 due on 2026-04-15", "read": False},
    ],
}


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _redis

    for cid, notifs in _SEED_NOTIFICATIONS.items():
        _notifications[cid] = list(notifs)

    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await _redis.ping()
        logger.info("mobile_app_redis_connected")
    except Exception as e:
        logger.warning("mobile_app_redis_unavailable", error=str(e))

    logger.info("mobile_app_seeded", user_count=len(_MOBILE_USERS))
    yield

    if _redis:
        await _redis.aclose()
    logger.info("mobile_app_shutdown")


mcp = FastMCP(
    name="simulated-mobile-app",
    instructions=(
        "Simulated Mobile App. Provides compact account views and mobile payment submission "
        "for auto loan/lease customers. Publishes payment.received events to SmartLedger."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _publish_payment_event(
    contract_id: str,
    payment_id: str,
    amount: float,
    payment_method: str,
    customer_id: str,
    correlation_id: str,
) -> str | None:
    if not _redis:
        logger.warning("redis_not_available_event_dropped")
        return None

    payload: dict[str, Any] = {
        "payment_id":     payment_id,
        "contract_id":    contract_id,
        "amount":         round(amount, 2),
        "payment_method": payment_method,
        "payment_date":   str(date.today()),
        "customer_id":    customer_id,
        "channel":        "mobile",
        "source_system":  SourceSystem.MOBILE_APP,
        "status":         "submitted",
    }

    message: dict[str, str] = {
        "event_id":       str(uuid.uuid4()),
        "event_type":     EventType.PAYMENT_RECEIVED,
        "source_system":  SourceSystem.MOBILE_APP,
        "contract_id":    contract_id,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "schema_version": SCHEMA_VERSION,
        "payload":        json.dumps(payload),
    }

    try:
        entry_id = await _redis.xadd(STREAM_KEY, message)
        logger.info("mobile_payment_event_published", payment_id=payment_id, contract_id=contract_id)
        return entry_id
    except Exception as e:
        logger.error("mobile_payment_publish_failed", error=str(e))
        return None


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_dashboard(customer_id: str) -> dict:
    """
    Return a compact mobile dashboard view for a customer.

    Includes account status, next payment info, and unread notification count.
    Returns {found: False} if the customer has no mobile account.
    """
    user = _MOBILE_USERS.get(customer_id)
    if not user:
        return {"found": False, "customer_id": customer_id}

    accounts = []
    for cid in user.get("contracts", []):
        snap = _ACCOUNT_SNAPSHOTS.get(cid, {})
        accounts.append({
            "contract_id":         cid,
            "current_balance":     snap.get("current_balance", 0.0),
            "next_payment_due":    snap.get("next_payment_due"),
            "next_payment_amount": snap.get("next_payment_amount", 0.0),
            "days_past_due":       snap.get("days_past_due", 0),
            "status":              snap.get("status", "unknown"),
        })

    unread_count = sum(
        1 for n in _notifications.get(customer_id, []) if not n.get("read", True)
    )

    return {
        "found":          True,
        "customer_id":    customer_id,
        "first_name":     user["first_name"],
        "accounts":       accounts,
        "unread_notifications": unread_count,
    }


@mcp.tool()
async def submit_payment(
    customer_id: str,
    contract_id: str,
    amount: float,
    payment_method: str = "ach",
) -> dict:
    """
    Submit a payment from the mobile app.

    Args:
        customer_id:    mobile user making the payment
        contract_id:    the contract to pay
        amount:         payment amount in USD (must be > 0)
        payment_method: ach | debit_card | credit_card | apple_pay | google_pay

    Returns: {success, payment_id, contract_id, amount, stream_entry_id}
    """
    user = _MOBILE_USERS.get(customer_id)
    if not user:
        return {"success": False, "reason": f"Customer '{customer_id}' not found in mobile app"}

    if contract_id not in user.get("contracts", []):
        return {
            "success": False,
            "reason":  f"Contract '{contract_id}' not associated with customer '{customer_id}'",
        }

    if amount <= 0:
        raise ValueError(f"amount must be greater than zero (got {amount})")
    if payment_method not in VALID_PAYMENT_METHODS:
        raise ValueError(f"payment_method must be one of {VALID_PAYMENT_METHODS}")

    payment_id = f"MOBILE-{uuid.uuid4().hex[:8].upper()}"
    correlation_id = str(uuid.uuid4())

    entry_id = await _publish_payment_event(
        contract_id=contract_id,
        payment_id=payment_id,
        amount=amount,
        payment_method=payment_method,
        customer_id=customer_id,
        correlation_id=correlation_id,
    )

    logger.info(
        "mobile_payment_submitted",
        customer_id=customer_id,
        contract_id=contract_id,
        payment_id=payment_id,
        amount=amount,
    )

    return {
        "success":         True,
        "payment_id":      payment_id,
        "contract_id":     contract_id,
        "amount":          round(amount, 2),
        "payment_method":  payment_method,
        "stream_entry_id": entry_id,
        "correlation_id":  correlation_id,
    }


@mcp.tool()
async def get_notifications(customer_id: str) -> dict:
    """Return pending notifications for a customer (payment reminders, alerts)."""
    if customer_id not in _MOBILE_USERS:
        return {"found": False, "customer_id": customer_id}
    notifs = _notifications.get(customer_id, [])
    return {
        "found":       True,
        "customer_id": customer_id,
        "notifications": notifs,
        "unread_count": sum(1 for n in notifs if not n.get("read", True)),
    }


@mcp.tool()
async def update_contact_info(
    contract_id: str,
    customer_id: str,
    changes: dict,
) -> dict:
    """
    Self-service contact/address update from the mobile app.

    Submits the change to the Integration System for SmartLedger validation
    before LLAS is updated.

    Args:
        contract_id: the contract to update
        customer_id: the mobile user making the change
        changes:     dict with address and/or contact keys

    Returns: {success, integration_ref, status}
    """
    session_ref = f"MOBILE-SESSION-{uuid.uuid4().hex[:8].upper()}"
    try:
        result = await call_mcp_tool(
            url=settings.mcp_integration_url,
            tool_name="submit_contact_update",
            arguments={
                "contract_id":   contract_id,
                "source_system": SourceSystem.MOBILE_APP,
                "changes":       changes,
                "source_ref":    session_ref,
            },
        )
    except MCPCallError as e:
        logger.error("mobile_contact_update_failed", customer_id=customer_id, error=str(e))
        return {"success": False, "reason": f"Integration System unavailable: {e}"}

    integration_ref = result.get("integration_ref", "") if isinstance(result, dict) else ""
    logger.info("mobile_contact_update_submitted", customer_id=customer_id, contract_id=contract_id)
    return {
        "success":         bool(integration_ref),
        "integration_ref": integration_ref,
        "status":          "pending_validation",
        "note":            "SmartLedger will validate before LLAS is updated",
    }


@mcp.tool()
async def update_payment_method(
    contract_id: str,
    customer_id: str,
    changes: dict,
) -> dict:
    """
    Self-service payment method update from the mobile app.

    Args:
        contract_id: the contract to update
        customer_id: the mobile user making the change
        changes:     dict with payment_info fields

    Returns: {success, integration_ref, status}
    """
    session_ref = f"MOBILE-SESSION-{uuid.uuid4().hex[:8].upper()}"
    try:
        result = await call_mcp_tool(
            url=settings.mcp_integration_url,
            tool_name="submit_payment_update",
            arguments={
                "contract_id":   contract_id,
                "source_system": SourceSystem.MOBILE_APP,
                "changes":       {"payment_info": changes},
                "source_ref":    session_ref,
            },
        )
    except MCPCallError as e:
        logger.error("mobile_payment_update_failed", customer_id=customer_id, error=str(e))
        return {"success": False, "reason": f"Integration System unavailable: {e}"}

    integration_ref = result.get("integration_ref", "") if isinstance(result, dict) else ""
    logger.info("mobile_payment_method_submitted", customer_id=customer_id, contract_id=contract_id)
    return {
        "success":         bool(integration_ref),
        "integration_ref": integration_ref,
        "status":          "pending_validation",
        "note":            "SmartLedger will validate before LLAS is updated",
    }


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "mobile_app"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8018)
