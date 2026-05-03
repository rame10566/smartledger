#!/usr/bin/env bash
# Scene 5: Smart Data Gateway - Party-Based Access Control (internal dashboard)

source "$(dirname "$0")/lib.sh"

banner "Scene 5: Smart Data Gateway - PBAC (internal)" "~1 min" \
    "Use the identity selector (top-right dropdown on the dashboard)" \
    "Switch to 'Borrower (James Carter)' - show only own contracts visible" \
    "Switch to 'Auditor' - full read access across all contracts" \
    "Switch back to 'Admin' - full operational access" \
    "Talk track: PBAC at API layer; every access logged to audit.access_log"

ready_prompt
start_recording "pbac" 180
wait_for_stop
