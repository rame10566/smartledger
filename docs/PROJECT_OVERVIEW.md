# SmartLedger — Project Overview & Status
*Last updated: 2026-03-17*

---

## What Is SmartLedger?

SmartLedger is a **validation-gated, immutable ledger** for auto/vehicle finance — specifically Loan/Lease Origination and Accounting. It solves one fundamental problem:

> Multiple enterprise systems (LOS, accounting, payments, CRM, insurance, dealer) all freely update loan records without validating against each other. Every system has its own version of the truth. They silently drift apart.

**SmartLedger's rule:** No data is written to the ledger unless it has been fully validated across all relevant systems and business rules.

**Secondary purpose:** A bridge during the Oracle LOS → Salesforce LOS migration — running both systems in parallel, detecting policy drift, providing an undeniable audit trail of what each system produced.

**Core thesis:** *Systems change. Platforms evolve. Contracts persist.*

---

## What We've Decided & Built

### Phase 1 — Requirements ✅ COMPLETE

All requirements are locked in `REQUIREMENTS.md` (20 sections, ~1000 lines).

| Section | What Was Decided |
|---|---|
| **Project scope** | Validation-gated ledger + migration bridge. Auto finance domain. |
| **Architecture** | AI Agent orchestrated, MCP protocol, Redis Streams event bus, Hyperledger Fabric |
| **Agent flows** | 6 flows defined: Origination, Payment, Report Generation, PDF Ingestion, Customer Profile Update, Conflict Resolution |
| **18 architecture gaps** | All identified and resolved (event delivery, concurrency, failure handling, security, idempotency, observability, etc.) |
| **Security model** | 3 layers: TLS, JWT, per-tool authorization matrix. Validation proof tokens required for all ledger writes. |
| **Data schemas** | Schema registry structure defined. Key schemas: event envelope, origination record, accounting record. |
| **Simulated systems** | 13 systems: Oracle LOS, Salesforce LOS, LLAS, CRM, Payment, Insurance, Dealer, Customer Portal, Mobile App, IVR, Rules Engine, Pricing Engine, **Integration System** |
| **MCP servers** | 4 built servers (Validation, Ledger, Semantic AI, Reporting) + Dashboard API (REST) |
| **Testing strategy** | 4 layers: unit, contract, integration, E2E. SVAL-01 to SVAL-10 are the E2E test scenarios. |
| **Performance targets** | Phase 1: 100 events/min, <5s end-to-end latency |
| **Regulatory** | PII off-chain only, CCPA deletion support, on-chain hashes remain |
| **Tech stack** | Fully locked (see below) |

### Phase 2 — Tech Stack Decisions ✅ LOCKED

| Layer | Decision |
|---|---|
| **AI Agent** | Custom agent — Anthropic API + MCP Python SDK v1.7.1 (FastMCP for servers) |
| **Agent Language** | Python 3.12 |
| **All MCP Servers** | Python 3.12 + FastMCP |
| **Event Bus** | Redis Streams |
| **Blockchain** | Hyperledger Fabric |
| **Chaincode** | Node.js |
| **Database** | PostgreSQL 16 |
| **Schema Validation** | JSON Schema files + Pydantic v2 |
| **Frontend** | Next.js (App Router) |
| **Dashboard API** | FastAPI (Python) — REST |
| **Semantic AI** | Claude API (claude-3-5-sonnet) |
| **Python Package Manager** | `uv` (installed via Homebrew) |
| **JS Package Manager** | `pnpm` |
| **Containerization** | Docker + Docker Compose |
| **Repo** | Monorepo, Hybrid folder layout |
| **MVP Target** | MVP-3: Full Stack Demo |

### Phase 3–7 — Implementation ✅ COMPLETE (Phases A–G)

Full stack implemented and running. All agents, MCP servers, event flows, dashboard, and Hyperledger Fabric integration are complete.

```
smartledger/
├── src/
│   ├── agent/                    # AI Agent (Python) ✅
│   │   ├── core/
│   │   │   ├── event_loop.py     # ✅ AgentEventLoop — XREADGROUP, lock, dispatch, ACK
│   │   │   ├── saga.py           # ✅ SagaManager — checkpoint, resume, crash recovery
│   │   │   ├── locks.py          # ✅ ContractLock — Redis SETNX/Lua safe release
│   │   │   └── mcp_client.py     # ✅ MCP client connections to all servers
│   │   ├── flows/
│   │   │   ├── origination.py    # ✅ Origination happy/unhappy path
│   │   │   ├── payment.py        # ✅ Payment flow (payment.received)
│   │   │   └── pdf_ingestion.py  # ✅ PDF ingestion (Semantic AI + batch archive)
│   │   └── main.py               # ✅ Bootstrap: connect, recover sagas, start loop
│   ├── mcp_servers/
│   │   ├── validation/server.py  # ✅ validate_event, get_quarantined, rules CRUD
│   │   ├── ledger/server.py      # ✅ write_record (proof token gated), query, state, audit
│   │   ├── semantic_ai/server.py # ✅ extract_contract_fields, confidence scoring
│   │   ├── reporting/server.py   # ✅ generate_report, list, get, export
│   │   └── simulated/            # ✅ All 12 simulators
│   │       ├── oracle_los/       # :8010 — originate_contract, get_contract
│   │       ├── salesforce_los/   # :8011
│   │       ├── llas/             # :8012 — get_account, get_balance
│   │       ├── crm/              # :8013
│   │       ├── payment/          # :8014
│   │       ├── insurance/        # :8015
│   │       ├── dealer/           # :8016
│   │       ├── customer_portal/  # :8017
│   │       ├── mobile_app/       # :8018
│   │       ├── ivr/              # :8019
│   │       ├── rules_engine/     # :8020 — credit-tier eligibility rules
│   │       ├── pricing_engine/   # :8021 — rate cards + payment calculation
│   │       └── integration/      # :8022 — integration layer data mover (NEW Phase H)
│   ├── dashboard_api/            # ✅ FastAPI :8000
│   │   ├── main.py               # ✅ CORS, PostgreSQL pool, lifespan
│   │   ├── routers/contracts.py  # ✅ GET /contracts, lifecycle, audit, state
│   │   ├── routers/quarantine.py # ✅ GET /quarantine (read-only audit trail)
│   │   ├── routers/reports.py    # ✅ GET /reports, export
│   │   └── middleware/           # ✅ PBAC, field filtering, access audit
│   └── shared/
│       ├── config.py             # ✅ All env vars, Settings class
│       ├── logging.py            # ✅ Structured JSON logging (structlog)
│       ├── models/               # ✅ All Pydantic v2 models
│       └── schemas/              # ✅ 15 JSON Schema files
├── apps/
│   ├── dashboard-ui/             # ✅ Next.js App Router
│   │   └── src/app/
│   │       ├── contracts/        # ✅ Contract list + detail
│   │       ├── quarantine/       # ✅ Read-only quarantine audit trail
│   │       └── reports/          # ✅ Report generation + export
│   └── chaincode/                # ✅ Node.js Hyperledger Fabric chaincode
├── infra/
│   ├── docker/postgres/init.sql  # ✅ Full PostgreSQL schema (all 6 schemas)
│   ├── fabric/                   # ✅ Network config, channel setup
│   └── [Dockerfiles]             # ✅ All services
├── scripts/
│   └── seed_demo.py              # ✅ Seeds demo contracts via Oracle LOS
├── docker-compose.yml            # ✅ All 14 services (incl. Rules + Pricing engine)
└── REQUIREMENTS.md               # ✅ Locked spec (20 sections)
```

**SDG Validate-Only Boundary (enforced as of 2026-03-17):**
SmartLedger is a validation gateway. It does NOT approve, override, or correct data. Quarantine is a **read-only audit trail** — the originating system must fix data and resubmit a new event.

**Integration Layer Intercept (Phase H — pending):**
SmartLedger intercepts at the Integration System boundary — the point where CRM, Portal, Mobile, and LOS push customer data changes to LLAS. Every contact, payment, and insurance update is validated before reaching LLAS, and conflicts between concurrent updates from different source systems are detected and held for LLAS Admin resolution.

---

## Pre-Implementation Gaps — All Resolved ✅

*Updated 2026-03-14 — all gaps resolved. Ready to implement.*

| Gap | Resolution |
|---|---|
| **GAP-1a: Proof token mechanism** | ✅ **Signed JWT (HS256)**. Validation Engine signs with `PROOF_TOKEN_SECRET`. Ledger MCP verifies signature + expiry + contract_id match + jti not in `validation.used_proof_tokens`. See REQUIREMENTS.md §6.4. |
| **GAP-1b: Dashboard notification** | ✅ **Polling for POC**. Dashboard polls `GET /api/quarantine` on an interval. SSE upgrade deferred to Phase 2. |
| **GAP-2: Agent system prompt** | ✅ **Deferred to Phase C** — drafted as first step of agent implementation, not before. All other design decisions are now locked, so the prompt can be written with full context. |
| **GAP-3: Schema files incomplete** | ✅ **All 15 JSON schema files created** in `src/shared/schemas/` with proper subdirectory structure (common/, events/, records/, entities/, validation/). |
| **GAP-4: Pydantic models missing** | ✅ **All models created** in `src/shared/models/` — common.py, entities.py, records.py, validation.py, saga.py, `__init__.py`. All types exported. |
| **GAP-5: Missing pyproject.toml deps** | ✅ Added `pydantic-settings>=2.3`, `structlog>=24.0`, `pyjwt>=2.8` to `src/shared/pyproject.toml`. |
| **GAP-6: PostgreSQL schema names** | ✅ `init.sql` rewritten. Schema names now match REQUIREMENTS.md exactly: `contracts`, `validation`, `sagas`, `audit`, `reports`, `extraction`. `contracts` schema has both `documents` table (PII) and `records` table (validated writes). |
| **GAP-7: Fabric config missing** | ✅ **By design** — Fabric deferred to Phase G. Write guard stays ON for Phases A–F. `infra/fabric/` populated when Phase G begins. |
| **GAP-8: Build order** | ✅ **Confirmed** — see build order below. |

---

## Confirmed Build Order

```
Phase A — Foundation ✅ DONE
  ✅ src/shared/models/         — all Pydantic models
  ✅ src/shared/schemas/        — all JSON schemas (15 files)
  ✅ src/shared/pyproject.toml  — all deps correct
  ✅ infra/docker/postgres/init.sql — correct schema names + tables

Phase B — First MCP Servers (each runnable standalone)
  → src/mcp_servers/simulated/oracle_los/server.py  — generate + publish events + serve tools
  → src/mcp_servers/simulated/llas/server.py         — serve account/balance tools
  → src/mcp_servers/validation/server.py             — validate events, issue JWT proof tokens
  → src/mcp_servers/ledger/server.py                 — write/query (PostgreSQL, write guard ON)

Phase C — Agent Core
  → src/agent/core/locks.py        — Redis per-contract distributed locks
  → src/agent/core/saga.py         — saga checkpoints (write + resume)
  → src/event_bus/consumer.py      — Redis Streams consumer
  → src/agent/core/event_loop.py   — orchestrates: event → lock → flow → unlock → ack
  → src/agent/main.py              — system prompt drafted here (first step of this phase)

Phase D — Origination Happy Path (first E2E vertical slice)
  → src/agent/flows/origination.py
  → E2E test: Oracle LOS event → Agent → Validation MCP → Ledger MCP → PostgreSQL
  → Verify: record written, saga completed, proof_token_jti on record

Phase E — Unhappy Path + Human-in-the-Loop (test, validate, correct at each step)
  → Quarantine flow in validation MCP
  → src/dashboard_api/main.py  — FastAPI: quarantine list, approve/reject endpoints
  → apps/dashboard-ui/         — Next.js: validation queue + quarantine review UI
  → E2E test: bad event → quarantine → dashboard shows it → human approves → agent retries → written
  → E2E test: bad event → quarantine → human rejects → permanently discarded
  → Saga resume test: crash agent mid-flow → restart → verify resume from correct checkpoint

Phase F — Remaining Flows + All Simulators
  → Payment flow (payment.received + customer/mobile/IVR channels)
  → PDF ingestion (Semantic AI MCP + pdf_ingestion flow)
  → Remaining 8 simulated systems
  → Reporting MCP + report generation flow
  → Full Dashboard UI (lifecycle, audit trail, reports)

Phase G — Full Stack (Hyperledger Fabric)
  → infra/fabric/ — network config, channel setup
  → Chaincode deployment
  → Write guard OFF (WRITE_GUARD=false, PHASE=1)
  → E2E re-test with live Fabric writes
```

---

## Current Status

| Area | Status | Notes |
|---|---|---|
| Requirements | ✅ Locked | REQUIREMENTS.md, all 20 sections, SDG boundary enforced |
| Architecture | ✅ Decided | All 18 gaps resolved |
| Tech stack | ✅ Locked | All decisions made |
| Proof token design | ✅ Done | Signed JWT — see REQUIREMENTS.md §6.4 |
| Dashboard notification | ✅ Done | Polling (10s interval) |
| Build order | ✅ Done | Phases A–I complete |
| Project structure | ✅ Done | Full monorepo implemented |
| Docker Compose | ✅ Done | 15 services (all sims, core, infra) |
| Dockerfiles | ✅ Done | All services |
| PostgreSQL schema | ✅ Done | All 6 schemas + tables + constraints |
| JSON Schemas | ✅ Done | 15 files in subdirectory structure |
| Pydantic models | ✅ Done | All models, full type coverage |
| Agent system prompt | ✅ Done | src/agent/main.py |
| Agent core | ✅ Done | event_loop, saga, locks, mcp_client |
| Agent flows | ✅ Done | origination, payment, pdf_ingestion, customer_update |
| MCP servers — core | ✅ Done | validation, ledger, semantic_ai, reporting |
| MCP servers — simulated | ✅ Done | All 13 simulators (incl. Rules, Pricing, Integration System) |
| Dashboard API | ✅ Done | FastAPI :8000 — contracts, quarantine (read-only), reports, conflicts, party portal |
| Dashboard UI | ✅ Done | Next.js :3000 — contracts, quarantine audit trail, reports, conflicts |
| Smart Data Gateway (PBAC) | ✅ Done | Party-based access control + field-level filtering on ops dashboard |
| **Smart Data Gateway — Party Portal (Path A)** | ✅ Done | JWT-authenticated party portal at `/party`. Borrowers/lenders authenticate with `entity_id + party_type`, receive 1-hour JWT, see only contracts where they are listed parties. Returns `fabric_tx_id` + `data_hash` as on-chain proof. Endpoints: `POST /api/party/auth`, `GET /api/party/contracts`, `GET /api/party/contracts/{id}`. |
| **Hyperledger Explorer** | ✅ Done | Visual blockchain browser at :8090. Connects to existing `smartledger_fabric_net` as external network. Browse blocks, search transactions by `tx_id`, inspect chaincode, view read/write sets. Login: `exploreradmin / exploreradminpw`. Files: `infra/fabric/explorer/` + `scripts/start-explorer.sh`. |
| Chaincode | ✅ Done | Node.js — SmartLedgerContract on Hyperledger Fabric |
| Fabric network config | ✅ Done | infra/fabric/ — channel, crypto, setup scripts |
| Fabric live writes | ✅ Done | WRITE_GUARD=false, Phase 1 mode |
| Seed script | ✅ Done | scripts/seed_demo.py — 12 demo contracts via Oracle LOS |
| SDG validate-only boundary | ✅ Enforced | No approve/override — quarantine is read-only audit trail |
| Integration Layer (Phase H) | ✅ Done | Integration System MCP, customer profile flows, conflict detection + LLAS Admin resolution |
| Tests | ⏳ Partial | Unit + integration tests written alongside each phase; SVAL-05/07/08 deferred |

---

## Files Reference

| File | Purpose |
|---|---|
| `REQUIREMENTS.md` | Full locked requirements (20 sections) |
| `docs/PROJECT_OVERVIEW.md` | This document — project status + decisions |
| `src/shared/config.py` | All environment variables (Settings class, pydantic-settings) |
| `src/shared/logging.py` | Structured JSON logging (structlog) |
| `src/shared/schemas/` | All 15 JSON Schema files (subdirectory structure) |
| `src/shared/models/` | All Pydantic v2 models (common, entities, records, validation, saga) |
| `infra/docker/postgres/init.sql` | PostgreSQL initialization (all schemas + tables, correct names) |
| `docker-compose.yml` | Local dev stack (14 services now; 15 with Integration System in Phase H) |
| `.env.example` | All environment variables documented |
| `scripts/setup.sh` | One-command local setup |
| `pyproject.toml` | uv workspace root (8 Python packages) |
