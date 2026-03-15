"""
Validation Engine MCP Server

Core gatekeeper: validates every event before it can be written to the ledger.
Issues single-use signed JWT proof tokens on successful validation.
Quarantines invalid events for human review.

Tools:
  - validate_event(request)              → ValidationResult + proof_token (if valid)
  - get_quarantined(contract_id?)        → list quarantined events pending review
  - approve_override(event_id, reason, reviewer) → approve quarantine, publish retry event
  - get_validation_rules(rule_type?)     → list active rules from DB
  - update_rule(rule_id, config, updated_by) → versioned rule update (append-only)
  - get_rule_history(rule_id)            → version history for a rule
  - get_rejection_log(contract_id?)      → rejected/quarantined history

Proof Token design (HS256 JWT):
  - Claims: jti (UUID), contract_id, event_id, saga_id, iat, exp (iat+60s)
  - Signed with PROOF_TOKEN_SECRET (shared with Ledger MCP only)
  - Single-use: Ledger MCP records jti in validation.used_proof_tokens after use
"""

import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import asyncpg
import jwt
import redis.asyncio as aioredis
from mcp.server.fastmcp import FastMCP

from shared.config import get_settings
from shared.logging import configure_logging, get_logger
from shared.models.common import EventType

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("validation", settings.log_level)
logger = get_logger(__name__)

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

# ─── Module-level state ───────────────────────────────────────────────────────

_pool: asyncpg.Pool | None = None
_redis: aioredis.Redis | None = None

# ─── Seed rules (inserted once on startup if rules table is empty) ─────────────

_SEED_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "RULE-SCHEMA-VIN",
        "rule_type": "schema",
        "event_type": "contract.originated",
        "description": "VIN must be exactly 17 characters [A-HJ-NPR-Z0-9] (no I, O, or Q)",
        "config": {"field": "vehicle.vin", "pattern": "^[A-HJ-NPR-Z0-9]{17}$"},
    },
    {
        "rule_id": "RULE-BIZ-AMT-POS",
        "rule_type": "business",
        "event_type": "contract.originated",
        "description": "Amount financed must be greater than zero",
        "config": {"field": "financial_terms.amount_financed", "min_exclusive": 0},
    },
    {
        "rule_id": "RULE-BIZ-TERM",
        "rule_type": "business",
        "event_type": "contract.originated",
        "description": "Term months must be between 1 and 84",
        "config": {"field": "financial_terms.term_months", "min": 1, "max": 84},
    },
    {
        "rule_id": "RULE-BIZ-RATE",
        "rule_type": "business",
        "event_type": "contract.originated",
        "description": "Interest rate must be between 0% and 36% APR",
        "config": {"field": "financial_terms.interest_rate", "min": 0, "max": 36},
    },
    {
        "rule_id": "RULE-BIZ-PMT",
        "rule_type": "business",
        "event_type": "contract.originated",
        "description": "Monthly payment must be greater than zero",
        "config": {"field": "financial_terms.monthly_payment", "min_exclusive": 0},
    },
    {
        "rule_id": "RULE-BIZ-DEALER",
        "rule_type": "business",
        "event_type": "contract.originated",
        "description": "Dealer ID is required",
        "config": {"field": "dealer_id", "required": True},
    },
    {
        "rule_id": "RULE-XSYS-LOS-VIN",
        "rule_type": "cross_system",
        "event_type": "contract.originated",
        "description": "VIN in event payload must match VIN in Oracle LOS contract record",
        "config": {"check": "vin_match_oracle_los"},
    },
]


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _pool, _redis

    # PostgreSQL pool
    try:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
        logger.info("validation_db_connected")
        await _seed_rules_if_empty()
    except Exception as e:
        logger.error("validation_db_connection_failed", error=str(e))

    # Redis (for publishing retry events on override approval)
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await _redis.ping()
        logger.info("validation_redis_connected")
    except Exception as e:
        logger.warning("validation_redis_unavailable", error=str(e))

    try:
        yield
    finally:
        if _pool:
            await _pool.close()
        if _redis:
            await _redis.aclose()
        logger.info("validation_shutdown")


mcp = FastMCP(
    name="smartledger-validation",
    instructions=(
        "Validation Engine for SmartLedger. Validates events against schema, business rules, "
        "and cross-system data. Issues single-use JWT proof tokens on success. "
        "Quarantines invalid events for human review."
    ),
    lifespan=lifespan,
)


# ─── DB helpers ───────────────────────────────────────────────────────────────

async def _seed_rules_if_empty() -> None:
    """Seed validation rules on first startup if the table is empty."""
    if not _pool:
        return
    async with _pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM validation.rules WHERE active = TRUE")
        if count and count > 0:
            return
        for rule in _SEED_RULES:
            await conn.execute(
                """
                INSERT INTO validation.rules (rule_id, rule_type, event_type, description, config, version, active)
                VALUES ($1, $2, $3, $4, $5::jsonb, 1, TRUE)
                ON CONFLICT (rule_id, version) DO NOTHING
                """,
                rule["rule_id"],
                rule["rule_type"],
                rule.get("event_type"),
                rule["description"],
                json.dumps(rule["config"]),
            )
        logger.info("validation_rules_seeded", count=len(_SEED_RULES))


async def _quarantine_event(
    event_envelope: dict[str, Any],
    failures: list[dict[str, Any]],
    context_snapshot: dict[str, Any] | None,
) -> None:
    """Write a failed event to the quarantine table."""
    if not _pool:
        logger.error("quarantine_failed_no_db", event_id=event_envelope.get("event_id"))
        return

    primary_failure = failures[0] if failures else {"code": "UNKNOWN", "message": "Unknown failure"}
    context_data = {
        "failures": failures,
        "context": context_snapshot or {},
    }

    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO validation.quarantine (
                event_id, contract_id, event_type, source_system,
                rejection_code, rejection_detail,
                context_snapshot, original_payload,
                status, escalation_level, created_at, sla_deadline
            ) VALUES (
                $1::uuid, $2, $3, $4,
                $5, $6,
                $7::jsonb, $8::jsonb,
                'pending', 0, NOW(), NOW() + INTERVAL '24 hours'
            )
            ON CONFLICT (event_id) DO UPDATE
                SET status = 'pending',
                    rejection_code = EXCLUDED.rejection_code,
                    rejection_detail = EXCLUDED.rejection_detail,
                    context_snapshot = EXCLUDED.context_snapshot,
                    original_payload = EXCLUDED.original_payload
            """,
            event_envelope["event_id"],
            event_envelope.get("contract_id", ""),
            event_envelope.get("event_type", ""),
            event_envelope.get("source_system", ""),
            primary_failure["code"],
            json.dumps(failures),
            json.dumps(context_data),
            json.dumps(event_envelope.get("payload", {})),
        )
    logger.info(
        "event_quarantined",
        event_id=event_envelope["event_id"],
        contract_id=event_envelope.get("contract_id"),
        failure_count=len(failures),
        primary_code=primary_failure["code"],
    )


# ─── Proof token helpers ──────────────────────────────────────────────────────

def _issue_proof_token(contract_id: str, event_id: str, saga_id: str) -> tuple[str, str]:
    """
    Issue a signed JWT proof token.
    Returns (token_string, jti).
    Expires in proof_token_expiry_seconds (default 60s).
    """
    jti = str(uuid.uuid4())
    now = int(time.time())
    claims = {
        "jti": jti,
        "contract_id": contract_id,
        "event_id": event_id,
        "saga_id": saga_id,
        "iat": now,
        "exp": now + settings.proof_token_expiry_seconds,
    }
    token = jwt.encode(claims, settings.proof_token_secret, algorithm="HS256")
    return token, jti


# ─── Validation logic ─────────────────────────────────────────────────────────

def _get_nested(data: dict, dotted_path: str) -> Any:
    """Get a value from a nested dict using dot notation, e.g. 'vehicle.vin'."""
    keys = dotted_path.split(".")
    val: Any = data
    for key in keys:
        if not isinstance(val, dict):
            return None
        val = val.get(key)
    return val


def _validate_origination(
    payload: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Run all validation checks for a contract.originated event.
    Returns a list of ValidationFailure dicts (empty = valid).
    """
    failures: list[dict[str, Any]] = []

    # ── Schema / field checks ─────────────────────────────────────────────────

    # VIN format
    vin = _get_nested(payload, "vehicle.vin") or ""
    if not _VIN_RE.match(str(vin)):
        failures.append({
            "rule_id": "RULE-SCHEMA-VIN",
            "rule_type": "schema",
            "code": "INVALID_VIN_FORMAT",
            "message": f"VIN '{vin}' is not valid: must be 17 chars [A-HJ-NPR-Z0-9]",
            "field": "vehicle.vin",
            "expected": "17-char VIN [A-HJ-NPR-Z0-9]",
            "actual": vin,
        })

    # Amount financed
    amount_financed = _get_nested(payload, "financial_terms.amount_financed")
    if amount_financed is None or float(amount_financed) <= 0:
        failures.append({
            "rule_id": "RULE-BIZ-AMT-POS",
            "rule_type": "business",
            "code": "INVALID_AMOUNT_FINANCED",
            "message": "amount_financed must be greater than zero",
            "field": "financial_terms.amount_financed",
            "expected": "> 0",
            "actual": amount_financed,
        })

    # Term months
    term_months = _get_nested(payload, "financial_terms.term_months")
    if term_months is None or not (1 <= int(term_months) <= 84):
        failures.append({
            "rule_id": "RULE-BIZ-TERM",
            "rule_type": "business",
            "code": "INVALID_TERM_MONTHS",
            "message": f"term_months must be between 1 and 84 (got {term_months})",
            "field": "financial_terms.term_months",
            "expected": "1-84",
            "actual": term_months,
        })

    # Interest rate
    rate = _get_nested(payload, "financial_terms.interest_rate")
    if rate is None or not (0 <= float(rate) <= 36):
        failures.append({
            "rule_id": "RULE-BIZ-RATE",
            "rule_type": "business",
            "code": "INVALID_INTEREST_RATE",
            "message": f"interest_rate must be between 0% and 36% APR (got {rate})",
            "field": "financial_terms.interest_rate",
            "expected": "0-36",
            "actual": rate,
        })

    # Monthly payment
    monthly_payment = _get_nested(payload, "financial_terms.monthly_payment")
    if monthly_payment is None or float(monthly_payment) <= 0:
        failures.append({
            "rule_id": "RULE-BIZ-PMT",
            "rule_type": "business",
            "code": "INVALID_MONTHLY_PAYMENT",
            "message": "monthly_payment must be greater than zero",
            "field": "financial_terms.monthly_payment",
            "expected": "> 0",
            "actual": monthly_payment,
        })

    # Dealer ID
    dealer_id = payload.get("dealer_id")
    if not dealer_id or not str(dealer_id).strip():
        failures.append({
            "rule_id": "RULE-BIZ-DEALER",
            "rule_type": "business",
            "code": "MISSING_DEALER_ID",
            "message": "dealer_id is required and cannot be empty",
            "field": "dealer_id",
            "expected": "non-empty string",
            "actual": dealer_id,
        })

    # ── Cross-system checks ───────────────────────────────────────────────────

    oracle_contract = context.get("oracle_los_contract")
    if oracle_contract and oracle_contract.get("found") is not False:
        oracle_vin = _get_nested(oracle_contract, "vehicle.vin")
        if oracle_vin and oracle_vin != vin:
            failures.append({
                "rule_id": "RULE-XSYS-LOS-VIN",
                "rule_type": "cross_system",
                "code": "VIN_MISMATCH",
                "message": (
                    f"VIN in event payload '{vin}' does not match "
                    f"Oracle LOS VIN '{oracle_vin}'"
                ),
                "field": "vehicle.vin",
                "expected": oracle_vin,
                "actual": vin,
            })

    # LLAS account should NOT exist for a new origination
    llas_account = context.get("llas_account")
    if llas_account and llas_account.get("found") is True:
        failures.append({
            "rule_id": "RULE-XSYS-LLAS-NEW",
            "rule_type": "cross_system",
            "code": "DUPLICATE_ORIGINATION",
            "message": (
                f"LLAS account already exists for contract "
                f"'{payload.get('contract_id')}' — possible duplicate origination"
            ),
            "field": "contract_id",
            "expected": "no existing LLAS account",
            "actual": f"account {llas_account.get('account_number')} exists",
        })

    return failures


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def validate_event(request: dict) -> dict:
    """
    Validate an event and issue a proof token if valid.

    Input (ValidationRequest):
      {
        "event_envelope": {event_id, event_type, source_system, contract_id,
                           timestamp, correlation_id, schema_version, payload},
        "saga_id": "uuid-string",
        "context": {
          "oracle_los_contract": {...} | None,
          "llas_account": {...} | None,
          ... other gathered context ...
        }
      }

    Returns (ValidationResult):
      {
        "valid": true|false,
        "event_id": "uuid",
        "contract_id": "str",
        "saga_id": "uuid",
        "checked_at": "iso-datetime",
        "proof_token": "jwt-string"  ← only when valid=True
        "failures": [...]             ← only when valid=False
        "warnings": [...]
      }
    """
    event_envelope = request.get("event_envelope", {})
    saga_id = request.get("saga_id", "")
    context = request.get("context", {})

    event_id = event_envelope.get("event_id", "")
    contract_id = event_envelope.get("contract_id", "")
    event_type = event_envelope.get("event_type", "")
    payload = event_envelope.get("payload", {})

    # If payload is a JSON string (from Redis stream), parse it
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}

    checked_at = datetime.now(timezone.utc).isoformat()

    logger.info(
        "validate_event_start",
        event_id=event_id,
        event_type=event_type,
        contract_id=contract_id,
        saga_id=saga_id,
    )

    # ── Route to the right validator ──────────────────────────────────────────
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if event_type == EventType.CONTRACT_ORIGINATED:
        failures = _validate_origination(payload, context)
    else:
        # For event types not yet implemented, warn but don't block
        warnings.append({
            "code": "UNHANDLED_EVENT_TYPE",
            "message": f"Validation rules not yet defined for event type '{event_type}'. Passing through.",
        })
        logger.warning("unhandled_event_type", event_type=event_type)

    # ── Build result ──────────────────────────────────────────────────────────
    if failures:
        # Quarantine the event
        await _quarantine_event(event_envelope, failures, context)

        logger.info(
            "validation_failed",
            event_id=event_id,
            contract_id=contract_id,
            failure_count=len(failures),
        )
        return {
            "valid": False,
            "event_id": event_id,
            "contract_id": contract_id,
            "saga_id": saga_id,
            "checked_at": checked_at,
            "proof_token": None,
            "failures": failures,
            "warnings": warnings,
        }

    # ── Issue proof token ─────────────────────────────────────────────────────
    proof_token, jti = _issue_proof_token(contract_id, event_id, saga_id)

    logger.info(
        "validation_passed",
        event_id=event_id,
        contract_id=contract_id,
        saga_id=saga_id,
        jti=jti,
    )

    return {
        "valid": True,
        "event_id": event_id,
        "contract_id": contract_id,
        "saga_id": saga_id,
        "checked_at": checked_at,
        "proof_token": proof_token,
        "failures": [],
        "warnings": warnings,
    }


@mcp.tool()
async def get_quarantined(contract_id: str | None = None) -> list[dict]:
    """
    Return quarantined events pending human review.
    Optionally filter by contract_id.
    """
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        if contract_id:
            rows = await conn.fetch(
                """
                SELECT event_id::text, contract_id, event_type, source_system,
                       rejection_code, rejection_detail, status, escalation_level,
                       reviewed_by, reviewed_at, override_reason,
                       created_at::text, sla_deadline::text
                FROM validation.quarantine
                WHERE contract_id = $1
                ORDER BY created_at DESC
                """,
                contract_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT event_id::text, contract_id, event_type, source_system,
                       rejection_code, rejection_detail, status, escalation_level,
                       reviewed_by, reviewed_at, override_reason,
                       created_at::text, sla_deadline::text
                FROM validation.quarantine
                WHERE status = 'pending'
                ORDER BY sla_deadline ASC
                """
            )

    return [dict(row) for row in rows]


@mcp.tool()
async def approve_override(event_id: str, reason: str, reviewer: str) -> dict:
    """
    Approve a quarantined event for override.

    Updates the quarantine status to 'approved' and publishes a
    quarantine.approved event to Redis Streams so the agent can retry
    the origination with the human's override.

    Returns: {success, event_id, contract_id}
    """
    if not _pool:
        raise RuntimeError("Database not available")

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE validation.quarantine
            SET status = 'approved', reviewed_by = $2, reviewed_at = NOW(), override_reason = $3
            WHERE event_id = $1::uuid AND status = 'pending'
            RETURNING event_id::text, contract_id, event_type, original_payload
            """,
            event_id,
            reviewer,
            reason,
        )

    if not row:
        raise ValueError(
            f"No pending quarantine record found for event_id '{event_id}'"
        )

    # Publish quarantine.approved event to Redis so the agent retries
    if _redis:
        message: dict[str, str] = {
            "event_id": str(uuid.uuid4()),
            "event_type": EventType.QUARANTINE_APPROVED,
            "source_system": "dashboard",
            "contract_id": row["contract_id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "payload": json.dumps({
                "original_event_id": event_id,
                "contract_id": row["contract_id"],
                "override_reason": reason,
                "reviewed_by": reviewer,
                "original_payload": json.loads(row["original_payload"] or "{}"),
            }),
        }
        await _redis.xadd("smartledger:events", message)

    logger.info(
        "quarantine_approved",
        event_id=event_id,
        contract_id=row["contract_id"],
        reviewer=reviewer,
    )

    return {
        "success": True,
        "event_id": event_id,
        "contract_id": row["contract_id"],
        "event_type": row["event_type"],
        "override_reason": reason,
        "reviewed_by": reviewer,
    }


@mcp.tool()
async def get_validation_rules(rule_type: str | None = None) -> list[dict]:
    """Return active validation rules from the database, optionally filtered by rule_type."""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        if rule_type:
            rows = await conn.fetch(
                """
                SELECT rule_id, rule_type, event_type, description,
                       config::text, version, active, created_at::text, updated_at::text, updated_by
                FROM validation.rules
                WHERE active = TRUE AND rule_type = $1
                ORDER BY rule_id
                """,
                rule_type,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT rule_id, rule_type, event_type, description,
                       config::text, version, active, created_at::text, updated_at::text, updated_by
                FROM validation.rules
                WHERE active = TRUE
                ORDER BY rule_id
                """
            )

    result = []
    for row in rows:
        r = dict(row)
        if r.get("config"):
            try:
                r["config"] = json.loads(r["config"])
            except Exception:
                pass
        result.append(r)
    return result


@mcp.tool()
async def update_rule(rule_id: str, config: dict, updated_by: str) -> dict:
    """
    Update a validation rule (versioned, append-only).
    Creates a new version; deactivates the previous one.
    """
    if not _pool:
        raise RuntimeError("Database not available")

    async with _pool.acquire() as conn:
        # Get current version
        current = await conn.fetchrow(
            "SELECT version, rule_type, event_type, description FROM validation.rules "
            "WHERE rule_id = $1 AND active = TRUE",
            rule_id,
        )
        if not current:
            raise ValueError(f"Rule '{rule_id}' not found or not active")

        new_version = current["version"] + 1

        async with conn.transaction():
            # Deactivate current
            await conn.execute(
                "UPDATE validation.rules SET active = FALSE, updated_at = NOW() "
                "WHERE rule_id = $1 AND active = TRUE",
                rule_id,
            )
            # Insert new version
            await conn.execute(
                """
                INSERT INTO validation.rules
                  (rule_id, rule_type, event_type, description, config, version, active, updated_by)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, TRUE, $7)
                """,
                rule_id,
                current["rule_type"],
                current["event_type"],
                current["description"],
                json.dumps(config),
                new_version,
                updated_by,
            )

    logger.info("rule_updated", rule_id=rule_id, new_version=new_version, updated_by=updated_by)
    return {"success": True, "rule_id": rule_id, "new_version": new_version}


@mcp.tool()
async def get_rule_history(rule_id: str) -> list[dict]:
    """Return all versions of a validation rule (newest first)."""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT rule_id, rule_type, event_type, description,
                   config::text, version, active, created_at::text, updated_at::text, updated_by
            FROM validation.rules
            WHERE rule_id = $1
            ORDER BY version DESC
            """,
            rule_id,
        )

    result = []
    for row in rows:
        r = dict(row)
        if r.get("config"):
            try:
                r["config"] = json.loads(r["config"])
            except Exception:
                pass
        result.append(r)
    return result


@mcp.tool()
async def get_rejection_log(contract_id: str | None = None) -> list[dict]:
    """
    Return rejected/quarantined event history.
    Optionally filter by contract_id. Returns all statuses (not just pending).
    """
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        if contract_id:
            rows = await conn.fetch(
                """
                SELECT event_id::text, contract_id, event_type, source_system,
                       rejection_code, rejection_detail, status, escalation_level,
                       reviewed_by, reviewed_at::text, override_reason,
                       created_at::text, sla_deadline::text
                FROM validation.quarantine
                WHERE contract_id = $1
                ORDER BY created_at DESC
                """,
                contract_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT event_id::text, contract_id, event_type, source_system,
                       rejection_code, rejection_detail, status, escalation_level,
                       reviewed_by, reviewed_at::text, override_reason,
                       created_at::text, sla_deadline::text
                FROM validation.quarantine
                ORDER BY created_at DESC
                LIMIT 100
                """
            )

    return [dict(row) for row in rows]


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8001)
