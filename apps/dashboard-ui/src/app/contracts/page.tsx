"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { listContracts, type ContractSummary } from "@/lib/api";

function StateChip({ state }: { state: string }) {
  const colours: Record<string, string> = {
    originated:      "bg-blue-100 text-blue-800",
    active:          "bg-green-100 text-green-800",
    delinquent:      "bg-yellow-100 text-yellow-800",
    paid_off:        "bg-gray-100 text-gray-600",
    charged_off:     "bg-red-100 text-red-800",
    in_repossession: "bg-red-200 text-red-900",
    title_released:  "bg-purple-100 text-purple-800",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colours[state] ?? "bg-gray-100 text-gray-600"}`}
    >
      {state.replace(/_/g, " ")}
    </span>
  );
}

export default function ContractsPage() {
  const [contracts, setContracts] = useState<ContractSummary[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    listContracts()
      .then(setContracts)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-2">Contracts</h1>
      <p className="text-sm text-gray-500 mb-6">
        Most recent contracts written to the immutable ledger
      </p>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      {loading && <p className="text-gray-500 text-sm">Loading…</p>}

      {!loading && contracts.length === 0 && (
        <p className="text-gray-400 text-sm">No contracts in the ledger yet.</p>
      )}

      {contracts.length > 0 && (
        <div className="bg-white shadow rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {["Contract ID", "State", "Records", "First Seen", "Last Updated"].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {contracts.map((c) => (
                <tr key={c.contract_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-sm">
                    <Link
                      href={`/contracts/${c.contract_id}`}
                      className="font-mono text-blue-600 hover:text-blue-800 hover:underline"
                    >
                      {c.contract_id}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <StateChip state={c.current_state} />
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">{c.record_count}</td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {new Date(c.first_seen).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {new Date(c.last_updated).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
