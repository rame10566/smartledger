"""
Quarantine Router

Endpoints for the human-review quarantine queue.
Access control enforced via Smart Data Gateway (Section 6.5).

GET  /api/quarantine               — list pending (and recently resolved) events
GET  /api/quarantine/{event_id}    — single quarantine record
POST /api/quarantine/{event_id}/approve  — approve override (calls Validation MCP)
POST /api/quarantine/{event_id}/reject   — reject the event
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from dashboard_api.mcp_clients import validation
from dashboard_api.middleware.access_audit import log_access
from dashboard_api.middleware.access_control import (
    AccessContext,
    WRITE_ROLES,
    get_access_context,
)
from shared.logging import get_logger
from shared.models.entities import OperationalRole

log = get_logger(__name__)
router = APIRouter(tags=["quarantine"])


# ── Request / Response models ──────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    reason:      str
    reviewer:    str
    corrections: dict[str, Any] | None = None  # reviewer-supplied field corrections (e.g. {"financial_terms": {"interest_rate": 6.49}})


class RejectRequest(BaseModel):
    reason:    str
    reviewer:  str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_jsonb(value: Any) -> Any:
    """asyncpg returns JSONB as a str; parse it if needed."""
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


def _require_write_role(ctx: AccessContext) -> None:
    """Ensure the caller has a role that can approve/reject quarantine items."""
    if ctx.role not in WRITE_ROLES and ctx.actor_type != "agent":
        raise HTTPException(
            status_code=403,
            detail="Access denied: approve/reject requires admin, operator, or compliance role",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/quarantine")
async def list_quarantined(
    request:        Request,
    status:         str | None = None,   # pending | approved | rejected
    contract_id:    str | None = None,
    limit:          int = 50,
    ctx:            AccessContext = Depends(get_access_context),
) -> list[dict[str, Any]]:
    """
    List quarantined events, newest first.
    Operational roles (admin, operator, auditor, compliance) see all.
    Party users see only quarantine records for their contracts.
    """
    pool = request.app.state.pool

    conditions: list[str] = []
    args: list[Any] = []

    if status:
        args.append(status)
        conditions.append(f"status = ${len(args)}")
    if contract_id:
        args.append(contract_id)
        conditions.append(f"contract_id = ${len(args)}")

    # Party users: filter to their contracts only
    if ctx.role is None and ctx.party_entity_id:
        async with pool.acquire() as conn:
            party_rows = await conn.fetch(
                "SELECT contract_id FROM contracts.parties WHERE entity_id = $1",
                ctx.party_entity_id,
            )
        party_contract_ids = [r["contract_id"] for r in party_rows]
        if not party_contract_ids:
            return []
        args.append(party_contract_ids)
        conditions.append(f"contract_id = ANY(${len(args)})")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    args.append(limit)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                event_id::text, contract_id, event_type, source_system,
                rejection_code, rejection_detail,
                context_snapshot::text, original_payload::text,
                status, escalation_level, reviewed_by,
                reviewed_at::text, override_reason,
                created_at::text, sla_deadline::text
            FROM validation.quarantine
            {where}
            ORDER BY created_at DESC
            LIMIT ${len(args)}
            """,
            *args,
        )

    await log_access(pool, ctx, "/api/quarantine", ip_address=_client_ip(request))

    return [_row_to_dict(r) for r in rows]


@router.get("/quarantine/{event_id}")
async def get_quarantine_record(
    event_id: str,
    request:  Request,
    ctx:      AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    """Return a single quarantine record."""
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                event_id::text, contract_id, event_type, source_system,
                rejection_code, rejection_detail,
                context_snapshot::text, original_payload::text,
                status, escalation_level, reviewed_by,
                reviewed_at::text, override_reason,
                created_at::text, sla_deadline::text
            FROM validation.quarantine
            WHERE event_id = $1::uuid
            """,
            event_id,
        )

    if not row:
        raise HTTPException(status_code=404, detail=f"Quarantine record not found: {event_id}")

    await log_access(pool, ctx, f"/api/quarantine/{event_id}",
                     contract_id=row["contract_id"], ip_address=_client_ip(request))

    return _row_to_dict(row)


@router.post("/quarantine/{event_id}/approve")
async def approve_quarantine(
    event_id: str,
    body:     ApproveRequest,
    request:  Request,
    ctx:      AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    """
    Human reviewer approves an override.
    Requires admin, operator, or compliance role.
    """
    _require_write_role(ctx)
    pool = request.app.state.pool

    log.info(
        "quarantine_approve_requested",
        event_id=event_id,
        reviewer=body.reviewer,
        actor_id=ctx.actor_id,
    )
    try:
        result = await validation.approve_override(
            event_id=event_id,
            reason=body.reason,
            reviewer=body.reviewer,
            corrections=body.corrections,
        )
    except Exception as e:
        log.error("quarantine_approve_failed", event_id=event_id, error=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    log.info(
        "quarantine_approved",
        event_id=event_id,
        contract_id=result.get("contract_id"),
        reviewer=body.reviewer,
    )

    await log_access(pool, ctx, f"/api/quarantine/{event_id}/approve",
                     contract_id=result.get("contract_id"), ip_address=_client_ip(request))

    return result


@router.post("/quarantine/{event_id}/reject")
async def reject_quarantine(
    event_id: str,
    body:     RejectRequest,
    request:  Request,
    ctx:      AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    """
    Human reviewer rejects a quarantined event.
    Requires admin, operator, or compliance role.
    """
    _require_write_role(ctx)
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE validation.quarantine
            SET status = 'rejected',
                reviewed_by = $2,
                reviewed_at = NOW(),
                override_reason = $3
            WHERE event_id = $1::uuid AND status = 'pending'
            RETURNING event_id::text, contract_id, status
            """,
            event_id,
            body.reviewer,
            body.reason,
        )

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No pending quarantine record found for event_id '{event_id}'",
        )

    log.info(
        "quarantine_rejected",
        event_id=event_id,
        contract_id=row["contract_id"],
        reviewer=body.reviewer,
    )

    await log_access(pool, ctx, f"/api/quarantine/{event_id}/reject",
                     contract_id=row["contract_id"], ip_address=_client_ip(request))

    return {
        "success":     True,
        "event_id":    row["event_id"],
        "contract_id": row["contract_id"],
        "status":      "rejected",
        "reviewer":    body.reviewer,
        "reason":      body.reason,
    }
