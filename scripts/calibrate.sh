#!/usr/bin/env bash
# Room calibration and the six baseline phrases from one microphone, in one command.
# Usage: scripts/calibrate.sh [--headset]     (normally via `make calibrate [HEADSET=1]`)
#   default      the laptop microphone
#   --headset    the Bluetooth headset (Sony WH-1000XM4 unless HEADSET_MATCH says otherwise);
#                falls back to the laptop microphone when it is not connected
# Needs you at the microphone: it asks for quiet, then for each phrase. Writes
# out/calibration.json and out/calibration-baseline.wav (docs/HEADSET-MIC.md).
set -euo pipefail
cd "$(dirname "$0")/.."

src_file="$(mktemp)"
log="$(mktemp)"
hold_pid=""
cleanup() {
    [[ -n "$hold_pid" ]] && kill "$hold_pid" 2> /dev/null && wait "$hold_pid" 2> /dev/null || true
    rm -f "$src_file" "$log"
}
trap cleanup EXIT INT TERM

if [[ "${1:-}" == "--headset" ]]; then
    scripts/headset_mic.sh --source-file "$src_file" > "$log" 2>&1 &
    hold_pid=$!
else
    HEADSET_MATCH="no-headset-requested" scripts/headset_mic.sh --source-file "$src_file" > "$log" 2>&1 || {
        cat "$log" >&2; exit 1; }
fi

for _ in $(seq 1 30); do # the source name appears once the microphone is ready
    [[ -s "$src_file" ]] && break
    kill -0 "${hold_pid:-$$}" 2> /dev/null || break
    sleep 0.5
done
[[ -s "$src_file" ]] || { echo "no microphone became ready:" >&2; cat "$log" >&2; exit 1; }
src="$(cat "$src_file")"
case "$src" in bluez_input*) echo "Using the headset microphone ($src)" ;; *) echo "Using the laptop microphone ($src)" ;; esac

PULSE_SOURCE="$src" uv run --group laptop python -W ignore -m doc_chat.calibrate \
    --mic-device pulse --baseline
