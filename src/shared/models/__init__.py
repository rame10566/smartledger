"""
SmartLedger Shared Models
All Pydantic models used across agent, MCP servers, and dashboard API.
Import from here: `from shared.models import EventEnvelope, ValidationResult, ...`
"""
from shared.models.common import (
    Address,
    ContractState,
    ContractType,
    EventEnvelope,
    EventType,
    Money,
    SourceSystem,
)
from shared.models.entities import (
    Contract,
    Customer,
    FinancialTerms,
    StateTransition,
    Vehicle,
)
from shared.models.records import (
    AccountingRecord,
    ContractLifecycle,
    OriginationRecord,
    PaymentBreakdown,
)
from shared.models.saga import (
    SagaCheckpoint,
    SagaStatus,
    SagaStep,
)
from shared.models.validation import (
    ProofTokenClaims,
    QuarantineRecord,
    ValidationContext,
    ValidationFailure,
    ValidationRequest,
    ValidationResult,
    ValidationWarning,
)

__all__ = [
    # common
    "Address", "ContractState", "ContractType", "EventEnvelope",
    "EventType", "Money", "SourceSystem",
    # entities
    "Contract", "Customer", "FinancialTerms", "StateTransition", "Vehicle",
    # records
    "AccountingRecord", "ContractLifecycle", "OriginationRecord", "PaymentBreakdown",
    # saga
    "SagaCheckpoint", "SagaStatus", "SagaStep",
    # validation
    "ProofTokenClaims", "QuarantineRecord", "ValidationContext",
    "ValidationFailure", "ValidationRequest", "ValidationResult", "ValidationWarning",
]
