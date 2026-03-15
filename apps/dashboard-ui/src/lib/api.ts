/**
 * API client helpers — thin wrappers around fetch that talk to the
 * Dashboard API (proxied via Next.js rewrites → http://localhost:8000/api).
 */

const BASE = "/api";

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

export async function approveOverride(
  eventId:  string,
  reason:   string,
  reviewer: string,
): Promise<{ success: boolean; contract_id: string }> {
  return apiFetch(`/quarantine/${eventId}/approve`, {
    method: "POST",
    body:   JSON.stringify({ reason, reviewer }),
  });
}

export async function rejectQuarantine(
  eventId:  string,
  reason:   string,
  reviewer: string,
): Promise<{ success: boolean }> {
  return apiFetch(`/quarantine/${eventId}/reject`, {
    method: "POST",
    body:   JSON.stringify({ reason, reviewer }),
  });
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
