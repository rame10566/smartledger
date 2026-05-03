#!/usr/bin/env bash
# Shared helpers for demo clip recordings.
# Source this from each scene script.

set -euo pipefail

CLIPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${CLIPS_DIR}/output"
mkdir -p "${OUT_DIR}"

: "${MAX_SECONDS:=900}"   # hard cap per clip (15 min). Override per scene.
: "${DISPLAY_ID:=1}"      # main display
: "${SHOW_CLICKS:=1}"     # 1 = show mouse clicks in recording

REC_PID=""
REC_FILE=""

banner() {
    local title="$1"
    local target="$2"
    shift 2
    cat <<EOF

================================================================
${title}
================================================================
Target duration: ${target}
Output dir:      ${OUT_DIR}

Steps to perform during the clip:
EOF
    local n=1
    for step in "$@"; do
        printf "  %d. %s\n" "$n" "$step"
        n=$((n+1))
    done
    cat <<EOF

Make sure:
  - Terminal hosting Claude Code has Screen Recording permission
    (System Settings -> Privacy & Security -> Screen Recording)
  - Windows for this scene are framed and visible NOW
  - Audio is recorded separately if you want voiceover

EOF
}

ready_prompt() {
    local prompt="${1:-Press ENTER to start recording}"
    printf "%s ... " "${prompt}"
    read -r _
}

start_recording() {
    local name="$1"
    local max="${2:-$MAX_SECONDS}"
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    REC_FILE="${OUT_DIR}/${ts}_${name}.mov"

    local args=( -V "${max}" -D "${DISPLAY_ID}" )
    if [[ "${SHOW_CLICKS}" == "1" ]]; then
        args+=( -k )
    fi

    echo "Recording -> ${REC_FILE}"
    echo "(Hard cap: ${max}s. Press ENTER to stop early.)"
    screencapture "${args[@]}" "${REC_FILE}" &
    REC_PID=$!
    # Give screencapture a moment to actually start writing
    sleep 1
}

stop_recording() {
    if [[ -z "${REC_PID}" ]]; then
        return 0
    fi
    if kill -0 "${REC_PID}" 2>/dev/null; then
        kill -INT "${REC_PID}" 2>/dev/null || true
    fi
    wait "${REC_PID}" 2>/dev/null || true
    REC_PID=""

    if [[ -f "${REC_FILE}" ]]; then
        local size_kb
        size_kb=$(($(stat -f%z "${REC_FILE}") / 1024))
        echo "Saved: ${REC_FILE} (${size_kb} KB)"
        if [[ "${size_kb}" -lt 50 ]]; then
            cat <<EOF

WARNING: The clip is unusually small (${size_kb} KB). Common causes:
  - Screen Recording permission not granted to this terminal app
  - Screen was locked / display asleep during the recording
Verify with:  open "${REC_FILE}"
EOF
        fi
    else
        echo "WARNING: expected output file was not created at ${REC_FILE}"
    fi
}

# Stop recording on script exit (covers Ctrl-C / errors)
trap 'stop_recording' EXIT

wait_for_stop() {
    local prompt="${1:-Press ENTER when the demo step is complete to stop recording}"
    printf "%s ... " "${prompt}"
    read -r _
    stop_recording
}
