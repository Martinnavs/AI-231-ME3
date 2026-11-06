#!/usr/bin/env bash
# pi_netem.sh cellular|bad|offline|off|status: simulate a poor internet link on the Pi's Wi-Fi
# (wlan0 only; the laptop cable stays untouched), for testing how the agent degrades in a demo.
#   cellular: 150 ms +/- 40 ms delay, 1% loss      bad: 400 ms +/- 150 ms, 5% loss      offline: 100% loss
# Needs sudo on the Pi (asks once). Any setting is removed automatically after NETEM_MAX_S
# (default 1800 s) by a transient systemd timer, so the Pi is never left degraded by accident.
source "$(dirname "$0")/lib.sh"
load_env
mode=${1:-status}
max=${NETEM_MAX_S:-1800}
case "$mode" in
  cellular) rule="delay 150ms 40ms distribution normal loss 1%" ;;
  bad)      rule="delay 400ms 150ms distribution normal loss 5%" ;;
  offline)  rule="loss 100%" ;;
  off|status) rule="" ;;
  *) die "usage: pi_netem.sh cellular|bad|offline|off|status" ;;
esac
tc=/usr/sbin/tc
if [ "$mode" = status ]; then
  pi_ssh "$tc qdisc show dev wlan0 | head -1"
  exit 0
fi
cmd="sudo $tc qdisc del dev wlan0 root 2>/dev/null; sudo systemctl stop me3-netem-revert.timer 2>/dev/null; true"
if [ -n "$rule" ]; then
  cmd="$cmd; sudo $tc qdisc add dev wlan0 root netem $rule && sudo systemd-run --quiet --unit=me3-netem-revert --on-active=${max}s $tc qdisc del dev wlan0 root"
fi
cmd="$cmd; $tc qdisc show dev wlan0 | head -1"
if [ "$DRY" = 1 ]; then echo "+ ssh -t $PI_USER@$PI_V4 '$cmd'"; exit 0; fi
ssh -t -i "$PI_KEY" -o UserKnownHostsFile="$PI_HOSTS_FILE" -o HostKeyAlias=pi-cable "$PI_USER@$PI_V4" "$cmd" ||
  die "netem change failed"
[ -n "$rule" ] && echo "stack: Pi Wi-Fi is now '$mode' for at most ${max}s (make pi-netem MODE=off to end it)"
