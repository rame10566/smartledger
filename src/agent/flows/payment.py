"""
Payment Processing Flow

Handles payment.received events from all payment channels:
  - Payment simulator (source_system: payment)
  - Customer Portal  (source_system: customer_portal)
  - Mobile App       (source_system: mobile_app)
  - IVR              (source_system: ivr)

Steps:
  1. CONTEXT_GATHERED  — fetch ledger state + LLAS account balance
  2. VALIDATED         — validate via Validation Engine (amount, state, account)
  3. PROOF_TOKEN_ISSUED (checkpoint, token value redacted)
  4. LEDGER_WRITTEN    — write payment record to Ledger MCP
  5. STATE_TRANSITIONED — transition state if payoff (→ paid_off) or catch-up (delinquent → active)
  6. COMPLETED         — post payment to LLAS accounting (non-fatal)

Called by AgentEventLoop when event_type in:
  payment.received | customer.payment_submitted | ivr.payment_submitted
"""

from typing import Any

from shared.logging import get_logger
from shared.models.saga import SagaStep

from agent.core.mcp_client import llas, ledger, validation
from agent.core.saga import SagaManager

logger = get_logger(__name__)

# Contract states where the ledger write is still useful even without a state transition
_PAYABLE_STATES = {"active", "delinquent", "originated"}


class PaymentFlow:
    """
    Handles payment.received (and equivalent) events from any payment channel.

    Usage:
        flow = PaymentFlow()
        event_loop.register_flow("payment.received", flow)
        event_loop.register_flow("customer.payment_submitted", flow)
        event_loop.register_flow("ivr.payment_submitted", flow)
    """

    async def __call__(self, saga: SagaManager, event: dict[str, Any]) -> None:
        """Entry point called by AgentEventLoop."""
        contract_id   = event["contract_id"]
        event_id      = event["event_id"]
        source_system = event["source_system"]
        payload       = event["payload"]
        timestamp     = event.get("timestamp", "")
        correlation_id = event.get("correlation_id", "")
        schema_version = event.get("schema_version", "1.0")

        amount     = float(payload.get("amount", 0))
        payment_id = payload.get("payment_id", "")
        payment_method = payload.get("payment_method", "")
        payment_date   = payload.get("payment_date", "")

        logger.info(
            "payment_flow_started",
            contract_id=contract_id,
            event_id=event_id,
            saga_id=saga.saga_id,
            payment_id=payment_id,
            amount=amount,
            source_system=source_system,
        )

        # ── Step 1: Gather context ────────────────────────────────────────────
        await saga.checkpoint(
            SagaStep.CONTEXT_GATHERED,
            payload={"status": "gathering"},
            status="in_progress",
        )

        ledger_state = await ledger.get_state(contract_id)
        llas_account = await llas.get_account(contract_id)

        context: dict[str, Any] = {
            "ledger_state": ledger_state,
            "llas_account": llas_account,
        }

        current_state = ledger_state.get("current_state", "unknown")

        await saga.checkpoint(
            SagaStep.CONTEXT_GATHERED,
            payload={"ledger_state": current_state, "llas_found": llas_account.get("found", False)},
            status="completed",
        )

        logger.info(
            "payment_context_gathered",
            contract_id=contract_id,
            event_id=event_id,
            current_state=current_state,
            llas_found=llas_account.get("found", False),
        )

        # ── Step 2: Validate ──────────────────────────────────────────────────
        await saga.checkpoint(
            SagaStep.VALIDATED,
            payload={"status": "validating"},
            status="in_progress",
        )

        validation_request: dict[str, Any] = {
            "event_envelope": {
                "event_id":       event_id,
                "event_type":     event["event_type"],
                "source_system":  source_system,
                "contract_id":    contract_id,
                "timestamp":      timestamp,
                "correlation_id": correlation_id,
                "schema_version": schema_version,
                "payload":        payload,
            },
            "saga_id": saga.saga_id,
            "context": context,
        }

        validation_result = await validation.validate_event(validation_request)

        if not validation_result.get("valid", False):
            failures = validation_result.get("failures", [])
            logger.warning(
                "payment_quarantined",
                contract_id=contract_id,
                event_id=event_id,
                failure_count=len(failures),
            )
            await saga.checkpoint(
                SagaStep.VALIDATED,
                payload={"valid": False, "failures": failures},
                status="completed",
            )
            await saga.quarantine(failures)
            return

        proof_token = validation_result.get("proof_token", "")

        await saga.checkpoint(
            SagaStep.VALIDATED,
            payload={"valid": True, "warnings": validation_result.get("warnings", [])},
            status="completed",
        )

        # Step 3: Proof token checkpoint (value redacted)
        await saga.checkpoint(
            SagaStep.PROOF_TOKEN_ISSUED,
            payload={"jti": "***REDACTED***"},
            status="completed",
        )

        # ── Step 4: Write payment record to ledger ────────────────────────────
        await saga.checkpoint(
            SagaStep.LEDGER_WRITTEN,
            payload={"status": "writing"},
            status="in_progress",
        )

        payment_record: dict[str, Any] = {
            "contract_id":    contract_id,
            "record_type":    "payment",
            "saga_id":        saga.saga_id,
            "event_id":       event_id,
            "source_system":  source_system,
            "payment_id":     payment_id,
            "amount":         amount,
            "payment_method": payment_method,
            "payment_date":   payment_date,
            "channel":        payload.get("channel", ""),
            "llas_balance_before": llas_account.get("current_balance"),
        }

        write_result = await ledger.write_record(
            record=payment_record,
            proof_token=proof_token,
        )

        record_id = write_result.get("record_id", "")
        data_hash = write_result.get("data_hash", "")

        await saga.checkpoint(
            SagaStep.LEDGER_WRITTEN,
            payload={
                "record_id": record_id,
                "data_hash": data_hash,
                "write_guard_active": write_result.get("write_guard_active", True),
            },
            status="completed",
        )

        logger.info(
            "payment_ledger_written",
            contract_id=contract_id,
            payment_id=payment_id,
            record_id=record_id,
        )

        # ── Step 5: State transition (if needed) ──────────────────────────────
        await saga.checkpoint(
            SagaStep.STATE_TRANSITIONED,
            payload={"status": "evaluating", "current_state": current_state},
            status="in_progress",
        )

        llas_balance = llas_account.get("current_balance", amount + 1)
        new_state: str | None = None

        if llas_balance - amount <= 0:
            new_state = "paid_off"
        elif current_state == "delinquent":
            # Payment on a delinquent account — check if it brings days_past_due to 0
            days_past_due = llas_account.get("days_past_due", 0)
            if days_past_due == 0 or amount >= llas_account.get("next_payment_amount", amount):
                new_state = "active"

        if new_state:
            try:
                await ledger.execute_state_transition(
                    contract_id=contract_id,
                    new_state=new_state,
                    trigger_event_id=event_id,
                    saga_id=saga.saga_id,
                )
                logger.info(
                    "payment_state_transitioned",
                    contract_id=contract_id,
                    from_state=current_state,
                    to_state=new_state,
                )
            except Exception as e:
                # Non-fatal: ledger write is the critical step
                logger.warning(
                    "payment_state_transition_failed_non_fatal",
                    contract_id=contract_id,
                    new_state=new_state,
                    error=str(e),
                )
                new_state = None

        await saga.checkpoint(
            SagaStep.STATE_TRANSITIONED,
            payload={"new_state": new_state, "previous_state": current_state},
            status="completed",
        )

        # ── Step 6: Post payment to LLAS ──────────────────────────────────────
        # Non-fatal: ledger is the source of truth; LLAS sync can be retried.
        try:
            await llas.post_payment(
                contract_id=contract_id,
                payment_data={
                    "payment_id":   payment_id,
                    "amount":       amount,
                    "payment_date": payment_date,
                    "principal":    amount,   # simplified: entire amount → principal for POC
                    "interest":     0.0,
                    "fees":         0.0,
                },
            )
            logger.info("llas_payment_posted", contract_id=contract_id, payment_id=payment_id)
        except Exception as e:
            logger.warning(
                "llas_payment_post_failed_non_fatal",
                contract_id=contract_id,
                payment_id=payment_id,
                error=str(e),
            )

        # ── Complete ──────────────────────────────────────────────────────────
        await saga.complete(
            payload={
                "record_id":  record_id,
                "data_hash":  data_hash,
                "payment_id": payment_id,
                "amount":     amount,
                "new_state":  new_state,
            }
        )

        logger.info(
            "payment_flow_completed",
            contract_id=contract_id,
            payment_id=payment_id,
            event_id=event_id,
            saga_id=saga.saga_id,
            record_id=record_id,
            new_state=new_state,
        )
