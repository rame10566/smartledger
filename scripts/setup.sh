#!/usr/bin/env bash
# SmartLedger — Local dev setup
# Run once after cloning: ./scripts/setup.sh

set -euo pipefail

echo "── SmartLedger Setup ──────────────────────────────────────────"

# 1. Check prerequisites
echo "→ Checking prerequisites..."
command -v brew   >/dev/null 2>&1 || { echo "✗ Homebrew not found. Install from https://brew.sh"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "✗ Docker not found. Install Docker Desktop."; exit 1; }

# 2. Install system tools via Homebrew
echo "→ Installing system tools via Homebrew..."
brew install uv pnpm python@3.12 node 2>/dev/null || true

# 3. Copy .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "→ Created .env from .env.example — please add your ANTHROPIC_API_KEY"
else
    echo "→ .env already exists, skipping"
fi

# 4. Install Python workspace dependencies
echo "→ Installing Python dependencies with uv..."
uv sync

# 5. Install Next.js dashboard dependencies
echo "→ Installing dashboard-ui dependencies with pnpm..."
cd apps/dashboard-ui && pnpm install && cd ../..

# 6. Install chaincode dependencies
echo "→ Installing chaincode dependencies with pnpm..."
cd apps/chaincode && pnpm install && cd ../..

echo ""
echo "── Setup complete! ────────────────────────────────────────────"
echo "  Next steps:"
echo "  1. Add ANTHROPIC_API_KEY to .env"
echo "  2. docker compose up -d     # start postgres + redis"
echo "  3. uv run python -m agent.main  # start the agent"
echo ""
