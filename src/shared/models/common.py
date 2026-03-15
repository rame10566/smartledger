"""
Common/reusable Pydantic models used across all SmartLedger services.
"""
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────

class SourceSystem(StrEnum):
    ORACLE_LOS       = "oracle_los"
    SALESFORCE_LOS   = "salesforce_los"
    LLAS             = "llas"
    CRM              = "crm"
    PAYMENT          = "payment"
    INSURANCE        = "insurance"
    DEALER           = "dealer"
    CUSTOMER_PORTAL  = "customer_portal"
    MOBILE_APP       = "mobile_app"
    IVR              = "ivr"
    AGENT            = "agent"
    DASHBOARD        = "dashboard"


class EventType(StrEnum):
    CONTRACT_ORIGINATED       = "contract.originated"
    DEALER_SUBMITTED          = "dealer.submitted"
    DEALER_PDF_SUBMITTED      = "dealer.pdf_submitted"
    PAYMENT_RECEIVED          = "payment.received"
    PAYMENT_MISSED            = "payment.missed"
    INSURANCE_VERIFIED        = "insurance.verified"
    INSURANCE_LAPSED          = "insurance.lapsed"
    CUSTOMER_PAYMENT_SUBMITTED = "customer.payment_submitted"
    CUSTOMER_PAYOFF_REQUESTED  = "customer.payoff_requested"
    IVR_PAYMENT_SUBMITTED     = "ivr.payment_submitted"
    IVR_CALLBACK_REQUESTED    = "ivr.callback_requested"
    REPORT_REQUESTED          = "report.requested"
    QUARANTINE_PENDING        = "quarantine.pending"
    QUARANTINE_APPROVED       = "quarantine.approved"
    QUARANTINE_REJECTED       = "quarantine.rejected"


class ContractState(StrEnum):
    ORIGINATED      = "originated"
    ACTIVE          = "active"
    DELINQUENT      = "delinquent"
    PAID_OFF        = "paid_off"
    CHARGED_OFF     = "charged_off"
    IN_REPOSSESSION = "in_repossession"
    TITLE_RELEASED  = "title_released"


class ContractType(StrEnum):
    LOAN  = "loan"
    LEASE = "lease"


# ─── Value Objects ────────────────────────────────────────────────────────────

class Money(BaseModel):
    """A monetary amount with currency."""
    amount:   float = Field(ge=0)
    currency: str   = Field(default="USD", pattern=r"^[A-Z]{3}$")


class Address(BaseModel):
    """A US mailing address. PII — stored off-chain only."""
    street1: str
    street2: str | None = None
    city:    str
    state:   str = Field(pattern=r"^[A-Z]{2}$")
    zip:     str = Field(pattern=r"^\d{5}(-\d{4})?$")
    country: str = "US"


# ─── Event Envelope ───────────────────────────────────────────────────────────

class EventEnvelope(BaseModel):
    """
    Standard wrapper for all events on the Redis Streams event bus.
    Every event published to the bus must conform to this model.
    """
    event_id:       UUID
    event_type:     EventType
    source_system:  SourceSystem
    contract_id:    str
    timestamp:      datetime
    correlation_id: UUID
    schema_version: str = "1.0"
    payload:        dict[str, Any]

    model_config = {"use_enum_values": True}
