#!/usr/bin/env bash
# Scene 3: Customer Profile Update - Integration Layer

source "$(dirname "$0")/lib.sh"

banner "Scene 3: Customer Profile Update via Integration Layer" "~3 min" \
    "Open http://localhost:3000/quarantine (filter: All statuses)" \
    "Talk track: CRM/Portal/LOS -> Integration System -> SmartLedger validates -> LLAS" \
    "Show clean update: CRM address change validated and written" \
    "Show its customer_update record in the contract's audit trail" \
    "Show stale sync: Oracle LOS quarantined with STALE_LOS_SYNC" \
    "Show state-ineligible: payment update on charged-off contract -> CONTRACT_STATE_INELIGIBLE"

ready_prompt
start_recording "customer_update" 360
wait_for_stop
