#!/usr/bin/env bash
##
## setup-fabric.sh — Bootstrap the SmartLedger Hyperledger Fabric network
##
## This script:
##   1. Downloads Fabric binaries (cryptogen, configtxgen, peer CLI) if needed
##   2. Generates crypto material
##   3. Creates genesis block and channel transaction
##   4. Starts the Fabric network (docker compose)
##   5. Creates the channel and joins the peer
##   6. Installs and instantiates the smartledger-cc chaincode
##
## Prerequisites:
##   - Docker Desktop / Docker Engine running
##   - curl, tar
##   - Node.js 18+ (for chaincode dependencies)
##
## Usage:
##   cd infra/fabric
##   chmod +x scripts/setup-fabric.sh
##   ./scripts/setup-fabric.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABRIC_DIR="$(dirname "$SCRIPT_DIR")"
CHAINCODE_DIR="$FABRIC_DIR/../chaincode"

FABRIC_VERSION="2.5.9"
CA_VERSION="1.5.12"
FABRIC_BIN="$FABRIC_DIR/bin"
CHANNEL_NAME="smartledger-channel"
CC_NAME="smartledger-cc"
CC_VERSION="1.0"
CC_SEQUENCE="1"

export PATH="$FABRIC_BIN:$PATH"

# ── Colour helpers ─────────────────────────────────────────────────────────────
info()  { echo -e "\033[0;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[0;32m[OK]\033[0m    $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
die()   { echo -e "\033[0;31m[ERROR]\033[0m $*" >&2; exit 1; }

# ── 1. Download Fabric binaries ────────────────────────────────────────────────
if [[ ! -f "$FABRIC_BIN/peer" ]]; then
  info "Downloading Hyperledger Fabric $FABRIC_VERSION binaries…"
  mkdir -p "$FABRIC_BIN"
  OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
  ARCH="$(uname -m)"
  [[ "$ARCH" == "arm64" ]] && ARCH="arm64" || ARCH="amd64"

  BINARY_URL="https://github.com/hyperledger/fabric/releases/download/v${FABRIC_VERSION}/hyperledger-fabric-${OS}-${ARCH}-${FABRIC_VERSION}.tar.gz"
  DEST="$FABRIC_DIR/fabric-binaries.tar.gz"
  curl -sSL "$BINARY_URL" -o "$DEST"
  tar -xzf "$DEST" -C "$FABRIC_DIR" --strip-components=0
  rm -f "$DEST"
  ok "Fabric binaries installed to $FABRIC_BIN"
else
  ok "Fabric binaries already present"
fi

# ── 2. Generate crypto material ────────────────────────────────────────────────
cd "$FABRIC_DIR"

if [[ ! -d "crypto-material/peerOrganizations" ]]; then
  info "Generating crypto material…"
  cryptogen generate --config=./crypto-config.yaml --output=./crypto-material
  ok "Crypto material generated"
else
  ok "Crypto material already exists"
fi

# ── 3. Create channel artifacts ────────────────────────────────────────────────
mkdir -p artifacts

if [[ ! -f "artifacts/genesis.block" ]]; then
  info "Creating orderer genesis block…"
  FABRIC_CFG_PATH="$FABRIC_DIR" \
    configtxgen \
      -profile SmartLedgerGenesis \
      -channelID system-channel \
      -outputBlock ./artifacts/genesis.block
  ok "Genesis block created"
else
  ok "Genesis block already exists"
fi

if [[ ! -f "artifacts/channel.tx" ]]; then
  info "Creating channel transaction…"
  FABRIC_CFG_PATH="$FABRIC_DIR" \
    configtxgen \
      -profile SmartLedgerChannel \
      -channelID "$CHANNEL_NAME" \
      -outputCreateChannelTx ./artifacts/channel.tx
  ok "Channel transaction created"
else
  ok "Channel transaction already exists"
fi

if [[ ! -f "artifacts/SmartLedgerOrgAnchors.tx" ]]; then
  info "Creating anchor peer update…"
  FABRIC_CFG_PATH="$FABRIC_DIR" \
    configtxgen \
      -profile SmartLedgerChannel \
      -channelID "$CHANNEL_NAME" \
      -outputAnchorPeersUpdate ./artifacts/SmartLedgerOrgAnchors.tx \
      -asOrg SmartLedgerOrg
  ok "Anchor peer update created"
fi

# ── 4. Start the Fabric network ────────────────────────────────────────────────
info "Starting Fabric network…"
docker compose -f "$FABRIC_DIR/docker-compose.fabric.yml" up -d

info "Waiting for peer and orderer to be ready (15s)…"
sleep 15

# ── 5. Create channel and join peer ───────────────────────────────────────────
ORDERER_TLS="$FABRIC_DIR/crypto-material/ordererOrganizations/orderer.smartledger.local/orderers/orderer.orderer.smartledger.local/tls/ca.crt"
PEER_TLS="$FABRIC_DIR/crypto-material/peerOrganizations/org1.smartledger.local/peers/peer0.org1.smartledger.local/tls/ca.crt"
ADMIN_MSP="$FABRIC_DIR/crypto-material/peerOrganizations/org1.smartledger.local/users/Admin@org1.smartledger.local/msp"

export CORE_PEER_TLS_ENABLED=true
export CORE_PEER_LOCALMSPID=SmartLedgerOrgMSP
export CORE_PEER_MSPCONFIGPATH="$ADMIN_MSP"
export CORE_PEER_ADDRESS=localhost:7051
export CORE_PEER_TLS_ROOTCERT_FILE="$PEER_TLS"

# Create channel
if ! peer channel list 2>/dev/null | grep -q "$CHANNEL_NAME"; then
  info "Creating channel $CHANNEL_NAME…"
  peer channel create \
    -o localhost:7050 \
    -c "$CHANNEL_NAME" \
    -f "$FABRIC_DIR/artifacts/channel.tx" \
    --outputBlock "$FABRIC_DIR/artifacts/${CHANNEL_NAME}.block" \
    --tls \
    --cafile "$ORDERER_TLS"
  ok "Channel created"
else
  ok "Channel already exists"
fi

# Join peer
info "Joining peer to channel…"
peer channel join \
  -b "$FABRIC_DIR/artifacts/${CHANNEL_NAME}.block"
ok "Peer joined channel"

# Update anchor peer
info "Updating anchor peer…"
peer channel update \
  -o localhost:7050 \
  -c "$CHANNEL_NAME" \
  -f "$FABRIC_DIR/artifacts/SmartLedgerOrgAnchors.tx" \
  --tls \
  --cafile "$ORDERER_TLS" || warn "Anchor peer update failed (may already be set)"

# ── 6. Install and instantiate chaincode ───────────────────────────────────────
CC_DIR="$CHAINCODE_DIR/smartledger-cc"

info "Installing chaincode Node.js dependencies…"
(cd "$CC_DIR" && npm install --quiet)

info "Packaging chaincode…"
peer lifecycle chaincode package \
  "$FABRIC_DIR/artifacts/${CC_NAME}.tar.gz" \
  --path "$CC_DIR" \
  --lang node \
  --label "${CC_NAME}_${CC_VERSION}"

info "Installing chaincode on peer…"
peer lifecycle chaincode install \
  "$FABRIC_DIR/artifacts/${CC_NAME}.tar.gz"

# Get package ID
PKG_ID=$(peer lifecycle chaincode queryinstalled 2>&1 | grep "${CC_NAME}_${CC_VERSION}" | awk '{print $3}' | tr -d ',')

if [[ -z "$PKG_ID" ]]; then
  die "Failed to get chaincode package ID after install"
fi
info "Chaincode package ID: $PKG_ID"

# Approve for org
peer lifecycle chaincode approveformyorg \
  -o localhost:7050 \
  --channelID "$CHANNEL_NAME" \
  --name "$CC_NAME" \
  --version "$CC_VERSION" \
  --package-id "$PKG_ID" \
  --sequence "$CC_SEQUENCE" \
  --tls \
  --cafile "$ORDERER_TLS"

# Commit
peer lifecycle chaincode commit \
  -o localhost:7050 \
  --channelID "$CHANNEL_NAME" \
  --name "$CC_NAME" \
  --version "$CC_VERSION" \
  --sequence "$CC_SEQUENCE" \
  --tls \
  --cafile "$ORDERER_TLS" \
  --peerAddresses localhost:7051 \
  --tlsRootCertFiles "$PEER_TLS"

ok "Chaincode $CC_NAME committed to channel $CHANNEL_NAME"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
ok "Fabric network is ready!"
echo ""
echo "  Peer endpoint:    localhost:7051"
echo "  Orderer endpoint: localhost:7050"
echo "  Channel:          $CHANNEL_NAME"
echo "  Chaincode:        $CC_NAME v$CC_VERSION"
echo ""
echo "  Set these in your .env:"
echo "    FABRIC_PEER_ENDPOINT=localhost:7051"
echo "    FABRIC_CHANNEL=$CHANNEL_NAME"
echo "    FABRIC_CHAINCODE=$CC_NAME"
echo "    FABRIC_MSP_ID=SmartLedgerOrgMSP"
echo "    WRITE_GUARD=false"
echo "    PHASE=1"
echo ""
echo "  TLS cert: $PEER_TLS"
echo "  Sign key: $ADMIN_MSP/keystore/..."
