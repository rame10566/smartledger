"""
Saga models: SagaCheckpoint, SagaStatus.
Used by the agent to persist and resume multi-step flows.
"""
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class SagaStep(StrEnum):
    """Valid saga checkpoint steps — in order for the origination flow."""
    EVENT_RECEIVED      = "EVENT_RECEIVED"
    LOCK_ACQUIRED       = "LOCK_ACQUIRED"
    CONTEXT_GATHERED    = "CONTEXT_GATHERED"
    VALIDATED           = "VALIDATED"
    PROOF_TOKEN_ISSUED  = "PROOF_TOKEN_ISSUED"
    LEDGER_WRITTEN      = "LEDGER_WRITTEN"
    STATE_TRANSITIONED  = "STATE_TRANSITIONED"
    COMPLETED           = "COMPLETED"
    QUARANTINED         = "QUARANTINED"
    FAILED              = "FAILED"


class SagaCheckpoint(BaseModel):
    """
    A single checkpoint in a saga. Persisted to sagas.checkpoints in PostgreSQL.
    The agent reads the latest checkpoint on restart to resume from the right step.
    """
    saga_id:     UUID
    contract_id: str
    event_id:    UUID
    step:        SagaStep
    status:      Literal["in_progress", "completed", "failed"]
    payload:     dict[str, Any] | None = None   # context snapshot at this step
    created_at:  datetime
    updated_at:  datetime

    model_config = {"use_enum_values": True}


class SagaStatus(BaseModel):
    """
    Full saga status — returned when agent queries for incomplete sagas on restart.
    """
    saga_id:        UUID
    contract_id:    str
    event_id:       UUID
    event_type:     str
    last_step:      SagaStep
    status:         Literal["in_progress", "completed", "failed", "quarantined"]
    started_at:     datetime
    last_updated:   datetime
    checkpoints:    list[SagaCheckpoint] = []

    model_config = {"use_enum_values": True}
