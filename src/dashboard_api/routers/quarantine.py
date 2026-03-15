"""
Quarantine Router

Endpoints for the human-review quarantine queue.

GET  /api/quarantine               — list pending (and recently resolved) events
GET  /api/quarantine/{event_id}    — single quarantine record
POST /api/quarantine/{event_id}/approve  — approve override (calls Validation MCP)
POST /api/quarantine/{event_id}/reject   — reject the event
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from dashboard_api.mcp_clients import validation
from shared.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["quarantine"])


# ── Request / Response models ──────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    reason:    str
    reviewer:  str


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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/quarantine")
async def list_quarantined(
    request:        Request,
    status:         str | None = None,   # pending | approved | rejected
    contract_id:    str | None = None,
    limit:          int = 50,
) -> list[dict[str, Any]]:
    """
    List quarantined events, newest first.
    Defaults to all statuses so reviewers can see the full history.
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

    return [_row_to_dict(r) for r in rows]


@router.get("/quarantine/{event_id}")
async def get_quarantine_record(
    event_id: str,
    request:  Request,
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

    return _row_to_dict(row)


@router.post("/quarantine/{event_id}/approve")
async def approve_quarantine(
    event_id: str,
    body:     ApproveRequest,
    request:  Request,
) -> dict[str, Any]:
    """
    Human reviewer approves an override.

    Calls the Validation Engine MCP approve_override tool, which:
      1. Updates the quarantine record to status='approved'
      2. Publishes a quarantine.approved event to Redis Streams
      3. The agent then picks it up and completes the origination flow

    Returns the result from the Validation Engine.
    """
    log.info(
        "quarantine_approve_requested",
        event_id=event_id,
        reviewer=body.reviewer,
    )
    try:
        result = await validation.approve_override(
            event_id=event_id,
            reason=body.reason,
            reviewer=body.reviewer,
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
    return result


@router.post("/quarantine/{event_id}/reject")
async def reject_quarantine(
    event_id: str,
    body:     RejectRequest,
    request:  Request,
) -> dict[str, Any]:
    """
    Human reviewer rejects a quarantined event.
    Updates the quarantine record to status='rejected'.
    No further processing happens — the origination is declined.
    """
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

    return {
        "success":     True,
        "event_id":    row["event_id"],
        "contract_id": row["contract_id"],
        "status":      "rejected",
        "reviewer":    body.reviewer,
        "reason":      body.reason,
    }
