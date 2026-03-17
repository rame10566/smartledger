"use client";

import { useState, useEffect, useCallback } from "react";
import { listQuarantine, type QuarantineRecord } from "@/lib/api";

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: QuarantineRecord["status"] }) {
  const colours: Record<string, string> = {
    pending:   "bg-yellow-100 text-yellow-800",
    approved:  "bg-green-100 text-green-800",
    rejected:  "bg-red-100 text-red-800",
    escalated: "bg-orange-100 text-orange-800",
  };
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${colours[status] ?? "bg-gray-100 text-gray-800"}`}
    >
      {status}
    </span>
  );
}

// ── Row detail expander ───────────────────────────────────────────────────────

function QuarantineRow({ record }: { record: QuarantineRecord }) {
  const [expanded, setExpanded] = useState(false);

  const failures: Array<{ code: string; message: string }> = (() => {
    try {
      const d = record.context_snapshot as {
        failures?: Array<{ code: string; message: string; field?: string; actual?: string }>;
      } | null;
      return d?.failures ?? [{ code: record.rejection_code, message: record.rejection_detail ?? "" }];
    } catch {
      return [{ code: record.rejection_code, message: record.rejection_detail ?? "" }];
    }
  })();

  return (
    <>
      <tr
        className="hover:bg-gray-50 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <td className="px-4 py-3 text-sm font-mono">{record.contract_id}</td>
        <td className="px-4 py-3 text-sm text-gray-600">{record.event_type}</td>
        <td className="px-4 py-3 text-sm text-gray-600">{record.source_system}</td>
        <td className="px-4 py-3 text-sm text-gray-600">{record.rejection_code}</td>
        <td className="px-4 py-3">
          <StatusBadge status={record.status} />
        </td>
        <td className="px-4 py-3 text-sm text-gray-500">
          {new Date(record.created_at).toLocaleString()}
        </td>
      </tr>

      {expanded && (
        <tr className="bg-gray-50 border-t border-gray-200">
          <td colSpan={6} className="px-4 py-4">
            <div className="grid grid-cols-2 gap-6 text-sm">
              <div>
                <p className="font-medium text-gray-700 mb-2">Validation Failures</p>
                <div className="space-y-2">
                  {failures.map((f, i) => (
                    <div key={i} className="bg-red-50 border border-red-200 rounded p-2">
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-red-100 text-red-800 mb-1">
                        {f.code}
                      </span>
                      <p className="text-xs text-gray-700 mt-1">{f.message}</p>
                    </div>
                  ))}
                </div>
                <div className="mt-3 p-3 bg-blue-50 border border-blue-200 rounded text-xs text-blue-800">
                  Data correction must be performed in the originating system
                  ({record.source_system}) and resubmitted for validation.
                </div>
              </div>
              <div>
                <p className="font-medium text-gray-700 mb-2">Event Details</p>
                <dl className="text-xs text-gray-600 space-y-1.5">
                  <div>
                    <dt className="inline font-medium">Event ID: </dt>
                    <dd className="inline font-mono">{record.event_id}</dd>
                  </div>
                  <div>
                    <dt className="inline font-medium">Event type: </dt>
                    <dd className="inline">{record.event_type}</dd>
                  </div>
                  <div>
                    <dt className="inline font-medium">Source system: </dt>
                    <dd className="inline">{record.source_system}</dd>
                  </div>
                  <div>
                    <dt className="inline font-medium">SLA deadline: </dt>
                    <dd className="inline">
                      {new Date(record.sla_deadline) < new Date() ? (
                        <span className="text-red-600 font-medium">
                          OVERDUE ({new Date(record.sla_deadline).toLocaleString()})
                        </span>
                      ) : (
                        new Date(record.sla_deadline).toLocaleString()
                      )}
                    </dd>
                  </div>
                </dl>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function QuarantinePage() {
  const [records, setRecords] = useState<QuarantineRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("pending");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listQuarantine(filter || undefined);
      setRecords(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  const pendingCount = records.filter((r) => r.status === "pending").length;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Validation Failures</h1>
          <p className="text-sm text-gray-500 mt-1">
            Events that failed validation — data must be corrected in the originating system and resubmitted
          </p>
        </div>
        <div className="flex items-center gap-3">
          {pendingCount > 0 && (
            <span className="bg-yellow-100 text-yellow-800 text-sm font-medium px-3 py-1 rounded-full">
              {pendingCount} pending
            </span>
          )}
          <select
            className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="pending">Pending</option>
            <option value="">All</option>
          </select>
          <button
            className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            onClick={load}
            disabled={loading}
          >
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          <strong>Error loading validation failures:</strong> {error}
          <br />
          <span className="text-xs text-red-500">
            Is the Dashboard API running? (./scripts/dev_start.sh)
          </span>
        </div>
      )}

      {!loading && !error && records.length === 0 && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg font-medium">No {filter || ""} validation failures</p>
          <p className="text-sm mt-1">All events have passed validation</p>
        </div>
      )}

      {records.length > 0 && (
        <div className="bg-white shadow rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {["Contract ID", "Event Type", "Source", "Failure Code", "Status", "Quarantined At"].map(
                  (h) => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {records.map((r) => (
                <QuarantineRow key={r.event_id} record={r} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
