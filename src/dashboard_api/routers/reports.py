"""
Dashboard API — Reports Router

Endpoints:
  GET  /api/reports                     — list recent reports
  GET  /api/reports/types               — list available report types
  POST /api/reports/generate            — generate a new report
  GET  /api/reports/{report_id}         — get a specific report
  GET  /api/reports/{report_id}/export  — export as JSON or CSV
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from agent.core.mcp_client import reporting

router = APIRouter(tags=["reports"])


_REPORT_TYPES = [
    {
        "type":        "portfolio_overview",
        "title":       "Portfolio Overview",
        "description": "All contracts: count by state, total financed, average rates",
        "supports_date_filter": False,
    },
    {
        "type":        "origination_summary",
        "title":       "Origination Summary",
        "description": "Contracts originated in a date range, broken down by dealer and vehicle",
        "supports_date_filter": True,
    },
    {
        "type":        "payment_summary",
        "title":       "Payment Summary",
        "description": "Payments received in a date range, totals by source system",
        "supports_date_filter": True,
    },
    {
        "type":        "delinquency_report",
        "title":       "Delinquency Report",
        "description": "Current delinquent accounts with days-past-due buckets",
        "supports_date_filter": False,
    },
    {
        "type":        "quarantine_summary",
        "title":       "Quarantine Summary",
        "description": "Quarantine events, failure codes, and SLA resolution rates",
        "supports_date_filter": False,
    },
    {
        "type":        "audit_summary",
        "title":       "Audit Summary",
        "description": "Agent actions, saga outcomes, and processing throughput",
        "supports_date_filter": False,
    },
]


class GenerateReportRequest(BaseModel):
    report_type:  str
    date_from:    str | None = None
    date_to:      str | None = None
    requested_by: str = "dashboard"


@router.get("/reports/types")
async def get_report_types() -> list[dict[str, Any]]:
    """Return the list of available report types."""
    return _REPORT_TYPES


@router.get("/reports")
async def list_reports(
    limit:       int = Query(default=20, ge=1, le=100),
    report_type: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """List recently generated reports, most recent first."""
    result = await reporting.list_reports(limit=limit, report_type=report_type)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=500, detail=result.get("message", "Failed to list reports"))
    return result


@router.post("/reports/generate")
async def generate_report(body: GenerateReportRequest) -> dict[str, Any]:
    """Generate a new report and persist it."""
    filters: dict[str, Any] = {}
    if body.date_from:
        filters["date_from"] = body.date_from
    if body.date_to:
        filters["date_to"] = body.date_to

    result = await reporting.generate_report(
        report_type=body.report_type,
        filters=filters,
        requested_by=body.requested_by,
    )
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result.get("message", "Report generation failed"))
    return result


@router.get("/reports/{report_id}")
async def get_report(report_id: str) -> dict[str, Any]:
    """Fetch a previously generated report by ID."""
    result = await reporting.get_report(report_id=report_id)
    if isinstance(result, dict) and result.get("error") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail=f"Report {report_id!r} not found")
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=500, detail=result.get("message", "Failed to fetch report"))
    return result


@router.get("/reports/{report_id}/export")
async def export_report(
    report_id: str,
    format: str = Query(default="json"),  # noqa: A002
) -> dict[str, Any]:
    """
    Export a report as JSON or CSV.

    Returns {content_type, data} where data is a string.
    """
    result = await reporting.export_report(report_id=report_id, format=format)
    if isinstance(result, dict) and result.get("error") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail=f"Report {report_id!r} not found")
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result.get("message", "Export failed"))
    return result
