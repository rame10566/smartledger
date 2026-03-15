"""
Validation models: ValidationRequest, ValidationResult, ProofToken, QuarantineRecord.

Proof Token design (Signed JWT):
  - Issued by Validation Engine MCP on successful validation
  - Claims: jti (unique token ID), contract_id, event_id, saga_id, iat, exp (+60s)
  - Signed with PROOF_TOKEN_SECRET (shared between Validation MCP + Ledger MCP)
  - Single-use: Ledger MCP records the jti in validation.used_proof_tokens after use
  - On-chain: jti stored in the record as proof_token_jti
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from shared.models.common import EventEnvelope


# ─── Validation Request ───────────────────────────────────────────────────────

class ValidationContext(BaseModel):
    """Cross-system context gathered by the agent before calling validate_event."""
    oracle_los_contract:     dict[str, Any] | None = None
    salesforce_los_contract: dict[str, Any] | None = None
    llas_account:            dict[str, Any] | None = None
    crm_customer:            dict[str, Any] | None = None
    dealer_submission:       dict[str, Any] | None = None
    insurance_policy:        dict[str, Any] | None = None
    ledger_state:            dict[str, Any] | None = None
    payment_record:          dict[str, Any] | None = None


class ValidationRequest(BaseModel):
    """Input to Validation Engine MCP validate_event tool."""
    event_envelope: EventEnvelope
    saga_id:        UUID
    context:        ValidationContext


# ─── Validation Result ────────────────────────────────────────────────────────

class ValidationFailure(BaseModel):
    rule_id:   str
    rule_type: Literal["schema", "cross_system", "business", "sequence", "duplicate"]
    code:      str    = Field(description="Machine-readable code e.g. VIN_MISMATCH, DUPLICATE_EVENT")
    message:   str    = Field(description="Human-readable description for dashboard display")
    field:     str | None = None
    expected:  Any | None = None
    actual:    Any | None = None


class ValidationWarning(BaseModel):
    code:    str
    message: str


class ValidationResult(BaseModel):
    """Output from Validation Engine MCP validate_event tool."""
    valid:       bool
    event_id:    UUID
    contract_id: str
    saga_id:     UUID
    checked_at:  datetime
    # Present only when valid=True
    proof_token: str | None = Field(
        default=None,
        description="Signed JWT proof token. Single-use, 60s expiry. Present only when valid=True."
    )
    # Present when valid=False
    failures:    list[ValidationFailure] = []
    warnings:    list[ValidationWarning] = []


# ─── Proof Token (JWT Claims) ─────────────────────────────────────────────────

class ProofTokenClaims(BaseModel):
    """
    Claims embedded in the validation proof token JWT.
    Verified by Ledger MCP before any write_record call.
    """
    jti:         str   = Field(description="Unique token ID — stored after use to prevent replay")
    contract_id: str
    event_id:    str
    saga_id:     str
    iat:         int   = Field(description="Issued at (Unix timestamp)")
    exp:         int   = Field(description="Expires at (Unix timestamp, iat + 60)")


# ─── Quarantine Record ────────────────────────────────────────────────────────

class QuarantineRecord(BaseModel):
    """A quarantined event awaiting human review in the Governance Dashboard."""
    event_id:         UUID
    contract_id:      str
    event_type:       str
    source_system:    str
    failures:         list[ValidationFailure]
    context_snapshot: dict[str, Any] | None = None
    original_payload: dict[str, Any]
    status:           Literal["pending", "approved", "rejected", "escalated"] = "pending"
    escalation_level: int = Field(default=0, ge=0, le=2, description="0=operator, 1=team_lead, 2=manager")
    reviewed_by:      str | None = None
    reviewed_at:      datetime | None = None
    override_reason:  str | None = None
    created_at:       datetime
    sla_deadline:     datetime = Field(description="created_at + 24h — first escalation trigger")
