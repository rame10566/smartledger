-- SmartLedger PostgreSQL Schema Initialization
-- This runs on first container startup.
-- Schema names match REQUIREMENTS.md Section 4.2 exactly.

-- ─── Schema Separation ───────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS contracts;   -- Full contract documents, PII, validated records, on-chain hash links
CREATE SCHEMA IF NOT EXISTS validation;  -- Validation state, proof tokens, quarantine, rules
CREATE SCHEMA IF NOT EXISTS sagas;       -- Saga checkpoints + idempotency dedup table
CREATE SCHEMA IF NOT EXISTS audit;       -- Full audit trail (agent decisions, MCP call traces, overrides)
CREATE SCHEMA IF NOT EXISTS reports;     -- Generated reports
CREATE SCHEMA IF NOT EXISTS extraction;  -- Semantic AI extraction results + review queue

-- ─── Contracts Schema ────────────────────────────────────────────────────────
-- Two tables:
--   documents  = full contract data including PII (off-chain only, linked to on-chain hash)
--   records    = validated write records (mirrors what goes to Hyperledger Fabric)

CREATE TABLE IF NOT EXISTS contracts.documents (
    id              BIGSERIAL PRIMARY KEY,
    contract_id     TEXT NOT NULL UNIQUE,
    los_system      TEXT NOT NULL,          -- oracle_los | salesforce_los
    contract_type   TEXT NOT NULL,          -- loan | lease
    origination_date DATE NOT NULL,
    -- PII fields (stored here only, never on-chain)
    customer_id     TEXT NOT NULL,
    customer_name   TEXT,                   -- PII
    customer_ssn_encrypted TEXT,            -- PII (encrypted at rest)
    customer_dob    DATE,                   -- PII
    customer_address JSONB,                 -- PII
    -- Vehicle
    vin             TEXT NOT NULL,
    vehicle_make    TEXT,
    vehicle_model   TEXT,
    vehicle_year    INTEGER,
    vehicle_msrp    NUMERIC(15,2),
    -- Financial terms
    amount_financed NUMERIC(15,2),
    term_months     INTEGER,
    interest_rate   NUMERIC(8,4),
    monthly_payment NUMERIC(15,2),
    residual_value  NUMERIC(15,2),          -- lease only
    down_payment    NUMERIC(15,2),
    -- Dealer
    dealer_id       TEXT,
    dealer_name     TEXT,
    -- Integrity
    data_hash       TEXT NOT NULL,          -- SHA-256 of this full record (matches on-chain hash)
    -- Regulatory
    deleted_per_regulation BOOLEAN NOT NULL DEFAULT FALSE,
    deletion_date   TIMESTAMPTZ,
    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contracts_documents_vin ON contracts.documents(vin);
CREATE INDEX IF NOT EXISTS idx_contracts_documents_customer ON contracts.documents(customer_id);

CREATE TABLE IF NOT EXISTS contracts.records (
    id              BIGSERIAL PRIMARY KEY,
    record_id       UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    contract_id     TEXT NOT NULL,
    record_type     TEXT NOT NULL,          -- origination | payment | amendment | state_transition | payoff
    payload         JSONB NOT NULL,         -- validated record payload (no PII)
    data_hash       TEXT NOT NULL,          -- SHA-256 of payload (matches on-chain hash)
    proof_token_jti TEXT,                   -- JWT ID of the proof token used for this write
    fabric_tx_id    TEXT,                   -- populated when Fabric writes are enabled (Phase 1+)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contracts_records_contract_id ON contracts.records(contract_id);
CREATE INDEX IF NOT EXISTS idx_contracts_records_type ON contracts.records(record_type);

CREATE TABLE IF NOT EXISTS contracts.state (
    contract_id     TEXT PRIMARY KEY,
    current_state   TEXT NOT NULL,          -- originated | active | delinquent | paid_off | charged_off | in_repossession | title_released
    previous_state  TEXT,
    state_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    days_past_due   INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Sagas Schema ─────────────────────────────────────────────────────────────

-- Idempotency dedup: every processed event_id goes here to prevent double processing
CREATE TABLE IF NOT EXISTS sagas.processed_events (
    event_id        UUID PRIMARY KEY,
    saga_id         UUID NOT NULL,
    event_type      TEXT NOT NULL,
    contract_id     TEXT NOT NULL,
    source_system   TEXT NOT NULL,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    outcome         TEXT NOT NULL           -- written | quarantined | skipped | failed
);

CREATE INDEX IF NOT EXISTS idx_processed_events_contract ON sagas.processed_events(contract_id);

-- Saga checkpoints: one row per step per saga, enabling crash recovery
CREATE TABLE IF NOT EXISTS sagas.checkpoints (
    id              BIGSERIAL PRIMARY KEY,
    saga_id         UUID NOT NULL,
    contract_id     TEXT NOT NULL,
    event_id        UUID NOT NULL,
    step            TEXT NOT NULL,
    -- Valid steps (origination flow):
    --   EVENT_RECEIVED | LOCK_ACQUIRED | CONTEXT_GATHERED | VALIDATED |
    --   PROOF_TOKEN_ISSUED | LEDGER_WRITTEN | STATE_TRANSITIONED | COMPLETED |
    --   QUARANTINED | FAILED
    status          TEXT NOT NULL,          -- in_progress | completed | failed
    payload         JSONB,                  -- context snapshot at this step
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_saga_id ON sagas.checkpoints(saga_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_contract_id ON sagas.checkpoints(contract_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_incomplete ON sagas.checkpoints(status)
    WHERE status = 'in_progress';

-- ─── Validation Schema ────────────────────────────────────────────────────────

-- Used JWT IDs: prevents proof token replay attacks
-- (JWT is single-use: once a jti appears here, it cannot be used again)
CREATE TABLE IF NOT EXISTS validation.used_proof_tokens (
    jti             TEXT PRIMARY KEY,       -- JWT ID from the proof token
    contract_id     TEXT NOT NULL,
    event_id        UUID NOT NULL,
    used_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL    -- for cleanup (tokens expire in 60s)
);

CREATE TABLE IF NOT EXISTS validation.quarantine (
    id              BIGSERIAL PRIMARY KEY,
    event_id        UUID NOT NULL UNIQUE,
    contract_id     TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    source_system   TEXT NOT NULL,
    rejection_code  TEXT NOT NULL,
    rejection_detail TEXT,
    context_snapshot JSONB,                 -- all cross-system data the agent gathered
    original_payload JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | escalated
    escalation_level INTEGER NOT NULL DEFAULT 0,      -- 0=operator, 1=team_lead, 2=manager
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,
    override_reason TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sla_deadline    TIMESTAMPTZ NOT NULL    -- created_at + 24h for first escalation
);

CREATE INDEX IF NOT EXISTS idx_quarantine_status ON validation.quarantine(status);
CREATE INDEX IF NOT EXISTS idx_quarantine_contract ON validation.quarantine(contract_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_sla ON validation.quarantine(sla_deadline)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS validation.rules (
    id              BIGSERIAL PRIMARY KEY,
    rule_id         TEXT NOT NULL,
    rule_type       TEXT NOT NULL,          -- schema | cross_system | business | sequence | duplicate
    event_type      TEXT,                   -- null = applies to all events
    description     TEXT,
    config          JSONB NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      TEXT,
    UNIQUE(rule_id, version)
);

CREATE INDEX IF NOT EXISTS idx_rules_active ON validation.rules(rule_id) WHERE active = TRUE;

-- ─── Audit Schema ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS audit.log (
    id              BIGSERIAL PRIMARY KEY,
    action          TEXT NOT NULL,          -- event_received | validated | quarantined | ledger_written | override_approved | etc.
    actor           TEXT NOT NULL,          -- agent | user:<user_id> | system
    contract_id     TEXT,
    event_id        UUID,
    saga_id         UUID,
    correlation_id  UUID,
    details         JSONB,                  -- full context: MCP calls made, data compared, decision rationale
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_contract ON audit.log(contract_id);
CREATE INDEX IF NOT EXISTS idx_audit_saga ON audit.log(saga_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit.log(created_at);

-- ─── Reports Schema ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS reports.generated (
    id              BIGSERIAL PRIMARY KEY,
    report_id       UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    report_type     TEXT NOT NULL,          -- regulatory | risk | fraud | parity | reconciliation | portfolio
    title           TEXT NOT NULL,
    parameters      JSONB,
    result          JSONB,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | completed | failed
    requested_by    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- ─── Extraction Schema ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS extraction.results (
    id              BIGSERIAL PRIMARY KEY,
    extraction_id   UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    contract_id     TEXT NOT NULL,
    source_file     TEXT,
    extracted_fields JSONB NOT NULL,
    confidence_scores JSONB NOT NULL,       -- field_name → confidence (0.0–1.0)
    overall_confidence NUMERIC(4,3),        -- aggregate confidence score
    review_status   TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,
    discrepancies   JSONB,                  -- fields that don't match the LOS structured data
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extraction_contract ON extraction.results(contract_id);
CREATE INDEX IF NOT EXISTS idx_extraction_review ON extraction.results(review_status)
    WHERE review_status = 'pending';

-- ─── Seed Data: Validation Rules ──────────────────────────────────────────────
-- These are the active rules used by the Validation Engine for Phase 0.
-- The validation server will also seed these on startup if the table is empty,
-- so this block is a safety net for fresh DB initialisation.

INSERT INTO validation.rules (rule_id, rule_type, event_type, description, config, version, active)
VALUES
    (
        'RULE-SCHEMA-VIN', 'schema', 'contract.originated',
        'VIN must be exactly 17 characters [A-HJ-NPR-Z0-9] (no I, O, or Q)',
        '{"field": "vehicle.vin", "pattern": "^[A-HJ-NPR-Z0-9]{17}$"}',
        1, TRUE
    ),
    (
        'RULE-BIZ-AMT-POS', 'business', 'contract.originated',
        'Amount financed must be greater than zero',
        '{"field": "financial_terms.amount_financed", "min_exclusive": 0}',
        1, TRUE
    ),
    (
        'RULE-BIZ-TERM', 'business', 'contract.originated',
        'Term months must be between 1 and 84',
        '{"field": "financial_terms.term_months", "min": 1, "max": 84}',
        1, TRUE
    ),
    (
        'RULE-BIZ-RATE', 'business', 'contract.originated',
        'Interest rate must be between 0% and 36% APR',
        '{"field": "financial_terms.interest_rate", "min": 0, "max": 36}',
        1, TRUE
    ),
    (
        'RULE-BIZ-PMT', 'business', 'contract.originated',
        'Monthly payment must be greater than zero',
        '{"field": "financial_terms.monthly_payment", "min_exclusive": 0}',
        1, TRUE
    ),
    (
        'RULE-BIZ-DEALER', 'business', 'contract.originated',
        'Dealer ID is required and cannot be empty',
        '{"field": "dealer_id", "required": true}',
        1, TRUE
    ),
    (
        'RULE-XSYS-LOS-VIN', 'cross_system', 'contract.originated',
        'VIN in event payload must match VIN in Oracle LOS contract record',
        '{"check": "vin_match_oracle_los"}',
        1, TRUE
    ),
    (
        'RULE-XSYS-LLAS-NEW', 'cross_system', 'contract.originated',
        'No LLAS account should exist for a newly originated contract',
        '{"check": "no_existing_llas_account"}',
        1, TRUE
    )
ON CONFLICT (rule_id, version) DO NOTHING;
