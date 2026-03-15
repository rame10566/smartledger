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
"""
import asyncio

from shared.config import get_settings
from shared.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(service_name=settings.service_name, log_level=settings.log_level)
log = get_logger(__name__)


async def main() -> None:
    log.info(
        "smartledger_agent_starting",
        phase=settings.phase,
        write_guard=settings.write_guard,
    )

    # TODO: Initialize MCP client connections
    # TODO: Initialize Redis Streams consumer
    # TODO: Start agent event loop

    log.info("smartledger_agent_ready")

    # Placeholder — keep alive
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
