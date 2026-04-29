"""
Smart Data Gateway — Party Portal Endpoints.

These endpoints are accessible ONLY by authenticated contract parties
(borrowers, lenders, lessees, lessors, dealers).  Each party sees only
contracts where they are listed in contracts.parties.

SDG Enforcement (Section 6.5 REQUIREMENTS):
  - Parties authenticate via POST /api/party/auth (entity_id + party_type)
  - Receive a signed JWT valid for 1 hour
  - All subsequent requests carry  Authorization: Bearer <token>
  - The gateway checks contracts.parties on every request to ensure the
    caller is a listed party on the requested contract

Blockchain Proof:
  - Every response includes fabric_tx_id — the Hyperledger Fabric tx ID
  - data_hash is the SHA-256 fingerprint of the record at write time
  - Any post-write alteration to the record would change the hash,
    making tampering detectable

Auditor note:
  - Auditors access the ops dashboard (read from PostgreSQL audit schema)
  - Auditors do NOT access the party portal or the ledger directly
  - On-chain records are for the parties only; audit trails are off-chain
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import jwt
from pydantic import BaseModel

from shared.config import get_settings

from ..middleware.party_auth import PartyContext, get_party_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/party", tags=["party-portal"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

_VALID_PARTY_TYPES = {
    "borrower", "lessee", "lender", "lessor",
    "dealer", "servicer", "insurer",
}


class AuthRequest(BaseModel):
    entity_id: str
    party_type: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    entity_id: str
    party_type: str
    name: str


class LedgerProof(BaseModel):
    fabric_tx_id: str | None = None
    data_hash: str | None = None
    written_at: str
    proof_token_jti: str | None = None
    verification_note: str = (
        "This transaction ID is permanently recorded on Hyperledger Fabric. "
        "The data_hash is a SHA-256 fingerprint of the record at the time it "
        "was written.  Any alteration to the record after writing would "
        "produce a different hash — proving the original terms are intact."
    )


class ContractSummary(BaseModel):
    contract_id: str
    party_role: str
    contract_type: str
    vehicle: str
    amount_financed: float | None = None
    monthly_payment: float | None = None
    term_months: int | None = None
    interest_rate: float | None = None
    origination_date: str | None = None
    current_state: str
    ledger_proof: LedgerProof
    written_at: str


class ContractRecord(BaseModel):
    record_id: str
    record_type: str
    payload: dict[str, Any]
    data_hash: str | None = None
    fabric_tx_id: str | None = None
    proof_token_jti: str | None = None
    written_at: str


class ContractDetail(BaseModel):
    contract_id: str
    party_role: str
    contract_type: str
    origination: dict[str, Any]
    current_state: str
    ledger_proof: LedgerProof
    history: list[ContractRecord]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    try:
        return dict(raw)
    except Exception:
        return {}


def _inner(payload: dict) -> dict:
    """Return the contract_data sub-dict if present, else the payload itself."""
    return payload.get("contract_data") or payload.get("los_contract") or payload


def _vehicle_str(payload: dict) -> str:
    d = _inner(payload)
    v = d.get("vehicle", {})
    parts = [
        str(v.get("year", "")),
        v.get("make", ""),
        v.get("model", ""),
        v.get("trim", ""),
    ]
    label = " ".join(p for p in parts if p).strip()
    return label or d.get("vin", "Unknown vehicle")


# ---------------------------------------------------------------------------
# POST /api/party/auth
# ---------------------------------------------------------------------------

@router.post("/auth", response_model=AuthResponse, summary="Authenticate a contract party")
async def party_auth(body: AuthRequest, request: Request) -> AuthResponse:
    """
    Authenticate a contract party and issue a JWT.

    Verification: entity_id must exist in contracts.parties with the
    claimed party_type.  In production this would be backed by SSO/OAuth;
    for the POC the entity_id is verified against the ledger directly.
    """
    settings = get_settings()
    pool = request.app.state.pool

    if body.party_type not in _VALID_PARTY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid party_type '{body.party_type}'. "
                   f"Must be one of: {', '.join(sorted(_VALID_PARTY_TYPES))}",
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT entity_id, party_role, metadata
            FROM   contracts.parties
            WHERE  entity_id  = $1
              AND  party_role = $2
            LIMIT  1
            """,
            body.entity_id,
            body.party_type,
        )

    if not row:
        raise HTTPException(
            status_code=401,
            detail=(
                f"No contract found for entity_id='{body.entity_id}' "
                f"with role='{body.party_type}'. "
                "Verify your entity ID from your contract documentation."
            ),
        )

    metadata = _parse_json(row["metadata"])
    name = metadata.get("name", body.entity_id)

    now = datetime.now(tz=timezone.utc)
    claims = {
        "sub":        body.entity_id,
        "party_type": body.party_type,
        "name":       name,
        "iat":        int(now.timestamp()),
        "exp":        int((now + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(claims, settings.dashboard_jwt_secret, algorithm="HS256")

    logger.info(
        "party_auth_issued",
        extra={"entity_id": body.entity_id, "party_type": body.party_type},
    )

    return AuthResponse(
        access_token=token,
        entity_id=body.entity_id,
        party_type=body.party_type,
        name=name,
    )


# ---------------------------------------------------------------------------
# GET /api/party/contracts
# ---------------------------------------------------------------------------

@router.get(
    "/contracts",
    response_model=list[ContractSummary],
    summary="List contracts for the authenticated party",
)
async def list_party_contracts(
    request: Request,
    ctx: PartyContext = Depends(get_party_context),
) -> list[ContractSummary]:
    """
    Return all contracts where the authenticated party is listed.

    SDG enforcement: only contracts whose contracts.parties table lists
    this entity_id are returned.  No cross-party data leakage.
    """
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                r.contract_id,
                r.payload,
                r.data_hash,
                r.fabric_tx_id,
                r.proof_token_jti,
                r.created_at::text     AS written_at,
                p.party_role,
                COALESCE(s.current_state, 'active') AS current_state
            FROM   contracts.records r
            JOIN   contracts.parties p
                     ON  p.contract_id = r.contract_id
                     AND p.entity_id   = $1
            LEFT JOIN contracts.state s
                     ON  s.contract_id = r.contract_id
            WHERE  r.record_type = 'origination'
            ORDER  BY r.created_at DESC
            """,
            ctx.entity_id,
        )

    results: list[ContractSummary] = []
    for row in rows:
        payload = _parse_json(row["payload"])
        inner = _inner(payload)
        fin = inner.get("financial_terms", {})
        results.append(ContractSummary(
            contract_id=row["contract_id"],
            party_role=row["party_role"],
            contract_type=inner.get("contract_type", payload.get("contract_type", "loan")),
            vehicle=_vehicle_str(payload),
            amount_financed=fin.get("amount_financed"),
            monthly_payment=fin.get("monthly_payment"),
            term_months=fin.get("term_months"),
            interest_rate=fin.get("interest_rate"),
            origination_date=inner.get("origination_date"),
            current_state=row["current_state"],
            ledger_proof=LedgerProof(
                fabric_tx_id=row["fabric_tx_id"],
                data_hash=row["data_hash"],
                written_at=row["written_at"],
                proof_token_jti=row["proof_token_jti"],
            ),
            written_at=row["written_at"],
        ))

    return results


# ---------------------------------------------------------------------------
# GET /api/party/contracts/{contract_id}
# ---------------------------------------------------------------------------

@router.get(
    "/contracts/{contract_id}",
    response_model=ContractDetail,
    summary="Get full contract detail for the authenticated party",
)
async def get_party_contract(
    contract_id: str,
    request: Request,
    ctx: PartyContext = Depends(get_party_context),
) -> ContractDetail:
    """
    Return full contract detail including all ledger records and blockchain proof.

    SDG enforcement: returns HTTP 403 if the authenticated party is not
    listed in contracts.parties for this contract.
    """
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        # ── SDG: verify this party is on this contract ────────────────────
        party_row = await conn.fetchrow(
            """
            SELECT party_role, metadata
            FROM   contracts.parties
            WHERE  contract_id = $1 AND entity_id = $2
            LIMIT  1
            """,
            contract_id,
            ctx.entity_id,
        )
        if not party_row:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Access denied.  You are not a party to contract "
                    f"'{contract_id}'.  The Smart Data Gateway only permits "
                    "access to contracts where you are a listed party."
                ),
            )

        # ── Origination record ────────────────────────────────────────────
        orig_row = await conn.fetchrow(
            """
            SELECT record_id::text, payload, data_hash, fabric_tx_id,
                   proof_token_jti, created_at::text AS written_at
            FROM   contracts.records
            WHERE  contract_id = $1 AND record_type = 'origination'
            ORDER  BY created_at ASC
            LIMIT  1
            """,
            contract_id,
        )
        if not orig_row:
            raise HTTPException(status_code=404, detail="Contract not found in ledger.")

        # ── Current state ─────────────────────────────────────────────────
        state_row = await conn.fetchrow(
            "SELECT current_state FROM contracts.state WHERE contract_id = $1",
            contract_id,
        )

        # ── Full history ──────────────────────────────────────────────────
        history_rows = await conn.fetch(
            """
            SELECT record_id::text, record_type, payload, data_hash,
                   fabric_tx_id, proof_token_jti, created_at::text AS written_at
            FROM   contracts.records
            WHERE  contract_id = $1
            ORDER  BY created_at ASC
            """,
            contract_id,
        )

    orig_payload = _parse_json(orig_row["payload"])
    orig_inner = _inner(orig_payload)

    history: list[ContractRecord] = [
        ContractRecord(
            record_id=h["record_id"],
            record_type=h["record_type"],
            payload=_parse_json(h["payload"]),
            data_hash=h["data_hash"],
            fabric_tx_id=h["fabric_tx_id"],
            proof_token_jti=h["proof_token_jti"],
            written_at=h["written_at"],
        )
        for h in history_rows
    ]

    return ContractDetail(
        contract_id=contract_id,
        party_role=party_row["party_role"],
        contract_type=orig_inner.get("contract_type", orig_payload.get("contract_type", "loan")),
        origination=orig_payload,
        current_state=(state_row["current_state"] if state_row else "active"),
        ledger_proof=LedgerProof(
            fabric_tx_id=orig_row["fabric_tx_id"],
            data_hash=orig_row["data_hash"],
            written_at=orig_row["written_at"],
            proof_token_jti=orig_row["proof_token_jti"],
        ),
        history=history,
    )
