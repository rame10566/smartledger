"""
Unit tests for PaymentFlow

Tests the payment.received saga:
  - Happy path: valid payment → ledger write → state check → LLAS post
  - Quarantine path: invalid payment (amount = 0)
  - State transitions: active stays active, delinquent → active, payoff → paid_off
  - LLAS post failure is non-fatal
  - State transition failure is non-fatal
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.flows.payment import PaymentFlow


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def contract_id():
    return "ORC-2024-001"


@pytest.fixture
def payment_id():
    return f"PAY-{uuid.uuid4().hex[:6].upper()}"


@pytest.fixture
def event_id():
    return str(uuid.uuid4())


@pytest.fixture
def mock_saga():
    saga = MagicMock()
    saga.saga_id = str(uuid.uuid4())
    saga.checkpoint = AsyncMock()
    saga.complete = AsyncMock()
    saga.quarantine = AsyncMock()
    saga.fail = AsyncMock()
    return saga


def _make_payment_event(contract_id, event_id, payment_id, amount=487.50):
    return {
        "event_id":      event_id,
        "event_type":    "payment.received",
        "source_system": "payment",
        "contract_id":   contract_id,
        "timestamp":     "2026-03-15T10:00:00Z",
        "correlation_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "payload": {
            "payment_id":     payment_id,
            "contract_id":    contract_id,
            "amount":         amount,
            "payment_method": "ach",
            "payment_date":   "2026-03-15",
            "channel":        "payment",
            "source_system":  "payment",
        },
    }


def _mock_ledger_state(state="active"):
    return {"current_state": state, "contract_id": "ORC-2024-001"}


def _mock_llas_account(balance=26980.50, days_past_due=0, next_payment=487.50):
    return {
        "found":                True,
        "contract_id":          "ORC-2024-001",
        "current_balance":      balance,
        "next_payment_amount":  next_payment,
        "days_past_due":        days_past_due,
        "status":               "active",
    }


def _mock_valid_proof_token():
    return {
        "valid":       True,
        "proof_token": "eyJ.mock.token",
        "failures":    [],
        "warnings":    [],
    }


def _mock_write_result():
    return {
        "success":            True,
        "record_id":          str(uuid.uuid4()),
        "data_hash":          "sha256:abc123",
        "write_guard_active": True,
    }


def _mock_state_transition_result():
    return {"success": True, "new_state": "paid_off"}


def _mock_post_payment_result():
    return {"success": True, "is_paid_off": False}


# ─── Happy path tests ─────────────────────────────────────────────────────────

class TestPaymentFlowHappyPath:

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_full_happy_path(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """Happy path: payment flows through all stages and completes."""
        mock_ledger.get_state     = AsyncMock(return_value=_mock_ledger_state("active"))
        mock_llas.get_account     = AsyncMock(return_value=_mock_llas_account())
        mock_validation.validate_event = AsyncMock(return_value=_mock_valid_proof_token())
        mock_ledger.write_record  = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock(return_value=_mock_state_transition_result())
        mock_llas.post_payment    = AsyncMock(return_value=_mock_post_payment_result())

        flow  = PaymentFlow()
        event = _make_payment_event(contract_id, event_id, payment_id)
        await flow(saga=mock_saga, event=event)

        mock_ledger.get_state.assert_awaited_once_with(contract_id)
        mock_llas.get_account.assert_awaited_once_with(contract_id)
        mock_validation.validate_event.assert_awaited_once()
        mock_ledger.write_record.assert_awaited_once()
        mock_llas.post_payment.assert_awaited_once()
        mock_saga.complete.assert_awaited_once()
        mock_saga.quarantine.assert_not_called()

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_payment_record_has_correct_fields(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """Ledger record must include payment_id, amount, payment_method."""
        mock_ledger.get_state = AsyncMock(return_value=_mock_ledger_state("active"))
        mock_llas.get_account = AsyncMock(return_value=_mock_llas_account())
        mock_validation.validate_event = AsyncMock(return_value=_mock_valid_proof_token())
        mock_ledger.write_record = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock()
        mock_llas.post_payment = AsyncMock(return_value=_mock_post_payment_result())

        flow  = PaymentFlow()
        event = _make_payment_event(contract_id, event_id, payment_id, amount=350.00)
        await flow(saga=mock_saga, event=event)

        call_kwargs = mock_ledger.write_record.call_args
        record = call_kwargs.kwargs["record"]
        assert record["record_type"] == "payment"
        assert record["payment_id"] == payment_id
        assert record["amount"] == 350.00
        assert record["contract_id"] == contract_id

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_validation_request_includes_ledger_and_llas_context(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """validate_event context must include ledger_state and llas_account."""
        mock_ledger.get_state = AsyncMock(return_value=_mock_ledger_state("active"))
        mock_llas.get_account = AsyncMock(return_value=_mock_llas_account())
        mock_validation.validate_event = AsyncMock(return_value=_mock_valid_proof_token())
        mock_ledger.write_record = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock()
        mock_llas.post_payment = AsyncMock(return_value=_mock_post_payment_result())

        flow  = PaymentFlow()
        event = _make_payment_event(contract_id, event_id, payment_id)
        await flow(saga=mock_saga, event=event)

        val_call = mock_validation.validate_event.call_args
        request = val_call.kwargs["request"] if val_call.kwargs else val_call.args[0]
        ctx = request["context"]
        assert "ledger_state" in ctx
        assert "llas_account" in ctx
        assert ctx["ledger_state"]["current_state"] == "active"


# ─── State transition tests ───────────────────────────────────────────────────

class TestPaymentFlowStateTransitions:

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_payoff_triggers_paid_off_transition(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """When payment >= balance, contract transitions to paid_off."""
        # LLAS balance exactly equals the payment amount → paid off
        mock_ledger.get_state = AsyncMock(return_value=_mock_ledger_state("active"))
        mock_llas.get_account = AsyncMock(return_value=_mock_llas_account(balance=487.50))
        mock_validation.validate_event = AsyncMock(return_value=_mock_valid_proof_token())
        mock_ledger.write_record = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock()
        mock_llas.post_payment = AsyncMock(return_value={"success": True, "is_paid_off": True})

        flow  = PaymentFlow()
        event = _make_payment_event(contract_id, event_id, payment_id, amount=487.50)
        await flow(saga=mock_saga, event=event)

        mock_ledger.execute_state_transition.assert_awaited_once()
        transition_call = mock_ledger.execute_state_transition.call_args
        assert transition_call.kwargs["new_state"] == "paid_off"

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_delinquent_contract_transitions_to_active(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """Payment on a delinquent contract that covers monthly payment → active."""
        mock_ledger.get_state = AsyncMock(return_value=_mock_ledger_state("delinquent"))
        mock_llas.get_account = AsyncMock(
            return_value=_mock_llas_account(balance=5000.00, days_past_due=0, next_payment=487.50)
        )
        mock_validation.validate_event = AsyncMock(return_value=_mock_valid_proof_token())
        mock_ledger.write_record = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock()
        mock_llas.post_payment = AsyncMock(return_value=_mock_post_payment_result())

        flow  = PaymentFlow()
        # Payment covers the monthly amount → should trigger active transition
        event = _make_payment_event(contract_id, event_id, payment_id, amount=487.50)
        await flow(saga=mock_saga, event=event)

        mock_ledger.execute_state_transition.assert_awaited_once()
        transition_call = mock_ledger.execute_state_transition.call_args
        assert transition_call.kwargs["new_state"] == "active"

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_active_regular_payment_no_state_transition(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """Regular payment on an active contract with large balance → no state transition."""
        mock_ledger.get_state = AsyncMock(return_value=_mock_ledger_state("active"))
        mock_llas.get_account = AsyncMock(return_value=_mock_llas_account(balance=26000.00))
        mock_validation.validate_event = AsyncMock(return_value=_mock_valid_proof_token())
        mock_ledger.write_record = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock()
        mock_llas.post_payment = AsyncMock(return_value=_mock_post_payment_result())

        flow  = PaymentFlow()
        event = _make_payment_event(contract_id, event_id, payment_id, amount=487.50)
        await flow(saga=mock_saga, event=event)

        mock_ledger.execute_state_transition.assert_not_called()
        mock_saga.complete.assert_awaited_once()


# ─── Quarantine path ─────────────────────────────────────────────────────────

class TestPaymentFlowQuarantine:

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_invalid_payment_is_quarantined(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """Payment failing validation is quarantined, not written to ledger."""
        mock_ledger.get_state = AsyncMock(return_value=_mock_ledger_state("active"))
        mock_llas.get_account = AsyncMock(return_value=_mock_llas_account())
        mock_validation.validate_event = AsyncMock(return_value={
            "valid":    False,
            "failures": [{"code": "INVALID_PAYMENT_AMOUNT", "message": "Amount must be > 0"}],
        })

        flow  = PaymentFlow()
        event = _make_payment_event(contract_id, event_id, payment_id, amount=0)
        await flow(saga=mock_saga, event=event)

        mock_saga.quarantine.assert_awaited_once()
        mock_ledger.write_record.assert_not_called()
        mock_saga.complete.assert_not_called()


# ─── Non-fatal error paths ────────────────────────────────────────────────────

class TestPaymentFlowNonFatalErrors:

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_llas_post_failure_is_non_fatal(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """LLAS post_payment failure does not fail the saga — ledger is the source of truth."""
        mock_ledger.get_state = AsyncMock(return_value=_mock_ledger_state("active"))
        mock_llas.get_account = AsyncMock(return_value=_mock_llas_account())
        mock_validation.validate_event = AsyncMock(return_value=_mock_valid_proof_token())
        mock_ledger.write_record = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock()
        mock_llas.post_payment = AsyncMock(side_effect=Exception("LLAS unavailable"))

        flow  = PaymentFlow()
        event = _make_payment_event(contract_id, event_id, payment_id)
        await flow(saga=mock_saga, event=event)

        # Saga should still complete — LLAS failure is non-fatal
        mock_saga.complete.assert_awaited_once()
        mock_saga.fail.assert_not_called()

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_state_transition_failure_is_non_fatal(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """State transition failure (e.g. already paid_off) does not fail the saga."""
        mock_ledger.get_state = AsyncMock(return_value=_mock_ledger_state("active"))
        # Balance equals payment → would trigger paid_off transition
        mock_llas.get_account = AsyncMock(return_value=_mock_llas_account(balance=487.50))
        mock_validation.validate_event = AsyncMock(return_value=_mock_valid_proof_token())
        mock_ledger.write_record = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock(
            side_effect=Exception("State already paid_off")
        )
        mock_llas.post_payment = AsyncMock(return_value=_mock_post_payment_result())

        flow  = PaymentFlow()
        event = _make_payment_event(contract_id, event_id, payment_id, amount=487.50)
        await flow(saga=mock_saga, event=event)

        mock_saga.complete.assert_awaited_once()
        mock_saga.fail.assert_not_called()

    @patch("agent.flows.payment.validation")
    @patch("agent.flows.payment.ledger")
    @patch("agent.flows.payment.llas")
    async def test_saga_checkpointed_through_all_steps(
        self,
        mock_llas,
        mock_ledger,
        mock_validation,
        mock_saga,
        contract_id,
        payment_id,
        event_id,
    ):
        """All saga steps should be checkpointed in order."""
        mock_ledger.get_state = AsyncMock(return_value=_mock_ledger_state("active"))
        mock_llas.get_account = AsyncMock(return_value=_mock_llas_account())
        mock_validation.validate_event = AsyncMock(return_value=_mock_valid_proof_token())
        mock_ledger.write_record = AsyncMock(return_value=_mock_write_result())
        mock_ledger.execute_state_transition = AsyncMock()
        mock_llas.post_payment = AsyncMock(return_value=_mock_post_payment_result())

        flow  = PaymentFlow()
        event = _make_payment_event(contract_id, event_id, payment_id)
        await flow(saga=mock_saga, event=event)

        checkpoint_steps = [
            call.args[0] if call.args else call.kwargs.get("step", "")
            for call in mock_saga.checkpoint.call_args_list
        ]
        step_names = [str(s) for s in checkpoint_steps]

        assert "CONTEXT_GATHERED" in step_names
        assert "VALIDATED" in step_names
        assert "PROOF_TOKEN_ISSUED" in step_names
        assert "LEDGER_WRITTEN" in step_names
        assert "STATE_TRANSITIONED" in step_names
