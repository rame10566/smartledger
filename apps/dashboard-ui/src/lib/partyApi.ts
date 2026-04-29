/**
 * Smart Data Gateway — Party Portal API Client
 *
 * Uses Authorization: Bearer <jwt> (not the X-SmartLedger-Identity header
 * used by the ops dashboard).  JWT is obtained from POST /api/party/auth
 * and stored in localStorage under "smartledger_party_token".
 */

const BASE = "/api/party";
const TOKEN_KEY = "smartledger_party_token";

// ── Token management ──────────────────────────────────────────────────────

export function getPartyToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setPartyToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearPartyToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// ── Types ─────────────────────────────────────────────────────────────────

export interface AuthResult {
  access_token: string;
  token_type: string;
  expires_in: number;
  entity_id: string;
  party_type: string;
  name: string;
}

export interface LedgerProof {
  fabric_tx_id: string | null;
  data_hash: string | null;
  written_at: string;
  proof_token_jti: string | null;
  verification_note: string;
}

export interface ContractSummary {
  contract_id: string;
  party_role: string;
  contract_type: string;
  vehicle: string;
  amount_financed: number | null;
  monthly_payment: number | null;
  term_months: number | null;
  interest_rate: number | null;
  origination_date: string | null;
  current_state: string;
  ledger_proof: LedgerProof;
  written_at: string;
}

export interface ContractRecord {
  record_id: string;
  record_type: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: Record<string, any>;
  data_hash: string | null;
  fabric_tx_id: string | null;
  proof_token_jti: string | null;
  written_at: string;
}

export interface ContractDetail {
  contract_id: string;
  party_role: string;
  contract_type: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  origination: Record<string, any>;
  current_state: string;
  ledger_proof: LedgerProof;
  history: ContractRecord[];
}

// ── Core fetch helper ─────────────────────────────────────────────────────

async function partyFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getPartyToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.detail ?? `HTTP ${res.status}: ${res.statusText}`
    );
  }
  return res.json() as Promise<T>;
}

// ── API calls ─────────────────────────────────────────────────────────────

export async function authenticateParty(
  entity_id: string,
  party_type: string
): Promise<AuthResult> {
  return partyFetch<AuthResult>(`${BASE}/auth`, {
    method: "POST",
    body: JSON.stringify({ entity_id, party_type }),
  });
}

export async function listPartyContracts(): Promise<ContractSummary[]> {
  return partyFetch<ContractSummary[]>(`${BASE}/contracts`);
}

export async function getPartyContract(contractId: string): Promise<ContractDetail> {
  return partyFetch<ContractDetail>(`${BASE}/contracts/${encodeURIComponent(contractId)}`);
}
