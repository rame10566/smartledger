"""
Unit tests for the Oracle LOS simulator.

Tests are pure Python — no Redis required.
Redis publish is mocked to verify event shape without network calls.
"""

import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# ─── Module-level patch: prevent real Redis connection on import ───────────────
# We access module globals directly after seeding in fixtures.

import sys
import importlib


@pytest.fixture(autouse=True)
def reset_oracle_los_state():
    """Reset oracle_los module state between tests."""
    # Import fresh state access (avoid re-import side effects)
    from mcp_servers.simulated.oracle_los import server as oracle
    oracle._contracts.clear()
    oracle._seq = 1
    # Seed contracts manually (bypass lifespan)
    for c in oracle._SEED_CONTRACTS:
        oracle._contracts[c["contract_id"]] = dict(c)
    oracle._seq = len(oracle._SEED_CONTRACTS) + 1
    yield
    oracle._contracts.clear()


@pytest.fixture
def mock_redis():
    """Provide a mock Redis client injected into the oracle_los module."""
    from mcp_servers.simulated.oracle_los import server as oracle
    mock = AsyncMock()
    mock.xadd = AsyncMock(return_value="1234567890-0")
    mock.ping = AsyncMock(return_value=True)
    oracle._redis = mock
    yield mock
    oracle._redis = None


# ─── Tests: originate_contract ────────────────────────────────────────────────

class TestOriginateContract:

    @pytest.mark.asyncio
    async def test_success_creates_contract_and_publishes_event(self, mock_redis):
        from mcp_servers.simulated.oracle_los.server import originate_contract

        result = await originate_contract({
            "contract_type": "loan",
            "customer": {
                "customer_id": "CUST-TEST",
                "first_name": "Alice",
                "last_name": "Test",
                "credit_score": 700,
                "credit_tier": "prime",
            },
            "vehicle": {
                "vin": "4T1BF3EK8AU138001",
                "make": "Toyota",
                "model": "Corolla",
                "year": 2024,
            },
            "financial_terms": {
                "amount_financed": 20000.00,
                "term_months": 60,
                "interest_rate": 5.99,
                "monthly_payment": 386.66,
            },
            "dealer_id": "DLR-TEST",
        })

        assert result["success"] is True
        assert result["contract_id"].startswith("ORC-")
        assert result["correlation_id"] is not None
        assert result["contract"]["state"] == "originated"
        assert result["contract"]["vehicle"]["vin"] == "4T1BF3EK8AU138001"

        # Verify Redis event was published
        mock_redis.xadd.assert_awaited_once()
        call_args = mock_redis.xadd.call_args
        stream_key = call_args[0][0]
        message = call_args[0][1]

        assert stream_key == "smartledger:events"
        assert message["event_type"] == "contract.originated"
        assert message["source_system"] == "oracle_los"
        assert message["contract_id"] == result["contract_id"]

        # Payload must be valid JSON
        payload = json.loads(message["payload"])
        assert payload["vehicle"]["vin"] == "4T1BF3EK8AU138001"

    @pytest.mark.asyncio
    async def test_contract_stored_in_memory(self, mock_redis):
        from mcp_servers.simulated.oracle_los import server as oracle
        initial_count = len(oracle._contracts)

        await oracle.originate_contract({
            "contract_type": "lease",
            "customer": {"customer_id": "C-X", "first_name": "X", "last_name": "Y",
                         "credit_score": 800, "credit_tier": "prime"},
            "vehicle": {"vin": "5YJSA1DG9DFP14705", "make": "Tesla", "model": "Model S", "year": 2024},
            "financial_terms": {"amount_financed": 60000.0, "term_months": 36,
                                 "interest_rate": 3.99, "monthly_payment": 999.0},
            "dealer_id": "DLR-EV",
        })

        assert len(oracle._contracts) == initial_count + 1

    @pytest.mark.asyncio
    async def test_invalid_vin_raises_value_error(self, mock_redis):
        from mcp_servers.simulated.oracle_los.server import originate_contract

        with pytest.raises(ValueError, match="Invalid VIN"):
            await originate_contract({
                "contract_type": "loan",
                "customer": {"customer_id": "C1", "first_name": "A", "last_name": "B",
                             "credit_score": 700, "credit_tier": "prime"},
                "vehicle": {"vin": "INVALID", "make": "Ford", "model": "F-150", "year": 2024},
                "financial_terms": {"amount_financed": 30000.0, "term_months": 60,
                                    "interest_rate": 7.0, "monthly_payment": 594.0},
                "dealer_id": "DLR-001",
            })

    @pytest.mark.asyncio
    async def test_vin_with_illegal_chars_raises(self, mock_redis):
        """VIN cannot contain I, O, or Q."""
        from mcp_servers.simulated.oracle_los.server import originate_contract

        with pytest.raises(ValueError, match="Invalid VIN"):
            await originate_contract({
                "contract_type": "loan",
                "customer": {"customer_id": "C1", "first_name": "A", "last_name": "B",
                             "credit_score": 700, "credit_tier": "prime"},
                "vehicle": {"vin": "1IOQBH41JXMN10918", "make": "Ford",
                            "model": "F-150", "year": 2024},
                "financial_terms": {"amount_financed": 30000.0, "term_months": 60,
                                    "interest_rate": 7.0, "monthly_payment": 594.0},
                "dealer_id": "DLR-001",
            })

    @pytest.mark.asyncio
    async def test_invalid_contract_type_raises(self, mock_redis):
        from mcp_servers.simulated.oracle_los.server import originate_contract

        with pytest.raises(ValueError, match="Invalid contract_type"):
            await originate_contract({
                "contract_type": "subscription",   # invalid
                "customer": {"customer_id": "C1", "first_name": "A", "last_name": "B",
                             "credit_score": 700, "credit_tier": "prime"},
                "vehicle": {"vin": "4T1BF3EK8AU138001", "make": "Toyota",
                            "model": "Camry", "year": 2024},
                "financial_terms": {"amount_financed": 20000.0, "term_months": 60,
                                    "interest_rate": 5.99, "monthly_payment": 386.0},
                "dealer_id": "DLR-001",
            })

    @pytest.mark.asyncio
    async def test_missing_required_fields_raises(self, mock_redis):
        from mcp_servers.simulated.oracle_los.server import originate_contract

        with pytest.raises(ValueError, match="Missing required fields"):
            await originate_contract({"contract_type": "loan"})  # missing customer, vehicle, etc.


# ─── Tests: get_contract ──────────────────────────────────────────────────────

class TestGetContract:

    @pytest.mark.asyncio
    async def test_returns_seeded_contract(self):
        from mcp_servers.simulated.oracle_los.server import get_contract

        result = await get_contract("ORC-2024-001")
        assert result["contract_id"] == "ORC-2024-001"
        assert result["vehicle"]["vin"] == "1HGBH41JXMN109186"
        assert result["los_system"] == "oracle_los"

    @pytest.mark.asyncio
    async def test_not_found_raises(self):
        from mcp_servers.simulated.oracle_los.server import get_contract

        with pytest.raises(ValueError, match="not found"):
            await get_contract("ORC-DOES-NOT-EXIST")


# ─── Tests: get_contracts ─────────────────────────────────────────────────────

class TestGetContracts:

    @pytest.mark.asyncio
    async def test_returns_all_seeded(self):
        from mcp_servers.simulated.oracle_los.server import get_contracts

        result = await get_contracts()
        assert len(result) == 3  # 3 seed contracts

    @pytest.mark.asyncio
    async def test_filter_by_state(self):
        from mcp_servers.simulated.oracle_los.server import get_contracts

        result = await get_contracts({"state": "originated"})
        assert all(c["state"] == "originated" for c in result)
        assert len(result) == 1  # ORC-2024-003

    @pytest.mark.asyncio
    async def test_filter_by_contract_type(self):
        from mcp_servers.simulated.oracle_los.server import get_contracts

        result = await get_contracts({"contract_type": "lease"})
        assert all(c["contract_type"] == "lease" for c in result)
        assert len(result) == 1  # ORC-2024-002

    @pytest.mark.asyncio
    async def test_filter_no_match_returns_empty(self):
        from mcp_servers.simulated.oracle_los.server import get_contracts

        result = await get_contracts({"dealer_id": "DLR-NONEXISTENT"})
        assert result == []


# ─── Tests: amend_contract ────────────────────────────────────────────────────

class TestAmendContract:

    @pytest.mark.asyncio
    async def test_amend_updates_field(self):
        from mcp_servers.simulated.oracle_los.server import amend_contract

        result = await amend_contract("ORC-2024-001", {"state": "delinquent"})
        assert result["success"] is True
        assert result["contract"]["state"] == "delinquent"

    @pytest.mark.asyncio
    async def test_amend_deep_merges_dict(self):
        from mcp_servers.simulated.oracle_los.server import amend_contract

        result = await amend_contract(
            "ORC-2024-001",
            {"financial_terms": {"interest_rate": 7.50}}
        )
        ft = result["contract"]["financial_terms"]
        # interest_rate updated, other fields preserved
        assert ft["interest_rate"] == 7.50
        assert ft["amount_financed"] == 28500.00  # unchanged

    @pytest.mark.asyncio
    async def test_amend_not_found_raises(self):
        from mcp_servers.simulated.oracle_los.server import amend_contract

        with pytest.raises(ValueError, match="not found"):
            await amend_contract("NONEXISTENT", {"state": "active"})
