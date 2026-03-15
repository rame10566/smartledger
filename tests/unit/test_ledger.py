"""
Unit tests for the Ledger MCP server.

Focuses on:
  - Proof token verification (valid, expired, replayed, mismatched)
  - write_record happy path
  - execute_state_transition
  - calculate_late_fee (formula-based, no DB needed)
"""

import hashlib
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt as pyjwt


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def settings():
    from shared.config import get_settings
    return get_settings()


@pytest.fixture
def valid_token_factory(settings):
    """Factory that creates a fresh valid proof token for a given contract_id."""
    def _make(contract_id: str, event_id: str | None = None, saga_id: str | None = None) -> tuple[str, str]:
        jti = str(uuid4())
        now = int(time.time())
        claims = {
            "jti": jti,
            "contract_id": contract_id,
            "event_id": event_id or str(uuid4()),
            "saga_id": saga_id or str(uuid4()),
            "iat": now,
            "exp": now + 60,
        }
        token = pyjwt.encode(claims, settings.proof_token_secret, algorithm="HS256")
        return token, jti
    return _make


@pytest.fixture
def expired_token_factory(settings):
    """Factory that creates an already-expired proof token."""
    def _make(contract_id: str) -> str:
        now = int(time.time())
        claims = {
            "jti": str(uuid4()),
            "contract_id": contract_id,
            "event_id": str(uuid4()),
            "saga_id": str(uuid4()),
            "iat": now - 120,
            "exp": now - 60,   # expired 60s ago
        }
        return pyjwt.encode(claims, settings.proof_token_secret, algorithm="HS256")
    return _make


@pytest.fixture
def mock_pool_no_replay():
    """Mock DB pool that simulates: jti not seen before (no replay)."""
    mock_conn = AsyncMock()
    # fetchval returns None → jti not in used_proof_tokens
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=False),
    ))
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=mock_conn)
    return pool, mock_conn


@pytest.fixture
def mock_pool_with_replay():
    """Mock DB pool that simulates: jti already used (replay attack)."""
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value="already-used-jti")  # jti found!
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=mock_conn)
    return pool, mock_conn


# ─── Tests: _verify_proof_token ───────────────────────────────────────────────

class TestVerifyProofToken:

    @pytest.mark.asyncio
    async def test_valid_token_returns_claims(self, valid_token_factory, mock_pool_no_replay):
        from mcp_servers.ledger import server as ledger
        pool, _ = mock_pool_no_replay
        ledger._pool = pool

        token, jti = valid_token_factory("ORC-2024-001")
        claims = await ledger._verify_proof_token(token, "ORC-2024-001")

        assert claims["contract_id"] == "ORC-2024-001"
        assert claims["jti"] == jti

        ledger._pool = None

    @pytest.mark.asyncio
    async def test_expired_token_raises(self, expired_token_factory, mock_pool_no_replay):
        from mcp_servers.ledger import server as ledger
        pool, _ = mock_pool_no_replay
        ledger._pool = pool

        token = expired_token_factory("ORC-2024-001")
        with pytest.raises(ValueError, match="expired"):
            await ledger._verify_proof_token(token, "ORC-2024-001")

        ledger._pool = None

    @pytest.mark.asyncio
    async def test_contract_id_mismatch_raises(self, valid_token_factory, mock_pool_no_replay):
        from mcp_servers.ledger import server as ledger
        pool, _ = mock_pool_no_replay
        ledger._pool = pool

        token, _ = valid_token_factory("ORC-2024-001")
        with pytest.raises(ValueError, match="contract_id"):
            # token was issued for ORC-2024-001 but we pass ORC-2024-999
            await ledger._verify_proof_token(token, "ORC-2024-999")

        ledger._pool = None

    @pytest.mark.asyncio
    async def test_replayed_token_raises(self, valid_token_factory, mock_pool_with_replay):
        from mcp_servers.ledger import server as ledger
        pool, _ = mock_pool_with_replay
        ledger._pool = pool

        token, _ = valid_token_factory("ORC-2024-001")
        with pytest.raises(ValueError, match="already been used"):
            await ledger._verify_proof_token(token, "ORC-2024-001")

        ledger._pool = None

    @pytest.mark.asyncio
    async def test_tampered_token_raises(self, mock_pool_no_replay):
        from mcp_servers.ledger import server as ledger
        pool, _ = mock_pool_no_replay
        ledger._pool = pool

        tampered = "eyJhbGciOiJIUzI1NiJ9.eyJjb250cmFjdF9pZCI6IkZBS0UifQ.invalidsig"
        with pytest.raises(ValueError, match="Invalid proof token"):
            await ledger._verify_proof_token(tampered, "ORC-2024-001")

        ledger._pool = None


# ─── Tests: write_record ──────────────────────────────────────────────────────

class TestWriteRecord:

    @pytest.fixture(autouse=True)
    def setup_pool(self, mock_pool_no_replay):
        from mcp_servers.ledger import server as ledger
        pool, conn = mock_pool_no_replay
        # transaction() is called inside write_record
        conn.transaction = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        ))
        ledger._pool = pool
        yield pool, conn
        ledger._pool = None

    @pytest.mark.asyncio
    async def test_write_record_success(self, setup_pool, valid_token_factory):
        from mcp_servers.ledger.server import write_record

        token, jti = valid_token_factory("ORC-2024-001")
        record = {
            "record_id": str(uuid4()),
            "contract_id": "ORC-2024-001",
            "record_type": "origination",
            "saga_id": str(uuid4()),
            "proof_token_jti": jti,
            "vehicle": {"vin": "4T1BF3EK8AU138001"},
            "financial_terms": {"amount_financed": 25000.0},
        }

        result = await write_record(record, token)

        assert result["success"] is True
        assert result["contract_id"] == "ORC-2024-001"
        assert result["data_hash"] is not None
        assert len(result["data_hash"]) == 64  # SHA-256 hex

    @pytest.mark.asyncio
    async def test_write_record_with_expired_token_fails(self, setup_pool, expired_token_factory):
        from mcp_servers.ledger.server import write_record

        token = expired_token_factory("ORC-2024-001")
        record = {
            "record_id": str(uuid4()),
            "contract_id": "ORC-2024-001",
            "record_type": "origination",
            "saga_id": str(uuid4()),
        }

        with pytest.raises(ValueError, match="expired"):
            await write_record(record, token)


# ─── Tests: calculate_late_fee ────────────────────────────────────────────────

class TestCalculateLateFee:

    @pytest.fixture(autouse=True)
    def setup_pool(self):
        from mcp_servers.ledger import server as ledger
        # Mock pool to return monthly_payment from origination record
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "payload": json.dumps({"monthly_payment": 487.50})
        })
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        pool = MagicMock()
        pool.acquire = MagicMock(return_value=mock_conn)
        ledger._pool = pool
        yield
        ledger._pool = None

    @pytest.mark.asyncio
    async def test_zero_days_no_fee(self):
        from mcp_servers.ledger.server import calculate_late_fee

        result = await calculate_late_fee("ORC-2024-001", 0)
        assert result["late_fee"] == 0.0

    @pytest.mark.asyncio
    async def test_1_to_14_days_flat_25(self):
        from mcp_servers.ledger.server import calculate_late_fee

        result = await calculate_late_fee("ORC-2024-001", 7)
        assert result["late_fee"] == 25.00
        assert result["fee_tier"] == "1-14_days"

    @pytest.mark.asyncio
    async def test_15_to_29_days_flat_50(self):
        from mcp_servers.ledger.server import calculate_late_fee

        result = await calculate_late_fee("ORC-2024-001", 20)
        assert result["late_fee"] == 50.00
        assert result["fee_tier"] == "15-29_days"

    @pytest.mark.asyncio
    async def test_30_plus_days_pct_of_payment(self):
        from mcp_servers.ledger.server import calculate_late_fee

        # monthly_payment = 487.50 → 5% = 24.375 → max(50, 24.375) = 50.00
        result = await calculate_late_fee("ORC-2024-001", 35)
        assert result["late_fee"] == 50.00  # floor 50
        assert result["fee_tier"] == "30+_days"

    @pytest.mark.asyncio
    async def test_30_plus_days_high_payment_uses_pct(self):
        """With a high monthly payment, 5% exceeds the $50 floor."""
        from mcp_servers.ledger import server as ledger

        # Override mock to return high payment
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "payload": json.dumps({"monthly_payment": 1200.0})
        })
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=mock_conn)
        ledger._pool = pool

        result = await ledger.calculate_late_fee("ORC-2024-001", 45)
        # 5% of 1200 = 60 > 50 → fee = 60
        assert result["late_fee"] == 60.00


# ─── Tests: governance rules ──────────────────────────────────────────────────

class TestGetGovernanceRules:

    @pytest.mark.asyncio
    async def test_returns_rule_set(self):
        from mcp_servers.ledger.server import get_governance_rules

        result = await get_governance_rules()
        assert result["rules"]["proof_token_required"] is True
        assert result["rules"]["single_use_tokens"] is True
        assert result["rules"]["pii_on_chain"] is False
        assert isinstance(result["rules"]["valid_states"], list)
