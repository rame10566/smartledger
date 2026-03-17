"""
Contract Origination Flow

The core happy-path and unhappy-path (quarantine) flow for origination events.

Steps (happy path):
  1. CONTEXT_GATHERED  — fetch Oracle LOS contract + LLAS account via MCP
  2. VALIDATED         — call Validation Engine: validate_event → proof_token
  3. PROOF_TOKEN_ISSUED (checkpoint, token value redacted)
  4. LEDGER_WRITTEN    — call Ledger MCP: write_record with proof_token
  5. STATE_TRANSITIONED — call Ledger MCP: execute_state_transition → "active"
  6. COMPLETED         — LLAS account created, saga complete

Unhappy path (quarantine):
  - If validate_event returns valid=False, the event is quarantined (Validation
    Engine writes it to validation.quarantine) and the saga is checkpointed
    QUARANTINED. A human reviews it in the Dashboard and can approve/reject.
  - On approval, quarantine.approved event is published to the stream and this
    flow handles it by re-running with the override context.

Called by AgentEventLoop when event_type == "contract.originated".
"""

from typing import Any

from shared.logging import get_logger
from shared.models.saga import SagaStep

from agent.core.mcp_client import (
    llas,
    ledger,
    oracle_los,
    pricing_engine,
    rules_engine,
    validation,
)
from agent.core.saga import SagaManager

logger = get_logger(__name__)


class OriginationFlow:
    """
    Handles the contract.originated event.

    Usage:
        flow = OriginationFlow()
        event_loop.register_flow("contract.originated", flow)
    """

    async def __call__(self, saga: SagaManager, event: dict[str, Any]) -> None:
        """Entry point called by AgentEventLoop."""
        contract_id  = event["contract_id"]
        event_id     = event["event_id"]
        source_system = event["source_system"]
        payload      = event["payload"]
        timestamp    = event.get("timestamp", "")
        correlation_id = event.get("correlation_id", "")
        schema_version = event.get("schema_version", "1.0")

        logger.info(
            "origination_flow_started",
            contract_id=contract_id,
            event_id=event_id,
            saga_id=saga.saga_id,
        )

        # ── Step 1: Gather cross-system context ───────────────────────────────
        await saga.checkpoint(
            SagaStep.CONTEXT_GATHERED,
            payload={"status": "gathering"},
            status="in_progress",
        )

        los_contract = await oracle_los.get_contract(contract_id)
        llas_account = await llas.get_account(contract_id)

        # Cross-reference: gather upstream rules + pricing data for validation.
        # These are non-blocking — if unavailable, validation skips the checks.
        financial_terms = payload.get("financial_terms", {})
        customer = payload.get("customer", {})
        vehicle = payload.get("vehicle", {})

        rules_context: dict[str, Any] | None = None
        pricing_context: dict[str, Any] | None = None

        try:
            rules_context = await rules_engine.evaluate_eligibility({
                "contract_type":         payload.get("contract_type", "loan"),
                "credit_score":          customer.get("credit_score", 0),
                "amount_financed":       financial_terms.get("amount_financed", 0),
                "vehicle_value":         financial_terms.get("vehicle_value",
                                             financial_terms.get("amount_financed", 0)),
                "term_months":           financial_terms.get("term_months", 0),
                "down_payment":          financial_terms.get("down_payment", 0),
                "monthly_income":        customer.get("monthly_income", 0),
                "existing_monthly_debt": customer.get("existing_monthly_debt", 0),
                "vehicle_year":          vehicle.get("year", 0),
            })
        except Exception as e:
            logger.warning("rules_engine_unavailable", error=str(e))

        try:
            credit_tier = (
                rules_context.get("credit_tier") if rules_context
                else customer.get("credit_tier", "unknown")
            )
            rate_result = await pricing_engine.calculate_rate({
                "contract_type":   payload.get("contract_type", "loan"),
                "credit_tier":     credit_tier,
                "term_months":     financial_terms.get("term_months", 60),
                "amount_financed": financial_terms.get("amount_financed", 0),
                "vehicle_value":   financial_terms.get("vehicle_value",
                                       financial_terms.get("amount_financed", 0)),
                "vehicle_year":    vehicle.get("year", 0),
                "dealer_markup":   financial_terms.get("dealer_markup", 0),
            })
            pricing_context = rate_result
        except Exception as e:
            logger.warning("pricing_engine_unavailable", error=str(e))

        context: dict[str, Any] = {
            "oracle_los_contract": los_contract,
            "llas_account":        llas_account,
            "rules_engine":        rules_context,
            "pricing_engine":      pricing_context,
        }

        await saga.checkpoint(
            SagaStep.CONTEXT_GATHERED,
            payload=context,
            status="completed",
        )

        logger.info(
            "origination_context_gathered",
            contract_id=contract_id,
            event_id=event_id,
            llas_found=llas_account.get("found", False),
        )

        # ── Step 2: Validate ──────────────────────────────────────────────────
        await saga.checkpoint(
            SagaStep.VALIDATED,
            payload={"status": "validating"},
            status="in_progress",
        )

        # Build the ValidationRequest the Validation Engine expects
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
            # ── Unhappy path: quarantine ──────────────────────────────────────
            failures = validation_result.get("failures", [])
            logger.warning(
                "origination_quarantined",
                contract_id=contract_id,
                event_id=event_id,
                saga_id=saga.saga_id,
                failure_count=len(failures),
            )
            await saga.checkpoint(
                SagaStep.VALIDATED,
                payload={"valid": False, "failures": failures},
                status="completed",
            )
            # Validation Engine already wrote to validation.quarantine;
            # saga.quarantine() checkpoints QUARANTINED + marks idempotent.
            await saga.quarantine(failures)
            return

        # ── Happy path ────────────────────────────────────────────────────────
        proof_token = validation_result.get("proof_token", "")

        await saga.checkpoint(
            SagaStep.VALIDATED,
            payload={"valid": True, "warnings": validation_result.get("warnings", [])},
            status="completed",
        )

        # Step 3: Proof token received (logged — value redacted from DB)
        await saga.checkpoint(
            SagaStep.PROOF_TOKEN_ISSUED,
            payload={"jti": "***REDACTED***"},
            status="completed",
        )

        # ── Step 4: Write origination record to ledger ────────────────────────
        await saga.checkpoint(
            SagaStep.LEDGER_WRITTEN,
            payload={"status": "writing"},
            status="in_progress",
        )

        # Build contract parties list (PBAC-01: every contract records its parties)
        customer_id = payload.get("customer", {}).get("customer_id") or payload.get("customer_id", "")
        dealer_id = payload.get("dealer_id", "")
        contract_type = payload.get("contract_type", "loan")
        parties = [
            {"party_role": "borrower" if contract_type == "loan" else "lessee",
             "entity_type": "customer", "entity_id": customer_id},
            {"party_role": "lender" if contract_type == "loan" else "lessor",
             "entity_type": "organization", "entity_id": "SMARTLEDGER_FINANCE"},
        ]
        if dealer_id:
            parties.append({"party_role": "dealer", "entity_type": "dealer", "entity_id": dealer_id})

        origination_record: dict[str, Any] = {
            "contract_id":   contract_id,
            "record_type":   "origination",
            "saga_id":       saga.saga_id,
            "event_id":      event_id,
            "source_system": source_system,
            "contract_data": payload,
            "los_contract":  los_contract,
            "parties":       parties,
        }

        write_result = await ledger.write_record(
            record=origination_record,
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
            "origination_ledger_written",
            contract_id=contract_id,
            event_id=event_id,
            record_id=record_id,
        )

        # ── Step 5: State transition (originated → active) ────────────────────
        await saga.checkpoint(
            SagaStep.STATE_TRANSITIONED,
            payload={"status": "transitioning", "new_state": "active"},
            status="in_progress",
        )

        await ledger.execute_state_transition(
            contract_id=contract_id,
            new_state="active",
            trigger_event_id=event_id,
            saga_id=saga.saga_id,
        )

        await saga.checkpoint(
            SagaStep.STATE_TRANSITIONED,
            payload={"new_state": "active", "previous_state": "originated"},
            status="completed",
        )

        # ── Step 6: Create LLAS account ───────────────────────────────────────
        # LLAS account creation happens after successful ledger write.
        # If this fails, the saga is still marked complete (ledger is the source
        # of truth; LLAS creation can be retried via reconciliation).
        financial_terms = payload.get("financial_terms", {})
        try:
            await llas.create_account(
                contract_id=contract_id,
                account_data={
                    "contract_id":     contract_id,
                    "contract_type":   payload.get("contract_type", "loan"),
                    "amount_financed": financial_terms.get("amount_financed"),
                    "term_months":     financial_terms.get("term_months"),
                    "monthly_payment": financial_terms.get("monthly_payment"),
                    "origination_date": payload.get("origination_date"),
                    "dealer_id":       payload.get("dealer_id"),
                },
            )
            logger.info(
                "llas_account_created",
                contract_id=contract_id,
                event_id=event_id,
            )
        except Exception as e:
            # Non-fatal: log and continue. Reconciliation handles LLAS sync.
            logger.warning(
                "llas_account_creation_failed_non_fatal",
                contract_id=contract_id,
                event_id=event_id,
                error=str(e),
            )

        # ── Complete ──────────────────────────────────────────────────────────
        await saga.complete(
            payload={
                "record_id":  record_id,
                "data_hash":  data_hash,
                "new_state":  "active",
            }
        )

        logger.info(
            "origination_flow_completed",
            contract_id=contract_id,
            event_id=event_id,
            saga_id=saga.saga_id,
            record_id=record_id,
        )
