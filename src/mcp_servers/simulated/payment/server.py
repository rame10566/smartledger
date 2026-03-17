"""
Payment Simulated MCP Server

Simulates the payment processing system.
Stores payments in-memory and publishes payment.received events to Redis Streams.

Tools:
  - submit_payment(contract_id, amount, payment_method, reference?)
      → validates, stores payment, publishes payment.received
  - get_payment(payment_id) → retrieve a specific payment record
  - get_payments_for_contract(contract_id, limit=10) → list payments for a contract

Event published to Redis Stream 'smartledger:events':
  event_type: payment.received
  source_system: payment
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
configure_logging("payment-sim", settings.log_level)
logger = get_logger(__name__)

STREAM_KEY = "smartledger:events"
SCHEMA_VERSION = "1.0"

VALID_PAYMENT_METHODS = ("ach", "check", "wire", "credit_card", "debit_card", "cash")

# ─── Module-level state ───────────────────────────────────────────────────────

_redis: aioredis.Redis | None = None
_payments: dict[str, dict[str, Any]] = {}
_payments_by_contract: dict[str, list[str]] = {}
_seq: int = 1

# ─── Seed data ────────────────────────────────────────────────────────────────

_SEED_PAYMENTS: list[dict[str, Any]] = [
    {
        "payment_id": "PAY-001-001",
        "contract_id": "ORC-2024-001",
        "amount": 487.50,
        "payment_method": "ach",
        "reference": "ACH-20250901-001",
        "payment_date": "2025-09-01",
        "status": "processed",
        "source_system": "payment",
    },
    {
        "payment_id": "PAY-001-002",
        "contract_id": "ORC-2024-001",
        "amount": 487.50,
        "payment_method": "ach",
        "reference": "ACH-20251001-001",
        "payment_date": "2025-10-01",
        "status": "processed",
        "source_system": "payment",
    },
    {
        "payment_id": "PAY-002-001",
        "contract_id": "ORC-2024-002",
        "amount": 349.00,
        "payment_method": "ach",
        "reference": "ACH-20250801-002",
        "payment_date": "2025-08-15",
        "status": "processed",
        "source_system": "payment",
    },
]


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _redis, _seq

    # Seed payments
    for p in _SEED_PAYMENTS:
        _payments[p["payment_id"]] = p
        cid = p["contract_id"]
        if cid not in _payments_by_contract:
            _payments_by_contract[cid] = []
        _payments_by_contract[cid].append(p["payment_id"])
    _seq = len(_SEED_PAYMENTS) + 1

    # Redis connection
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await _redis.ping()
        logger.info("payment_sim_redis_connected")
    except Exception as e:
        logger.warning("payment_sim_redis_unavailable", error=str(e))

    logger.info("payment_sim_seeded", payment_count=len(_SEED_PAYMENTS))
    yield

    if _redis:
        await _redis.aclose()
    logger.info("payment_sim_shutdown")


mcp = FastMCP(
    name="simulated-payment",
    instructions=(
        "Simulated Payment processor. Accepts payment submissions for auto loan/lease contracts, "
        "stores them in-memory, and publishes payment.received events to the SmartLedger event bus."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _next_payment_id() -> str:
    global _seq
    pid = f"PAY-{_seq:06d}"
    _seq += 1
    return pid


async def _publish_event(
    event_type: str,
    contract_id: str,
    payload: dict[str, Any],
    source_system: str = SourceSystem.PAYMENT,
    correlation_id: str | None = None,
) -> str | None:
    """Publish an event to the Redis Stream. Returns stream entry ID or None."""
    if not _redis:
        logger.warning("redis_not_available_event_dropped", event_type=event_type)
        return None

    corr_id = correlation_id or str(uuid.uuid4())
    message: dict[str, str] = {
        "event_id":       str(uuid.uuid4()),
        "event_type":     event_type,
        "source_system":  source_system,
        "contract_id":    contract_id,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "correlation_id": corr_id,
        "schema_version": SCHEMA_VERSION,
        "payload":        json.dumps(payload),
    }

    try:
        entry_id = await _redis.xadd(STREAM_KEY, message)
        logger.info(
            "payment_event_published",
            event_type=event_type,
            contract_id=contract_id,
            stream_entry=entry_id,
        )
        return entry_id
    except Exception as e:
        logger.error("payment_event_publish_failed", event_type=event_type, error=str(e))
        return None


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def submit_payment(
    contract_id: str,
    amount: float,
    payment_method: str,
    reference: str = "",
    correlation_id: str = "",
) -> dict:
    """
    Submit a payment for a contract.

    Validates the input, records the payment, and publishes a payment.received
    event to the SmartLedger event bus for agent processing.

    Args:
        contract_id:    the contract receiving the payment
        amount:         payment amount in USD (must be > 0)
        payment_method: one of: ach, check, wire, credit_card, debit_card, cash
        reference:      optional external reference (check number, ACH trace, etc.)
        correlation_id: optional, propagate existing correlation for tracing

    Returns: {success, payment_id, contract_id, amount, stream_entry_id}
    """
    if not contract_id or not contract_id.strip():
        raise ValueError("contract_id is required")
    if amount <= 0:
        raise ValueError(f"amount must be greater than zero (got {amount})")
    if payment_method not in VALID_PAYMENT_METHODS:
        raise ValueError(
            f"Invalid payment_method '{payment_method}': must be one of {VALID_PAYMENT_METHODS}"
        )

    payment_id = _next_payment_id()
    now = datetime.now(timezone.utc)

    payment: dict[str, Any] = {
        "payment_id":      payment_id,
        "contract_id":     contract_id,
        "amount":          round(amount, 2),
        "payment_method":  payment_method,
        "reference":       reference or "",
        "payment_date":    str(date.today()),
        "status":          "pending",
        "source_system":   SourceSystem.PAYMENT,
        "created_at":      now.isoformat(),
    }

    _payments[payment_id] = payment
    if contract_id not in _payments_by_contract:
        _payments_by_contract[contract_id] = []
    _payments_by_contract[contract_id].append(payment_id)

    # Publish payment.received event to Redis Streams
    corr_id = correlation_id or str(uuid.uuid4())
    entry_id = await _publish_event(
        EventType.PAYMENT_RECEIVED,
        contract_id,
        payload=payment,
        correlation_id=corr_id,
    )

    # Mark as submitted (agent will update status after processing)
    _payments[payment_id]["status"] = "submitted"

    logger.info(
        "payment_submitted",
        payment_id=payment_id,
        contract_id=contract_id,
        amount=amount,
        payment_method=payment_method,
    )

    return {
        "success":        True,
        "payment_id":     payment_id,
        "contract_id":    contract_id,
        "amount":         round(amount, 2),
        "payment_method": payment_method,
        "stream_entry_id": entry_id,
        "correlation_id": corr_id,
    }


@mcp.tool()
async def get_payment(payment_id: str) -> dict:
    """
    Retrieve a payment record by payment_id.

    Returns {found: True, ...payment fields} or {found: False, payment_id}.
    """
    payment = _payments.get(payment_id)
    if payment is None:
        return {"found": False, "payment_id": payment_id}
    return {"found": True, **payment}


@mcp.tool()
async def get_payments_for_contract(contract_id: str, limit: int = 10) -> dict:
    """
    Return recent payments for a contract (most recent first).

    Returns {found: True, payments: [...]} or {found: False} if no payments exist.
    """
    payment_ids = _payments_by_contract.get(contract_id, [])
    if not payment_ids:
        return {"found": False, "contract_id": contract_id, "payments": []}

    payments = [_payments[pid] for pid in payment_ids if pid in _payments]
    payments_sorted = sorted(payments, key=lambda p: p.get("payment_date", ""), reverse=True)

    return {
        "found":        True,
        "contract_id":  contract_id,
        "payments":     payments_sorted[:limit],
        "total_count":  len(payments),
    }


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "payment"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8014)
