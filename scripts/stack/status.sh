#!/usr/bin/env bash
# status.sh [pi]: read-only. Cable, Pi ping, ports; with "pi" also the Pi's logs and temperature over ssh.
source "$(dirname "$0")/lib.sh"
load_env
if [ "$DRY" = 1 ]; then echo "+ ping -c1 $PI_V4; ss -ltn; /dev/tcp checks; curl router /props"; [ "${1:-}" = pi ] && pi_ssh 'tail -n 2 /tmp/pi_speaker.log /tmp/pi_device.log'; exit 0; fi
echo "cable: $CABLE_IF $LAPTOP_IP   Pi ping: $(ping -c1 -W2 "$PI_V4" >/dev/null 2>&1 && echo ok || echo NO REPLY)   (config/pi.env detected ${DETECTED_AT:-?})"
if [ "$HARNESS" = pi ]; then LAPTOP_PORTS=("$PORT_ROUTER:router" "$PORT_SPEECH:speech" "$PORT_RESOLVER:resolver"); else LAPTOP_PORTS=("$PORT_ROUTER:router" "$PORT_SPEECH:speech" "$PORT_AGENT:agent" "$PORT_APPS:apps"); fi
for p in "${LAPTOP_PORTS[@]}"; do
  port_listening "${p%%:*}" && echo "  ${p##*:} (:${p%%:*}) up" || echo "  ${p##*:} (:${p%%:*}) down"
done
{ [ -r "$TOKEN_FILE" ] && printf 'header = "Authorization: Bearer %s"\n' "$(cat "$TOKEN_FILE")"; } |
  curl -s -K - "http://$ROUTER_HOST:$PORT_ROUTER/props" 2>/dev/null | grep -o '"model_path":"[^"]*"' | head -1
if [ "$HARNESS" = pi ]; then PI_PORTS=("$PORT_PI_SPEAKER:pi-speaker" "$PORT_AGENT:pi-agent" "$PORT_APPS:pi-apps"); else PI_PORTS=("$PORT_PI_SPEAKER:pi-speaker" "$PORT_PI_DEVICE:pi-device"); fi
for p in "${PI_PORTS[@]}"; do
  pi_port_open "${p%%:*}" && echo "  ${p##*:} (:${p%%:*}) reachable" || echo "  ${p##*:} (:${p%%:*}) not reachable"
done
[ "${1:-}" = pi ] && pi_ssh 'tail -n 2 /tmp/pi_speaker.log /tmp/pi_device.log; vcgencmd measure_temp 2>/dev/null'
exit 0
