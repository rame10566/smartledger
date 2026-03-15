"""
Unit tests for the Reporting MCP server tools.

Tests each report generator with a mocked asyncpg pool, verifying:
  - generate_report returns the correct structure for each report type
  - list_reports returns a list of report summaries
  - get_report returns the stored report
  - export_report returns valid JSON and CSV
  - Unknown report types are rejected gracefully
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Helpers for mocking asyncpg rows ─────────────────────────────────────────

class _Row(dict):
    """asyncpg Record-like dict that also supports attribute access."""
    def __getitem__(self, item):
        return super().__getitem__(item)


def _make_pool(fetch_return=None, fetchrow_return=None, fetchval_return=None):
    """
    Build a minimal asyncpg Pool mock.

    The reporting server calls pool.fetch / pool.fetchrow / pool.fetchval
    directly (not via pool.acquire).  Other servers use pool.acquire(), so the
    conn mock is also wired for those cases.
    """
    pool = AsyncMock()
    pool.fetch    = AsyncMock(return_value=fetch_return or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetchval = AsyncMock(return_value=fetchval_return or 0)
    pool.execute  = AsyncMock()

    # Also set up an acquire() context manager for servers that use it
    conn = AsyncMock()
    conn.fetch    = pool.fetch
    conn.fetchrow = pool.fetchrow
    conn.fetchval = pool.fetchval
    conn.execute  = pool.execute

    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return pool, conn


# ─── generate_report tests ────────────────────────────────────────────────────

def _make_pool_multi_fetch(fetch_side_effects, fetchrow_return=None, fetchval_return=None):
    """Pool mock where pool.fetch() returns different values on successive calls."""
    pool = AsyncMock()
    pool.fetch    = AsyncMock(side_effect=fetch_side_effects)
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetchval = AsyncMock(return_value=fetchval_return or 0)
    pool.execute  = AsyncMock()

    conn = AsyncMock()
    conn.fetch    = pool.fetch
    conn.fetchrow = pool.fetchrow
    conn.fetchval = pool.fetchval
    conn.execute  = pool.execute

    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return pool, conn


class TestGenerateReportPortfolioOverview:

    @patch("mcp_servers.reporting.server._store_report", new_callable=AsyncMock)
    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_portfolio_overview_returns_summary(self, mock_get_pool, mock_store):
        from mcp_servers.reporting.server import generate_report

        fin_row = _Row({
            "total_contracts":        2,
            "total_amount_financed":  57000.0,
            "avg_amount_financed":    28500.0,
            "avg_interest_rate":      0.0699,
            "avg_term_months":        72,
            "total_monthly_payments": 975.0,
        })
        pool, _ = _make_pool_multi_fetch(
            fetch_side_effects=[
                [_Row({"current_state": "active", "cnt": 2})],  # state query
                [fin_row],                                        # financial query
            ],
        )
        mock_get_pool.return_value = pool
        mock_store.return_value    = str(uuid.uuid4())

        result = await generate_report("portfolio_overview")

        assert "report_id"   in result
        assert "result"      in result
        summary = result["result"]["summary"]
        assert summary["total_contracts"] == 2
        assert summary["total_amount_financed"] == 57000.0

    @patch("mcp_servers.reporting.server._store_report", new_callable=AsyncMock)
    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_portfolio_by_state_breakdown(self, mock_get_pool, mock_store):
        from mcp_servers.reporting.server import generate_report

        fin_row = _Row({
            "total_contracts": 7, "total_amount_financed": 0,
            "avg_amount_financed": 0, "avg_interest_rate": 0,
            "avg_term_months": 0, "total_monthly_payments": 0,
        })
        pool, _ = _make_pool_multi_fetch(
            fetch_side_effects=[
                [
                    _Row({"current_state": "active",     "cnt": 5}),
                    _Row({"current_state": "delinquent", "cnt": 2}),
                ],
                [fin_row],
            ],
        )
        mock_get_pool.return_value = pool
        mock_store.return_value    = str(uuid.uuid4())

        result = await generate_report("portfolio_overview")
        by_state = result["result"]["by_state"]
        states   = {s["state"] for s in by_state}
        assert "active"     in states
        assert "delinquent" in states


class TestGenerateReportPaymentSummary:

    @patch("mcp_servers.reporting.server._store_report", new_callable=AsyncMock)
    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_payment_summary_structure(self, mock_get_pool, mock_store):
        from mcp_servers.reporting.server import generate_report

        pool, conn = _make_pool(
            fetch_return=[
                _Row({"source_system": "payment", "count": 10, "total_amount": 4875.0}),
            ],
            fetchrow_return=_Row({"total": 10, "total_amount": 4875.0}),
        )
        mock_get_pool.return_value = pool
        mock_store.return_value    = str(uuid.uuid4())

        result = await generate_report("payment_summary", filters={"date_from": "2026-01-01"})

        assert result["report_type"] == "payment_summary"
        summary = result["result"]["summary"]
        assert summary["total_payments"] == 10
        assert summary["total_amount"]   == 4875.0

    @patch("mcp_servers.reporting.server._store_report", new_callable=AsyncMock)
    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_payment_summary_by_source_system(self, mock_get_pool, mock_store):
        from mcp_servers.reporting.server import generate_report

        pool, conn = _make_pool(
            fetch_return=[
                _Row({"source_system": "customer_portal", "count": 3, "total_amount": 1462.5}),
                _Row({"source_system": "ivr",             "count": 2, "total_amount": 975.0}),
            ],
            fetchrow_return=_Row({"total": 5, "total_amount": 2437.5}),
        )
        mock_get_pool.return_value = pool
        mock_store.return_value    = str(uuid.uuid4())

        result   = await generate_report("payment_summary")
        by_sys   = result["result"]["by_source_system"]
        systems  = {s["source_system"] for s in by_sys}
        assert "customer_portal" in systems
        assert "ivr"             in systems


class TestGenerateReportDelinquency:

    @patch("mcp_servers.reporting.server._store_report", new_callable=AsyncMock)
    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_delinquency_report_structure(self, mock_get_pool, mock_store):
        from mcp_servers.reporting.server import generate_report

        now = datetime.now(timezone.utc)
        pool, conn = _make_pool(
            fetch_return=[
                _Row({
                    "contract_id":     "ORC-2024-001",
                    "current_state":   "delinquent",
                    "days_past_due":   35,
                    "state_changed_at": now,
                    "dealer_id":       "DLR-001",
                    "vehicle_make":    "Toyota",
                    "vehicle_model":   "Camry",
                    "vehicle_year":    2024,
                    "amount_financed": 28500.0,
                    "monthly_payment": 487.50,
                }),
            ],
        )
        mock_get_pool.return_value = pool
        mock_store.return_value    = str(uuid.uuid4())

        result    = await generate_report("delinquency_report")
        contracts = result["result"]["contracts"]
        assert len(contracts) == 1
        assert contracts[0]["bucket"] == "30-59 days"

    @patch("mcp_servers.reporting.server._store_report", new_callable=AsyncMock)
    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_delinquency_90_plus_bucket(self, mock_get_pool, mock_store):
        from mcp_servers.reporting.server import generate_report

        now = datetime.now(timezone.utc)
        pool, conn = _make_pool(
            fetch_return=[
                _Row({
                    "contract_id": "ORC-2024-002", "current_state": "delinquent",
                    "days_past_due": 95, "state_changed_at": now,
                    "dealer_id": "DLR-002", "vehicle_make": None,
                    "vehicle_model": None, "vehicle_year": None,
                    "amount_financed": 20000.0, "monthly_payment": 400.0,
                }),
            ],
        )
        mock_get_pool.return_value = pool
        mock_store.return_value    = str(uuid.uuid4())

        result = await generate_report("delinquency_report")
        assert result["result"]["contracts"][0]["bucket"] == "90+ days"


class TestGenerateReportUnknownType:

    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_unknown_report_type_returns_error(self, mock_get_pool):
        from mcp_servers.reporting.server import generate_report

        result = await generate_report("made_up_type")
        assert "error" in result
        assert result["error"] == "UNKNOWN_REPORT_TYPE"
        assert "valid_types" in result


# ─── list_reports tests ───────────────────────────────────────────────────────

class TestListReports:

    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_list_reports_returns_list(self, mock_get_pool):
        from mcp_servers.reporting.server import list_reports

        now = datetime.now(timezone.utc)
        pool, conn = _make_pool(
            fetch_return=[
                _Row({
                    "report_id":    uuid.uuid4(),
                    "report_type":  "portfolio_overview",
                    "title":        "Portfolio Overview",
                    "status":       "completed",
                    "requested_by": "dashboard",
                    "created_at":   now,
                    "completed_at": now,
                }),
            ],
        )
        mock_get_pool.return_value = pool

        result = await list_reports(limit=10)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["report_type"] == "portfolio_overview"

    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_list_reports_empty(self, mock_get_pool):
        from mcp_servers.reporting.server import list_reports

        pool, _ = _make_pool(fetch_return=[])
        mock_get_pool.return_value = pool

        result = await list_reports()
        assert result == []


# ─── get_report tests ─────────────────────────────────────────────────────────

class TestGetReport:

    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_get_report_found(self, mock_get_pool):
        from mcp_servers.reporting.server import get_report

        rid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        pool, conn = _make_pool(
            fetchrow_return=_Row({
                "report_id":    rid,
                "report_type":  "portfolio_overview",
                "title":        "Portfolio Overview",
                "parameters":   "{}",
                "result":       '{"summary": {}}',
                "status":       "completed",
                "requested_by": "dashboard",
                "created_at":   now,
                "completed_at": now,
            }),
        )
        mock_get_pool.return_value = pool

        result = await get_report(rid)
        assert result["report_id"]   == rid
        assert result["report_type"] == "portfolio_overview"
        assert isinstance(result["result"], dict)

    @patch("mcp_servers.reporting.server._get_pool", new_callable=AsyncMock)
    async def test_get_report_not_found(self, mock_get_pool):
        from mcp_servers.reporting.server import get_report

        pool, conn = _make_pool(fetchrow_return=None)
        mock_get_pool.return_value = pool

        result = await get_report("nonexistent-id")
        assert result.get("error") == "NOT_FOUND"


# ─── export_report tests ──────────────────────────────────────────────────────

class TestExportReport:

    @patch("mcp_servers.reporting.server.get_report", new_callable=AsyncMock)
    async def test_export_json(self, mock_get_report):
        from mcp_servers.reporting.server import export_report

        mock_get_report.return_value = {
            "report_id":   "test-id",
            "report_type": "payment_summary",
            "result":      {"summary": {"total": 5}},
        }

        result = await export_report("test-id", format="json")
        assert result["format"]       == "json"
        assert result["content_type"] == "application/json"
        data = json.loads(result["data"])
        assert data["summary"]["total"] == 5

    @patch("mcp_servers.reporting.server.get_report", new_callable=AsyncMock)
    async def test_export_csv(self, mock_get_report):
        from mcp_servers.reporting.server import export_report

        mock_get_report.return_value = {
            "report_id":   "test-id",
            "report_type": "delinquency_report",
            "result": {
                "contracts": [
                    {"contract_id": "ORC-001", "days_past_due": 35, "bucket": "30-59 days"},
                    {"contract_id": "ORC-002", "days_past_due": 12, "bucket": "1-14 days"},
                ],
            },
        }

        result = await export_report("test-id", format="csv")
        assert result["format"]       == "csv"
        assert result["content_type"] == "text/csv"
        assert "ORC-001" in result["data"]
        assert "ORC-002" in result["data"]

    @patch("mcp_servers.reporting.server.get_report", new_callable=AsyncMock)
    async def test_export_not_found(self, mock_get_report):
        from mcp_servers.reporting.server import export_report

        mock_get_report.return_value = {"error": "NOT_FOUND", "message": "Not found"}

        result = await export_report("bad-id")
        assert result.get("error") == "NOT_FOUND"

    @patch("mcp_servers.reporting.server.get_report", new_callable=AsyncMock)
    async def test_export_unsupported_format(self, mock_get_report):
        from mcp_servers.reporting.server import export_report

        mock_get_report.return_value = {
            "report_id":   "test-id",
            "report_type": "portfolio_overview",
            "result":      {"summary": {}},
        }

        result = await export_report("test-id", format="xml")
        assert result.get("error") == "UNSUPPORTED_FORMAT"
