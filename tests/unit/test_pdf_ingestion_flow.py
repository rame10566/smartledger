"""
Unit tests for PDFIngestionFlow

Tests the dealer.pdf_submitted saga:
  - High-confidence extraction → oracle_los.originate_contract() called
  - Low-confidence extraction → submit_for_review + saga.quarantine
  - Missing document_text → saga.fail
  - Origination failure after high-confidence extraction → saga.fail
  - All saga steps checkpointed
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.flows.pdf_ingestion import PDFIngestionFlow, HIGH_CONFIDENCE_THRESHOLD


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def contract_id():
    return "PDF-DOC-001"


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


_SAMPLE_TEXT = """
RETAIL INSTALLMENT CONTRACT

Date: 2026-03-15

Buyer: James Carter
Customer ID: CUST-TEST-001

Vehicle: 2024 Toyota Camry
VIN: 1HGBH41JXMN109186

Amount Financed: $28,500.00
Term: 72 months
APR: 6.99%
Monthly Payment: $487.50
Down Payment: $3,000.00

Dealer ID: DLR-001
"""

_MINIMAL_TEXT = "some document"


def _make_pdf_event(contract_id, event_id, document_text, dealer_id="DLR-001"):
    return {
        "event_id":      event_id,
        "event_type":    "dealer.pdf_submitted",
        "source_system": "dealer",
        "contract_id":   contract_id,
        "timestamp":     "2026-03-15T10:00:00Z",
        "correlation_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "payload": {
            "document_id":   f"DOC-{uuid.uuid4().hex[:8].upper()}",
            "document_text": document_text,
            "dealer_id":     dealer_id,
        },
    }


def _mock_high_confidence_extraction(extraction_id=None):
    eid = extraction_id or str(uuid.uuid4())
    return {
        "extraction_id":     eid,
        "document_id":       "DOC-001",
        "overall_confidence": HIGH_CONFIDENCE_THRESHOLD + 0.05,  # just above threshold
        "extraction_quality": "high",
        "needs_review":      False,
        "contract_data": {
            "contract_type": "loan",
            "vin":           "1HGBH41JXMN109186",
            "vehicle":       {"vin": "1HGBH41JXMN109186", "year": 2024, "make": "Toyota", "model": "Camry"},
            "customer":      {"customer_id": "CUST-001", "first_name": "James", "last_name": "Carter", "email": ""},
            "financial_terms": {"amount_financed": 28500.0, "term_months": 72, "interest_rate": 6.99,
                                "monthly_payment": 487.50, "down_payment": 3000.0},
            "dealer_id":     "DLR-001",
            "origination_date": "2026-03-15",
        },
        "confidence_scores": {"vin": 0.98, "amount_financed": 0.95},
    }


def _mock_low_confidence_extraction(extraction_id=None):
    eid = extraction_id or str(uuid.uuid4())
    return {
        "extraction_id":     eid,
        "document_id":       "DOC-002",
        "overall_confidence": 0.55,   # below threshold
        "extraction_quality": "low",
        "needs_review":      True,
        "contract_data":     {},
        "confidence_scores": {},
    }


def _mock_origination_result():
    return {
        "success":        True,
        "contract_id":    f"ORC-TEST-{uuid.uuid4().hex[:6].upper()}",
        "stream_entry_id": "1-1",
        "correlation_id": str(uuid.uuid4()),
    }


def _mock_review_result():
    return {"queued": True, "extraction_id": "some-id", "queue_position": 1}


# ─── High-confidence path ─────────────────────────────────────────────────────

class TestPDFIngestionFlowHighConfidence:

    @patch("agent.flows.pdf_ingestion.oracle_los")
    @patch("agent.flows.pdf_ingestion.semantic_ai")
    async def test_high_confidence_triggers_origination(
        self,
        mock_semantic_ai,
        mock_oracle_los,
        mock_saga,
        contract_id,
        event_id,
    ):
        """High-confidence extraction must call oracle_los.originate_contract()."""
        mock_semantic_ai.extract_contract_fields = AsyncMock(
            return_value=_mock_high_confidence_extraction()
        )
        mock_oracle_los.originate_contract = AsyncMock(return_value=_mock_origination_result())

        flow  = PDFIngestionFlow()
        event = _make_pdf_event(contract_id, event_id, _SAMPLE_TEXT)
        await flow(saga=mock_saga, event=event)

        mock_oracle_los.originate_contract.assert_awaited_once()
        mock_saga.complete.assert_awaited_once()
        mock_saga.quarantine.assert_not_called()

    @patch("agent.flows.pdf_ingestion.oracle_los")
    @patch("agent.flows.pdf_ingestion.semantic_ai")
    async def test_submit_for_review_not_called_on_high_confidence(
        self,
        mock_semantic_ai,
        mock_oracle_los,
        mock_saga,
        contract_id,
        event_id,
    ):
        """submit_for_review should NOT be called for high-confidence extraction."""
        mock_semantic_ai.extract_contract_fields = AsyncMock(
            return_value=_mock_high_confidence_extraction()
        )
        mock_oracle_los.originate_contract = AsyncMock(return_value=_mock_origination_result())

        flow  = PDFIngestionFlow()
        event = _make_pdf_event(contract_id, event_id, _SAMPLE_TEXT)
        await flow(saga=mock_saga, event=event)

        mock_semantic_ai.submit_for_review.assert_not_called()

    @patch("agent.flows.pdf_ingestion.oracle_los")
    @patch("agent.flows.pdf_ingestion.semantic_ai")
    async def test_complete_payload_includes_extraction_id_and_new_contract(
        self,
        mock_semantic_ai,
        mock_oracle_los,
        mock_saga,
        contract_id,
        event_id,
    ):
        """saga.complete payload must include extraction_id and new_contract_id."""
        extraction_id = str(uuid.uuid4())
        mock_semantic_ai.extract_contract_fields = AsyncMock(
            return_value=_mock_high_confidence_extraction(extraction_id)
        )
        origination = _mock_origination_result()
        mock_oracle_los.originate_contract = AsyncMock(return_value=origination)

        flow  = PDFIngestionFlow()
        event = _make_pdf_event(contract_id, event_id, _SAMPLE_TEXT)
        await flow(saga=mock_saga, event=event)

        complete_call = mock_saga.complete.call_args
        payload = complete_call.kwargs.get("payload", {})
        assert payload.get("extraction_id") == extraction_id
        assert payload.get("new_contract_id") == origination["contract_id"]


# ─── Low-confidence path ──────────────────────────────────────────────────────

class TestPDFIngestionFlowLowConfidence:

    @patch("agent.flows.pdf_ingestion.oracle_los")
    @patch("agent.flows.pdf_ingestion.semantic_ai")
    async def test_low_confidence_queues_for_review_and_quarantines(
        self,
        mock_semantic_ai,
        mock_oracle_los,
        mock_saga,
        contract_id,
        event_id,
    ):
        """Low-confidence → submit_for_review + saga.quarantine. NO origination."""
        mock_semantic_ai.extract_contract_fields = AsyncMock(
            return_value=_mock_low_confidence_extraction()
        )
        mock_semantic_ai.submit_for_review = AsyncMock(return_value=_mock_review_result())

        flow  = PDFIngestionFlow()
        event = _make_pdf_event(contract_id, event_id, _MINIMAL_TEXT)
        await flow(saga=mock_saga, event=event)

        mock_semantic_ai.submit_for_review.assert_awaited_once()
        mock_saga.quarantine.assert_awaited_once()
        mock_oracle_los.originate_contract.assert_not_called()
        mock_saga.complete.assert_not_called()

    @patch("agent.flows.pdf_ingestion.oracle_los")
    @patch("agent.flows.pdf_ingestion.semantic_ai")
    async def test_quarantine_failure_code_is_low_extraction_confidence(
        self,
        mock_semantic_ai,
        mock_oracle_los,
        mock_saga,
        contract_id,
        event_id,
    ):
        """Quarantine failure code must be LOW_EXTRACTION_CONFIDENCE."""
        mock_semantic_ai.extract_contract_fields = AsyncMock(
            return_value=_mock_low_confidence_extraction()
        )
        mock_semantic_ai.submit_for_review = AsyncMock(return_value=_mock_review_result())

        flow  = PDFIngestionFlow()
        event = _make_pdf_event(contract_id, event_id, _MINIMAL_TEXT)
        await flow(saga=mock_saga, event=event)

        quarantine_call = mock_saga.quarantine.call_args
        failures = quarantine_call.args[0] if quarantine_call.args else quarantine_call.kwargs.get("failures", [])
        assert len(failures) >= 1
        assert failures[0]["code"] == "LOW_EXTRACTION_CONFIDENCE"


# ─── Error paths ─────────────────────────────────────────────────────────────

class TestPDFIngestionFlowErrors:

    @patch("agent.flows.pdf_ingestion.oracle_los")
    @patch("agent.flows.pdf_ingestion.semantic_ai")
    async def test_missing_document_text_calls_saga_fail(
        self,
        mock_semantic_ai,
        mock_oracle_los,
        mock_saga,
        contract_id,
        event_id,
    ):
        """Empty document_text must immediately fail the saga."""
        flow  = PDFIngestionFlow()
        event = _make_pdf_event(contract_id, event_id, "")   # empty text
        await flow(saga=mock_saga, event=event)

        mock_saga.fail.assert_awaited_once()
        mock_semantic_ai.extract_contract_fields.assert_not_called()

    @patch("agent.flows.pdf_ingestion.oracle_los")
    @patch("agent.flows.pdf_ingestion.semantic_ai")
    async def test_origination_failure_calls_saga_fail(
        self,
        mock_semantic_ai,
        mock_oracle_los,
        mock_saga,
        contract_id,
        event_id,
    ):
        """If originate_contract raises an error, saga.fail is called."""
        mock_semantic_ai.extract_contract_fields = AsyncMock(
            return_value=_mock_high_confidence_extraction()
        )
        mock_oracle_los.originate_contract = AsyncMock(
            side_effect=Exception("Oracle LOS unavailable")
        )

        flow  = PDFIngestionFlow()
        event = _make_pdf_event(contract_id, event_id, _SAMPLE_TEXT)
        await flow(saga=mock_saga, event=event)

        mock_saga.fail.assert_awaited_once()
        mock_saga.complete.assert_not_called()

    @patch("agent.flows.pdf_ingestion.oracle_los")
    @patch("agent.flows.pdf_ingestion.semantic_ai")
    async def test_saga_checkpointed_through_all_steps(
        self,
        mock_semantic_ai,
        mock_oracle_los,
        mock_saga,
        contract_id,
        event_id,
    ):
        """All saga steps must be checkpointed on the high-confidence path."""
        mock_semantic_ai.extract_contract_fields = AsyncMock(
            return_value=_mock_high_confidence_extraction()
        )
        mock_oracle_los.originate_contract = AsyncMock(return_value=_mock_origination_result())

        flow  = PDFIngestionFlow()
        event = _make_pdf_event(contract_id, event_id, _SAMPLE_TEXT)
        await flow(saga=mock_saga, event=event)

        checkpoint_steps = [
            str(call.args[0]) if call.args else str(call.kwargs.get("step", ""))
            for call in mock_saga.checkpoint.call_args_list
        ]
        assert "PDF_RECEIVED" in checkpoint_steps
        assert "FIELDS_EXTRACTED" in checkpoint_steps
        assert "CONFIDENCE_EVALUATED" in checkpoint_steps
        assert "ORIGINATION_TRIGGERED" in checkpoint_steps
