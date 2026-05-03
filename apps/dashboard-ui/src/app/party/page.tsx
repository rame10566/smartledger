"use client";

/**
 * Smart Data Gateway — Party Portal
 *
 * Consumer-facing and lender-facing view of contract records on the
 * Hyperledger Fabric ledger.  Each party sees ONLY their own contracts.
 *
 * Auth flow:
 *   1.  Party enters entity_id + party_type and clicks "Access My Contract"
 *   2.  Backend verifies they exist in contracts.parties and issues a JWT
 *   3.  JWT stored in localStorage; used for all subsequent requests
 *   4.  Contracts list → Contract detail (with blockchain proof)
 */

import { useState, useEffect, useCallback } from "react";
import {
  authenticateParty,
  listPartyContracts,
  getPartyContract,
  setPartyToken,
  getPartyToken,
  clearPartyToken,
  type ContractSummary,
  type ContractDetail,
} from "@/lib/partyApi";

// ── Types ──────────────────────────────────────────────────────────────────

type View = "login" | "list" | "detail";

interface Session {
  entity_id: string;
  party_type: string;
  name: string;
}

// ── Small UI helpers ────────────────────────────────────────────────────────

const PARTY_TYPES = [
  { value: "borrower",  label: "Borrower (Auto Loan)" },
  { value: "lessee",   label: "Lessee (Auto Lease)" },
  { value: "lender",   label: "Lender / Capital Finance" },
  { value: "lessor",   label: "Lessor / Capital Finance" },
  { value: "dealer",   label: "Dealer" },
];

function StateBadge({ state }: { state: string }) {
  const colours: Record<string, string> = {
    originated:      "bg-blue-100 text-blue-800 border-blue-200",
    active:          "bg-emerald-100 text-emerald-800 border-emerald-200",
    delinquent:      "bg-amber-100 text-amber-800 border-amber-200",
    paid_off:        "bg-slate-100 text-slate-600 border-slate-200",
    charged_off:     "bg-red-100 text-red-800 border-red-200",
    in_repossession: "bg-red-200 text-red-900 border-red-300",
    title_released:  "bg-purple-100 text-purple-800 border-purple-200",
  };
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border
        ${colours[state] ?? "bg-slate-100 text-slate-600 border-slate-200"}`}
    >
      {state.replace(/_/g, " ")}
    </span>
  );
}

function RecordTypeBadge({ type }: { type: string }) {
  const colours: Record<string, string> = {
    origination:         "bg-blue-50 text-blue-700",
    state_transition:    "bg-purple-50 text-purple-700",
    payment_applied:     "bg-emerald-50 text-emerald-700",
    fee_assessed:        "bg-amber-50 text-amber-700",
    late_fee:            "bg-red-50 text-red-700",
    balance_adjustment:  "bg-indigo-50 text-indigo-700",
    payoff:              "bg-teal-50 text-teal-700",
    insurance_lapse_noted: "bg-orange-50 text-orange-700",
    customer_update:     "bg-slate-50 text-slate-700",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium
        ${colours[type] ?? "bg-slate-50 text-slate-700"}`}
    >
      {type.replace(/_/g, " ")}
    </span>
  );
}

function BlockchainProofBox({
  txId,
  dataHash,
  writtenAt,
}: {
  txId: string | null;
  dataHash: string | null;
  writtenAt: string;
}) {
  const [copied, setCopied] = useState(false);

  const copyTxId = () => {
    if (!txId) return;
    navigator.clipboard.writeText(txId).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5 space-y-3">
      <div className="flex items-center gap-2">
        <svg className="w-5 h-5 text-emerald-600 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
        <h3 className="text-sm font-semibold text-emerald-800">Blockchain Proof — Hyperledger Fabric</h3>
      </div>

      <p className="text-xs text-emerald-700 leading-relaxed">
        This contract is permanently recorded on a permissioned blockchain.
        The transaction ID below is your cryptographic proof that the
        contract terms have not been altered since they were written.
      </p>

      {txId ? (
        <div className="space-y-2">
          <div>
            <p className="text-xs font-medium text-emerald-700 mb-1">Transaction ID (Fabric tx_id)</p>
            <div className="flex items-center gap-2">
              <code className="text-xs font-mono bg-white border border-emerald-200 rounded px-2 py-1 flex-1 break-all text-slate-700">
                {txId}
              </code>
              <button
                onClick={copyTxId}
                className="text-xs text-emerald-700 hover:text-emerald-900 border border-emerald-300 rounded px-2 py-1 flex-shrink-0 hover:bg-emerald-100 transition-colors"
              >
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
          </div>
          {dataHash && (
            <div>
              <p className="text-xs font-medium text-emerald-700 mb-1">Data Hash (SHA-256)</p>
              <code className="text-xs font-mono bg-white border border-emerald-200 rounded px-2 py-1 block break-all text-slate-700">
                {dataHash}
              </code>
            </div>
          )}
          <p className="text-xs text-emerald-600">
            Written to ledger: {new Date(writtenAt).toLocaleString()}
          </p>
        </div>
      ) : (
        <p className="text-xs text-amber-700 font-medium">
          ⚠ No blockchain transaction ID — write guard was active when this record was created.
        </p>
      )}
    </div>
  );
}

// ── Login view ──────────────────────────────────────────────────────────────

function LoginView({ onLogin }: { onLogin: (s: Session) => void }) {
  const [entityId, setEntityId]   = useState("");
  const [partyType, setPartyType] = useState("borrower");
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!entityId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await authenticateParty(entityId.trim(), partyType);
      setPartyToken(result.access_token);
      onLogin({ entity_id: result.entity_id, party_type: result.party_type, name: result.name });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-[70vh] flex items-center justify-center">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 bg-blue-600 rounded-2xl mb-4">
            <svg className="w-8 h-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-slate-900">Contract Party Portal</h1>
          <p className="text-sm text-slate-500 mt-1">
            Access your contract records on the immutable ledger
          </p>
        </div>

        {/* Form */}
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 space-y-5">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Entity ID
              </label>
              <input
                type="text"
                value={entityId}
                onChange={(e) => setEntityId(e.target.value)}
                placeholder="e.g. CUST-A1B2C3 or SMARTLEDGER_FINANCE"
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                autoComplete="off"
                spellCheck={false}
              />
              <p className="text-xs text-slate-400 mt-1">
                Your entity ID is printed on your contract documentation.
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Your Role
              </label>
              <select
                value={partyType}
                onChange={(e) => setPartyType(e.target.value)}
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
              >
                {PARTY_TYPES.map((pt) => (
                  <option key={pt.value} value={pt.value}>{pt.label}</option>
                ))}
              </select>
            </div>

            {error && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-xs text-red-700">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !entityId.trim()}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white font-medium py-2.5 px-4 rounded-lg text-sm transition-colors"
            >
              {loading ? "Verifying…" : "Access My Contracts"}
            </button>
          </form>

          {/* Demo helper */}
          <div className="border-t border-slate-100 pt-4">
            <p className="text-xs font-medium text-slate-500 mb-2">Demo credentials</p>
            <div className="space-y-1.5 text-xs text-slate-500">
              <div className="flex items-center gap-2">
                <span className="font-mono bg-slate-50 border border-slate-200 rounded px-1.5 py-0.5 text-slate-700">
                  SMARTLEDGER_FINANCE
                </span>
                <span>→ role: lender (sees all contracts)</span>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono bg-slate-50 border border-slate-200 rounded px-1.5 py-0.5 text-slate-700">
                  CUST-&lt;id&gt;
                </span>
                <span>→ role: borrower (check Contracts page for IDs)</span>
              </div>
            </div>
          </div>
        </div>

        {/* SDG note */}
        <p className="text-xs text-slate-400 text-center mt-4">
          Smart Data Gateway enforces party-based access.
          You will only see contracts where you are a listed party.
        </p>
      </div>
    </div>
  );
}

// ── Contracts list view ─────────────────────────────────────────────────────

function ContractsListView({
  session,
  onSelect,
  onLogout,
}: {
  session: Session;
  onSelect: (id: string) => void;
  onLogout: () => void;
}) {
  const [contracts, setContracts] = useState<ContractSummary[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setContracts(await listPartyContracts());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const formatCurrency = (n: number | null) =>
    n == null ? "—" : `$${n.toLocaleString("en-US", { minimumFractionDigits: 2 })}`;

  return (
    <div>
      {/* Session bar */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">My Contracts</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Authenticated as{" "}
            <span className="font-medium text-slate-700">{session.name}</span>
            {" "}·{" "}
            <span className="capitalize text-blue-600 font-medium">{session.party_type}</span>
          </p>
        </div>
        <button
          onClick={onLogout}
          className="text-xs text-slate-500 hover:text-slate-700 border border-slate-200 rounded-lg px-3 py-1.5 hover:bg-slate-50 transition-colors"
        >
          Sign out
        </button>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      {loading && (
        <div className="text-slate-400 text-sm py-12 text-center">Loading contracts…</div>
      )}

      {!loading && contracts.length === 0 && (
        <div className="text-slate-400 text-sm py-12 text-center">
          No contracts found for entity ID &quot;{session.entity_id}&quot;.
        </div>
      )}

      {contracts.length > 0 && (
        <div className="space-y-3">
          {contracts.map((c) => (
            <button
              key={c.contract_id}
              onClick={() => onSelect(c.contract_id)}
              className="w-full text-left bg-white rounded-xl border border-slate-200 hover:border-blue-300 hover:shadow-sm transition-all p-5"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="font-mono text-sm font-semibold text-blue-600">
                      {c.contract_id}
                    </span>
                    <StateBadge state={c.current_state} />
                    <span className="text-xs text-slate-400 capitalize">{c.party_role}</span>
                  </div>
                  <p className="text-sm font-medium text-slate-800 mb-2">{c.vehicle}</p>
                  <div className="flex flex-wrap gap-4 text-xs text-slate-500">
                    <span>
                      <span className="font-medium text-slate-700">Amount financed:</span>{" "}
                      {formatCurrency(c.amount_financed)}
                    </span>
                    <span>
                      <span className="font-medium text-slate-700">Monthly:</span>{" "}
                      {formatCurrency(c.monthly_payment)}
                    </span>
                    <span>
                      <span className="font-medium text-slate-700">Term:</span>{" "}
                      {c.term_months ? `${c.term_months} mo` : "—"}
                    </span>
                    <span>
                      <span className="font-medium text-slate-700">Rate:</span>{" "}
                      {c.interest_rate != null ? `${c.interest_rate}%` : "—"}
                    </span>
                  </div>
                </div>
                {/* Blockchain badge */}
                <div className="flex-shrink-0 flex items-center gap-1.5 bg-emerald-50 border border-emerald-200 rounded-lg px-2.5 py-1.5">
                  <svg className="w-3.5 h-3.5 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                  <span className="text-xs font-medium text-emerald-700">On-chain</span>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Contract detail view ────────────────────────────────────────────────────

function ContractDetailView({
  contractId,
  session,
  onBack,
}: {
  contractId: string;
  session: Session;
  onBack: () => void;
}) {
  const [detail, setDetail]   = useState<ContractDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getPartyContract(contractId)
      .then(setDetail)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [contractId]);

  const formatCurrency = (n: unknown) =>
    typeof n === "number" ? `$${n.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : String(n ?? "—");

  if (loading) {
    return (
      <div className="text-slate-400 text-sm py-12 text-center">Loading contract…</div>
    );
  }
  if (error) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="text-sm text-blue-600 hover:underline">← Back</button>
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
      </div>
    );
  }
  if (!detail) return null;

  const o = detail.origination;
  // Data may be nested under contract_data (origination flow) or at top level
  const inner = o.contract_data ?? o.los_contract ?? o;
  const fin = inner.financial_terms ?? o.financial_terms ?? {};
  const vehicle = inner.vehicle ?? o.vehicle ?? {};
  const customer = inner.customer ?? o.customer ?? {};

  return (
    <div className="space-y-6">
      {/* Back + header */}
      <div>
        <button
          onClick={onBack}
          className="text-sm text-blue-600 hover:underline mb-3 flex items-center gap-1"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Back to my contracts
        </button>
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-xl font-bold text-slate-900 font-mono">{detail.contract_id}</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {vehicle.year} {vehicle.make} {vehicle.model} {vehicle.trim} ·{" "}
              <span className="capitalize">{detail.contract_type}</span> ·{" "}
              <span className="capitalize">{detail.party_role}</span>
            </p>
          </div>
          <StateBadge state={detail.current_state} />
        </div>
      </div>

      {/* Blockchain proof — hero section */}
      <BlockchainProofBox
        txId={detail.ledger_proof.fabric_tx_id}
        dataHash={detail.ledger_proof.data_hash}
        writtenAt={detail.ledger_proof.written_at}
      />

      {/* Contract terms */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-5 py-3 bg-slate-50 border-b border-slate-200">
          <h2 className="text-sm font-semibold text-slate-700">Contract Terms</h2>
        </div>
        <div className="p-5 grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-4 text-sm">
          {[
            ["Amount Financed",  formatCurrency(fin.amount_financed)],
            ["Monthly Payment",  formatCurrency(fin.monthly_payment)],
            ["Term",             fin.term_months ? `${fin.term_months} months` : "—"],
            ["Interest Rate",    fin.interest_rate != null ? `${fin.interest_rate}%` : "—"],
            ["Down Payment",     formatCurrency(fin.down_payment)],
            ["Origination Date", inner.origination_date ?? o.origination_date ?? "—"],
            ["LOS System",       inner.los_system ?? o.los_system ?? "—"],
            ["Dealer",           inner.dealer_id ?? o.dealer_id ?? "—"],
          ].map(([label, value]) => (
            <div key={label}>
              <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-0.5">{label}</p>
              <p className="text-slate-800 font-medium">{value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Vehicle details */}
      {Object.keys(vehicle).length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="px-5 py-3 bg-slate-50 border-b border-slate-200">
            <h2 className="text-sm font-semibold text-slate-700">Vehicle</h2>
          </div>
          <div className="p-5 grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-4 text-sm">
            {[
              ["VIN",   vehicle.vin   ?? o.vin ?? "—"],
              ["Make",  vehicle.make  ?? "—"],
              ["Model", vehicle.model ?? "—"],
              ["Year",  vehicle.year  ?? "—"],
              ["Trim",  vehicle.trim  ?? "—"],
              ["Color", vehicle.color ?? "—"],
            ].map(([label, value]) => (
              <div key={label}>
                <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-0.5">{label}</p>
                <p className="text-slate-800 font-medium">{String(value)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Customer summary — for lender view only (SDG: borrower sees their own data anyway) */}
      {(detail.party_role === "lender" || detail.party_role === "lessor") &&
        Object.keys(customer).length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="px-5 py-3 bg-slate-50 border-b border-slate-200">
            <h2 className="text-sm font-semibold text-slate-700">Borrower</h2>
          </div>
          <div className="p-5 grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-4 text-sm">
            {[
              ["Name",        `${customer.first_name ?? ""} ${customer.last_name ?? ""}`.trim() || "—"],
              ["Customer ID", customer.customer_id ?? "—"],
              ["Credit Tier", customer.credit_tier ?? "—"],
            ].map(([label, value]) => (
              <div key={label}>
                <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-0.5">{label}</p>
                <p className="text-slate-800 font-medium">{value}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Ledger history */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <button
          onClick={() => setShowHistory((v) => !v)}
          className="w-full px-5 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between hover:bg-slate-100 transition-colors"
        >
          <h2 className="text-sm font-semibold text-slate-700">
            Ledger History ({detail.history.length} record{detail.history.length !== 1 ? "s" : ""})
          </h2>
          <svg
            className={`w-4 h-4 text-slate-500 transition-transform ${showHistory ? "rotate-180" : ""}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {showHistory && (
          <div className="divide-y divide-slate-100">
            {detail.history.map((rec) => (
              <div key={rec.record_id} className="px-5 py-4">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <RecordTypeBadge type={rec.record_type} />
                  <span className="text-xs text-slate-400">
                    {new Date(rec.written_at).toLocaleString()}
                  </span>
                </div>
                {rec.fabric_tx_id && (
                  <p className="text-xs font-mono text-slate-500 truncate">
                    <span className="text-slate-400">tx:</span> {rec.fabric_tx_id}
                  </p>
                )}
                {rec.data_hash && (
                  <p className="text-xs font-mono text-slate-400 truncate">
                    <span className="text-slate-400">hash:</span> {rec.data_hash}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Root page component ─────────────────────────────────────────────────────

export default function PartyPortalPage() {
  const [view, setView]           = useState<View>("login");
  const [session, setSession]     = useState<Session | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Restore session from localStorage on mount
  useEffect(() => {
    const token = getPartyToken();
    if (!token) return;
    try {
      // Decode payload without verify (just to restore display name)
      const parts = token.split(".");
      if (parts.length !== 3) return;
      const payload = JSON.parse(atob(parts[1]));
      const exp = payload.exp as number;
      if (exp && Date.now() / 1000 > exp) {
        clearPartyToken();
        return;
      }
      setSession({ entity_id: payload.sub, party_type: payload.party_type, name: payload.name ?? payload.sub });
      setView("list");
    } catch {
      clearPartyToken();
    }
  }, []);

  const handleLogin = (s: Session) => {
    setSession(s);
    setView("list");
  };

  const handleLogout = () => {
    clearPartyToken();
    setSession(null);
    setSelectedId(null);
    setView("login");
  };

  const handleSelect = (id: string) => {
    setSelectedId(id);
    setView("detail");
  };

  const handleBack = () => {
    setSelectedId(null);
    setView("list");
  };

  return (
    <div>
      {view === "login" && <LoginView onLogin={handleLogin} />}

      {view === "list" && session && (
        <ContractsListView
          session={session}
          onSelect={handleSelect}
          onLogout={handleLogout}
        />
      )}

      {view === "detail" && session && selectedId && (
        <ContractDetailView
          contractId={selectedId}
          session={session}
          onBack={handleBack}
        />
      )}
    </div>
  );
}
