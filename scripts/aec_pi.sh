#!/usr/bin/env bash
# Software echo cancellation (PipeWire WebRTC) for the Pi, without pactl or root.
# Usage: scripts/aec_pi.sh on|off|status [mic-node-substring] [speaker-node-substring]
# Hosts the echo-cancel module in a separate `pipewire -c` client process, so `off` (or a reboot)
# removes it completely. Creates source va_aec_source and sink va_aec_sink; defaults are untouched.
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
PIDFILE=${XDG_RUNTIME_DIR:-/tmp}/va_aec_pi.pid
CONF=${XDG_RUNTIME_DIR:-/tmp}/va_aec_pi.conf

node() { pw-cli ls Node | awk -v c="$2" -F'"' '/node.name/ {n=$2} /media.class/ && $2==c && n ~ s {print n; exit}' s="$1"; }
running() { [[ -f $PIDFILE ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-status}" in
    on)
        if ! running; then
            mic=$(node "${2:-UACDemo}" Audio/Source)
            spk=$(node "${3:-CD002}" Audio/Sink)
            [[ -n $mic && -n $spk ]] || { echo "mic or speaker node not found" >&2; exit 1; }
            sed -e "s|@MIC@|$mic|" -e "s|@SPEAKER@|$spk|" "$HERE/config/pipewire-echo-cancel.conf" > "$CONF"
            pipewire -c "$CONF" > "${CONF%.conf}.log" 2>&1 &
            echo $! > "$PIDFILE"
            for _ in $(seq 20); do
                sleep 0.5
                pw-cli ls Node | grep -q va_aec_source && break
            done
        fi
        ;;
    off)
        if running; then kill "$(cat "$PIDFILE")"; fi
        rm -f "$PIDFILE"
        ;;
    status) ;;
    *) echo "usage: $0 on|off|status [mic] [speaker]" >&2; exit 2 ;;
esac
if running && pw-cli ls Node | grep -q va_aec_source; then echo "echo cancel: ON (va_aec_source, va_aec_sink)"; else echo "echo cancel: off"; fi
