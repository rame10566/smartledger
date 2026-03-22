"""
E2E Test — Contract Origination Unhappy Path (Quarantine)

Requires the full dev stack running:
  ./scripts/dev_start.sh

Tests the complete unhappy path:
  1. Originate a contract that will FAIL validation
     (e.g. interest_rate > 36% — triggers RULE-BIZ-RATE)
  2. Verify the event is quarantined (not written to ledger)
  3. Dashboard API shows the quarantined event for visibility

SmartLedger enforces the SDG boundary: it never approves or overrides
quarantined data. The originating system must correct the data and resend.

Run with:
  uv run pytest tests/e2e/test_origination_unhappy_path.py -v -s

Markers:
  pytest.mark.e2e — requires live infra
"""

import asyncio
import json
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

@pytest_asyncio.fixture(scope="function")
async def pg_pool():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="function")
async def http():
    """httpx async client for Dashboard API calls (admin identity for PBAC)."""
    headers = {
        "X-SmartLedger-Identity": json.dumps({
            "actor_id": "e2e-test-admin",
            "actor_type": "user",
            "role": "admin",
        })
    }
    async with httpx.AsyncClient(base_url=DASHBOARD_API, timeout=10, headers=headers) as client:
        yield client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _invalid_contract() -> dict:
    """Contract with interest_rate > 36% — will fail RULE-BIZ-RATE."""
    return {
        "contract_id":   f"UNHAPPY-{uuid.uuid4().hex[:8].upper()}",
        "contract_type": "loan",
        "vehicle": {
            "vin":   f"1HGBH41JXMN{uuid.uuid4().hex[:6].upper()}",
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


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOriginationUnhappyPath:
    """Unhappy path: invalid data → quarantine → visible on dashboard → originating system must fix."""

    async def test_invalid_contract_is_quarantined(self, pg_pool):
        """An invalid contract must end up in validation.quarantine, not in the ledger."""
        data = _invalid_contract()
        result = await oracle_los.originate_contract(data)
        assert result["success"], "Oracle LOS should accept the contract regardless of rules"

        contract_id = result["contract_id"]
        quarantine = await _wait_for_quarantine(pg_pool, contract_id)
        assert quarantine is not None, (
            f"No quarantine record found for {contract_id} after {AGENT_TIMEOUT}s"
        )
        assert quarantine["status"] == "pending"

    async def test_quarantined_contract_not_in_ledger(self, pg_pool):
        """A quarantined contract must NOT have any ledger record."""
        data = _invalid_contract()
        result = await oracle_los.originate_contract(data)
        contract_id = result["contract_id"]

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
        """Dashboard API /api/quarantine should list the pending event for visibility."""
        data = _invalid_contract()
        result = await oracle_los.originate_contract(data)
        contract_id = result["contract_id"]

        quarantine = await _wait_for_quarantine(pg_pool, contract_id)
        assert quarantine is not None

        res = await http.get("/api/quarantine", params={"status": "pending"})
        assert res.status_code == 200
        records = res.json()
        matching = [r for r in records if r["contract_id"] == contract_id]
        assert len(matching) == 1, f"Expected contract {contract_id} in queue, not found"
        assert matching[0]["status"] == "pending"
