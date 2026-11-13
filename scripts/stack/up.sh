#!/usr/bin/env bash
# up.sh [all|laptop|pi|agent|apps]: router, speech server, Pi tools, apps server, agent server (in that order, so the agent sees the Pi tools).
#   all = everything   laptop = router + speech + agent, no Pi   pi = Pi speaker and device tools only
#   agent = (re)start only the agent server
# The router (--api-key-file), the speech server and the agent server all require the session token (the delegate sandbox shares loopback).
# Variables (make passes them): ROUTER_MODEL ROUTER_THREADS GGUF_TOOLS ENTITIES KITTEN_VOICE KITTEN_SITE SPEECH_ARGS AGENT_ARGS
#   PI_START=ssh|systemd  PI_RUN_SECONDS  DRY=1 (print the commands only)
#   CALIBRATE=auto|1|0 (default auto: after the stack is up, recalibrate when out/calibration.json is missing, older than
#   CALIBRATE_MAX_AGE_H hours (default 12) or short; 1 always, 0 never; asks first, and only on a terminal)  HEADSET=1
source "$(dirname "$0")/lib.sh"
MODE=${1:-all}
load_env
ROUTER_MODEL=${ROUTER_MODEL:-router-350m-kd-web4-s0-unverified}
ROUTER_THREADS=${ROUTER_THREADS:-3}
GGUF_TOOLS=${GGUF_TOOLS:-$HOME/work/gguf-tools}
ENTITIES=${ENTITIES:-config/entities-baby-onit.json}
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
router_health() { curl -sf "http://$ROUTER_HOST:$PORT_ROUTER/health" >/dev/null; }

# tune: the laptop's power profile (no sudo), and on the Pi the CPU governor and Wi-Fi power save
# without asking for a password (sudo -n); if the Pi needs it, `make pi-tune` once makes it permanent
tune() {
  [ "$DRY" = 1 ] && { echo "+ tune: powerprofilesctl set performance; Pi governor and Wi-Fi power save"; return 0; }
  command -v powerprofilesctl >/dev/null && powerprofilesctl set performance 2>/dev/null
  local state
  state=$(pi_ssh 'g=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)
    [ "$g" = performance ] || { for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance | sudo -n tee "$f" >/dev/null 2>&1; done; }
    /usr/sbin/iw dev wlan0 get power_save 2>/dev/null | grep -q on && sudo -n /usr/sbin/iw dev wlan0 set power_save off 2>/dev/null
    echo "$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor) $(/usr/sbin/iw dev wlan0 get power_save 2>/dev/null | cut -d: -f2)"' 2>/dev/null) || state=""
  echo "tuning: laptop $(powerprofilesctl get 2>/dev/null || echo ?), Pi governor/Wi-Fi power save: ${state:-unreachable}"
  case "$state" in performance*off) ;; "") ;; *) echo "stack: the Pi is not tuned; run make pi-tune once (asks for its sudo password)" ;; esac
}
start_router() {
  check_free "$PORT_ROUTER"
  need_token
  local g=$GGUF_TOOLS model=$GGUF_TOOLS/models/$ROUTER_MODEL/gguf/router-Q4_K_M.gguf
  [ "$DRY" = 1 ] || [ -f "$model" ] || die "router model not found: $model (ROUTER_MODEL=$ROUTER_MODEL)"
  echo "router model: $ROUTER_MODEL   cable address: $LAPTOP_IP"
  LD_LIBRARY_PATH=$g/llama.cpp/build/bin bg router "$LOGS/router.log" "$g/llama.cpp/build/bin/llama-server" --jinja -c 8192 -np 1 -fa on \
    -t "$ROUTER_THREADS" -tb "$ROUTER_THREADS" -m "$model" --host "$ROUTER_HOST" --port "$PORT_ROUTER" --api-key-file "$TOKEN_FILE"
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
wait_pi_agent() {
  if [ "$DRY" = 1 ]; then echo "+ (wait for $PI_V4:$PORT_AGENT)"; return 0; fi
  local i; for i in $(seq 90); do pi_port_open "$PORT_AGENT" && { echo "Pi agent server :$PORT_AGENT reachable"; return 0; }; sleep 2; done
  die "the Pi agent server did not come up: ssh in, journalctl --user -u pi-agent"
}
start_resolver() {
  # HARNESS=pi: the music search (yt-dlp) stays on the laptop; the Pi's music MCP server calls it with the token
  check_free "$PORT_RESOLVER"
  need_token
  bg resolver "$LOGS/resolver.log" uv run --offline --group laptop python -m voice_agent.music_resolver --host "$LAPTOP_IP" --port "$PORT_RESOLVER"
  resolver_ready() { port_listening "$PORT_RESOLVER"; }
  wait_for "music resolver" "$LOGS/resolver.log" 30 resolver_ready
}
start_librespot() {
  [ "${MUSIC_SOURCE:-$(music_source)}" = spotify ] || return 0
  local fifo=${SPOTIFY_FIFO:-$HOME/.cache/me3/librespot.pcm}
  if [ "${SPOTIFY_PLAYER:-app}" = app ]; then
    # the Spotify desktop app plays into a private sink; doc_chat records its monitor (librespot's
    # audio keys are refused for some accounts, librespot issue #1664), so no FIFO may remain
    rm -f "$fifo"
    # a librespot still running (e.g. its one-time login) becomes the active Spotify device and the
    # app then only remote-controls it: playback goes silent
    pgrep -x librespot >/dev/null 2>&1 && echo "stack: a librespot is running; stop it (pkill -x librespot) or Spotify plays silently"
    pactl list short sinks 2>/dev/null | grep -q "\b${SPOTIFY_SINK:-me3_spotify}\b" ||
      run pactl load-module module-null-sink "sink_name=${SPOTIFY_SINK:-me3_spotify}" "sink_properties=device.description=ME3-Spotify" >/dev/null
    return 0
  fi
  if [ "$DRY" != 1 ] && ! command -v librespot >/dev/null 2>&1; then
    echo "stack: librespot not installed (MUSIC_SOURCE=spotify)"
    return 0
  fi
  mkdir -p "$(dirname "$fifo")" "$HOME/.cache/librespot"
  [ -p "$fifo" ] || mkfifo -m 600 "$fifo" 2>/dev/null || true
  # librespot's first login (Spotify Connect) is the owner's job
  bg librespot "$LOGS/librespot.log" librespot --name "ME3 Laptop" --backend pipe --device "$fifo" --bitrate 160 --initial-volume 100 --cache "$HOME/.cache/librespot"
}
start_pi_harness() {
  [ "$PI_START" = systemd ] && { wait_pi_agent; return 0; }  # the pi-agent unit was started by start_pi
  # HARNESS=pi: the agent server and the apps server run on the Pi (the device tools are a local child), under the same timeout as the speaker
  local env="LAN_SPEECH_TOKEN=\$(cat ~/.config/lan_speech_token) MCP_PYTHON=\$PWD/.venv-mcp/bin/python PYTHONPATH=\$PWD/pipeline/src:\$PWD/src ROUTER_URL=http://$LAPTOP_IP:$PORT_ROUTER MUSIC_RESOLVER_URL=http://$LAPTOP_IP:$PORT_RESOLVER APPS_PUBLIC_HOST=$PI_V4 PORT_APPS=$PORT_APPS"
  [ "${MUSIC_SOURCE:-$(music_source)}" = spotify ] && env="$env MUSIC_SOURCE=spotify"
  pi_ssh "cd $(printf '%q' "$PI_REPO") && mkdir -p ~/agent_sandbox/workspace/apps && export $env
    nohup timeout $PI_RUN_SECONDS .venv-mcp/bin/python -u -m voice_agent.apps_server --host $PI_V4 --port $PORT_APPS > /tmp/pi_apps.log 2>&1 < /dev/null &
    echo \$! > /tmp/pi_apps.pid
    nohup timeout $PI_RUN_SECONDS .venv-mcp/bin/python -u -m home_dashboard --host $PI_V4 --port $PORT_DASH > /tmp/pi_dash.log 2>&1 < /dev/null &
    echo \$! > /tmp/pi_dash.pid
    nohup timeout $PI_RUN_SECONDS .venv-agent/bin/python -u -m voice_pipeline.agent_server --host $PI_V4 --port $PORT_AGENT --pi-url local --apps-host $PI_V4 --ipv4 --hedge 0 --live http://$PI_V4:$PORT_DASH ${AGENT_ARGS:-} > /tmp/pi_agent.log 2>&1 < /dev/null &
    echo \$! > /tmp/pi_agent.pid
    sleep 6; tail -n 3 /tmp/pi_apps.log /tmp/pi_agent.log" || die "starting the Pi agent over ssh failed"
  if [ "$DRY" = 1 ]; then echo "+ (wait for $PI_V4:$PORT_AGENT)"; return 0; fi
  local i; for i in $(seq 60); do pi_port_open "$PORT_AGENT" && { echo "Pi agent server :$PORT_AGENT reachable"; return 0; }; sleep 2; done
  die "the Pi agent server did not come up: ssh in and read /tmp/pi_agent.log"
}
start_apps() {
  [ "$HARNESS" = pi ] && return 0   # served from the Pi (start_pi_harness)
  # the web apps the delegate writes (apps/<name>/index.html), 127.0.0.1 only, CSP on every response
  mkdir -p "$HOME/agent_sandbox/workspace/apps"
  bg apps "$LOGS/apps.log" uv run --offline python -m voice_agent.apps_server --port "$PORT_APPS"
  apps_ready() { port_listening "$PORT_APPS"; }
  wait_for "apps server" "$LOGS/apps.log" 30 apps_ready
}
start_agent() {
  if [ "$HARNESS" = pi ]; then start_pi_harness; return 0; fi
  need_token
  # shellcheck disable=SC2086
  say_dry "agent environment: PI_DEVICE_URL=http://$PI_V4:$PORT_PI_DEVICE/mcp"
  (cd pipeline && PI_DEVICE_URL="http://$PI_V4:$PORT_PI_DEVICE/mcp" bg agent "$LOGS/agent.log" uv run python -m voice_pipeline.agent_server --ipv4 --hedge 0 \
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
    local units="pi-speaker pi-device-mcp"; [ "$HARNESS" = pi ] && units="pi-speaker pi-agent"
    pi_ssh "systemctl --user restart $units" || die "systemd start failed on the Pi (was deploy/install-pi.sh run?)"
  else
    if [ "$DRY" != 1 ] && pi_ssh 'systemctl --user is-active --quiet pi-speaker' 2>/dev/null; then
      die "the pi-speaker service already runs on the Pi (port $PORT_PI_SPEAKER): use PI_START=systemd, the default with HARNESS=pi"
    fi
    local ld=${PI_LD_LIBRARY_PATH:-} device_start=""
    # HARNESS=pi: the device tools are a stdio child of the Pi's agent server, not a service of their own
    [ "$HARNESS" = pi ] || device_start="nohup timeout $PI_RUN_SECONDS .venv/bin/python -u scripts/pi_device_mcp.py --port $PORT_PI_DEVICE > /tmp/pi_device.log 2>&1 < /dev/null & echo \$! > /tmp/pi_device.pid"
    pi_ssh "cd $(printf '%q' "$PI_REPO") && export LAN_SPEECH_TOKEN=\$(cat ~/.config/lan_speech_token) LD_LIBRARY_PATH=$(printf '%q' "$ld")
      nohup timeout $PI_RUN_SECONDS .venv/bin/python -u scripts/pi_speaker.py --device $(printf '%q' "$PI_AUDIO_DEVICE") --rate ${PI_SPEAKER_RATE:-48000} --port $PORT_PI_SPEAKER > /tmp/pi_speaker.log 2>&1 < /dev/null &
      echo \$! > /tmp/pi_speaker.pid
      $device_start
      sleep 4; tail -n 3 /tmp/pi_speaker.log /tmp/pi_device.log" || die "starting the Pi services over ssh failed"
  fi
  if [ "$DRY" = 1 ]; then echo "+ (wait for $PI_V4:$PORT_PI_SPEAKER and :$PORT_PI_DEVICE)"; return 0; fi
  local p name
  local ports=("$PORT_PI_SPEAKER:pi speaker" "$PORT_PI_DEVICE:pi device tools")
  [ "$HARNESS" = pi ] && ports=("$PORT_PI_SPEAKER:pi speaker")
  for p in "${ports[@]}"; do
    name=${p#*:}
    local i; for i in 1 2 3 4 5 6 7 8; do pi_port_open "${p%%:*}" && break; sleep 1; done
    pi_port_open "${p%%:*}" && echo "$name :${p%%:*} reachable" || die "$name :${p%%:*} not reachable: ssh in and read /tmp/pi_speaker.log /tmp/pi_device.log"
  done
}

if [ "$HARNESS" = pi ]; then
  case "$MODE" in all | laptop) check_free "$PORT_ROUTER" "$PORT_SPEECH" ;; esac
else
  case "$MODE" in all | laptop) check_free "$PORT_ROUTER" "$PORT_SPEECH" "$PORT_AGENT" "$PORT_APPS" ;; esac
fi
# maybe_calibrate: the stack is up (the Pi speaker is reachable, so the music can be piped to it): recalibrate when
# the calibration is missing, old or short. make talk reads the result, so nothing else needs setting.
maybe_calibrate() {
  case "${CALIBRATE:-auto}" in 0 | no | off) return 0 ;; esac
  if [ "$DRY" = 1 ]; then echo "+ calibration check (CALIBRATE=${CALIBRATE:-auto}), then scripts/calibrate.sh if needed"; return 0; fi
  local why
  if why=$(uv run --offline --group laptop python -W ignore -m doc_chat.calibrate --check --max-age-h "${CALIBRATE_MAX_AGE_H:-12}" 2>/dev/null); then
    echo "$why"
    [ "${CALIBRATE:-auto}" = 1 ] || return 0
  else
    echo "$why"
  fi
  if [ ! -t 0 ]; then echo "not a terminal: calibration skipped (run make calibrate before make talk)"; return 0; fi
  local answer=Y
  read -r -t 20 -p "Recalibrate now? It needs you at the microphone, about 4 minutes. [Y/n] " answer || answer=Y
  case "$answer" in n | N | no) echo "skipped: make calibrate when you are ready"; return 0 ;; esac
  scripts/calibrate.sh ${HEADSET:+--headset}
}
case "$MODE" in
  all)    tune; start_router; start_speech; [ "$HARNESS" = pi ] && start_resolver; start_librespot; start_pi; start_apps; start_agent; maybe_calibrate ;;
  laptop) start_router; start_speech; [ "$HARNESS" = pi ] && start_resolver; start_librespot; start_apps; start_agent; maybe_calibrate ;;
  pi)     start_pi ;;
  agent)  stop_agent; start_agent ;;
  apps)   start_apps ;;
  *) die "usage: up.sh [all|laptop|pi|agent|apps]" ;;
esac
if [ "$HARNESS" = pi ] && [ "$MODE" = all ] && [ "$DRY" != 1 ]; then
  echo "home dashboard: http://$PI_V4:$PORT_DASH (open it in a browser on the laptop; the agent's home commands show there)"
fi
