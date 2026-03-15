"""
Saga Pattern — Persistent Checkpoints in PostgreSQL

Every event processing attempt is a "saga" with a unique saga_id.
Checkpoints are written to sagas.checkpoints after each significant step.
If the agent crashes, it reads the latest checkpoint on restart and resumes
from where it left off instead of reprocessing from scratch.

Idempotency: every processed event_id is recorded in sagas.processed_events
so the same event is never processed twice (even across restarts).

Saga steps for the origination flow (in order):
  EVENT_RECEIVED → LOCK_ACQUIRED → CONTEXT_GATHERED →
  VALIDATED → PROOF_TOKEN_ISSUED → LEDGER_WRITTEN →
  STATE_TRANSITIONED → COMPLETED
  (or QUARANTINED / FAILED on failure paths)
"""

import json
from typing import Any
from uuid import UUID, uuid4

from shared.logging import get_logger
from shared.models.saga import SagaStep

logger = get_logger(__name__)


class SagaManager:
    """
    Manages one saga: writes checkpoints, tracks idempotency, handles recovery.

    One SagaManager instance per event being processed.

    Args:
        pool:        asyncpg connection pool
        saga_id:     UUID for this saga (generated fresh or resumed from DB)
        contract_id: the contract being processed
        event_id:    the event being processed
        event_type:  e.g. "contract.originated"
        source_system: e.g. "oracle_los"
    """

    def __init__(
        self,
        pool: Any,
        saga_id: UUID | str,
        contract_id: str,
        event_id: UUID | str,
        event_type: str,
        source_system: str = "unknown",
    ) -> None:
        self.pool = pool
        self.saga_id = str(saga_id)
        self.contract_id = contract_id
        self.event_id = str(event_id)
        self.event_type = event_type
        self.source_system = source_system

    # ── Checkpoints ───────────────────────────────────────────────────────────

    async def checkpoint(
        self,
        step: str | SagaStep,
        payload: dict[str, Any] | None = None,
        status: str = "completed",
    ) -> None:
        """
        Write a saga checkpoint.

        Args:
            step:    The SagaStep name (e.g. SagaStep.EVENT_RECEIVED or "EVENT_RECEIVED")
            payload: Context snapshot at this step (stored as JSONB)
            status:  "in_progress" | "completed" | "failed"
        """
        step_val = str(step)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sagas.checkpoints
                  (saga_id, contract_id, event_id, step, status, payload, created_at, updated_at)
                VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6::jsonb, NOW(), NOW())
                """,
                self.saga_id,
                self.contract_id,
                self.event_id,
                step_val,
                status,
                json.dumps(payload) if payload is not None else None,
            )
        logger.info(
            "saga_checkpoint",
            saga_id=self.saga_id,
            step=step_val,
            status=status,
            contract_id=self.contract_id,
        )

    async def complete(self, payload: dict[str, Any] | None = None) -> None:
        """Mark the saga as COMPLETED and record the event as processed."""
        await self.checkpoint(SagaStep.COMPLETED, payload=payload, status="completed")
        await self.mark_idempotent("written")

    async def quarantine(self, failures: list[dict[str, Any]]) -> None:
        """Mark the saga as QUARANTINED."""
        await self.checkpoint(
            SagaStep.QUARANTINED,
            payload={"failures": failures},
            status="completed",
        )
        await self.mark_idempotent("quarantined")

    async def fail(self, error: str, step: str | SagaStep | None = None) -> None:
        """Mark the saga as FAILED with the error message."""
        await self.checkpoint(
            step or SagaStep.FAILED,
            payload={"error": error},
            status="failed",
        )
        await self.mark_idempotent("failed")

    # ── Idempotency ───────────────────────────────────────────────────────────

    async def is_already_processed(self) -> str | None:
        """
        Check the idempotency dedup table.
        Returns the outcome string ("written", "quarantined", "failed") if already
        processed, or None if this event has never been processed.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT outcome FROM sagas.processed_events WHERE event_id = $1::uuid",
                self.event_id,
            )
        return row["outcome"] if row else None

    async def mark_idempotent(self, outcome: str) -> None:
        """
        Record event_id in sagas.processed_events to prevent reprocessing.
        Uses ON CONFLICT DO NOTHING so duplicate calls are safe.
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sagas.processed_events
                  (event_id, saga_id, event_type, contract_id, source_system, processed_at, outcome)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, NOW(), $6)
                ON CONFLICT (event_id) DO NOTHING
                """,
                self.event_id,
                self.saga_id,
                self.event_type,
                self.contract_id,
                self.source_system,
                outcome,
            )

    # ── Recovery ──────────────────────────────────────────────────────────────

    @classmethod
    async def get_incomplete_sagas(cls, pool: Any) -> list[dict[str, Any]]:
        """
        Return all sagas that were left in_progress (e.g. after a crash).
        The agent reads this on startup to resume interrupted flows.

        Returns the latest checkpoint per saga (DISTINCT ON saga_id).
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (saga_id)
                    saga_id::text,
                    contract_id,
                    event_id::text,
                    step,
                    status,
                    payload::text,
                    created_at::text,
                    updated_at::text
                FROM sagas.checkpoints
                WHERE status = 'in_progress'
                ORDER BY saga_id, updated_at DESC
                """
            )
        result = []
        for row in rows:
            r = dict(row)
            if r.get("payload"):
                try:
                    r["payload"] = json.loads(r["payload"])
                except Exception:
                    pass
            result.append(r)
        return result

    @classmethod
    async def get_saga_checkpoints(
        cls, pool: Any, saga_id: str
    ) -> list[dict[str, Any]]:
        """Return all checkpoints for a specific saga (for debugging/audit)."""
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT saga_id::text, contract_id, event_id::text,
                       step, status, payload::text, created_at::text
                FROM sagas.checkpoints
                WHERE saga_id = $1::uuid
                ORDER BY created_at ASC
                """,
                saga_id,
            )
        result = []
        for row in rows:
            r = dict(row)
            if r.get("payload"):
                try:
                    r["payload"] = json.loads(r["payload"])
                except Exception:
                    pass
            result.append(r)
        return result
