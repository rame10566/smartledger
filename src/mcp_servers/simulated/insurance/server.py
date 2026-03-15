"""
Insurance Simulated MCP Server (stub)

Phase D stub — exposes minimal tools so the launcher can start without errors.
Full implementation in Phase F (remaining flows).
"""

from mcp.server.fastmcp import FastMCP

from shared.logging import configure_logging, get_logger

configure_logging(service_name="mcp-insurance", log_level="INFO")
logger = get_logger(__name__)

mcp = FastMCP(
    "insurance-sim",
    instructions="Simulated Insurance system — stub server for Phase 0/D.",
)


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "insurance"}


@mcp.tool()
async def get_policy(contract_id: str) -> dict:
    """Stub: returns a minimal insurance policy."""
    return {
        "found": True,
        "contract_id": contract_id,
        "policy_status": "active",
        "coverage_type": "comprehensive",
        "note": "Insurance stub",
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8015)
