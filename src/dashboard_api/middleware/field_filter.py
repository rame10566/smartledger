"""
Field-Level Filtering — Smart Data Gateway (REQUIREMENTS Section 6.5.3)

Loads the field visibility matrix from contracts.field_visibility and
strips fields the caller is not entitled to see before returning data.
"""

from typing import Any

import asyncpg

from shared.logging import get_logger
from shared.models.entities import AccessContext, OperationalRole

log = get_logger(__name__)

# Map field groups to the keys they control in API response payloads.
# Each field group maps to a set of top-level keys that get stripped if not visible.
FIELD_GROUP_KEYS: dict[str, set[str]] = {
    "contract_identity": {"contract_id", "contract_type", "state", "current_state", "los_system",
                          "origination_date", "maturity_date", "first_seen", "last_updated",
                          "record_count", "state_changed_at"},
    "vehicle":           {"vehicle", "vin", "vehicle_make", "vehicle_model", "vehicle_year",
                          "vehicle_msrp"},
    "financial_terms":   {"financial_terms", "amount_financed", "term_months", "interest_rate",
                          "monthly_payment", "down_payment", "residual_value"},
    "customer_pii_own":  {"customer", "customer_name", "customer_id", "customer_dob",
                          "customer_address", "customer_ssn_encrypted"},
    "customer_credit":   {"credit_score", "credit_tier"},
    "payment_history":   {"payment_history", "payments", "total_payments", "payment_records"},
    "delinquency":       {"delinquency", "days_past_due", "delinquency_status"},
    "dealer_margin":     {"dealer_margin", "dealer_incentives", "dealer_reserve"},
    "internal_risk":     {"risk_score", "risk_tier", "risk_flags", "internal_risk"},
    "compliance_notes":  {"compliance_notes", "compliance_flags"},
    "audit_trail":       {"audit_trail", "audit_log", "audit_entries"},
}


async def get_visible_field_groups(
    viewer_role: str,
    pool: asyncpg.Pool,
) -> set[str]:
    """
    Load visible field groups for a given viewer role from the database.
    Returns a set of field_group names that the role can see.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT field_group FROM contracts.field_visibility
            WHERE viewer_role = $1 AND visible = TRUE AND active = TRUE
            ORDER BY version DESC
            """,
            viewer_role,
        )

    return {r["field_group"] for r in rows}


def determine_viewer_role(ctx: AccessContext) -> str:
    """
    Determine the effective viewer role for field-level filtering.
    Operational roles use their role name. Party users use their party_role.
    """
    if ctx.role:
        return ctx.role
    if ctx.party_role:
        return ctx.party_role
    return "borrower"  # default: most restrictive


async def filter_fields(
    data: dict[str, Any] | list[dict[str, Any]],
    ctx: AccessContext,
    pool: asyncpg.Pool,
) -> tuple[dict[str, Any] | list[dict[str, Any]], list[str], list[str]]:
    """
    Apply field-level filtering to API response data.

    Returns:
        (filtered_data, fields_returned, fields_filtered)
    """
    viewer_role = determine_viewer_role(ctx)
    visible_groups = await get_visible_field_groups(viewer_role, pool)

    # If no visibility config found, default to most restrictive
    if not visible_groups:
        log.warning("no_visibility_config", viewer_role=viewer_role)
        visible_groups = {"contract_identity"}

    # Compute which keys to keep and which to strip
    allowed_keys: set[str] = set()
    stripped_groups: list[str] = []
    returned_groups: list[str] = []

    for group, keys in FIELD_GROUP_KEYS.items():
        if group in visible_groups:
            allowed_keys.update(keys)
            returned_groups.append(group)
        else:
            stripped_groups.append(group)

    if isinstance(data, list):
        filtered = [_strip_dict(item, allowed_keys) for item in data]
    else:
        filtered = _strip_dict(data, allowed_keys)

    return filtered, returned_groups, stripped_groups


def _strip_dict(d: dict[str, Any], allowed_keys: set[str]) -> dict[str, Any]:
    """Remove keys not in the allowed set. Preserves keys not mapped to any field group."""
    # Collect all keys controlled by any field group
    all_controlled_keys: set[str] = set()
    for keys in FIELD_GROUP_KEYS.values():
        all_controlled_keys.update(keys)

    result = {}
    for k, v in d.items():
        if k not in all_controlled_keys:
            # Key not controlled by any field group — pass through (e.g., record_id, timestamps)
            result[k] = v
        elif k in allowed_keys:
            result[k] = v
        # else: stripped

    return result
