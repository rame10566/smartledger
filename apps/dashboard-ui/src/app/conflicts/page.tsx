"use client";

import { useState, useEffect, useCallback } from "react";
import {
  listConflicts,
  getConflict,
  resolveConflict,
  type ConflictSummary,
  type ConflictPair,
  type ConflictSide,
} from "@/lib/api";

// ── Helpers ────────────────────────────────────────────────────────────────────

function extractChanges(side: ConflictSide | null): Record<string, unknown> {
  if (!side?.original_payload) return {};
  const p = side.original_payload as Record<string, unknown>;
  return (p.changes as Record<string, unknown>) ?? {};
}

function extractFields(side: ConflictSide | null): string[] {
  return Object.keys(extractChanges(side));
}

// ── Side panel ────────────────────────────────────────────────────────────────

function SidePanel({
  label,
  side,
  isWinner,
  onSelect,
  disabled,
}: {
  label: string;
  side: ConflictSide | null;
  isWinner: boolean;
  onSelect: () => void;
  disabled: boolean;
}) {
  if (!side) return null;
  const changes = extractChanges(side);

  return (
    <div
      className={`rounded-lg border-2 p-4 flex flex-col gap-3 transition-colors ${
        isWinner
          ? "border-green-500 bg-green-50"
          : "border-gray-200 bg-white"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="font-semibold text-gray-800">{label}</span>
        <span className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded">
          {side.source_system}
        </span>
      </div>

      <div className="text-xs text-gray-500 font-mono">{side.event_id}</div>

      <div className="flex-1">
        <p className="text-xs font-medium text-gray-600 uppercase tracking-wide mb-1">
          Proposed Changes
        </p>
        {Object.keys(changes).length === 0 ? (
          <p className="text-xs text-gray-400 italic">No fields extracted</p>
        ) : (
          <dl className="space-y-1">
            {Object.entries(changes).map(([field, value]) => (
              <div key={field} className="text-xs">
                <dt className="inline font-medium text-gray-600">{field}: </dt>
                <dd className="inline text-gray-900">{String(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      <div className="text-xs text-gray-400">
        Submitted {new Date(side.created_at).toLocaleString()}
      </div>

      <button
        onClick={onSelect}
        disabled={disabled}
        className={`mt-1 w-full py-2 rounded text-sm font-medium transition-colors ${
          isWinner
            ? "bg-green-600 text-white hover:bg-green-700"
            : "bg-gray-100 text-gray-700 hover:bg-gray-200"
        } disabled:opacity-50 disabled:cursor-not-allowed`}
      >
        {isWinner ? "Selected as Authoritative" : "Select This Value"}
      </button>
    </div>
  );
}

// ── Resolve modal ─────────────────────────────────────────────────────────────

function ResolveModal({
  pair,
  winnerId,
  onConfirm,
  onCancel,
}: {
  pair: ConflictPair;
  winnerId: string;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const winner =
    pair.side_a?.event_id === winnerId ? pair.side_a : pair.side_b;

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md">
        <h3 className="text-lg font-semibold text-gray-900 mb-2">
          Confirm Resolution
        </h3>
        <p className="text-sm text-gray-600 mb-4">
          You are selecting{" "}
          <strong>{winner?.source_system ?? "unknown"}</strong> as the
          authoritative source for contract{" "}
          <span className="font-mono">{pair.contract_id}</span>.
        </p>

        <label className="block text-sm font-medium text-gray-700 mb-1">
          Reason for selection
        </label>
        <textarea
          className="w-full border border-gray-300 rounded p-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          rows={3}
          placeholder="e.g. CRM record verified against customer call on 2026-03-18"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />

        <div className="flex gap-3 mt-4">
          <button
            className="flex-1 py-2 rounded bg-gray-100 text-gray-700 text-sm hover:bg-gray-200"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="flex-1 py-2 rounded bg-green-600 text-white text-sm hover:bg-green-700 disabled:opacity-50"
            disabled={!reason.trim()}
            onClick={() => onConfirm(reason.trim())}
          >
            Resolve
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Conflict detail panel ─────────────────────────────────────────────────────

function ConflictDetail({
  conflictPairId,
  onResolved,
  onClose,
}: {
  conflictPairId: string;
  onResolved: () => void;
  onClose: () => void;
}) {
  const [pair, setPair] = useState<ConflictPair | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [resolving, setResolving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getConflict(conflictPairId)
      .then((data) => { if (!cancelled) { setPair(data); setLoading(false); } })
      .catch((e) => { if (!cancelled) { setError(String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [conflictPairId]);

  async function handleConfirm(reason: string) {
    if (!selectedId) return;
    setResolving(true);
    try {
      await resolveConflict(conflictPairId, selectedId, reason);
      setConfirming(false);
      onResolved();
    } catch (e) {
      setError(String(e));
      setResolving(false);
    }
  }

  const llasProfile = pair?.current_llas ?? {};
  const llasAddress = (llasProfile as Record<string, unknown>).address as Record<string, unknown> | undefined;
  const llasContact = (llasProfile as Record<string, unknown>).contact as Record<string, unknown> | undefined;

  return (
    <div className="mt-6 bg-white shadow rounded-xl p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-900">
          Conflict Detail — <span className="font-mono text-sm">{conflictPairId}</span>
        </h2>
        <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700">
          Close
        </button>
      </div>

      {loading && <p className="text-sm text-gray-500">Loading...</p>}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      {pair && !loading && (
        <>
          {/* Affected fields */}
          <div className="mb-4">
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
              Conflicting Fields:{" "}
            </span>
            {extractFields(pair.side_a).map((f) => (
              <span
                key={f}
                className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-800 mr-1"
              >
                {f}
              </span>
            ))}
          </div>

          {/* Side-by-side + current LLAS */}
          <div className="grid grid-cols-3 gap-4 mb-6">
            <SidePanel
              label="Source A"
              side={pair.side_a}
              isWinner={selectedId === pair.side_a?.event_id}
              onSelect={() => setSelectedId(pair.side_a?.event_id ?? null)}
              disabled={resolving}
            />
            <SidePanel
              label="Source B"
              side={pair.side_b}
              isWinner={selectedId === pair.side_b?.event_id}
              onSelect={() => setSelectedId(pair.side_b?.event_id ?? null)}
              disabled={resolving}
            />

            {/* Current LLAS state (read-only) */}
            <div className="rounded-lg border-2 border-blue-200 bg-blue-50 p-4 flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-gray-800">Current LLAS</span>
                <span className="text-xs text-blue-700 bg-blue-100 px-2 py-0.5 rounded">
                  system of record
                </span>
              </div>
              <div className="flex-1 text-xs">
                {llasAddress && (
                  <div className="mb-2">
                    <p className="font-medium text-gray-600 uppercase tracking-wide mb-1">Address</p>
                    {Object.entries(llasAddress).map(([k, v]) => (
                      <div key={k}>
                        <span className="font-medium text-gray-600">{k}: </span>
                        <span className="text-gray-900">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                )}
                {llasContact && (
                  <div>
                    <p className="font-medium text-gray-600 uppercase tracking-wide mb-1">Contact</p>
                    {Object.entries(llasContact).map(([k, v]) => (
                      <div key={k}>
                        <span className="font-medium text-gray-600">{k}: </span>
                        <span className="text-gray-900">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                )}
                {!llasAddress && !llasContact && (
                  <p className="text-gray-400 italic">LLAS profile not available</p>
                )}
              </div>
              <p className="text-xs text-blue-600 italic">
                Not changed until resolution is validated and written to ledger.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              className="px-4 py-2 bg-green-600 text-white text-sm rounded hover:bg-green-700 disabled:opacity-50"
              disabled={!selectedId || resolving}
              onClick={() => setConfirming(true)}
            >
              Resolve Conflict
            </button>
            {!selectedId && (
              <p className="text-sm text-gray-500">Select the authoritative value above</p>
            )}
          </div>

          {confirming && selectedId && (
            <ResolveModal
              pair={pair}
              winnerId={selectedId}
              onConfirm={handleConfirm}
              onCancel={() => setConfirming(false)}
            />
          )}
        </>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ConflictsPage() {
  const [conflicts, setConflicts] = useState<ConflictSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listConflicts();
      setConflicts(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Conflict Resolution</h1>
          <p className="text-sm text-gray-500 mt-1">
            Competing customer data updates from different source systems — LLAS Admin adjudicates
          </p>
        </div>
        <div className="flex items-center gap-3">
          {conflicts.length > 0 && (
            <span className="bg-orange-100 text-orange-800 text-sm font-medium px-3 py-1 rounded-full">
              {conflicts.length} pending
            </span>
          )}
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
          <strong>Error:</strong> {error}
          <br />
          <span className="text-xs text-red-500">
            Conflicts require Admin role. Check your identity selector.
          </span>
        </div>
      )}

      {!loading && !error && conflicts.length === 0 && !selected && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg font-medium">No active conflicts</p>
          <p className="text-sm mt-1">All competing updates have been resolved</p>
        </div>
      )}

      {conflicts.length > 0 && (
        <div className="bg-white shadow rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {["Contract ID", "Source A", "Source B", "Fields", "Detected At", ""].map((h) => (
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
              {conflicts.map((c) => (
                <tr
                  key={c.conflict_pair_id}
                  className={`hover:bg-gray-50 ${selected === c.conflict_pair_id ? "bg-yellow-50" : ""}`}
                >
                  <td className="px-4 py-3 text-sm font-mono">{c.contract_id}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{c.source_a}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{c.source_b}</td>
                  <td className="px-4 py-3 text-sm">
                    {(c.fields ?? []).map((f) => (
                      <span
                        key={f}
                        className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-800 mr-1"
                      >
                        {f}
                      </span>
                    ))}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {new Date(c.created_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <button
                      className="text-blue-600 hover:text-blue-800 font-medium text-xs"
                      onClick={() =>
                        setSelected((prev) =>
                          prev === c.conflict_pair_id ? null : c.conflict_pair_id
                        )
                      }
                    >
                      {selected === c.conflict_pair_id ? "Hide" : "Resolve"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <ConflictDetail
          conflictPairId={selected}
          onResolved={() => {
            setSelected(null);
            load();
          }}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
