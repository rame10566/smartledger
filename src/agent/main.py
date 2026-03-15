"""
SmartLedger AI Agent — Entry Point

Custom agent built on Anthropic API + MCP Python SDK.
Reads events from Redis Streams and orchestrates flows via MCP servers.

Architecture:
  Redis Streams → Event Bus Consumer → Agent Event Loop
                                            ↓
                              [acquire per-contract lock]
                                            ↓
                              [run flow via MCP servers]
                                            ↓
                              [release lock / saga checkpoint]

Startup sequence:
  1. Configure logging
  2. Connect to PostgreSQL (asyncpg pool)
  3. Connect to Redis
  4. Recover any in-progress sagas from before the last crash (observability)
  5. Create AgentEventLoop, register flow handlers
  6. Setup consumer group
  7. Run event loop (blocks until SIGINT/SIGTERM)
  8. Graceful shutdown: stop loop, close connections
"""

import asyncio
import signal
from typing import Any

import asyncpg
import redis.asyncio as aioredis

from shared.config import get_settings
from shared.logging import configure_logging, get_logger

from agent.core.event_loop import AgentEventLoop
from agent.core.saga import SagaManager
from agent.flows.origination import OriginationFlow

settings = get_settings()
configure_logging(service_name="smartledger-agent", log_level=settings.log_level)
log = get_logger(__name__)


async def _recover_incomplete_sagas(pool: Any) -> None:
    """
    On startup, log any sagas that were left in_progress after a crash.

    Recovery is observability-only for Phase 0 — operators can investigate and
    re-trigger events manually if needed. Full automatic recovery in Phase 1+.
    """
    try:
        incomplete = await SagaManager.get_incomplete_sagas(pool)
        if incomplete:
            log.warning(
                "incomplete_sagas_detected_on_startup",
                count=len(incomplete),
                sagas=[
                    {
                        "saga_id":    s["saga_id"],
                        "contract_id": s["contract_id"],
                        "step":       s["step"],
                    }
                    for s in incomplete
                ],
            )
        else:
            log.info("no_incomplete_sagas_on_startup")
    except Exception as e:
        # Non-fatal — DB might not be available yet on first boot
        log.warning("saga_recovery_check_failed", error=str(e))


async def main() -> None:
    log.info(
        "smartledger_agent_starting",
        phase=settings.phase,
        write_guard=settings.write_guard,
        model=settings.anthropic_model,
    )

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    log.info("connecting_to_postgres")
    pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    log.info("postgres_connected")

    # ── Redis ──────────────────────────────────────────────────────────────────
    log.info("connecting_to_redis")
    redis_client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,  # stream fields arrive as bytes; we decode manually
    )
    await redis_client.ping()
    log.info("redis_connected")

    # ── Crash recovery (observability) ────────────────────────────────────────
    await _recover_incomplete_sagas(pool)

    # ── Event Loop + Flows ─────────────────────────────────────────────────────
    event_loop = AgentEventLoop(
        pool=pool,
        redis=redis_client,
        consumer_name="agent-0",
    )

    # Register flow handlers (add more here as new event types are implemented)
    origination_flow = OriginationFlow()
    event_loop.register_flow("contract.originated", origination_flow)

    # Setup Redis Streams consumer group
    await event_loop.setup()

    log.info("smartledger_agent_ready")

    # ── Graceful shutdown ──────────────────────────────────────────────────────
    async_loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(sig: signal.Signals) -> None:
        log.info("shutdown_signal_received", signal=sig.name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        async_loop.add_signal_handler(sig, _handle_signal, sig)

    # Run the event loop in a background task so we can await the stop signal
    loop_task = asyncio.create_task(event_loop.run(), name="agent-event-loop")

    try:
        await stop_event.wait()
    finally:
        log.info("smartledger_agent_stopping")
        await event_loop.stop()
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

        await redis_client.aclose()
        await pool.close()
        log.info("smartledger_agent_stopped")


if __name__ == "__main__":
    asyncio.run(main())
