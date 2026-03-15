"""
Immutable Ledger MCP Server

Wraps Hyperledger Fabric + Chaincode. Write guard active in Phase 0.

Ledger Tools (exposed to Agent):
  - write_record(record, proof_token) → writes to Fabric (Phase 1+) or PostgreSQL (Phase 0)
  - query_records(contract_id, filters?) → query ledger records
  - get_contract_lifecycle(contract_id) → full state history
  - get_audit_trail(contract_id) → all actions on this contract
  - get_state(contract_id) → current contract state

Smart Contract (Chaincode) Tools:
  - execute_state_transition(contract_id, transition, data) → ORIGINATED→ACTIVE etc.
  - calculate_late_fee(contract_id, days_past_due) → fee calculation via chaincode
  - check_title_release(contract_id) → eligibility check
  - get_governance_rules() → rules from chaincode

Write Guard:
  - WRITE_GUARD=true (Phase 0): write_record logs what WOULD be written, returns success
  - WRITE_GUARD=false (Phase 1+): writes to Hyperledger Fabric
"""
from mcp.server.fastmcp import FastMCP

from shared.config import get_settings

settings = get_settings()

mcp = FastMCP(
    name="smartledger-ledger",
    instructions="Immutable Ledger for SmartLedger. Wraps Hyperledger Fabric.",
)


@mcp.tool()
async def write_record(record: dict, proof_token: str) -> dict:
    """
    Write a validated record to the immutable ledger.
    Requires a valid, unused proof token from the Validation Engine.
    Phase 0: write guard ON — logs intent, does not write to Fabric.
    Phase 1+: writes to Hyperledger Fabric.
    """
    # TODO: Validate proof token (single-use check)
    # TODO: If write_guard=True: log and return mock success
    # TODO: If write_guard=False: submit to Fabric, write to PostgreSQL fallback
    raise NotImplementedError


@mcp.tool()
async def query_records(contract_id: str, record_type: str | None = None) -> list[dict]:
    """Query ledger records for a contract."""
    # TODO: Query ledger.records or Fabric
    raise NotImplementedError


@mcp.tool()
async def get_contract_lifecycle(contract_id: str) -> dict:
    """Return the full state transition history for a contract."""
    # TODO: Query Fabric or ledger.records ordered by timestamp
    raise NotImplementedError


@mcp.tool()
async def get_audit_trail(contract_id: str) -> list[dict]:
    """Return full audit trail for a contract."""
    # TODO: Query audit.log
    raise NotImplementedError


@mcp.tool()
async def get_state(contract_id: str) -> dict:
    """Return current contract state."""
    # TODO: Query latest state from Fabric or ledger
    raise NotImplementedError


@mcp.tool()
async def execute_state_transition(contract_id: str, transition: str, data: dict) -> dict:
    """Execute a state transition on the contract (calls chaincode)."""
    # TODO: Call Fabric chaincode execute_state_transition
    raise NotImplementedError


@mcp.tool()
async def calculate_late_fee(contract_id: str, days_past_due: int) -> dict:
    """Calculate late fee via chaincode."""
    # TODO: Call Fabric chaincode calculate_late_fee
    raise NotImplementedError


@mcp.tool()
async def check_title_release(contract_id: str) -> dict:
    """Check if title release conditions are met via chaincode."""
    # TODO: Call Fabric chaincode check_title_release
    raise NotImplementedError


@mcp.tool()
async def get_governance_rules() -> dict:
    """Return governance rules from chaincode."""
    # TODO: Call Fabric chaincode get_governance_rules
    raise NotImplementedError


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8002)
