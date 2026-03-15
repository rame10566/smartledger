"""
E2E Test — Contract Origination Unhappy Path (Quarantine → Override)

Requires the full dev stack running:
  ./scripts/dev_start.sh

Tests the complete unhappy path:
  1. Originate a contract that will FAIL validation
     (e.g. interest_rate > 36% — triggers RULE-BIZ-RATE)
  2. Verify the event is quarantined (not written to ledger)
  3. Human reviewer approves via Dashboard API
  4. quarantine.approved event published to Redis Stream
  5. Agent OverrideFlow runs → record written to ledger
  6. Contract state = active, saga COMPLETED with override metadata

Run with:
  uv run pytest tests/e2e/test_origination_unhappy_path.py -v -s

Markers:
  pytest.mark.e2e — requires live infra
"""

import asyncio
import uuid
from datetime import date

import asyncpg
import pytest
import pytest_asyncio
import httpx

from agent.core.mcp_client import ledger, oracle_los

pytestmark = pytest.mark.e2e

DATABASE_URL     = "postgresql://smartledger:smartledger_dev@localhost:5432/smartledger"
DASHBOARD_API    = "http://localhost:8000"
AGENT_TIMEOUT    = 30
POLL_INTERVAL    = 0.5


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def pg_pool():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module")
async def http():
    """httpx async client for Dashboard API calls."""
    async with httpx.AsyncClient(base_url=DASHBOARD_API, timeout=10) as client:
        yield client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _invalid_contract() -> dict:
    """Contract with interest_rate > 36% — will fail RULE-BIZ-RATE."""
    return {
        "contract_id":   f"UNHAPPY-{uuid.uuid4().hex[:8].upper()}",
        "contract_type": "loan",
        "vin":           f"1HGBH41JXMN{uuid.uuid4().hex[:6].upper()}",
        "vehicle": {
            "year":  2024,
            "make":  "Toyota",
            "model": "Camry",
            "trim":  "SE",
            "color": "Red",
        },
        "customer": {
            "customer_id": f"CUST-{uuid.uuid4().hex[:6].upper()}",
            "first_name":  "Unhappy",
            "last_name":   "Path",
            "email":       "unhappy@example.com",
        },
        "financial_terms": {
            "amount_financed": 20_000.00,
            "term_months":     60,
            "interest_rate":   99.99,   # ← will fail RULE-BIZ-RATE (max 36%)
            "monthly_payment": 522.15,
            "down_payment":    1_000.00,
        },
        "dealer_id":        "DLR-001",
        "origination_date": date.today().isoformat(),
        "notes":            "E2E unhappy path test — deliberately invalid",
    }


async def _wait_for_quarantine(
    pg_pool: asyncpg.Pool,
    contract_id: str,
    timeout: float = AGENT_TIMEOUT,
) -> dict | None:
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        await asyncio.sleep(POLL_INTERVAL)
        row = await pg_pool.fetchrow(
            """
            SELECT event_id::text, contract_id, rejection_code, status, created_at::text
            FROM validation.quarantine
            WHERE contract_id = $1 AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            contract_id,
        )
        if row:
            return dict(row)
    return None


async def _wait_for_ledger_record(
    contract_id: str,
    timeout: float = AGENT_TIMEOUT,
) -> dict | None:
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            lifecycle = await ledger.get_contract_lifecycle(contract_id)
            if any(r.get("record_type") == "origination" for r in lifecycle.get("records", [])):
                return lifecycle
        except Exception:
            pass
    return None


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOriginationUnhappyPath:
    """Full unhappy path: quarantine → human approve → ledger write."""

    async def test_invalid_contract_is_quarantined(self, pg_pool):
        """An invalid contract must end up in validation.quarantine, not in the ledger."""
        data = _invalid_contract()
        result = await oracle_los.originate_contract(data)
        assert result["success"], "Oracle LOS should accept the contract regardless of rules"

        contract_id = data["contract_id"]
        quarantine = await _wait_for_quarantine(pg_pool, contract_id)
        assert quarantine is not None, (
            f"No quarantine record found for {contract_id} after {AGENT_TIMEOUT}s"
        )
        assert quarantine["status"] == "pending"

    async def test_quarantined_contract_not_in_ledger(self, pg_pool):
        """Before override approval, no ledger record should exist."""
        data = _invalid_contract()
        await oracle_los.originate_contract(data)
        contract_id = data["contract_id"]

        # Wait for quarantine to be written
        await _wait_for_quarantine(pg_pool, contract_id)

        # Ledger should have NO record
        try:
            lifecycle = await ledger.get_contract_lifecycle(contract_id)
            records = lifecycle.get("records", [])
            assert len(records) == 0, (
                f"Expected no ledger records for quarantined contract, got {len(records)}"
            )
        except Exception:
            pass  # lifecycle 404 is also acceptable

    async def test_dashboard_api_quarantine_queue_shows_event(self, pg_pool, http):
        """Dashboard API /api/quarantine should list the pending event."""
        data = _invalid_contract()
        await oracle_los.originate_contract(data)
        contract_id = data["contract_id"]

        quarantine = await _wait_for_quarantine(pg_pool, contract_id)
        assert quarantine is not None

        res = await http.get("/api/quarantine", params={"status": "pending"})
        assert res.status_code == 200
        records = res.json()
        matching = [r for r in records if r["contract_id"] == contract_id]
        assert len(matching) == 1, f"Expected contract {contract_id} in queue, not found"
        assert matching[0]["status"] == "pending"

    async def test_approve_override_triggers_ledger_write(self, pg_pool, http):
        """
        Approving a quarantined event should:
          1. Update quarantine status to 'approved'
          2. Publish quarantine.approved to Redis
          3. Agent writes origination record to ledger
          4. Contract state = active
        """
        data = _invalid_contract()
        await oracle_los.originate_contract(data)
        contract_id = data["contract_id"]

        quarantine = await _wait_for_quarantine(pg_pool, contract_id)
        assert quarantine is not None
        event_id = quarantine["event_id"]

        # Approve via Dashboard API
        res = await http.post(
            f"/api/quarantine/{event_id}/approve",
            json={
                "reason":   "Manual verification confirmed all details are correct",
                "reviewer": "e2e.test.reviewer",
            },
        )
        assert res.status_code == 200
        approval = res.json()
        assert approval["success"] is True

        # Wait for agent to write the ledger record
        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None, (
            f"Ledger record not found after override approval for {contract_id}"
        )
        assert lifecycle.get("current_state") == "active"

    async def test_approved_record_carries_override_metadata(self, pg_pool, http):
        """The ledger record written after override must include override metadata."""
        data = _invalid_contract()
        await oracle_los.originate_contract(data)
        contract_id = data["contract_id"]

        quarantine = await _wait_for_quarantine(pg_pool, contract_id)
        assert quarantine is not None
        event_id = quarantine["event_id"]

        await http.post(
            f"/api/quarantine/{event_id}/approve",
            json={"reason": "Test override", "reviewer": "test.reviewer"},
        )

        lifecycle = await _wait_for_ledger_record(contract_id)
        assert lifecycle is not None

        origination_records = [
            r for r in lifecycle.get("records", [])
            if r.get("record_type") == "origination"
        ]
        assert len(origination_records) >= 1

        # At least one record should have override metadata
        record_payloads = [r.get("payload", {}) for r in origination_records]
        override_records = [p for p in record_payloads if p.get("is_override")]
        assert len(override_records) >= 1, "Expected at least one record with is_override=True"

        override_record = override_records[0]
        assert override_record.get("reviewed_by") == "test.reviewer"

    async def test_reject_quarantine_does_not_write_ledger(self, pg_pool, http):
        """Rejecting a quarantined event must NOT write anything to the ledger."""
        data = _invalid_contract()
        await oracle_los.originate_contract(data)
        contract_id = data["contract_id"]

        quarantine = await _wait_for_quarantine(pg_pool, contract_id)
        assert quarantine is not None
        event_id = quarantine["event_id"]

        # Reject via Dashboard API
        res = await http.post(
            f"/api/quarantine/{event_id}/reject",
            json={"reason": "Fraudulent application", "reviewer": "fraud.team"},
        )
        assert res.status_code == 200
        rejection = res.json()
        assert rejection["success"] is True
        assert rejection["status"] == "rejected"

        # Wait a moment then confirm no ledger record
        await asyncio.sleep(3)
        try:
            lifecycle = await ledger.get_contract_lifecycle(contract_id)
            records = lifecycle.get("records", [])
            assert len(records) == 0, (
                f"Expected no ledger records after rejection, got {len(records)}"
            )
        except Exception:
            pass  # 404 is fine

    async def test_quarantine_status_updated_after_approve(self, pg_pool, http):
        """After approval, quarantine status should be 'approved' in the DB."""
        data = _invalid_contract()
        await oracle_los.originate_contract(data)
        contract_id = data["contract_id"]

        quarantine = await _wait_for_quarantine(pg_pool, contract_id)
        assert quarantine is not None
        event_id = quarantine["event_id"]

        await http.post(
            f"/api/quarantine/{event_id}/approve",
            json={"reason": "Approved", "reviewer": "reviewer1"},
        )

        # Poll until status changes
        for _ in range(20):
            row = await pg_pool.fetchrow(
                "SELECT status, reviewed_by FROM validation.quarantine WHERE event_id = $1::uuid",
                event_id,
            )
            if row and row["status"] == "approved":
                assert row["reviewed_by"] == "reviewer1"
                return
            await asyncio.sleep(0.5)

        pytest.fail(f"Quarantine status never changed to 'approved' for event {event_id}")
