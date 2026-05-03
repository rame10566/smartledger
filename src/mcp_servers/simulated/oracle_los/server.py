"""
Oracle LOS Simulated MCP Server

Simulates the Oracle Loan Origination System.
Stores contracts in-memory and publishes events to Redis Streams.

Tools:
  - originate_contract(contract_data) → creates a contract, publishes contract.originated event
  - get_contract(contract_id)         → returns contract from Oracle LOS
  - get_contracts(filters?)           → list contracts
  - amend_contract(contract_id, changes) → amends a contract in-memory

Event published to Redis Stream 'smartledger:events':
  event_type: contract.originated
  source_system: oracle_los
"""

import json
import re
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
configure_logging("oracle-los", settings.log_level)
logger = get_logger(__name__)

STREAM_KEY = "smartledger:events"
SCHEMA_VERSION = "1.0"
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

# ─── Module-level state ───────────────────────────────────────────────────────

_redis: aioredis.Redis | None = None
_contracts: dict[str, dict[str, Any]] = {}
_seq: int = 1  # incremented per origination; combined with random suffix to avoid restart collisions

# ─── Seed data ────────────────────────────────────────────────────────────────

_SEED_CONTRACTS: list[dict[str, Any]] = [
    {
        "contract_id": "ORC-2024-001",
        "los_system": "oracle_los",
        "contract_type": "loan",
        "origination_date": "2024-03-01",
        "maturity_date": "2030-03-01",
        "state": "active",
        "customer": {
            "customer_id": "CUST-001",
            "first_name": "James",
            "last_name": "Carter",
            "credit_score": 725,
            "credit_tier": "prime",
            "monthly_income": 6500.00,
            "existing_monthly_debt": 1200.00,
        },
        "vehicle": {
            "vin": "1HGBH41JXMN109186",
            "make": "Toyota",
            "model": "Camry",
            "year": 2024,
            "color": "Silver",
            "condition": "new",
            "mileage": 12,
        },
        "financial_terms": {
            "amount_financed": 28500.00,
            "vehicle_value": 31500.00,
            "term_months": 72,
            "interest_rate": 6.99,
            "monthly_payment": 487.50,
            "down_payment": 3000.00,
        },
        "dealer_id": "DLR-0042",
    },
    {
        "contract_id": "ORC-2024-002",
        "los_system": "oracle_los",
        "contract_type": "lease",
        "origination_date": "2024-06-15",
        "maturity_date": "2027-06-15",
        "state": "active",
        "customer": {
            "customer_id": "CUST-002",
            "first_name": "Maria",
            "last_name": "Gonzalez",
            "credit_score": 780,
            "credit_tier": "super_prime",
            "monthly_income": 8200.00,
            "existing_monthly_debt": 800.00,
        },
        "vehicle": {
            "vin": "2T1BURHE0JC990856",
            "make": "Honda",
            "model": "Civic",
            "year": 2024,
            "color": "Blue",
            "condition": "new",
            "mileage": 5,
        },
        "financial_terms": {
            "amount_financed": 21000.00,
            "vehicle_value": 26000.00,
            "term_months": 36,
            "interest_rate": 4.99,
            "monthly_payment": 349.00,
            "down_payment": 2500.00,
            "residual_value": 13000.00,
        },
        "dealer_id": "DLR-0017",
    },
    {
        "contract_id": "ORC-2024-003",
        "los_system": "oracle_los",
        "contract_type": "loan",
        "origination_date": "2024-09-10",
        "maturity_date": "2030-09-10",
        "state": "originated",
        "customer": {
            "customer_id": "CUST-003",
            "first_name": "Robert",
            "last_name": "Kim",
            "credit_score": 640,
            "credit_tier": "subprime",
            "monthly_income": 5000.00,
            "existing_monthly_debt": 1800.00,
        },
        "vehicle": {
            "vin": "3VWFE21C04M000001",
            "make": "Ford",
            "model": "F-150",
            "year": 2024,
            "color": "White",
            "condition": "new",
            "mileage": 8,
        },
        "financial_terms": {
            "amount_financed": 45000.00,
            "vehicle_value": 50000.00,
            "term_months": 72,
            "interest_rate": 8.49,
            "monthly_payment": 799.50,
            "down_payment": 5000.00,
        },
        "dealer_id": "DLR-0091",
    },
]


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _redis, _seq

    for c in _SEED_CONTRACTS:
        _contracts[c["contract_id"]] = c
    _seq = len(_SEED_CONTRACTS) + 1
    logger.info("oracle_los_seeded", contract_count=len(_SEED_CONTRACTS))

    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await _redis.ping()
        logger.info("oracle_los_redis_connected", url=settings.redis_url)
    except Exception as e:
        logger.warning("oracle_los_redis_unavailable", error=str(e))

    try:
        yield
    finally:
        if _redis:
            await _redis.aclose()
        logger.info("oracle_los_shutdown")


mcp = FastMCP(
    name="simulated-oracle-los",
    instructions=(
        "Simulated Oracle Loan Origination System (LOS). "
        "Creates contracts and publishes events to Redis Streams. "
        "Use originate_contract to create new contracts, get_contract to fetch details."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _next_contract_id() -> str:
    """Generate a unique contract ID using random hex to avoid restart collisions."""
    suffix = uuid.uuid4().hex[:6].upper()
    return f"ORC-{date.today().year}-{suffix}"


async def _publish_event(
    event_type: str,
    contract_id: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> str | None:
    """Publish an EventEnvelope to the Redis Stream. Returns stream entry ID or None."""
    if not _redis:
        logger.warning("redis_not_available_event_dropped", event_type=event_type)
        return None

    corr_id = correlation_id or str(uuid.uuid4())
    message: dict[str, str] = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "source_system": SourceSystem.ORACLE_LOS,
        "contract_id": contract_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": corr_id,
        "schema_version": SCHEMA_VERSION,
        "payload": json.dumps(payload),
    }

    try:
        entry_id = await _redis.xadd(STREAM_KEY, message)
        logger.info(
            "event_published",
            event_type=event_type,
            contract_id=contract_id,
            stream_entry=entry_id,
        )
        return entry_id
    except Exception as e:
        logger.error("event_publish_failed", event_type=event_type, error=str(e))
        return None


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def originate_contract(contract_data: dict) -> dict:
    """
    Originate a new contract in Oracle LOS and publish a contract.originated event.

    Required keys in contract_data:
      - contract_type: "loan" | "lease"
      - customer: {customer_id, first_name, last_name, credit_score, credit_tier}
      - vehicle: {vin, make, model, year}  — VIN: 17 chars [A-HJ-NPR-Z0-9]
      - financial_terms: {amount_financed, term_months, interest_rate, monthly_payment}
      - dealer_id: str

    Optional: maturity_date (ISO date), correlation_id (propagate existing)

    Returns: {success, contract_id, stream_entry_id, correlation_id, contract}
    """
    required = ["contract_type", "customer", "vehicle", "financial_terms", "dealer_id"]
    missing = [f for f in required if f not in contract_data]
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    contract_type = contract_data["contract_type"]
    if contract_type not in ("loan", "lease"):
        raise ValueError(f"Invalid contract_type '{contract_type}': must be 'loan' or 'lease'")

    vin = contract_data.get("vehicle", {}).get("vin", "")
    if not _VIN_RE.match(vin):
        raise ValueError(
            f"Invalid VIN '{vin}': must be exactly 17 characters [A-HJ-NPR-Z0-9] (no I, O, or Q)"
        )

    contract_id = _next_contract_id()
    now = datetime.now(timezone.utc)
    correlation_id = contract_data.get("correlation_id") or str(uuid.uuid4())

    contract: dict[str, Any] = {
        "contract_id": contract_id,
        "los_system": "oracle_los",
        "contract_type": contract_type,
        "origination_date": str(date.today()),
        "maturity_date": contract_data.get("maturity_date"),
        "state": "originated",
        "customer": contract_data["customer"],
        "vehicle": contract_data["vehicle"],
        "financial_terms": contract_data["financial_terms"],
        "dealer_id": contract_data["dealer_id"],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    _contracts[contract_id] = contract

    # Sync LLAS account through the Integration System BEFORE publishing
    # contract.originated. SmartLedger validates the sync, writes a customer_update
    # ledger record, and creates the LLAS account. Per-contract Redis lock
    # serializes the two events so the account exists by the time origination
    # validation runs (RULE-XSYS-LLAS-PRESENT).
    sync_payload: dict[str, Any] = {
        "contract_type":     contract_type,
        "amount_financed":   contract_data["financial_terms"].get("amount_financed"),
        "monthly_payment":   contract_data["financial_terms"].get("monthly_payment"),
        "term_months":       contract_data["financial_terms"].get("term_months"),
        "first_payment_date": contract_data.get("first_payment_date"),
        "origination_date":  contract["origination_date"],
        "dealer_id":         contract_data["dealer_id"],
        "los_updated_at":    contract["updated_at"],
    }
    sync_payload = {k: v for k, v in sync_payload.items() if v is not None}
    try:
        await call_mcp_tool(
            url=settings.mcp_integration_url,
            tool_name="submit_llas_sync",
            arguments={
                "contract_id":   contract_id,
                "source_system": SourceSystem.ORACLE_LOS,
                "sync_payload":  sync_payload,
            },
        )
    except MCPCallError as e:
        # Don't block origination on sync failure; origination validation will
        # detect the missing LLAS account and quarantine appropriately.
        logger.warning(
            "oracle_los_initial_llas_sync_failed",
            contract_id=contract_id,
            error=str(e),
        )

    entry_id = await _publish_event(
        EventType.CONTRACT_ORIGINATED,
        contract_id,
        payload=contract,
        correlation_id=correlation_id,
    )

    logger.info(
        "contract_originated",
        contract_id=contract_id,
        contract_type=contract_type,
        vin=vin,
        dealer_id=contract_data["dealer_id"],
    )

    return {
        "success": True,
        "contract_id": contract_id,
        "stream_entry_id": entry_id,
        "correlation_id": correlation_id,
        "contract": contract,
    }


@mcp.tool()
async def get_contract(contract_id: str) -> dict:
    """Return full contract details from Oracle LOS. Raises if not found."""
    contract = _contracts.get(contract_id)
    if not contract:
        raise ValueError(f"Contract '{contract_id}' not found in Oracle LOS")
    return contract


@mcp.tool()
async def get_contracts(filters: dict | None = None) -> list[dict]:
    """
    Return all contracts, optionally filtered.
    Supported filter keys: state, contract_type, dealer_id
    """
    contracts = list(_contracts.values())
    if filters:
        if "state" in filters:
            contracts = [c for c in contracts if c.get("state") == filters["state"]]
        if "contract_type" in filters:
            contracts = [c for c in contracts if c.get("contract_type") == filters["contract_type"]]
        if "dealer_id" in filters:
            contracts = [c for c in contracts if c.get("dealer_id") == filters["dealer_id"]]
    return contracts


@mcp.tool()
async def amend_contract(contract_id: str, changes: dict) -> dict:
    """
    Apply amendments to an existing Oracle LOS contract.
    Deep-merges dict fields; replaces scalar fields directly.
    Returns: {success, contract_id, contract}
    """
    contract = _contracts.get(contract_id)
    if not contract:
        raise ValueError(f"Contract '{contract_id}' not found in Oracle LOS")

    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(contract.get(key), dict):
            contract[key].update(value)
        else:
            contract[key] = value

    contract["updated_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("contract_amended", contract_id=contract_id, changed_fields=list(changes.keys()))

    return {"success": True, "contract_id": contract_id, "contract": contract}


@mcp.tool()
async def sync_to_llas(contract_id: str) -> dict:
    """
    Sync current Oracle LOS contract data to LLAS via the Integration System.

    Called when Oracle LOS has updated contract master data and needs to push
    it to LLAS. SmartLedger validates for staleness — if this LOS data is older
    than the last validated ledger record, it will be quarantined as STALE_LOS_SYNC.

    Args:
        contract_id: the contract to sync

    Returns: {success, integration_ref, status}
    """
    contract = _contracts.get(contract_id)
    if not contract:
        return {"success": False, "reason": f"Contract '{contract_id}' not found in Oracle LOS"}

    # Build the sync payload from current LOS contract data
    sync_payload = {
        "address":    contract.get("customer", {}).get("address"),
        "contact": {
            "first_name": contract.get("customer", {}).get("first_name"),
            "last_name":  contract.get("customer", {}).get("last_name"),
            "phone":      contract.get("customer", {}).get("phone"),
            "email":      contract.get("customer", {}).get("email"),
        },
        "los_updated_at": contract.get("updated_at", contract.get("origination_date")),
    }
    # Remove None values
    sync_payload = {k: v for k, v in sync_payload.items() if v is not None}

    try:
        result = await call_mcp_tool(
            url=settings.mcp_integration_url,
            tool_name="submit_llas_sync",
            arguments={
                "contract_id":   contract_id,
                "source_system": SourceSystem.ORACLE_LOS,
                "sync_payload":  sync_payload,
            },
        )
    except MCPCallError as e:
        logger.error("oracle_los_sync_failed", contract_id=contract_id, error=str(e))
        return {"success": False, "reason": f"Integration System unavailable: {e}"}

    integration_ref = result.get("integration_ref", "") if isinstance(result, dict) else ""
    logger.info("oracle_los_sync_submitted", contract_id=contract_id, integration_ref=integration_ref)
    return {
        "success":         bool(integration_ref),
        "integration_ref": integration_ref,
        "status":          "pending_validation",
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8010)
