"""
Semantic AI Engine MCP Server

Extracts structured contract fields from unstructured PDF/document text.
Uses pattern matching + confidence scoring to simulate Claude-based extraction.
Low-confidence extractions are queued for human review.

Tools:
  - extract_contract_fields(document_text, document_id?) → extracted fields + confidence scores
  - get_extraction_result(extraction_id)                 → retrieve a previous extraction
  - submit_for_review(extraction_id, reason?)            → queue extraction for human review
  - list_review_queue()                                  → pending human review items

Architecture note:
  For the POC this server uses rule-based extraction (regex + heuristics) to
  simulate the AI extraction pipeline. The confidence model approximates real
  extraction quality: structured text scores high, ambiguous text scores low.
  Phase 2+ will use the Anthropic Claude API for real extraction.
"""

import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.config import get_settings
from shared.logging import configure_logging, get_logger

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("semantic-ai", settings.log_level)
logger = get_logger(__name__)

# Confidence thresholds
HIGH_CONFIDENCE = 0.85   # auto-proceed to origination
LOW_CONFIDENCE  = 0.60   # below this → must go to human review

# ─── Module-level state ───────────────────────────────────────────────────────

_extractions: dict[str, dict[str, Any]] = {}   # extraction_id → result
_review_queue: list[dict[str, Any]] = []        # pending human review items


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    logger.info("semantic_ai_started")
    yield
    logger.info("semantic_ai_shutdown")


mcp = FastMCP(
    name="smartledger-semantic-ai",
    instructions=(
        "Semantic AI Engine for SmartLedger. Extracts structured contract fields from "
        "unstructured document text. Returns extracted fields with per-field confidence scores. "
        "High-confidence extractions (>= 0.85) can proceed directly to origination. "
        "Low-confidence extractions should be routed to human review."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


# ─── Extraction helpers ────────────────────────────────────────────────────────

def _try_extract(pattern: str, text: str, group: int = 1) -> str | None:
    """Return the first capture group from a regex match, or None."""
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(group).strip() if m else None


def _confidence(value: Any, high: float = 0.92, low: float = 0.55) -> float:
    """Return high confidence if value was found, low if not."""
    return high if value is not None else low


def _extract_fields_from_text(text: str) -> dict[str, Any]:
    """
    Rule-based extraction from document text.

    Looks for labelled fields using common contract document patterns.
    Returns a dict of extracted field values (None if not found).
    """
    # Contract type
    contract_type = None
    if re.search(r"\b(retail installment|loan agreement|auto loan)\b", text, re.I):
        contract_type = "loan"
    elif re.search(r"\b(lease agreement|closed.end lease|operating lease)\b", text, re.I):
        contract_type = "lease"

    # VIN
    vin = _try_extract(r"\bVIN[:\s#]*([A-HJ-NPR-Z0-9]{17})\b", text)

    # Vehicle
    vehicle_year  = _try_extract(r"\b(20\d{2}|19\d{2})\b(?=\s+[A-Z][a-z])", text)
    vehicle_make  = _try_extract(r"(?:Year|Vehicle)[:\s]*\d{4}\s+([A-Z][a-zA-Z]+)", text)
    vehicle_model = _try_extract(r"(?:Make)[:\s]*[A-Za-z]+\s+([A-Z][a-zA-Z0-9\s]+?)(?:\s+\d|\n|,)", text)

    # Customer
    customer_first = _try_extract(r"(?:Buyer|Customer|Borrower)[:\s]*([A-Z][a-z]+)", text)
    customer_last  = _try_extract(r"(?:Buyer|Customer|Borrower)[:\s]*[A-Z][a-z]+\s+([A-Z][a-zA-Z-]+)", text)
    customer_id    = _try_extract(r"Customer\s*(?:ID|#|No)[:\s]*([A-Z0-9-]+)", text)

    # Financial terms
    amount_str = _try_extract(r"Amount\s+Financed[:\s]*\$?([\d,]+(?:\.\d{2})?)", text)
    amount_financed = float(amount_str.replace(",", "")) if amount_str else None

    term_str = _try_extract(r"Term[:\s]*(\d{1,3})\s*(?:months?|mo\.?)", text)
    term_months = int(term_str) if term_str else None

    rate_str = _try_extract(r"(?:APR|Interest\s+Rate|Annual\s+Rate)[:\s]*([\d.]+)\s*%?", text)
    interest_rate = float(rate_str) if rate_str else None

    payment_str = _try_extract(r"Monthly\s+Payment[:\s]*\$?([\d,]+(?:\.\d{2})?)", text)
    monthly_payment = float(payment_str.replace(",", "")) if payment_str else None

    down_str = _try_extract(r"Down\s+Payment[:\s]*\$?([\d,]+(?:\.\d{2})?)", text)
    down_payment = float(down_str.replace(",", "")) if down_str else None

    # Dealer
    dealer_id = _try_extract(r"Dealer\s*(?:ID|#|No)[:\s]*([A-Z0-9-]+)", text)

    # Origination date
    orig_date = _try_extract(
        r"(?:Date|Contract Date|Sale Date)[:\s]*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", text
    )

    return {
        "contract_type":   contract_type,
        "vin":             vin,
        "vehicle_year":    int(vehicle_year) if vehicle_year else None,
        "vehicle_make":    vehicle_make,
        "vehicle_model":   vehicle_model,
        "customer_first":  customer_first,
        "customer_last":   customer_last,
        "customer_id":     customer_id,
        "amount_financed": amount_financed,
        "term_months":     term_months,
        "interest_rate":   interest_rate,
        "monthly_payment": monthly_payment,
        "down_payment":    down_payment,
        "dealer_id":       dealer_id,
        "origination_date": orig_date,
    }


def _score_extraction(fields: dict[str, Any]) -> dict[str, float]:
    """
    Compute per-field confidence scores.
    Extracted values get high confidence; missing values get low confidence.
    """
    field_weights = {
        "contract_type":   (0.95, 0.40),
        "vin":             (0.98, 0.30),
        "vehicle_year":    (0.90, 0.55),
        "vehicle_make":    (0.88, 0.50),
        "vehicle_model":   (0.85, 0.50),
        "customer_first":  (0.90, 0.45),
        "customer_last":   (0.90, 0.45),
        "customer_id":     (0.92, 0.35),
        "amount_financed": (0.95, 0.40),
        "term_months":     (0.93, 0.45),
        "interest_rate":   (0.92, 0.40),
        "monthly_payment": (0.94, 0.40),
        "down_payment":    (0.85, 0.55),
        "dealer_id":       (0.90, 0.35),
        "origination_date":(0.88, 0.50),
    }

    scores: dict[str, float] = {}
    for field, (high, low) in field_weights.items():
        val = fields.get(field)
        scores[field] = high if val is not None else low
    return scores


def _overall_confidence(scores: dict[str, float]) -> float:
    """Weighted average of per-field scores, emphasising critical fields."""
    critical = {
        "contract_type", "vin", "amount_financed",
        "term_months", "interest_rate", "monthly_payment",
    }
    critical_score = sum(scores[f] for f in critical if f in scores) / len(critical)
    all_score = sum(scores.values()) / len(scores)
    # 60% weight on critical fields, 40% on all fields
    return round(0.6 * critical_score + 0.4 * all_score, 4)


def _build_contract_data(fields: dict[str, Any], confidence_scores: dict[str, float]) -> dict[str, Any]:
    """
    Assemble extracted fields into a contract_data dict suitable for oracle_los.originate_contract().
    Missing fields are filled with sensible placeholders.
    """
    customer_id = fields.get("customer_id") or f"CUST-{uuid.uuid4().hex[:6].upper()}"
    return {
        "contract_type": fields.get("contract_type") or "loan",
        "vin":           fields.get("vin", ""),
        "vehicle": {
            "vin":   fields.get("vin", ""),
            "year":  fields.get("vehicle_year") or 2024,
            "make":  fields.get("vehicle_make") or "Unknown",
            "model": fields.get("vehicle_model") or "Unknown",
            "trim":  "",
            "color": "",
        },
        "customer": {
            "customer_id": customer_id,
            "first_name":  fields.get("customer_first") or "Unknown",
            "last_name":   fields.get("customer_last") or "Unknown",
            "email":       "",
        },
        "financial_terms": {
            "amount_financed": fields.get("amount_financed") or 0.0,
            "term_months":     fields.get("term_months") or 0,
            "interest_rate":   fields.get("interest_rate") or 0.0,
            "monthly_payment": fields.get("monthly_payment") or 0.0,
            "down_payment":    fields.get("down_payment") or 0.0,
        },
        "dealer_id":        fields.get("dealer_id") or "",
        "origination_date": fields.get("origination_date") or "",
    }


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def extract_contract_fields(document_text: str, document_id: str = "") -> dict:
    """
    Extract structured contract fields from unstructured document text.

    Runs pattern-matching extraction and scores each field's confidence.
    The caller should check overall_confidence:
      >= 0.85 → high confidence, can proceed to oracle_los.originate_contract()
      <  0.85 → low confidence, call submit_for_review() for human verification
      <  0.60 → very low confidence, extraction likely failed

    Args:
        document_text: raw text content extracted from a PDF or document
        document_id:   optional external identifier for the source document

    Returns: {
        extraction_id, document_id, overall_confidence,
        confidence_scores: {field: score, ...},
        contract_data: {...},   ← ready to pass to oracle_los.originate_contract()
        needs_review: bool,     ← True if overall_confidence < 0.85
        extracted_at: ISO datetime
    }
    """
    if not document_text or not document_text.strip():
        raise ValueError("document_text cannot be empty")

    extraction_id = str(uuid.uuid4())
    doc_id = document_id or extraction_id

    raw_fields = _extract_fields_from_text(document_text)
    confidence_scores = _score_extraction(raw_fields)
    overall = _overall_confidence(confidence_scores)
    contract_data = _build_contract_data(raw_fields, confidence_scores)

    result: dict[str, Any] = {
        "extraction_id":     extraction_id,
        "document_id":       doc_id,
        "overall_confidence": overall,
        "confidence_scores": confidence_scores,
        "raw_fields":        raw_fields,
        "contract_data":     contract_data,
        "needs_review":      overall < HIGH_CONFIDENCE,
        "extraction_quality": (
            "high"   if overall >= HIGH_CONFIDENCE else
            "medium" if overall >= LOW_CONFIDENCE  else
            "low"
        ),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "status": "extracted",
    }

    _extractions[extraction_id] = result

    logger.info(
        "fields_extracted",
        extraction_id=extraction_id,
        document_id=doc_id,
        overall_confidence=overall,
        needs_review=result["needs_review"],
    )

    return result


@mcp.tool()
async def get_extraction_result(extraction_id: str) -> dict:
    """
    Retrieve a previous extraction result by extraction_id.

    Returns {found: True, ...result} or {found: False, extraction_id}.
    """
    result = _extractions.get(extraction_id)
    if result is None:
        return {"found": False, "extraction_id": extraction_id}
    return {"found": True, **result}


@mcp.tool()
async def submit_for_review(extraction_id: str, reason: str = "") -> dict:
    """
    Submit a low-confidence extraction for human review.

    The extraction is added to the review queue. A human reviewer will
    verify or correct the extracted fields in the Governance Dashboard.

    Args:
        extraction_id: the extraction to queue for review
        reason:        optional note about why review is needed

    Returns: {queued: True, extraction_id, queue_position}
    """
    result = _extractions.get(extraction_id)
    if result is None:
        return {"queued": False, "reason": f"Extraction '{extraction_id}' not found"}

    if result.get("status") == "in_review":
        return {"queued": True, "extraction_id": extraction_id, "note": "Already in review queue"}

    result["status"] = "in_review"
    result["review_reason"] = reason
    result["queued_at"] = datetime.now(timezone.utc).isoformat()

    _review_queue.append({
        "extraction_id": extraction_id,
        "document_id":   result.get("document_id"),
        "overall_confidence": result.get("overall_confidence"),
        "reason":        reason,
        "queued_at":     result["queued_at"],
    })

    logger.info(
        "extraction_queued_for_review",
        extraction_id=extraction_id,
        reason=reason,
        queue_size=len(_review_queue),
    )

    return {
        "queued":         True,
        "extraction_id":  extraction_id,
        "queue_position": len(_review_queue),
        "overall_confidence": result.get("overall_confidence"),
    }


@mcp.tool()
async def list_review_queue() -> list[dict]:
    """
    Return all extractions currently pending human review.
    Ordered by queued_at (oldest first — highest priority).
    """
    pending = [
        item for item in _review_queue
        if _extractions.get(item["extraction_id"], {}).get("status") == "in_review"
    ]
    return sorted(pending, key=lambda x: x.get("queued_at", ""))


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "semantic_ai"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = 8003
    mcp.run(transport="streamable-http")
