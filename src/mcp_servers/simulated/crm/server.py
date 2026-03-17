"""
CRM Simulated MCP Server (stub)

Phase D stub — exposes minimal tools so the launcher can start without errors.
Full implementation in Phase F (remaining flows).
"""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.logging import configure_logging, get_logger

configure_logging(service_name="mcp-crm", log_level="INFO")
logger = get_logger(__name__)

mcp = FastMCP(
    "crm-sim",
    instructions="Simulated CRM — stub server for Phase 0/D.",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "crm"}


@mcp.tool()
async def get_customer(customer_id: str) -> dict:
    """Stub: returns a minimal customer record."""
    return {
        "found": True,
        "customer_id": customer_id,
        "name": "Stub Customer",
        "email": "stub@example.com",
        "note": "CRM stub",
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8013)
