##
## mcp_ledger.Dockerfile — Immutable Ledger MCP server
##
## Phase 0: write_guard=true  → PostgreSQL only
## Phase 1: write_guard=false → live Hyperledger Fabric writes via peer CLI
##
## The peer CLI binary is copied from the official fabric-tools image so no
## separate download is needed.  The crypto material is mounted at runtime
## via docker-compose volume mounts (only required when WRITE_GUARD=false).
##

# ── Stage 1: pull peer binary from official fabric-tools image ────────────────
FROM hyperledger/fabric-tools:2.5 AS fabric-tools

# ── Stage 2: application image ────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Peer CLI binary (needed for Phase 1 live Fabric writes)
COPY --from=fabric-tools /usr/local/bin/peer        /usr/local/bin/peer
# core.yaml is required by the peer CLI (FABRIC_CFG_PATH must point to this dir)
COPY --from=fabric-tools /etc/hyperledger/fabric/   /etc/hyperledger/fabric/

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./
COPY src/shared ./src/shared
COPY src/mcp_servers/ledger ./src/mcp_servers/ledger

RUN uv sync --package smartledger-mcp-ledger --no-dev

EXPOSE 8002

CMD ["uv", "run", "--package", "smartledger-mcp-ledger", "python", "-m", "mcp_servers.ledger.server"]
