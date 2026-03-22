"""
Unit tests for the Validation Engine MCP server.

All database and Redis interactions are mocked.
Tests focus on the core validation logic and proof token issuance.
"""

import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_origination_request(
    contract_id: str | None = None,
    vin: str = "4T1BF3EK8AU138001",
    amount_financed: float = 25000.0,
    term_months: int = 60,
    interest_rate: float = 6.99,
    monthly_payment: float = 483.0,
    dealer_id: str = "DLR-TEST",
    oracle_los_contract: dict | None = None,
    llas_account: dict | None = None,
) -> dict:
    """Build a minimal valid ValidationRequest for contract.originated."""
    cid = contract_id or f"ORC-2024-{uuid4().hex[:4].upper()}"
    return {
        "event_envelope": {
            "event_id": str(uuid4()),
            "event_type": "contract.originated",
            "source_system": "oracle_los",
            "contract_id": cid,
            "timestamp": "2026-03-14T10:00:00Z",
            "correlation_id": str(uuid4()),
            "schema_version": "1.0",
            "payload": {
                "contract_id": cid,
                "los_system": "oracle_los",
                "contract_type": "loan",
                "origination_date": "2026-03-14",
                "state": "originated",
                "customer": {
                    "customer_id": "CUST-T01",
                    "first_name": "Test",
                    "last_name": "User",
                    "credit_score": 700,
                    "credit_tier": "prime",
                },
                "vehicle": {
                    "vin": vin,
                    "make": "Toyota",
                    "model": "Camry",
                    "year": 2024,
                },
                "financial_terms": {
                    "amount_financed": amount_financed,
                    "term_months": term_months,
                    "interest_rate": interest_rate,
                    "monthly_payment": monthly_payment,
                    "down_payment": 2000.0,
                },
                "dealer_id": dealer_id,
            },
        },
        "saga_id": str(uuid4()),
        "context": {
            "oracle_los_contract": oracle_los_contract,
            "llas_account": llas_account,
        },
    }


# ─── Tests: validation logic ──────────────────────────────────────────────────

class TestValidationLogic:
    """Tests for _validate_origination — pure Python, no DB needed."""

    def test_valid_payload_returns_no_failures(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "vehicle": {"vin": "4T1BF3EK8AU138001"},
            "financial_terms": {
                "amount_financed": 25000.0,
                "term_months": 60,
                "interest_rate": 6.99,
                "monthly_payment": 483.0,
            },
            "dealer_id": "DLR-001",
        }
        failures = _validate_origination(payload, {})
        assert failures == []

    def test_invalid_vin_returns_failure(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "vehicle": {"vin": "BADVIN"},
            "financial_terms": {
                "amount_financed": 25000.0, "term_months": 60,
                "interest_rate": 6.99, "monthly_payment": 483.0,
            },
            "dealer_id": "DLR-001",
        }
        failures = _validate_origination(payload, {})
        codes = [f["code"] for f in failures]
        assert "INVALID_VIN_FORMAT" in codes

    def test_zero_amount_financed_fails(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "vehicle": {"vin": "4T1BF3EK8AU138001"},
            "financial_terms": {
                "amount_financed": 0,
                "term_months": 60,
                "interest_rate": 6.99,
                "monthly_payment": 483.0,
            },
            "dealer_id": "DLR-001",
        }
        failures = _validate_origination(payload, {})
        codes = [f["code"] for f in failures]
        assert "INVALID_AMOUNT_FINANCED" in codes

    def test_term_too_long_fails(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "vehicle": {"vin": "4T1BF3EK8AU138001"},
            "financial_terms": {
                "amount_financed": 25000.0,
                "term_months": 120,  # over 84 max
                "interest_rate": 6.99,
                "monthly_payment": 483.0,
            },
            "dealer_id": "DLR-001",
        }
        failures = _validate_origination(payload, {})
        codes = [f["code"] for f in failures]
        assert "INVALID_TERM_MONTHS" in codes

    def test_rate_too_high_fails(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "vehicle": {"vin": "4T1BF3EK8AU138001"},
            "financial_terms": {
                "amount_financed": 25000.0,
                "term_months": 60,
                "interest_rate": 45.0,  # over 36% max
                "monthly_payment": 483.0,
            },
            "dealer_id": "DLR-001",
        }
        failures = _validate_origination(payload, {})
        codes = [f["code"] for f in failures]
        assert "INVALID_INTEREST_RATE" in codes

    def test_missing_dealer_id_fails(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "vehicle": {"vin": "4T1BF3EK8AU138001"},
            "financial_terms": {
                "amount_financed": 25000.0, "term_months": 60,
                "interest_rate": 6.99, "monthly_payment": 483.0,
            },
            "dealer_id": "",   # empty
        }
        failures = _validate_origination(payload, {})
        codes = [f["code"] for f in failures]
        assert "MISSING_DEALER_ID" in codes

    def test_vin_mismatch_with_oracle_los_fails(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "vehicle": {"vin": "4T1BF3EK8AU138001"},   # event VIN
            "financial_terms": {
                "amount_financed": 25000.0, "term_months": 60,
                "interest_rate": 6.99, "monthly_payment": 483.0,
            },
            "dealer_id": "DLR-001",
        }
        context = {
            "oracle_los_contract": {
                "found": True,
                "vehicle": {"vin": "1HGBH41JXMN109186"},  # different VIN in LOS
            }
        }
        failures = _validate_origination(payload, context)
        codes = [f["code"] for f in failures]
        assert "VIN_MISMATCH" in codes

    def test_vin_matches_oracle_los_no_failure(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "vehicle": {"vin": "4T1BF3EK8AU138001"},
            "financial_terms": {
                "amount_financed": 25000.0, "term_months": 60,
                "interest_rate": 6.99, "monthly_payment": 483.0,
            },
            "dealer_id": "DLR-001",
        }
        context = {
            "oracle_los_contract": {
                "found": True,
                "vehicle": {"vin": "4T1BF3EK8AU138001"},  # same VIN ✓
            }
        }
        failures = _validate_origination(payload, context)
        codes = [f["code"] for f in failures]
        assert "VIN_MISMATCH" not in codes

    def test_existing_llas_account_flags_duplicate(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "contract_id": "ORC-2024-001",
            "vehicle": {"vin": "4T1BF3EK8AU138001"},
            "financial_terms": {
                "amount_financed": 25000.0, "term_months": 60,
                "interest_rate": 6.99, "monthly_payment": 483.0,
            },
            "dealer_id": "DLR-001",
        }
        context = {
            "llas_account": {
                "found": True,              # account already exists!
                "account_number": "LLAS-ORC-2024-001",
            }
        }
        failures = _validate_origination(payload, context)
        codes = [f["code"] for f in failures]
        assert "DUPLICATE_ORIGINATION" in codes

    def test_multiple_failures_returned(self):
        from mcp_servers.validation.server import _validate_origination

        payload = {
            "vehicle": {"vin": "BADVIN"},         # bad VIN
            "financial_terms": {
                "amount_financed": -100.0,          # bad amount
                "term_months": 0,                   # bad term
                "interest_rate": 6.99,
                "monthly_payment": 0.0,             # bad payment
            },
            "dealer_id": "",                        # missing dealer
        }
        failures = _validate_origination(payload, {})
        assert len(failures) >= 4


# ─── Tests: proof token issuance ──────────────────────────────────────────────

class TestProofToken:

    def test_proof_token_is_valid_jwt(self):
        import jwt as pyjwt
        from mcp_servers.validation.server import _issue_proof_token
        from shared.config import get_settings

        settings = get_settings()
        contract_id = "ORC-2024-001"
        event_id = str(uuid4())
        saga_id = str(uuid4())

        token, jti = _issue_proof_token(contract_id, event_id, saga_id)

        assert token is not None
        assert len(jti) == 36   # UUID4 format

        # Decode and verify claims
        claims = pyjwt.decode(token, settings.proof_token_secret, algorithms=["HS256"])
        assert claims["contract_id"] == contract_id
        assert claims["event_id"] == event_id
        assert claims["saga_id"] == saga_id
        assert claims["jti"] == jti
        assert claims["exp"] > claims["iat"]
        assert claims["exp"] - claims["iat"] == settings.proof_token_expiry_seconds

    def test_two_tokens_have_different_jtis(self):
        from mcp_servers.validation.server import _issue_proof_token

        _, jti1 = _issue_proof_token("ORC-001", str(uuid4()), str(uuid4()))
        _, jti2 = _issue_proof_token("ORC-001", str(uuid4()), str(uuid4()))
        assert jti1 != jti2

    def test_token_expires_after_expiry(self):
        """Verify token is invalid after expiry window (we fake time)."""
        import jwt as pyjwt
        from shared.config import get_settings

        settings = get_settings()

        # Create a token that expired 10s ago
        jti = str(uuid4())
        now = int(time.time())
        claims = {
            "jti": jti,
            "contract_id": "ORC-001",
            "event_id": str(uuid4()),
            "saga_id": str(uuid4()),
            "iat": now - 70,   # 70s ago
            "exp": now - 10,   # expired 10s ago
        }
        expired_token = pyjwt.encode(claims, settings.proof_token_secret, algorithm="HS256")

        with pytest.raises(pyjwt.ExpiredSignatureError):
            pyjwt.decode(expired_token, settings.proof_token_secret, algorithms=["HS256"])


# ─── Tests: validate_event tool (integrated, mocked DB) ───────────────────────

class TestValidateEventTool:

    @pytest.fixture(autouse=True)
    def mock_db_and_redis(self):
        """Mock the asyncpg pool and Redis client in the validation server."""
        from mcp_servers.validation import server as val

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        # conn.transaction() is a sync call returning an async context manager
        mock_txn = MagicMock()
        mock_txn.__aenter__ = AsyncMock(return_value=mock_txn)
        mock_txn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction = MagicMock(return_value=mock_txn)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_conn

        val._pool = mock_pool
        val._redis = None   # don't need Redis for validate_event

        yield mock_pool, mock_conn

        val._pool = None

    @pytest.mark.asyncio
    async def test_valid_event_returns_proof_token(self):
        from mcp_servers.validation.server import validate_event

        request = _make_origination_request()
        result = await validate_event(request)

        assert result["valid"] is True
        assert result["proof_token"] is not None
        assert len(result["failures"]) == 0

    @pytest.mark.asyncio
    async def test_invalid_event_returns_failures_no_token(self):
        from mcp_servers.validation.server import validate_event

        request = _make_origination_request(vin="BADVIN")
        result = await validate_event(request)

        assert result["valid"] is False
        assert result["proof_token"] is None
        assert len(result["failures"]) > 0
        codes = [f["code"] for f in result["failures"]]
        assert "INVALID_VIN_FORMAT" in codes

    @pytest.mark.asyncio
    async def test_invalid_amount_quarantines_event(self, mock_db_and_redis):
        from mcp_servers.validation.server import validate_event
        mock_pool, mock_conn = mock_db_and_redis

        request = _make_origination_request(amount_financed=0.0)
        result = await validate_event(request)

        assert result["valid"] is False
        # Verify quarantine INSERT was called
        mock_conn.execute.assert_awaited()
        sql_call = mock_conn.execute.call_args_list[0][0][0]
        assert "validation.quarantine" in sql_call.lower()

    @pytest.mark.asyncio
    async def test_result_contains_expected_fields(self):
        from mcp_servers.validation.server import validate_event

        request = _make_origination_request()
        result = await validate_event(request)

        required_keys = {"valid", "event_id", "contract_id", "saga_id", "checked_at",
                         "proof_token", "failures", "warnings"}
        assert required_keys.issubset(result.keys())

    @pytest.mark.asyncio
    async def test_unhandled_event_type_passes_with_warning(self):
        from mcp_servers.validation.server import validate_event

        request = _make_origination_request()
        request["event_envelope"]["event_type"] = "report.requested"

        result = await validate_event(request)
        assert result["valid"] is True
        assert len(result["warnings"]) > 0
        assert any("UNHANDLED_EVENT_TYPE" in w["code"] for w in result["warnings"])
