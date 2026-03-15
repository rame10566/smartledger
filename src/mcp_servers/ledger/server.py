"""
Immutable Ledger MCP Server

Wraps Hyperledger Fabric + Chaincode. Phase 0: write guard active (PostgreSQL only).

Write Guard semantics:
  WRITE_GUARD=true  (Phase 0): writes to PostgreSQL only, skips Fabric
  WRITE_GUARD=false (Phase 1+): writes to Fabric AND PostgreSQL

Every write requires a valid, unused proof token from the Validation Engine.

Ledger Tools:
  - write_record(record, proof_token)      → write validated record
  - query_records(contract_id, record_type?) → query ledger records
  - get_contract_lifecycle(contract_id)     → full state + history
  - get_audit_trail(contract_id)            → all audit entries
  - get_state(contract_id)                  → current contract state

State / Chaincode Tools:
  - execute_state_transition(contract_id, new_state, trigger_event_id) → update state
  - calculate_late_fee(contract_id, days_past_due)   → Phase 0: formula-based
  - check_title_release(contract_id)                 → eligibility check
  - get_governance_rules()                            → Phase 0: static rules
"""

import hashlib
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg
import jwt
from mcp.server.fastmcp import FastMCP

from shared.config import get_settings
from shared.logging import configure_logging, get_logger

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("ledger", settings.log_level)
logger = get_logger(__name__)

# ─── Module-level state ───────────────────────────────────────────────────────

_pool: asyncpg.Pool | None = None


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _pool
    try:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
        logger.info(
            "ledger_db_connected",
            write_guard=settings.write_guard,
            phase=settings.phase,
        )
    except Exception as e:
        logger.error("ledger_db_connection_failed", error=str(e))

    try:
        yield
    finally:
        if _pool:
            await _pool.close()
        logger.info("ledger_shutdown")


mcp = FastMCP(
    name="smartledger-ledger",
    instructions=(
        "Immutable Ledger for SmartLedger. "
        "Writes validated records to the ledger (PostgreSQL in Phase 0, Fabric in Phase 1+). "
        "Every write requires a valid proof token from the Validation Engine. "
        f"Write guard is {'ON (Phase 0 — PostgreSQL only)' if settings.write_guard else 'OFF (Phase 1+ — Fabric active)'}."
    ),
    lifespan=lifespan,
)


# ─── Proof token verification ─────────────────────────────────────────────────

async def _verify_proof_token(
    proof_token: str,
    expected_contract_id: str,
) -> dict[str, Any]:
    """
    Verify a proof token JWT and check it has not been used before.

    Raises ValueError with a clear message on any failure:
      - Invalid signature
      - Token expired
      - contract_id mismatch
      - Token already used (replay)

    Returns the decoded claims dict on success.
    """
    # 1. Decode and verify signature + expiry
    try:
        claims = jwt.decode(
            proof_token,
            settings.proof_token_secret,
            algorithms=["HS256"],
            options={"require": ["jti", "contract_id", "event_id", "saga_id", "exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise ValueError("Proof token has expired (60s window exceeded)")
    except jwt.InvalidTokenError as e:
        raise ValueError(f"Invalid proof token: {e}")

    # 2. contract_id must match the record being written
    token_contract_id = claims.get("contract_id", "")
    if token_contract_id != expected_contract_id:
        raise ValueError(
            f"Proof token contract_id '{token_contract_id}' does not match "
            f"record contract_id '{expected_contract_id}'"
        )

    # 3. Check jti has not been used before (replay prevention)
    jti = claims["jti"]
    if not _pool:
        raise RuntimeError("Database not available for jti check")

    async with _pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT jti FROM validation.used_proof_tokens WHERE jti = $1",
            jti,
        )
        if existing:
            raise ValueError(f"Proof token jti '{jti}' has already been used (replay attack)")

    return claims


async def _mark_token_used(
    jti: str,
    contract_id: str,
    event_id: str,
    exp: int,
    conn: asyncpg.Connection,
) -> None:
    """Record a proof token jti as used to prevent replay."""
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
    await conn.execute(
        """
        INSERT INTO validation.used_proof_tokens (jti, contract_id, event_id, used_at, expires_at)
        VALUES ($1, $2, $3::uuid, NOW(), $4)
        ON CONFLICT (jti) DO NOTHING
        """,
        jti,
        contract_id,
        event_id,
        expires_at,
    )


async def _write_audit(
    conn: asyncpg.Connection,
    action: str,
    contract_id: str,
    event_id: str | None,
    saga_id: str | None,
    details: dict[str, Any],
) -> None:
    """Write an entry to the audit log."""
    await conn.execute(
        """
        INSERT INTO audit.log (action, actor, contract_id, event_id, saga_id, details, created_at)
        VALUES ($1, 'agent', $2, $3::uuid, $4::uuid, $5::jsonb, NOW())
        """,
        action,
        contract_id,
        event_id,
        saga_id,
        json.dumps(details),
    )


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def write_record(record: dict, proof_token: str) -> dict:
    """
    Write a validated record to the immutable ledger.

    Phase 0 (WRITE_GUARD=true):  writes to PostgreSQL only
    Phase 1+ (WRITE_GUARD=false): writes to Hyperledger Fabric + PostgreSQL

    Requires a valid, unused proof token from the Validation Engine.
    The token must match the contract_id in the record and not be expired or replayed.

    Required record fields:
      - record_id (uuid str)
      - contract_id (str)
      - record_type (str): "origination" | "payment" | "amendment" | "payoff"
      - saga_id (uuid str)
      - proof_token_jti (str): must match the jti in the proof_token JWT

    Returns: {success, record_id, contract_id, fabric_tx_id, write_guard_active}
    """
    if not _pool:
        raise RuntimeError("Ledger database not available")

    contract_id = record.get("contract_id", "")
    record_id = record.get("record_id") or str(uuid4())
    record_type = record.get("record_type", "origination")
    saga_id = record.get("saga_id", "")

    # ── Verify proof token ────────────────────────────────────────────────────
    claims = await _verify_proof_token(proof_token, contract_id)
    jti = claims["jti"]
    event_id = claims["event_id"]

    # ── Compute data hash ─────────────────────────────────────────────────────
    record_payload = {k: v for k, v in record.items() if k != "proof_token_jti"}
    data_hash = hashlib.sha256(
        json.dumps(record_payload, sort_keys=True).encode()
    ).hexdigest()

    # ── Write to PostgreSQL + mark token used (single transaction) ────────────
    fabric_tx_id: str | None = None

    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Mark proof token used first (prevents race conditions)
            await _mark_token_used(jti, contract_id, event_id, claims["exp"], conn)

            # Write to contracts.records
            await conn.execute(
                """
                INSERT INTO contracts.records
                  (record_id, contract_id, record_type, payload, data_hash, proof_token_jti, fabric_tx_id)
                VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6, $7)
                """,
                record_id,
                contract_id,
                record_type,
                json.dumps(record),
                data_hash,
                jti,
                fabric_tx_id,
            )

            # Audit log
            await _write_audit(
                conn,
                action="ledger_written",
                contract_id=contract_id,
                event_id=event_id,
                saga_id=saga_id,
                details={
                    "record_id": record_id,
                    "record_type": record_type,
                    "data_hash": data_hash,
                    "write_guard_active": settings.write_guard,
                    "fabric_tx_id": fabric_tx_id,
                },
            )

    # Phase 1+: submit to Fabric (write_guard=False)
    if not settings.write_guard:
        logger.warning(
            "fabric_write_not_implemented",
            contract_id=contract_id,
            note="Phase 1+ Fabric writes not yet wired — PostgreSQL only for now",
        )
        # TODO Phase 1: submit_to_fabric(record, data_hash) → fabric_tx_id

    logger.info(
        "record_written",
        record_id=record_id,
        contract_id=contract_id,
        record_type=record_type,
        write_guard=settings.write_guard,
    )

    return {
        "success": True,
        "record_id": record_id,
        "contract_id": contract_id,
        "record_type": record_type,
        "data_hash": data_hash,
        "fabric_tx_id": fabric_tx_id,
        "write_guard_active": settings.write_guard,
    }


@mcp.tool()
async def query_records(contract_id: str, record_type: str | None = None) -> list[dict]:
    """Query ledger records for a contract, optionally filtered by record_type."""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        if record_type:
            rows = await conn.fetch(
                """
                SELECT record_id::text, contract_id, record_type,
                       payload::text, data_hash, proof_token_jti,
                       fabric_tx_id, created_at::text
                FROM contracts.records
                WHERE contract_id = $1 AND record_type = $2
                ORDER BY created_at DESC
                """,
                contract_id,
                record_type,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT record_id::text, contract_id, record_type,
                       payload::text, data_hash, proof_token_jti,
                       fabric_tx_id, created_at::text
                FROM contracts.records
                WHERE contract_id = $1
                ORDER BY created_at DESC
                """,
                contract_id,
            )

    result = []
    for row in rows:
        r = dict(row)
        if r.get("payload"):
            try:
                r["payload"] = json.loads(r["payload"])
            except Exception:
                pass
        result.append(r)
    return result


@mcp.tool()
async def get_contract_lifecycle(contract_id: str) -> dict:
    """
    Return the full lifecycle view of a contract:
    current state, state history, payment summary.
    """
    if not _pool:
        raise RuntimeError("Database not available")

    async with _pool.acquire() as conn:
        # Current state
        state_row = await conn.fetchrow(
            """
            SELECT current_state, previous_state,
                   state_changed_at::text, days_past_due
            FROM contracts.state
            WHERE contract_id = $1
            """,
            contract_id,
        )

        # All records (state transitions build history)
        records = await conn.fetch(
            """
            SELECT record_type, payload::text, created_at::text
            FROM contracts.records
            WHERE contract_id = $1
            ORDER BY created_at ASC
            """,
            contract_id,
        )

    if not state_row and not records:
        raise ValueError(f"No ledger data found for contract '{contract_id}'")

    # Build payment summary from records
    total_payments_made = 0
    total_amount_paid = 0.0
    state_history: list[dict] = []

    for rec in records:
        rec_dict = dict(rec)
        payload_str = rec_dict.get("payload", "{}")
        try:
            payload = json.loads(payload_str)
        except Exception:
            payload = {}

        if rec_dict["record_type"] == "payment_applied":
            total_payments_made += 1
            total_amount_paid += float(payload.get("amount", 0))

        if rec_dict["record_type"] == "state_transition":
            state_history.append({
                "state": payload.get("new_state"),
                "previous_state": payload.get("previous_state"),
                "transitioned_at": rec_dict["created_at"],
                "trigger_event_id": payload.get("trigger_event_id"),
            })

    return {
        "contract_id": contract_id,
        "current_state": state_row["current_state"] if state_row else "unknown",
        "previous_state": state_row["previous_state"] if state_row else None,
        "state_changed_at": state_row["state_changed_at"] if state_row else None,
        "days_past_due": state_row["days_past_due"] if state_row else 0,
        "state_history": state_history,
        "total_payments_made": total_payments_made,
        "total_amount_paid": total_amount_paid,
        "record_count": len(records),
    }


@mcp.tool()
async def get_audit_trail(contract_id: str) -> list[dict]:
    """Return the full audit trail for a contract (all agent actions, writes, overrides)."""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT action, actor, contract_id,
                   event_id::text, saga_id::text,
                   details::text, created_at::text
            FROM audit.log
            WHERE contract_id = $1
            ORDER BY created_at DESC
            """,
            contract_id,
        )

    result = []
    for row in rows:
        r = dict(row)
        if r.get("details"):
            try:
                r["details"] = json.loads(r["details"])
            except Exception:
                pass
        result.append(r)
    return result


@mcp.tool()
async def get_state(contract_id: str) -> dict:
    """Return the current state of a contract."""
    if not _pool:
        raise RuntimeError("Database not available")

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT current_state, previous_state,
                   state_changed_at::text, days_past_due, updated_at::text
            FROM contracts.state
            WHERE contract_id = $1
            """,
            contract_id,
        )

    if not row:
        raise ValueError(f"No state found for contract '{contract_id}'")

    return dict(row)


@mcp.tool()
async def execute_state_transition(
    contract_id: str,
    new_state: str,
    trigger_event_id: str,
    saga_id: str | None = None,
) -> dict:
    """
    Execute a state transition for a contract.

    Valid states: originated → active → delinquent → paid_off
                            ↘ charged_off, in_repossession, title_released

    Phase 0: updates PostgreSQL only.
    Phase 1+: calls Fabric chaincode execute_state_transition.

    Returns: {success, contract_id, previous_state, new_state, transitioned_at}
    """
    valid_states = {
        "originated", "active", "delinquent",
        "paid_off", "charged_off", "in_repossession", "title_released",
    }
    if new_state not in valid_states:
        raise ValueError(
            f"Invalid state '{new_state}'. Valid states: {sorted(valid_states)}"
        )

    if not _pool:
        raise RuntimeError("Database not available")

    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Get current state (if any)
            current_row = await conn.fetchrow(
                "SELECT current_state FROM contracts.state WHERE contract_id = $1",
                contract_id,
            )
            previous_state = current_row["current_state"] if current_row else None

            # Upsert state
            await conn.execute(
                """
                INSERT INTO contracts.state
                  (contract_id, current_state, previous_state, state_changed_at, updated_at)
                VALUES ($1, $2, $3, NOW(), NOW())
                ON CONFLICT (contract_id) DO UPDATE
                  SET current_state = EXCLUDED.current_state,
                      previous_state = contracts.state.current_state,
                      state_changed_at = NOW(),
                      updated_at = NOW()
                """,
                contract_id,
                new_state,
                previous_state,
            )

            # Write state_transition record to ledger
            transition_payload = {
                "contract_id": contract_id,
                "previous_state": previous_state,
                "new_state": new_state,
                "trigger_event_id": trigger_event_id,
                "transitioned_at": datetime.now(timezone.utc).isoformat(),
            }
            data_hash = hashlib.sha256(
                json.dumps(transition_payload, sort_keys=True).encode()
            ).hexdigest()

            await conn.execute(
                """
                INSERT INTO contracts.records
                  (record_id, contract_id, record_type, payload, data_hash)
                VALUES ($1::uuid, $2, 'state_transition', $3::jsonb, $4)
                """,
                str(uuid4()),
                contract_id,
                json.dumps(transition_payload),
                data_hash,
            )

            # Audit
            await _write_audit(
                conn,
                action="state_transitioned",
                contract_id=contract_id,
                event_id=trigger_event_id,
                saga_id=saga_id,
                details={
                    "previous_state": previous_state,
                    "new_state": new_state,
                    "write_guard_active": settings.write_guard,
                },
            )

    transitioned_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "state_transitioned",
        contract_id=contract_id,
        previous_state=previous_state,
        new_state=new_state,
    )

    return {
        "success": True,
        "contract_id": contract_id,
        "previous_state": previous_state,
        "new_state": new_state,
        "transitioned_at": transitioned_at,
        "write_guard_active": settings.write_guard,
    }


@mcp.tool()
async def calculate_late_fee(contract_id: str, days_past_due: int) -> dict:
    """
    Calculate the late fee for a delinquent contract.

    Phase 0: formula-based (no chaincode).
    Phase 1+: delegates to Fabric chaincode calculate_late_fee.

    Standard schedule:
      1-14 days:  $25 flat
      15-29 days: $50 flat
      30+ days:   5% of monthly payment (min $50)
    """
    if days_past_due <= 0:
        return {
            "contract_id": contract_id,
            "days_past_due": days_past_due,
            "late_fee": 0.0,
            "fee_tier": "none",
        }

    # Get monthly payment from ledger records
    monthly_payment = 0.0
    if _pool:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT payload::text FROM contracts.records
                WHERE contract_id = $1 AND record_type = 'origination'
                ORDER BY created_at DESC LIMIT 1
                """,
                contract_id,
            )
        if row:
            try:
                payload = json.loads(row["payload"])
                monthly_payment = float(payload.get("monthly_payment", 0))
            except Exception:
                pass

    if days_past_due <= 14:
        fee = 25.00
        tier = "1-14_days"
    elif days_past_due <= 29:
        fee = 50.00
        tier = "15-29_days"
    else:
        fee = max(50.00, monthly_payment * 0.05)
        tier = "30+_days"

    return {
        "contract_id": contract_id,
        "days_past_due": days_past_due,
        "monthly_payment": monthly_payment,
        "late_fee": round(fee, 2),
        "fee_tier": tier,
        "write_guard_active": settings.write_guard,
    }


@mcp.tool()
async def check_title_release(contract_id: str) -> dict:
    """
    Check if a contract is eligible for title release (payoff complete).

    Phase 0: checks PostgreSQL for paid_off state + zero outstanding balance.
    Phase 1+: delegates to Fabric chaincode check_title_release.
    """
    if not _pool:
        raise RuntimeError("Database not available")

    async with _pool.acquire() as conn:
        state_row = await conn.fetchrow(
            "SELECT current_state, days_past_due FROM contracts.state WHERE contract_id = $1",
            contract_id,
        )

    if not state_row:
        return {
            "contract_id": contract_id,
            "eligible": False,
            "reason": "Contract not found in ledger state",
        }

    is_paid_off = state_row["current_state"] == "paid_off"
    eligible = is_paid_off and state_row["days_past_due"] == 0

    return {
        "contract_id": contract_id,
        "eligible": eligible,
        "current_state": state_row["current_state"],
        "days_past_due": state_row["days_past_due"],
        "reason": "Contract is paid off with no outstanding balance" if eligible
                  else f"Contract state is '{state_row['current_state']}' — must be 'paid_off'",
    }


@mcp.tool()
async def get_governance_rules() -> dict:
    """
    Return governance rules.
    Phase 0: returns static rule set.
    Phase 1+: queries Fabric chaincode get_governance_rules.
    """
    return {
        "phase": settings.phase,
        "write_guard_active": settings.write_guard,
        "rules": {
            "proof_token_required": True,
            "proof_token_expiry_seconds": settings.proof_token_expiry_seconds,
            "proof_token_algorithm": "HS256",
            "single_use_tokens": True,
            "pii_on_chain": False,
            "valid_record_types": [
                "origination", "payment_applied", "fee_assessed",
                "balance_adjustment", "payoff", "late_fee",
                "insurance_lapse_noted", "state_transition",
            ],
            "valid_states": [
                "originated", "active", "delinquent",
                "paid_off", "charged_off", "in_repossession", "title_released",
            ],
            "late_fee_schedule": {
                "1_to_14_days": 25.00,
                "15_to_29_days": 50.00,
                "30_plus_days": "max(50.00, monthly_payment * 0.05)",
            },
        },
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8002)
