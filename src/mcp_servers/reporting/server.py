"""
SmartLedger Reporting MCP Server

Provides report generation and retrieval for the Governance Dashboard.

Report types:
  portfolio_overview   — All contracts: count by state, total financed, avg rate
  origination_summary  — Contracts originated in a date range, by dealer / vehicle
  payment_summary      — Payments received in a date range, totals by source system
  delinquency_report   — Current delinquent accounts with days-past-due buckets
  quarantine_summary   — Quarantine events, failure codes, resolution rates
  audit_summary        — Saga outcomes, agent actions, processing throughput

Tools:
  generate_report(report_type, filters, requested_by) → report_id + result
  list_reports(limit, report_type?)                   → list of generated reports
  get_report(report_id)                               → single report
  export_report(report_id, format)                    → CSV or JSON string
"""

import csv
import io
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import asyncpg
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.config import get_settings
from shared.logging import get_logger

settings = get_settings()
logger   = get_logger(__name__)

# Module-level pool — initialised in lifespan
_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(server: FastMCP):
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        logger.info("reporting_mcp_started", phase=settings.phase)
    except Exception as e:
        logger.error("reporting_mcp_db_connect_failed", error=str(e))
    try:
        yield
    finally:
        if _pool:
            await _pool.close()
        logger.info("reporting_mcp_stopped")


mcp = FastMCP("smartledger-reporting", lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
    return _pool


async def _store_report(
    pool: asyncpg.Pool,
    report_type: str,
    title: str,
    parameters: dict,
    result: dict,
    requested_by: str,
) -> str:
    """Persist a completed report and return its report_id."""
    report_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO reports.generated
            (report_id, report_type, title, parameters, result, status, requested_by, completed_at)
        VALUES ($1, $2, $3, $4, $5, 'completed', $6, NOW())
        """,
        report_id,
        report_type,
        title,
        json.dumps(parameters),
        json.dumps(result),
        requested_by or "dashboard",
    )
    return report_id


# ─── Report generators ────────────────────────────────────────────────────────

async def _generate_portfolio_overview(pool: asyncpg.Pool, filters: dict) -> dict:
    """Count contracts by state, sum financed amounts, average interest rates.

    Reads from contracts.state (state breakdown) and contracts.records
    (origination payloads contain financial_terms).  The off-chain documents
    table may be empty in Phase 0, so we extract directly from ledger records.
    """

    state_rows = await pool.fetch(
        """
        SELECT current_state, COUNT(*) AS cnt
        FROM contracts.state
        GROUP BY current_state
        ORDER BY current_state
        """
    )

    # Extract financial terms from origination record payloads (JSONB)
    fin_rows = await pool.fetch(
        """
        SELECT
            COUNT(DISTINCT contract_id)                                                          AS total_contracts,
            COALESCE(SUM(
                (payload->'contract_data'->'financial_terms'->>'amount_financed')::NUMERIC
            ), 0)                                                                                AS total_amount_financed,
            COALESCE(AVG(
                (payload->'contract_data'->'financial_terms'->>'amount_financed')::NUMERIC
            ), 0)                                                                                AS avg_amount_financed,
            COALESCE(AVG(
                (payload->'contract_data'->'financial_terms'->>'interest_rate')::NUMERIC
            ), 0)                                                                                AS avg_interest_rate,
            COALESCE(AVG(
                (payload->'contract_data'->'financial_terms'->>'term_months')::NUMERIC
            ), 0)                                                                                AS avg_term_months,
            COALESCE(SUM(
                (payload->'contract_data'->'financial_terms'->>'monthly_payment')::NUMERIC
            ), 0)                                                                                AS total_monthly_payments
        FROM contracts.records
        WHERE record_type = 'origination'
        """
    )

    fin = dict(fin_rows[0]) if fin_rows else {}

    return {
        "report_type": "portfolio_overview",
        "generated_at": _now_iso(),
        "summary": {
            "total_contracts":         int(fin.get("total_contracts", 0)),
            "total_amount_financed":   float(fin.get("total_amount_financed", 0)),
            "avg_amount_financed":     round(float(fin.get("avg_amount_financed", 0)), 2),
            "avg_interest_rate":       round(float(fin.get("avg_interest_rate", 0)), 4),
            "avg_term_months":         round(float(fin.get("avg_term_months", 0)), 1),
            "total_monthly_payments":  float(fin.get("total_monthly_payments", 0)),
        },
        "by_state": [
            {"state": row["current_state"], "count": row["cnt"]}
            for row in state_rows
        ],
    }


async def _generate_origination_summary(pool: asyncpg.Pool, filters: dict) -> dict:
    """Contracts originated in a date range, broken down by dealer and vehicle make."""
    date_from = filters.get("date_from", "2000-01-01")
    date_to   = filters.get("date_to",   "2099-12-31")

    rows = await pool.fetch(
        """
        SELECT
            d.dealer_id,
            d.dealer_name,
            d.vehicle_make,
            d.contract_type,
            COUNT(*)                       AS count,
            SUM(d.amount_financed)         AS total_financed,
            AVG(d.interest_rate)           AS avg_rate
        FROM contracts.documents d
        WHERE d.origination_date BETWEEN $1 AND $2
          AND d.deleted_per_regulation = FALSE
        GROUP BY d.dealer_id, d.dealer_name, d.vehicle_make, d.contract_type
        ORDER BY total_financed DESC NULLS LAST
        """,
        date_from,
        date_to,
    )

    total_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)                AS total,
            SUM(amount_financed)    AS total_financed
        FROM contracts.documents
        WHERE origination_date BETWEEN $1 AND $2
          AND deleted_per_regulation = FALSE
        """,
        date_from,
        date_to,
    )

    return {
        "report_type": "origination_summary",
        "generated_at": _now_iso(),
        "filters": {"date_from": date_from, "date_to": date_to},
        "summary": {
            "total_originated": int(total_row["total"] or 0),
            "total_financed":   float(total_row["total_financed"] or 0),
        },
        "breakdown": [
            {
                "dealer_id":    row["dealer_id"],
                "dealer_name":  row["dealer_name"],
                "vehicle_make": row["vehicle_make"],
                "contract_type": row["contract_type"],
                "count":        row["count"],
                "total_financed": float(row["total_financed"] or 0),
                "avg_rate":     round(float(row["avg_rate"] or 0), 4),
            }
            for row in rows
        ],
    }


async def _generate_payment_summary(pool: asyncpg.Pool, filters: dict) -> dict:
    """Payments recorded in the ledger within a date range, by source system."""
    date_from = filters.get("date_from", "2000-01-01")
    date_to   = filters.get("date_to",   "2099-12-31")

    rows = await pool.fetch(
        """
        SELECT
            r.payload->>'source_system'      AS source_system,
            COUNT(*)                          AS count,
            SUM((r.payload->>'amount')::NUMERIC)  AS total_amount
        FROM contracts.records r
        WHERE r.record_type = 'payment'
          AND r.created_at::DATE BETWEEN $1 AND $2
        GROUP BY r.payload->>'source_system'
        ORDER BY total_amount DESC NULLS LAST
        """,
        date_from,
        date_to,
    )

    total_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)                                        AS total,
            SUM((payload->>'amount')::NUMERIC)              AS total_amount
        FROM contracts.records
        WHERE record_type = 'payment'
          AND created_at::DATE BETWEEN $1 AND $2
        """,
        date_from,
        date_to,
    )

    return {
        "report_type": "payment_summary",
        "generated_at": _now_iso(),
        "filters": {"date_from": date_from, "date_to": date_to},
        "summary": {
            "total_payments": int(total_row["total"] or 0),
            "total_amount":   float(total_row["total_amount"] or 0),
        },
        "by_source_system": [
            {
                "source_system": row["source_system"] or "unknown",
                "count":         row["count"],
                "total_amount":  float(row["total_amount"] or 0),
            }
            for row in rows
        ],
    }


async def _generate_delinquency_report(pool: asyncpg.Pool, filters: dict) -> dict:
    """Current delinquent contracts grouped by days-past-due buckets."""

    rows = await pool.fetch(
        """
        SELECT
            cs.contract_id,
            cs.current_state,
            cs.days_past_due,
            cs.state_changed_at,
            d.dealer_id,
            d.vehicle_make,
            d.vehicle_model,
            d.vehicle_year,
            d.amount_financed,
            d.monthly_payment
        FROM contracts.state cs
        LEFT JOIN contracts.documents d USING (contract_id)
        WHERE cs.current_state = 'delinquent'
          AND (d.deleted_per_regulation IS NULL OR d.deleted_per_regulation = FALSE)
        ORDER BY cs.days_past_due DESC
        """
    )

    def _bucket(dpd: int) -> str:
        if dpd is None or dpd < 1:
            return "0 days"
        if dpd <= 14:
            return "1-14 days"
        if dpd <= 29:
            return "15-29 days"
        if dpd <= 59:
            return "30-59 days"
        if dpd <= 89:
            return "60-89 days"
        return "90+ days"

    buckets: dict[str, int] = {}
    contracts = []
    for row in rows:
        dpd    = int(row["days_past_due"] or 0)
        bucket = _bucket(dpd)
        buckets[bucket] = buckets.get(bucket, 0) + 1
        contracts.append({
            "contract_id":    row["contract_id"],
            "days_past_due":  dpd,
            "bucket":         bucket,
            "dealer_id":      row["dealer_id"],
            "vehicle":        f"{row['vehicle_year'] or ''} {row['vehicle_make'] or ''} {row['vehicle_model'] or ''}".strip(),
            "amount_financed": float(row["amount_financed"] or 0),
            "monthly_payment": float(row["monthly_payment"] or 0),
            "state_changed_at": row["state_changed_at"].isoformat() if row["state_changed_at"] else None,
        })

    return {
        "report_type": "delinquency_report",
        "generated_at": _now_iso(),
        "summary": {
            "total_delinquent": len(contracts),
            "by_bucket": buckets,
        },
        "contracts": contracts,
    }


async def _generate_quarantine_summary(pool: asyncpg.Pool, filters: dict) -> dict:
    """Quarantine events: resolution rates, failure codes, SLA status."""

    status_rows = await pool.fetch(
        """
        SELECT status, COUNT(*) AS cnt
        FROM validation.quarantine
        GROUP BY status
        ORDER BY status
        """
    )

    code_rows = await pool.fetch(
        """
        SELECT rejection_code, COUNT(*) AS cnt
        FROM validation.quarantine
        GROUP BY rejection_code
        ORDER BY cnt DESC
        LIMIT 20
        """
    )

    sla_overdue = await pool.fetchval(
        """
        SELECT COUNT(*) FROM validation.quarantine
        WHERE status = 'pending' AND sla_deadline < NOW()
        """
    )

    return {
        "report_type": "quarantine_summary",
        "generated_at": _now_iso(),
        "summary": {
            "sla_overdue": int(sla_overdue or 0),
            "by_status": [
                {"status": row["status"], "count": row["cnt"]}
                for row in status_rows
            ],
        },
        "top_failure_codes": [
            {"code": row["rejection_code"], "count": row["cnt"]}
            for row in code_rows
        ],
    }


async def _generate_audit_summary(pool: asyncpg.Pool, filters: dict) -> dict:
    """Agent actions, saga outcomes, processing throughput."""

    action_rows = await pool.fetch(
        """
        SELECT action, COUNT(*) AS cnt
        FROM audit.log
        GROUP BY action
        ORDER BY cnt DESC
        LIMIT 20
        """
    )

    outcome_rows = await pool.fetch(
        """
        SELECT outcome, COUNT(*) AS cnt
        FROM sagas.processed_events
        GROUP BY outcome
        ORDER BY cnt DESC
        """
    )

    recent = await pool.fetch(
        """
        SELECT action, actor, contract_id, created_at
        FROM audit.log
        ORDER BY created_at DESC
        LIMIT 50
        """
    )

    return {
        "report_type": "audit_summary",
        "generated_at": _now_iso(),
        "top_actions": [
            {"action": row["action"], "count": row["cnt"]}
            for row in action_rows
        ],
        "saga_outcomes": [
            {"outcome": row["outcome"], "count": row["cnt"]}
            for row in outcome_rows
        ],
        "recent_events": [
            {
                "action":      row["action"],
                "actor":       row["actor"],
                "contract_id": row["contract_id"],
                "created_at":  row["created_at"].isoformat(),
            }
            for row in recent
        ],
    }


_GENERATORS = {
    "portfolio_overview":  _generate_portfolio_overview,
    "origination_summary": _generate_origination_summary,
    "payment_summary":     _generate_payment_summary,
    "delinquency_report":  _generate_delinquency_report,
    "quarantine_summary":  _generate_quarantine_summary,
    "audit_summary":       _generate_audit_summary,
}

_TITLES = {
    "portfolio_overview":  "Portfolio Overview",
    "origination_summary": "Origination Summary",
    "payment_summary":     "Payment Summary",
    "delinquency_report":  "Delinquency Report",
    "quarantine_summary":  "Quarantine Summary",
    "audit_summary":       "Audit Summary",
}


# ─── MCP Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
async def generate_report(
    report_type: str,
    filters: dict | None = None,
    requested_by: str = "dashboard",
) -> dict[str, Any]:
    """
    Generate a report and persist it.

    report_type: portfolio_overview | origination_summary | payment_summary |
                 delinquency_report | quarantine_summary | audit_summary

    filters (optional): date_from, date_to (ISO date strings)

    Returns: {report_id, report_type, title, result, generated_at}
    """
    if report_type not in _GENERATORS:
        return {
            "error":          "UNKNOWN_REPORT_TYPE",
            "message":        f"Unknown report type: {report_type!r}",
            "valid_types":    list(_GENERATORS.keys()),
        }

    filters = filters or {}
    pool    = await _get_pool()

    try:
        result = await _GENERATORS[report_type](pool, filters)
        title  = _TITLES[report_type]
        report_id = await _store_report(pool, report_type, title, filters, result, requested_by)

        logger.info(
            "report_generated",
            report_id=report_id,
            report_type=report_type,
            requested_by=requested_by,
        )

        return {
            "report_id":   report_id,
            "report_type": report_type,
            "title":       title,
            "generated_at": _now_iso(),
            "result":      result,
        }

    except Exception as e:
        logger.error("report_generation_failed", report_type=report_type, error=str(e))
        return {"error": "REPORT_GENERATION_FAILED", "message": str(e)}


@mcp.tool()
async def list_reports(
    limit: int = 20,
    report_type: str | None = None,
) -> list[dict[str, Any]]:
    """
    List previously generated reports, most recent first.

    report_type (optional): filter by type
    limit: max results (default 20)
    """
    pool = await _get_pool()

    if report_type:
        rows = await pool.fetch(
            """
            SELECT report_id, report_type, title, status, requested_by, created_at, completed_at
            FROM reports.generated
            WHERE report_type = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            report_type,
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT report_id, report_type, title, status, requested_by, created_at, completed_at
            FROM reports.generated
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )

    return [
        {
            "report_id":    str(row["report_id"]),
            "report_type":  row["report_type"],
            "title":        row["title"],
            "status":       row["status"],
            "requested_by": row["requested_by"],
            "created_at":   row["created_at"].isoformat(),
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        }
        for row in rows
    ]


@mcp.tool()
async def get_report(report_id: str) -> dict[str, Any]:
    """
    Fetch a previously generated report by ID.

    Returns: full report including the result payload.
    """
    pool = await _get_pool()
    row  = await pool.fetchrow(
        """
        SELECT report_id, report_type, title, parameters, result,
               status, requested_by, created_at, completed_at
        FROM reports.generated
        WHERE report_id = $1
        """,
        report_id,
    )

    if not row:
        return {"error": "NOT_FOUND", "message": f"Report {report_id!r} not found"}

    return {
        "report_id":    str(row["report_id"]),
        "report_type":  row["report_type"],
        "title":        row["title"],
        "parameters":   json.loads(row["parameters"]) if row["parameters"] else {},
        "result":       json.loads(row["result"])      if row["result"] else {},
        "status":       row["status"],
        "requested_by": row["requested_by"],
        "created_at":   row["created_at"].isoformat(),
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
    }


@mcp.tool()
async def export_report(
    report_id: str,
    format: str = "json",  # noqa: A002
) -> dict[str, Any]:
    """
    Export a report as JSON or CSV.

    format: "json" (default) | "csv"

    Returns: {content_type, data} where data is a string.
    For CSV, the top-level list in the result is serialised; falls back to JSON if
    the report result has no obvious list to flatten.
    """
    report = await get_report(report_id)
    if "error" in report:
        return report

    result = report["result"]

    if format == "json":
        return {
            "report_id":    report_id,
            "format":       "json",
            "content_type": "application/json",
            "data":         json.dumps(result, indent=2),
        }

    if format == "csv":
        # Find the first list value in result to use as the CSV rows
        rows_list = None
        for value in result.values():
            if isinstance(value, list) and value:
                rows_list = value
                break

        if not rows_list:
            # Fallback: flatten summary dict into key/value rows
            rows_list = [{"key": k, "value": v} for k, v in result.get("summary", result).items()]

        buf = io.StringIO()
        if rows_list:
            writer = csv.DictWriter(buf, fieldnames=list(rows_list[0].keys()), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows_list)

        return {
            "report_id":    report_id,
            "format":       "csv",
            "content_type": "text/csv",
            "data":         buf.getvalue(),
        }

    return {"error": "UNSUPPORTED_FORMAT", "message": f"Unsupported format: {format!r}. Use 'json' or 'csv'."}


if __name__ == "__main__":
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = 8004
    mcp.run(transport="streamable-http")
