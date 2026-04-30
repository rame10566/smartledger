# SmartLedger — Claude Code Project Context

> Validation-gated, immutable ledger for auto/vehicle finance.
> **"Systems change. Platforms evolve. Contracts persist."**

**Status:** POC complete. All originating systems simulated, MCP servers built, end-to-end testing done. Now in light-maintenance / minor-adjustment mode.

For full detail see `README.md` and `REQUIREMENTS.md` (the latter is the source of truth — do not duplicate it; read it when needed).

---

## What This System Does

Multiple enterprise systems (LOS, accounting, payments, CRM, insurance, dealer platforms) freely write loan/lease records without cross-validating. SmartLedger enforces one rule:

> **No data is written to the ledger unless it has been fully validated across all relevant systems and business rules.**

An AI Agent orchestrates the flow via MCP, runs cross-system validation, and writes only validated data to an immutable Hyperledger Fabric ledger. Failed validations go to a **quarantine** workflow.

**Secondary purpose:** Oracle LOS → Salesforce LOS migration bridge — runs both in parallel and detects policy drift.

---

## Architecture (one-screen view)

```
Simulated source MCP servers (Oracle LOS, Salesforce LOS, LLAS, CRM, Payment,
Insurance, Dealer, Customer Portal, Mobile, IVR, Rules, Pricing, Integration)
        │ events
        ▼
Redis Streams ──► AI Agent (orchestrator)
                    │  MCP calls
                    ▼
        Validation MCP │ Ledger MCP │ Semantic AI MCP │ Reporting MCP
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
  PostgreSQL (off-chain)   Hyperledger Fabric (on-chain)
                           + Explorer @ :8090
```

**Key patterns in use:** saga orchestration, FastMCP, validation gates, proof tokens, two-layer Postgres (working + immutable), quarantine workflow for failed validations.

---

## Stack

- Python 3.12, **uv workspace** (see `pyproject.toml` for the 9 members under `src/`)
- FastMCP for MCP servers
- PostgreSQL (off-chain working layer)
- Redis Streams (event bus + locks + dedup)
- Hyperledger Fabric (on-chain immutable ledger)
- Hyperledger Explorer (port 8090)
- Docker Compose for local infra
- pytest + pytest-asyncio (async mode)
- ruff (line-length 100, target py312), mypy strict

---

## Common Commands

**Run / status:**
```bash
make help
make status
docker compose up -d
```

**Tests (always set PYTHONPATH=src for direct pytest):**
```bash
PYTHONPATH=src uv run pytest
PYTHONPATH=src uv run pytest tests/e2e/test_origination_happy_path.py -v --tb=short
```

**Postgres quick check:**
```bash
docker exec smartledger-postgres psql -U smartledger -d smartledger -c "SELECT 1"
```

**MCP server smoke test (example, port 8010):**
```bash
curl -s -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -X POST http://localhost:8010/mcp \
     -d '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{...}}'
```

**Quarantine API (port 8000) — requires identity header:**
```bash
curl -s -H 'X-SmartLedger-Identity: {"actor_id":"test","role":"admin"}' \
     "http://localhost:8000/api/quarantine?status=pending"
```

**Fabric Explorer:** http://localhost:8090 (login `exploreradmin` / `exploreradminpw`, network `smartledger-network`)

---

## Project Layout

```
apps/                    # frontends / dashboard
src/
  shared/                # cross-cutting: types, schemas, utils
  agent/                 # AI Agent orchestrator
  event_bus/             # Redis Streams wiring
  dashboard_api/         # quarantine + ops API (port 8000)
  mcp_servers/
    validation/          # validation engine MCP
    ledger/              # immutable ledger MCP (Fabric)
    semantic_ai/         # semantic checks
    reporting/           # reporting MCP
    simulated/           # all simulated source systems
infra/
  docker/                # Dockerfiles
  fabric/                # Fabric network + Explorer scripts
docs/                    # architecture diagrams, design notes
tests/
  e2e/                   # marked @pytest.mark.e2e — needs live infra
```

---

## Conventions & Rules

**Always:**
- Validate before writing. The validation gate is non-negotiable — any new write path must go through Validation MCP first.
- Failed validations → quarantine, never silently dropped.
- Use `uv run` for Python execution, not bare `python`/`python3`.
- Set `PYTHONPATH=src` when invoking pytest directly.
- Keep code under `ruff` (line-length 100) and `mypy --strict`.

**Never:**
- Bypass validation to write directly to Fabric or Postgres.
- Add a new originating system without a corresponding simulated MCP server in `src/mcp_servers/simulated/`.
- Commit `.env` (it's gitignored — `.env.example` is the template).
- Modify Fabric chaincode without a corresponding Postgres migration plan and vice versa.

**Tests:**
- E2E tests require full infra: Postgres + Redis + all MCP servers running. They're marked `@pytest.mark.e2e`.
- Unit tests should mock MCP calls.

---

## What's Stable vs. What's In Flux

- **Stable:** simulated source systems, MCP server contracts, validation engine, ledger MCP, quarantine workflow, two-layer Postgres design, Fabric network + Explorer.
- **Light adjustments only:** minor tweaks expected — no large architectural changes planned.
- If a request implies redesigning a stable component, surface that explicitly before changing code.

---

## When Asked to Do Something Ambiguous

1. Check `REQUIREMENTS.md` first — it's exhaustive (100KB) and likely answers the question.
2. Check `docs/ARCHITECTURE.md` for design rationale.
3. If still ambiguous, ask before changing code. Do not infer a redesign from a small request.
