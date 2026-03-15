#!/usr/bin/env bash
# SmartLedger — Local Development Startup
#
# Starts the full Phase D stack for local development:
#   1. PostgreSQL + Redis (via Docker Compose — infra only)
#   2. Validation Engine MCP (port 8001)
#   3. Ledger MCP (port 8002)
#   4. Simulated Systems (ports 8010-8019)
#   5. AI Agent (event loop consumer)
#
# Usage:
#   ./scripts/dev_start.sh          # start everything
#   ./scripts/dev_start.sh stop     # stop all background processes
#   ./scripts/dev_start.sh logs     # tail all log files
#
# Prerequisites:
#   - Docker Desktop running
#   - uv installed (brew install uv)
#   - .env file present (cp .env.example .env)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOGS_DIR="$PROJECT_ROOT/.logs"
PIDS_FILE="$LOGS_DIR/pids"

# Colour helpers
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}→${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC} $*"; }
error()   { echo -e "${RED}✗${NC} $*" >&2; }

# ── Commands ──────────────────────────────────────────────────────────────────

cmd_stop() {
    if [ ! -f "$PIDS_FILE" ]; then
        warn "No PID file found — nothing to stop."
        return
    fi
    info "Stopping SmartLedger services..."
    while IFS=': ' read -r name pid; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" && info "  Stopped $name (pid $pid)"
        fi
    done < "$PIDS_FILE"
    rm -f "$PIDS_FILE"
    info "Stopping infra (Docker)..."
    docker compose -f "$PROJECT_ROOT/docker-compose.dev.yml" down
    info "Done."
}

cmd_logs() {
    if [ ! -d "$LOGS_DIR" ]; then
        warn "No log directory found. Run dev_start.sh first."
        exit 1
    fi
    tail -f "$LOGS_DIR"/*.log
}

# ── Start ─────────────────────────────────────────────────────────────────────

cmd_start() {
    mkdir -p "$LOGS_DIR"
    > "$PIDS_FILE"

    echo "── SmartLedger Dev Stack ────────────────────────────────────────"

    # ── Infra ──────────────────────────────────────────────────────────────────
    info "Starting infra (PostgreSQL + Redis)..."
    docker compose -f "$PROJECT_ROOT/docker-compose.dev.yml" up -d

    info "Waiting for PostgreSQL..."
    until docker exec smartledger-postgres pg_isready -U smartledger -q; do
        sleep 1
    done
    info "  PostgreSQL ready."

    info "Waiting for Redis..."
    until docker exec smartledger-redis redis-cli ping | grep -q PONG; do
        sleep 1
    done
    info "  Redis ready."

    # ── Python services ────────────────────────────────────────────────────────
    cd "$PROJECT_ROOT"

    start_service() {
        local name="$1"; shift
        local log="$LOGS_DIR/${name}.log"
        uv run "$@" > "$log" 2>&1 &
        local pid=$!
        echo "${name}: ${pid}" >> "$PIDS_FILE"
        info "  Started $name (pid $pid) → $log"
    }

    info "Starting MCP servers..."
    start_service "mcp-validation" \
        --package smartledger-mcp-validation \
        python -m mcp_servers.validation.server

    start_service "mcp-ledger" \
        --package smartledger-mcp-ledger \
        python -m mcp_servers.ledger.server

    start_service "mcp-simulated" \
        --package smartledger-mcp-simulated \
        python -m mcp_servers.simulated.launcher

    # Give servers a moment to bind their ports
    sleep 3

    info "Starting AI Agent..."
    start_service "agent" \
        --package smartledger-agent \
        python -m agent.main

    echo "────────────────────────────────────────────────────────────────"
    echo ""
    echo "  Services running:"
    echo "    Validation MCP    http://localhost:8001"
    echo "    Ledger MCP        http://localhost:8002"
    echo "    Oracle LOS sim    http://localhost:8010"
    echo "    LLAS sim          http://localhost:8012"
    echo "    Agent             (consuming Redis Stream)"
    echo ""
    echo "  Commands:"
    echo "    ./scripts/dev_start.sh logs   — tail all service logs"
    echo "    ./scripts/dev_start.sh stop   — stop everything"
    echo "    python scripts/run_origination_demo.py  — run E2E demo"
    echo ""
}

# ── Entry point ───────────────────────────────────────────────────────────────

case "${1:-start}" in
    start) cmd_start ;;
    stop)  cmd_stop  ;;
    logs)  cmd_logs  ;;
    *)
        echo "Usage: $0 [start|stop|logs]"
        exit 1
        ;;
esac
