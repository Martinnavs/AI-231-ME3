#!/usr/bin/env bash
# down.sh [all|agent|laptop|pi]: stop what up.sh started. Laptop services by PID file (the whole process group, because
# `uv run` is the parent of the server); Pi services by the PID files up.sh wrote there. Never matches by name or port.
source "$(dirname "$0")/lib.sh"
MODE=${1:-all}
load_env
PIDS=$STACK_DIR/pids

# marker of what each PID must look like before it is signalled (guards against a reused PID)
marker() { case $1 in router) echo llama-server;; speech) echo lan_speech;; agent) echo agent_server;; apps) echo apps_server;; esac; }

stop_one() {
  local name=$1 f=$PIDS/$1 pid pg
  [ -f "$f" ] || return 0
  pid=$(cat "$f")
  if [ "$DRY" = 1 ]; then echo "+ kill -TERM -- -$pid   ($name, PID file $f)"; return 0; fi
  if [ -d "/proc/$pid" ] && tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q "$(marker "$name")\|uv"; then
    pg=$(ps -o pgid= -p "$pid" | tr -d ' ')
    if [ "$pg" = "$pid" ]; then kill -TERM -- "-$pid" 2>/dev/null; else kill -TERM "$pid" 2>/dev/null; fi
    local i; for i in $(seq 10); do [ -d "/proc/$pid" ] || break; sleep 1; done
    [ -d "/proc/$pid" ] && echo "WARNING: $name ($pid) still running after 10 s; stop it by hand" || echo "stopped $name ($pid)"
  else
    echo "$name: PID $pid is not running (stale file removed)"
  fi
  rm -f "$f"
}

stop_laptop() { local n; for n in agent apps speech router; do stop_one "$n"; done; }
stop_pi() {
  if [ "${PI_START:-ssh}" = systemd ]; then
    pi_ssh 'systemctl --user stop pi-speaker pi-device-mcp; echo "Pi services stopped"'
  else
    pi_ssh 'for f in /tmp/pi_speaker.pid /tmp/pi_device.pid; do [ -f $f ] && kill $(cat $f) 2>/dev/null && rm -f $f; done; echo "Pi services stopped"'
  fi
}
case "$MODE" in
  all)    stop_laptop; stop_pi ;;
  laptop) stop_laptop ;;
  agent)  stop_one agent ;;
  pi)     stop_pi ;;
  *) die "usage: down.sh [all|laptop|agent|pi]" ;;
esac
if [ "$DRY" != 1 ]; then
  ss -ltn | grep -E ":($PORT_AGENT|$PORT_APPS|$PORT_SPEECH|$PORT_ROUTER) " && echo "WARNING: a laptop port is still listening (not started by make up?)" || echo "laptop ports free"
fi
