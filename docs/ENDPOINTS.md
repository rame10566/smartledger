# SmartLedger — Endpoints, URLs & Login Reference

Single-source quick reference for every URL, port, endpoint, and login account in the local POC stack. For architecture see `docs/ARCHITECTURE.md`; for the full spec see `REQUIREMENTS.md`.

---

## At-a-glance

| Layer | URL / Port | Notes |
|------|------|------|
| Governance Dashboard (UI) | http://localhost:3000 | Next.js, redirects `/` → main view |
| Party Portal (UI) | http://localhost:3000/party | SDG party-based access, JWT-auth |
| Dashboard API (REST) | http://localhost:8000 | All routes under `/api/...` and `/api/party/...` |
| Hyperledger Explorer | http://localhost:8090 | Fabric block explorer |
| PostgreSQL | localhost:5432 | Off-chain working store |
| Redis | localhost:6379 | Event bus + locks + idempotency |
| MCP servers (built) | localhost:8001-8004 | Validation, Ledger, Semantic AI, Reporting |
| MCP servers (simulated) | localhost:8010-8022 | All 13 simulated systems |

---

## Frontends (Browser)

### Governance Dashboard — http://localhost:3000

| Path | Purpose |
|------|---------|
| `/` | Landing (redirects to a default view) |
| `/contracts` | Contract list + lifecycle |
| `/contracts/{contractId}` | Single contract detail |
| `/quarantine` | Quarantine queue |
| `/conflicts` | LLAS Admin conflict resolution |
| `/reports` | Reports |
| `/party` | Party Portal (separate auth) |

Container: `smartledger-dashboard-ui`. Start/stop:
```bash
docker compose up -d dashboard-ui
docker compose stop dashboard-ui
docker logs -f smartledger-dashboard-ui
```

### Hyperledger Explorer — http://localhost:8090

| Field | Value |
|-------|-------|
| Username | `exploreradmin` |
| Password | `exploreradminpw` |
| Network | `smartledger-network` |

Container: `smartledger-explorer`. Browses the `smartledger-channel` blocks, transactions, and chaincode `smartledger-cc` v1.0.

---

## Dashboard API — http://localhost:8000

All endpoints (except `/api/health` and `/api/party/auth`) require an identity header. See [Authentication](#authentication--login-accounts).

### Operational endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/health` | Liveness check (no auth) |
| `GET` | `/api/contracts` | List contracts (lifecycle view) |
| `GET` | `/api/contracts/{contract_id}/lifecycle` | Full lifecycle |
| `GET` | `/api/contracts/{contract_id}/state` | Current state |
| `GET` | `/api/contracts/{contract_id}/audit` | Audit trail |
| `GET` | `/api/contracts/{contract_id}/saga` | Saga checkpoints |
| `GET` | `/api/quarantine?status=pending` | Quarantine queue |
| `GET` | `/api/quarantine/{event_id}` | Single quarantine record |
| `GET` | `/api/conflicts` | Conflict-resolution queue |
| `GET` | `/api/conflicts/{conflict_pair_id}` | Single conflict |
| `POST` | `/api/conflicts/{conflict_pair_id}/resolve` | LLAS Admin adjudication (admin role only) |
| `GET` | `/api/reports` | List reports |
| `GET` | `/api/reports/types` | Report type catalogue |
| `POST` | `/api/reports/generate` | Generate a report |
| `GET` | `/api/reports/{report_id}` | Read report |
| `GET` | `/api/reports/{report_id}/export` | Export (CSV/PDF) |

### Party Portal endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/party/auth` | Authenticate party → JWT (no identity header) |
| `GET` | `/api/party/contracts` | List contracts visible to this party (Bearer JWT) |
| `GET` | `/api/party/contracts/{contract_id}` | Contract detail with blockchain proof (Bearer JWT) |

---

## MCP Servers (built)

All speak MCP over Streamable HTTP at `/mcp`.

| Port | Service | Container | Endpoint |
|------|---------|-----------|----------|
| 8001 | Validation Engine | `smartledger-mcp-validation` | http://localhost:8001/mcp |
| 8002 | Ledger MCP (wraps Hyperledger Fabric) | `smartledger-mcp-ledger` | http://localhost:8002/mcp |
| 8003 | Semantic AI | `smartledger-mcp-semantic-ai` | http://localhost:8003/mcp |
| 8004 | Reporting | `smartledger-mcp-reporting` | http://localhost:8004/mcp |

---

## Simulated MCP Servers

All run inside the single container `smartledger-mcp-simulated` (one process per port).

| Port | System | Endpoint |
|------|--------|----------|
| 8010 | Oracle LOS | http://localhost:8010/mcp |
| 8011 | Salesforce LOS | http://localhost:8011/mcp |
| 8012 | LLAS (Accounting) | http://localhost:8012/mcp |
| 8013 | CRM | http://localhost:8013/mcp |
| 8014 | Payment | http://localhost:8014/mcp |
| 8015 | Insurance | http://localhost:8015/mcp |
| 8016 | Dealer | http://localhost:8016/mcp |
| 8017 | Customer Portal | http://localhost:8017/mcp |
| 8018 | Mobile App | http://localhost:8018/mcp |
| 8019 | IVR | http://localhost:8019/mcp |
| 8020 | Rules Engine | http://localhost:8020/mcp |
| 8021 | Pricing Engine | container-internal only (host port in use) |
| 8022 | Integration System | http://localhost:8022/mcp |

---

## Infrastructure

### PostgreSQL — localhost:5432

| Field | Value |
|-------|-------|
| User | `smartledger` |
| Password | from `.env` (`POSTGRES_PASSWORD`, default `smartledger_dev`) |
| Database | `smartledger` |
| Container | `smartledger-postgres` |

```bash
# psql shell
docker exec -it smartledger-postgres psql -U smartledger -d smartledger

# One-off query
docker exec smartledger-postgres psql -U smartledger -d smartledger -c "SELECT COUNT(*) FROM contracts.records;"
```

Schemas: `contracts`, `validation`, `sagas`, `audit`, `reports`, `extraction`, `integration_system`.

### Redis — localhost:6379

| Field | Value |
|-------|-------|
| Auth | none (POC) |
| Container | `smartledger-redis` |

```bash
# Redis CLI
docker exec -it smartledger-redis redis-cli

# Event stream length
docker exec smartledger-redis redis-cli XLEN smartledger:events
```

Key namespaces: `smartledger:events` (event stream), `smartledger:lock:*` (per-contract locks), idempotency cache.

---

## Hyperledger Fabric (background)

No app endpoints — the agent and dashboard reach Fabric via the **Ledger MCP on 8002**.

| Component | Container | Notes |
|-----------|-----------|-------|
| Peer | `fabric-peer0.org1.smartledger.local-1` | mTLS, MSP `SmartLedgerOrgMSP` |
| Orderer | `fabric-orderer.orderer.smartledger.local-1` | etcdraft, single consenter (POC) |
| CA | `fabric-ca.org1.smartledger.local-1` | Self-signed via cryptogen |
| State DB | `fabric-couchdb-1` | Rich queries enabled |
| Chaincode | `dev-peer0…smartledger-cc_1.1…` | Node.js, channel `smartledger-channel` |

---

## Authentication & Login Accounts

### 1. Dashboard API operational identity (header-based)

For everything under `/api/...` except `/api/health` and `/api/party/...`. Pass as raw JSON in this header:

```
X-SmartLedger-Identity: {"actor_id":"<id>","actor_type":"user","role":"<role>"}
```

| Role | Sees | Can write |
|------|------|-----------|
| `admin` | all contracts, all quarantine, all conflicts | yes |
| `auditor` | all (read-only) | no |
| `operator` | own-org contracts only | yes (limited) |
| `compliance` | all | yes |

Examples:
```bash
# Quarantine queue as admin
curl -s -H 'X-SmartLedger-Identity: {"actor_id":"demo","role":"admin"}' \
  "http://localhost:8000/api/quarantine?status=pending"

# Contract lifecycle as auditor (read-only)
curl -s -H 'X-SmartLedger-Identity: {"actor_id":"demo","role":"auditor"}' \
  "http://localhost:8000/api/contracts/ORC-2026-7C5A4D/lifecycle"

# Resolve a conflict as admin (the LLAS Admin role uses admin)
curl -s -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-SmartLedger-Identity: {"actor_id":"llas-admin","role":"admin"}' \
  -d '{"winning_event_id":"...", "reason":"latest source authoritative"}' \
  "http://localhost:8000/api/conflicts/<conflict_pair_id>/resolve"
```

### 2. Party Portal accounts (entity_id + party_type)

Authenticate with `POST /api/party/auth` (or via the UI at `/party`). The portal verifies the row exists in `contracts.parties` and issues a JWT.

**Valid `party_type` values:** `borrower`, `lessee`, `lender`, `lessor`, `dealer`, `servicer`, `insurer`.

**Seeded entity IDs (POC demo data, contracts created 2026-03-19):**

| `party_type` | `entity_id` | What they see |
|--------------|-------------|---------------|
| `lender` | `SMARTLEDGER_FINANCE` | every loan contract |
| `lessor` | `SMARTLEDGER_FINANCE` | every lease contract |
| `borrower` | `CUST-3EF86D` | only `ORC-2026-7C5A4D` |
| `borrower` | `CUST-FF2125` | only `ORC-2026-581E84` |
| `lessee` | `CUST-E312EF` | only `ORC-2026-4CB178` |
| `dealer` | `DLR-001` | only `ORC-2026-7C5A4D` |
| `dealer` | `DLR-002` | only `ORC-2026-4CB178` |

**For the most data, log in as `lender` / `SMARTLEDGER_FINANCE`.**

To list every valid combo currently in the database:

```bash
docker exec smartledger-postgres psql -U smartledger -d smartledger -c "
SELECT party_role, entity_id, COUNT(*) AS contract_count
FROM contracts.parties
GROUP BY party_role, entity_id
ORDER BY party_role, entity_id;
"
```

If the table is empty (e.g. after `seed_demo.py` truncate), reseed:

```bash
PYTHONPATH=src uv run python scripts/seed_demo.py
```

### 3. Hyperledger Explorer

| Field | Value |
|-------|-------|
| URL | http://localhost:8090 |
| Username | `exploreradmin` |
| Password | `exploreradminpw` |
| Network | `smartledger-network` |

### 4. Service-to-service secrets

Configured in `.env` (gitignored). All POC-scope; rotate before any non-local deployment.

| Variable | Used by | Notes |
|----------|---------|-------|
| `JWT_SECRET` | Validation Engine, Ledger MCP | HS256 proof-token signing |
| `PROOF_TOKEN_SECRET` | Validation Engine, Ledger MCP | secondary |
| `DASHBOARD_JWT_SECRET` | Dashboard API party-portal JWT | required even if unused (strict check) |
| `ANTHROPIC_API_KEY` | Agent, Semantic AI | Claude API |
| `POSTGRES_PASSWORD` | Postgres + every service | DB connection |

`shared/config.py` refuses to start any service if any of the three secret vars equal a `change-me*` placeholder. Bypass for local: `SMARTLEDGER_ENV=test`.

---

## Smoke-test snippets

```bash
# Probe an MCP server (substitute port)
curl -s -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -X POST http://localhost:8001/mcp \
     -d '{"jsonrpc":"2.0","method":"initialize","id":1,
          "params":{"protocolVersion":"2024-11-05","capabilities":{},
                    "clientInfo":{"name":"probe","version":"1"}}}'

# Watch the agent in real time
docker logs -f smartledger-agent

# All container status
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep smartledger

# Stop / start everything
docker compose down                 # stops all, keeps volumes
docker compose up -d                # bring it all back
docker compose restart agent        # restart a single service
```

---

## When in doubt

| Question | Where to look |
|----------|---------------|
| Why is X not working? | `docker logs <container>` |
| What's in the ledger? | Hyperledger Explorer @ :8090 |
| What's in quarantine? | Dashboard `/quarantine` or `GET /api/quarantine` |
| What contracts exist? | `psql ... SELECT * FROM contracts.records;` |
| What sagas ran? | `psql ... SELECT * FROM sagas.processed_events ORDER BY processed_at DESC LIMIT 20;` |
| Which simulator runs on port X? | This file, simulated MCP table |
