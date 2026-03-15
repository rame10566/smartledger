"""
Contract PDF Ingestion Flow

Handles dealer.pdf_submitted events — contract documents submitted as PDFs
by dealers or uploaded through the portal.

Steps:
  1. PDF_RECEIVED          — extract document_text from event payload
  2. FIELDS_EXTRACTED      — call Semantic AI MCP: extract_contract_fields()
  3. CONFIDENCE_EVALUATED  — check overall_confidence score:
       >= 0.85 → high confidence → proceed automatically
       <  0.85 → low confidence → submit_for_review() + quarantine for human
  4. ORIGINATION_TRIGGERED — call oracle_los.originate_contract() (high-confidence path)
                              This publishes contract.originated → OriginationFlow handles it
  5. COMPLETED

Called by AgentEventLoop when event_type == "dealer.pdf_submitted".
"""

from typing import Any

from shared.logging import get_logger

from agent.core.mcp_client import oracle_los, semantic_ai
from agent.core.saga import SagaManager

logger = get_logger(__name__)

HIGH_CONFIDENCE_THRESHOLD = 0.85


class PDFIngestionFlow:
    """
    Handles dealer.pdf_submitted events.

    Usage:
        flow = PDFIngestionFlow()
        event_loop.register_flow("dealer.pdf_submitted", flow)
    """

    async def __call__(self, saga: SagaManager, event: dict[str, Any]) -> None:
        """Entry point called by AgentEventLoop."""
        contract_id   = event["contract_id"]
        event_id      = event["event_id"]
        source_system = event["source_system"]
        payload       = event["payload"]

        document_id   = payload.get("document_id", event_id)
        document_text = payload.get("document_text", "")
        dealer_id     = payload.get("dealer_id", "")

        logger.info(
            "pdf_ingestion_flow_started",
            contract_id=contract_id,
            event_id=event_id,
            saga_id=saga.saga_id,
            document_id=document_id,
        )

        # ── Step 1: Receive PDF event ─────────────────────────────────────────
        await saga.checkpoint(
            "PDF_RECEIVED",
            payload={
                "document_id":   document_id,
                "has_text":      bool(document_text),
                "text_length":   len(document_text),
                "dealer_id":     dealer_id,
            },
            status="completed",
        )

        if not document_text:
            logger.warning(
                "pdf_ingestion_no_text",
                contract_id=contract_id,
                event_id=event_id,
                document_id=document_id,
            )
            await saga.fail("No document_text in payload — cannot extract fields")
            return

        # ── Step 2: Extract contract fields via Semantic AI ───────────────────
        await saga.checkpoint(
            "FIELDS_EXTRACTED",
            payload={"status": "extracting", "document_id": document_id},
            status="in_progress",
        )

        extraction = await semantic_ai.extract_contract_fields(
            document_text=document_text,
            document_id=document_id,
        )

        extraction_id       = extraction.get("extraction_id", "")
        overall_confidence  = extraction.get("overall_confidence", 0.0)
        contract_data       = extraction.get("contract_data", {})
        confidence_scores   = extraction.get("confidence_scores", {})

        await saga.checkpoint(
            "FIELDS_EXTRACTED",
            payload={
                "extraction_id":     extraction_id,
                "overall_confidence": overall_confidence,
                "extraction_quality": extraction.get("extraction_quality", "unknown"),
            },
            status="completed",
        )

        logger.info(
            "pdf_fields_extracted",
            contract_id=contract_id,
            document_id=document_id,
            extraction_id=extraction_id,
            overall_confidence=overall_confidence,
        )

        # ── Step 3: Evaluate confidence ───────────────────────────────────────
        needs_review = overall_confidence < HIGH_CONFIDENCE_THRESHOLD

        await saga.checkpoint(
            "CONFIDENCE_EVALUATED",
            payload={
                "overall_confidence":   overall_confidence,
                "needs_review":         needs_review,
                "threshold":            HIGH_CONFIDENCE_THRESHOLD,
            },
            status="completed",
        )

        if needs_review:
            # Low confidence — send to human review queue
            logger.warning(
                "pdf_low_confidence_review_required",
                contract_id=contract_id,
                extraction_id=extraction_id,
                overall_confidence=overall_confidence,
            )

            try:
                await semantic_ai.submit_for_review(
                    extraction_id=extraction_id,
                    reason=(
                        f"Overall confidence {overall_confidence:.2%} is below the "
                        f"{HIGH_CONFIDENCE_THRESHOLD:.0%} threshold. "
                        "Human verification required before origination."
                    ),
                )
            except Exception as e:
                logger.warning(
                    "pdf_submit_for_review_failed",
                    extraction_id=extraction_id,
                    error=str(e),
                )

            await saga.quarantine([{
                "code":    "LOW_EXTRACTION_CONFIDENCE",
                "message": (
                    f"PDF extraction confidence {overall_confidence:.2%} < "
                    f"{HIGH_CONFIDENCE_THRESHOLD:.0%} threshold. "
                    "Queued for human review in Semantic AI dashboard."
                ),
                "extraction_id": extraction_id,
            }])
            return

        # ── Step 4: High confidence — trigger origination ─────────────────────
        await saga.checkpoint(
            "ORIGINATION_TRIGGERED",
            payload={"status": "triggering", "extraction_id": extraction_id},
            status="in_progress",
        )

        # Inject dealer_id from the PDF event if not in the extracted data
        if dealer_id and not contract_data.get("dealer_id"):
            contract_data["dealer_id"] = dealer_id

        # originate_contract() publishes contract.originated → OriginationFlow handles it
        try:
            origination_result = await oracle_los.originate_contract(contract_data)
            new_contract_id = origination_result.get("contract_id", "")

            await saga.checkpoint(
                "ORIGINATION_TRIGGERED",
                payload={
                    "new_contract_id":  new_contract_id,
                    "stream_entry_id":  origination_result.get("stream_entry_id"),
                    "extraction_id":    extraction_id,
                    "overall_confidence": overall_confidence,
                },
                status="completed",
            )

            logger.info(
                "pdf_origination_triggered",
                original_contract_id=contract_id,
                new_contract_id=new_contract_id,
                extraction_id=extraction_id,
                overall_confidence=overall_confidence,
            )

        except Exception as e:
            logger.error(
                "pdf_origination_failed",
                contract_id=contract_id,
                extraction_id=extraction_id,
                error=str(e),
            )
            await saga.fail(f"Origination call failed after high-confidence extraction: {e}")
            return

        # ── Complete ──────────────────────────────────────────────────────────
        await saga.complete(
            payload={
                "extraction_id":     extraction_id,
                "overall_confidence": overall_confidence,
                "new_contract_id":   new_contract_id,
            }
        )

        logger.info(
            "pdf_ingestion_flow_completed",
            contract_id=contract_id,
            new_contract_id=new_contract_id,
            event_id=event_id,
            saga_id=saga.saga_id,
        )
