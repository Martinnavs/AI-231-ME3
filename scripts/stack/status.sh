#!/usr/bin/env bash
# status.sh [pi]: read-only. Cable, Pi ping, ports; with "pi" also the Pi's logs and temperature over ssh.
source "$(dirname "$0")/lib.sh"
load_env
if [ "$DRY" = 1 ]; then echo "+ ping -c1 $PI_V4; ss -ltn; /dev/tcp checks; curl router /props"; [ "${1:-}" = pi ] && pi_ssh 'tail -n 2 /tmp/pi_speaker.log /tmp/pi_device.log'; exit 0; fi
echo "cable: $CABLE_IF $LAPTOP_IP   Pi ping: $(ping -c1 -W2 "$PI_V4" >/dev/null 2>&1 && echo ok || echo NO REPLY)   (config/pi.env detected ${DETECTED_AT:-?})"
for p in "$PORT_ROUTER:router" "$PORT_SPEECH:speech" "$PORT_AGENT:agent" "$PORT_APPS:apps"; do
  port_listening "${p%%:*}" && echo "  ${p##*:} (:${p%%:*}) up" || echo "  ${p##*:} (:${p%%:*}) down"
done
{ [ -r "$TOKEN_FILE" ] && printf 'header = "Authorization: Bearer %s"\n' "$(cat "$TOKEN_FILE")"; } |
  curl -s -K - "http://127.0.0.1:$PORT_ROUTER/props" 2>/dev/null | grep -o '"model_path":"[^"]*"' | head -1
for p in "$PORT_PI_SPEAKER:pi-speaker" "$PORT_PI_DEVICE:pi-device"; do
  pi_port_open "${p%%:*}" && echo "  ${p##*:} (:${p%%:*}) reachable" || echo "  ${p##*:} (:${p%%:*}) not reachable"
done
[ "${1:-}" = pi ] && pi_ssh 'tail -n 2 /tmp/pi_speaker.log /tmp/pi_device.log; vcgencmd measure_temp 2>/dev/null'
exit 0
