# SmartLedger (AutoLedger AI)

> **"Systems change. Platforms evolve. Contracts persist."**

A **validation-gated, immutable ledger** for auto/vehicle finance — Loan/Lease Origination and Accounting.

---

## The Problem

Multiple enterprise systems (LOS, accounting, payments, CRM, insurance, dealer platforms) freely update loan and lease records **without validating against each other**. Every system holds its own version of the truth. They silently drift apart. No single system can be trusted.

## The Solution

SmartLedger enforces one fundamental rule:

> **No data is written to the ledger unless it has been fully validated across all relevant systems and business rules.**

An **AI Agent** orchestrates the entire flow — connecting to all systems via **MCP (Model Context Protocol)**, running cross-system validation, and writing only validated data to an **immutable Hyperledger Fabric ledger**.

## Secondary Purpose

A bridge during the Oracle LOS → Salesforce LOS migration: both systems run in parallel, SmartLedger compares their outputs and detects policy drift — providing an undeniable audit trail of what each system produced.

---

## Architecture

```
External Systems (Simulated MCP Servers)
  Oracle LOS │ Salesforce LOS │ LLAS │ CRM │ Payment │ Insurance │ Dealer
  Customer Portal │ Mobile App │ IVR
          │
          │  publish events
          ▼
    Redis Streams (Event Bus)
          │
          │  agent subscribes
          ▼
      AI Agent  ◄──── MCP calls ────►  Validation Engine MCP
   (Orchestrator)                       Immutable Ledger MCP
                                        Semantic AI MCP
                                        Reporting MCP
          │
          ▼
    PostgreSQL (off-chain)    Hyperledger Fabric (on-chain)
    Redis (locks + dedup)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for full diagrams.

---

## Tech Stack

| Layer | Technology |
|---|---|
| AI Agent | Custom — Anthropic API + MCP Python SDK v1.7.1 |
| MCP Servers | Python 3.12 + FastMCP |
| Event Bus | Redis Streams |
| Blockchain | Hyperledger Fabric |
| Chaincode | Node.js |
| Database | PostgreSQL 16 |
| Frontend | Next.js (App Router) |
| Dashboard API | FastAPI (Python) |
| Semantic AI | Claude API (claude-3-5-sonnet) |
| Package Manager | `uv` (Python) + `pnpm` (JS) |

---

## Project Structure

```
smartledger/
├── src/
│   ├── agent/              # AI Agent orchestrator
│   │   ├── core/           # Event loop, saga, locks
│   │   └── flows/          # Origination, payment, PDF ingestion
│   ├── mcp_servers/
│   │   ├── validation/     # Validation Engine MCP
│   │   ├── ledger/         # Immutable Ledger MCP
│   │   ├── semantic_ai/    # Semantic AI Engine MCP
│   │   ├── reporting/      # Reporting System MCP
│   │   └── simulated/      # 10 simulated external systems
│   ├── event_bus/          # Redis Streams consumer
│   ├── dashboard_api/      # FastAPI REST server
│   └── shared/             # Schemas, Pydantic models, config
├── apps/
│   ├── dashboard-ui/       # Next.js Governance Dashboard
│   └── chaincode/          # Hyperledger Fabric chaincode
├── infra/
│   ├── docker/             # Dockerfiles + postgres init
│   └── fabric/             # Fabric network config (Phase G)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/                   # Architecture diagrams, implementation plan
├── docker-compose.yml      # Full local dev stack
└── scripts/setup.sh        # One-command setup
```

---

## Quick Start

### Prerequisites
- macOS with [Homebrew](https://brew.sh) installed
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- An [Anthropic API key](https://console.anthropic.com/)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_ORG/smartledger.git
cd smartledger

# 2. Run setup (installs uv, pnpm, Python deps, JS deps)
./scripts/setup.sh

# 3. Add your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# 4. Start infrastructure (PostgreSQL + Redis)
docker compose up -d postgres redis

# 5. Run database migrations
docker compose up --build postgres   # init.sql runs automatically on first start
```

### Run the Stack (once implemented)

```bash
# Start all services
docker compose up -d

# Or run individual services for development
uv run python -m mcp_servers.validation.server   # port 8001
uv run python -m mcp_servers.ledger.server       # port 8002
uv run python -m mcp_servers.simulated.oracle_los.server  # port 8010
uv run python -m agent.main                      # agent

# Dashboard
cd apps/dashboard-ui && pnpm dev                 # port 3000
```

---

## Documentation

| Document | Description |
|---|---|
| [`REQUIREMENTS.md`](REQUIREMENTS.md) | Full locked requirements — 20 sections, all architectural decisions |
| [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md) | Project status, gap analysis, resolved decisions |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Architecture diagrams (Mermaid) |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | Full implementation checklist by phase |
| [`.env.example`](.env.example) | All environment variables |

---

## Implementation Status

| Phase | Description | Status |
|---|---|---|
| **A** | Foundation: schemas, models, deps, DB schema | ✅ Complete |
| **B** | First MCP servers: Oracle LOS sim, LLAS sim, Validation, Ledger | ⏳ Next |
| **C** | Agent core: event loop, saga, locks | ⏳ Pending |
| **D** | Origination happy path E2E | ⏳ Pending |
| **E** | Unhappy path: quarantine + human override + dashboard | ⏳ Pending |
| **F** | Remaining flows: payment, PDF, all simulators, reporting | ⏳ Pending |
| **G** | Full stack: Hyperledger Fabric live writes | ⏳ Pending |

---

## Key Concepts

| Term | Description |
|---|---|
| **Validation Gate** | No data written without full cross-system + business rule validation |
| **Proof Token** | Signed JWT issued by Validation Engine; required for every ledger write |
| **Saga** | Multi-step flow with PostgreSQL checkpoints; crash-safe and resumable |
| **Per-Contract Lock** | Redis distributed lock; one event processed per contract at a time |
| **Write Guard** | Phase 0 protection — agent can run but Ledger MCP rejects all writes |
| **Quarantine** | Failed events held for human review in the Governance Dashboard |

---

## Running on Linux

The default Quick Start above targets macOS with Docker Desktop. To run on any Linux system, the differences are minimal — everything runs in Docker, so the host requirements are small.

### Host prerequisites (Linux)

| Requirement | Notes |
|---|---|
| **Docker Engine 20.10+** with Compose v2 plugin | Use `docker compose` (v2), not `docker-compose` (v1). Install via [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) |
| **`curl` and `tar`** | Pre-installed on all major distros |
| **`uv`** (Python runner) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Node.js 18+** | Required only for the one-time Fabric chaincode `npm install` during `setup-fabric.sh`. Install via `apt install nodejs npm` or [nvm](https://github.com/nvm-sh/nvm) |

Python, pnpm, and all application dependencies run inside Docker — they do not need to be installed on the host.

### What works without any changes

- All Docker images (`python:3.12-slim`, `node:22-alpine`, `postgres:16-alpine`, `redis:7-alpine`) are multi-architecture and run on both amd64 and arm64 Linux.
- `setup-fabric.sh` detects the OS and CPU architecture automatically (`uname -s` / `uname -m`) and downloads the correct Hyperledger Fabric binaries for Linux. Nothing needs to be changed.
- The Fabric binaries directory (`infra/fabric/bin/`) is gitignored — on a fresh clone it is empty and filled by the setup script.

### Setup steps (Linux)

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_ORG/smartledger.git
cd smartledger

# 2. Copy and configure environment
cp .env.example .env
# Edit .env and set: ANTHROPIC_API_KEY=sk-ant-...

# 3. Build all Docker images
docker compose build

# 4. Bootstrap the Hyperledger Fabric network (one-time)
cd infra/fabric
chmod +x scripts/setup-fabric.sh
./scripts/setup-fabric.sh
cd ../..

# 5. Start all services
docker compose -f infra/fabric/docker-compose.fabric.yml up -d
docker compose up -d

# 6. Load demo data
uv run python scripts/seed_demo.py --clean
```

Dashboard will be available at **http://localhost:3000**.

### Watch out for

- **Local PostgreSQL on port 5432** — if the host already runs a PostgreSQL service, it may conflict with the Docker container on the same port. Either stop the local service (`sudo systemctl stop postgresql`) or change the host port mapping in `docker-compose.yml` (e.g. `"5433:5432"`).
- **Docker socket permissions** — on some Linux distros, `docker` commands require `sudo` unless your user is in the `docker` group: `sudo usermod -aG docker $USER` (log out and back in after).

---

## License

Private — SmartLedger POC
