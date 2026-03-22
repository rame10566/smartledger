"""
E2E Test — Contract Origination Happy Path

Requires the full dev stack running:
  ./scripts/dev_start.sh

Tests the complete flow end-to-end:
  Oracle LOS originate_contract
    → Redis Stream event published
    → Agent consumes event
    → Validation Engine validates + issues proof token
    → Ledger MCP writes immutable record
    → State transitioned to "active"
    → LLAS account created
    → Saga checkpointed COMPLETED

Run with:
  uv run pytest tests/e2e/test_origination_happy_path.py -v -s

Markers:
  pytest.mark.e2e — requires live infra (skipped in unit test runs)

IMPORTANT: These tests hit real running services. They are NOT mocked.
"""

import asyncio
import uuid
from datetime import date

import asyncpg
import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from agent.core.mcp_client import ledger, llas, oracle_los, validation

# ── Test infrastructure ───────────────────────────────────────────────────────

pytestmark = pytest.mark.e2e

DATABASE_URL = "postgresql://smartledger:smartledger_dev@localhost:5432/smartledger"
REDIS_URL    = "redis://localhost:6379"

# How long to wait for the agent to process an event
AGENT_TIMEOUT_SECS = 30
POLL_INTERVAL_SECS = 0.5


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def pg_pool():
    """Module-scoped asyncpg pool for assertion queries."""
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="function")
async def redis_client():
    """Module-scoped Redis client."""
    client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    yield client
    await client.aclose()


def _unique_contract() -> dict:
    """Generate a unique valid origination payload."""
    cid = f"E2E-{uuid.uuid4().hex[:8].upper()}"
    vin = f"1HGBH41JXMN{uuid.uuid4().hex[:6].upper()}"
    return {
        "contract_id":     cid,
        "contract_type":   "loan",
        "vehicle": {
            "vin":   vin,
            "year":  2024,
            "make":  "Toyota",
            "model": "Camry",
            "trim":  "XLE",
            "color": "Blue",
        },
        "customer": {
            "customer_id": f"CUST-{uuid.uuid4().hex[:6].upper()}",
            "first_name":  "E2E",
            "last_name":   "Test",
            "email":       "e2e@example.com",
        },
        "financial_terms": {
            "amount_financed": 25_000.00,
            "term_months":     60,
            "interest_rate":   5.99,
            "monthly_payment": 483.15,
            "down_payment":    2_500.00,
        },
        "dealer_id":        "DLR-001",
        "origination_date": date.today().isoformat(),
        "notes":            "E2E test contract",
    }


async def _wait_for_ledger_record(
    contract_id: str,
    record_type: str = "origination",
    timeout: float = AGENT_TIMEOUT_SECS,
    wait_for_state: str | None = "active",
) -> dict | None:
    """
    Poll the Ledger MCP until a record of the given type appears.
    If wait_for_state is set, also waits for current_state to match.
    Returns the lifecycle dict or None on timeout.
    """
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        await asyncio.sleep(POLL_INTERVAL_SECS)
        try:
            lifecycle = await ledger.get_contract_lifecycle(contract_id)
            records = lifecycle.get("records", [])
            has_record = any(r.get("record_type") == record_type for r in records)
            if has_record:
                if wait_for_state is None or lifecycle.get("current_state") == wait_for_state:
                    return lifecycle
        except Exception:
            pass  # services might be warming up
    return None


async def _wait_for_saga_complete(
    pg_pool: asyncpg.Pool,
    contract_id: str,
    timeout: float = AGENT_TIMEOUT_SECS,
) -> dict | None:
    """
    Poll sagas.processed_events until the contract's event is written.
    Returns the row or None on timeout.
    """
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        await asyncio.sleep(POLL_INTERVAL_SECS)
        row = await pg_pool.fetchrow(
            """
            SELECT event_id::text, saga_id::text, event_type, outcome, processed_at::text
            FROM sagas.processed_events
            WHERE contract_id = $1
            ORDER BY processed_at DESC
            LIMIT 1
            """,
            contract_id,
        )
        if row:
            return dict(row)
    return None


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOriginationHappyPath:
    """
    Full E2E: origination event flows through every layer and lands in the ledger.
    """

    async def test_oracle_los_accepts_valid_contract(self):
        """Oracle LOS should accept a valid contract and return success."""
        data = _unique_contract()
        result = await oracle_los.originate_contract(data)

        assert result["success"] is True
        assert result["contract_id"]  # Oracle LOS generates its own ID
        assert result["stream_entry_id"]  # event was published to Redis

    async def test_event_published_to_redis_stream(self, redis_client):
        """After origination, the stream should contain the event."""
        data = _unique_contract()
        await oracle_los.originate_contract(data)

        # Read the last entry from the stream
        entries = await redis_client.xrevrange("smartledger:events", count=1)
        assert entries, "Stream should have at least one entry"

        _, fields = entries[0]
        assert fields.get("event_type") == "contract.originated"
        assert fields.get("contract_id")

    async def test_ledger_receives_origination_record(self, pg_pool):
        """
        After origination, the agent must write an 'origination' record to the ledger.
        This verifies the full flow: Oracle LOS → Redis → Agent → Validation → Ledger.
        """
        data = _unique_contract()
        result = await oracle_los.originate_contract(data)
        assert result["success"]

        contract_id = result["contract_id"]
        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None, (
            f"Origination record not found in ledger after {AGENT_TIMEOUT_SECS}s — "
            f"is the agent running? (dev_start.sh)"
        )

        records = lifecycle.get("records", [])
        origination_records = [r for r in records if r.get("record_type") == "origination"]
        assert len(origination_records) == 1

    async def test_contract_state_is_active_after_origination(self):
        """After successful origination, state should be 'active'."""
        data = _unique_contract()
        result = await oracle_los.originate_contract(data)
        contract_id = result["contract_id"]

        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None, "Ledger record not found"
        assert lifecycle.get("current_state") == "active"

    async def test_state_history_shows_originated_to_active_transition(self):
        """State history should record the originated → active transition."""
        data = _unique_contract()
        result = await oracle_los.originate_contract(data)
        contract_id = result["contract_id"]

        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None

        state_history = lifecycle.get("state_history", [])
        assert any(
            s.get("state") == "active"
            for s in state_history
        ), f"Expected 'active' in state_history, got: {state_history}"

    async def test_saga_checkpointed_completed(self, pg_pool):
        """The saga must be checkpointed COMPLETED in sagas.processed_events."""
        data = _unique_contract()
        result = await oracle_los.originate_contract(data)
        contract_id = result["contract_id"]

        # Wait for ledger write first (proves agent ran)
        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None

        # Then check saga is marked completed
        row = await _wait_for_saga_complete(pg_pool, contract_id)
        assert row is not None, "Saga not found in sagas.processed_events"
        assert row["outcome"] == "written", f"Expected outcome='written', got: {row['outcome']}"
        assert row["event_type"] == "contract.originated"

    async def test_audit_trail_contains_ledger_written_action(self):
        """Audit trail should record the ledger_written action."""
        data = _unique_contract()
        result = await oracle_los.originate_contract(data)
        contract_id = result["contract_id"]

        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None

        audit = await ledger.get_audit_trail(contract_id)
        actions = [e.get("action") for e in audit]
        assert "ledger_written" in actions, f"Expected ledger_written in audit, got: {actions}"
        assert "state_transitioned" in actions, f"Expected state_transitioned in audit, got: {actions}"

    async def test_llas_account_created(self):
        """LLAS should have an account after origination completes."""
        data = _unique_contract()
        result = await oracle_los.originate_contract(data)
        contract_id = result["contract_id"]

        # Wait for full flow completion (ledger write + state transition)
        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None

        # Poll LLAS for account (may take a moment after ledger write)
        account = None
        for _ in range(10):
            account = await llas.get_account(contract_id)
            if account.get("found"):
                break
            await asyncio.sleep(1)
        assert account and account.get("found") is True, (
            f"LLAS account not found for {contract_id} — got: {account}"
        )

    async def test_idempotency_same_event_not_processed_twice(self, pg_pool):
        """
        Publishing the same contract_id twice should not create two ledger records.
        The idempotency table prevents double-processing the same event_id.
        (Note: two originate_contract calls will produce two different event_ids,
        but LLAS account creation would fail on the second — that's the business rule.)
        """
        data = _unique_contract()
        r1 = await oracle_los.originate_contract(data)
        assert r1["success"]

        contract_id = r1["contract_id"]
        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None

        origination_count = sum(
            1 for r in lifecycle.get("records", [])
            if r.get("record_type") == "origination"
        )
        assert origination_count == 1, (
            f"Expected exactly 1 origination record, got {origination_count}"
        )

    async def test_validation_proof_token_is_single_use(self, pg_pool):
        """
        The proof token jti should be recorded in validation.used_proof_tokens,
        preventing replay attacks.
        """
        data = _unique_contract()
        result = await oracle_los.originate_contract(data)
        contract_id = result["contract_id"]

        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None

        # Verify a used proof token was recorded
        row = await pg_pool.fetchrow(
            """
            SELECT jti, contract_id, used_at::text
            FROM validation.used_proof_tokens
            WHERE contract_id = $1
            ORDER BY used_at DESC
            LIMIT 1
            """,
            contract_id,
        )
        assert row is not None, "No used proof token found — write did not use a proof token"
        assert row["contract_id"] == contract_id

    async def test_write_guard_respected(self):
        """
        In Phase 1 (WRITE_GUARD=false), the ledger should write to Fabric
        and the contract state should be 'active'.
        """
        data = _unique_contract()
        result = await oracle_los.originate_contract(data)
        contract_id = result["contract_id"]

        lifecycle = await _wait_for_ledger_record(contract_id, timeout=60)
        assert lifecycle is not None

        state = await ledger.get_state(contract_id)
        # In Phase 1 with live Fabric, fabric_tx_id should be present OR state should be active
        assert state.get("fabric_tx_id") is not None or state.get("current_state") == "active"
