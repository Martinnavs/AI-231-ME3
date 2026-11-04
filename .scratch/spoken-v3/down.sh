#!/usr/bin/env bash
# stop everything this folder started, by PID (laptop), and the Pi services (add "pi")
source "$(dirname "$0")/env.sh"
for n in agent speech speech-ed router; do
  f=$D/pids/$n; [ -f "$f" ] && { pid=$(cat "$f"); kill "$pid" 2>/dev/null && echo "stopped $n ($pid)"; rm -f "$f"; }
done
# uv run is the parent: stop whatever still listens on the three ports, by the PID ss reports
for p in 8781 8770 8771 8089; do pid=$(ss -ltnp | awk -v p=":$p$" '$4 ~ p {match($0,/pid=[0-9]+/); print substr($0,RSTART+4,RLENGTH-4)}' | head -1); [ -n "$pid" ] && kill "$pid" && echo "stopped listener on $p ($pid)"; done
[ "${1:-}" = pi ] && pi_ssh 'kill $(cat /tmp/pi_speaker.pid /tmp/pi_device.pid) 2>/dev/null; echo "Pi services stopped"'
sleep 1; ss -ltn | grep -E ":8781 |:8770 |:8771 |:8089 " || echo "laptop ports free"
