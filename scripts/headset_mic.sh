#!/usr/bin/env bash
# Use a Bluetooth headset (default: Sony WH-1000XM4) as the chat client's microphone, or the
# laptop microphone when no headset is connected. Run it by hand before a session, in its own
# terminal, and leave it open; Ctrl-C puts everything back.
#
# Usage: scripts/headset_mic.sh [--no-hold]
#   HEADSET_MATCH=WH-1000XM4   text to find in the Bluetooth card name or description
#
# Why this exists (docs/HEADSET-MIC.md): the XM4 is playback-only (A2DP, no microphone) until
# something records from it, and it drops back about 20 s after the last recording stops. A
# cold start gave a silent first seconds ("room -120 dBFS"). This switches it to the call
# profile, makes its microphone the default source, and holds it open until you stop.
# It changes only the default source and the headset's profile, and undoes both on exit.
set -euo pipefail

MATCH="${HEADSET_MATCH:-WH-1000XM4}"
HOLD=1
[[ "${1:-}" == "--no-hold" ]] && HOLD=0

command -v pactl > /dev/null || { echo "pactl not found (PipeWire/PulseAudio needed)" >&2; exit 1; }

level_db() { # level of 2 s of a source, in dBFS
    timeout 3 parecord --rate=16000 --channels=1 --format=s16le --file-format=wav -d "$1" /tmp/headset_mic_probe.wav 2> /dev/null || true
    python3 - <<'EOF'
import math, struct, wave
try:
    w = wave.open("/tmp/headset_mic_probe.wav")
    d = w.readframes(w.getnframes())
    s = struct.unpack("<%dh" % (len(d) // 2), d)
    print("%.0f dBFS" % (20 * math.log10(math.sqrt(sum(x * x for x in s) / len(s)) / 32768 + 1e-9)))
except Exception:
    print("no audio")
EOF
}

laptop_source() {
    local all
    all="$(pactl list short sources | awk '$2 ~ /alsa_input/ && $2 !~ /monitor/ {print $2}')"
    # Mic1 is the laptop's own array (Mic2 reads silent on the laptop this was tuned on)
    { grep -m1 'Mic1' <<< "$all" || grep -m1 . <<< "$all"; } || true
}

prev_source="$(pactl get-default-source)"
card="$(pactl list cards | awk -v m="$MATCH" 'BEGIN{IGNORECASE=1}
    /^Card #/ {if (name ~ /^bluez_card/ && block ~ m) {print name; exit}; name=""; block=""}
    /Name: bluez_card/ {name=$2}
    {block = block "\n" $0}
    END {if (name ~ /^bluez_card/ && block ~ m) print name}')"

if [[ -z "$card" ]]; then
    src="$(laptop_source)"
    [[ -n "$src" ]] || { echo "no headset matching '$MATCH' and no laptop microphone found" >&2; exit 1; }
    pactl set-default-source "$src"
    pactl set-source-mute "$src" 0
    echo "No '$MATCH' connected: using the laptop microphone."
    echo "  source: $src  (level now: $(level_db "$src"))"
    echo "Run the client as usual:  uv run --group laptop python -m doc_chat --mic ..."
    echo "(default source left set to the laptop microphone; this script does not need to stay open)"
    exit 0
fi

prev_profile="$(pactl list cards | awk -v c="$card" '$0 ~ "Name: " c {f=1} f && /Active Profile/ {print $3; exit}')"
restored=0
restore() {
    [[ $restored -eq 1 ]] && return
    restored=1
    echo; echo "Restoring: profile ${prev_profile:-a2dp-sink}, default source $prev_source"
    [[ -n "${keep_pid:-}" ]] && kill "$keep_pid" 2> /dev/null || true
    pactl set-card-profile "$card" "${prev_profile:-a2dp-sink}" 2> /dev/null || true
    pactl set-default-source "$prev_source" 2> /dev/null || true
}
trap restore EXIT INT TERM

pactl set-card-profile "$card" headset-head-unit 2> /dev/null \
    || pactl set-card-profile "$card" headset-head-unit-cvsd

src=""
for _ in $(seq 1 20); do # the microphone appears a moment after the profile changes
    src="$(pactl list short sources | awk '$2 ~ /^bluez_input/ {print $2; exit}')"
    [[ -n "$src" ]] && break
    sleep 0.5
done
[[ -n "$src" ]] || { echo "headset connected but its microphone did not appear" >&2; exit 1; }

pactl set-default-source "$src"
pactl set-source-mute "$src" 0
pactl set-source-volume "$src" 100%
echo "Headset: $card"
echo "  profile: $(pactl list cards | awk -v c="$card" '$0 ~ "Name: " c {f=1} f && /Active Profile/ {print $3; exit}')"
echo "  source:  $src  (level now: $(level_db "$src"); wear it on your head and speak close)"
echo "Run the client in another terminal:"
echo "  PULSE_SOURCE=$src uv run --group laptop python -W ignore -m doc_chat --mic --mic-device pulse ..."
echo "Check first that it hears you, not the room: say a phrase and compare with the laptop"
echo "microphone (docs/HEADSET-MIC.md, 'How to check')."

if [[ $HOLD -eq 1 ]]; then
    parecord --rate=16000 --channels=1 --format=s16le --file-format=wav -d "$src" /dev/null 2> /dev/null &
    keep_pid=$!
    echo "Holding the headset microphone open. Ctrl-C to release it and restore the settings."
    wait "$keep_pid" || true
else
    trap - EXIT INT TERM
    echo "(--no-hold: it will fall back to playback-only about 20 s after the last recording stops)"
fi
