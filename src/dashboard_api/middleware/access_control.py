"""
Access Control — Smart Data Gateway (REQUIREMENTS Section 6.5)

Extracts caller identity from the request and produces an AccessContext
available to all route handlers as a FastAPI dependency.

POC mode: reads from X-SmartLedger-Identity JSON header.
Production: reads from Authorization Bearer JWT.
"""

import json
from typing import Any

import asyncpg
from fastapi import Depends, HTTPException, Request

from shared.config import get_settings
from shared.logging import get_logger
from shared.models.entities import AccessContext, OperationalRole, PartyRole

log = get_logger(__name__)
settings = get_settings()

# Operational roles that have cross-contract visibility
PRIVILEGED_ROLES = {OperationalRole.ADMIN, OperationalRole.AUDITOR, OperationalRole.COMPLIANCE}
WRITE_ROLES = {OperationalRole.ADMIN, OperationalRole.OPERATOR, OperationalRole.COMPLIANCE}


async def get_access_context(request: Request) -> AccessContext:
    """
    FastAPI dependency that extracts identity from the request.

    POC: X-SmartLedger-Identity header with JSON:
      {"actor_id": "...", "actor_type": "user", "role": "admin"}
      {"actor_id": "...", "actor_type": "user", "party_entity_id": "CUST-001", "party_role": "borrower"}

    If PBAC is disabled, returns a default admin context (backward-compatible).
    """
    if not settings.pbac_enabled:
        return AccessContext(actor_id="system", actor_type="agent", role=OperationalRole.ADMIN)

    identity_header = request.headers.get("X-SmartLedger-Identity")
    if not identity_header:
        raise HTTPException(status_code=401, detail="Missing identity header (X-SmartLedger-Identity)")

    try:
        data = json.loads(identity_header)
    except json.JSONDecodeError:
        raise HTTPException(status_code=401, detail="Invalid identity header: not valid JSON")

    actor_id = data.get("actor_id")
    if not actor_id:
        raise HTTPException(status_code=401, detail="Identity header must include actor_id")

    return AccessContext(
        actor_id=actor_id,
        actor_type=data.get("actor_type", "user"),
        role=data.get("role"),
        party_entity_id=data.get("party_entity_id"),
        party_role=data.get("party_role"),
    )


async def check_party_access(
    contract_id: str,
    ctx: AccessContext,
    pool: asyncpg.Pool,
) -> bool:
    """
    Check whether the caller is a party to the given contract.
    Returns True if the caller has access, False otherwise.

    Privileged operational roles (admin, auditor, compliance) always have access.
    The agent always has access.
    Party users must be listed in contracts.parties.
    """
    if ctx.actor_type == "agent":
        return True

    if ctx.role and ctx.role in PRIVILEGED_ROLES:
        return True

    # Operators have access to all contracts (they handle quarantine)
    if ctx.role == OperationalRole.OPERATOR:
        return True

    if not ctx.party_entity_id:
        return False

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM contracts.parties
            WHERE contract_id = $1 AND entity_id = $2
            """,
            contract_id,
            ctx.party_entity_id,
        )

    return row is not None


async def get_party_contract_ids(
    ctx: AccessContext,
    pool: asyncpg.Pool,
) -> list[str] | None:
    """
    For party users, return the list of contract_ids they are a party to.
    For privileged roles, return None (meaning: no filter, all contracts).
    """
    if ctx.actor_type == "agent":
        return None

    if ctx.role and ctx.role in PRIVILEGED_ROLES:
        return None

    if ctx.role == OperationalRole.OPERATOR:
        return None

    if not ctx.party_entity_id:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT contract_id FROM contracts.parties WHERE entity_id = $1",
            ctx.party_entity_id,
        )

    return [r["contract_id"] for r in rows]
