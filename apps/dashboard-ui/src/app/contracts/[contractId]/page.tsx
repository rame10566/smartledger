"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { getLifecycle, getAuditTrail, type Lifecycle, type AuditEntry } from "@/lib/api";

export default function ContractDetailPage() {
  const params                        = useParams<{ contractId: string }>();
  const contractId                    = params.contractId;
  const [lifecycle, setLifecycle]     = useState<Lifecycle | null>(null);
  const [audit,     setAudit]         = useState<AuditEntry[]>([]);
  const [loading,   setLoading]       = useState(true);
  const [error,     setError]         = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([getLifecycle(contractId), getAuditTrail(contractId)])
      .then(([lc, au]) => {
        setLifecycle(lc);
        setAudit(au);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [contractId]);

  return (
    <div>
      <Link href="/contracts" className="text-blue-600 text-sm hover:underline mb-4 inline-block">
        ← Back to Contracts
      </Link>

      <h1 className="text-2xl font-bold text-gray-900 mb-1 font-mono">{contractId}</h1>
      <p className="text-sm text-gray-500 mb-6">Contract lifecycle &amp; audit trail</p>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}
      {loading && <p className="text-gray-500 text-sm">Loading…</p>}

      {lifecycle && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
          {[
            { label: "Current State",   value: lifecycle.current_state.replace(/_/g, " ") },
            { label: "Total Records",   value: String(lifecycle.total_records) },
            { label: "Payments Made",   value: String(lifecycle.total_payments_made) },
            { label: "Amount Paid",     value: `$${Number(lifecycle.total_amount_paid ?? 0).toLocaleString()}` },
          ].map(({ label, value }) => (
            <div key={label} className="bg-white shadow rounded-lg p-4">
              <p className="text-xs text-gray-500 uppercase tracking-wider">{label}</p>
              <p className="text-2xl font-semibold text-gray-900 mt-1">{value}</p>
            </div>
          ))}
        </div>
      )}

      {lifecycle && (
        <section className="mb-8">
          <h2 className="text-lg font-semibold text-gray-800 mb-3">State History</h2>
          {lifecycle.state_history.length > 0 ? (
            <div className="flex items-center gap-2 flex-wrap">
              {lifecycle.state_history.map((s, i) => (
                <span key={i} className="flex items-center gap-1 text-sm">
                  {i > 0 && <span className="text-gray-400">→</span>}
                  <span className="font-medium text-gray-700">{s.state.replace(/_/g, " ")}</span>
                </span>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400">No state transitions yet.</p>
          )}
        </section>
      )}

      {!loading && (
        <section>
          <h2 className="text-lg font-semibold text-gray-800 mb-3">Audit Trail</h2>
          {audit.length > 0 ? (
            <div className="bg-white shadow rounded-lg overflow-hidden">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    {["Timestamp", "Action", "Actor", "Details"].map((h) => (
                      <th
                        key={h}
                        className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200 text-sm">
                  {audit.map((e, i) => (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                        {new Date(e.created_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs">{e.action}</td>
                      <td className="px-4 py-3 text-gray-600">{e.actor}</td>
                      <td className="px-4 py-3 text-gray-500 text-xs font-mono truncate max-w-xs">
                        {e.details ? JSON.stringify(e.details).slice(0, 120) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-gray-400">No audit entries yet.</p>
          )}
        </section>
      )}
    </div>
  );
}
