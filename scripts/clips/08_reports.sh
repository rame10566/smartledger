#!/usr/bin/env bash
# Scene 8: Reports

source "$(dirname "$0")/lib.sh"

banner "Scene 8: Reports" "~1 min" \
    "Open http://localhost:3000/reports" \
    "Generate a Portfolio Overview - total contracts, total financed, avg rate, by-state" \
    "Generate a Quarantine Summary - SLA compliance, failure codes, pending counts" \
    "Export to CSV - demonstrate data portability"

ready_prompt
start_recording "reports" 180
wait_for_stop
