"""
IVR (Interactive Voice Response) Simulated MCP Server

Simulates the telephone payment system for customers calling in.
Customers authenticate with their phone number and contract ID, then submit payments.

Tools:
  - get_account_info(contract_id, phone_last4) → balance and next payment (for IVR prompt)
  - capture_payment(contract_id, amount, phone_last4, payment_method?) → phone payment
  - get_call_history(contract_id, limit=5) → recent IVR call records

Payments are published as payment.received events with source_system=ivr.
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any

import redis.asyncio as aioredis
from mcp.server.fastmcp import FastMCP

from shared.config import get_settings
from shared.logging import configure_logging, get_logger
from shared.models.common import EventType, SourceSystem

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("ivr-sim", settings.log_level)
logger = get_logger(__name__)

STREAM_KEY = "smartledger:events"
SCHEMA_VERSION = "1.0"

# IVR only accepts bank-account-based payments (no cards)
VALID_PAYMENT_METHODS = ("ach", "check")

# ─── Module-level state ───────────────────────────────────────────────────────

_redis: aioredis.Redis | None = None
_call_history: dict[str, list[dict[str, Any]]] = {}

# ─── Seed data ────────────────────────────────────────────────────────────────

# Contracts accessible via IVR (auth: last 4 digits of registered phone)
_IVR_AUTH: dict[str, str] = {
    "ORC-2024-001": "5678",   # James Carter's registered phone ending in 5678
    "ORC-2024-002": "9012",   # Maria Gonzalez's registered phone ending in 9012
}

_ACCOUNT_SNAPSHOTS: dict[str, dict[str, Any]] = {
    "ORC-2024-001": {
        "current_balance":      26980.50,
        "next_payment_due":     "2026-04-01",
        "next_payment_amount":  487.50,
        "days_past_due":        0,
        "status":               "active",
    },
    "ORC-2024-002": {
        "current_balance":      18200.00,
        "next_payment_due":     "2026-04-15",
        "next_payment_amount":  349.00,
        "days_past_due":        0,
        "status":               "active",
    },
}


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _redis

    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await _redis.ping()
        logger.info("ivr_redis_connected")
    except Exception as e:
        logger.warning("ivr_redis_unavailable", error=str(e))

    logger.info("ivr_seeded")
    yield

    if _redis:
        await _redis.aclose()
    logger.info("ivr_shutdown")


mcp = FastMCP(
    name="simulated-ivr",
    instructions=(
        "Simulated IVR (Interactive Voice Response) system for phone-based payments. "
        "Customers call in, authenticate with phone last-4, and make ACH/check payments. "
        "Publishes payment.received events to SmartLedger."
    ),
    lifespan=lifespan,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _publish_payment_event(
    contract_id: str,
    payment_id: str,
    amount: float,
    payment_method: str,
    phone_last4: str,
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
        "phone_last4":    phone_last4,
        "channel":        "ivr",
        "source_system":  SourceSystem.IVR,
        "status":         "submitted",
    }

    message: dict[str, str] = {
        "event_id":       str(uuid.uuid4()),
        "event_type":     EventType.PAYMENT_RECEIVED,
        "source_system":  SourceSystem.IVR,
        "contract_id":    contract_id,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "schema_version": SCHEMA_VERSION,
        "payload":        json.dumps(payload),
    }

    try:
        entry_id = await _redis.xadd(STREAM_KEY, message)
        logger.info("ivr_payment_event_published", payment_id=payment_id, contract_id=contract_id)
        return entry_id
    except Exception as e:
        logger.error("ivr_payment_publish_failed", error=str(e))
        return None


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_account_info(contract_id: str, phone_last4: str) -> dict:
    """
    Return account balance and next payment info for an IVR caller.

    Authentication: caller must provide last 4 digits of their registered phone number.
    Used to read account information aloud during an IVR call.

    Returns {found: True, ...balance info} or {found: False, auth_failed: True}.
    """
    registered_last4 = _IVR_AUTH.get(contract_id)
    if not registered_last4:
        return {"found": False, "contract_id": contract_id, "reason": "Contract not found in IVR system"}

    if phone_last4 != registered_last4:
        logger.warning("ivr_auth_failed", contract_id=contract_id)
        return {"found": False, "auth_failed": True, "contract_id": contract_id}

    snapshot = _ACCOUNT_SNAPSHOTS.get(contract_id, {})
    return {
        "found":               True,
        "contract_id":         contract_id,
        "current_balance":     snapshot.get("current_balance", 0.0),
        "next_payment_due":    snapshot.get("next_payment_due"),
        "next_payment_amount": snapshot.get("next_payment_amount", 0.0),
        "days_past_due":       snapshot.get("days_past_due", 0),
        "status":              snapshot.get("status", "unknown"),
    }


@mcp.tool()
async def capture_payment(
    contract_id: str,
    amount: float,
    phone_last4: str,
    payment_method: str = "ach",
) -> dict:
    """
    Capture a payment from an IVR caller.

    Authenticates the caller, validates the payment amount, and publishes
    a payment.received event to the SmartLedger event bus.

    Args:
        contract_id:    the contract to pay
        amount:         payment amount in USD (must be > 0)
        phone_last4:    last 4 digits of caller's registered phone (authentication)
        payment_method: ach | check (default: ach)

    Returns: {success, payment_id, contract_id, amount, confirmation_number}
    """
    registered_last4 = _IVR_AUTH.get(contract_id)
    if not registered_last4:
        return {"success": False, "reason": "Contract not found in IVR system"}

    if phone_last4 != registered_last4:
        logger.warning("ivr_payment_auth_failed", contract_id=contract_id)
        return {"success": False, "reason": "Phone authentication failed"}

    if amount <= 0:
        raise ValueError(f"amount must be greater than zero (got {amount})")
    if payment_method not in VALID_PAYMENT_METHODS:
        raise ValueError(f"IVR payment_method must be one of {VALID_PAYMENT_METHODS}")

    payment_id = f"IVR-{uuid.uuid4().hex[:8].upper()}"
    correlation_id = str(uuid.uuid4())
    confirmation = f"CONF-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(timezone.utc)

    # Record call
    if contract_id not in _call_history:
        _call_history[contract_id] = []
    _call_history[contract_id].append({
        "call_id":       f"CALL-{uuid.uuid4().hex[:6].upper()}",
        "payment_id":    payment_id,
        "amount":        round(amount, 2),
        "payment_method": payment_method,
        "confirmation":  confirmation,
        "timestamp":     now.isoformat(),
    })

    entry_id = await _publish_payment_event(
        contract_id=contract_id,
        payment_id=payment_id,
        amount=amount,
        payment_method=payment_method,
        phone_last4=phone_last4,
        correlation_id=correlation_id,
    )

    logger.info(
        "ivr_payment_captured",
        contract_id=contract_id,
        payment_id=payment_id,
        amount=amount,
    )

    return {
        "success":            True,
        "payment_id":         payment_id,
        "contract_id":        contract_id,
        "amount":             round(amount, 2),
        "payment_method":     payment_method,
        "confirmation_number": confirmation,
        "stream_entry_id":    entry_id,
    }


@mcp.tool()
async def get_call_history(contract_id: str, limit: int = 5) -> dict:
    """Return recent IVR call records for a contract."""
    calls = _call_history.get(contract_id, [])
    sorted_calls = sorted(calls, key=lambda c: c.get("timestamp", ""), reverse=True)
    return {
        "contract_id": contract_id,
        "calls":       sorted_calls[:limit],
        "total_count": len(calls),
    }


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "ivr"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8019)
