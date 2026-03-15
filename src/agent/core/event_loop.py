"""
Agent Event Loop

Reads events from Redis Streams, acquires per-contract locks,
dispatches to appropriate flow handler, manages saga checkpoints.

Flow:
  1. Read event from Redis Stream (XREADGROUP)
  2. Check idempotency (event_id dedup table)
  3. Acquire per-contract distributed lock
  4. Checkpoint: LOCK_ACQUIRED
  5. Dispatch to flow handler (origination, payment, etc.)
  6. Release lock
  7. ACK event on stream
  8. On failure: release lock, update saga, ACK to prevent infinite retry

Lock failure (LockAcquisitionError):
  - Do NOT ACK — message stays pending, will be re-delivered after visibility timeout.
  - Another consumer (or this one after TTL expiry) will pick it up and retry.
"""

import asyncio
import json
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from shared.logging import get_logger

from .locks import ContractLock, LockAcquisitionError
from .saga import SagaManager

logger = get_logger(__name__)

# Redis Stream / Consumer Group config
STREAM_KEY = "smartledger:events"
CONSUMER_GROUP = "smartledger-agent"

# How long to block waiting for a new message (ms). 0 = wait forever.
BLOCK_MS = 5_000

# Max messages to fetch per XREADGROUP call
FETCH_COUNT = 1


class AgentEventLoop:
    """
    Async event loop that consumes events from Redis Streams.

    Each event is:
      - idempotency-checked (already-processed events are ACK'd and skipped)
      - protected by a per-contract distributed lock
      - dispatched to a registered flow handler
      - saga-checkpointed throughout

    Args:
        pool:          asyncpg connection pool (for saga checkpoints)
        redis:         redis.asyncio client
        consumer_name: unique name for this consumer instance (e.g. "agent-0")
    """

    def __init__(
        self,
        pool: Any,
        redis: Any,
        consumer_name: str = "agent-0",
    ) -> None:
        self.pool = pool
        self.redis = redis
        self.consumer_name = consumer_name
        self._running = False
        # event_type → async callable(saga, event) -> None
        self._flows: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}

    def register_flow(
        self,
        event_type: str,
        handler: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        """Register an async flow handler for an event type."""
        self._flows[event_type] = handler
        logger.info("flow_registered", event_type=event_type)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        """
        Create the consumer group (MKSTREAM creates the stream if it doesn't exist).
        Safe to call multiple times — BUSYGROUP error is swallowed.
        """
        try:
            await self.redis.xgroup_create(
                STREAM_KEY,
                CONSUMER_GROUP,
                id="0",       # start from the beginning of the stream
                mkstream=True,
            )
            logger.info(
                "consumer_group_created",
                group=CONSUMER_GROUP,
                stream=STREAM_KEY,
            )
        except Exception as e:
            if "BUSYGROUP" in str(e):
                logger.info(
                    "consumer_group_already_exists",
                    group=CONSUMER_GROUP,
                    stream=STREAM_KEY,
                )
            else:
                raise

    async def run(self) -> None:
        """
        Main loop. Runs until stop() is called or asyncio.CancelledError is raised.
        """
        self._running = True
        logger.info(
            "event_loop_started",
            consumer=self.consumer_name,
            stream=STREAM_KEY,
            group=CONSUMER_GROUP,
        )

        while self._running:
            try:
                await self._process_next()
            except asyncio.CancelledError:
                logger.info("event_loop_cancelled")
                break
            except Exception as e:
                # Log unexpected errors and back off briefly before retrying
                logger.error("event_loop_unexpected_error", error=str(e))
                await asyncio.sleep(1)

        logger.info("event_loop_stopped", consumer=self.consumer_name)

    async def stop(self) -> None:
        """Signal the loop to stop after the current iteration."""
        self._running = False

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _process_next(self) -> None:
        """
        Fetch one message from the stream and handle it.
        Blocks for BLOCK_MS ms if no messages are available.
        """
        messages = await self.redis.xreadgroup(
            CONSUMER_GROUP,
            self.consumer_name,
            {STREAM_KEY: ">"},   # ">" = only new, undelivered messages
            count=FETCH_COUNT,
            block=BLOCK_MS,
        )

        if not messages:
            return  # timeout — no messages available

        # messages format: [(stream_key_bytes, [(entry_id_bytes, fields_dict), ...])]
        stream_key, entries = messages[0]
        stream_id, fields = entries[0]

        await self._handle_entry(
            stream_id=stream_id if isinstance(stream_id, str) else stream_id.decode(),
            fields=fields,
        )

    async def _handle_entry(self, stream_id: str, fields: dict) -> None:
        """
        Process a single stream entry end-to-end:
          idempotency → lock → checkpoint → dispatch → ack
        """
        def _d(v: Any) -> str:
            """Decode bytes → str if needed."""
            return v.decode() if isinstance(v, bytes) else str(v)

        event_id     = _d(fields.get(b"event_id" if b"event_id" in fields else "event_id", ""))
        event_type   = _d(fields.get(b"event_type" if b"event_type" in fields else "event_type", ""))
        contract_id  = _d(fields.get(b"contract_id" if b"contract_id" in fields else "contract_id", ""))
        source_system = _d(fields.get(b"source_system" if b"source_system" in fields else "source_system", "unknown"))
        timestamp    = _d(fields.get(b"timestamp" if b"timestamp" in fields else "timestamp", ""))
        correlation_id = _d(fields.get(b"correlation_id" if b"correlation_id" in fields else "correlation_id", ""))
        schema_version = _d(fields.get(b"schema_version" if b"schema_version" in fields else "schema_version", "1.0"))
        payload_raw  = _d(fields.get(b"payload" if b"payload" in fields else "payload", "{}"))

        # Guard: malformed entry
        if not event_id or not contract_id:
            logger.error(
                "malformed_stream_entry_dropped",
                stream_id=stream_id,
                event_id=event_id,
                contract_id=contract_id,
            )
            await self.redis.xack(STREAM_KEY, CONSUMER_GROUP, stream_id)
            return

        # Parse payload JSON
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = {}

        # Build event dict passed to flow handlers
        event: dict[str, Any] = {
            "event_id":       event_id,
            "event_type":     event_type,
            "contract_id":    contract_id,
            "source_system":  source_system,
            "timestamp":      timestamp,
            "correlation_id": correlation_id,
            "schema_version": schema_version,
            "payload":        payload,
            "stream_id":      stream_id,
        }

        # New saga_id for this processing attempt
        saga_id = str(uuid.uuid4())
        saga = SagaManager(
            pool=self.pool,
            saga_id=saga_id,
            contract_id=contract_id,
            event_id=event_id,
            event_type=event_type,
            source_system=source_system,
        )

        # ── Idempotency check ─────────────────────────────────────────────────
        prior_outcome = await saga.is_already_processed()
        if prior_outcome:
            logger.info(
                "event_already_processed_skipping",
                event_id=event_id,
                contract_id=contract_id,
                prior_outcome=prior_outcome,
            )
            await self.redis.xack(STREAM_KEY, CONSUMER_GROUP, stream_id)
            return

        # ── Per-contract distributed lock ─────────────────────────────────────
        lock = ContractLock(self.redis, contract_id, saga_id)
        try:
            await lock.acquire()
        except LockAcquisitionError as e:
            # Do NOT ACK — the pending message will be re-delivered once the
            # lock-holding saga finishes and the TTL expires.
            logger.warning(
                "lock_acquisition_failed_will_retry",
                event_id=event_id,
                contract_id=contract_id,
                error=str(e),
            )
            return

        # ── Process ───────────────────────────────────────────────────────────
        try:
            await saga.checkpoint("LOCK_ACQUIRED", payload={"stream_id": stream_id})

            handler = self._flows.get(event_type)
            if handler is None:
                logger.warning(
                    "no_flow_handler_for_event_type",
                    event_type=event_type,
                    event_id=event_id,
                )
                await saga.fail(f"No handler registered for event_type='{event_type}'")
            else:
                await handler(saga=saga, event=event)

            # ACK on success OR handled failure (quarantine/fail checkpointed)
            await self.redis.xack(STREAM_KEY, CONSUMER_GROUP, stream_id)

        except Exception as e:
            logger.error(
                "flow_execution_error",
                event_id=event_id,
                contract_id=contract_id,
                event_type=event_type,
                saga_id=saga_id,
                error=str(e),
            )
            # Best-effort failure checkpoint
            try:
                await saga.fail(str(e))
            except Exception:
                pass
            # ACK to prevent the broker from re-delivering indefinitely.
            # Broken events are checkpointed as FAILED for manual investigation.
            await self.redis.xack(STREAM_KEY, CONSUMER_GROUP, stream_id)

        finally:
            await lock.release()
