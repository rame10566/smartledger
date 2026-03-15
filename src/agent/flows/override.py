"""
Human Override Flow

Handles quarantine.approved events — i.e., events where a human reviewer
in the Governance Dashboard has explicitly approved a previously-quarantined
origination despite validation failures.

Why this flow is separate from OriginationFlow:
  - The proof token is issued for event_type="quarantine.approved", which the
    Validation Engine passes through without re-running business rules.
  - The written record carries override metadata (reviewer, reason, original
    failures) so the audit trail is complete.
  - State may already be partially written (e.g. originated) from the first
    attempt; we handle that gracefully.

Flow:
  1. CONTEXT_GATHERED  — fetch Oracle LOS contract + LLAS account + original
                         quarantine record
  2. VALIDATED         — call Validation Engine with quarantine.approved event
                         type (passes through, issues proof token)
  3. PROOF_TOKEN_ISSUED
  4. LEDGER_WRITTEN    — write_record with record_type="origination" + override
                         metadata in payload
  5. STATE_TRANSITIONED — transition to "active" (idempotent — skipped if already
                          active)
  6. COMPLETED         — LLAS account created (idempotent), saga complete

Called by AgentEventLoop when event_type == "quarantine.approved".
"""

from typing import Any

from shared.logging import get_logger
from shared.models.saga import SagaStep

from agent.core.mcp_client import llas, ledger, oracle_los, validation
from agent.core.saga import SagaManager

logger = get_logger(__name__)


class OverrideFlow:
    """
    Handles the quarantine.approved event (human-in-the-loop override path).

    Usage:
        flow = OverrideFlow()
        event_loop.register_flow("quarantine.approved", flow)
    """

    async def __call__(self, saga: SagaManager, event: dict[str, Any]) -> None:
        """Entry point called by AgentEventLoop."""
        contract_id   = event["contract_id"]
        event_id      = event["event_id"]      # new UUID for the approval event
        source_system = event["source_system"]  # "dashboard"
        payload       = event["payload"]        # override metadata + original payload
        timestamp     = event.get("timestamp", "")
        correlation_id = event.get("correlation_id", "")
        schema_version = event.get("schema_version", "1.0")

        # Extract override metadata from the approval event payload
        original_event_id = payload.get("original_event_id", "")
        override_reason   = payload.get("override_reason", "No reason provided")
        reviewed_by       = payload.get("reviewed_by", "unknown")
        original_payload  = payload.get("original_payload", {})

        logger.info(
            "override_flow_started",
            contract_id=contract_id,
            event_id=event_id,
            original_event_id=original_event_id,
            reviewed_by=reviewed_by,
            saga_id=saga.saga_id,
        )

        # ── Step 1: Gather cross-system context ───────────────────────────────
        await saga.checkpoint(
            SagaStep.CONTEXT_GATHERED,
            payload={"status": "gathering", "original_event_id": original_event_id},
            status="in_progress",
        )

        los_contract = await oracle_los.get_contract(contract_id)
        llas_account = await llas.get_account(contract_id)

        # Fetch the quarantine record so we can embed the original failures
        quarantine_records = await validation.get_quarantined(contract_id)
        quarantine_record = next(
            (q for q in quarantine_records if q.get("event_id") == original_event_id),
            None,
        )
        original_failures = (
            quarantine_record.get("context_snapshot", {}).get("failures", [])
            if quarantine_record
            else []
        )

        context: dict[str, Any] = {
            "oracle_los_contract":  los_contract,
            "llas_account":         llas_account,
            "is_override":          True,
            "override_reason":      override_reason,
            "reviewed_by":          reviewed_by,
            "original_event_id":    original_event_id,
            "original_failures":    original_failures,
        }

        await saga.checkpoint(
            SagaStep.CONTEXT_GATHERED,
            payload={
                "llas_found":         llas_account.get("found", False),
                "original_event_id":  original_event_id,
                "failure_count":      len(original_failures),
                "is_override":        True,
            },
            status="completed",
        )

        logger.info(
            "override_context_gathered",
            contract_id=contract_id,
            event_id=event_id,
            original_failure_count=len(original_failures),
        )

        # ── Step 2: Get proof token via Validation Engine ─────────────────────
        # Event type "quarantine.approved" bypasses business rules in the
        # Validation Engine (passes through the else-branch) and issues a
        # proof token. This is the designed behaviour — the human's approval
        # is the authorisation.
        await saga.checkpoint(
            SagaStep.VALIDATED,
            payload={"status": "getting_override_proof_token"},
            status="in_progress",
        )

        validation_request: dict[str, Any] = {
            "event_envelope": {
                "event_id":       event_id,
                "event_type":     event["event_type"],  # "quarantine.approved"
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
            # This should not happen for quarantine.approved events.
            # If it does, something is wrong with the validation server.
            failures = validation_result.get("failures", [])
            logger.error(
                "override_validation_unexpectedly_failed",
                contract_id=contract_id,
                event_id=event_id,
                failures=failures,
            )
            await saga.fail(
                f"Override validation unexpectedly failed: {failures}",
                step=SagaStep.VALIDATED,
            )
            return

        proof_token = validation_result.get("proof_token", "")

        await saga.checkpoint(
            SagaStep.VALIDATED,
            payload={"valid": True, "is_override": True},
            status="completed",
        )
        await saga.checkpoint(
            SagaStep.PROOF_TOKEN_ISSUED,
            payload={"jti": "***REDACTED***", "is_override": True},
            status="completed",
        )

        # ── Step 3: Write override origination record to ledger ───────────────
        await saga.checkpoint(
            SagaStep.LEDGER_WRITTEN,
            payload={"status": "writing", "is_override": True},
            status="in_progress",
        )

        override_record: dict[str, Any] = {
            "contract_id":     contract_id,
            "record_type":     "origination",
            "saga_id":         saga.saga_id,
            "event_id":        event_id,
            "source_system":   source_system,
            "is_override":     True,
            "override_reason": override_reason,
            "reviewed_by":     reviewed_by,
            "original_event_id":   original_event_id,
            "original_failures":   original_failures,
            "contract_data":       original_payload,
            "los_contract":        los_contract,
        }

        write_result = await ledger.write_record(
            record=override_record,
            proof_token=proof_token,
        )

        record_id = write_result.get("record_id", "")
        data_hash = write_result.get("data_hash", "")

        await saga.checkpoint(
            SagaStep.LEDGER_WRITTEN,
            payload={
                "record_id":         record_id,
                "data_hash":         data_hash,
                "is_override":       True,
                "write_guard_active": write_result.get("write_guard_active", True),
            },
            status="completed",
        )

        logger.info(
            "override_ledger_written",
            contract_id=contract_id,
            event_id=event_id,
            record_id=record_id,
            reviewed_by=reviewed_by,
        )

        # ── Step 4: State transition → active (idempotent) ───────────────────
        await saga.checkpoint(
            SagaStep.STATE_TRANSITIONED,
            payload={"status": "transitioning", "new_state": "active"},
            status="in_progress",
        )

        try:
            await ledger.execute_state_transition(
                contract_id=contract_id,
                new_state="active",
                trigger_event_id=event_id,
                saga_id=saga.saga_id,
            )
            await saga.checkpoint(
                SagaStep.STATE_TRANSITIONED,
                payload={"new_state": "active", "is_override": True},
                status="completed",
            )
        except Exception as e:
            # State transition may fail if already active — treat as non-fatal
            # since the ledger record is already written.
            logger.warning(
                "override_state_transition_failed_non_fatal",
                contract_id=contract_id,
                error=str(e),
            )
            await saga.checkpoint(
                SagaStep.STATE_TRANSITIONED,
                payload={"error": str(e), "note": "non-fatal — record already written"},
                status="completed",
            )

        # ── Step 5: Create LLAS account (idempotent) ──────────────────────────
        if not llas_account.get("found", False):
            financial_terms = original_payload.get("financial_terms", {})
            try:
                await llas.create_account(
                    contract_id=contract_id,
                    account_data={
                        "contract_id":     contract_id,
                        "contract_type":   original_payload.get("contract_type", "loan"),
                        "amount_financed": financial_terms.get("amount_financed"),
                        "term_months":     financial_terms.get("term_months"),
                        "monthly_payment": financial_terms.get("monthly_payment"),
                        "origination_date": original_payload.get("origination_date"),
                        "dealer_id":       original_payload.get("dealer_id"),
                    },
                )
                logger.info("llas_account_created_via_override", contract_id=contract_id)
            except Exception as e:
                logger.warning(
                    "llas_account_creation_failed_non_fatal",
                    contract_id=contract_id,
                    error=str(e),
                )
        else:
            logger.info("llas_account_already_exists", contract_id=contract_id)

        # ── Complete ──────────────────────────────────────────────────────────
        await saga.complete(
            payload={
                "record_id":       record_id,
                "data_hash":       data_hash,
                "new_state":       "active",
                "is_override":     True,
                "reviewed_by":     reviewed_by,
                "override_reason": override_reason,
            }
        )

        logger.info(
            "override_flow_completed",
            contract_id=contract_id,
            event_id=event_id,
            saga_id=saga.saga_id,
            record_id=record_id,
            reviewed_by=reviewed_by,
        )
