#!/usr/bin/env bash
# up.sh [all|laptop|pi|agent]: router, speech server, Pi tools, agent server (in that order, so the agent sees the Pi tools).
#   all = everything   laptop = router + speech + agent, no Pi   pi = Pi speaker and device tools only
#   agent = (re)start only the agent server
# The router (--api-key-file), the speech server and the agent server all require the session token (the delegate sandbox shares loopback).
# Variables (make passes them): ROUTER_MODEL ROUTER_THREADS GGUF_TOOLS ENTITIES KITTEN_VOICE KITTEN_SITE SPEECH_ARGS AGENT_ARGS
#   PI_START=ssh|systemd  PI_RUN_SECONDS  DRY=1 (print the commands only)
source "$(dirname "$0")/lib.sh"
MODE=${1:-all}
load_env
ROUTER_MODEL=${ROUTER_MODEL:-router-350m-kd-web3-s0-unverified}
ROUTER_THREADS=${ROUTER_THREADS:-3}
GGUF_TOOLS=${GGUF_TOOLS:-$HOME/work/gguf-tools}
ENTITIES=${ENTITIES:-config/entities.json}
KITTEN_VOICE=${KITTEN_VOICE:-Jasper}
KITTEN_SITE=${KITTEN_SITE:-$HOME/work/tts-bakeoff/venv/lib/python3.12/site-packages}
PI_START=${PI_START:-ssh}
PI_RUN_SECONDS=${PI_RUN_SECONDS:-7200}
LOGS=$STACK_DIR/logs; PIDS=$STACK_DIR/pids
cd "$STACK_REPO" || exit 1

# bg NAME LOG CMD...: start in its own process group, record the PID
bg() {
  local name=$1 log=$2; shift 2
  if [ "$DRY" = 1 ]; then printf '+ setsid'; printf ' %q' "$@"; printf ' > %s   (pid -> %s/%s)\n' "$log" "$PIDS" "$name"; return 0; fi
  setsid "$@" >"$log" 2>&1 < /dev/null &
  echo $! >"$PIDS/$name"
}
# wait_for DESC LOG SECONDS TEST...: poll, fail loudly with the log path
wait_for() {
  local desc=$1 log=$2 n=$3; shift 3
  if [ "$DRY" = 1 ]; then echo "+ (wait up to ${n}s for: $desc)"; return 0; fi
  local i; for i in $(seq "$n"); do "$@" && { echo "$desc: up"; return 0; }; sleep 1; done
  die "$desc FAILED, see $log"
}
check_free() {
  [ "$DRY" = 1 ] && return 0
  local p; for p in "$@"; do port_listening "$p" && die "port $p is already in use: run make down first"; done
  return 0
}
router_health() { curl -sf "http://127.0.0.1:$PORT_ROUTER/health" >/dev/null; }

start_router() {
  check_free "$PORT_ROUTER"
  need_token
  local g=$GGUF_TOOLS model=$GGUF_TOOLS/models/$ROUTER_MODEL/gguf/router-Q4_K_M.gguf
  [ "$DRY" = 1 ] || [ -f "$model" ] || die "router model not found: $model (ROUTER_MODEL=$ROUTER_MODEL)"
  echo "router model: $ROUTER_MODEL   cable address: $LAPTOP_IP"
  LD_LIBRARY_PATH=$g/llama.cpp/build/bin bg router "$LOGS/router.log" "$g/llama.cpp/build/bin/llama-server" --jinja -c 8192 -np 1 -fa on \
    -t "$ROUTER_THREADS" -tb "$ROUTER_THREADS" -m "$model" --port "$PORT_ROUTER" --api-key-file "$TOKEN_FILE"
  wait_for router "$LOGS/router.log" 60 router_health
}
start_speech() {
  check_free "$PORT_SPEECH"
  need_token
  # shellcheck disable=SC2086
  KITTEN_SITE=$KITTEN_SITE bg speech "$LOGS/speech.log" uv run --offline --group laptop python -W ignore -m lan_speech \
    --host "$LAPTOP_IP" --port "$PORT_SPEECH" --stt-model parakeet --entities "$ENTITIES" --tts-engine kitten \
    --kitten-voice "$KITTEN_VOICE" ${SPEECH_ARGS:-}
  wait_for "speech server" "$LOGS/speech.log" 120 port_listening "$PORT_SPEECH"
}
start_agent() {
  need_token
  # shellcheck disable=SC2086
  say_dry "agent environment: PI_DEVICE_URL=http://$PI_V4:$PORT_PI_DEVICE/mcp"
  (cd pipeline && PI_DEVICE_URL="http://$PI_V4:$PORT_PI_DEVICE/mcp" bg agent "$LOGS/agent.log" uv run python -m voice_pipeline.agent_server --ipv4 --hedge -1 \
    --router-url "http://127.0.0.1:$PORT_ROUTER" ${AGENT_ARGS:-})
  agent_ready() { grep -q "agent server on" "$LOGS/agent.log"; }
  wait_for "agent server" "$LOGS/agent.log" 120 agent_ready
  [ "$DRY" = 1 ] || { grep "agent server on" "$LOGS/agent.log"
    grep "left out" "$LOGS/agent.log" && echo "WARNING: a Pi tool was left out: run make up-pi, then make restart-agent"; true; }
}
stop_agent() {
  [ "$DRY" = 1 ] && { echo "+ (stop the agent started by the last make up, by PID file)"; return 0; }
  "$STACK_REPO/scripts/stack/down.sh" agent
}
start_pi() {
  if [ "$DRY" != 1 ]; then
    local local_sum pi_sum
    local_sum=$(sha256sum "$TOKEN_FILE" | cut -c1-12)
    pi_sum=$(pi_ssh 'sha256sum "$HOME/.config/lan_speech_token" | cut -c1-12') || die "cannot reach the Pi over ssh: run make pi-detect"
    [ "$local_sum" = "$pi_sum" ] || die "TOKEN MISMATCH (laptop $local_sum, Pi $pi_sum): make a fresh pair, docs/PI-BRIDGE.md"
    echo "session token: same on both sides ($local_sum)"
  else
    echo "+ (compare the first 12 characters of sha256(token) on both sides)"
  fi
  if [ "$PI_START" = systemd ]; then
    pi_ssh 'systemctl --user start pi-speaker pi-device-mcp' || die "systemd start failed on the Pi (was deploy/install-pi.sh run?)"
  else
    local ld=${PI_LD_LIBRARY_PATH:-}
    pi_ssh "cd $(printf '%q' "$PI_REPO") && export LAN_SPEECH_TOKEN=\$(cat ~/.config/lan_speech_token) LD_LIBRARY_PATH=$(printf '%q' "$ld")
      nohup timeout $PI_RUN_SECONDS .venv/bin/python -u scripts/pi_speaker.py --device $(printf '%q' "$PI_AUDIO_DEVICE") --rate ${PI_SPEAKER_RATE:-48000} --port $PORT_PI_SPEAKER > /tmp/pi_speaker.log 2>&1 < /dev/null &
      echo \$! > /tmp/pi_speaker.pid
      nohup timeout $PI_RUN_SECONDS .venv/bin/python -u scripts/pi_device_mcp.py --port $PORT_PI_DEVICE > /tmp/pi_device.log 2>&1 < /dev/null &
      echo \$! > /tmp/pi_device.pid
      sleep 4; tail -n 3 /tmp/pi_speaker.log /tmp/pi_device.log" || die "starting the Pi services over ssh failed"
  fi
  if [ "$DRY" = 1 ]; then echo "+ (wait for $PI_V4:$PORT_PI_SPEAKER and :$PORT_PI_DEVICE)"; return 0; fi
  local p name
  for p in "$PORT_PI_SPEAKER:pi speaker" "$PORT_PI_DEVICE:pi device tools"; do
    name=${p#*:}
    local i; for i in 1 2 3 4 5 6 7 8; do pi_port_open "${p%%:*}" && break; sleep 1; done
    pi_port_open "${p%%:*}" && echo "$name :${p%%:*} reachable" || die "$name :${p%%:*} not reachable: ssh in and read /tmp/pi_speaker.log /tmp/pi_device.log"
  done
}

case "$MODE" in all | laptop) check_free "$PORT_ROUTER" "$PORT_SPEECH" "$PORT_AGENT" ;; esac
case "$MODE" in
  all)    start_router; start_speech; start_pi; start_agent ;;
  laptop) start_router; start_speech; start_agent ;;
  pi)     start_pi ;;
  agent)  stop_agent; start_agent ;;
  *) die "usage: up.sh [all|laptop|pi|agent]" ;;
esac
