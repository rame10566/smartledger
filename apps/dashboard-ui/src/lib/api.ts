/**
 * API client helpers — thin wrappers around fetch that talk to the
 * Dashboard API (proxied via Next.js rewrites → http://localhost:8000/api).
 *
 * Smart Data Gateway identity is sent via X-SmartLedger-Identity header
 * on every request (Section 6.5).
 */

const BASE = "/api";

// ── Identity Management (Smart Data Gateway — Section 6.5.2) ──────────────

export interface Identity {
  actor_id:         string;
  actor_type:       "user" | "service" | "agent";
  role?:            "admin" | "auditor" | "operator" | "compliance";
  party_entity_id?: string;
  party_role?:      "borrower" | "lessee" | "dealer" | "servicer" | "insurer";
  label:            string;  // human-readable display name
}

/** Pre-configured demo identities for the POC identity selector */
export const DEMO_IDENTITIES: Identity[] = [
  { actor_id: "admin-001",      actor_type: "user", role: "admin",      label: "Admin (full access)" },
  { actor_id: "auditor-001",    actor_type: "user", role: "auditor",    label: "Auditor (read-only, all contracts)" },
  { actor_id: "operator-001",   actor_type: "user", role: "operator",   label: "Operator (quarantine queue)" },
  { actor_id: "compliance-001", actor_type: "user", role: "compliance", label: "Compliance (full access)" },
  { actor_id: "borrower-CUST-001", actor_type: "user", party_entity_id: "CUST-001", party_role: "borrower", label: "Borrower (CUST-001)" },
  { actor_id: "dealer-DLR-042",    actor_type: "user", party_entity_id: "DLR-042",  party_role: "dealer",   label: "Dealer (DLR-042)" },
];

// Current identity — defaults to admin for backward compatibility
let _currentIdentity: Identity = DEMO_IDENTITIES[0];

export function getCurrentIdentity(): Identity {
  return _currentIdentity;
}

export function setCurrentIdentity(identity: Identity): void {
  _currentIdentity = identity;
}

function identityHeader(): Record<string, string> {
  const { label, ...payload } = _currentIdentity;
  return { "X-SmartLedger-Identity": JSON.stringify(payload) };
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface QuarantineRecord {
  event_id:         string;
  contract_id:      string;
  event_type:       string;
  source_system:    string;
  rejection_code:   string;
  rejection_detail: string | null;
  context_snapshot: Record<string, unknown> | null;
  original_payload: Record<string, unknown> | null;
  status:           "pending" | "approved" | "rejected" | "escalated";
  escalation_level: number;
  reviewed_by:      string | null;
  reviewed_at:      string | null;
  override_reason:  string | null;
  created_at:       string;
  sla_deadline:     string;
}

export interface ContractSummary {
  contract_id:      string;
  first_seen:       string;
  last_updated:     string;
  record_count:     number;
  current_state:    string;
  state_changed_at: string | null;
}

export interface LifecycleRecord {
  record_type: string;
  payload:     Record<string, unknown>;
  created_at:  string;
}

export interface Lifecycle {
  contract_id:         string;
  current_state:       string;
  total_records:       number;
  total_payments_made: number;
  total_amount_paid:   number;
  state_history:       Array<{ state: string; previous_state: string; changed_at: string }>;
  records:             LifecycleRecord[];
}

export interface AuditEntry {
  action:        string;
  actor:         string;
  event_id:      string | null;
  saga_id:       string | null;
  details:       Record<string, unknown> | null;
  created_at:    string;
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...identityHeader(),
      ...(options?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// ── Quarantine ────────────────────────────────────────────────────────────────

export async function listQuarantine(status?: string): Promise<QuarantineRecord[]> {
  const qs = status ? `?status=${status}` : "";
  return apiFetch<QuarantineRecord[]>(`/quarantine${qs}`);
}

export async function getQuarantineRecord(eventId: string): Promise<QuarantineRecord> {
  return apiFetch<QuarantineRecord>(`/quarantine/${eventId}`);
}

// ── Contracts ─────────────────────────────────────────────────────────────────

export async function listContracts(): Promise<ContractSummary[]> {
  return apiFetch<ContractSummary[]>("/contracts");
}

export async function getLifecycle(contractId: string): Promise<Lifecycle> {
  return apiFetch<Lifecycle>(`/contracts/${contractId}/lifecycle`);
}

export async function getAuditTrail(contractId: string): Promise<AuditEntry[]> {
  return apiFetch<AuditEntry[]>(`/contracts/${contractId}/audit`);
}

// ── Conflicts ─────────────────────────────────────────────────────────────────

export interface ConflictSide {
  event_id:         string;
  contract_id:      string;
  event_type:       string;
  source_system:    string;
  rejection_code:   string;
  rejection_detail: string | null;
  original_payload: Record<string, unknown> | null;
  status:           string;
  conflict_pair_id: string;
  created_at:       string;
}

export interface ConflictPair {
  conflict_pair_id: string;
  contract_id:      string;
  side_a:           ConflictSide | null;
  side_b:           ConflictSide | null;
  current_llas:     Record<string, unknown>;
}

export interface ConflictSummary {
  conflict_pair_id: string;
  contract_id:      string;
  source_a:         string;
  source_b:         string;
  fields:           string[];
  created_at:       string;
}

export async function listConflicts(contractId?: string): Promise<ConflictSummary[]> {
  const qs = contractId ? `?contract_id=${contractId}` : "";
  return apiFetch<ConflictSummary[]>(`/conflicts${qs}`);
}

export async function getConflict(conflictPairId: string): Promise<ConflictPair> {
  return apiFetch<ConflictPair>(`/conflicts/${conflictPairId}`);
}

export async function resolveConflict(
  conflictPairId:  string,
  winningEventId:  string,
  reason:          string,
): Promise<{ success: boolean; stream_entry_id?: string }> {
  return apiFetch(`/conflicts/${conflictPairId}/resolve`, {
    method: "POST",
    body:   JSON.stringify({ winning_event_id: winningEventId, reason }),
  });
}

// ── Reports ───────────────────────────────────────────────────────────────────

export interface ReportType {
  type:                 string;
  title:                string;
  description:          string;
  supports_date_filter: boolean;
}

export interface ReportSummary {
  report_id:    string;
  report_type:  string;
  title:        string;
  status:       "pending" | "completed" | "failed";
  requested_by: string | null;
  created_at:   string;
  completed_at: string | null;
}

export interface Report extends ReportSummary {
  parameters: Record<string, unknown>;
  result:     Record<string, unknown>;
}

export interface ReportExport {
  report_id:    string;
  format:       string;
  content_type: string;
  data:         string;
}

export async function listReportTypes(): Promise<ReportType[]> {
  return apiFetch<ReportType[]>("/reports/types");
}

export async function listReports(reportType?: string): Promise<ReportSummary[]> {
  const qs = reportType ? `?report_type=${reportType}` : "";
  return apiFetch<ReportSummary[]>(`/reports${qs}`);
}

export async function generateReport(
  reportType:   string,
  dateFrom?:    string,
  dateTo?:      string,
  requestedBy?: string,
): Promise<Report> {
  return apiFetch<Report>("/reports/generate", {
    method: "POST",
    body:   JSON.stringify({
      report_type:  reportType,
      date_from:    dateFrom,
      date_to:      dateTo,
      requested_by: requestedBy ?? "dashboard",
    }),
  });
}

export async function getReport(reportId: string): Promise<Report> {
  return apiFetch<Report>(`/reports/${reportId}`);
}

export async function exportReport(reportId: string, format = "json"): Promise<ReportExport> {
  return apiFetch<ReportExport>(`/reports/${reportId}/export?format=${format}`);
}
