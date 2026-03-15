"""
Payment Simulated MCP Server (stub)

Phase D stub — exposes minimal tools so the launcher can start without errors.
Full implementation in Phase F (payment flow).
"""

from mcp.server.fastmcp import FastMCP

from shared.logging import configure_logging, get_logger

configure_logging(service_name="mcp-payment", log_level="INFO")
logger = get_logger(__name__)

mcp = FastMCP(
    "payment-sim",
    instructions="Simulated Payment processor — stub server for Phase 0/D.",
)


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "payment"}


@mcp.tool()
async def get_payment(payment_id: str) -> dict:
    """Stub: returns not-found for any payment."""
    return {"found": False, "payment_id": payment_id, "note": "Payment stub"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8014)
