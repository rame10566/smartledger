# SmartLedger — Demo Walkthrough

**Duration**: ~20 minutes
**Audience**: Stakeholders, architects, compliance, business
**Prerequisites**:
- Full stack running (`docker compose up -d`)
- Hyperledger Fabric network up (`docker compose -f infra/fabric/docker-compose.fabric.yml up -d`)
- Hyperledger Explorer up (`infra/fabric/scripts/start-explorer.sh`)
- Demo data seeded (`uv run python scripts/seed_demo.py --clean`)

---

## Opening (2 min)

**Key message**: "Systems change. Platforms evolve. Contracts persist."

SmartLedger is a validation-gated immutable ledger for auto/vehicle finance. It sits between originating systems (LOS, CRM, portals) and the accounting system (LLAS), ensuring every data change is validated, audited, and written to an immutable blockchain before it reaches LLAS.

**Architecture in one sentence**: An AI Agent orchestrates 13 source systems via MCP protocol, validates every event, writes immutable records to Hyperledger Fabric, and exposes two distinct frontends — an internal Governance Dashboard for ops staff and a Party Portal for the actual contract parties — backed by independent on-chain verification through Hyperledger Explorer.

---

## Scene 1: Contract Origination — Happy Path (3 min)

**Dashboard**: http://localhost:3000/contracts

1. **Show the contracts list** — 3 active contracts from the seed data (Toyota Camry, BMW 3 Series, Ford F-150)
2. **Click into a contract** (e.g., the Toyota Camry)
   - **Lifecycle view**: current state = `active`, record count, payment count
   - **State history**: `originated → active` transition with timestamp
   - **Audit trail**: every action logged — `ledger_written`, `state_transitioned`, actor = `smartledger-agent`
3. **Explain the flow**:
   > "Oracle LOS originated this contract. The event hit Redis Streams. Our AI Agent picked it up, validated it against business rules (VIN format, rate caps, credit tier eligibility via Rules Engine, rate calculation via Pricing Engine), obtained a single-use proof token from the Validation Engine, wrote the record to Hyperledger Fabric, transitioned the state, and created the LLAS account — all in under 5 seconds."

**Key callout**: "Every ledger write requires a cryptographic proof token. No token, no write. The token is single-use and expires in 60 seconds."

---

## Scene 2: Validation Failure — Unhappy Path (3 min)

**Dashboard**: http://localhost:3000/quarantine

1. **Show the quarantine queue** — at least 1 pending event (the 99.9% interest rate contract)
2. **Expand the quarantined record** — show:
   - Rejection code: `INVALID_INTEREST_RATE`
   - Failure details: "interest_rate must be between 0% and 36% APR (got 99.99)"
   - SLA deadline (24 hours from quarantine time)
   - Event metadata: source system, event type, event ID
3. **Emphasize the SDG boundary**:
   > "Notice there's no 'Approve' or 'Override' button. SmartLedger does NOT approve or correct data. This is the Smart Data Gateway boundary — the originating system (Oracle LOS in this case) must fix the interest rate and resend the event. SmartLedger only validates."

**Key callout**: "Quarantine is a read-only audit trail. The data correction happens at the source. This prevents any single system from bypassing validation rules."

---

## Scene 3: Customer Profile Update — Integration Layer (3 min)

**Dashboard**: http://localhost:3000/quarantine (filter: All statuses)

1. **Explain the integration layer**:
   > "When a customer calls to update their address, the CRM agent creates a Service Request. When it's completed, the data flows through the Integration System to LLAS. But SmartLedger intercepts at that boundary — it validates the change before LLAS is updated."

2. **Show the clean update** — the CRM address update that validated successfully
   - Visible in the contract's audit trail as a `customer_update` record

3. **Show the stale sync** — Oracle LOS tried to sync stale data
   - Quarantine status: `pending`, rejection code: `STALE_LOS_SYNC`
   > "The LOS had outdated data. SmartLedger detected the stale sync and quarantined it. The LOS must refresh its data and try again."

4. **Show the state-ineligible update** — payment update on a charged-off contract
   - Quarantine status: `pending`, rejection code: `CONTRACT_STATE_INELIGIBLE`
   > "You can't update payment info on a charged-off contract. SmartLedger enforces state eligibility rules."

---

## Scene 4: Conflict Detection & Resolution (3 min)

**Dashboard**: http://localhost:3000/conflicts

1. **Show the conflict pair** — CRM and Customer Portal both submitted address changes for the same contract
2. **Click to expand** — side-by-side comparison:
   - **Source A (CRM)**: New address from the call center agent
   - **Source B (Portal)**: New address from the customer's self-service update
   - **Current LLAS**: The existing address in LLAS
3. **Walk through resolution**:
   > "Two different source systems submitted competing updates to the same field. SmartLedger detected the conflict and quarantined both. Now the LLAS Admin reviews the side-by-side comparison and selects the authoritative value."

4. **Demonstrate resolution** (if live):
   - Select the winning value
   - Enter a reason: "CRM record verified against customer call on [date]"
   - Click Resolve
   - Show: conflict removed, audit trail updated, LLAS profile updated

**Key callout**: "Conflict resolution is the one place where a human makes a decision in SmartLedger — but even then, the selected value goes through validation before it's written to the ledger."

---

## Scene 5: Smart Data Gateway — PBAC (1 min)

**Dashboard**: Use the identity selector (top-right dropdown)

1. **Switch to "Borrower (James Carter)"** — show that the borrower can only see their own contracts
2. **Switch to "Auditor"** — show full read access across all contracts
3. **Switch back to "Admin"** — full access

> "Party-Based Access Control. The borrower sees only their contracts. The auditor sees everything but can't resolve conflicts. The admin has full operational access. Every access is logged."

---

## Scene 6: Party Portal — Smart Data Gateway (3 min)

**Portal**: http://localhost:3000/party

Up to this point the demo has been the *internal ops* view — admins, auditors, operators looking at their own dashboard. Now we show what an actual contract party (borrower or lender) sees.

1. **Open the Party Portal** — clean, distinct from the ops dashboard. A login form asking for entity ID and role.

2. **Demo the lender experience**:
   - Enter `SMARTLEDGER_FINANCE` and select `Lender / Capital Finance`
   - Click "Access My Contracts"
   - **Result**: a list of every contract this lender has originated. Each row shows the vehicle, financial terms, current state, and a green **"On-chain"** badge for contracts written with live Fabric writes.

3. **Click a contract to see the detail view**:
   - The hero element is the **Blockchain Proof** box (green): shows the Fabric `tx_id`, the SHA-256 `data_hash`, and the timestamp it was written to the ledger.
   - Below: full contract terms, vehicle details, and a collapsible ledger history showing every record with its tx_id and hash.
   - **Click "Copy" on the tx_id** — this is what the party would use to verify independently.

4. **Now demo the SDG enforcement** — sign out and try to log in as a borrower:
   - Enter a customer ID like `CUST-XXXXXX` (you can grab one from the lender view) and select `Borrower`
   - **Result**: the borrower sees only *their* contract — not anyone else's.
   - Try to access another contract by URL: `/party` will load the borrower's session, but a direct API call to a contract they don't own returns **403** with: *"You are not a party to contract '...'. The Smart Data Gateway only permits access to contracts where you are a listed party."*

**Key callout**: "This is the Smart Data Gateway in action. Every party has independent visibility into their own contracts, with cryptographic proof from the blockchain — but they cannot see anyone else's data. The originating system controls the data, the ledger holds the proof, and the gateway enforces the access boundary."

---

## Scene 7: Independent Verification — Hyperledger Explorer (2 min)

**Explorer**: http://localhost:8090
**Login**: `exploreradmin` / `exploreradminpw`

The Party Portal showed a tx_id. The question every auditor asks: *"How do I know SmartLedger isn't lying about that tx_id?"* This scene shows the answer.

1. **Copy a tx_id** from the Party Portal (any contract with the green On-chain badge)

2. **Open Hyperledger Explorer** at http://localhost:8090, log in
   - **Dashboard view**: shows total blocks, transactions, peers, chaincodes — independent of SmartLedger's API
   - **Channels**: `smartledger-channel`
   - **Chaincodes**: `smartledger-cc` (our contract) + `_lifecycle` (Fabric system)

3. **Search for the tx_id** in the Transactions tab
   - **Result**: the actual transaction on the chain — block number, block hash, previous-block hash (the hash chain), the chaincode that wrote it (`WriteRecord` on `smartledger-cc`), the read/write set with the exact record content

4. **Show the block view**: every Fabric block links to the previous block's hash. Tampering with any record would invalidate the entire chain from that point forward.

**Key callout**: "Hyperledger Fabric is permissioned — there is no public Etherscan. But each member organization can run its own copy of the ledger and verify independently. In production, Capital Finance Corp would be a Fabric org with their own peer node, and the consumer would have a lightweight identity. They would never have to trust SmartLedger; they could verify the chain directly."

---

## Scene 8: Reports (1 min)

**Dashboard**: http://localhost:3000/reports

1. **Generate a Portfolio Overview** — shows total contracts, total financed, average rate, by-state breakdown
2. **Generate a Quarantine Summary** — shows SLA compliance, failure codes, pending counts
3. **Export to CSV** — demonstrate data portability

---

## Architecture Recap (2 min)

Bring up the architecture slide or whiteboard:

```
Source Systems (13 simulators)
    ↓ events (Redis Streams)
AI Agent (orchestrator)
    ↓ MCP calls
Validation Engine → proof token → Ledger MCP → Hyperledger Fabric
    ↓                                                  ↓
Dashboard API ────────────►  Governance Dashboard      │
    ↓ (JWT)                  (internal ops)            │
Party Portal /party                                    │
    (borrower / lender)                                │
                              Hyperledger Explorer ◄───┘
                              (independent verification)
```

**Key architectural points**:
- **MCP protocol**: Every system interaction is a tool call. Auditable, replayable, typed.
- **Saga pattern**: Crash at any step → agent resumes from last checkpoint
- **Per-contract locks**: No two events for the same contract process simultaneously
- **Idempotency**: Same event processed twice → second one is skipped
- **Proof tokens**: Single-use JWT, 60-second expiry, prevents unauthorized writes
- **Hyperledger Fabric**: Permissioned blockchain — immutable, auditable, tamper-evident
- **Smart Data Gateway**: Two faces — the internal ops dashboard and the external Party Portal — both enforce party-based access at the API layer
- **Independent verification**: Every record carries a Fabric tx_id; parties verify on the chain directly via Hyperledger Explorer, not on faith

---

## Closing

> "This POC demonstrates that we can intercept every data change between source systems and LLAS, validate it against configurable business rules, write an immutable audit trail to blockchain, and provide operational visibility through a governance dashboard — all orchestrated by an AI Agent using the MCP protocol."

**What's next**: Production hardening (mTLS, Kafka, observability stack), then feature expansion as needed.

---

## Quick Reference — Demo Data

| Contract | Type | State | Vehicle |
|----------|------|-------|---------|
| ORC-2026-xxx | Loan $28,500 | Active | Toyota Camry |
| ORC-2026-xxx | Lease $15,200 | Active | BMW 3 Series |
| ORC-2026-xxx | Loan $44,800 | Active | Ford F-150 |
| ORC-2026-xxx | Loan $20,000 | Quarantined | 99.9% rate |

| Integration Scenario | Result |
|----------------------|--------|
| CRM address update | Validated, written |
| Portal payment update | Validated, written |
| CRM + Portal conflict | Both quarantined, awaiting resolution |
| Oracle LOS stale sync | Quarantined (STALE_LOS_SYNC) |
| Payment on charged-off | Quarantined (CONTRACT_STATE_INELIGIBLE) |

## Troubleshooting

```bash
# Reseed demo data (clean start)
uv run python scripts/seed_demo.py --clean

# Check all services running
docker ps

# View agent logs (real-time processing)
docker compose logs agent --follow

# Restart if needed
docker compose restart
```
