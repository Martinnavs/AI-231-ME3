#!/usr/bin/env bash
# Software echo cancellation (WebRTC AEC) through PipeWire, for laptop speakers + microphone.
# Usage: scripts/aec.sh on|off|status
# Creates a source "va_aec_source" and a sink "va_aec_sink". It never changes your default
# devices; the chat client opts in with --aec. A hardware-AEC speakerphone does not need this.
set -euo pipefail

SRC=va_aec_source
SNK=va_aec_sink

loaded() { pactl list short modules | awk '$2 == "module-echo-cancel" && /va_aec_/ {print $1}'; }

case "${1:-status}" in
    on)
        if [[ -z "$(loaded)" ]]; then
            pactl load-module module-echo-cancel aec_method=webrtc \
                source_name=$SRC sink_name=$SNK use_master_format=1 > /dev/null
        fi
        ;;
    off)
        for id in $(loaded); do pactl unload-module "$id"; done
        ;;
    status) ;;
    *) echo "usage: $0 on|off|status" >&2; exit 2 ;;
esac

if [[ -n "$(loaded)" ]]; then
    echo "echo cancel: ON  (source $SRC, sink $SNK)"
else
    echo "echo cancel: off"
fi
echo "default sink:   $(pactl get-default-sink)"
echo "default source: $(pactl get-default-source)"
