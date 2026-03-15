"""
Contracts Router

Read-only views of contract lifecycle, state, and audit trail.
Calls the Ledger MCP for immutable record queries.

GET /api/contracts                              — recent contracts list
GET /api/contracts/{contract_id}/lifecycle      — full lifecycle via Ledger MCP
GET /api/contracts/{contract_id}/state          — current state
GET /api/contracts/{contract_id}/audit          — audit trail
GET /api/contracts/{contract_id}/saga           — saga checkpoints (for debugging)
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from dashboard_api.mcp_clients import ledger
from shared.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["contracts"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _str_ts(v: Any) -> str | None:
    return str(v) if v is not None else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/contracts")
async def list_contracts(
    request: Request,
    limit:   int = 50,
) -> list[dict[str, Any]]:
    """
    Return the most recent contracts (by last write to contracts.records).
    Includes current state if available.
    """
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                r.contract_id,
                MIN(r.created_at)::text                AS first_seen,
                MAX(r.created_at)::text                AS last_updated,
                COUNT(*)                               AS record_count,
                COALESCE(s.current_state, 'unknown')   AS current_state,
                s.state_changed_at::text               AS state_changed_at
            FROM contracts.records r
            LEFT JOIN contracts.state s ON s.contract_id = r.contract_id
            GROUP BY r.contract_id, s.current_state, s.state_changed_at
            ORDER BY MAX(r.created_at) DESC
            LIMIT $1
            """,
            limit,
        )

    return [dict(r) for r in rows]


@router.get("/contracts/{contract_id}/lifecycle")
async def get_contract_lifecycle(
    contract_id: str,
) -> dict[str, Any]:
    """
    Return the full contract lifecycle from the Ledger MCP.
    Includes all records, state history, payment totals, and current state.
    """
    try:
        return await ledger.get_contract_lifecycle(contract_id)
    except Exception as e:
        log.error("lifecycle_fetch_failed", contract_id=contract_id, error=str(e))
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/contracts/{contract_id}/state")
async def get_contract_state(
    contract_id: str,
) -> dict[str, Any]:
    """Return the current state of a contract."""
    try:
        return await ledger.get_state(contract_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/contracts/{contract_id}/audit")
async def get_audit_trail(
    contract_id: str,
) -> list[dict[str, Any]]:
    """Return the full audit trail for a contract."""
    try:
        return await ledger.get_audit_trail(contract_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/contracts/{contract_id}/saga")
async def get_saga_checkpoints(
    contract_id: str,
    request:     Request,
) -> list[dict[str, Any]]:
    """
    Return saga checkpoints for a contract (all sagas).
    Useful for debugging and observability in the Dashboard.
    """
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                saga_id::text, contract_id, event_id::text,
                step, status, payload::text,
                created_at::text, updated_at::text
            FROM sagas.checkpoints
            WHERE contract_id = $1
            ORDER BY created_at ASC
            """,
            contract_id,
        )

    result = []
    for row in rows:
        d = dict(row)
        if d.get("payload"):
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
        result.append(d)

    return result
