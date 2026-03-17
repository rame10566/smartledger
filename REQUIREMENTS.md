# SmartLedger (AutoLedger AI) - Project Requirements

## 1. Project Overview

**SmartLedger** is a validation-gated, immutable ledger for **Loan/Lease Origination** and **Loan/Lease Accounting** in the auto/vehicle finance domain.

### The Core Problem

Today, multiple enterprise systems (LOS, LLAS, CRM, payments, insurance, dealer platforms) freely update loan and lease records **without full cross-system validation**. This has created widespread data discrepancies — no single system holds the validated truth about a contract. Every system has its own version, and they silently drift apart.

### The Solution

SmartLedger enforces one fundamental rule:

> **No update is written to the ledger unless it has been fully validated against all relevant rules, cross-system checks, and business logic.**

An **AI Agent** orchestrates the entire flow — connecting to external systems via **MCP (Model Context Protocol)**, running validation, and writing only validated data to the immutable ledger.

### The Migration Layer (Added Capability)

A decision was made to migrate from Legacy Oracle LOS to Salesforce LOS. SmartLedger also serves as the bridge during this two-year parallel run — comparing outputs and detecting policy drift. The migration is temporary. The validated ledger is permanent.

### Core Thesis

**Systems change. Platforms evolve. Contracts persist.**

---

## 2. Orchestration Model — AI Agent + MCP + Event Bus

SmartLedger is **AI-agent-orchestrated**. An AI agent is the central brain that drives the entire contract lifecycle flow. It connects to all systems and services through **MCP (Model Context Protocol)** and receives work through an **Event Bus**.

### How It Works

```
                    SIMULATED EXTERNAL SYSTEMS (MCP Servers)
┌───────────┐ ┌───────────┐ ┌──────┐ ┌─────────┐ ┌───────┐ ┌─────┐ ┌──────┐
│Oracle LOS │ │Salesforce │ │ LLAS │ │Insurance│ │Payment│ │ CRM │ │Dealer│
│ (mock)    │ │LOS (mock) │ │(mock)│ │ (mock)  │ │(mock) │ │(mock│ │(mock)│
└─────┬─────┘ └─────┬─────┘ └──┬───┘ └────┬────┘ └───┬───┘ └──┬──┘ └──┬───┘

┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│Customer Portal│  │  Mobile App   │  │  IVR System   │
│   (mock)      │  │   (mock)      │  │   (mock)      │
└───────┬───────┘  └───────┬───────┘  └───────┬───────┘
      │              │          │          │          │        │       │
      │   MCP (request/response — agent calls these)          │       │
      │              │          │          │          │        │       │
      │   Events published to ──┴──────────┴──────────┴────────┘       │
      │                         │                                      │
      │                         ▼                                      │
      │              ┌─────────────────────┐                           │
      │              │     EVENT BUS        │ (Redis Streams)          │
      │              │                     │                           │
      │              │  Topics:            │                           │
      │              │  • contract.originated                          │
      │              │  • payment.received  │                          │
      │              │  • insurance.verified│                          │
      │              │  • dealer.submitted  │                          │
      │              │  • quarantine.pending│                          │
      │              │  • quarantine.approved                          │
      │              └──────────┬──────────┘                           │
      │                         │                                      │
      │                         │ Agent subscribes / polls             │
      │                         ▼                                      │
      │              ┌─────────────────────────────────┐               │
      │              │        AI AGENT                  │               │
      │              │      (The Orchestrator)          │               │
      │              │                                  │               │
      │              │  • Picks events from bus         │               │
      │              │  • Acquires per-contract lock    │               │
      │              │  • Calls MCP servers for context │◄──────────────┘
      │              │  • Runs validation via MCP       │
      │              │  • Writes to ledger via MCP      │
      │              │  • Checkpoints saga state        │
      │              │  • Releases lock                 │
      │              └──────────┬───────────────────────┘
      │                         │
      │              MCP (request/response)
      │                         │
  ════╪═════════════════════════╪══════════════════════════════════
  ║   │    WE BUILD EVERYTHING BELOW THIS LINE                  ║
  ════╪═════════════════════════╪══════════════════════════════════
      │                         │
      │         ┌───────────────┴───────────────┐
      │         ▼                               ▼
┌─────┴────────────┐              ┌──────────────────────────┐
│Validation Engine │              │ Immutable Ledger         │
│   MCP Server     │              │  MCP Server              │
│                  │              │  (Hyperledger + Chaincode)│
│• validate_event  │              │                          │
│• get_quarantined │              │ Ledger:                  │
│• approve_override│              │ • write_record           │
│• get_rules       │              │ • query_records          │
│• get_rejections  │              │ • get_lifecycle          │
│                  │              │ • get_audit_trail        │
│                  │              │ Smart Contracts:         │
│                  │              │ • execute_state_transition│
│                  │              │ • calculate_late_fee     │
│                  │              │ • check_title_release    │
│                  │              │ • get_governance_rules   │
└──────────────────┘              └──────────────────────────┘
      │                         │
      │         ┌───────────────┴───────────────┐
      │         ▼                               ▼
┌─────┴────────────┐              ┌─────────────────┐
│ Semantic AI      │              │ Reporting       │
│  MCP Server      │              │  MCP Server     │
│                  │              │                 │
│• extract_fields  │              │• generate_report│
│• get_confidence  │              │• list_reports   │
│• submit_review   │              │• export_report  │
└──────────────────┘              └─────────────────┘
                                          │
                   ┌──────────────────────┘
                   ▼
         ┌───────────────────────┐       ┌─────────────────┐
         │  Dashboard API        │       │   Governance     │
         │  (REST/GraphQL — NOT  │◄──────│   Dashboard      │
         │   MCP. Serves the     │       │   (Frontend)     │
         │   frontend directly.) │       └─────────────────┘
         │                       │
         │  Reads from:          │
         │  • PostgreSQL (audit, │
         │    validation, reports)│
         │  • Ledger MCP (read)  │
         └───────────────────────┘

  ════════════════════════════════════════════════════════════
  ║                 INFRASTRUCTURE LAYER                    ║
  ════════════════════════════════════════════════════════════

  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
  │   PostgreSQL     │ │   Redis          │ │  Observability   │
  │   (Off-Chain)    │ │                  │ │                  │
  │                  │ │ • Event Bus      │ │ • Logging (ELK/  │
  │ • contracts      │ │   (Streams)      │ │   Loki)          │
  │ • validation     │ │ • Contract Locks │ │ • Metrics        │
  │ • audit          │ │ • Idempotency    │ │   (Prometheus)   │
  │ • reports        │ │   Cache          │ │ • Tracing        │
  │ • extraction     │ │                  │ │   (OpenTelemetry)│
  │ • saga state     │ │                  │ │                  │
  └─────────────────┘ └─────────────────┘ └─────────────────┘
```

### Why MCP?

| Benefit                    | Description                                                              |
|----------------------------|--------------------------------------------------------------------------|
| **Universal protocol**     | Every system — real or simulated — speaks the same protocol. The agent doesn't care what's behind the MCP server. |
| **Swappable backends**     | When real enterprise systems replace simulators, swap the MCP server implementation. The agent's logic doesn't change. |
| **AI-native**              | MCP is designed for AI agents to discover and use tools. The agent can inspect what tools each server offers, understand schemas, and call them. |
| **Composable**             | Each MCP server is an independent, deployable service. Add new systems by adding new MCP servers. |
| **Auditable**              | Every MCP tool call the agent makes is logged — full trace of what data was fetched, what validation ran, and what was written. |

### Agent Optimization Trade-Off

The AI agent orchestrates **all** flows in Phase 1 for simplicity and rapid development. However, not every event requires AI reasoning. Many flows (e.g., a clean payment on an active contract) are deterministic and follow a fixed validation → write path.

**Planned evolution:**

| Phase | Approach |
|-------|----------|
| **Phase 1** | AI agent handles everything — simplest to build, easiest to debug, full audit trail of agent reasoning |
| **Phase 2** | Introduce a **deterministic processing engine** (code, not LLM) for routine events that match known patterns. AI agent handles only: complex/ambiguous validations, multi-system discrepancies, new event types, edge cases |
| **Phase 3** | AI agent becomes the exception handler. 80%+ of events flow through the deterministic engine. Agent focuses on what AI is actually good at — reasoning under uncertainty |

This is a deliberate trade-off: we accept higher per-event cost in Phase 1 to get the system running faster. We optimize once we understand the real-world event distribution.

### Why Event Bus (Redis Streams)?

MCP is request-response — servers cannot push events to the agent. The event bus solves this:

| Benefit                    | Description                                                              |
|----------------------------|--------------------------------------------------------------------------|
| **Decoupled delivery**     | External systems publish events without knowing about the agent          |
| **Persistent**             | Events survive system restarts — nothing is lost                         |
| **Ordered**                | Events for the same contract arrive in order (partitioned by contract_id)|
| **Replayable**             | If something goes wrong, replay events from a point in time             |
| **Dead Letter Queue**      | Events that fail repeatedly are moved to DLQ for manual investigation    |
| **Backpressure**           | If the agent can't keep up, events queue up safely                      |

### Event Envelope Standard

Every event published to the bus follows this format:

```json
{
  "event_id": "evt-uuid-unique",
  "event_type": "payment.received",
  "source_system": "payment-system",
  "contract_id": "C-1234",
  "timestamp": "2026-03-12T10:00:00Z",
  "correlation_id": "corr-uuid",
  "payload": { }
}
```

### MCP Server Inventory

| MCP Server                   | Type       | Key Tools (exposed to the AI Agent)                          |
|------------------------------|------------|--------------------------------------------------------------|
| **Oracle LOS**               | Simulated  | `get_contract`, `get_pricing_output`, `get_blaze_decision`, `list_events` |
| **Salesforce LOS**           | Simulated  | `get_contract`, `get_logic_output`, `list_events`            |
| **LLAS (Accounting)**        | Simulated  | `get_account`, `get_balance`, `get_payment_history`, `get_fees`, `get_delinquency_status` |
| **CRM**                      | Simulated  | `get_customer`, `get_risk_indicators`                        |
| **Payment System**           | Simulated  | `get_payment`, `list_payments`, `get_settlement`             |
| **Insurance System**         | Simulated  | `get_policy_status`, `verify_insurance`, `list_events`       |
| **Dealer System**            | Simulated  | `get_submission`, `list_submissions`                         |
| **Customer Portal (Web)**    | Simulated  | `get_account_summary`, `get_payment_schedule`, `get_payment_history`, `submit_payment`, `get_payoff_quote`, `get_documents` |
| **Mobile App**               | Simulated  | Same tools as Customer Portal — different channel, same data. `submit_payment`, `get_account_summary`, `get_notifications` |
| **IVR System**               | Simulated  | `get_account_status`, `get_balance_due`, `submit_phone_payment`, `request_callback`, `get_payoff_amount` |
| **Validation Engine**        | Built      | `validate_event`, `get_quarantined`, `approve_override`, `get_validation_rules`, `update_rule`, `get_rule_history`, `get_rejection_log` |
| **Immutable Ledger**         | Built      | `write_record`, `query_records`, `get_contract_lifecycle`, `get_audit_trail`, `get_state`, `execute_state_transition`, `calculate_late_fee`, `check_title_release`, `get_governance_rules` |
| **Semantic AI**              | Built      | `extract_contract_fields`, `get_extraction_confidence`, `submit_for_review` |
| **Reporting**                | Built      | `generate_report`, `list_reports`, `get_report`, `export_report` |
| **Dashboard API**            | Built (REST) | REST/GraphQL API serving the frontend. NOT an MCP server. Reads from PostgreSQL + Ledger MCP. |

---

## 3. AI Agent Orchestration Flows

### 3.1 Contract Origination Flow

```
1. Dealer System publishes event to bus: { event_type: "dealer.submitted", contract_id: "C-1234" }
2. Agent picks event from bus
3. Agent acquires lock: LOCK("contract:C-1234")
4. Agent creates saga checkpoint: EVENT_RECEIVED
5. Agent calls Dealer MCP → get_submission(id) → full contract details (VIN, terms, customer)
6. Agent calls CRM MCP → get_customer(id) → customer profile, risk indicators
7. Agent calls Oracle LOS MCP → get_contract(id) → Oracle's origination version
8. Agent calls Salesforce LOS MCP → get_contract(id) → Salesforce's origination version
9. Agent creates saga checkpoint: CONTEXT_GATHERED
10. Agent calls Validation Engine MCP → validate_event(event + all context)
    - Cross-system check: do Oracle and Salesforce agree? (parity)
    - Business rule check: do terms comply with policy?
    - Sequence check: is this contract_id new? (not duplicate)
11. Agent creates saga checkpoint: VALIDATION_COMPLETE
12. IF VALID:
    → Agent calls Ledger MCP → write_record(origination_record, validation_proof_token)
    → Agent creates saga checkpoint: LEDGER_WRITTEN
    → Agent calls Ledger MCP → execute_state_transition(contract_id, "originated")
    → Agent creates saga checkpoint: COMPLETE
13. IF INVALID:
    → Agent calls Validation Engine MCP → quarantine(event, reasons)
    → Event bus publishes: { event_type: "quarantine.pending" }
    → Agent creates saga checkpoint: QUARANTINED
14. Agent releases lock: UNLOCK("contract:C-1234")
15. Agent acknowledges event on bus (removes from queue)
```

### 3.2 Payment Processing Flow

```
1. Payment System publishes event to bus: { event_type: "payment.received", contract_id: "C-1234" }
2. Agent picks event from bus
3. Agent acquires lock: LOCK("contract:C-1234")
4. Agent checks idempotency: has event_id already been processed? If yes, skip.
5. Agent calls Payment MCP → get_payment(id) → payment details
6. Agent calls Ledger MCP → get_contract_lifecycle(contract_id) → current contract state
7. Agent calls LLAS MCP → get_balance(account_id) → current accounting balance
8. Agent calls Validation Engine MCP → validate_event(payment + contract + balance)
   - Cross-system check: does payment amount match contract terms?
   - Sequence check: is contract in an active state?
   - Duplicate check: has this payment already been recorded?
9. IF VALID:
   → Agent calls Ledger MCP → write_record(accounting_record, validation_proof_token)
   → Agent calls Ledger MCP → execute_state_transition(contract_id, "payment_applied")
10. IF INVALID → quarantine with reasons
11. Agent releases lock, acknowledges event
```

### 3.3 Report Generation Flow

```
1. Agent receives request via bus: { event_type: "report.requested" } (or scheduled trigger)
2. Agent calls Ledger MCP → query_records(filters) → relevant contract data
3. Agent calls Validation Engine MCP → get_rejection_log(filters) → validation failures
4. Agent calls Reporting MCP → generate_report(type, data, compliance_policies)
5. Agent calls Reporting MCP → store report in PostgreSQL (Dashboard API reads it via REST)
```

### 3.4 Contract PDF Ingestion Flow (Semantic AI)

```
1. Dealer submits a contract as a PDF (event: "dealer.pdf_submitted")
2. Agent picks event from bus
3. Agent calls Semantic AI MCP → extract_contract_fields(pdf_reference)
4. Agent receives structured JSON with confidence scores
5. Agent calls LOS MCP → get_contract(id) → system's structured version
6. Agent compares: extracted PDF fields vs LOS structured data
7. IF fields match (within confidence threshold):
   → Proceed to standard origination validation (Flow 3.1, step 10)
8. IF fields DON'T match:
   → Quarantine for human review with discrepancy details
   → ("PDF says $25,000 monthly payment but LOS says $24,500")
9. IF confidence below threshold:
   → Quarantine for human review with low-confidence flag
```

### 3.5 Human-in-the-Loop Override Flow

```
1. Agent quarantines an event (from any flow above)
   → Writes quarantine record to PostgreSQL with full context
   → Publishes "quarantine.pending" to event bus
2. Dashboard picks up notification, shows in review queue
3. Human reviews in Governance Dashboard:
   → Sees: original event, cross-system data, rejection reason, agent's recommendation
   → Actions: Approve (override), Reject (confirm rejection), Escalate
   → Decision is authenticated (user identity) and stored in audit log
4. On Approve:
   → Dashboard calls Validation Engine MCP → approve_override(quarantine_id, approver_id, reason)
   → Validation Engine publishes "quarantine.approved" to event bus
   → Agent picks up, resumes saga from VALIDATION_COMPLETE checkpoint with override flag
   → Writes to ledger with override audit trail attached
5. On Reject:
   → Quarantine record marked as REJECTED, event is permanently discarded
6. SLAs:
   → 24 hours: escalation alert to team lead
   → 72 hours: auto-escalate to manager
   → Dashboard shows aging metrics for all quarantined events
```

---

## 4. Infrastructure Layer

### 4.1 Event Bus (Redis Streams)

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| EVT-01  | Redis Streams as the event bus for all event delivery                       | **Critical** |
| EVT-02  | Events partitioned by `contract_id` to ensure per-contract ordering         | **Critical** |
| EVT-03  | All events follow the standard event envelope schema                        | **Critical** |
| EVT-04  | Events persist until explicitly acknowledged by the agent                   | High     |
| EVT-05  | Dead Letter Queue (DLQ) topic for events that fail after max retries        | High     |
| EVT-06  | Support event replay from a point in time for recovery                      | Medium   |
| EVT-07  | Backpressure: events queue safely when agent cannot keep up                 | Medium   |
| EVT-08  | AOF persistence + daily RDB snapshots for durability                        | High     |

**Event Topics:**

| Topic                     | Publisher              | Consumer   | Description                          |
|---------------------------|------------------------|------------|--------------------------------------|
| `contract.originated`     | Oracle LOS, Salesforce LOS | Agent | New contract origination event       |
| `dealer.submitted`        | Dealer System          | Agent      | New dealer contract submission       |
| `dealer.pdf_submitted`    | Dealer System          | Agent      | Contract PDF submitted for extraction|
| `payment.received`        | Payment System         | Agent      | Payment received event               |
| `payment.missed`          | Payment System         | Agent      | Missed payment event                 |
| `insurance.verified`      | Insurance System       | Agent      | Insurance verification event         |
| `insurance.lapsed`        | Insurance System       | Agent      | Insurance lapse notification         |
| `customer.payment_submitted` | Customer Portal / Mobile App | Agent | Customer made an online payment    |
| `customer.payoff_requested`  | Customer Portal / Mobile App | Agent | Customer requested early payoff    |
| `ivr.payment_submitted`  | IVR System             | Agent      | Payment submitted via phone IVR      |
| `ivr.callback_requested` | IVR System             | Agent      | Customer requested agent callback    |
| `report.requested`        | Dashboard / Scheduler  | Agent      | Report generation request            |
| `quarantine.pending`      | Agent                  | Dashboard  | New quarantined event for human review |
| `quarantine.approved`     | Validation Engine      | Agent      | Human approved quarantined event     |
| `quarantine.rejected`     | Validation Engine      | Dashboard  | Human rejected quarantined event     |
| `dlq`                     | Agent                  | Operations | Events that failed after max retries |

### 4.2 Off-Chain Data Store (PostgreSQL)

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| PG-01   | Single PostgreSQL instance with schema separation                           | **Critical** |
| PG-02   | JSONB columns for flexible event payloads where needed                      | High     |
| PG-03   | Daily automated backup + WAL archiving for point-in-time recovery           | High     |
| PG-04   | Connection pooling for all MCP servers                                      | Medium   |

**Schemas:**

| Schema        | Stores                                                              | Accessed By              |
|---------------|---------------------------------------------------------------------|--------------------------|
| `contracts`   | Full contract documents, PII, customer data (linked to on-chain hash) | Ledger MCP, Reporting MCP |
| `validation`  | Quarantine records, rejection logs, validation rule configs + version history | Validation Engine MCP |
| `audit`       | Agent decision trails, full MCP call traces, override approvals     | Dashboard API (REST)     |
| `reports`     | Generated report data, export files                                 | Reporting MCP            |
| `extraction`  | Semantic AI results, confidence scores, review queue                | Semantic AI MCP          |
| `sagas`       | Saga state (checkpoints, idempotency table)                         | Agent                    |

**On-chain ↔ Off-chain link:**
```
On-chain:  { contract_id: "C-1234", data_hash: "sha256:abc123..." }
Off-chain: { contract_id: "C-1234", full_data: {...}, hash: "sha256:abc123..." }

Verification: hash(off-chain full_data) === on-chain data_hash
```

### 4.3 Concurrency Control (Redis Distributed Locks)

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| LCK-01  | Per-contract distributed lock before processing any event                   | **Critical** |
| LCK-02  | Lock key format: `contract:{contract_id}`                                   | High     |
| LCK-03  | Lock TTL: 60 seconds (prevents deadlocks if agent crashes while holding)    | High     |
| LCK-04  | If lock not acquired: requeue event with delay                              | High     |
| LCK-05  | Events for DIFFERENT contracts process in parallel                          | High     |
| LCK-06  | Events for the SAME contract process sequentially                           | **Critical** |
| LCK-07  | Hyperledger Fabric MVCC as second safety net (rejects conflicting transactions) | High |

### 4.4 Observability

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| OBS-01  | Structured JSON logging from all services (agent + all MCP servers)         | High     |
| OBS-02  | Every log entry includes: `timestamp`, `service`, `correlation_id`, `saga_id`, `contract_id`, `level`, `message` | High |
| OBS-03  | Central log aggregation (ELK stack or Loki + Grafana)                       | High     |
| OBS-04  | Metrics via Prometheus + Grafana dashboards                                 | High     |
| OBS-05  | Distributed tracing via OpenTelemetry (saga_id = trace ID)                  | Medium   |
| OBS-06  | Health check endpoint on every MCP server                                   | High     |

**Key Metrics:**

| Metric                              | Source              | Alert Threshold            |
|--------------------------------------|---------------------|----------------------------|
| Events processed per minute          | Agent               | < 10/min (agent stalled)   |
| Validation pass/fail/quarantine rate | Validation Engine   | Fail rate > 30%            |
| Ledger write latency                 | Ledger MCP          | > 5 seconds                |
| MCP call latency per server          | Agent               | > 2 seconds                |
| Event bus queue depth                | Redis Streams       | > 1000 (agent falling behind) |
| Active / incomplete sagas            | Agent               | Incomplete > 30 min        |
| Quarantine aging                     | Validation Engine   | Any > 24 hours             |
| Hyperledger peer health              | Ledger MCP          | Peer unreachable           |

**Alerting rules:**
- Event bus queue depth > threshold → agent is falling behind
- Validation failure rate spike → possible system issue
- Incomplete sagas older than 30 minutes → agent may be stuck
- MCP server health check failure → service down
- Quarantined events aging beyond SLA → escalation needed

---

## 5. Reliability & Failure Handling

### 5.1 Saga Pattern (Persistent Checkpoints)

Every multi-step flow is a **saga** with persistent checkpoints in PostgreSQL. If the agent crashes, it resumes from the last checkpoint on restart.

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| SAG-01  | Every flow creates a saga record with persistent checkpoints                | **Critical** |
| SAG-02  | Checkpoints: `EVENT_RECEIVED`, `CONTEXT_GATHERED`, `VALIDATION_COMPLETE`, `LEDGER_WRITTEN`, `COMPLETE`, `QUARANTINED` | **Critical** |
| SAG-03  | On agent restart, query for incomplete sagas and resume from last checkpoint | **Critical** |
| SAG-04  | Each saga carries a `saga_id` used as correlation_id for all MCP calls      | High     |
| SAG-05  | All MCP tool calls within a saga are idempotent or safely re-callable       | High     |

**Saga State Table (PostgreSQL):**

| Column           | Type      | Description                                |
|------------------|-----------|--------------------------------------------|
| `saga_id`        | UUID      | Unique saga identifier                     |
| `event_id`       | UUID      | Source event that triggered this saga       |
| `contract_id`    | string    | Contract being processed                   |
| `current_step`   | enum      | Last completed checkpoint                  |
| `validation_result` | JSON   | Result from validation (if reached)        |
| `started_at`     | timestamp | When saga began                            |
| `last_updated`   | timestamp | Last checkpoint time                       |
| `status`         | enum      | `in_progress`, `completed`, `failed`, `quarantined` |

### 5.2 Idempotency

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| IDP-01  | Every event has a unique `event_id`                                         | **Critical** |
| IDP-02  | Before processing, agent checks: has this `event_id` already been processed? | **Critical** |
| IDP-03  | Idempotency table in PostgreSQL tracks all processed event_ids              | High     |
| IDP-04  | Smart contract calls (state transitions) must be idempotent                 | High     |

**Idempotency Table (PostgreSQL):**

| Column         | Type      | Description                          |
|----------------|-----------|--------------------------------------|
| `event_id`     | UUID      | Unique event identifier (primary key)|
| `saga_id`      | UUID      | Associated saga                      |
| `status`       | enum      | `in_progress`, `completed`, `rejected`, `quarantined` |
| `result`       | JSON      | Outcome details                      |
| `processed_at` | timestamp | When processing completed            |

### 5.3 Failure Recovery

| Failure Scenario                                | Recovery                                              |
|-------------------------------------------------|-------------------------------------------------------|
| Agent crash after validation, before ledger write | Resume saga: re-submit validated event to ledger     |
| Agent crash after ledger write, before state transition | Resume saga: re-execute state transition (idempotent) |
| MCP server down mid-flow                        | Retry with exponential backoff; after N retries → DLQ |
| Hyperledger endorsement failure                 | Retry; if persistent → alert operations               |
| Event with missing/corrupt data                 | Reject at schema validation level, log, move to DLQ   |
| Redis lock expired while processing             | Saga detects stale lock on resume, re-acquires        |

---

## 6. Security Model

### 6.1 Three-Layer Security

| Layer              | Mechanism                                                            | Priority |
|--------------------|----------------------------------------------------------------------|----------|
| **Transport**      | All MCP communication over TLS. Mutual TLS in production.           | **Critical** |
| **Authentication** | JWT identity tokens for agent and every MCP server                   | **Critical** |
| **Authorization**  | MCP servers enforce who can call which tools                         | **Critical** |

### 6.2 Authentication

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| SEC-01  | Agent has an `agent` role JWT token                                         | **Critical** |
| SEC-02  | Each MCP server has a `service` role JWT token                              | **Critical** |
| SEC-03  | Phase 1: JWT with shared secrets. Phase 2: mTLS + proper PKI.              | High     |
| SEC-04  | Token rotation on a configurable schedule                                   | Medium   |

### 6.3 Authorization Matrix

| MCP Server                | `write_record`         | `query_records` | `validate_event` | All tools |
|---------------------------|------------------------|-----------------|-------------------|-----------|
| Agent                     | ✅ (with validation proof) | ✅           | ✅               | ✅        |
| Reporting MCP             | ❌                     | ✅ (read-only)  | ❌               | Own tools |
| Dashboard API (REST)      | ❌                     | ✅ (read-only via Ledger MCP) | ❌    | N/A — REST endpoints, not MCP |
| Simulated systems         | ❌                     | ❌              | ❌               | Own tools |
| Any unauthenticated caller| ❌                     | ❌              | ❌               | ❌        |

**Note**: The matrix above governs **tool-level** access (which service can call which MCP tool). **Party-level** access (which human/entity can see which contract and which fields) is a separate dimension, defined in Section 6.5. Both dimensions are enforced simultaneously: a service must have tool-level access (this matrix) AND the requesting user/entity must have party-level or role-level access (Section 6.5).

### 6.4 Validation Proof Token

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| SEC-05  | The Validation Engine issues a **validation proof token** when validation passes | **Critical** |
| SEC-06  | The Ledger MCP `write_record` tool requires BOTH: agent identity token + validation proof token | **Critical** |
| SEC-07  | Validation proof tokens are single-use and time-limited (expire in 60 seconds) | High |
| SEC-08  | Even if someone calls the Ledger directly, they cannot write without a valid proof token | **Critical** |

**Implementation: Signed JWT (decided 2026-03-14)**

Proof tokens are implemented as **signed JWTs** — not database lookups. Rationale: no cross-service database dependency; Ledger MCP verifies the signature independently.

```
JWT Claims:
  jti         — unique token ID (UUID); stored in validation.used_proof_tokens after use to prevent replay
  contract_id — must match the record being written
  event_id    — the event that triggered this validation
  saga_id     — for audit trail correlation
  iat         — issued-at (Unix timestamp)
  exp         — expires-at (iat + 60 seconds)

Signing:
  Algorithm:  HS256
  Secret:     PROOF_TOKEN_SECRET env var (shared between Validation MCP + Ledger MCP only)

Verification (Ledger MCP):
  1. Verify JWT signature (PROOF_TOKEN_SECRET)
  2. Check exp — token must not be expired
  3. Check contract_id claim matches record.contract_id
  4. Check jti NOT in validation.used_proof_tokens (replay prevention)
  5. Write record to ledger
  6. INSERT jti into validation.used_proof_tokens

On-chain:
  proof_token_jti stored in every ledger record as cryptographic evidence of validation
```

### 6.5 Party-Based Access Control (Smart Data Gateway)

Hyperledger Fabric is a **permissioned** blockchain — its purpose is not just immutability but **controlled access to a shared truth**. The MCP layer + Dashboard API together form the **Smart Data Gateway** — the ONLY path through which any human or system reads contract data. No one queries Fabric directly. The Gateway enforces party-level and role-level access on every read.

#### 6.5.1 Contract Party Model

Every contract has explicit **parties** — the entities with a legitimate interest in that contract's data. Parties are recorded at origination and updated as the contract lifecycle evolves (e.g., servicing transfer, insurance added).

| Party Role       | Entity Type     | Description                                                   | When Added              |
|------------------|-----------------|---------------------------------------------------------------|-------------------------|
| `borrower`       | customer        | The individual financing the vehicle                          | Origination             |
| `lessee`         | customer        | The individual leasing the vehicle (alias for borrower)       | Origination             |
| `lender`         | organization    | The finance company extending credit (always a party)         | Origination             |
| `lessor`         | organization    | The finance company in a lease (alias for lender)             | Origination             |
| `dealer`         | dealer          | The dealership that originated the deal                       | Origination             |
| `servicer`       | organization    | Entity servicing the loan (may be lender or a third party)    | Origination or transfer |
| `insurer`        | organization    | Insurance company if coverage is bundled                      | When insurance verified |

| ID        | Requirement                                                                         | Priority     |
|-----------|-------------------------------------------------------------------------------------|--------------|
| PBAC-01   | Every contract must record its parties with role, entity_type, and entity_id        | **Critical** |
| PBAC-02   | The lender/lessor party is always present (implicit — the finance company)          | **Critical** |
| PBAC-03   | Parties are written to PostgreSQL `contracts.parties` table at origination          | **Critical** |
| PBAC-04   | Party list is append-only; parties can be added (servicer transfer, insurer) but never removed | High |
| PBAC-05   | On-chain records store a `parties_hash` (SHA-256 of the sorted party list) — not the party details themselves | High |

#### 6.5.2 Access Tiers

Access to contract data is determined by the intersection of **identity** and **relationship**.

| Access Tier          | Who                                            | Scope                                       | How Identified              |
|----------------------|------------------------------------------------|----------------------------------------------|-----------------------------|
| **Party Access**     | A party to a specific contract                 | That contract only, filtered to entitled fields | JWT `sub` claim matched against `contracts.parties.entity_id` |
| **Operational Role** | admin, operator, auditor, compliance           | All contracts, filtered to role entitlements | JWT `role` claim             |
| **System Access**    | MCP servers (agent, reporting, etc.)           | As defined in Section 6.3 Authorization Matrix | JWT `role=service`, tool-level |

| ID        | Requirement                                                                         | Priority     |
|-----------|-------------------------------------------------------------------------------------|--------------|
| PBAC-06   | Party access: a party can view ONLY contracts where their entity_id appears in `contracts.parties` | **Critical** |
| PBAC-07   | Operational roles: admin and compliance see all contracts and all fields            | **Critical** |
| PBAC-08   | Operational roles: auditor sees all contracts, read-only, with full audit trail     | High         |
| PBAC-09   | Operational roles: operator sees quarantine queue and assigned contracts            | High         |
| PBAC-10   | System access: AI agent retains full access (it is the orchestrator)               | **Critical** |

#### 6.5.3 Field-Level Visibility Matrix

Different parties see different fields. The Gateway strips fields the caller is not entitled to see.

| Field Category            | Borrower/Lessee | Dealer  | Servicer | Insurer | Admin | Auditor | Compliance |
|---------------------------|:---------------:|:-------:|:--------:|:-------:|:-----:|:-------:|:----------:|
| Contract ID, type, state  | Yes             | Yes     | Yes      | Yes     | Yes   | Yes     | Yes        |
| Vehicle (VIN, make, model)| Yes             | Yes     | Yes      | Yes     | Yes   | Yes     | Yes        |
| Financial terms (APR, payment, term) | Yes  | Yes     | Yes      | No      | Yes   | Yes     | Yes        |
| Amount financed, residual | Yes             | Yes     | Yes      | No      | Yes   | Yes     | Yes        |
| Down payment              | Yes             | Yes     | Yes      | No      | Yes   | Yes     | Yes        |
| Dealer margin / incentives| No              | Yes     | No       | No      | Yes   | Yes     | Yes        |
| Customer PII (name, SSN, DOB, address) | Own only | No | No   | No      | Yes   | Yes     | Yes        |
| Customer credit score / tier | Own only     | No      | No       | No      | Yes   | Yes     | Yes        |
| Payment history           | Yes             | No      | Yes      | No      | Yes   | Yes     | Yes        |
| Delinquency status        | Yes             | No      | Yes      | No      | Yes   | Yes     | Yes        |
| Internal risk scores      | No              | No      | No       | No      | Yes   | Yes     | Yes        |
| Compliance notes          | No              | No      | No       | No      | Yes   | No      | Yes        |
| Audit trail               | No              | No      | No       | No      | Yes   | Yes     | Yes        |
| Other dealers' contracts  | No              | No      | N/A      | N/A     | Yes   | Yes     | Yes        |

| ID        | Requirement                                                                         | Priority     |
|-----------|-------------------------------------------------------------------------------------|--------------|
| PBAC-11   | Gateway applies field-level filtering BEFORE returning data to the caller           | **Critical** |
| PBAC-12   | Field visibility matrix is defined in configuration, not hardcoded                  | High         |
| PBAC-13   | A borrower sees ONLY their own PII — never another customer's data                  | **Critical** |
| PBAC-14   | A dealer sees ONLY contracts they originated — never another dealer's contracts      | **Critical** |

#### 6.5.4 The Smart Data Gateway Principle

The MCP layer (Ledger MCP, Validation MCP, Reporting MCP) and the Dashboard API together form the **Smart Data Gateway**. This is the enforcement point for all access control.

```
    External Users / Systems
              |
              v
    +---------------------------+
    |   SMART DATA GATEWAY      |
    |                           |
    |  Dashboard API (REST)     | <-- Human users (Dashboard UI)
    |  Ledger MCP Server        | <-- AI Agent, Reporting MCP
    |  Validation MCP Server    | <-- AI Agent
    |  Reporting MCP Server     | <-- AI Agent, Dashboard API
    |                           |
    |  +---------------------+  |
    |  | Access Enforcement  |  |
    |  | - Identity (JWT)    |  |
    |  | - Party lookup      |  |
    |  | - Field filtering   |  |
    |  | - Audit logging     |  |
    |  +---------------------+  |
    +---------------------------+
              |
              v
    +---------------------------+
    |  PostgreSQL + Fabric      |
    |  (never accessed          |
    |   directly by users)      |
    +---------------------------+
```

| ID        | Requirement                                                                         | Priority     |
|-----------|-------------------------------------------------------------------------------------|--------------|
| PBAC-15   | No human user or external system queries Fabric or PostgreSQL directly              | **Critical** |
| PBAC-16   | All reads go through the Smart Data Gateway (Dashboard API or Ledger MCP)           | **Critical** |
| PBAC-17   | The Gateway is the ONLY code path that returns contract data to callers             | **Critical** |
| PBAC-18   | Phase 1: Gateway enforcement. Phase 2: add Fabric MSP-level enforcement when orgs have own peers | High |

#### 6.5.5 Access Audit

Every data access through the Gateway is logged — not just writes.

| ID        | Requirement                                                                         | Priority     |
|-----------|-------------------------------------------------------------------------------------|--------------|
| PBAC-19   | Every read request is logged to `audit.access_log` with: actor, role, contract_id, fields_returned, timestamp | **Critical** |
| PBAC-20   | Actor identity is extracted from JWT — never hardcoded                               | **Critical** |
| PBAC-21   | Access logs are queryable by contract_id, actor, and time range                     | High         |
| PBAC-22   | Access audit satisfies REG-06 ("Full audit trail for all data access")               | **Critical** |
| PBAC-23   | Access denied events are logged separately with reason                               | High         |

#### 6.5.6 Design Decision: Enforcement Layer

| Option                | Pros                                                     | Cons                                               |
|-----------------------|----------------------------------------------------------|----------------------------------------------------|
| Chaincode (Fabric MSP)| Enforcement at the data layer; cryptographically bound  | In this POC the agent is the only Fabric caller; no benefit yet |
| MCP/API Gateway       | Natural enforcement point where humans interact; simple to implement | Must ensure no bypass paths exist |
| Both                  | Defense in depth                                         | Complexity for POC                                 |

**Decision**: Enforce at the **Gateway level** (Dashboard API + Ledger MCP) for Phase 1. The chaincode adds MSP-level enforcement in Phase 2 when organizations run their own Fabric peers. This is sufficient because (a) the agent is the only chaincode caller in Phase 1, and (b) the Gateway is the only interface humans and external systems use.

---

## 7. Data Schemas

### 7.1 Schema Registry

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| SCH-01  | Central schema registry in project `schemas/` directory using JSON Schema   | **Critical** |
| SCH-02  | Every MCP server validates inputs/outputs against the registry              | **Critical** |
| SCH-03  | Invalid data rejected at the MCP tool boundary before reaching business logic | High |
| SCH-04  | Schemas are versioned (version field in each schema file)                   | High     |

**Schema Directory Structure** (located at `src/shared/schemas/`):

```
src/shared/schemas/
├── common/
│   ├── event_envelope.json      ✅ Event bus envelope (all events)
│   ├── money.json               ✅ Monetary amount + currency
│   └── address.json             ✅ US mailing address (PII — off-chain only)
├── events/
│   ├── contract_originated.json ✅ Payload for contract.originated
│   ├── dealer_submitted.json    ✅ Payload for dealer.submitted
│   ├── dealer_pdf_submitted.json ✅ Payload for dealer.pdf_submitted
│   ├── payment_received.json    ✅ Payload for payment.received
│   ├── payment_missed.json      ✅ Payload for payment.missed
│   ├── insurance_verified.json  ✅ Payload for insurance.verified
│   └── insurance_lapsed.json    ✅ Payload for insurance.lapsed
├── records/
│   ├── origination_record.json  ✅ On-chain origination record (no PII)
│   ├── accounting_record.json   ✅ On-chain accounting/payment record
│   └── contract_lifecycle.json  ✅ Aggregate state history view
├── entities/
│   ├── contract.json            ✅ Core contract entity
│   ├── customer.json            ✅ Customer (hashes on-chain, PII off-chain)
│   ├── vehicle.json             ✅ Vehicle details
│   ├── financial_terms.json     ✅ Loan/lease financial terms
│   ├── payment.json             ✅ Payment transaction
│   └── account.json             ✅ LLAS accounting account
└── validation/
    ├── validation_request.json  ✅ Input to validate_event tool
    ├── validation_result.json   ✅ Output from validate_event tool
    └── quarantine_record.json   ✅ Quarantined event awaiting review
```

**Pydantic Models** (located at `src/shared/models/`): Typed Python models mirroring all schemas above. Import via `from shared.models import EventEnvelope, ValidationResult, ...`

### 7.2 Origination Record Schema (Key Fields)

| Field               | Type    | Description                                  |
|---------------------|---------|----------------------------------------------|
| `contract_id`       | string  | Unique contract identifier                   |
| `vin`               | string  | Vehicle Identification Number                |
| `customer_id`       | string  | Reference to customer (PII stored off-chain) |
| `dealer_id`         | string  | Originating dealer                           |
| `loan_amount`       | Money   | Principal amount (amount + currency)         |
| `interest_rate`     | decimal | Annual percentage rate                       |
| `term_months`       | integer | Loan/lease term in months                    |
| `monthly_payment`   | Money   | Calculated monthly payment                   |
| `origination_date`  | date    | Contract start date                          |
| `maturity_date`     | date    | Contract end date                            |
| `origination_source`| enum    | `oracle_los` or `salesforce_los`             |
| `contract_type`     | enum    | `loan` or `lease`                            |
| `state`             | enum    | `originated`, `active`, `delinquent`, `paid_off`, `title_released` |
| `data_hash`         | string  | SHA-256 hash of full off-chain record        |

### 7.3 Accounting Record Schema (Key Fields)

| Field               | Type    | Description                                  |
|---------------------|---------|----------------------------------------------|
| `record_id`         | string  | Unique accounting record ID                  |
| `contract_id`       | string  | Associated contract                          |
| `account_id`        | string  | Accounting system account reference          |
| `record_type`       | enum    | `payment_applied`, `fee_assessed`, `balance_adjustment`, `payoff` |
| `amount`            | Money   | Transaction amount                           |
| `running_balance`   | Money   | Balance after this transaction               |
| `effective_date`    | date    | When this transaction takes effect           |
| `source_event_id`   | string  | The event_id that triggered this record      |
| `validation_proof`  | string  | Proof token used for this write              |
| `data_hash`         | string  | SHA-256 hash of full off-chain record        |

---

## 8. Requirements — Systems We BUILD

### 8.1 AI Agent (The Orchestrator)

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| AGT-01  | Connect to all MCP servers (external systems + SmartLedger core services)   | **Critical** |
| AGT-02  | Subscribe to event bus and process events from all topics                   | **Critical** |
| AGT-03  | Orchestrate all lifecycle flows (origination, payment, insurance, delinquency, title release, PDF ingestion) | **Critical** |
| AGT-04  | Acquire per-contract lock before processing; release after                  | **Critical** |
| AGT-05  | Create and maintain saga checkpoints for every flow                         | **Critical** |
| AGT-06  | Check idempotency before processing any event                               | **Critical** |
| AGT-07  | For every event, gather cross-system context via MCP before validation      | **Critical** |
| AGT-08  | Call Validation Engine for every event; never write to ledger without validation | **Critical** |
| AGT-09  | Handle MCP server failures (retry with exponential backoff, circuit break, DLQ) | High |
| AGT-10  | On restart, resume incomplete sagas from last checkpoint                    | **Critical** |
| AGT-11  | Log every decision with full context (all MCP calls traced with saga_id)    | High     |
| AGT-12  | Support human-in-the-loop for quarantined events                            | Medium   |
| AGT-13  | Trigger report generation on schedule or on-demand                          | Medium   |
| AGT-14  | Operate in `read_only` mode (Phase 0) or `active` mode (Phase 1+)          | High     |

### 8.2 Validation Engine (MCP Server)

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| VAL-01  | Expose MCP tools: `validate_event`, `get_quarantined`, `approve_override`, `get_validation_rules`, `update_rule`, `get_rule_history`, `get_rejection_log` | **Critical** |
| VAL-02  | Cross-system validation: verify event data matches across all relevant systems | **Critical** |
| VAL-03  | Business rule validation: verify compliance with contract terms, fee schedules, regulations | **Critical** |
| VAL-04  | Sequence validation: reject out-of-order events                             | High     |
| VAL-05  | Duplicate detection: reject events already recorded on the ledger           | High     |
| VAL-06  | Issue **validation proof token** on successful validation (single-use, 60s expiry) | **Critical** |
| VAL-07  | Reject and quarantine failed events with full rejection reasons             | High     |
| VAL-08  | Configurable validation rule sets per event type, stored in PostgreSQL      | High     |
| VAL-09  | All rules versioned and auditable (append-only history, rollback = activate previous version) | High |
| VAL-10  | Parity validation: compare Oracle LOS vs Salesforce LOS outputs            | High     |
| VAL-11  | Policy drift detection                                                      | High     |

### 8.3 Immutable Ledger (MCP Server wrapping Hyperledger Fabric + Chaincode)

This is a **single MCP server** that wraps both Hyperledger Fabric (the ledger) and the chaincode (smart contracts). There is no separate Smart Contracts MCP server — chaincode runs inside Fabric as part of the same deployment.

**Ledger Tools:**

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| BC-01   | Expose MCP tools: `write_record`, `query_records`, `get_contract_lifecycle`, `get_audit_trail`, `get_state` | **Critical** |
| BC-02   | Deploy on Hyperledger Fabric as a permissioned network                      | High     |
| BC-03   | Two core on-chain record types: **Origination Records** and **Accounting Records** | **Critical** |
| BC-04   | All records immutable once finalized                                        | **Critical** |
| BC-05   | `write_record` requires agent identity token + validation proof token       | **Critical** |
| BC-06   | **Write guard**: configurable flag to disable writes entirely (Phase 0 enforcement) | High |
| BC-07   | Emit events for all state changes                                           | High     |
| BC-08   | Party-based and role-based access control — enforced at the Smart Data Gateway (Section 6.5). Phase 1: Gateway enforcement. Phase 2: Fabric MSP enforcement per organization. | High |
| BC-09   | Store only cryptographic hashes on-chain for PII                            | High     |
| BC-10   | Undeniable audit trail with timestamps                                      | High     |
| BC-11   | MVCC conflict detection as safety net against concurrent writes             | High     |

**Smart Contract (Chaincode) Tools:**

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| SC-01   | Expose MCP tools: `execute_state_transition`, `calculate_late_fee`, `check_title_release`, `get_governance_rules` | High |
| SC-02   | State transitions: originated → active → delinquent → paid-off → title-released | High |
| SC-03   | Execute governance logic (late fees, penalties, title release conditions)    | High     |
| SC-04   | Only validated writes trigger state changes                                 | **Critical** |
| SC-05   | All state transitions emit auditable events                                 | High     |
| SC-06   | State transition calls are idempotent (safe to retry)                       | High     |

### 8.4 Semantic AI Engine (MCP Server)

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| SAI-01  | Expose MCP tools: `extract_contract_fields`, `get_extraction_confidence`, `submit_for_review` | High |
| SAI-02  | Extract key contract fields from PDF documents                              | High     |
| SAI-03  | Layout-agnostic: understand legal intent, not text position                 | High     |
| SAI-04  | Output structured JSON with confidence scores                               | High     |
| SAI-05  | Flag low-confidence extractions for human review                            | Medium   |
| SAI-06  | Results stored in PostgreSQL `extraction` schema                            | High     |

### 8.5 Reporting System (MCP Server)

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| RPT-01  | Expose MCP tools: `generate_report`, `list_reports`, `get_report`, `export_report` | High |
| RPT-02  | Read contract data from the immutable ledger (read-only access)             | High     |
| RPT-03  | Enforce compliance policies on data access (role-based, field-level)        | High     |
| RPT-04  | Report types: regulatory/audit, risk analytics, fraud indicators, parity/drift, reconciliation, portfolio due diligence | High |
| RPT-05  | Support scheduled and on-demand report generation                           | Medium   |
| RPT-06  | Export to CSV, PDF                                                          | Medium   |
| RPT-07  | Reports stored in PostgreSQL `reports` schema                               | High     |

### 8.6 Dashboard API (REST/GraphQL) & Governance Dashboard (Frontend)

The Dashboard API is a **REST/GraphQL service** — NOT an MCP server. The frontend (Governance Dashboard) cannot speak MCP. The Dashboard API reads from PostgreSQL directly and calls Ledger MCP for on-chain data when needed.

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| FE-01   | Dashboard API exposes REST endpoints: validation summary, lifecycle view, drift alerts, discrepancies | High |
| FE-02   | Frontend: validation status view (accepted / rejected / quarantined)        | High     |
| FE-03   | Frontend: contract lifecycle timeline                                       | High     |
| FE-04   | Frontend: cross-system discrepancy view                                     | High     |
| FE-05   | Frontend: parity & drift alerts (migration period)                          | High     |
| FE-06   | Frontend: audit trail viewer with blockchain verification                   | High     |
| FE-07   | Frontend: report viewer and export                                          | High     |
| FE-08   | Frontend: human review queue for quarantined events                         | High     |
| FE-09   | Frontend: identity-aware login with role AND party context. Operational roles (admin, auditor, operator, compliance) see role-scoped views. Party users (borrower, dealer) see only their own contracts with field-level filtering per Section 6.5.3. | High |
| FE-10   | Frontend: quarantine aging metrics and SLA tracking                         | Medium   |

---

## 9. Requirements — Simulated Systems (MCP Servers)

### 9.1 General Simulator Requirements

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| SGEN-01 | Each simulated system is an **MCP server** exposing tools the AI agent can call | **Critical** |
| SGEN-02 | Simulators also **publish events to the event bus** when things happen       | **Critical** |
| SGEN-03 | Simulators produce realistic, schema-validated data (validated against schema registry) | High |
| SGEN-04 | Simulators support scenario-based operation (happy path, error cases, edge cases) | High |
| SGEN-05 | Each simulator runs independently as its own MCP server process             | Medium   |
| SGEN-06 | Support configurable data generation rates                                  | Low      |
| SGEN-07 | Support seeded/deterministic mode for reproducible testing                  | Medium   |
| SGEN-08 | Simulator → real system swap via environment variable (MCP URL config)       | High     |

### 9.2 Simulated MCP Server Tools

| MCP Server             | Tools Exposed to Agent                                                     |
|------------------------|----------------------------------------------------------------------------|
| **Oracle LOS**         | `get_contract(id)`, `get_pricing_output(id)`, `get_blaze_decision(id)`, `list_events(filters)` |
| **Salesforce LOS**     | `get_contract(id)`, `get_logic_output(id)`, `list_events(filters)`         |
| **LLAS (Accounting)**  | `get_account(id)`, `get_balance(id)`, `get_payment_history(id)`, `get_fees(id)`, `get_delinquency_status(id)` |
| **CRM**                | `get_customer(id)`, `get_risk_indicators(id)`                              |
| **Payment System**     | `get_payment(id)`, `list_payments(contract_id)`, `get_settlement(id)`      |
| **Insurance System**   | `get_policy_status(id)`, `verify_insurance(contract_id)`, `list_events(filters)` |
| **Dealer System**      | `get_submission(id)`, `list_submissions(filters)`                          |
| **Customer Portal (Web)** | `get_account_summary(id)`, `get_payment_schedule(id)`, `get_payment_history(id)`, `submit_payment(payment)`, `get_payoff_quote(id)`, `get_documents(id)` |
| **Mobile App**         | `get_account_summary(id)`, `submit_payment(payment)`, `get_notifications(customer_id)` |
| **IVR System**         | `get_account_status(id)`, `get_balance_due(id)`, `submit_phone_payment(payment)`, `request_callback(customer_id)`, `get_payoff_amount(id)` |

### 9.3 Simulated Validation Scenarios

| ID      | Scenario                              | Expected Agent Behavior                                 |
|---------|---------------------------------------|---------------------------------------------------------|
| SVAL-01 | Valid contract lifecycle (happy path)  | Agent validates and writes full lifecycle to ledger      |
| SVAL-02 | Payment doesn't match contract terms   | Agent detects mismatch via cross-system calls, rejects   |
| SVAL-03 | Accounting balance doesn't match origination | Agent detects discrepancy, quarantines             |
| SVAL-04 | Duplicate event submission             | Agent detects duplicate via idempotency check, rejects   |
| SVAL-05 | Out-of-sequence event                  | Agent detects wrong state via lifecycle query, rejects   |
| SVAL-06 | Oracle and Salesforce disagree         | Agent detects parity drift, flags for review             |
| SVAL-07 | Insurance lapse mid-contract           | Agent validates state change, writes with alert          |
| SVAL-08 | Early termination / payoff             | Agent validates, updates lifecycle state                 |
| SVAL-09 | Event with missing required fields     | Agent rejects at schema level before validation          |
| SVAL-10 | Valid event requiring override         | Agent quarantines, presents to human, awaits approval    |

---

## 10. Configuration Management

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| CFG-01  | Validation rules stored in PostgreSQL `validation` schema                   | High     |
| CFG-02  | All rule changes are versioned (append-only — old versions never deleted)   | High     |
| CFG-03  | Rule changes require approval workflow                                      | Medium   |
| CFG-04  | Rollback = activate a previous rule version                                 | High     |
| CFG-05  | Fee schedules and rate tables: same versioned approach                      | High     |
| CFG-06  | Validation Engine MCP exposes: `get_validation_rules`, `update_rule`, `get_rule_history` | High |
| CFG-07  | Agent mode (`read_only` / `active`) configurable via environment variable   | High     |
| CFG-08  | MCP server URLs configurable via environment variables (enables simulator → real swap) | High |

---

## 11. Versioning Strategy

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| VER-01  | MCP tools are **additive only** — new optional fields can be added, existing fields cannot be removed or renamed | High |
| VER-02  | When breaking changes are necessary: new tool version alongside old (e.g., `get_contract_v2`) with deprecation period | High |
| VER-03  | Smart contracts versioned using Hyperledger Fabric native chaincode upgrade mechanism | High |
| VER-04  | Each schema in `schemas/` has a version field                               | High     |
| VER-05  | MCP servers declare which schema version they support                       | Medium   |

---

## 12. Testing Strategy

| Layer              | What                                                    | Tools                  |
|--------------------|---------------------------------------------------------|------------------------|
| **Unit tests**     | Each MCP server's internal logic (validation rules, schema parsing) | Jest / Pytest |
| **Contract tests** | Verify MCP tool request/response schemas match between producer and consumer | Pact or custom schema validation |
| **Integration tests** | Agent + one MCP server at a time (mock the rest)     | Test harness with mock MCP servers |
| **E2E tests**      | Full flow: event → agent → all MCP servers → ledger → report. The SVAL scenarios (SVAL-01 through SVAL-10) are the E2E test suite. | Docker Compose with all 13 services |
| **Chaos tests**    | Randomly kill MCP servers mid-flow, verify saga recovery | Phase 2              |
| **Performance tests** | Load testing against throughput targets               | Phase 2              |

---

## 13. Performance Requirements

| Metric                                | Target (Phase 1) | Target (Phase 2) |
|---------------------------------------|-------------------|-------------------|
| Event throughput                      | 100 events/min    | 500 events/min    |
| End-to-end latency (event → ledger)  | < 5 seconds       | < 2 seconds       |
| Validation Engine response time       | < 500ms           | < 200ms           |
| Ledger query response time            | < 1 second        | < 500ms           |
| Report generation                     | < 30 seconds      | < 10 seconds      |
| Dashboard concurrent users            | 10                | 50                |
| Storage growth (estimated)            | ~10 GB/year       | ~50 GB/year       |

---

## 14. Disaster Recovery & Backup

| Component            | Backup Strategy                                              | RPO       | RTO       |
|----------------------|--------------------------------------------------------------|-----------|-----------|
| PostgreSQL           | Daily automated backup + WAL archiving (point-in-time recovery) | 1 hour | 4 hours   |
| Hyperledger Fabric   | Peer replication (built-in); weekly state snapshots          | Near-zero | 1 hour    |
| Redis Streams        | AOF persistence + daily RDB snapshots                        | 1 minute  | 30 minutes|
| Agent config         | Stored in PostgreSQL + version controlled in git             | —         | —         |
| Schema registry      | Version controlled in git repo                               | —         | —         |
| Validation rules     | Stored in PostgreSQL (versioned, append-only)                | 1 hour    | 4 hours   |

---

## 15. Regulatory Compliance

| ID      | Requirement                                                                 | Priority |
|---------|-----------------------------------------------------------------------------|----------|
| REG-01  | PII stored ONLY in off-chain PostgreSQL. On-chain: only cryptographic hashes. | **Critical** |
| REG-02  | Right-to-delete (CCPA/GDPR): delete PII from PostgreSQL, mark record as `DELETED_PER_REGULATION`. On-chain hash remains but proves nothing without source data. | High |
| REG-03  | Data retention policies defined per data type in configuration              | High     |
| REG-04  | Automated purge job for expired off-chain data                              | Medium   |
| REG-05  | TILA and ECOA compliance: Reporting MCP generates required regulatory formats | Medium |
| REG-06  | Full audit trail for all data access (who accessed what, when) for regulatory review. Implemented via `audit.access_log` — see PBAC-19 through PBAC-23 in Section 6.5.5. | High |

---

## 16. Environment Strategy

| Environment    | Purpose                          | Data                           | Agent Mode    |
|----------------|----------------------------------|--------------------------------|---------------|
| `local`        | Developer machine (Docker Compose)| Synthetic test data            | `active`      |
| `staging`      | Pre-production, full stack       | Anonymized production-like data | `active`      |
| `production`   | Live                             | Real data                      | Configurable  |

**Phase enforcement:**
- Agent `mode` config: `read_only` (Phase 0) or `active` (Phase 1+)
- In `read_only` mode: agent runs full flow but logs what it WOULD do; does not call `write_record`
- Ledger MCP has independent **write guard** — even if agent attempts write in Phase 0, ledger rejects it (defense in depth)
- Simulator → real system swap: change MCP server URL via environment variable; agent code unchanged

---

## 17. Enterprise Governance & Risk Mitigation

| Risk Area               | Challenge                                    | Mitigation                                                    |
|-------------------------|----------------------------------------------|---------------------------------------------------------------|
| Data Integrity Risk     | Systems pushing unvalidated data             | AI Agent + Validation Engine reject ALL unvalidated writes. Validation proof token required for ledger writes. |
| Concurrency Risk        | Simultaneous events for same contract        | Per-contract distributed locks + Hyperledger MVCC as safety net |
| Failure Risk            | Agent crash mid-flow                         | Saga pattern with persistent checkpoints; resume on restart    |
| Security Risk           | Unauthorized ledger access or data exposure  | JWT auth + validation proof tokens + MCP authorization matrix + Smart Data Gateway party-based access control (Section 6.5). No direct Fabric/DB access. |
| Regulatory Risk         | Blockchain immutability vs. Right-to-delete  | PII off-chain only; on-chain hashes; deletion workflow         |
| AI Accuracy Risk        | AI misinterpreting legal clauses             | Human review gates, confidence thresholds, audit workflows     |
| AI Agent Risk           | Agent making wrong decisions                 | Full MCP call trace; human-in-the-loop; all decisions auditable |
| Integration Risk        | Bridging legacy systems to modern ledger     | MCP abstraction — swap implementations without changing agent  |
| Configuration Risk      | Bad validation rule deployed                 | Versioned rules, approval workflow, instant rollback           |
| AI Traceability         | Proving AI decisions are auditable           | MCP call logging + saga trails = full action transparency. Formalized as DAGT protocol in Phase 2. |

---

## 18. Deployment Phases

### Phase 0 — Migration Safety Net (Read-Only)
- **Goal**: Observe and validate contracts securely
- **Agent mode**: `read_only`
- **Build**: AI Agent (read-only), Validation Engine MCP, Ledger MCP (write guard ON), Event Bus, PostgreSQL, simulated LOS MCP servers
- **Outcome**: Confidence that Salesforce rollout behaves identically to legacy LOS

### Phase 1 — Active Validated Ledger
- **Goal**: Enforce the validation gate; write only validated events to the immutable ledger
- **Agent mode**: `active`
- **Build**: Full agent orchestration, all core MCP servers, all simulated MCP servers, Reporting MCP, saga/idempotency, security model, observability
- **Outcome**: Single, undeniable, validated lifecycle view regardless of origination source

### Phase 2 — Steady-State Permanent Intelligence
- **Goal**: Post-migration permanent platform
- **Build**: Advanced reporting, full Governance Dashboard, predictive analytics, chaos testing, performance tuning, DAGT protocol formalization
- **Outcome**: Permanent contract intelligence platform that outlives any single origination tool

---

## 19. Tech Stack (Locked — POC)

### Core Decisions

| Layer                           | Decision                                            | Rationale                                                                                           |
|---------------------------------|-----------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| **AI Agent**                    | Custom agent (Anthropic API + MCP Python SDK)       | Maximum control over saga pattern, per-contract locks, idempotency, proof tokens. No framework abstraction fighting the architecture. |
| **MCP Framework**               | MCP Python SDK v1.7.1 (FastMCP for servers)         | Native MCP client + server support. FastMCP reduces MCP server boilerplate.                         |
| **Agent Language**              | Python 3.12                                         | Consistent with all backend services. Strong async support (asyncio).                               |
| **MCP Server Language**         | Python 3.12                                         | All MCP servers (Validation, Ledger, Semantic AI, Reporting, Simulated) built in Python.            |
| **Event Bus**                   | Redis Streams                                       | Ordered delivery, persistence, consumer groups, DLQ. Phase 2: evaluate Kafka.                      |
| **Concurrency Locks**           | Redis distributed locks (Redlock pattern)           | Per-contract sequential processing; parallel across contracts.                                      |
| **Blockchain**                  | Hyperledger Fabric                                  | Permissioned blockchain. Phase 0: write guard ON (PostgreSQL only). Phase 1+: Fabric writes enabled. |
| **Chaincode (Smart Contracts)** | Node.js                                             | Fabric SDK maturity for Node.js chaincode.                                                          |
| **Database (off-chain)**        | PostgreSQL 16                                       | Contracts, saga checkpoints, validation state, audit, reports, idempotency dedup table.             |
| **Schema Validation**           | JSON Schema + Pydantic v2                           | JSON Schema files in `shared/schemas/`; Pydantic models for runtime validation across all services. |
| **Frontend (Dashboard)**        | Next.js (App Router)                                | React-based, SSR/SSG, API routes built in.                                                          |
| **Dashboard API**               | FastAPI (Python)                                    | REST/GraphQL. Reads from PostgreSQL + Ledger MCP. Serves Dashboard UI.                              |
| **Semantic AI Engine**          | Claude API (claude-3-5-sonnet) + Python             | Field extraction from contract PDFs. Confidence scoring.                                            |
| **Observability**               | Structured JSON logs + OpenTelemetry                | Phase 1: structured logs + basic metrics. Phase 2: Loki/ELK + Prometheus + Grafana.                 |
| **Simulators**                  | Python (as MCP servers)                             | Oracle LOS, Salesforce LOS, LLAS, CRM, Payment, Insurance, Dealer, Customer Portal, Mobile App, IVR. |
| **DevOps**                      | Docker + Docker Compose                             | Local dev environment; all services containerized.                                                  |

### Package & Dependency Management

| Ecosystem  | Tool    | Installation        | Purpose                                                                    |
|------------|---------|---------------------|----------------------------------------------------------------------------|
| System     | Homebrew | Pre-installed       | Installs all system-level tools (Python, Node, uv, pnpm, Redis, PostgreSQL, Docker) |
| Python     | `uv`    | `brew install uv`   | Virtual envs, dependency resolution, monorepo workspace packages, lockfiles |
| JavaScript | `pnpm`  | `brew install pnpm` | Next.js dashboard-ui, Node.js chaincode                                    |

### Repository & Project Structure

| Decision             | Choice      | Rationale                                                                      |
|----------------------|-------------|--------------------------------------------------------------------------------|
| **Repo layout**      | Monorepo    | Single repo; cross-service changes in one PR; shared schemas imported directly |
| **Folder layout**    | Hybrid (C)  | `src/` for all Python packages, `apps/` for Next.js + chaincode, `infra/` for Docker/Fabric |
| **Containerization** | Docker + Docker Compose | All services containerized; `docker-compose.yml` spins up full local stack |

### MVP Scope (POC Build Target)

**MVP-3: Full Stack Demo** — Full stack POC, end-to-end

| Area | Scope |
|---|---|
| **Origination happy path** | Oracle LOS (simulated) → Redis Streams → Agent → Validation MCP → Ledger MCP |
| **Unhappy path** | Validation failure → quarantine → Dashboard review queue → human override → retry |
| **Payment flow** | Payment system (simulated) + Customer Portal/Mobile/IVR payments → Agent → Ledger |
| **Semantic AI** | PDF contract ingestion → field extraction → confidence scoring → human review if low confidence |
| **Blockchain** | Hyperledger Fabric live writes (Phase 1 — write guard OFF for POC demo) |
| **All simulated systems** | All 10 simulated MCP servers: Oracle LOS, Salesforce LOS, LLAS, CRM, Payment, Insurance, Dealer, Customer Portal, Mobile App, IVR |
| **Saga + resilience** | Checkpoints at each step; crash recovery tested; per-contract locks; idempotency dedup |
| **Full Dashboard UI** | Contract lifecycle view, validation queue, quarantined items, audit trail, basic reporting |
| **Reporting** | At least 1 end-to-end report: contract origination summary |
| **Security (Phase 1)** | JWT auth on all MCP servers; validation proof tokens enforced |

---

## 20. Domain Glossary

| Term                  | Definition                                                                      |
|-----------------------|---------------------------------------------------------------------------------|
| MCP                   | Model Context Protocol — standardized protocol for AI agents to connect to tools and data sources |
| MCP Server            | A service that exposes tools (functions) via MCP for an AI agent to call         |
| MCP Client            | The AI agent that connects to MCP servers and calls their tools                  |
| AI Agent              | The central orchestrator that drives SmartLedger flows using MCP                 |
| Event Bus             | Message broker (Redis Streams) that delivers events from external systems to the agent |
| Event Envelope        | Standard format for all events: event_id, event_type, source_system, contract_id, timestamp, correlation_id, payload |
| Saga                  | A multi-step flow with persistent checkpoints that can be resumed after failure  |
| Saga Checkpoint       | A saved point in a saga's progress (EVENT_RECEIVED, CONTEXT_GATHERED, etc.)     |
| Idempotency           | The guarantee that processing the same event twice produces the same result      |
| Validation Proof Token| Single-use, time-limited token issued by the Validation Engine proving data was validated. Required for ledger writes. |
| Validation Gate       | The principle that no data is written without full validation                    |
| Quarantine            | Holding area for events that fail validation, pending human review               |
| Dead Letter Queue     | Topic for events that failed after maximum retries, requiring manual investigation |
| Write Guard           | Configurable flag on the Ledger MCP to disable all writes (Phase 0 enforcement) |
| LOS                   | Loan Origination System — where auto finance contracts are created               |
| LLAS                  | Loan/Lease Ledger Accounting System — tracks balances, payments, fees            |
| CRM                   | Customer Relationship Management — customer profiles and relationship data       |
| Parity Analysis       | Comparing Oracle LOS vs Salesforce LOS outputs (migration period)               |
| Policy Drift          | Subtle differences in business logic between legacy and modern systems           |
| VIN                   | Vehicle Identification Number                                                    |
| Blaze Rules           | Legacy business rules engine in Oracle LOS                                       |
| DAGT Protocol         | Data Access Governance Technology — protocol governing AI tool usage, data access, and action transparency. Phase 1: implemented as the Smart Data Gateway (Section 6.5) with party-based access control, field-level filtering, and access audit logging. Phase 2: formalized protocol specification with Fabric MSP integration. |
| Chaincode             | Smart contract code running on Hyperledger Fabric                                |
| MVCC                  | Multi-Version Concurrency Control — Hyperledger's built-in mechanism to reject conflicting transactions |
| RPO                   | Recovery Point Objective — maximum acceptable data loss measured in time         |
| RTO                   | Recovery Time Objective — maximum acceptable downtime after a failure            |
| Smart Data Gateway    | The MCP layer + Dashboard API acting as the single access control enforcement point. All reads flow through the Gateway, which enforces party-based and role-based access (Section 6.5). |
| Contract Party        | An entity with a legitimate interest in a contract's data (borrower, lender, dealer, servicer, insurer). Recorded in `contracts.parties`. |
| Field-Level Filtering | The Gateway strips fields from responses based on the caller's party role or operational role per the visibility matrix in Section 6.5.3. |
| Access Tier           | Classification of how a caller accesses data: party access (relationship to contract), operational role (admin/auditor/etc.), or system access (MCP service). |
