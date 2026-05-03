#!/usr/bin/env bash
# Scene 6: Party Portal - Smart Data Gateway (external)

source "$(dirname "$0")/lib.sh"

banner "Scene 6: Party Portal - SDG (external)" "~3 min" \
    "Open http://localhost:3000/party (clean, distinct from ops dashboard)" \
    "Login as Lender: SMARTLEDGER_FINANCE / role 'Lender / Capital Finance'" \
    "Show contracts list with green 'On-chain' badge per contract" \
    "Click into a contract - hero is the Blockchain Proof box (green)" \
    "Show Fabric tx_id, SHA-256 data_hash, ledger timestamp" \
    "Click 'Copy' on the tx_id (will be used in Scene 7)" \
    "Sign out, log in as Borrower with a CUST-XXXXXX id" \
    "Show: borrower sees only own contract; direct access to others returns 403" \
    "Talk track: independent visibility, cryptographic proof, hard access boundary"

ready_prompt
start_recording "party_portal" 360
wait_for_stop
