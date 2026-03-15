"""
Customer Portal Simulated MCP Server (stub)

Phase D stub — exposes minimal tools so the launcher can start without errors.
Full implementation in Phase F (payment flow — customer self-service).
"""

from mcp.server.fastmcp import FastMCP

from shared.logging import configure_logging, get_logger

configure_logging(service_name="mcp-customer-portal", log_level="INFO")
logger = get_logger(__name__)

mcp = FastMCP(
    "customer-portal-sim",
    instructions="Simulated Customer Portal — stub server for Phase 0/D.",
)


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "customer_portal"}


@mcp.tool()
async def get_account_summary(customer_id: str) -> dict:
    """Stub: returns a minimal account summary."""
    return {
        "customer_id": customer_id,
        "contracts": [],
        "note": "Customer Portal stub",
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8017)
