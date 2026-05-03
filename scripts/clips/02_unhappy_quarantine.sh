#!/usr/bin/env bash
# Scene 2: Validation Failure - Unhappy Path / Quarantine

source "$(dirname "$0")/lib.sh"

banner "Scene 2: Validation Failure - Quarantine" "~3 min" \
    "Open http://localhost:3000/quarantine" \
    "Show pending events (the 99.9% interest rate contract)" \
    "Expand the quarantined record" \
    "Show rejection code: INVALID_INTEREST_RATE" \
    "Show failure details: 'must be between 0% and 36% APR (got 99.99)'" \
    "Show SLA deadline (24 hours) and event metadata (source, type, id)" \
    "Talk track: NO Approve/Override button - SDG boundary" \
    "Talk track: originating system fixes data and resends; SmartLedger only validates"

ready_prompt
start_recording "unhappy_quarantine" 360
wait_for_stop
