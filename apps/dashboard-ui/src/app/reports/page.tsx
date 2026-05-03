"use client";

import { useState, useEffect, useCallback } from "react";
import {
  listReportTypes,
  listReports,
  generateReport,
  getReport,
  exportReport,
  type ReportType,
  type ReportSummary,
  type Report,
} from "@/lib/api";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString();
}

function StatusBadge({ status }: { status: ReportSummary["status"] }) {
  const colors: Record<string, string> = {
    completed: "bg-green-100 text-green-800",
    pending:   "bg-yellow-100 text-yellow-800",
    failed:    "bg-red-100 text-red-800",
  };
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${colors[status] ?? "bg-gray-100 text-gray-700"}`}
    >
      {status}
    </span>
  );
}

// ─── Result renderers ─────────────────────────────────────────────────────────

function PortfolioResult({ result }: { result: Record<string, unknown> }) {
  const summary = result.summary as Record<string, unknown> | undefined;
  const byState = result.by_state as Array<{ state: string; count: number }> | undefined;
  if (!summary) return null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: "Total Contracts",     value: String(summary.total_contracts ?? 0) },
          { label: "Total Financed",      value: `$${Number(summary.total_amount_financed ?? 0).toLocaleString()}` },
          { label: "Avg Rate",            value: `${(Number(summary.avg_interest_rate ?? 0) * 100).toFixed(2)}%` },
          { label: "Avg Term",            value: `${summary.avg_term_months ?? 0} mo` },
          { label: "Avg Amount Financed", value: `$${Number(summary.avg_amount_financed ?? 0).toLocaleString()}` },
          { label: "Total Monthly Pmts",  value: `$${Number(summary.total_monthly_payments ?? 0).toLocaleString()}` },
        ].map(({ label, value }) => (
          <div key={label} className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wider">{label}</p>
            <p className="text-lg font-semibold text-gray-900 mt-0.5">{value}</p>
          </div>
        ))}
      </div>
      {byState && byState.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">By State</h3>
          <div className="flex gap-2 flex-wrap">
            {byState.map((s) => (
              <span key={s.state} className="px-3 py-1 bg-blue-50 text-blue-800 rounded text-sm">
                {s.state.replace(/_/g, " ")}: <strong>{s.count}</strong>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function TableResult({ rows, label }: { rows: Record<string, unknown>[]; label: string }) {
  if (!rows || rows.length === 0) return <p className="text-gray-500 text-sm">No {label} data.</p>;
  const keys = Object.keys(rows[0]);
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50">
          <tr>
            {keys.map((k) => (
              <th key={k} className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                {k.replace(/_/g, " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-200">
          {rows.map((row, i) => (
            <tr key={i} className="hover:bg-gray-50">
              {keys.map((k) => (
                <td key={k} className="px-3 py-2 text-gray-700 whitespace-nowrap">
                  {row[k] === null || row[k] === undefined
                    ? "—"
                    : typeof row[k] === "number"
                    ? Number(row[k]).toLocaleString()
                    : String(row[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReportResultView({ report }: { report: Report }) {
  const { report_type, result } = report;

  if (report_type === "portfolio_overview") {
    return <PortfolioResult result={result} />;
  }

  if (report_type === "origination_summary" || report_type === "payment_summary") {
    const summary = result.summary as Record<string, unknown> | undefined;
    const rows = (result.breakdown ?? result.by_source_system) as Record<string, unknown>[] | undefined;
    return (
      <div className="space-y-4">
        {summary && (
          <div className="flex gap-4">
            {Object.entries(summary).map(([k, v]) => (
              <div key={k} className="bg-gray-50 rounded p-3">
                <p className="text-xs text-gray-500 uppercase tracking-wider">{k.replace(/_/g, " ")}</p>
                <p className="text-lg font-semibold text-gray-900 mt-0.5">
                  {typeof v === "number" ? v.toLocaleString() : String(v)}
                </p>
              </div>
            ))}
          </div>
        )}
        {rows && <TableResult rows={rows} label="breakdown" />}
      </div>
    );
  }

  if (report_type === "delinquency_report") {
    const summary  = result.summary as Record<string, unknown> | undefined;
    const contracts = result.contracts as Record<string, unknown>[] | undefined;
    const buckets  = summary?.by_bucket as Record<string, number> | undefined;
    return (
      <div className="space-y-4">
        {summary && (
          <div className="flex gap-4 items-start">
            <div className="bg-red-50 rounded p-3">
              <p className="text-xs text-red-500 uppercase tracking-wider">Total Delinquent</p>
              <p className="text-2xl font-bold text-red-700 mt-0.5">{String(summary.total_delinquent)}</p>
            </div>
            {buckets && (
              <div className="flex gap-2 flex-wrap">
                {Object.entries(buckets).map(([bucket, count]) => (
                  <span key={bucket} className="px-3 py-1 bg-orange-50 text-orange-800 rounded text-sm">
                    {bucket}: <strong>{count}</strong>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
        {contracts && <TableResult rows={contracts} label="delinquent contracts" />}
      </div>
    );
  }

  if (report_type === "quarantine_summary") {
    const byStatus = (result.summary as Record<string, unknown>)?.by_status as Array<{ status: string; count: number }> | undefined;
    const codes    = result.top_failure_codes as Array<{ code: string; count: number }> | undefined;
    const overdue  = (result.summary as Record<string, unknown>)?.sla_overdue;
    return (
      <div className="space-y-4">
        <div className="flex gap-4">
          {overdue !== undefined && (
            <div className="bg-red-50 rounded p-3">
              <p className="text-xs text-red-500 uppercase tracking-wider">SLA Overdue</p>
              <p className="text-2xl font-bold text-red-700 mt-0.5">{String(overdue)}</p>
            </div>
          )}
          {byStatus?.map((s) => (
            <div key={s.status} className="bg-gray-50 rounded p-3">
              <p className="text-xs text-gray-500 uppercase tracking-wider">{s.status}</p>
              <p className="text-lg font-semibold text-gray-900 mt-0.5">{s.count}</p>
            </div>
          ))}
        </div>
        {codes && codes.length > 0 && (
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-2">Top Failure Codes</h3>
            <TableResult rows={codes as Record<string, unknown>[]} label="failure codes" />
          </div>
        )}
      </div>
    );
  }

  if (report_type === "audit_summary") {
    const actions  = result.top_actions  as Record<string, unknown>[] | undefined;
    const outcomes = result.saga_outcomes as Record<string, unknown>[] | undefined;
    return (
      <div className="space-y-4">
        {actions && (
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-2">Top Agent Actions</h3>
            <TableResult rows={actions} label="actions" />
          </div>
        )}
        {outcomes && (
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-2">Saga Outcomes</h3>
            <TableResult rows={outcomes} label="outcomes" />
          </div>
        )}
      </div>
    );
  }

  // Fallback: raw JSON viewer
  return (
    <pre className="text-xs bg-gray-50 rounded p-4 overflow-auto max-h-80">
      {JSON.stringify(result, null, 2)}
    </pre>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function ReportsPage() {
  const [types,       setTypes]       = useState<ReportType[]>([]);
  const [history,     setHistory]     = useState<ReportSummary[]>([]);
  const [selected,    setSelected]    = useState<string>("");
  const [dateFrom,    setDateFrom]    = useState<string>("");
  const [dateTo,      setDateTo]      = useState<string>("");
  const [generating,  setGenerating]  = useState(false);
  const [activeReport, setActiveReport] = useState<Report | null>(null);
  const [error,       setError]       = useState<string | null>(null);
  const [loadingId,   setLoadingId]   = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await listReports());
    } catch {
      // non-fatal
    }
  }, []);

  useEffect(() => {
    Promise.all([listReportTypes(), listReports()])
      .then(([t, h]) => {
        setTypes(t);
        setHistory(h);
        if (t.length > 0) setSelected(t[0].type);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setInitialLoading(false));
  }, []);

  const selectedType = types.find((t) => t.type === selected);

  async function handleGenerate() {
    setGenerating(true);
    setError(null);
    setActiveReport(null);
    try {
      const report = await generateReport(
        selected,
        selectedType?.supports_date_filter ? dateFrom || undefined : undefined,
        selectedType?.supports_date_filter ? dateTo   || undefined : undefined,
      );
      setActiveReport(report);
      await refreshHistory();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  async function handleLoad(reportId: string) {
    setLoadingId(reportId);
    setError(null);
    try {
      setActiveReport(await getReport(reportId));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingId(null);
    }
  }

  async function handleExport(reportId: string, fmt: "json" | "csv") {
    try {
      const exp  = await exportReport(reportId, fmt);
      const blob = new Blob([exp.data], { type: exp.content_type });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = `report-${reportId.slice(0, 8)}.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-1">Reports</h1>
      <p className="text-sm text-gray-500 mb-6">Generate and view portfolio reports</p>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      {initialLoading && <p className="text-gray-500 text-sm mb-6">Loading report types...</p>}

      {/* Generate panel */}
      <div className="bg-white shadow rounded-lg p-6 mb-6">
        <h2 className="text-base font-semibold text-gray-800 mb-4">Generate New Report</h2>
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1 uppercase tracking-wider">Report Type</label>
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              className="border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {types.map((t) => (
                <option key={t.type} value={t.type}>{t.title}</option>
              ))}
            </select>
          </div>

          {selectedType?.supports_date_filter && (
            <>
              <div>
                <label className="block text-xs text-gray-500 mb-1 uppercase tracking-wider">From</label>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1 uppercase tracking-wider">To</label>
                <input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </>
          )}

          <button
            onClick={handleGenerate}
            disabled={generating || !selected}
            className="px-4 py-2 bg-blue-700 text-white rounded text-sm font-medium hover:bg-blue-800 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {generating ? "Generating…" : "Generate Report"}
          </button>
        </div>

        {selectedType && (
          <p className="mt-3 text-xs text-gray-500">{selectedType.description}</p>
        )}
      </div>

      {/* Active report result */}
      {activeReport && (
        <div className="bg-white shadow rounded-lg p-6 mb-6">
          <div className="flex items-start justify-between mb-4">
            <div>
              <h2 className="text-base font-semibold text-gray-800">{activeReport.title}</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Generated {fmtDate(activeReport.created_at)}
                {activeReport.requested_by && ` by ${activeReport.requested_by}`}
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => handleExport(activeReport.report_id, "json")}
                className="px-3 py-1.5 border border-gray-300 rounded text-xs text-gray-700 hover:bg-gray-50"
              >
                Export JSON
              </button>
              <button
                onClick={() => handleExport(activeReport.report_id, "csv")}
                className="px-3 py-1.5 border border-gray-300 rounded text-xs text-gray-700 hover:bg-gray-50"
              >
                Export CSV
              </button>
            </div>
          </div>
          <ReportResultView report={activeReport} />
        </div>
      )}

      {/* Report history */}
      {history.length > 0 && (
        <div className="bg-white shadow rounded-lg overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-base font-semibold text-gray-800">Report History</h2>
          </div>
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {["Title", "Type", "Status", "Generated At", "Actions"].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200 text-sm">
              {history.map((r) => (
                <tr key={r.report_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-medium text-gray-900">{r.title}</td>
                  <td className="px-4 py-3 font-mono text-xs text-gray-600">{r.report_type}</td>
                  <td className="px-4 py-3"><StatusBadge status={r.status} /></td>
                  <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                    {fmtDate(r.created_at)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleLoad(r.report_id)}
                        disabled={loadingId === r.report_id}
                        className="text-blue-600 hover:underline text-xs disabled:opacity-50"
                      >
                        {loadingId === r.report_id ? "Loading…" : "View"}
                      </button>
                      <button
                        onClick={() => handleExport(r.report_id, "json")}
                        className="text-gray-500 hover:underline text-xs"
                      >
                        JSON
                      </button>
                      <button
                        onClick={() => handleExport(r.report_id, "csv")}
                        className="text-gray-500 hover:underline text-xs"
                      >
                        CSV
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {history.length === 0 && !generating && (
        <p className="text-gray-400 text-sm text-center py-8">
          No reports generated yet. Use the form above to generate your first report.
        </p>
      )}
    </div>
  );
}
