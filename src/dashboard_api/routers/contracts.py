"""
Contracts Router

Read-only views of contract lifecycle, state, and audit trail.
Calls the Ledger MCP for immutable record queries.

All endpoints enforce party-based access control via the Smart Data Gateway
(REQUIREMENTS Section 6.5).

GET /api/contracts                              — recent contracts list
GET /api/contracts/{contract_id}/lifecycle      — full lifecycle via Ledger MCP
GET /api/contracts/{contract_id}/state          — current state
GET /api/contracts/{contract_id}/audit          — audit trail
GET /api/contracts/{contract_id}/saga           — saga checkpoints (for debugging)
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from dashboard_api.mcp_clients import ledger
from dashboard_api.middleware.access_audit import log_access
from dashboard_api.middleware.access_control import (
    AccessContext,
    PRIVILEGED_ROLES,
    check_party_access,
    get_access_context,
    get_party_contract_ids,
)
from dashboard_api.middleware.field_filter import filter_fields
from shared.logging import get_logger
from shared.models.entities import OperationalRole

log = get_logger(__name__)
router = APIRouter(tags=["contracts"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _str_ts(v: Any) -> str | None:
    return str(v) if v is not None else None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/contracts")
async def list_contracts(
    request: Request,
    limit:   int = 50,
    ctx:     AccessContext = Depends(get_access_context),
) -> list[dict[str, Any]]:
    """
    Return the most recent contracts (by last write to contracts.records).
    Party users only see contracts where they are a listed party.
    """
    pool = request.app.state.pool

    # Determine which contracts this caller can see
    allowed_ids = await get_party_contract_ids(ctx, pool)

    if allowed_ids is not None and len(allowed_ids) == 0:
        # Party user with no contracts
        await log_access(pool, ctx, "/api/contracts", access_granted=True,
                         fields_returned=[], fields_filtered=[], ip_address=_client_ip(request))
        return []

    async with pool.acquire() as conn:
        if allowed_ids is None:
            # Privileged role: see all contracts
            rows = await conn.fetch(
                """
                SELECT
                    r.contract_id,
                    MIN(r.created_at)::text                AS first_seen,
                    MAX(r.created_at)::text                AS last_updated,
                    COUNT(*)                               AS record_count,
                    COALESCE(s.current_state, 'active')    AS current_state,
                    s.state_changed_at::text               AS state_changed_at
                FROM contracts.records r
                LEFT JOIN contracts.state s ON s.contract_id = r.contract_id
                GROUP BY r.contract_id, s.current_state, s.state_changed_at
                ORDER BY MAX(r.created_at) DESC
                LIMIT $1
                """,
                limit,
            )
        else:
            # Party user: filter to their contracts
            rows = await conn.fetch(
                """
                SELECT
                    r.contract_id,
                    MIN(r.created_at)::text                AS first_seen,
                    MAX(r.created_at)::text                AS last_updated,
                    COUNT(*)                               AS record_count,
                    COALESCE(s.current_state, 'active')    AS current_state,
                    s.state_changed_at::text               AS state_changed_at
                FROM contracts.records r
                LEFT JOIN contracts.state s ON s.contract_id = r.contract_id
                WHERE r.contract_id = ANY($2)
                GROUP BY r.contract_id, s.current_state, s.state_changed_at
                ORDER BY MAX(r.created_at) DESC
                LIMIT $1
                """,
                limit,
                allowed_ids,
            )

    result = [dict(r) for r in rows]

    # Apply field-level filtering
    filtered, returned, stripped = await filter_fields(result, ctx, pool)

    await log_access(pool, ctx, "/api/contracts", fields_returned=returned,
                     fields_filtered=stripped, ip_address=_client_ip(request))

    return filtered


@router.get("/contracts/{contract_id}/lifecycle")
async def get_contract_lifecycle(
    contract_id: str,
    request:     Request,
    ctx:         AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    """
    Return the full contract lifecycle from the Ledger MCP.
    Party users must be a party to the contract.
    """
    pool = request.app.state.pool

    if not await check_party_access(contract_id, ctx, pool):
        await log_access(pool, ctx, f"/api/contracts/{contract_id}/lifecycle",
                         contract_id=contract_id, access_granted=False,
                         denial_reason="not_a_party", ip_address=_client_ip(request))
        raise HTTPException(status_code=403, detail="Access denied: you are not a party to this contract")

    try:
        data = await ledger.get_contract_lifecycle(contract_id)
    except Exception as e:
        log.error("lifecycle_fetch_failed", contract_id=contract_id, error=str(e))
        raise HTTPException(status_code=404, detail=str(e))

    filtered, returned, stripped = await filter_fields(data, ctx, pool)

    await log_access(pool, ctx, f"/api/contracts/{contract_id}/lifecycle",
                     contract_id=contract_id, fields_returned=returned,
                     fields_filtered=stripped, ip_address=_client_ip(request))

    return filtered


@router.get("/contracts/{contract_id}/state")
async def get_contract_state(
    contract_id: str,
    request:     Request,
    ctx:         AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    """Return the current state of a contract. Party access enforced."""
    pool = request.app.state.pool

    if not await check_party_access(contract_id, ctx, pool):
        await log_access(pool, ctx, f"/api/contracts/{contract_id}/state",
                         contract_id=contract_id, access_granted=False,
                         denial_reason="not_a_party", ip_address=_client_ip(request))
        raise HTTPException(status_code=403, detail="Access denied: you are not a party to this contract")

    try:
        data = await ledger.get_state(contract_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

    await log_access(pool, ctx, f"/api/contracts/{contract_id}/state",
                     contract_id=contract_id, ip_address=_client_ip(request))

    return data


@router.get("/contracts/{contract_id}/audit")
async def get_audit_trail(
    contract_id: str,
    request:     Request,
    ctx:         AccessContext = Depends(get_access_context),
) -> list[dict[str, Any]]:
    """
    Return the full audit trail for a contract.
    Restricted to admin, auditor, and compliance roles (Section 6.5.3).
    """
    pool = request.app.state.pool

    # Audit trail is restricted to privileged roles
    if ctx.role not in PRIVILEGED_ROLES and ctx.actor_type != "agent":
        await log_access(pool, ctx, f"/api/contracts/{contract_id}/audit",
                         contract_id=contract_id, access_granted=False,
                         denial_reason="insufficient_role_for_audit_trail",
                         ip_address=_client_ip(request))
        raise HTTPException(status_code=403, detail="Access denied: audit trail requires admin, auditor, or compliance role")

    try:
        result = await ledger.get_audit_trail(contract_id)
        data = result if result is not None else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await log_access(pool, ctx, f"/api/contracts/{contract_id}/audit",
                     contract_id=contract_id, fields_returned=["audit_trail"],
                     ip_address=_client_ip(request))

    return data


@router.get("/contracts/{contract_id}/saga")
async def get_saga_checkpoints(
    contract_id: str,
    request:     Request,
    ctx:         AccessContext = Depends(get_access_context),
) -> list[dict[str, Any]]:
    """
    Return saga checkpoints for a contract (all sagas).
    Restricted to admin, auditor, and compliance roles.
    """
    pool = request.app.state.pool

    if ctx.role not in PRIVILEGED_ROLES and ctx.actor_type != "agent":
        await log_access(pool, ctx, f"/api/contracts/{contract_id}/saga",
                         contract_id=contract_id, access_granted=False,
                         denial_reason="insufficient_role_for_saga",
                         ip_address=_client_ip(request))
        raise HTTPException(status_code=403, detail="Access denied: saga view requires admin, auditor, or compliance role")

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

    await log_access(pool, ctx, f"/api/contracts/{contract_id}/saga",
                     contract_id=contract_id, ip_address=_client_ip(request))

    return result
