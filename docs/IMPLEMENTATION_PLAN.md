# SmartLedger — Implementation Plan & Checklist

*All items ordered by dependency. Complete each phase before starting the next.*

---

## Phase A — Foundation ✅ COMPLETE

- [x] JSON Schema registry — 15 files in `src/shared/schemas/` (common, events, records, entities, validation)
- [x] Pydantic v2 models — `src/shared/models/` (common, entities, records, validation, saga)
- [x] Shared config — `src/shared/config.py` (pydantic-settings, all env vars)
- [x] Shared logging — `src/shared/logging.py` (structlog, structured JSON)
- [x] `src/shared/pyproject.toml` — pydantic, pydantic-settings, structlog, pyjwt, jsonschema
- [x] PostgreSQL init SQL — `infra/docker/postgres/init.sql` (all 6 schemas, all tables, indexes)
- [x] `docker-compose.yml` — all 11 services defined
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

## Phase B — First MCP Servers

> Build in order. Each can be started and tested independently before the agent exists.

### B1. Oracle LOS Simulator (`src/mcp_servers/simulated/oracle_los/`)

- [ ] `server.py` — FastMCP server on port 8010
- [ ] `data_generator.py` — generates realistic contract data (VIN, customer, financial terms)
- [ ] Tool: `originate_contract(contract_data)` → stores in-memory + publishes `contract.originated` to Redis Streams
- [ ] Tool: `get_contract(contract_id)` → returns full contract from Oracle LOS
- [ ] Tool: `get_pricing_output(contract_id)` → returns pricing/APR calculation
- [ ] Tool: `get_blaze_decision(contract_id)` → returns simulated Blaze rules output
- [ ] Tool: `list_events(filters)` → returns recent events
- [ ] Redis publisher — wraps event in `EventEnvelope`, publishes to `smartledger:events` stream
- [ ] Scenario support — happy path, mismatched VIN, missing fields, duplicate event
- [ ] Unit tests — `tests/unit/test_oracle_los.py`

### B2. LLAS Simulator (`src/mcp_servers/simulated/llas/`)

- [ ] `server.py` — FastMCP server on port 8012
- [ ] `data_store.py` — in-memory account store seeded from Oracle LOS contract IDs
- [ ] Tool: `get_account(account_id)` → full account details
- [ ] Tool: `get_balance(account_id)` → current balance breakdown (principal, interest, fees)
- [ ] Tool: `get_payment_history(account_id)` → list of payments
- [ ] Tool: `get_fees(account_id)` → outstanding fees
- [ ] Tool: `get_delinquency_status(account_id)` → days past due, status
- [ ] Scenario support — matching account (happy path), balance mismatch, delinquent account
- [ ] Unit tests — `tests/unit/test_llas.py`

### B3. Validation Engine MCP (`src/mcp_servers/validation/`)

- [ ] `server.py` — FastMCP server on port 8001
- [ ] `database.py` — asyncpg pool setup, query helpers
- [ ] `rules.py` — load + cache validation rules from PostgreSQL
- [ ] `validators/schema_validator.py` — validates payload against JSON Schema registry
- [ ] `validators/cross_system_validator.py` — compares context fields (VIN match, amount match, etc.)
- [ ] `validators/business_validator.py` — business rules (APR limits, term limits, etc.)
- [ ] `validators/sequence_validator.py` — checks contract not in wrong state
- [ ] `validators/duplicate_validator.py` — checks event_id not already processed
- [ ] `token.py` — JWT proof token issuance (HS256, 60s expiry, jti + claims)
- [ ] Tool: `validate_event(event_envelope, saga_id, context)` → `ValidationResult`
- [ ] Tool: `get_quarantined(contract_id?)` → list quarantine records
- [ ] Tool: `approve_override(event_id, reason, reviewer)` → update status + publish `quarantine.approved`
- [ ] Tool: `get_validation_rules(rule_type?)` → active rules
- [ ] Tool: `update_rule(rule_id, config, updated_by)` → new rule version
- [ ] Tool: `get_rule_history(rule_id)` → version history
- [ ] Tool: `get_rejection_log(contract_id?)` → rejected events
- [ ] Seed default validation rules into PostgreSQL on startup
- [ ] Unit tests — `tests/unit/test_validation.py`
- [ ] Unit tests — proof token issuance + verification

### B4. Immutable Ledger MCP (`src/mcp_servers/ledger/`)

- [ ] `server.py` — FastMCP server on port 8002
- [ ] `database.py` — asyncpg pool, query helpers
- [ ] `token_verifier.py` — JWT proof token verification (signature, expiry, contract_id match, jti dedup)
- [ ] `write_guard.py` — reads `WRITE_GUARD` env var; intercepts write_record in Phase 0
- [ ] Tool: `write_record(record, proof_token)` → verify token → write to PostgreSQL (Phase 0) or Fabric (Phase 1)
- [ ] Tool: `query_records(contract_id, record_type?)` → query `contracts.records`
- [ ] Tool: `get_contract_lifecycle(contract_id)` → state history from `contracts.state` + records
- [ ] Tool: `get_audit_trail(contract_id)` → query `audit.log`
- [ ] Tool: `get_state(contract_id)` → current state from `contracts.state`
- [ ] Tool: `execute_state_transition(contract_id, transition, data)` → update `contracts.state` (calls chaincode in Phase 1)
- [ ] Tool: `calculate_late_fee(contract_id, days_past_due)` → fee calc (hardcoded rules in Phase 0)
- [ ] Tool: `check_title_release(contract_id)` → eligibility check
- [ ] Tool: `get_governance_rules()` → governance rules
- [ ] Write guard logging — log what WOULD be written in Phase 0
- [ ] Unit tests — `tests/unit/test_ledger.py`
- [ ] Unit tests — proof token verification edge cases (expired, wrong contract, replayed jti)

### B — Integration Tests

- [ ] `tests/integration/test_validation_ledger.py` — full validation → proof token → ledger write flow (no agent)
- [ ] `tests/integration/test_oracle_los_events.py` — Oracle LOS publishes event, verify it appears on Redis stream

---

## Phase C — Agent Core

> Build the agent infrastructure before wiring up any flow.

### C1. Agent System Prompt

- [ ] Draft `src/agent/prompt.py` — system prompt defining:
  - What SmartLedger is and agent's role
  - Event types and which flow each triggers
  - All MCP tools available and when to use them
  - Decision criteria: when to quarantine vs proceed
  - How to structure reasoning for audit trail
  - Phase-aware behavior (read_only vs active)

### C2. Per-Contract Distributed Locks (`src/agent/core/locks.py`)

- [ ] `ContractLock` class — async context manager
- [ ] Acquire: `SET contract:{id} {saga_id} NX PX 60000`
- [ ] Release: `DEL contract:{id}` (only if value matches saga_id — prevent releasing another saga's lock)
- [ ] `LockAcquisitionError` — raised when lock unavailable → event requeued with delay
- [ ] Unit tests — concurrent lock attempts, TTL expiry, safe release

### C3. Saga Checkpoints (`src/agent/core/saga.py`)

- [ ] `SagaManager` class — async context manager
- [ ] `checkpoint(step, payload)` — INSERT/UPDATE `sagas.checkpoints`
- [ ] `load_incomplete_sagas()` — query all in_progress sagas on agent startup
- [ ] `resume(saga_id)` — returns last checkpoint + payload for resumption
- [ ] `complete()` / `fail()` / `quarantine()` — terminal state updates
- [ ] Unit tests — checkpoint, resume, concurrent saga isolation

### C4. Redis Streams Consumer (`src/event_bus/consumer.py`)

- [ ] `EventConsumer` class
- [ ] `XGROUP CREATE` — create consumer group on startup (if not exists)
- [ ] `XREADGROUP` — poll for new events with `>` (undelivered to this group)
- [ ] `XACK` — acknowledge after successful processing
- [ ] `XPENDING` — reclaim stale messages (PEL entries older than 5 min → DLQ)
- [ ] Dead Letter Queue — move failed events to `smartledger:dlq` stream after max retries
- [ ] Deserialization — parse raw stream entry into `EventEnvelope`
- [ ] Unit tests — consume, ack, nack, DLQ routing

### C5. Agent Event Loop (`src/agent/core/event_loop.py`)

- [ ] `AgentEventLoop` class
- [ ] Startup: load incomplete sagas + resume any in-progress flows
- [ ] Main loop: `consume event → check idempotency → acquire lock → dispatch flow → release lock → ack`
- [ ] Idempotency check — query `sagas.processed_events`
- [ ] Flow dispatch — route `event_type` to correct flow handler
- [ ] Error handling — MCP server down → exponential backoff → DLQ after N retries
- [ ] Graceful shutdown — finish current event before stopping

### C6. Agent MCP Client (`src/agent/core/mcp_client.py`)

- [ ] MCP client connections — connect to all MCP servers on startup
- [ ] Retry wrapper — auto-retry failed MCP calls with backoff
- [ ] Tool call logging — every call logged with saga_id, duration, result

### C7. Agent Entrypoint (`src/agent/main.py`)

- [ ] Wire up: event loop + MCP clients + system prompt
- [ ] Startup sequence: connect MCP servers → resume incomplete sagas → start event loop
- [ ] Unit tests — startup sequence, shutdown

---

## Phase D — Origination Happy Path (First Vertical Slice)

> First full end-to-end flow. Run this and watch a contract go from event to ledger record.

### D1. Origination Flow (`src/agent/flows/origination.py`)

- [ ] `OriginationFlow` class
- [ ] Step 1: Extract contract_id from event payload
- [ ] Step 2: `oracle_los.get_contract(id)` — gather Oracle LOS data
- [ ] Step 3: `llas.get_account(id)` — gather LLAS account data
- [ ] Step 4: Checkpoint `CONTEXT_GATHERED`
- [ ] Step 5: `validation.validate_event(event, context)` → `ValidationResult`
- [ ] Step 6: Checkpoint `VALIDATED`
- [ ] Step 7 (happy path): `ledger.write_record(origination_record, proof_token)`
- [ ] Step 8: Checkpoint `LEDGER_WRITTEN`
- [ ] Step 9: `ledger.execute_state_transition(contract_id, "ORIGINATED→ACTIVE")`
- [ ] Step 10: Checkpoint `COMPLETED`
- [ ] Proof token expired edge case — re-validate if token expired before write

### D2. E2E Happy Path Tests

- [ ] `tests/e2e/test_origination_happy_path.py`
  - [ ] SVAL-01: Valid contract lifecycle — event → agent → validation → ledger ✅
  - [ ] Verify: `contracts.records` has entry with correct `proof_token_jti`
  - [ ] Verify: `contracts.state` shows `ACTIVE`
  - [ ] Verify: `sagas.checkpoints` shows all steps COMPLETED
  - [ ] Verify: `sagas.processed_events` has `event_id` (idempotency)
- [ ] SVAL-04: Duplicate event — second send of same event_id is skipped ✅
- [ ] SVAL-09: Missing required fields — rejected at schema validation ✅
- [ ] Saga resume test — kill agent after `VALIDATED`, restart, verify resume from correct step

---

## Phase E — Unhappy Path + Human-in-the-Loop

> Quarantine flow, Dashboard API, Dashboard UI. Includes test, validate, and correct at every step.

### E1. Quarantine Flow (Validation MCP)

- [ ] `validation.quarantine_event(event, failures, context)` — INSERT `validation.quarantine`
- [ ] SLA calculation — `sla_deadline = created_at + 24h`
- [ ] Publish `quarantine.pending` to Redis Streams
- [ ] Agent picks up `quarantine.approved` → resumes saga with override flag
- [ ] Unit tests — quarantine creation, SLA calculation

### E2. Dashboard API (`src/dashboard_api/`)

- [ ] `main.py` — FastAPI app setup
- [ ] `routers/quarantine.py`
  - [ ] `GET /api/quarantine` — list pending quarantine items (sorted by SLA)
  - [ ] `GET /api/quarantine/{event_id}` — single quarantine record with full context
  - [ ] `POST /api/quarantine/{event_id}/approve` — calls Validation MCP `approve_override`
  - [ ] `POST /api/quarantine/{event_id}/reject` — marks rejected
- [ ] `routers/contracts.py`
  - [ ] `GET /api/contracts/{contract_id}/lifecycle` — calls Ledger MCP
  - [ ] `GET /api/contracts/{contract_id}/audit` — full audit trail
- [ ] `routers/health.py` — `GET /health`
- [ ] Integration tests — `tests/integration/test_dashboard_api.py`

### E3. Dashboard UI (`apps/dashboard-ui/`)

- [ ] Next.js app setup — `src/app/` structure
- [ ] `/` — home → redirect to `/contracts` or `/quarantine`
- [ ] `/quarantine` — validation queue
  - [ ] List view: event_id, contract_id, rejection reason, age, SLA countdown
  - [ ] Sort by SLA deadline (oldest first)
  - [ ] Filter by status (pending / all)
- [ ] `/quarantine/[event_id]` — quarantine detail
  - [ ] Show: original event payload
  - [ ] Show: cross-system context (Oracle data vs LLAS data)
  - [ ] Show: validation failures (code, message, expected vs actual)
  - [ ] Action: Approve Override (with reason input)
  - [ ] Action: Reject (with reason input)
- [ ] `/contracts/[contract_id]` — contract detail
  - [ ] State timeline
  - [ ] Ledger records list
  - [ ] Audit trail
- [ ] API client — typed fetch functions calling Dashboard API
- [ ] Polling — refresh quarantine list every 10 seconds
- [ ] Tailwind CSS styling

### E4. E2E Unhappy Path Tests

- [ ] `tests/e2e/test_origination_unhappy_path.py`
  - [ ] SVAL-02: Payment mismatch → quarantine → Dashboard shows it
  - [ ] SVAL-03: Balance mismatch → quarantine → human approves → written with override flag
  - [ ] SVAL-06: Oracle/Salesforce parity drift → quarantine
  - [ ] SVAL-10: Override flow — quarantine → approve → agent retries → ledger write
  - [ ] Reject flow — quarantine → reject → permanently discarded
  - [ ] SLA escalation — quarantine older than 24h → escalation_level increments
- [ ] Saga crash recovery tests
  - [ ] Kill agent after `CONTEXT_GATHERED`, restart → resumes from `CONTEXT_GATHERED`
  - [ ] Kill agent after `VALIDATED`, restart → proof token still valid → resumes
  - [ ] Kill agent after `VALIDATED`, restart after 60s → proof token expired → re-validates
  - [ ] Kill agent while holding lock → lock TTL expires → next restart acquires lock

### E5. Correction Steps (Unhappy Path)

- [ ] After writing with override: verify `audit.log` contains `override=true` + reviewer + reason
- [ ] Verify on-chain record has `proof_token_jti` matching the override proof token
- [ ] Verify `contracts.records.record_type` correctly set for overridden records

---

## Phase F — Remaining Flows + All Simulators

### F1. Remaining Simulators

- [ ] `salesforce_los/server.py` — port 8011 (same tools as Oracle LOS, slightly different field names)
- [ ] `crm/server.py` — port 8013 (`get_customer`, `get_risk_indicators`)
- [ ] `payment/server.py` — port 8014 (`get_payment`, `list_payments`, `get_settlement`)
- [ ] `insurance/server.py` — port 8015 (`get_policy_status`, `verify_insurance`, `list_events`)
- [ ] `dealer/server.py` — port 8016 (`get_submission`, `list_submissions`)
- [ ] `customer_portal/server.py` — port 8017 (`get_account_summary`, `submit_payment`, etc.)
- [ ] `mobile_app/server.py` — port 8018 (same tools as customer_portal)
- [ ] `ivr/server.py` — port 8019 (`get_balance_due`, `submit_phone_payment`, etc.)

### F2. Payment Flow (`src/agent/flows/payment.py`)

- [ ] Handle `payment.received`, `customer.payment_submitted`, `ivr.payment_submitted`
- [ ] Gather: `payment.get_payment`, `ledger.get_state`, `llas.get_balance`
- [ ] Validate: amount match, contract active, not duplicate
- [ ] Write: `AccountingRecord` with `record_type=payment_applied`
- [ ] State transition: check if `DELINQUENT→ACTIVE` or `ACTIVE→PAID_OFF`
- [ ] E2E tests: SVAL-07, SVAL-08

### F3. Semantic AI MCP (`src/mcp_servers/semantic_ai/`)

- [ ] `server.py` — FastMCP on port 8003
- [ ] `extractor.py` — calls Claude API (claude-3-5-sonnet) with PDF content + extraction prompt
- [ ] `confidence.py` — per-field confidence scoring
- [ ] Tool: `extract_contract_fields(file_reference)` → structured JSON + confidence scores
- [ ] Tool: `get_extraction_confidence(extraction_id)` → confidence breakdown
- [ ] Tool: `submit_for_review(extraction_id, discrepancies)` → INSERT `extraction.results`
- [ ] Store results in `extraction.results` PostgreSQL table

### F4. PDF Ingestion Flow (`src/agent/flows/pdf_ingestion.py`)

- [ ] Handle `dealer.pdf_submitted`
- [ ] Call `semantic_ai.extract_contract_fields(file_reference)`
- [ ] Compare extracted fields vs `oracle_los.get_contract(id)`
- [ ] High confidence + match → proceed to origination validation
- [ ] Low confidence → `submit_for_review` → quarantine with `LOW_CONFIDENCE` code
- [ ] Discrepancy → quarantine with field-level diff
- [ ] E2E tests — high confidence match, low confidence, field mismatch

### F5. Reporting MCP (`src/mcp_servers/reporting/`)

- [ ] `server.py` — FastMCP on port 8004
- [ ] Tool: `generate_report(type, parameters)` → queries Ledger MCP + PostgreSQL
- [ ] Tool: `list_reports()` → query `reports.generated`
- [ ] Tool: `get_report(report_id)` → return report data
- [ ] Tool: `export_report(report_id, format)` → CSV or PDF export
- [ ] Report type: origination summary (contract count, total volume, validation pass/fail rate)
- [ ] Dashboard UI: `/reports` page

### F6. Remaining E2E Scenarios

- [ ] SVAL-05: Out-of-sequence event (payment on non-existent contract) → rejected
- [ ] All 10 SVAL scenarios passing

---

## Phase G — Full Stack (Hyperledger Fabric)

### G1. Fabric Network Setup (`infra/fabric/`)

- [ ] `configtx.yaml` — channel + org definitions (single org for POC)
- [ ] `crypto-config.yaml` — CA, peer, orderer cryptographic material
- [ ] `docker-compose-fabric.yml` — orderer, peer, CA, CLI containers
- [ ] `scripts/fabric-setup.sh` — channel creation, join peer, install chaincode

### G2. Chaincode (`apps/chaincode/src/`)

- [ ] `index.ts` — main chaincode entry
- [ ] `contracts/SmartLedgerContract.ts` — implements all Fabric contract functions:
  - [ ] `writeRecord(recordType, payload, proofTokenJti)` → put state
  - [ ] `getRecord(recordId)` → get state
  - [ ] `executeStateTransition(contractId, fromState, toState)` → state machine enforcement
  - [ ] `calculateLateFee(contractId, daysPastDue)` → fee calculation
  - [ ] `checkTitleRelease(contractId)` → eligibility check
  - [ ] `getGovernanceRules()` → return on-chain rules
- [ ] TypeScript build setup (`tsconfig.json`)
- [ ] Chaincode unit tests

### G3. Ledger MCP — Fabric Integration

- [ ] `fabric_client.py` — Fabric Gateway SDK integration
- [ ] Update `write_record` — submit transaction to Fabric, store `fabric_tx_id`
- [ ] Update `execute_state_transition` — call chaincode
- [ ] `WRITE_GUARD=false, PHASE=1` — turn off write guard
- [ ] E2E re-test with live Fabric writes
- [ ] Verify: `contracts.records.fabric_tx_id` populated
- [ ] Verify: on-chain state queryable via Fabric explorer

### G4. Full Dashboard UI

- [ ] `/contracts` — paginated contract list with state badges
- [ ] `/contracts/[id]` — full lifecycle timeline + blockchain verification link
- [ ] `/audit` — system-wide audit log viewer
- [ ] `/reports` — report list + viewer + export
- [ ] Role-based views — admin, auditor, operator, compliance

---

## Testing Checklist (Cross-Phase)

### Unit Tests
- [ ] All validation rules (schema, cross-system, business, sequence, duplicate)
- [ ] JWT proof token issuance + verification
- [ ] Saga checkpoint write + resume
- [ ] Redis lock acquire + release + TTL
- [ ] Event envelope serialization/deserialization
- [ ] Pydantic models — validation + serialization

### Integration Tests
- [ ] Validation Engine + Ledger MCP (full token flow)
- [ ] Oracle LOS → Redis Stream → Agent event loop
- [ ] Quarantine → Dashboard API → approve_override → quarantine.approved event

### E2E Tests (SVAL Scenarios)
- [ ] SVAL-01: Valid contract lifecycle (happy path) ✅
- [ ] SVAL-02: Payment amount mismatch
- [ ] SVAL-03: Balance mismatch → quarantine → override
- [ ] SVAL-04: Duplicate event → skipped
- [ ] SVAL-05: Out-of-sequence event → rejected
- [ ] SVAL-06: Oracle/Salesforce parity drift
- [ ] SVAL-07: Insurance lapse mid-contract
- [ ] SVAL-08: Early payoff
- [ ] SVAL-09: Missing required fields → schema rejection
- [ ] SVAL-10: Override required → quarantine → human approve → write

### Resilience Tests
- [ ] Agent crash at each saga step → resume correctly
- [ ] MCP server down → backoff → DLQ
- [ ] Redis down → graceful degradation
- [ ] Proof token expired during crash recovery → re-validates
- [ ] Duplicate lock attempt → safe rejection

---

## Definition of Done (POC)

- [ ] All 10 SVAL E2E scenarios pass
- [ ] All resilience tests pass
- [ ] Origination happy path runs end-to-end in < 5 seconds
- [ ] `docker compose up -d` starts the full stack cleanly
- [ ] Dashboard shows contract lifecycle, quarantine queue, audit trail
- [ ] Human override flow works in Dashboard (approve + reject)
- [ ] Proof token appears on every ledger record
- [ ] Agent correctly resumes after crash at any checkpoint
- [ ] No PII in `contracts.records` table (hashes only)
