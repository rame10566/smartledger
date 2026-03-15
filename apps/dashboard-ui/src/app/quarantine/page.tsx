"use client";

import { useState, useEffect, useCallback } from "react";
import {
  listQuarantine,
  approveOverride,
  rejectQuarantine,
  type QuarantineRecord,
} from "@/lib/api";

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

// ── Override dialog ───────────────────────────────────────────────────────────

interface ActionDialogProps {
  record:   QuarantineRecord;
  mode:     "approve" | "reject";
  onDone:   () => void;
  onCancel: () => void;
}

function ActionDialog({ record, mode, onDone, onCancel }: ActionDialogProps) {
  const [reason,   setReason]   = useState("");
  const [reviewer, setReviewer] = useState("");
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  const submit = async () => {
    if (!reason.trim() || !reviewer.trim()) {
      setError("Both reason and reviewer name are required.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      if (mode === "approve") {
        await approveOverride(record.event_id, reason, reviewer);
      } else {
        await rejectQuarantine(record.event_id, reason, reviewer);
      }
      onDone();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md">
        <h2 className="text-lg font-semibold mb-1">
          {mode === "approve" ? "Approve Override" : "Reject Event"}
        </h2>
        <p className="text-sm text-gray-500 mb-4">
          Contract: <span className="font-mono">{record.contract_id}</span>
        </p>

        {mode === "approve" && (
          <div className="mb-3 p-3 bg-yellow-50 border border-yellow-200 rounded text-sm">
            <strong>Failures being overridden:</strong>
            <p className="text-gray-700 mt-1">{record.rejection_detail ?? record.rejection_code}</p>
          </div>
        )}

        <label className="block text-sm font-medium text-gray-700 mb-1">
          Reviewer name
        </label>
        <input
          className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="e.g. jane.doe"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
        />

        <label className="block text-sm font-medium text-gray-700 mb-1">
          {mode === "approve" ? "Override reason" : "Rejection reason"}
        </label>
        <textarea
          className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
          rows={3}
          placeholder={
            mode === "approve"
              ? "e.g. VIN confirmed via manual dealer check — system lookup was stale"
              : "e.g. Fraudulent application — refer to compliance team"
          }
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />

        {error && (
          <p className="text-sm text-red-600 mb-3">{error}</p>
        )}

        <div className="flex gap-3 justify-end">
          <button
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900"
            onClick={onCancel}
            disabled={loading}
          >
            Cancel
          </button>
          <button
            className={`px-4 py-2 text-sm font-medium text-white rounded ${
              mode === "approve"
                ? "bg-green-600 hover:bg-green-700"
                : "bg-red-600 hover:bg-red-700"
            } disabled:opacity-50`}
            onClick={submit}
            disabled={loading}
          >
            {loading
              ? "Submitting…"
              : mode === "approve"
              ? "Approve & Write to Ledger"
              : "Reject"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Row detail expander ───────────────────────────────────────────────────────

function QuarantineRow({
  record,
  onRefresh,
}: {
  record:    QuarantineRecord;
  onRefresh: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [dialog,   setDialog]   = useState<"approve" | "reject" | null>(null);

  const failures: string[] = (() => {
    try {
      const d = record.context_snapshot as { failures?: Array<{ code: string; message: string }> } | null;
      return d?.failures?.map((f) => `[${f.code}] ${f.message}`) ?? [record.rejection_detail ?? record.rejection_code];
    } catch {
      return [record.rejection_code];
    }
  })();

  return (
    <>
      <tr
        className="hover:bg-gray-50 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <td className="px-4 py-3 text-sm font-mono">{record.contract_id}</td>
        <td className="px-4 py-3 text-sm text-gray-600">{record.rejection_code}</td>
        <td className="px-4 py-3">
          <StatusBadge status={record.status} />
        </td>
        <td className="px-4 py-3 text-sm text-gray-500">
          {new Date(record.created_at).toLocaleString()}
        </td>
        <td className="px-4 py-3 text-sm text-gray-500">
          {new Date(record.sla_deadline) < new Date() && record.status === "pending" ? (
            <span className="text-red-600 font-medium">OVERDUE</span>
          ) : (
            new Date(record.sla_deadline).toLocaleString()
          )}
        </td>
        <td className="px-4 py-3 text-right">
          {record.status === "pending" && (
            <div className="flex gap-2 justify-end" onClick={(e) => e.stopPropagation()}>
              <button
                className="px-3 py-1 text-xs font-medium bg-green-100 text-green-800 rounded hover:bg-green-200"
                onClick={() => setDialog("approve")}
              >
                Approve
              </button>
              <button
                className="px-3 py-1 text-xs font-medium bg-red-100 text-red-800 rounded hover:bg-red-200"
                onClick={() => setDialog("reject")}
              >
                Reject
              </button>
            </div>
          )}
          {record.status !== "pending" && (
            <span className="text-xs text-gray-400">
              by {record.reviewed_by ?? "—"}
            </span>
          )}
        </td>
      </tr>

      {expanded && (
        <tr className="bg-gray-50 border-t border-gray-200">
          <td colSpan={6} className="px-4 py-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="font-medium text-gray-700 mb-1">Validation failures</p>
                <ul className="list-disc list-inside text-gray-600 space-y-0.5">
                  {failures.map((f, i) => (
                    <li key={i} className="font-mono text-xs">{f}</li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="font-medium text-gray-700 mb-1">Event details</p>
                <dl className="text-xs text-gray-600 space-y-1">
                  <div><dt className="inline font-medium">Event ID: </dt><dd className="inline font-mono">{record.event_id}</dd></div>
                  <div><dt className="inline font-medium">Event type: </dt><dd className="inline">{record.event_type}</dd></div>
                  <div><dt className="inline font-medium">Source: </dt><dd className="inline">{record.source_system}</dd></div>
                  {record.override_reason && (
                    <div><dt className="inline font-medium">Override reason: </dt><dd className="inline">{record.override_reason}</dd></div>
                  )}
                </dl>
              </div>
            </div>
          </td>
        </tr>
      )}

      {dialog && (
        <ActionDialog
          record={record}
          mode={dialog}
          onCancel={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            onRefresh();
          }}
        />
      )}
    </>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function QuarantinePage() {
  const [records,  setRecords]  = useState<QuarantineRecord[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState<string | null>(null);
  const [filter,   setFilter]   = useState<string>("pending");

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

  useEffect(() => { load(); }, [load]);

  const pendingCount = records.filter((r) => r.status === "pending").length;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Quarantine Queue</h1>
          <p className="text-sm text-gray-500 mt-1">
            Events that failed validation and require human review
          </p>
        </div>
        <div className="flex items-center gap-3">
          {pendingCount > 0 && (
            <span className="bg-yellow-100 text-yellow-800 text-sm font-medium px-3 py-1 rounded-full">
              {pendingCount} pending review
            </span>
          )}
          <select
            className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="">All</option>
          </select>
          <button
            className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            onClick={load}
            disabled={loading}
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          <strong>Error loading quarantine queue:</strong> {error}
          <br />
          <span className="text-xs text-red-500">
            Is the Dashboard API running? (./scripts/dev_start.sh)
          </span>
        </div>
      )}

      {!loading && !error && records.length === 0 && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-4xl mb-3">✓</p>
          <p className="text-lg font-medium">No {filter || ""} events in quarantine</p>
          <p className="text-sm mt-1">All events have been processed successfully</p>
        </div>
      )}

      {records.length > 0 && (
        <div className="bg-white shadow rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {["Contract ID", "Failure Code", "Status", "Quarantined At", "SLA Deadline", "Actions"].map(
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
                <QuarantineRow key={r.event_id} record={r} onRefresh={load} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
