"""
Conflicts Router

Endpoints for LLAS Admin to view and resolve competing customer data updates.
When two source systems submit conflicting updates to the same field(s) on the
same contract, both are quarantined with status='conflict'. The LLAS Admin
reviews both values alongside the current LLAS profile and selects the
authoritative one.

Endpoints:
  GET  /api/conflicts                               — list active conflict pairs
  GET  /api/conflicts/{conflict_pair_id}            — side-by-side comparison
  POST /api/conflicts/{conflict_pair_id}/resolve    — select winning value

All endpoints require OperationalRole.ADMIN (LLAS Admin role).
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from dashboard_api.middleware.access_audit import log_access
from dashboard_api.middleware.access_control import AccessContext, get_access_context
from dashboard_api.mcp_clients import llas, validation
from shared.logging import get_logger
from shared.models.entities import OperationalRole

log = get_logger(__name__)
router = APIRouter(tags=["conflicts"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _require_admin(ctx: AccessContext) -> None:
    """Raise 403 if the caller is not an operational admin."""
    if ctx.role != OperationalRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="LLAS Admin role required to manage conflicts",
        )


def _parse_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _row_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    for k in ("context_snapshot", "original_payload"):
        if k in d:
            d[k] = _parse_jsonb(d[k])
    for k in ("created_at", "reviewed_at", "sla_deadline"):
        if k in d and d[k] is not None:
            d[k] = str(d[k])
    return d


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ── Request body ──────────────────────────────────────────────────────────────

class ResolveConflictRequest(BaseModel):
    winning_event_id: str   # event_id of the quarantine row whose value wins
    reason: str             # admin's stated reason for selecting this value


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/conflicts")
async def list_conflicts(
    request:     Request,
    contract_id: str | None = None,
    ctx:         AccessContext = Depends(get_access_context),
) -> list[dict[str, Any]]:
    """
    List all active conflict pairs (status='conflict').
    Each item represents a pair: two competing updates to the same field(s).
    Requires LLAS Admin role.
    """
    _require_admin(ctx)

    conflict_list = await validation.get_conflicts(contract_id=contract_id)

    await log_access(pool=request.app.state.pool, ctx=ctx,
                     resource="/api/conflicts", ip_address=_client_ip(request))

    return conflict_list


@router.get("/conflicts/{conflict_pair_id}")
async def get_conflict_detail(
    conflict_pair_id: str,
    request:          Request,
    ctx:              AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    """
    Return full detail for a conflict pair:
      - Both competing quarantine records (side A and side B)
      - The current LLAS customer profile for the contract

    The dashboard uses this to show a side-by-side comparison so the LLAS
    Admin can choose the authoritative value.
    """
    _require_admin(ctx)
    pool = request.app.state.pool

    # Fetch both quarantine rows for this conflict pair
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                event_id::text, contract_id, event_type, source_system,
                rejection_code, rejection_detail,
                context_snapshot::text, original_payload::text,
                status, conflict_pair_id,
                created_at::text
            FROM validation.quarantine
            WHERE conflict_pair_id = $1
            ORDER BY created_at ASC
            """,
            conflict_pair_id,
        )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Conflict pair not found: {conflict_pair_id}",
        )

    pair = [_row_to_dict(r) for r in rows]
    contract_id = pair[0]["contract_id"]

    # Fetch current LLAS customer profile for side-by-side comparison
    llas_profile: dict[str, Any] = {}
    try:
        llas_profile = await llas.get_customer_profile(contract_id) or {}
    except Exception as e:
        log.warning("conflict_detail_llas_unavailable", contract_id=contract_id, error=str(e))

    await log_access(pool=pool, ctx=ctx,
                     resource=f"/api/conflicts/{conflict_pair_id}",
                     contract_id=contract_id, ip_address=_client_ip(request))

    return {
        "conflict_pair_id": conflict_pair_id,
        "contract_id":      contract_id,
        "side_a":           pair[0] if len(pair) > 0 else None,
        "side_b":           pair[1] if len(pair) > 1 else None,
        "current_llas":     llas_profile,
    }


@router.post("/conflicts/{conflict_pair_id}/resolve")
async def resolve_conflict(
    conflict_pair_id: str,
    body:             ResolveConflictRequest,
    request:          Request,
    ctx:              AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    """
    Resolve a conflict by selecting the winning event.

    The Validation Engine:
      1. Issues a proof token for the winning value
      2. Updates both quarantine rows to status='resolved'
      3. Publishes an integration.conflict_resolved event

    The agent then picks up the event, writes the ledger record, and
    updates the LLAS customer profile.
    """
    _require_admin(ctx)

    result = await validation.resolve_conflict(
        conflict_pair_id=conflict_pair_id,
        winning_event_id=body.winning_event_id,
        admin_id=ctx.actor_id,
        reason=body.reason,
    )

    await log_access(
        pool=request.app.state.pool,
        ctx=ctx,
        resource=f"/api/conflicts/{conflict_pair_id}/resolve",
        ip_address=_client_ip(request),
    )

    log.info(
        "conflict_resolved_via_dashboard",
        conflict_pair_id=conflict_pair_id,
        winning_event_id=body.winning_event_id,
        admin_id=ctx.actor_id,
    )

    return result
