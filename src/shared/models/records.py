"""
Ledger record models: OriginationRecord, AccountingRecord, ContractLifecycle.
These are written to the immutable ledger (Hyperledger Fabric or PostgreSQL Phase 0).
All are PII-free — on-chain only contains hashes and non-sensitive fields.
"""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from shared.models.common import ContractState, ContractType, SourceSystem
from shared.models.entities import StateTransition


class OriginationRecord(BaseModel):
    """
    Validated origination record for the immutable ledger.
    No PII — customer represented by hash only.
    proof_token_jti must be set before calling write_record on Ledger MCP.
    """
    record_id:        UUID
    contract_id:      str
    los_system:       SourceSystem
    origination_date: date
    maturity_date:    date | None = None
    contract_type:    ContractType
    # Customer — on-chain safe
    customer_id_hash: str = Field(description="SHA-256 of customer_id")
    # Vehicle
    vin:              str = Field(pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    vehicle_make:     str | None = None
    vehicle_model:    str | None = None
    vehicle_year:     int | None = None
    # Financial terms
    amount_financed:  float = Field(ge=0)
    term_months:      int   = Field(ge=1)
    interest_rate:    float = Field(ge=0)
    monthly_payment:  float = Field(ge=0)
    residual_value:   float | None = Field(default=None, ge=0)
    down_payment:     float         = Field(default=0.0, ge=0)
    # Dealer
    dealer_id:        str
    # Integrity
    data_hash:        str  = Field(description="SHA-256 of the full off-chain contracts.documents row")
    proof_token_jti:  str  = Field(description="JWT ID of the validation proof token")
    saga_id:          UUID
    correlation_id:   UUID

    model_config = {"use_enum_values": True}


class PaymentBreakdown(BaseModel):
    principal: float = 0.0
    interest:  float = 0.0
    fees:      float = 0.0


class AccountingRecord(BaseModel):
    """
    Validated accounting/payment record for the immutable ledger. No PII.
    """
    record_id:        UUID
    contract_id:      str
    record_type:      Literal[
        "payment_applied", "fee_assessed", "balance_adjustment",
        "payoff", "late_fee", "insurance_lapse_noted"
    ]
    amount:           float
    currency:         str = "USD"
    effective_date:   date
    running_balance:  float | None = None
    applied_to:       PaymentBreakdown | None = None
    source_event_id:  UUID
    data_hash:        str
    proof_token_jti:  str
    saga_id:          UUID


class FieldChange(BaseModel):
    """A single field change in a customer profile update."""
    field:     str
    old_value: str | None = None
    new_value: str


class CustomerUpdateRecord(BaseModel):
    """
    Validated customer profile update record for the immutable ledger.
    Written when a CRM SR, portal self-service, or LOS sync changes customer data in LLAS.
    No PII values stored on-chain — field names only.
    """
    record_id:        UUID
    contract_id:      str
    source_system:    SourceSystem
    source_reference: str  = Field(description="SR number, session ID, or LOS ref from originating system")
    integration_ref:  str  = Field(description="UUID issued by Integration System for this submission")
    change_type:      Literal["contact_update", "payment_update", "insurance_update", "llas_sync"]
    field_names:      list[str] = Field(description="Names of changed fields (values not stored on-chain)")
    conflict_pair_id: str | None = None
    resolved_by:      str | None = None
    resolution_reason: str | None = None
    data_hash:        str
    proof_token_jti:  str
    saga_id:          UUID

    model_config = {"use_enum_values": True}


class ContractLifecycle(BaseModel):
    """
    Aggregate view of a contract's full state history.
    Returned by Ledger MCP get_contract_lifecycle().
    """
    contract_id:          str
    current_state:        ContractState
    origination_date:     date | None = None
    maturity_date:        date | None = None
    state_history:        list[StateTransition] = []
    total_payments_made:  int   = 0
    total_amount_paid:    float = 0.0
    current_balance:      float | None = None
    days_past_due:        int   = 0

    model_config = {"use_enum_values": True}
