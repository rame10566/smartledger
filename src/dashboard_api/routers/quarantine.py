"""
Quarantine Router

Read-only endpoints for the quarantine audit trail.
SmartLedger does not override or correct data — that is the responsibility
of the originating system (LOS, etc.). This router provides visibility
into validation failures so operators can notify upstream systems.

GET  /api/quarantine               — list quarantined events (all statuses)
GET  /api/quarantine/{event_id}    — single quarantine record detail
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from dashboard_api.middleware.access_audit import log_access
from dashboard_api.middleware.access_control import (
    AccessContext,
    get_access_context,
)
from shared.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["quarantine"])


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
    """Return a single quarantine record with full failure details."""
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
