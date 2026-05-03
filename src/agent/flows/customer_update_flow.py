"""
Customer Profile Update Flow

Handles integration.* events — customer data changes submitted by CRM, Portal,
Mobile App, or LOS via the Integration System.

Supported event types:
  - integration.contact_update_requested
  - integration.payment_update_requested
  - integration.insurance_update_requested
  - integration.llas_sync_requested
  - integration.conflict_resolved  (post-resolution write)

Steps (happy path):
  1. CONTEXT_GATHERED   — fetch LLAS customer profile + ledger state
  2. VALIDATED          — call Validation Engine: validate_event → proof_token
                          (conflict check included in validation)
  3. PROOF_TOKEN_ISSUED — proof token received (value redacted)
  4. LEDGER_WRITTEN     — write customer_update record to ledger
  5. COMPLETED          — update LLAS customer profile, update integration status

Unhappy paths:
  - QUARANTINED         — validation fails (bad field, state ineligible, stale sync)
  - QUARANTINED_CONFLICT — competing update from different source on same field

Called by AgentEventLoop for integration.* events.
"""

import hashlib
import json
from typing import Any

from shared.logging import get_logger
from shared.models.saga import SagaStep

from agent.core.mcp_client import (
    integration_system,
    ledger,
    llas,
    validation,
)
from agent.core.saga import SagaManager

logger = get_logger(__name__)

# Map event_type → change_type for the ledger record
_EVENT_TO_CHANGE_TYPE: dict[str, str] = {
    "integration.contact_update_requested":   "contact_update",
    "integration.payment_update_requested":   "payment_update",
    "integration.insurance_update_requested": "insurance_update",
    "integration.llas_sync_requested":        "llas_sync",
    "integration.conflict_resolved":          "contact_update",  # resolved value type varies
}


class CustomerUpdateFlow:
    """
    Handles integration.* events for customer profile updates.

    Usage:
        flow = CustomerUpdateFlow()
        for event_type in _EVENT_TO_CHANGE_TYPE:
            event_loop.register_flow(event_type, flow)
    """

    async def __call__(self, saga: SagaManager, event: dict[str, Any]) -> None:
        """Entry point called by AgentEventLoop."""
        contract_id    = event["contract_id"]
        event_id       = event["event_id"]
        event_type     = event["event_type"]
        source_system  = event["source_system"]
        payload        = event["payload"]
        timestamp      = event.get("timestamp", "")
        correlation_id = event.get("correlation_id", "")
        schema_version = event.get("schema_version", "1.0")

        # For conflict_resolved events the proof_token is embedded in payload
        is_resolution = event_type == "integration.conflict_resolved"

        logger.info(
            "customer_update_flow_started",
            contract_id=contract_id,
            event_id=event_id,
            event_type=event_type,
            source_system=source_system,
            saga_id=saga.saga_id,
        )

        # ── Step 1: Gather context ─────────────────────────────────────────────
        await saga.checkpoint(
            SagaStep.CONTEXT_GATHERED,
            payload={"status": "gathering"},
            status="in_progress",
        )

        llas_profile = await llas.get_customer_profile(contract_id)
        try:
            ledger_state = await ledger.get_state(contract_id)
        except Exception:
            # Contract may predate this stack (e.g. LLAS-seeded demo contracts).
            # Default to empty — validation will treat missing state as active.
            ledger_state = {}

        # For conflict_resolved: get last customer_update record timestamp for stale-sync check
        last_customer_update_at: str | None = None
        try:
            lifecycle = await ledger.get_contract_lifecycle(contract_id)
            records = lifecycle.get("records", []) if isinstance(lifecycle, dict) else []
            update_records = [r for r in records if r.get("record_type") == "customer_update"]
            if update_records:
                last_customer_update_at = max(
                    r.get("created_at", "") for r in update_records
                )
        except Exception:
            pass

        context: dict[str, Any] = {
            "llas_profile":            llas_profile,
            "ledger_state":            ledger_state,
            "last_customer_update_at": last_customer_update_at,
        }

        await saga.checkpoint(
            SagaStep.CONTEXT_GATHERED,
            payload={"llas_found": llas_profile.get("found", False)},
            status="completed",
        )

        # ── Step 2: Validate (or use embedded proof token for conflict_resolved) ─
        await saga.checkpoint(
            SagaStep.VALIDATED,
            payload={"status": "validating"},
            status="in_progress",
        )

        if is_resolution:
            # Conflict_resolved: proof token was already issued by resolve_conflict tool
            proof_token = payload.get("proof_token", "")
            if not proof_token:
                logger.error(
                    "conflict_resolved_missing_proof_token",
                    contract_id=contract_id,
                    event_id=event_id,
                )
                await saga.fail(payload={"reason": "conflict_resolved event missing proof_token"})
                return

            await saga.checkpoint(
                SagaStep.VALIDATED,
                payload={"valid": True, "source": "conflict_resolution"},
                status="completed",
            )
        else:
            # Normal validation
            validation_request: dict[str, Any] = {
                "event_envelope": {
                    "event_id":       event_id,
                    "event_type":     event_type,
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
                is_conflict = any(f.get("code") == "CONFLICT_PENDING" for f in failures)

                logger.warning(
                    "customer_update_quarantined",
                    contract_id=contract_id,
                    event_id=event_id,
                    saga_id=saga.saga_id,
                    failure_count=len(failures),
                    is_conflict=is_conflict,
                )

                step = SagaStep.QUARANTINED
                await saga.checkpoint(
                    SagaStep.VALIDATED,
                    payload={"valid": False, "failures": failures},
                    status="completed",
                )
                await saga.quarantine(failures)

                # Update integration status
                integration_ref = payload.get("integration_ref", "")
                if integration_ref:
                    try:
                        await integration_system.update_integration_status(
                            integration_ref=integration_ref,
                            status="conflict" if is_conflict else "quarantined",
                            detail=failures[0].get("message", "") if failures else "",
                        )
                    except Exception as e:
                        logger.warning("integration_status_update_failed", error=str(e))
                return

            proof_token = validation_result.get("proof_token", "")
            await saga.checkpoint(
                SagaStep.VALIDATED,
                payload={"valid": True, "warnings": validation_result.get("warnings", [])},
                status="completed",
            )

        # ── Step 3: Proof token received ──────────────────────────────────────
        await saga.checkpoint(
            SagaStep.PROOF_TOKEN_ISSUED,
            payload={"jti": "***REDACTED***"},
            status="completed",
        )

        # ── Step 4: Write customer_update record to ledger ────────────────────
        await saga.checkpoint(
            SagaStep.LEDGER_WRITTEN,
            payload={"status": "writing"},
            status="in_progress",
        )

        changes = payload.get("changes", {})
        source_ref = payload.get("source_ref", "")
        integration_ref = payload.get("integration_ref", "")
        conflict_pair_id = payload.get("conflict_pair_id")
        resolved_by = payload.get("admin_id") if is_resolution else None
        resolution_reason = payload.get("reason") if is_resolution else None

        # Build data hash from change content (field names only — no PII values on-chain)
        field_names = sorted(changes.keys())
        hash_input = json.dumps({
            "contract_id":    contract_id,
            "event_type":     event_type,
            "source_system":  source_system,
            "field_names":    field_names,
            "integration_ref": integration_ref,
        }, sort_keys=True)
        data_hash = hashlib.sha256(hash_input.encode()).hexdigest()

        customer_update_record: dict[str, Any] = {
            "contract_id":      contract_id,
            "record_type":      "customer_update",
            "saga_id":          saga.saga_id,
            "event_id":         event_id,
            "source_system":    source_system,
            "source_reference": source_ref,
            "integration_ref":  integration_ref,
            "change_type":      _EVENT_TO_CHANGE_TYPE.get(event_type, "contact_update"),
            "field_names":      field_names,
            "conflict_pair_id": conflict_pair_id,
            "resolved_by":      resolved_by,
            "resolution_reason": resolution_reason,
            "data_hash":        data_hash,
        }

        write_result = await ledger.write_record(
            record=customer_update_record,
            proof_token=proof_token,
        )

        record_id = write_result.get("record_id", "")
        await saga.checkpoint(
            SagaStep.LEDGER_WRITTEN,
            payload={
                "record_id":         record_id,
                "data_hash":         data_hash,
                "write_guard_active": write_result.get("write_guard_active", True),
            },
            status="completed",
        )

        logger.info(
            "customer_update_ledger_written",
            contract_id=contract_id,
            event_id=event_id,
            record_id=record_id,
            change_type=customer_update_record["change_type"],
        )

        # ── Step 5: Apply change to LLAS ──────────────────────────────────────
        # For an initial llas_sync (LOS seeding LLAS before contract.originated),
        # changes carry account-creation fields (amount_financed, monthly_payment, …)
        # and the LLAS account does not yet exist → create it.
        # All other cases (contact/payment/insurance updates, llas_sync amendments)
        # update the customer profile.
        is_initial_llas_sync = (
            customer_update_record["change_type"] == "llas_sync"
            and "amount_financed" in changes
            and not (context.get("llas_profile") or {}).get("found", False)
        )
        try:
            if is_initial_llas_sync:
                await llas.create_account(
                    contract_id=contract_id,
                    account_data=changes,
                )
                logger.info(
                    "llas_account_created_via_integration",
                    contract_id=contract_id,
                    event_id=event_id,
                    source_system=source_system,
                )
            else:
                await llas.update_customer_profile(
                    contract_id=contract_id,
                    changes=changes,
                    validated_by="smartledger",
                    source_system=source_system,
                )
                logger.info(
                    "llas_customer_profile_updated",
                    contract_id=contract_id,
                    event_id=event_id,
                    changed_keys=field_names,
                )
        except Exception as e:
            # Non-fatal — ledger is source of truth; LLAS can be reconciled
            logger.warning(
                "llas_apply_failed_non_fatal",
                contract_id=contract_id,
                event_id=event_id,
                error=str(e),
            )

        # Update integration status to validated
        if integration_ref:
            try:
                await integration_system.update_integration_status(
                    integration_ref=integration_ref,
                    status="validated",
                )
            except Exception as e:
                logger.warning("integration_status_update_failed", error=str(e))

        # ── Complete ──────────────────────────────────────────────────────────
        await saga.complete(
            payload={
                "record_id":   record_id,
                "data_hash":   data_hash,
                "change_type": customer_update_record["change_type"],
                "field_names": field_names,
            }
        )

        logger.info(
            "customer_update_flow_completed",
            contract_id=contract_id,
            event_id=event_id,
            saga_id=saga.saga_id,
            record_id=record_id,
        )
