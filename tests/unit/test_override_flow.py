"""
Unit tests for agent.flows.override — OverrideFlow.

All MCP calls are mocked so no live servers are needed.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.flows.override import OverrideFlow
from agent.core.saga import SagaManager
from shared.models.saga import SagaStep


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def contract_id() -> str:
    return "ORC-2024-TEST"


@pytest.fixture
def original_event_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def approval_event_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def mock_saga(contract_id, approval_event_id):
    saga = MagicMock()
    saga.saga_id    = str(uuid.uuid4())
    saga.contract_id = contract_id
    saga.event_id   = approval_event_id
    saga.checkpoint = AsyncMock()
    saga.complete   = AsyncMock()
    saga.quarantine = AsyncMock()
    saga.fail       = AsyncMock()
    return saga


def _make_approval_event(contract_id: str, original_event_id: str, approval_event_id: str) -> dict:
    return {
        "event_id":      approval_event_id,
        "event_type":    "quarantine.approved",
        "contract_id":   contract_id,
        "source_system": "dashboard",
        "timestamp":     "2026-03-15T10:00:00Z",
        "correlation_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "payload": {
            "original_event_id":  original_event_id,
            "contract_id":        contract_id,
            "override_reason":    "VIN confirmed via manual check",
            "reviewed_by":        "jane.doe",
            "original_payload": {
                "contract_type": "loan",
                "financial_terms": {
                    "amount_financed": 25_000,
                    "term_months":     60,
                    "monthly_payment": 483.15,
                },
                "origination_date": "2026-03-15",
                "dealer_id":        "DLR-001",
            },
        },
        "stream_id": "1710000000000-0",
    }


# ── Mock MCP responses ────────────────────────────────────────────────────────

def _mock_los_contract(contract_id: str) -> dict:
    return {"contract_id": contract_id, "state": "originated", "vin": "1HGBH41JXMN000001"}


def _mock_llas_not_found() -> dict:
    return {"found": False}


def _mock_llas_found() -> dict:
    return {"found": True, "contract_id": "ORC-2024-TEST", "balance": 25_000.0}


def _mock_quarantine_records(event_id: str) -> list:
    return [
        {
            "event_id":       event_id,
            "status":         "approved",
            "context_snapshot": {
                "failures": [
                    {"code": "RULE-BIZ-DEALER", "message": "Dealer ID not found"}
                ]
            },
        }
    ]


def _mock_valid_proof_token_result() -> dict:
    return {
        "valid":      True,
        "event_id":   str(uuid.uuid4()),
        "contract_id": "ORC-2024-TEST",
        "saga_id":    str(uuid.uuid4()),
        "proof_token": "eyJhbGciOiJIUzI1NiJ9.stub",
        "failures":   [],
        "warnings":   [{"code": "UNHANDLED_EVENT_TYPE", "message": "Passing through."}],
    }


def _mock_write_result() -> dict:
    return {
        "success":           True,
        "record_id":         str(uuid.uuid4()),
        "data_hash":         "abc123",
        "write_guard_active": True,
    }


def _mock_state_transition_result() -> dict:
    return {"success": True, "new_state": "active", "previous_state": "originated"}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOverrideFlowHappyPath:
    """Override flow completes successfully — LLAS not yet created."""

    @patch("agent.flows.override.validation")
    @patch("agent.flows.override.ledger")
    @patch("agent.flows.override.llas")
    @patch("agent.flows.override.oracle_los")
    async def test_full_happy_path(
        self,
        mock_oracle_los,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        original_event_id,
        approval_event_id,
    ):
        """Override flow calls all MCP servers in order and completes the saga."""
        mock_oracle_los.get_contract   = AsyncMock(return_value=_mock_los_contract(contract_id))
        mock_llas.get_account          = AsyncMock(return_value=_mock_llas_not_found())
        mock_llas.create_account       = AsyncMock(return_value={"success": True})
        mock_validation.get_quarantined = AsyncMock(return_value=_mock_quarantine_records(original_event_id))
        mock_validation.validate_event  = AsyncMock(return_value=_mock_valid_proof_token_result())
        mock_ledger.write_record        = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock(return_value=_mock_state_transition_result())

        flow  = OverrideFlow()
        event = _make_approval_event(contract_id, original_event_id, approval_event_id)
        await flow(saga=mock_saga, event=event)

        mock_oracle_los.get_contract.assert_awaited_once_with(contract_id)
        mock_llas.get_account.assert_awaited_once_with(contract_id)
        mock_validation.validate_event.assert_awaited_once()
        mock_ledger.write_record.assert_awaited_once()
        mock_ledger.execute_state_transition.assert_awaited_once()
        mock_llas.create_account.assert_awaited_once()
        mock_saga.complete.assert_awaited_once()
        mock_saga.fail.assert_not_awaited()
        mock_saga.quarantine.assert_not_awaited()

    @patch("agent.flows.override.validation")
    @patch("agent.flows.override.ledger")
    @patch("agent.flows.override.llas")
    @patch("agent.flows.override.oracle_los")
    async def test_llas_already_exists_skips_create(
        self,
        mock_oracle_los,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        original_event_id,
        approval_event_id,
    ):
        """If LLAS account already exists, create_account should NOT be called."""
        mock_oracle_los.get_contract   = AsyncMock(return_value=_mock_los_contract(contract_id))
        mock_llas.get_account          = AsyncMock(return_value=_mock_llas_found())
        mock_validation.get_quarantined = AsyncMock(return_value=[])
        mock_validation.validate_event  = AsyncMock(return_value=_mock_valid_proof_token_result())
        mock_ledger.write_record        = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock(return_value=_mock_state_transition_result())

        flow  = OverrideFlow()
        event = _make_approval_event(contract_id, original_event_id, approval_event_id)
        await flow(saga=mock_saga, event=event)

        mock_llas.create_account.assert_not_called()
        mock_saga.complete.assert_awaited_once()

    @patch("agent.flows.override.validation")
    @patch("agent.flows.override.ledger")
    @patch("agent.flows.override.llas")
    @patch("agent.flows.override.oracle_los")
    async def test_validation_request_uses_quarantine_approved_event_type(
        self,
        mock_oracle_los,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        original_event_id,
        approval_event_id,
    ):
        """The validate_event call must use event_type='quarantine.approved' (not 'contract.originated')."""
        mock_oracle_los.get_contract   = AsyncMock(return_value=_mock_los_contract(contract_id))
        mock_llas.get_account          = AsyncMock(return_value=_mock_llas_not_found())
        mock_llas.create_account       = AsyncMock(return_value={"success": True})
        mock_validation.get_quarantined = AsyncMock(return_value=[])
        mock_validation.validate_event  = AsyncMock(return_value=_mock_valid_proof_token_result())
        mock_ledger.write_record        = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock(return_value=_mock_state_transition_result())

        flow  = OverrideFlow()
        event = _make_approval_event(contract_id, original_event_id, approval_event_id)
        await flow(saga=mock_saga, event=event)

        call_args = mock_validation.validate_event.call_args[0][0]
        assert call_args["event_envelope"]["event_type"] == "quarantine.approved"

    @patch("agent.flows.override.validation")
    @patch("agent.flows.override.ledger")
    @patch("agent.flows.override.llas")
    @patch("agent.flows.override.oracle_los")
    async def test_ledger_record_contains_override_metadata(
        self,
        mock_oracle_los,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        original_event_id,
        approval_event_id,
    ):
        """Written ledger record must carry is_override, reviewed_by, and original_event_id."""
        mock_oracle_los.get_contract   = AsyncMock(return_value=_mock_los_contract(contract_id))
        mock_llas.get_account          = AsyncMock(return_value=_mock_llas_not_found())
        mock_llas.create_account       = AsyncMock(return_value={"success": True})
        mock_validation.get_quarantined = AsyncMock(return_value=[])
        mock_validation.validate_event  = AsyncMock(return_value=_mock_valid_proof_token_result())
        mock_ledger.write_record        = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock(return_value=_mock_state_transition_result())

        flow  = OverrideFlow()
        event = _make_approval_event(contract_id, original_event_id, approval_event_id)
        await flow(saga=mock_saga, event=event)

        record = mock_ledger.write_record.call_args[1]["record"]
        assert record["is_override"] is True
        assert record["reviewed_by"] == "jane.doe"
        assert record["original_event_id"] == original_event_id
        assert record["override_reason"] == "VIN confirmed via manual check"

    @patch("agent.flows.override.validation")
    @patch("agent.flows.override.ledger")
    @patch("agent.flows.override.llas")
    @patch("agent.flows.override.oracle_los")
    async def test_saga_checkpointed_through_all_steps(
        self,
        mock_oracle_los,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        original_event_id,
        approval_event_id,
    ):
        """Saga checkpoints should cover CONTEXT_GATHERED, VALIDATED, PROOF_TOKEN_ISSUED, LEDGER_WRITTEN, STATE_TRANSITIONED."""
        mock_oracle_los.get_contract   = AsyncMock(return_value=_mock_los_contract(contract_id))
        mock_llas.get_account          = AsyncMock(return_value=_mock_llas_not_found())
        mock_llas.create_account       = AsyncMock(return_value={"success": True})
        mock_validation.get_quarantined = AsyncMock(return_value=[])
        mock_validation.validate_event  = AsyncMock(return_value=_mock_valid_proof_token_result())
        mock_ledger.write_record        = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock(return_value=_mock_state_transition_result())

        flow  = OverrideFlow()
        event = _make_approval_event(contract_id, original_event_id, approval_event_id)
        await flow(saga=mock_saga, event=event)

        checkpointed_steps = {
            call.args[0] if call.args else call.kwargs.get("step")
            for call in mock_saga.checkpoint.call_args_list
        }
        # Convert StrEnum values to strings for comparison
        checkpointed_steps_str = {str(s) for s in checkpointed_steps}
        for expected in ["CONTEXT_GATHERED", "VALIDATED", "PROOF_TOKEN_ISSUED",
                         "LEDGER_WRITTEN", "STATE_TRANSITIONED"]:
            assert expected in checkpointed_steps_str, (
                f"Expected checkpoint step {expected!r}, got: {checkpointed_steps_str}"
            )


class TestOverrideFlowErrorHandling:
    """Edge cases and error paths."""

    @patch("agent.flows.override.validation")
    @patch("agent.flows.override.ledger")
    @patch("agent.flows.override.llas")
    @patch("agent.flows.override.oracle_los")
    async def test_unexpected_validation_failure_calls_saga_fail(
        self,
        mock_oracle_los,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        original_event_id,
        approval_event_id,
    ):
        """If validate_event returns valid=False for an override, saga.fail should be called."""
        mock_oracle_los.get_contract   = AsyncMock(return_value=_mock_los_contract(contract_id))
        mock_llas.get_account          = AsyncMock(return_value=_mock_llas_not_found())
        mock_validation.get_quarantined = AsyncMock(return_value=[])
        mock_validation.validate_event  = AsyncMock(return_value={
            "valid":    False,
            "failures": [{"code": "UNEXPECTED", "message": "Unexpected failure"}],
        })

        flow  = OverrideFlow()
        event = _make_approval_event(contract_id, original_event_id, approval_event_id)
        await flow(saga=mock_saga, event=event)

        mock_saga.fail.assert_awaited_once()
        mock_ledger.write_record.assert_not_called()
        mock_saga.complete.assert_not_awaited()

    @patch("agent.flows.override.validation")
    @patch("agent.flows.override.ledger")
    @patch("agent.flows.override.llas")
    @patch("agent.flows.override.oracle_los")
    async def test_state_transition_failure_is_non_fatal(
        self,
        mock_oracle_los,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        original_event_id,
        approval_event_id,
    ):
        """State transition failure should be logged but not prevent saga completion."""
        mock_oracle_los.get_contract   = AsyncMock(return_value=_mock_los_contract(contract_id))
        mock_llas.get_account          = AsyncMock(return_value=_mock_llas_not_found())
        mock_llas.create_account       = AsyncMock(return_value={"success": True})
        mock_validation.get_quarantined = AsyncMock(return_value=[])
        mock_validation.validate_event  = AsyncMock(return_value=_mock_valid_proof_token_result())
        mock_ledger.write_record        = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock(
            side_effect=Exception("State transition failed — already active")
        )

        flow  = OverrideFlow()
        event = _make_approval_event(contract_id, original_event_id, approval_event_id)
        await flow(saga=mock_saga, event=event)

        # Saga should still complete despite the state transition failure
        mock_saga.complete.assert_awaited_once()
        mock_saga.fail.assert_not_awaited()

    @patch("agent.flows.override.validation")
    @patch("agent.flows.override.ledger")
    @patch("agent.flows.override.llas")
    @patch("agent.flows.override.oracle_los")
    async def test_llas_create_failure_is_non_fatal(
        self,
        mock_oracle_los,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        original_event_id,
        approval_event_id,
    ):
        """LLAS account creation failure should not prevent saga completion."""
        mock_oracle_los.get_contract   = AsyncMock(return_value=_mock_los_contract(contract_id))
        mock_llas.get_account          = AsyncMock(return_value=_mock_llas_not_found())
        mock_llas.create_account       = AsyncMock(side_effect=Exception("LLAS unavailable"))
        mock_validation.get_quarantined = AsyncMock(return_value=[])
        mock_validation.validate_event  = AsyncMock(return_value=_mock_valid_proof_token_result())
        mock_ledger.write_record        = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock(return_value=_mock_state_transition_result())

        flow  = OverrideFlow()
        event = _make_approval_event(contract_id, original_event_id, approval_event_id)
        await flow(saga=mock_saga, event=event)

        mock_saga.complete.assert_awaited_once()
        mock_saga.fail.assert_not_awaited()
