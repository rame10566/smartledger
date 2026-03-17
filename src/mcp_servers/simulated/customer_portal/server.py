"""
Customer Portal Simulated MCP Server

Simulates the web-based customer self-service portal.
Customers can view their account, make payments, and see their schedule.

Tools:
  - get_account_summary(customer_id)               → account balances, next payment, status
  - make_payment(customer_id, contract_id, amount, payment_method) → submit via portal
  - get_payment_schedule(contract_id, months=3)    → upcoming payment dates and amounts
  - get_portal_activity(customer_id, limit=10)     → recent portal activity log

Payments are published as payment.received events with source_system=customer_portal.
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
from shared.models.common import EventType, SourceSystem

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("customer-portal-sim", settings.log_level)
logger = get_logger(__name__)

STREAM_KEY = "smartledger:events"
SCHEMA_VERSION = "1.0"
VALID_PAYMENT_METHODS = ("ach", "debit_card", "credit_card")

# ─── Module-level state ───────────────────────────────────────────────────────

_redis: aioredis.Redis | None = None
_activity_log: dict[str, list[dict[str, Any]]] = {}

# ─── Seed data ────────────────────────────────────────────────────────────────

_PORTAL_USERS: dict[str, dict[str, Any]] = {
    "CUST-001": {
        "customer_id":      "CUST-001",
        "first_name":       "James",
        "last_name":        "Carter",
        "email":            "james.carter@example.com",
        "contracts":        ["ORC-2024-001"],
        "preferred_method": "ach",
    },
    "CUST-002": {
        "customer_id":      "CUST-002",
        "first_name":       "Maria",
        "last_name":        "Gonzalez",
        "email":            "maria.gonzalez@example.com",
        "contracts":        ["ORC-2024-002"],
        "preferred_method": "ach",
    },
}

# Mirrors LLAS seed data — approximate balance snapshot for portal display
_ACCOUNT_SNAPSHOTS: dict[str, dict[str, Any]] = {
    "ORC-2024-001": {
        "current_balance":      26980.50,
        "next_payment_due":     "2026-04-01",
        "next_payment_amount":  487.50,
        "days_past_due":        0,
        "status":               "active",
        "monthly_payment":      487.50,
    },
    "ORC-2024-002": {
        "current_balance":      18200.00,
        "next_payment_due":     "2026-04-15",
        "next_payment_amount":  349.00,
        "days_past_due":        0,
        "status":               "active",
        "monthly_payment":      349.00,
    },
}


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _redis

    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await _redis.ping()
        logger.info("customer_portal_redis_connected")
    except Exception as e:
        logger.warning("customer_portal_redis_unavailable", error=str(e))

    logger.info("customer_portal_seeded", user_count=len(_PORTAL_USERS))
    yield

    if _redis:
        await _redis.aclose()
    logger.info("customer_portal_shutdown")


mcp = FastMCP(
    name="simulated-customer-portal",
    instructions=(
        "Simulated Customer Portal. Web self-service for auto loan/lease customers. "
        "Customers can view balances, make payments, and check payment schedules."
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
        "channel":        "web",
        "source_system":  SourceSystem.CUSTOMER_PORTAL,
        "status":         "submitted",
    }

    message: dict[str, str] = {
        "event_id":       str(uuid.uuid4()),
        "event_type":     EventType.PAYMENT_RECEIVED,
        "source_system":  SourceSystem.CUSTOMER_PORTAL,
        "contract_id":    contract_id,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "schema_version": SCHEMA_VERSION,
        "payload":        json.dumps(payload),
    }

    try:
        entry_id = await _redis.xadd(STREAM_KEY, message)
        logger.info(
            "portal_payment_event_published",
            payment_id=payment_id,
            contract_id=contract_id,
            amount=amount,
        )
        return entry_id
    except Exception as e:
        logger.error("portal_payment_publish_failed", error=str(e))
        return None


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_account_summary(customer_id: str) -> dict:
    """
    Return the account summary for a portal customer.

    Returns all contracts for the customer with balance, next payment,
    and account status. Returns {found: False} if customer is not in portal.
    """
    user = _PORTAL_USERS.get(customer_id)
    if not user:
        return {"found": False, "customer_id": customer_id}

    accounts = []
    for cid in user.get("contracts", []):
        snapshot = _ACCOUNT_SNAPSHOTS.get(cid, {})
        accounts.append({
            "contract_id":         cid,
            "current_balance":     snapshot.get("current_balance", 0.0),
            "next_payment_due":    snapshot.get("next_payment_due"),
            "next_payment_amount": snapshot.get("next_payment_amount", 0.0),
            "days_past_due":       snapshot.get("days_past_due", 0),
            "status":              snapshot.get("status", "unknown"),
        })

    return {
        "found":       True,
        "customer_id": customer_id,
        "first_name":  user["first_name"],
        "last_name":   user["last_name"],
        "accounts":    accounts,
    }


@mcp.tool()
async def make_payment(
    customer_id: str,
    contract_id: str,
    amount: float,
    payment_method: str = "ach",
) -> dict:
    """
    Submit a payment through the customer portal.

    Validates that the customer owns the contract, logs the activity,
    and publishes a payment.received event to the SmartLedger event bus.

    Args:
        customer_id:    portal user making the payment
        contract_id:    the contract to pay
        amount:         payment amount in USD (must be > 0)
        payment_method: ach | debit_card | credit_card (default: ach)

    Returns: {success, payment_id, contract_id, amount, stream_entry_id}
    """
    user = _PORTAL_USERS.get(customer_id)
    if not user:
        return {"success": False, "reason": f"Customer '{customer_id}' not found in portal"}

    if contract_id not in user.get("contracts", []):
        return {
            "success": False,
            "reason":  f"Contract '{contract_id}' not associated with customer '{customer_id}'",
        }

    if amount <= 0:
        raise ValueError(f"amount must be greater than zero (got {amount})")
    if payment_method not in VALID_PAYMENT_METHODS:
        raise ValueError(f"payment_method must be one of {VALID_PAYMENT_METHODS}")

    payment_id = f"PORTAL-{uuid.uuid4().hex[:8].upper()}"
    correlation_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    if customer_id not in _activity_log:
        _activity_log[customer_id] = []
    _activity_log[customer_id].append({
        "action":      "payment_submitted",
        "contract_id": contract_id,
        "amount":      round(amount, 2),
        "payment_id":  payment_id,
        "timestamp":   now.isoformat(),
    })

    entry_id = await _publish_payment_event(
        contract_id=contract_id,
        payment_id=payment_id,
        amount=amount,
        payment_method=payment_method,
        customer_id=customer_id,
        correlation_id=correlation_id,
    )

    logger.info(
        "portal_payment_submitted",
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
async def get_payment_schedule(contract_id: str, months: int = 3) -> dict:
    """
    Return upcoming payment dates and amounts for a contract.

    Generates a forward-looking schedule based on the current snapshot.
    """
    snapshot = _ACCOUNT_SNAPSHOTS.get(contract_id)
    if not snapshot:
        return {"found": False, "contract_id": contract_id}

    monthly_payment = snapshot.get("monthly_payment", 0.0)
    next_due_str = snapshot.get("next_payment_due")
    if not next_due_str:
        return {
            "found": True, "contract_id": contract_id, "schedule": [],
            "note": "Contract is paid off or has no upcoming payments",
        }

    try:
        next_due = date.fromisoformat(next_due_str)
    except ValueError:
        next_due = date.today()

    schedule = []
    for i in range(months):
        month = (next_due.month - 1 + i) % 12 + 1
        year  = next_due.year + (next_due.month - 1 + i) // 12
        due = next_due.replace(month=month, year=year)
        schedule.append({"due_date": str(due), "amount": round(monthly_payment, 2), "status": "upcoming"})

    return {
        "found":           True,
        "contract_id":     contract_id,
        "current_balance": snapshot.get("current_balance", 0.0),
        "schedule":        schedule,
    }


@mcp.tool()
async def get_portal_activity(customer_id: str, limit: int = 10) -> dict:
    """Return recent portal activity for a customer."""
    if customer_id not in _PORTAL_USERS:
        return {"found": False, "customer_id": customer_id}
    activity = _activity_log.get(customer_id, [])
    sorted_activity = sorted(activity, key=lambda a: a.get("timestamp", ""), reverse=True)
    return {"found": True, "customer_id": customer_id, "activity": sorted_activity[:limit]}


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "customer_portal"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8017)
