"""
Access Audit Logging — Smart Data Gateway (REQUIREMENTS Section 6.5.5, PBAC-19 to PBAC-23)

Logs every read request through the Dashboard API to audit.access_log.
Satisfies REG-06: "Full audit trail for all data access."
"""

from typing import Any

import asyncpg

from shared.logging import get_logger
from shared.models.entities import AccessContext

log = get_logger(__name__)


async def log_access(
    pool: asyncpg.Pool,
    ctx: AccessContext,
    endpoint: str,
    contract_id: str | None = None,
    fields_returned: list[str] | None = None,
    fields_filtered: list[str] | None = None,
    access_granted: bool = True,
    denial_reason: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Write an access audit log entry to audit.access_log."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit.access_log
                    (actor_id, actor_type, role, party_role, contract_id,
                     endpoint, fields_returned, fields_filtered,
                     access_granted, denial_reason, ip_address)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                ctx.actor_id,
                ctx.actor_type,
                ctx.role,
                ctx.party_role,
                contract_id,
                endpoint,
                fields_returned,
                fields_filtered,
                access_granted,
                denial_reason,
                ip_address,
            )
    except Exception as e:
        # Access audit failures should not break the request
        log.error("access_audit_write_failed", error=str(e), endpoint=endpoint)
