#!/usr/bin/env bash
# read-only check of the laptop side (add "pi" to also ask the Pi; needs the owner's go)
source "$(dirname "$0")/env.sh"
echo "cable: $CABLE_IF $LAPTOP_IP   Pi ping: $(ping -c1 -W2 $PI_V4 >/dev/null 2>&1 && echo ok || echo NO REPLY)"
for p in 8089:router 8770:speech 8781:agent; do ss -ltn | grep -q ":${p%%:*} " && echo "  ${p##*:} (:${p%%:*}) up" || echo "  ${p##*:} (:${p%%:*}) down"; done
curl -s http://127.0.0.1:8089/props 2>/dev/null | grep -o '"model_path":"[^"]*"' | head -1
for p in 8780:pi-speaker 8790:pi-device; do (exec 3<>/dev/tcp/$PI_V4/${p%%:*}) 2>/dev/null && echo "  ${p##*:} (:${p%%:*}) reachable" || echo "  ${p##*:} (:${p%%:*}) not reachable"; done
if [ "${1:-}" = pi ]; then pi_ssh 'tail -n 2 /tmp/pi_speaker.log /tmp/pi_device.log; vcgencmd measure_temp 2>/dev/null'; fi
