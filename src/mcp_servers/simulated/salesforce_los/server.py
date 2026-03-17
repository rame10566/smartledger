"""
Salesforce LOS Simulated MCP Server (stub)

Phase D stub — exposes minimal tools so the launcher can start without errors.
Full implementation in Phase F (remaining flows).
"""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.logging import configure_logging, get_logger

configure_logging(service_name="mcp-salesforce-los", log_level="INFO")
logger = get_logger(__name__)

mcp = FastMCP(
    "salesforce-los-sim",
    instructions="Simulated Salesforce LOS — stub server for Phase 0/D.",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "salesforce_los"}


@mcp.tool()
async def get_contract(contract_id: str) -> dict:
    """Stub: Salesforce LOS not yet implemented. Returns not-found."""
    return {"found": False, "contract_id": contract_id, "note": "Salesforce LOS stub"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8011)
