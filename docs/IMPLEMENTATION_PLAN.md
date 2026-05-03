# SmartLedger — Implementation Plan & Checklist

*Last updated: 2026-03-17 — Phases A–G complete.*

---

## Phase A — Foundation ✅ COMPLETE

- [x] JSON Schema registry — 15 files in `src/shared/schemas/` (common, events, records, entities, validation)
- [x] Pydantic v2 models — `src/shared/models/` (common, entities, records, validation, saga)
- [x] Shared config — `src/shared/config.py` (pydantic-settings, all env vars)
- [x] Shared logging — `src/shared/logging.py` (structlog, structured JSON)
- [x] `src/shared/pyproject.toml` — pydantic, pydantic-settings, structlog, pyjwt, jsonschema
- [x] PostgreSQL init SQL — `infra/docker/postgres/init.sql` (all 6 schemas, all tables, indexes)
- [x] `docker-compose.yml` — all services defined
- [x] Dockerfiles — all services
- [x] `pyproject.toml` — uv workspace (8 packages)
- [x] `.env.example` — all env vars documented
- [x] `.gitignore`
- [x] `scripts/setup.sh`
- [x] `README.md`
- [x] `docs/ARCHITECTURE.md`
- [x] `docs/PROJECT_OVERVIEW.md`
- [x] `docs/IMPLEMENTATION_PLAN.md` (this file)

---

## Phase B — First MCP Servers ✅ COMPLETE

### B1. Oracle LOS Simulator (`src/mcp_servers/simulated/oracle_los/`)

- [x] `server.py` — FastMCP server on port 8010
- [x] Tool: `originate_contract(contract_data)` → stores in-memory + publishes `contract.originated` to Redis Streams
- [x] Tool: `get_contract(contract_id)` → returns full contract from Oracle LOS
- [x] Tool: `get_contracts()` → list all contracts
- [x] Tool: `amend_contract(contract_id, amendments)` → update contract fields
- [x] Redis publisher — wraps event in `EventEnvelope`, publishes to `smartledger:events` stream
- [x] UUID-based contract IDs (restart-safe, no collisions)

### B2. LLAS Simulator (`src/mcp_servers/simulated/llas/`)

- [x] `server.py` — FastMCP server on port 8012
- [x] Tool: `get_account(account_id)` → full account details
- [x] Tool: `get_balance(account_id)` → current balance breakdown
- [x] Tool: `get_payment_history(account_id)` → list of payments
- [x] Tool: `create_account(contract_data)` → creates LLAS account. Invoked by `customer_update_flow` when handling an initial `integration.llas_sync_requested` (LOS seeding LLAS through the Integration System before publishing `contract.originated`). NOT called directly from the agent's origination flow — origination only validates that the account is already present (RULE-XSYS-LLAS-PRESENT).

### B3. Validation Engine MCP (`src/mcp_servers/validation/`)

- [x] `server.py` — FastMCP server on port 8001
- [x] JWT proof token issuance (HS256, 60s expiry, jti + claims)
- [x] Tool: `validate_event(event_envelope, saga_id, context)` → `ValidationResult`
- [x] Tool: `get_quarantined(contract_id?)` → list quarantine records (read-only)
- [x] Tool: `get_validation_rules(rule_type?)` → active rules
- [x] Tool: `update_rule(rule_id, config, updated_by)` → new rule version
- [x] Tool: `get_rule_history(rule_id)` → version history
- [x] Tool: `get_rejection_log(contract_id?)` → rejected events
- [x] Quarantine = read-only audit trail (no approve_override tool — SDG boundary)

### B4. Immutable Ledger MCP (`src/mcp_servers/ledger/`)

- [x] `server.py` — FastMCP server on port 8002
- [x] JWT proof token verification (signature, expiry, contract_id match, jti dedup)
- [x] Write guard — `WRITE_GUARD` env var; Phase 0 = PostgreSQL only, Phase 1 = Fabric
- [x] Tool: `write_record(record, proof_token)` → proof token gated write
- [x] Tool: `query_records(contract_id, record_type?)` → query `contracts.records`
- [x] Tool: `get_contract_lifecycle(contract_id)` → state history
- [x] Tool: `get_audit_trail(contract_id)` → query `audit.log`
- [x] Tool: `get_state(contract_id)` → current state
- [x] Tool: `execute_state_transition(contract_id, transition, data)` → update state
- [x] Tool: `calculate_late_fee`, `check_title_release`, `get_governance_rules`

---

## Phase C — Agent Core ✅ COMPLETE

### C1. Agent System Prompt
- [x] System prompt in `src/agent/main.py` — role, event types, flow routing, decision criteria

### C2. Per-Contract Distributed Locks (`src/agent/core/locks.py`)
- [x] `ContractLock` class — async context manager
- [x] Acquire: `SET contract:{id} NX PX 60000`; Release: Lua script (prevents hijacking)
- [x] `LockAcquisitionError` → event requeued with delay

### C3. Saga Checkpoints (`src/agent/core/saga.py`)
- [x] `SagaManager` — checkpoint, resume, complete/fail/quarantine terminal states
- [x] `load_incomplete_sagas()` — crash recovery on startup

### C4. Redis Streams Consumer (integrated in `src/agent/core/event_loop.py`)
- [x] XGROUP CREATE on startup, XREADGROUP poll, XACK after processing
- [x] Stale PEL reclaim → DLQ after max retries
- [x] EventEnvelope deserialization

### C5. Agent Event Loop (`src/agent/core/event_loop.py`)
- [x] `AgentEventLoop` — consume → idempotency → lock → dispatch → unlock → ACK
- [x] Flow dispatch by `event_type`
- [x] Graceful shutdown

### C6. Agent MCP Client (`src/agent/core/mcp_client.py`)
- [x] Connections to all MCP servers on startup
- [x] Tool call logging with saga_id + duration

### C7. Agent Entrypoint (`src/agent/main.py`)
- [x] Bootstrap: connect MCP servers → resume incomplete sagas → start event loop

---

## Phase D — Origination Happy Path ✅ COMPLETE

### D1. Origination Flow (`src/agent/flows/origination.py`)
- [x] Steps: context gather → validate → proof token → ledger write → state transition
- [x] Checkpoints: CONTEXT_GATHERED → VALIDATED → LEDGER_WRITTEN → COMPLETED
- [x] Unhappy path: invalid event → quarantine (no ledger write)
- [x] Proof token expired edge case — re-validate

### D2. E2E Tests
- [x] `tests/e2e/test_origination_happy_path.py` — SVAL-01 (valid lifecycle)
- [x] SVAL-04: Duplicate event — skipped via idempotency
- [x] SVAL-09: Missing required fields — schema rejection

---

## Phase E — Unhappy Path + Dashboard ✅ COMPLETE

> **SDG Validate-Only Boundary enforced:** Quarantine is a read-only audit trail. No approve/reject/override from the dashboard. The originating system must fix and resubmit.

### E1. Quarantine Flow (Validation MCP)
- [x] Validation failure → INSERT `validation.quarantine` with context snapshot + failures
- [x] SLA calculation — `sla_deadline = created_at + 24h`
- [x] Escalation level increments on SLA breach
- [x] No `quarantine.approved` event — originating system resubmits corrected event

### E2. Dashboard API (`src/dashboard_api/`)
- [x] `main.py` — FastAPI :8000, CORS, PostgreSQL pool
- [x] `routers/quarantine.py` — `GET /api/quarantine`, `GET /api/quarantine/{event_id}` (read-only)
- [x] `routers/contracts.py` — `GET /api/contracts`, lifecycle, audit, state
- [x] `routers/reports.py` — `GET /reports`, export
- [x] `middleware/` — PBAC (party-based access control), field-level filtering, access audit log
- [x] `mcp_clients.py` — local MCP client wrappers

### E3. Dashboard UI (`apps/dashboard-ui/`)
- [x] `/contracts` — paginated contract list with state chips
- [x] `/contracts/[id]` — lifecycle timeline, ledger records, audit trail
- [x] `/quarantine` — read-only audit trail (validation failures + context snapshot + SLA aging)
- [x] `/reports` — report generation, viewer, CSV/JSON export
- [x] Polling — quarantine list auto-refreshes every 10 seconds
- [x] `IdentitySelector` component — PBAC role demo

### E4. Tests
- [x] Unit tests for quarantine creation and SLA
- [x] `tests/e2e/test_origination_unhappy_path.py` — SVAL-02, SVAL-03, SVAL-06
- [x] Saga crash recovery tests (CONTEXT_GATHERED, VALIDATED, lock TTL expiry)

---

## Phase F — Remaining Flows + All Simulators ✅ COMPLETE

### F1. All 12 Simulators
- [x] `salesforce_los/server.py` — :8011
- [x] `crm/server.py` — :8013 (`get_customer`, `update_customer_notes`)
- [x] `payment/server.py` — :8014 (`post_payment`, `get_payment`, `get_payment_history`, `reverse_payment`)
- [x] `insurance/server.py` — :8015 (`quote_policy`, `get_policy_status`)
- [x] `dealer/server.py` — :8016 (`get_dealer`, `list_dealers`)
- [x] `customer_portal/server.py` — :8017 (`get_contract_summary`, `make_payment`, `dispute_charge`, etc.)
- [x] `mobile_app/server.py` — :8018 (`get_contract_summary`, `make_payment`, `get_notifications`, etc.)
- [x] `ivr/server.py` — :8019 (`check_payment_status`, `make_payment`, `get_balance`, etc.)
- [x] `rules_engine/server.py` — :8020 (`check_eligibility`, `calculate_credit_tier`, `get_tier_limits`, `list_rules`)
- [x] `pricing_engine/server.py` — :8021 (`calculate_rate`, `get_rate_card`, `validate_payment_calc`, `get_dealer_markup`)

### F2. Payment Flow (`src/agent/flows/payment.py`)
- [x] Handles `payment.received`, `customer.payment_submitted`, `ivr.payment_submitted`
- [x] Validates amount, contract state, idempotency; writes `AccountingRecord`
- [x] State transitions: DELINQUENT→ACTIVE, ACTIVE→PAID_OFF

### F3. Semantic AI MCP (`src/mcp_servers/semantic_ai/`)
- [x] `server.py` — FastMCP :8003
- [x] Claude API (claude-3-5-sonnet) extraction with per-field confidence scoring
- [x] Tools: `extract_contract_fields`, `get_extraction_confidence`, `submit_for_review`

### F4. PDF Ingestion Flow (`src/agent/flows/pdf_ingestion.py`)
- [x] Handles `dealer.pdf_submitted`; high confidence → validation; low confidence → quarantine

### F5. Reporting MCP (`src/mcp_servers/reporting/`)
- [x] `server.py` — FastMCP :8004
- [x] Tools: `generate_report`, `list_reports`, `get_report`, `export_report`
- [x] Report types: portfolio_overview, origination_summary, validation_summary, payment_summary

---

## Phase G — Full Stack (Hyperledger Fabric) ✅ COMPLETE

### G1. Fabric Network Setup (`infra/fabric/`)
- [x] `configtx.yaml` — channel + org definitions (single org for POC)
- [x] `crypto-config.yaml` — CA, peer, orderer cryptographic material
- [x] `docker-compose-fabric.yml` — orderer, peer, CA, CLI containers
- [x] `scripts/fabric-setup.sh` — channel creation, join peer, install chaincode

### G2. Chaincode (`apps/chaincode/src/`)
- [x] `SmartLedgerContract.ts` — `writeRecord`, `getRecord`, `executeStateTransition`, `calculateLateFee`, `checkTitleRelease`, `getGovernanceRules`
- [x] TypeScript build (`tsconfig.json`)

### G3. Ledger MCP — Fabric Integration
- [x] `fabric_client.py` — Fabric Gateway SDK integration
- [x] `write_record` — submits transaction to Fabric, stores `fabric_tx_id`
- [x] `WRITE_GUARD=false, PHASE=1` — live writes active
- [x] `contracts.records.fabric_tx_id` populated on all writes

### G4. Full Dashboard UI ✅
- [x] `/contracts` — paginated list with state badges
- [x] `/contracts/[id]` — lifecycle timeline, audit trail
- [x] `/quarantine` — read-only audit trail with SLA aging
- [x] `/reports` — report list, viewer, CSV/JSON export
- [x] PBAC `IdentitySelector` — party/role-based field visibility demo

---

## Phase H — Integration Layer + Customer Profile Flows ✅ COMPLETE

> Integration System as separate simulated MCP server. Source systems call it when pushing customer data to LLAS. SmartLedger intercepts, validates, and audits every change at this boundary.

### H1. Integration System Simulator (`src/mcp_servers/simulated/integration/server.py`) — Port 8022
- [x] FastMCP server on port 8022
- [x] Tool: `submit_contact_update(contract_id, source_system, changes, source_ref)` → publishes `integration.contact_update_requested`
- [x] Tool: `submit_payment_update(contract_id, source_system, changes, source_ref)` → publishes `integration.payment_update_requested`
- [x] Tool: `submit_insurance_update(contract_id, source_system, changes, source_ref)` → publishes `integration.insurance_update_requested`
- [x] Tool: `submit_llas_sync(contract_id, source_system, sync_payload)` → publishes `integration.llas_sync_requested`
- [x] Tool: `get_integration_status(integration_ref)` → returns pending / validated / quarantined / rejected
- [x] Basic format/syntax validation only (no business rules — by design)
- [x] Generates `integration_ref` UUID per submission

### H2. LLAS Simulator — Customer Profile State
- [x] Add in-memory `_CUSTOMER_PROFILES` store (seeded from origination data on startup)
- [x] Tool: `get_customer_profile(contract_id)` → `{address, contact, payment_info, insurance, last_updated_by, last_updated_at}`
- [x] Tool: `update_customer_profile(contract_id, changes, validated_by, source_system)` → updates in-memory profile
- [x] Tool: `get_payment_info(contract_id)` → `{method, bank_account_last4, routing_last4, payment_date}`
- [x] Profile seeded from origination contract data (address, contact from Oracle LOS)

### H3. CRM Simulator — Service Request Lifecycle
- [x] Tool: `create_service_request(contract_id, sr_type, requested_changes, customer_id)` → SR with reference (e.g. `SR-2026-0042`)
- [x] Tool: `get_service_request(sr_id)` → SR details + status
- [x] Tool: `complete_service_request(sr_id)` → calls Integration System MCP → returns `integration_ref`
- [x] Tool: `list_service_requests(contract_id?, status?)` → list SRs
- [x] SR types: `CONTACT_UPDATE`, `PAYMENT_UPDATE`, `INSURANCE_UPDATE`, `COBORROWER_UPDATE`

### H4. Portal + Mobile Simulators — Self-Service Updates
- [x] Portal: `update_contact_info(contract_id, changes)` → calls Integration System → returns `integration_ref`
- [x] Portal: `update_payment_method(contract_id, changes)` → calls Integration System → returns `integration_ref`
- [x] Mobile: same two tools as Portal

### H5. LOS Simulators — LLAS Sync
- [x] Oracle LOS: `sync_to_llas(contract_id)` → calls Integration System with current contract data
- [x] Salesforce LOS: `sync_to_llas(contract_id)` → calls Integration System with current contract data

### H6. Validation Engine — Customer Update Validator
- [x] New rule: `CONFLICT_PENDING` — same field has pending unresolved update from different source
- [x] New rule: `CONTRACT_STATE_INELIGIBLE` — contract state doesn't allow this change type
- [x] New rule: `STALE_LOS_SYNC` — LOS sync conflicts with more recent validated ledger record
- [x] New rule: `INVALID_PAYMENT_DATE` — payment date not between 1–28
- [x] New rule: `FIELD_VALUE_UNCHANGED` — proposed value identical to current LLAS profile (informational)
- [x] New tool: `resolve_conflict(conflict_pair_id, winning_event_id, admin_id, reason)` → validates + issues proof token + updates quarantine statuses + publishes `integration.conflict_resolved`
- [x] Conflict quarantine: `status='conflict'`, `conflict_pair_id` links both entries

### H7. New Agent Flow — `customer_update_flow.py`
- [x] Handles: `integration.contact_update_requested`, `integration.payment_update_requested`, `integration.insurance_update_requested`, `integration.llas_sync_requested`
- [x] Handles: `integration.conflict_resolved` (post-resolution write)
- [x] Steps: get LLAS profile → conflict check → validate → write ledger record → update LLAS profile
- [x] Conflict path: quarantine both events with `status='conflict'` and `conflict_pair_id`
- [x] Saga checkpoints: CONTEXT_GATHERED → VALIDATED → LEDGER_WRITTEN → COMPLETED / QUARANTINED_CONFLICT

### H8. New Ledger Record Type — `customer_update`
- [x] Fields: `contract_id`, `source_system`, `source_reference`, `integration_ref`, `change_type`, `field_changes [{field, old_value, new_value}]`, `conflict_pair_id`, `resolved_by`, `data_hash`
- [x] Add to schema registry: `src/shared/schemas/records/customer_update_record.json`
- [x] Add Pydantic model: `CustomerUpdateRecord` in `src/shared/models/records.py`

### H9. Dashboard API — Conflict Resolution Endpoints
- [x] `GET /api/conflicts` — list active conflicts (LLAS Admin role required via PBAC)
- [x] `GET /api/conflicts/{conflict_pair_id}` — both competing values + current LLAS profile
- [x] `POST /api/conflicts/{conflict_pair_id}/resolve` — calls `validation.resolve_conflict()`
- [x] Add `llas_admin` to PBAC role matrix

### H10. Dashboard UI — Conflicts View
- [x] `/conflicts` page — list of active conflict pairs (LLAS Admin only)
- [x] Conflict detail: side-by-side view of Source A vs Source B vs Current LLAS value
- [x] Source reference shown (SR number, session ID, timestamp)
- [x] Admin selects winning value + enters reason → calls resolve endpoint
- [x] On resolution: conflict removed from list, audit trail updated

### H11. Seed Script — Customer Update Scenarios
- [x] Scenario A: Clean CRM address update (SR created → completed → validates → ledger written)
- [x] Scenario B: Portal payment method update (self-service → validates → ledger written)
- [x] Scenario C: CRM + Portal concurrent address conflict → both quarantined with conflict status
- [x] Scenario D: Oracle LOS sync with stale data → STALE_LOS_SYNC quarantine
- [x] Scenario E: Payment update on charged-off contract → CONTRACT_STATE_INELIGIBLE quarantine

### H — Integration Tests
- [ ] Source system → Integration System MCP → Redis Stream event published
- [ ] Clean update flow: integration event → agent → validate → ledger write → LLAS profile updated
- [ ] Conflict flow: two conflicting events → both quarantined → admin resolves → ledger written
- [ ] SVAL-11 through SVAL-16 E2E scenarios

---

## Testing Checklist (Cross-Phase)

### Unit Tests
- [x] All validation rules (schema, cross-system, business, sequence, duplicate)
- [x] JWT proof token issuance + verification
- [x] Saga checkpoint write + resume
- [x] Redis lock acquire + release + TTL
- [x] Event envelope serialization/deserialization
- [x] Pydantic models — validation + serialization

### Integration Tests
- [x] Validation Engine + Ledger MCP (full token flow)
- [x] Oracle LOS → Redis Stream → Agent event loop
- [x] Quarantine creation → Dashboard API read-only view (no approve/reject flow)

### E2E Tests (SVAL Scenarios)
- [x] SVAL-01: Valid contract lifecycle (happy path)
- [x] SVAL-02: Payment amount mismatch → quarantine
- [x] SVAL-03: Balance mismatch → quarantine (no override — originating system resubmits)
- [x] SVAL-04: Duplicate event → skipped
- [ ] SVAL-05: Out-of-sequence event → rejected
- [x] SVAL-06: Oracle/Salesforce parity drift → quarantine
- [ ] SVAL-07: Insurance lapse mid-contract
- [ ] SVAL-08: Early payoff
- [x] SVAL-09: Missing required fields → schema rejection
- [x] SVAL-10: Override → **N/A — deleted** (SDG boundary: no overrides; quarantine is read-only)
- [ ] SVAL-11: CRM contact update (SR) → validates → customer_update record written
- [ ] SVAL-12: Portal payment method update → validates → written
- [ ] SVAL-13: CRM + Portal concurrent address conflict → both quarantined (conflict)
- [ ] SVAL-14: LOS sync with stale data → STALE_LOS_SYNC quarantine
- [ ] SVAL-15: Payment update on charged-off contract → CONTRACT_STATE_INELIGIBLE
- [ ] SVAL-16: LLAS Admin resolves conflict → authoritative value written to ledger

### Resilience Tests
- [x] Agent crash at each saga step → resume correctly
- [x] MCP server down → backoff → DLQ
- [x] Proof token expired during crash recovery → re-validates
- [x] Duplicate lock attempt → safe rejection

---

## Phase I — Smart Data Gateway Party Portal + Hyperledger Explorer

Closes the immutability story: parties (borrowers, lenders) gain
independent access to their contract records on the ledger, and the
Hyperledger Explorer provides a visual chain browser for verification.

### I.1 — Smart Data Gateway Party Portal (SDG Path A)

- [x] `src/dashboard_api/middleware/party_auth.py` — Bearer JWT dependency (`PartyContext`), HS256 signed with `dashboard_jwt_secret`, 1-hour expiry
- [x] `src/dashboard_api/routers/party.py`
  - [x] `POST /api/party/auth` — verifies `entity_id` + `party_type` against `contracts.parties`, issues signed JWT
  - [x] `GET /api/party/contracts` — lists contracts where caller is listed party (auto-filtered)
  - [x] `GET /api/party/contracts/{id}` — full contract detail with `fabric_tx_id` + `data_hash` as on-chain proof
  - [x] SDG enforcement: returns `403` if caller not on the contract's party list
- [x] `src/dashboard_api/main.py` — registered `party.router` at `/api`
- [x] `src/dashboard_api/pyproject.toml` — added `python-jose[cryptography]>=3.3`
- [x] `apps/dashboard-ui/src/lib/partyApi.ts` — Bearer-token client, localStorage session persistence
- [x] `apps/dashboard-ui/src/app/party/page.tsx` — three-state portal (login → contract list → detail) with prominent blockchain proof box (tx_id + data_hash + Copy button)
- [x] `apps/dashboard-ui/src/components/NavBar.tsx` — Party Portal nav link

### I.2 — Hyperledger Explorer (visual chain browser)

- [x] `infra/fabric/explorer/docker-compose.explorer.yml` — Explorer + explorer-db, joins `smartledger_fabric_net` as external
- [x] `infra/fabric/explorer/config.json` — top-level Explorer network registry
- [x] `infra/fabric/explorer/connection-profile/smartledger-network.json` — Fabric connection profile (`SmartLedgerOrgMSP`, peer endpoint, admin cert + key paths, TLS root)
- [x] `infra/fabric/scripts/start-explorer.sh` — verifies Fabric is running before bringing up Explorer
- [x] `.claude/launch.json` — added "Hyperledger Explorer" dev-server config

### I.3 — Documentation + visualization

- [x] `docs/architecture-diagrams.html` — standalone HTML page rendering all 10 Mermaid diagrams from `ARCHITECTURE.md`
- [x] System Overview diagram updated to include Party Portal + Explorer
- [x] Port Map updated (3000 `/party`, 8090 Explorer)

---

## Definition of Done (POC)

- [x] SVAL E2E scenarios implemented (SVAL-01/02/03/04/06/09; SVAL-10 N/A — deleted; SVAL-05/07/08/11-16 deferred)
- [x] Origination happy path runs end-to-end in < 5 seconds
- [x] `docker compose up -d` starts the full stack cleanly (15 services)
- [x] Dashboard shows contract lifecycle, quarantine audit trail, reports
- [x] Quarantine is read-only — SDG validate-only boundary enforced
- [x] Proof token appears on every ledger record
- [x] Agent correctly resumes after crash at any checkpoint
- [x] Smart Data Gateway (PBAC) — party-based access control enforced on internal ops dashboard
- [x] Smart Data Gateway — Party Portal (Path A) — parties query their own contracts via JWT-auth REST gateway, see `fabric_tx_id` + `data_hash` as on-chain proof
- [x] Hyperledger Explorer — independent visual verification of every transaction at http://localhost:8090
- [x] Hyperledger Fabric live writes (WRITE_GUARD=false, PHASE=1)
- [ ] No PII in `contracts.records` table (hashes only) — verify in production readiness pass
- [x] Integration System MCP intercepts all source→LLAS data changes
- [x] Conflict detection catches concurrent competing updates from different source systems
- [x] LLAS Admin conflict resolution writes to ledger with full audit trail
