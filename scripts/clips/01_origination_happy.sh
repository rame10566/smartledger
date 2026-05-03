#!/usr/bin/env bash
# Scene 1: Contract Origination - Happy Path

source "$(dirname "$0")/lib.sh"

banner "Scene 1: Contract Origination - Happy Path" "~3 min" \
    "Open http://localhost:3000/contracts and show the 3 active contracts" \
    "Click into Toyota Camry contract" \
    "Show lifecycle view: state = active, record count, payment count" \
    "Show state history: originated -> active with timestamp" \
    "Show audit trail: ledger_written, state_transitioned, smartledger-agent" \
    "Talk track: validation gate, single-use proof token, full chain in <5s"

ready_prompt
start_recording "origination_happy" 360
wait_for_stop
