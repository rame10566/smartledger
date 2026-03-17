"""
Dealer Simulated MCP Server (stub)

Phase D stub — exposes minimal tools so the launcher can start without errors.
Full implementation in Phase F (remaining flows).
"""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.logging import configure_logging, get_logger

configure_logging(service_name="mcp-dealer", log_level="INFO")
logger = get_logger(__name__)

mcp = FastMCP(
    "dealer-sim",
    instructions="Simulated Dealer system — stub server for Phase 0/D.",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)

# Minimal dealer data for validation support
_DEALERS: dict[str, dict] = {
    "DLR-001": {"dealer_id": "DLR-001", "name": "AutoNation Toyota", "state": "CA", "active": True},
    "DLR-002": {"dealer_id": "DLR-002", "name": "Hendrick Honda",    "state": "NC", "active": True},
    "DLR-003": {"dealer_id": "DLR-003", "name": "Penske Ford",       "state": "TX", "active": True},
}


@mcp.tool()
async def ping() -> dict:
    """Health-check tool."""
    return {"status": "ok", "service": "dealer"}


@mcp.tool()
async def get_dealer(dealer_id: str) -> dict:
    """Return dealer record. Returns found=False if unknown."""
    dealer = _DEALERS.get(dealer_id)
    if not dealer:
        return {"found": False, "dealer_id": dealer_id}
    return {"found": True, **dealer}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8016)
