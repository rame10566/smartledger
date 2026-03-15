"""
IVR (Interactive Voice Response) Simulated MCP Server (stub)

Phase D stub — exposes minimal tools so the launcher can start without errors.
Full implementation in Phase F (payment flow — IVR phone payments).
"""

from mcp.server.fastmcp import FastMCP

from shared.logging import configure_logging, get_logger

configure_logging(service_name="mcp-ivr", log_level="INFO")
logger = get_logger(__name__)

mcp = FastMCP(
    "ivr-sim",
    instructions="Simulated IVR system — stub server for Phase 0/D.",
)


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "ivr"}


@mcp.tool()
async def capture_payment(contract_id: str, amount: float, phone_last4: str) -> dict:
    """Stub: acknowledges IVR payment capture."""
    return {
        "accepted": True,
        "contract_id": contract_id,
        "amount": amount,
        "phone_last4": phone_last4,
        "note": "IVR stub — not yet processed",
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8019)
