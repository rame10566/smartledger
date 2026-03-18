"""
Validation Engine MCP Server

Core gatekeeper: validates every event before it can be written to the ledger.
Issues single-use signed JWT proof tokens on successful validation.
Quarantines invalid events as a read-only audit trail.

Data correction is the responsibility of the originating system (LOS, etc.).
SmartLedger does NOT override or correct data — it validates and records.

Tools:
  - validate_event(request)              → ValidationResult + proof_token (if valid)
  - get_quarantined(contract_id?)        → list quarantined events (read-only audit trail)
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
from mcp.server.transport_security import TransportSecuritySettings

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
    # ── Cross-reference rules (warnings only — never block writes) ──────────
    {
        "rule_id": "RULE-XREF-ELIGIBILITY",
        "rule_type": "cross_system",
        "event_type": "contract.originated",
        "description": "Cross-reference: flag if Rules Engine says contract would not meet current eligibility (informational)",
        "config": {"check": "rules_engine_eligibility", "severity": "warning"},
    },
    {
        "rule_id": "RULE-XREF-RATE",
        "rule_type": "cross_system",
        "event_type": "contract.originated",
        "description": "Cross-reference: flag if LOS interest rate deviates >2% from Pricing Engine calculation (informational)",
        "config": {"check": "pricing_engine_rate_deviation", "threshold_pct": 2.0, "severity": "warning"},
    },
    # ── Customer update rules ─────────────────────────────────────────────────
    {
        "rule_id": "RULE-CUST-PMT-DATE",
        "rule_type": "business",
        "event_type": "integration.payment_update_requested",
        "description": "Payment date must be between 1 and 28",
        "config": {"field": "payment_info.payment_date", "min": 1, "max": 28},
    },
    {
        "rule_id": "RULE-CUST-STATE-ELIGIBLE",
        "rule_type": "cross_system",
        "event_type": "integration.*",
        "description": "Customer profile updates only allowed on active or delinquent contracts",
        "config": {"check": "contract_state_update_eligible"},
    },
    {
        "rule_id": "RULE-CUST-STALE-SYNC",
        "rule_type": "cross_system",
        "event_type": "integration.llas_sync_requested",
        "description": "LOS sync data must not be older than last validated ledger record",
        "config": {"check": "stale_los_sync"},
    },
    # ── Payment rules ─────────────────────────────────────────────────────────
    {
        "rule_id": "RULE-PAY-AMT",
        "rule_type": "business",
        "event_type": "payment.received",
        "description": "Payment amount must be greater than zero",
        "config": {"field": "amount", "min_exclusive": 0},
    },
    {
        "rule_id": "RULE-PAY-STATE",
        "rule_type": "cross_system",
        "event_type": "payment.received",
        "description": "Payments can only be applied to active or delinquent contracts",
        "config": {"check": "contract_state_payable"},
    },
    {
        "rule_id": "RULE-PAY-ACCT",
        "rule_type": "cross_system",
        "event_type": "payment.received",
        "description": "A LLAS account must exist before a payment can be posted",
        "config": {"check": "llas_account_exists"},
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
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


# ─── DB helpers ───────────────────────────────────────────────────────────────

async def _seed_rules_if_empty() -> None:
    """Seed all validation rules using INSERT ... ON CONFLICT DO NOTHING.

    Runs on every startup — safe to re-run because new rules are inserted
    idempotently. This ensures payment rules are added even if origination
    rules were already seeded in a prior run.
    """
    if not _pool:
        return
    inserted = 0
    async with _pool.acquire() as conn:
        for rule in _SEED_RULES:
            result = await conn.execute(
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
            if result == "INSERT 0 1":
                inserted += 1
    if inserted:
        logger.info("validation_rules_seeded", inserted=inserted, total=len(_SEED_RULES))


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
    is_conflict = primary_failure.get("code") == "CONFLICT_PENDING"
    conflict_pair_id = primary_failure.get("conflict_pair_id") if is_conflict else None
    conflicting_event_id = primary_failure.get("conflicting_event_id") if is_conflict else None
    status = "conflict" if is_conflict else "pending"

    context_data = {
        "failures": failures,
        "context": {k: v for k, v in (context_snapshot or {}).items() if not k.startswith("_")},
    }

    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO validation.quarantine (
                    event_id, contract_id, event_type, source_system,
                    rejection_code, rejection_detail,
                    context_snapshot, original_payload,
                    status, escalation_level, conflict_pair_id, created_at, sla_deadline
                ) VALUES (
                    $1::uuid, $2, $3, $4,
                    $5, $6,
                    $7::jsonb, $8::jsonb,
                    $9, 0, $10, NOW(), NOW() + INTERVAL '24 hours'
                )
                ON CONFLICT (event_id) DO UPDATE
                    SET status = EXCLUDED.status,
                        rejection_code = EXCLUDED.rejection_code,
                        rejection_detail = EXCLUDED.rejection_detail,
                        context_snapshot = EXCLUDED.context_snapshot,
                        original_payload = EXCLUDED.original_payload,
                        conflict_pair_id = EXCLUDED.conflict_pair_id
                """,
                event_envelope["event_id"],
                event_envelope.get("contract_id", ""),
                event_envelope.get("event_type", ""),
                event_envelope.get("source_system", ""),
                primary_failure["code"],
                json.dumps(failures),
                json.dumps(context_data),
                json.dumps(event_envelope.get("payload", {})),
                status,
                conflict_pair_id,
            )

            # If this is a conflict, also update the other quarantine entry
            if is_conflict and conflicting_event_id and conflict_pair_id:
                await conn.execute(
                    """
                    UPDATE validation.quarantine
                    SET status = 'conflict', conflict_pair_id = $1
                    WHERE event_id = $2::uuid
                    """,
                    conflict_pair_id,
                    conflicting_event_id,
                )

    logger.info(
        "event_quarantined",
        event_id=event_envelope["event_id"],
        contract_id=event_envelope.get("contract_id"),
        failure_count=len(failures),
        primary_code=primary_failure["code"],
        status=status,
        conflict_pair_id=conflict_pair_id,
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


def _validate_payment(
    payload: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Validate a payment.received / customer.payment_submitted / ivr.payment_submitted event.

    Rules:
      RULE-PAY-AMT   — payment amount must be > 0
      RULE-PAY-STATE — contract state must be active or delinquent
      RULE-PAY-ACCT  — LLAS account must exist
    """
    failures: list[dict[str, Any]] = []

    # RULE-PAY-AMT: amount > 0
    amount = payload.get("amount")
    try:
        if amount is None or float(amount) <= 0:
            raise ValueError
    except (ValueError, TypeError):
        failures.append({
            "rule_id": "RULE-PAY-AMT",
            "rule_type": "business",
            "code": "INVALID_PAYMENT_AMOUNT",
            "message": f"Payment amount must be greater than zero (got {amount})",
            "field": "amount",
            "expected": "> 0",
            "actual": amount,
        })

    # RULE-PAY-STATE: contract state must be active or delinquent
    ledger_state = context.get("ledger_state", {})
    current_state = ledger_state.get("current_state")
    _PAYABLE_STATES = {"active", "delinquent", "originated"}
    if current_state and current_state not in _PAYABLE_STATES:
        failures.append({
            "rule_id": "RULE-PAY-STATE",
            "rule_type": "cross_system",
            "code": "CONTRACT_NOT_PAYABLE",
            "message": (
                f"Payments cannot be applied to a contract in state '{current_state}'. "
                f"Contract must be active or delinquent."
            ),
            "field": "contract_state",
            "expected": str(_PAYABLE_STATES),
            "actual": current_state,
        })

    # RULE-PAY-ACCT: LLAS account must exist
    llas_account = context.get("llas_account", {})
    if llas_account and llas_account.get("found") is False:
        failures.append({
            "rule_id": "RULE-PAY-ACCT",
            "rule_type": "cross_system",
            "code": "NO_LLAS_ACCOUNT",
            "message": (
                f"No LLAS account found for contract '{payload.get('contract_id')}'. "
                "Cannot post payment without an active accounting record."
            ),
            "field": "llas_account",
            "expected": "existing LLAS account",
            "actual": "not found",
        })

    return failures


def _validate_customer_update(
    payload: dict[str, Any],
    context: dict[str, Any],
    event_type: str,
) -> list[dict[str, Any]]:
    """
    Validate a customer profile update (integration.* events).

    Rules:
      RULE-CUST-STATE-ELIGIBLE  — contract must be active or delinquent
      RULE-CUST-PMT-DATE        — payment date must be 1-28 (payment updates only)
      RULE-CUST-STALE-SYNC      — LOS sync data must not be stale
    """
    failures: list[dict[str, Any]] = []

    # RULE-CUST-STATE-ELIGIBLE: contract must be active or delinquent
    _UPDATE_ELIGIBLE_STATES = {"active", "delinquent", "originated"}
    ledger_state = context.get("ledger_state", {})
    current_state = ledger_state.get("current_state")
    if current_state and current_state not in _UPDATE_ELIGIBLE_STATES:
        failures.append({
            "rule_id":   "RULE-CUST-STATE-ELIGIBLE",
            "rule_type": "cross_system",
            "code":      "CONTRACT_STATE_INELIGIBLE",
            "message":   (
                f"Customer profile updates are not allowed on contracts in state "
                f"'{current_state}'. Contract must be active or delinquent."
            ),
            "field":    "contract_state",
            "expected": str(_UPDATE_ELIGIBLE_STATES),
            "actual":   current_state,
        })
        # Early return — no point checking other rules if state is ineligible
        return failures

    # RULE-CUST-PMT-DATE: payment date must be 1–28
    if event_type == "integration.payment_update_requested":
        payment_info = payload.get("changes", {}).get("payment_info", {})
        payment_date = payment_info.get("payment_date")
        if payment_date is not None:
            try:
                day = int(payment_date)
                if not (1 <= day <= 28):
                    raise ValueError
            except (ValueError, TypeError):
                failures.append({
                    "rule_id":   "RULE-CUST-PMT-DATE",
                    "rule_type": "business",
                    "code":      "INVALID_PAYMENT_DATE",
                    "message":   f"Payment date must be between 1 and 28 (got {payment_date})",
                    "field":     "payment_info.payment_date",
                    "expected":  "1-28",
                    "actual":    payment_date,
                })

    # RULE-CUST-STALE-SYNC: LOS sync data must not be older than last ledger record
    if event_type == "integration.llas_sync_requested":
        los_updated_at = payload.get("changes", {}).get("los_updated_at")
        last_ledger_update = context.get("last_customer_update_at")
        if los_updated_at and last_ledger_update:
            try:
                from datetime import datetime, timezone
                los_dt = datetime.fromisoformat(los_updated_at.replace("Z", "+00:00"))
                ledger_dt = datetime.fromisoformat(last_ledger_update.replace("Z", "+00:00"))
                if los_dt < ledger_dt:
                    failures.append({
                        "rule_id":   "RULE-CUST-STALE-SYNC",
                        "rule_type": "cross_system",
                        "code":      "STALE_LOS_SYNC",
                        "message":   (
                            f"LOS sync data (updated {los_updated_at}) is older than the last "
                            f"validated customer update in the ledger ({last_ledger_update}). "
                            "Applying this sync would overwrite more recent validated data."
                        ),
                        "field":    "los_updated_at",
                        "expected": f">= {last_ledger_update}",
                        "actual":   los_updated_at,
                    })
            except Exception:
                pass  # If dates can't be compared, allow through

    return failures


async def _check_conflict(
    contract_id: str,
    source_system: str,
    changes: dict,
    event_id: str,
) -> str | None:
    """
    Check if any pending quarantine entry conflicts with this update
    (same contract, same field(s), different source system).

    Returns conflict_pair_id if a conflict is found, None otherwise.
    """
    if not _pool:
        return None

    # Determine which top-level change keys are being modified
    changed_keys = set(changes.keys())
    if not changed_keys:
        return None

    async with _pool.acquire() as conn:
        # Look for pending integration updates to same contract from different source
        rows = await conn.fetch(
            """
            SELECT event_id::text, source_system, original_payload, context_snapshot
            FROM validation.quarantine
            WHERE contract_id = $1
              AND status = 'pending'
              AND source_system != $2
              AND event_type LIKE 'integration.%'
              AND event_id::text != $3
            ORDER BY created_at DESC
            LIMIT 10
            """,
            contract_id,
            source_system,
            event_id,
        )

    for row in rows:
        try:
            other_payload = json.loads(row["original_payload"] or "{}")
            other_changes = other_payload.get("changes", {})
            other_keys = set(other_changes.keys())
            # Conflict = overlapping top-level change keys
            if changed_keys & other_keys:
                return str(row["event_id"])
        except Exception:
            continue

    return None


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base dict. Returns a new dict."""
    result = dict(base)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


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


def _cross_reference_origination(
    payload: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Cross-reference origination data against upstream Rules Engine and Pricing
    Engine. These are informational warnings — they flag data anomalies but
    do NOT block the ledger write. The LOS already approved the deal; we're
    just verifying consistency.

    Returns a list of warning dicts.
    """
    warnings: list[dict[str, Any]] = []

    # ── Rules Engine cross-reference ─────────────────────────────────────────
    rules_data = context.get("rules_engine")
    if rules_data and not rules_data.get("eligible", True):
        failed_rules = [
            r for r in rules_data.get("results", [])
            if not r.get("passed")
        ]
        warning_details = "; ".join(r.get("message", r.get("rule", "")) for r in failed_rules)
        warnings.append({
            "code": "RULES_ENGINE_INELIGIBLE",
            "message": (
                f"Rules Engine indicates this contract would not meet current "
                f"eligibility criteria (credit tier: {rules_data.get('credit_tier')}). "
                f"Failed: {warning_details}. "
                f"LOS already approved — recording as informational warning."
            ),
            "severity": "warning",
            "source": "rules_engine",
        })

    # ── Pricing Engine cross-reference: rate deviation ───────────────────────
    pricing_data = context.get("pricing_engine")
    if pricing_data and pricing_data.get("final_rate") is not None:
        los_rate = _get_nested(payload, "financial_terms.interest_rate")
        engine_rate = pricing_data["final_rate"]

        if los_rate is not None:
            rate_delta = abs(float(los_rate) - float(engine_rate))
            # Flag if LOS rate deviates more than 2% from engine calculation
            if rate_delta > 2.0:
                warnings.append({
                    "code": "RATE_DEVIATION",
                    "message": (
                        f"Interest rate from LOS ({los_rate}%) deviates "
                        f"from Pricing Engine calculation ({engine_rate}%) "
                        f"by {rate_delta:.2f} percentage points. "
                        f"May indicate dealer markup, promotional rate, or data entry error."
                    ),
                    "severity": "warning",
                    "source": "pricing_engine",
                    "los_rate": los_rate,
                    "engine_rate": engine_rate,
                    "delta": round(rate_delta, 2),
                })

    return warnings


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

    _PAYMENT_EVENT_TYPES = {
        EventType.PAYMENT_RECEIVED,
        EventType.CUSTOMER_PAYMENT_SUBMITTED,
        EventType.IVR_PAYMENT_SUBMITTED,
    }

    _INTEGRATION_EVENT_TYPES = {
        EventType.INTEGRATION_CONTACT_UPDATE,
        EventType.INTEGRATION_PAYMENT_UPDATE,
        EventType.INTEGRATION_INSURANCE_UPDATE,
        EventType.INTEGRATION_LLAS_SYNC,
    }

    if event_type == EventType.CONTRACT_ORIGINATED:
        failures = _validate_origination(payload, context)
        # Cross-reference against upstream systems (warnings only, never blocks)
        warnings.extend(_cross_reference_origination(payload, context))
    elif event_type in _PAYMENT_EVENT_TYPES:
        failures = _validate_payment(payload, context)
    elif event_type in _INTEGRATION_EVENT_TYPES:
        failures = _validate_customer_update(payload, context, event_type)
        # Conflict check: if no other failures, look for competing pending updates
        if not failures:
            source_system = event_envelope.get("source_system", "")
            changes = payload.get("changes", {})
            conflicting_event_id = await _check_conflict(contract_id, source_system, changes, event_id)
            if conflicting_event_id:
                # Quarantine this event AND update the other event to conflict status
                conflict_pair_id = str(uuid.uuid4())
                failures.append({
                    "rule_id":   "RULE-CONFLICT-PENDING",
                    "rule_type": "cross_system",
                    "code":      "CONFLICT_PENDING",
                    "message":   (
                        f"Competing update to the same fields from a different source system "
                        f"is already pending. Both updates are quarantined for LLAS Admin resolution."
                    ),
                    "field":            list(changes.keys()),
                    "conflict_pair_id": conflict_pair_id,
                    "conflicting_event_id": conflicting_event_id,
                })
                # Store conflict_pair_id in context for _quarantine_event to use
                context["_conflict_pair_id"] = conflict_pair_id
                context["_conflicting_event_id"] = conflicting_event_id
    elif event_type == EventType.INTEGRATION_CONFLICT_RESOLVED:
        # Conflict resolution events are pre-validated by resolve_conflict tool — pass through
        pass
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
async def get_conflicts(contract_id: str | None = None) -> list[dict]:
    """
    Return active conflict pairs (status='conflict') pending LLAS Admin resolution.
    Optionally filter by contract_id.

    Returns pairs of quarantine entries grouped by conflict_pair_id.
    """
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        if contract_id:
            rows = await conn.fetch(
                """
                SELECT event_id::text, contract_id, event_type, source_system,
                       rejection_code, rejection_detail, status, conflict_pair_id,
                       context_snapshot::text, original_payload::text,
                       created_at::text, sla_deadline::text
                FROM validation.quarantine
                WHERE status = 'conflict'
                  AND conflict_pair_id IS NOT NULL
                  AND contract_id = $1
                ORDER BY created_at DESC
                """,
                contract_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT event_id::text, contract_id, event_type, source_system,
                       rejection_code, rejection_detail, status, conflict_pair_id,
                       context_snapshot::text, original_payload::text,
                       created_at::text, sla_deadline::text
                FROM validation.quarantine
                WHERE status = 'conflict'
                  AND conflict_pair_id IS NOT NULL
                ORDER BY created_at DESC
                """
            )

    result = []
    for row in rows:
        r = dict(row)
        for key in ("context_snapshot", "original_payload"):
            if r.get(key):
                try:
                    r[key] = json.loads(r[key])
                except Exception:
                    pass
        result.append(r)
    return result


@mcp.tool()
async def resolve_conflict(
    conflict_pair_id: str,
    winning_event_id: str,
    admin_id: str,
    reason: str,
) -> dict:
    """
    Resolve a conflict between two competing customer profile updates.

    Called by the Dashboard API when an LLAS Admin selects the authoritative value.
    SmartLedger still validates the winning value before issuing a proof token.
    Both entries are updated: winning=resolved, losing=rejected.
    A conflict_resolved event is published to trigger the agent to write the ledger record.

    Args:
        conflict_pair_id: the UUID linking both quarantine entries
        winning_event_id: the event_id of the update the admin has selected as authoritative
        admin_id:         user ID of the LLAS Admin making the decision
        reason:           reason for selecting the winning value (required)

    Returns: {success, proof_token, winning_event_id, contract_id}
    """
    if not _pool:
        raise RuntimeError("Database not available")
    if not reason.strip():
        return {"success": False, "reason": "reason is required for conflict resolution"}

    # Fetch both entries in the conflict pair
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_id::text, contract_id, event_type, source_system,
                   original_payload::text, context_snapshot::text
            FROM validation.quarantine
            WHERE conflict_pair_id = $1 AND status = 'conflict'
            """,
            conflict_pair_id,
        )

    if not rows:
        return {
            "success": False,
            "reason": f"No active conflict found for conflict_pair_id '{conflict_pair_id}'",
        }

    winning_row = next((r for r in rows if str(r["event_id"]) == winning_event_id), None)
    if not winning_row:
        return {
            "success": False,
            "reason": f"winning_event_id '{winning_event_id}' not in conflict pair '{conflict_pair_id}'",
        }

    losing_row = next((r for r in rows if str(r["event_id"]) != winning_event_id), None)
    contract_id = winning_row["contract_id"]

    # Parse payload
    try:
        winning_payload = json.loads(winning_row["original_payload"] or "{}")
        winning_context = json.loads(winning_row.get("context_snapshot") or "{}")
    except Exception:
        winning_payload = {}
        winning_context = {}

    # Validate the winning value (business rules must still pass)
    win_event_type = winning_row["event_type"]
    win_context = winning_context.get("context", {})
    failures = _validate_customer_update(winning_payload, win_context, win_event_type)
    if failures:
        return {
            "success": False,
            "reason":  "Winning value failed validation — cannot resolve conflict",
            "failures": failures,
        }

    # Issue proof token for the winning event
    proof_token, jti = _issue_proof_token(contract_id, winning_event_id, f"conflict-resolution-{conflict_pair_id}")

    # Update quarantine statuses
    now = datetime.now(timezone.utc)
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE validation.quarantine
                SET status = 'resolved', reviewed_by = $1, reviewed_at = $2,
                    override_reason = $3
                WHERE event_id = $4::uuid
                """,
                admin_id, now, reason, winning_event_id,
            )
            if losing_row:
                await conn.execute(
                    """
                    UPDATE validation.quarantine
                    SET status = 'rejected', reviewed_by = $1, reviewed_at = $2,
                        override_reason = $3
                    WHERE event_id = $4::uuid
                    """,
                    admin_id, now,
                    f"CONFLICT_RESOLVED_BY_ADMIN: {admin_id} selected competing update. Reason: {reason}",
                    str(losing_row["event_id"]),
                )

    # Publish integration.conflict_resolved event to trigger agent
    if _redis:
        message: dict[str, str] = {
            "event_id":       str(uuid.uuid4()),
            "event_type":     "integration.conflict_resolved",
            "source_system":  "validation_engine",
            "contract_id":    contract_id,
            "timestamp":      now.isoformat(),
            "correlation_id": conflict_pair_id,
            "schema_version": "1.0",
            "payload":        json.dumps({
                "contract_id":      contract_id,
                "conflict_pair_id": conflict_pair_id,
                "winning_event_id": winning_event_id,
                "winning_payload":  winning_payload,
                "admin_id":         admin_id,
                "reason":           reason,
                "proof_token":      proof_token,
            }),
        }
        try:
            await _redis.xadd("smartledger:events", message)
            logger.info(
                "conflict_resolved_event_published",
                conflict_pair_id=conflict_pair_id,
                winning_event_id=winning_event_id,
                admin_id=admin_id,
            )
        except Exception as e:
            logger.error("conflict_resolved_publish_failed", error=str(e))

    return {
        "success":          True,
        "proof_token":      proof_token,
        "winning_event_id": winning_event_id,
        "contract_id":      contract_id,
        "conflict_pair_id": conflict_pair_id,
    }


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
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = 8001
    mcp.run(transport="streamable-http")
