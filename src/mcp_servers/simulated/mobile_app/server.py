"""
Mobile App Simulated MCP Server (stub)

Phase D stub — exposes minimal tools so the launcher can start without errors.
Full implementation in Phase F (payment flow — mobile payments).
"""

from mcp.server.fastmcp import FastMCP

from shared.logging import configure_logging, get_logger

configure_logging(service_name="mcp-mobile-app", log_level="INFO")
logger = get_logger(__name__)

mcp = FastMCP(
    "mobile-app-sim",
    instructions="Simulated Mobile App — stub server for Phase 0/D.",
)


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "mobile_app"}


@mcp.tool()
async def initiate_payment(contract_id: str, amount: float) -> dict:
    """Stub: acknowledges payment initiation."""
    return {
        "accepted": True,
        "contract_id": contract_id,
        "amount": amount,
        "note": "Mobile App stub — not yet processed",
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8018)
