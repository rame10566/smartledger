"""
Entity models: Vehicle, Customer, FinancialTerms, Contract.
These represent the core domain objects.
Note: PII fields on Customer are off-chain only — never written to Fabric.
"""
from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from shared.models.common import Address, ContractState, ContractType, Money, SourceSystem


class Vehicle(BaseModel):
    vin:           str   = Field(pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    make:          str
    model:         str
    year:          int   = Field(ge=1980, le=2030)
    trim:          str | None = None
    color:         str | None = None
    mileage:       int | None = Field(default=None, ge=0)
    msrp:          Money | None = None
    invoice_price: Money | None = None
    condition:     str | None = Field(default=None, pattern=r"^(new|used|certified_pre_owned)$")


class Customer(BaseModel):
    """
    Customer entity. PII fields must NOT be written on-chain.
    On-chain representation uses _hash fields only.
    """
    customer_id:    str
    # On-chain safe (hashes)
    name_hash:      str | None = None
    ssn_hash:       str | None = None
    email_hash:     str | None = None
    phone_hash:     str | None = None
    credit_score:   int | None = Field(default=None, ge=300, le=850)
    credit_tier:    str | None = Field(default=None, pattern=r"^(prime|near_prime|subprime)$")
    # Off-chain only (PII) — never set these in on-chain records
    date_of_birth:  date | None = None
    address:        Address | None = None


class FinancialTerms(BaseModel):
    amount_financed:   float = Field(ge=0)
    term_months:       int   = Field(ge=1)
    interest_rate:     float = Field(ge=0, le=100, description="Annual percentage rate (APR)")
    monthly_payment:   float = Field(ge=0)
    down_payment:      float = Field(default=0.0, ge=0)
    residual_value:    float | None = Field(default=None, ge=0, description="Lease only")
    money_factor:      float | None = Field(default=None, ge=0, description="Lease only")
    acquisition_fee:   float | None = Field(default=None, ge=0)
    disposition_fee:   float | None = Field(default=None, ge=0, description="Lease only")


class StateTransition(BaseModel):
    state:            ContractState
    previous_state:   ContractState | None = None
    transitioned_at:  str
    trigger_event_id: UUID
    fabric_tx_id:     str | None = None

    model_config = {"use_enum_values": True}


class Contract(BaseModel):
    """
    Core contract entity.
    When used in on-chain context: customer must only contain hashes (no PII).
    """
    contract_id:      str
    los_system:       SourceSystem
    contract_type:    ContractType
    origination_date: date
    maturity_date:    date | None = None
    state:            ContractState
    customer:         Customer
    vehicle:          Vehicle
    financial_terms:  FinancialTerms
    dealer_id:        str
    data_hash:        str | None = None

    model_config = {"use_enum_values": True}
