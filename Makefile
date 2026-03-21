##
## SmartLedger — Developer Makefile
##
## Prerequisites (host):
##   - Docker Engine 20.10+ with Compose v2 plugin  (docker compose)
##   - curl, tar                                     (to download Fabric binaries)
##   - uv   (Python runner — install: curl -LsSf https://astral.sh/uv/install.sh | sh)
##
## Everything else (Node.js, Fabric peer/orderer/cryptogen, Python deps) is
## either downloaded automatically or runs inside Docker containers.
##
## Quick start:
##   cp .env.example .env          # then set ANTHROPIC_API_KEY
##   make setup                    # bootstrap Fabric network (one-time)
##   make start                    # start all services
##   make seed                     # load demo data
##   open http://localhost:3000    # view dashboard
##

.PHONY: help setup start stop restart seed seed-clean logs status reset \
        fabric-up fabric-down fabric-reset build rebuild

# ── Default target ─────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  SmartLedger — available targets"
	@echo ""
	@echo "  First-time setup:"
	@echo "    make setup          Bootstrap Fabric network + build all images"
	@echo "    make start          Start all services (app + Fabric)"
	@echo "    make seed           Load demo data (clean slate)"
	@echo ""
	@echo "  Day-to-day:"
	@echo "    make stop           Stop all services"
	@echo "    make restart        Restart all services"
	@echo "    make logs           Tail logs for all services"
	@echo "    make status         Show container health"
	@echo ""
	@echo "  Reset:"
	@echo "    make reset          Full reset — wipe ALL data (Fabric + Postgres + Redis)"
	@echo "    make rebuild        Rebuild all Docker images from scratch"
	@echo ""
	@echo "  Fabric (advanced):"
	@echo "    make fabric-up      Start only the Fabric network"
	@echo "    make fabric-down    Stop only the Fabric network"
	@echo "    make fabric-reset   Wipe and re-bootstrap Fabric from block #1"
	@echo ""

# ── First-time setup ──────────────────────────────────────────────────────────
setup: _check-env _check-docker
	@echo ""
	@echo "━━━ [1/3] Building Docker images ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	docker compose build
	@echo ""
	@echo "━━━ [2/3] Bootstrapping Hyperledger Fabric ━━━━━━━━━━━━━━━━━━━━━━━━━"
	chmod +x infra/fabric/scripts/setup-fabric.sh
	cd infra/fabric && bash scripts/setup-fabric.sh
	@echo ""
	@echo "━━━ [3/3] Starting app services ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	docker compose up -d
	@echo ""
	@echo "✓ Setup complete. Run 'make seed' to load demo data."
	@echo "  Dashboard: http://localhost:3000"
	@echo ""

# ── Start / Stop ──────────────────────────────────────────────────────────────
start: _check-env _check-docker
	docker compose -f infra/fabric/docker-compose.fabric.yml up -d
	docker compose up -d
	@echo "✓ All services started. Dashboard: http://localhost:3000"

stop:
	docker compose stop
	docker compose -f infra/fabric/docker-compose.fabric.yml stop

restart:
	docker compose restart
	docker compose -f infra/fabric/docker-compose.fabric.yml restart

# ── Seed demo data ────────────────────────────────────────────────────────────
seed: _check-uv
	uv run python scripts/seed_demo.py --clean

# ── Logs & status ─────────────────────────────────────────────────────────────
logs:
	docker compose logs --follow --tail 50

status:
	@echo "── App stack ────────────────────────────────────────────────────────"
	@docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | grep -v "^time="
	@echo ""
	@echo "── Fabric network ───────────────────────────────────────────────────"
	@docker compose -f infra/fabric/docker-compose.fabric.yml ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null | grep -v "^time="
	@echo ""

# ── Full reset (wipes everything, keeps images) ───────────────────────────────
reset:
	@echo "⚠  This will wipe ALL data: Fabric blockchain, PostgreSQL, Redis."
	@read -p "   Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ] || exit 1
	@echo ""
	@echo "── Stopping all services ─────────────────────────────────────────────"
	-docker compose down 2>/dev/null || true
	-docker compose -f infra/fabric/docker-compose.fabric.yml down -v 2>/dev/null || true
	@echo "── Removing Fabric generated artifacts ───────────────────────────────"
	rm -rf infra/fabric/crypto-material infra/fabric/artifacts infra/fabric/bin
	@echo "── Rebuilding and starting fresh ─────────────────────────────────────"
	$(MAKE) setup
	$(MAKE) seed
	@echo ""
	@echo "✓ Full reset complete."

# ── Rebuild images ────────────────────────────────────────────────────────────
rebuild:
	docker compose build --no-cache
	docker compose up -d

# ── Fabric targets ────────────────────────────────────────────────────────────
fabric-up:
	docker compose -f infra/fabric/docker-compose.fabric.yml up -d

fabric-down:
	docker compose -f infra/fabric/docker-compose.fabric.yml down

fabric-reset:
	@echo "⚠  This will wipe the Fabric blockchain (all on-chain records lost)."
	@read -p "   Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ] || exit 1
	-docker compose -f infra/fabric/docker-compose.fabric.yml down -v 2>/dev/null || true
	rm -rf infra/fabric/crypto-material infra/fabric/artifacts infra/fabric/bin
	chmod +x infra/fabric/scripts/setup-fabric.sh
	cd infra/fabric && bash scripts/setup-fabric.sh

# ── Internal checks ───────────────────────────────────────────────────────────
_check-env:
	@if [ ! -f .env ]; then \
	  echo ""; \
	  echo "✗ .env file not found."; \
	  echo "  Run: cp .env.example .env"; \
	  echo "  Then set ANTHROPIC_API_KEY in .env"; \
	  echo ""; \
	  exit 1; \
	fi
	@if grep -q "sk-ant-\.\.\." .env 2>/dev/null; then \
	  echo ""; \
	  echo "✗ ANTHROPIC_API_KEY is still the placeholder value in .env."; \
	  echo "  Edit .env and set a real API key."; \
	  echo ""; \
	  exit 1; \
	fi

_check-docker:
	@if ! docker info &>/dev/null; then \
	  echo ""; \
	  echo "✗ Docker is not running or not installed."; \
	  echo "  Install Docker Engine: https://docs.docker.com/engine/install/"; \
	  echo ""; \
	  exit 1; \
	fi
	@if ! docker compose version &>/dev/null; then \
	  echo ""; \
	  echo "✗ Docker Compose v2 plugin not found."; \
	  echo "  Install: https://docs.docker.com/compose/install/"; \
	  echo ""; \
	  exit 1; \
	fi

_check-uv:
	@if ! command -v uv &>/dev/null; then \
	  echo ""; \
	  echo "✗ 'uv' is not installed."; \
	  echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; \
	  echo "  Or via pip: pip install uv"; \
	  echo ""; \
	  exit 1; \
	fi
