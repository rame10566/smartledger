#!/usr/bin/env bash
#
# start-explorer.sh — bring up Hyperledger Explorer for the SmartLedger Fabric network.
#
# Verifies the Fabric network is running, then starts Explorer + its DB.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPLORER_DIR="$SCRIPT_DIR/../explorer"
FABRIC_NET="smartledger_fabric_net"

# ── Verify Fabric is running ──────────────────────────────────────────────
echo "▸ Checking Fabric network..."

if ! docker network inspect "$FABRIC_NET" > /dev/null 2>&1; then
  echo "✗ Docker network '$FABRIC_NET' does not exist."
  echo "  Start the Fabric network first:"
  echo "    cd $(dirname "$EXPLORER_DIR") && docker compose -f docker-compose.fabric.yml up -d"
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q 'peer0.org1.smartledger.local'; then
  echo "✗ Fabric peer is not running."
  echo "  Start the Fabric network first:"
  echo "    cd $(dirname "$EXPLORER_DIR") && docker compose -f docker-compose.fabric.yml up -d"
  exit 1
fi

echo "✓ Fabric network is up."

# ── Start Explorer ────────────────────────────────────────────────────────
echo "▸ Starting Hyperledger Explorer..."
cd "$EXPLORER_DIR"
docker compose -f docker-compose.explorer.yml up -d

echo ""
echo "▸ Explorer is starting up.  Initial sync takes 30–60 seconds."
echo ""
echo "  ✓ UI:    http://localhost:8090"
echo "  ✓ Login: exploreradmin / exploreradminpw"
echo ""
echo "  Tail logs:    docker logs -f smartledger-explorer"
echo "  Stop:         docker compose -f $EXPLORER_DIR/docker-compose.explorer.yml down"
echo "  Wipe state:   docker compose -f $EXPLORER_DIR/docker-compose.explorer.yml down -v"
