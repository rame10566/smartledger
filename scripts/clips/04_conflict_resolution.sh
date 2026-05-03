#!/usr/bin/env bash
# Scene 4: Conflict Detection & Resolution

source "$(dirname "$0")/lib.sh"

banner "Scene 4: Conflict Detection & Resolution" "~3 min" \
    "Open http://localhost:3000/conflicts" \
    "Show conflict pair: CRM and Customer Portal both submitted address changes" \
    "Click to expand - side-by-side comparison" \
    "Show: Source A (CRM), Source B (Portal), Current LLAS value" \
    "Talk track: SmartLedger detected conflict, quarantined both, LLAS Admin adjudicates" \
    "Demonstrate resolution: select winning value, enter reason, click Resolve" \
    "Show: conflict cleared, audit trail updated, LLAS profile updated" \
    "Talk track: only place a human decides - and selected value still goes through validation"

ready_prompt
start_recording "conflict_resolution" 360
wait_for_stop
