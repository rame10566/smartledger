# SmartLedger — Architecture

---

## 1. System Overview

```mermaid
graph TB
    subgraph EXTERNAL["External Systems (Simulated MCP Servers)"]
        OL[Oracle LOS<br/>:8010]
        SF[Salesforce LOS<br/>:8011]
        LL[LLAS Accounting<br/>:8012]
        CR[CRM<br/>:8013]
        PM[Payment<br/>:8014]
        IN[Insurance<br/>:8015]
        DE[Dealer<br/>:8016]
        CP[Customer Portal<br/>:8017]
        MA[Mobile App<br/>:8018]
        IV[IVR System<br/>:8019]
    end

    subgraph BUS["Event Bus"]
        RS[(Redis Streams)]
    end

    subgraph CORE["SmartLedger Core (We Build)"]
        AG[AI Agent<br/>Orchestrator]
        VA[Validation Engine<br/>MCP :8001]
        LE[Immutable Ledger<br/>MCP :8002]
        SA[Semantic AI<br/>MCP :8003]
        RE[Reporting<br/>MCP :8004]
    end

    subgraph FRONTEND["Dashboard"]
        DA[Dashboard API<br/>FastAPI :8000]
        UI[Governance Dashboard<br/>Next.js :3000]
    end

    subgraph INFRA["Infrastructure"]
        PG[(PostgreSQL 16<br/>off-chain store)]
        RD[(Redis<br/>locks + dedup)]
        FA[(Hyperledger Fabric<br/>on-chain ledger)]
    end

    EXTERNAL -->|publish events| RS
    RS -->|consume events| AG
    AG <-->|MCP calls| VA
    AG <-->|MCP calls| LE
    AG <-->|MCP calls| SA
    AG <-->|MCP calls| RE
    AG <-->|context queries| EXTERNAL

    VA --> PG
    LE --> PG
    LE --> FA
    AG --> PG
    AG --> RD
    RE --> PG
    SA --> PG

    DA --> PG
    DA <-->|read ledger| LE
    UI --> DA

    style EXTERNAL fill:#e8f4f8,stroke:#4a9ebe
    style CORE fill:#e8f8e8,stroke:#4abe4a
    style FRONTEND fill:#f8f4e8,stroke:#be9e4a
    style INFRA fill:#f8e8e8,stroke:#be4a4a
    style BUS fill:#f4e8f8,stroke:#9e4abe
```

---

## 2. Agent Event Loop

```mermaid
sequenceDiagram
    participant Bus as Redis Streams
    participant Agent as AI Agent
    participant Lock as Redis Lock
    participant Saga as Saga (PostgreSQL)
    participant Flow as Flow Handler

    Bus->>Agent: XREADGROUP — new event
    Agent->>Saga: Check idempotency (event_id)
    alt Already processed
        Agent->>Bus: XACK (skip)
    else Not seen
        Agent->>Lock: SETNX contract:{id} (60s TTL)
        alt Lock acquired
            Agent->>Saga: Checkpoint: EVENT_RECEIVED
            Agent->>Flow: dispatch(event)
            Flow-->>Agent: result (written | quarantined | failed)
            Agent->>Saga: Checkpoint: COMPLETED / QUARANTINED
            Agent->>Lock: DEL contract:{id}
            Agent->>Bus: XACK
        else Lock not acquired
            Agent->>Bus: NACK + delay retry
        end
    end
```

---

## 3. Contract Origination — Happy Path

```mermaid
sequenceDiagram
    participant OL as Oracle LOS Sim
    participant Bus as Redis Streams
    participant Agent as AI Agent
    participant LLAS as LLAS Sim
    participant VAL as Validation Engine
    participant LED as Ledger MCP
    participant PG as PostgreSQL

    OL->>Bus: publish contract.originated
    Bus->>Agent: deliver event

    Note over Agent: Acquire per-contract lock
    Note over Agent: Checkpoint: CONTEXT_GATHERED

    Agent->>OL: get_contract(id)
    OL-->>Agent: Oracle contract data
    Agent->>LLAS: get_account(id)
    LLAS-->>Agent: LLAS account data

    Note over Agent: Checkpoint: CONTEXT_GATHERED

    Agent->>VAL: validate_event(event + context)
    VAL->>VAL: schema check
    VAL->>VAL: cross-system check (Oracle vs LLAS)
    VAL->>VAL: business rules check
    VAL->>PG: store proof token (jti)
    VAL-->>Agent: ValidationResult(valid=true, proof_token=JWT)

    Note over Agent: Checkpoint: VALIDATED

    Agent->>LED: write_record(origination_record, proof_token)
    LED->>LED: verify JWT signature
    LED->>LED: check jti not used
    LED->>PG: INSERT contracts.records
    LED->>PG: INSERT validation.used_proof_tokens (jti)
    LED-->>Agent: RecordWritten(record_id, fabric_tx_id)

    Note over Agent: Checkpoint: LEDGER_WRITTEN

    Agent->>LED: execute_state_transition(contract_id, "ORIGINATED→ACTIVE")
    LED-->>Agent: StateTransitioned

    Note over Agent: Checkpoint: COMPLETED
    Note over Agent: Release lock + ACK event
```

---

## 4. Contract Origination — Unhappy Path (Quarantine + Override)

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant VAL as Validation Engine
    participant Bus as Redis Streams
    participant PG as PostgreSQL
    participant DA as Dashboard API
    participant HU as Human Reviewer
    participant UI as Dashboard UI

    Agent->>VAL: validate_event(event + context)
    VAL->>VAL: ❌ VIN_MISMATCH detected
    VAL->>PG: INSERT validation.quarantine
    VAL-->>Agent: ValidationResult(valid=false, failures=[...])

    Note over Agent: Checkpoint: QUARANTINED
    Agent->>Bus: publish quarantine.pending

    Note over HU,UI: Human Review (Dashboard)
    UI->>DA: GET /api/quarantine (polling)
    DA->>PG: SELECT validation.quarantine WHERE status='pending'
    DA-->>UI: quarantine list with context + rejection reasons
    UI-->>HU: Shows: event data, cross-system diff, rejection reason

    alt Human APPROVES override
        HU->>UI: click Approve + enter reason
        UI->>DA: POST /api/quarantine/{id}/approve
        DA->>VAL: approve_override(event_id, reason, reviewer)
        VAL->>PG: UPDATE quarantine SET status='approved'
        VAL->>Bus: publish quarantine.approved

        Bus->>Agent: deliver quarantine.approved
        Note over Agent: Resume saga from QUARANTINED checkpoint
        Agent->>VAL: validate_event(..., override=true)
        VAL-->>Agent: ValidationResult(valid=true, proof_token=JWT, override_flag=true)
        Agent->>LED: write_record(record, proof_token) + override audit trail
    else Human REJECTS
        HU->>UI: click Reject + enter reason
        UI->>DA: POST /api/quarantine/{id}/reject
        DA->>PG: UPDATE quarantine SET status='rejected'
        Note over Agent: Event permanently discarded
    end
```

---

## 5. Validation Proof Token Flow

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant VAL as Validation Engine
    participant LED as Ledger MCP
    participant PG as PostgreSQL

    Note over Agent,VAL: After successful validation...
    VAL->>VAL: Generate JWT:<br/>jti=uuid, contract_id, event_id,<br/>saga_id, iat, exp=iat+60s
    VAL->>VAL: Sign with PROOF_TOKEN_SECRET (HS256)
    VAL->>PG: store jti in validation.used_proof_tokens<br/>(with expires_at)
    VAL-->>Agent: proof_token (JWT string)

    Note over Agent,LED: Agent calls write_record...
    Agent->>LED: write_record(record, proof_token)
    LED->>LED: 1. Verify JWT signature
    LED->>LED: 2. Check exp not expired
    LED->>LED: 3. Check contract_id claim == record.contract_id
    LED->>PG: 4. SELECT FROM validation.used_proof_tokens WHERE jti=?
    alt jti already used
        LED-->>Agent: ❌ Error: PROOF_TOKEN_ALREADY_USED
    else jti not used + all checks pass
        LED->>PG: INSERT contracts.records (with proof_token_jti)
        LED->>PG: INSERT validation.used_proof_tokens (mark used)
        LED-->>Agent: ✅ RecordWritten
    end
```

---

## 6. Saga Crash Recovery

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant PG as PostgreSQL
    participant VAL as Validation Engine
    participant LED as Ledger MCP

    Note over Agent: Agent crashes here...
    Note over Agent: (after VALIDATED, before LEDGER_WRITTEN)

    Note over Agent: Agent restarts
    Agent->>PG: SELECT * FROM sagas.checkpoints<br/>WHERE status='in_progress'
    PG-->>Agent: [saga_id, contract_id, last_step=VALIDATED, payload={proof_token}]

    Note over Agent: Resume from VALIDATED checkpoint
    Note over Agent: proof_token still valid? Check exp...

    alt Proof token still valid (within 60s)
        Agent->>LED: write_record(record, proof_token) — resume
    else Proof token expired
        Agent->>VAL: validate_event(...) — re-validate to get new token
        Agent->>LED: write_record(record, new_proof_token)
    end

    Agent->>PG: Checkpoint: LEDGER_WRITTEN
    Agent->>PG: Checkpoint: COMPLETED
```

---

## 7. Contract State Machine

```mermaid
stateDiagram-v2
    [*] --> ORIGINATED: contract.originated event validated + written

    ORIGINATED --> ACTIVE: Insurance verified + funding confirmed
    ACTIVE --> DELINQUENT: payment.missed (days_past_due > 0)
    DELINQUENT --> ACTIVE: payment.received (catches up)
    ACTIVE --> PAID_OFF: payoff payment received + balance = 0
    DELINQUENT --> CHARGED_OFF: days_past_due > 180
    ACTIVE --> IN_REPOSSESSION: repossession initiated
    DELINQUENT --> IN_REPOSSESSION: repossession initiated
    IN_REPOSSESSION --> CHARGED_OFF: repossession completed
    PAID_OFF --> TITLE_RELEASED: title release conditions met
    CHARGED_OFF --> [*]: End of lifecycle

    note right of ORIGINATED: Write guard ON = stops here (Phase 0)
    note right of ACTIVE: Most events happen here
    note right of TITLE_RELEASED: Final state — contract complete
```

---

## 8. PostgreSQL Schema Layout

```mermaid
erDiagram
    contracts_documents {
        text contract_id PK
        text los_system
        text contract_type
        date origination_date
        text customer_id
        text customer_name "PII"
        text vin
        numeric amount_financed
        integer term_months
        text data_hash
        boolean deleted_per_regulation
    }

    contracts_records {
        uuid record_id PK
        text contract_id FK
        text record_type
        jsonb payload
        text data_hash
        text proof_token_jti
        text fabric_tx_id
    }

    contracts_state {
        text contract_id PK
        text current_state
        integer days_past_due
    }

    sagas_checkpoints {
        bigint id PK
        uuid saga_id
        text contract_id
        uuid event_id
        text step
        text status
        jsonb payload
    }

    sagas_processed_events {
        uuid event_id PK
        uuid saga_id
        text outcome
    }

    validation_quarantine {
        bigint id PK
        uuid event_id
        text contract_id
        text rejection_code
        text status
        timestamptz sla_deadline
    }

    validation_used_proof_tokens {
        text jti PK
        text contract_id
        uuid event_id
        timestamptz expires_at
    }

    contracts_documents ||--o{ contracts_records : "contract_id"
    contracts_documents ||--o| contracts_state : "contract_id"
    sagas_checkpoints }o--|| sagas_processed_events : "event_id"
```

---

## 9. MCP Server Port Map

| Service | Port | Type |
|---|---|---|
| Dashboard API | 8000 | REST (FastAPI) |
| Validation Engine MCP | 8001 | MCP (streamable-http) |
| Immutable Ledger MCP | 8002 | MCP (streamable-http) |
| Semantic AI MCP | 8003 | MCP (streamable-http) |
| Reporting MCP | 8004 | MCP (streamable-http) |
| Oracle LOS (sim) | 8010 | MCP (streamable-http) |
| Salesforce LOS (sim) | 8011 | MCP (streamable-http) |
| LLAS (sim) | 8012 | MCP (streamable-http) |
| CRM (sim) | 8013 | MCP (streamable-http) |
| Payment (sim) | 8014 | MCP (streamable-http) |
| Insurance (sim) | 8015 | MCP (streamable-http) |
| Dealer (sim) | 8016 | MCP (streamable-http) |
| Customer Portal (sim) | 8017 | MCP (streamable-http) |
| Mobile App (sim) | 8018 | MCP (streamable-http) |
| IVR (sim) | 8019 | MCP (streamable-http) |
| Dashboard UI | 3000 | Next.js |
| PostgreSQL | 5432 | Database |
| Redis | 6379 | Cache + Streams |
