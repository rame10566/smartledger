#!/usr/bin/env bash
# Scene 7: Independent Verification via Hyperledger Explorer

source "$(dirname "$0")/lib.sh"

banner "Scene 7: Independent Verification - Hyperledger Explorer" "~2 min" \
    "Have a tx_id copied from the Party Portal (from Scene 6)" \
    "Open http://localhost:8090 and log in (exploreradmin / exploreradminpw)" \
    "Show Dashboard: total blocks, transactions, peers, chaincodes" \
    "Show Channels: smartledger-channel" \
    "Show Chaincodes: smartledger-cc + _lifecycle" \
    "Search for the tx_id in the Transactions tab" \
    "Show: block number, block hash, previous-block hash, WriteRecord call, read/write set" \
    "Switch to Block view - show hash chain linking blocks" \
    "Talk track: permissioned chain, but each org runs its own peer; never trust SmartLedger - verify chain directly"

ready_prompt
start_recording "explorer_verification" 240
wait_for_stop
