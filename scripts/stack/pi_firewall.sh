#!/usr/bin/env bash
# Let the Pi reach the laptop's router, speech server and music resolver over the cable (HARNESS=pi). Laptop side,
# needs sudo. Opens the three ports in the firewalld zone of the cable interface, at runtime and permanently.
# The zone may be shared with Wi-Fi (both in "public" by default): the services bind to the cable address only,
# so they are not reachable over Wi-Fi, but the firewall itself no longer blocks those ports there.
source "$(dirname "$0")/lib.sh"
load_env
command -v firewall-cmd >/dev/null || { echo "stack: no firewalld on this laptop, nothing to open"; exit 0; }
[ -n "$CABLE_IF" ] || die "CABLE_IF is empty in $PI_ENV_FILE: run make pi-detect"
zone=$(firewall-cmd --get-zone-of-interface="$CABLE_IF" 2>/dev/null) || zone=$(firewall-cmd --get-default-zone)
ports=("$PORT_ROUTER/tcp" "$PORT_SPEECH/tcp" "$PORT_RESOLVER/tcp")
echo "stack: opening ${ports[*]} in firewalld zone '$zone' (cable interface $CABLE_IF)"
for p in "${ports[@]}"; do
  run sudo firewall-cmd --zone="$zone" --add-port="$p" >/dev/null || die "firewall-cmd failed for $p"
  run sudo firewall-cmd --permanent --zone="$zone" --add-port="$p" >/dev/null || die "firewall-cmd --permanent failed for $p"
done
echo "stack: done; check from the Pi with make status HARNESS=pi"
