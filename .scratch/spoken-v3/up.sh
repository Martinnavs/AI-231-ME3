#!/usr/bin/env bash
# LAPTOP side: router (llama-server), speech server (Parakeet + Kitten), agent server. Pi side: pi_up.sh.
source "$(dirname "$0")/env.sh"; cd "$REPO" || exit 1
[ -n "$LAPTOP_IP" ] || { echo "no 169.254.x.x cable address: is the Pi cable up?"; exit 1; }
export LAN_SPEECH_TOKEN=$(cat ~/.config/lan_speech_token)
for p in 8089 8770 8771 8781; do ss -ltn | grep -q ":$p " && { echo "port $p is already in use: run down.sh first"; exit 1; }; done
echo "router model: $ROUTER_MODEL   cable address: $LAPTOP_IP"
LD_LIBRARY_PATH=$G/llama.cpp/build/bin nohup $G/llama.cpp/build/bin/llama-server --jinja -c 8192 -np 1 -fa on -t 3 -tb 3 \
  -m $G/models/$ROUTER_MODEL/gguf/router-Q4_K_M.gguf --port 8089 > "$D/logs/router.log" 2>&1 &
echo $! > "$D/pids/router"
for i in $(seq 60); do curl -sf http://127.0.0.1:8089/health >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:8089/health >/dev/null && echo "router: up" || { echo "router FAILED, see $D/logs/router.log"; exit 1; }
KITTEN_SITE=$HOME/work/tts-bakeoff/venv/lib/python3.12/site-packages nohup uv run --offline --group laptop python -W ignore -m lan_speech \
  --host "$LAPTOP_IP" --port 8770 --stt-model parakeet --entities config/entities.json --tts-engine kitten --kitten-voice Jasper \
  > "$D/logs/speech.log" 2>&1 &
echo $! > "$D/pids/speech"
for i in $(seq 120); do ss -ltn | grep -q ":8770 " && break; sleep 1; done
ss -ltn | grep -q ":8770 " && echo "speech server: up" || { echo "speech server FAILED, see $D/logs/speech.log"; exit 1; }
if [ "${AB:-}" = 1 ]; then   # a second speech server, early decode at the first 200 ms pause (the early-decode A/B)
  KITTEN_SITE=$HOME/work/tts-bakeoff/venv/lib/python3.12/site-packages nohup uv run --offline --group laptop python -W ignore -m lan_speech \
    --host "$LAPTOP_IP" --port 8771 --stt-model parakeet --entities config/entities.json --tts-engine kitten --kitten-voice Jasper \
    --early-decode-ms 200 > "$D/logs/speech-ed.log" 2>&1 &
  echo $! > "$D/pids/speech-ed"
  for i in $(seq 120); do ss -ltn | grep -q ":8771 " && break; sleep 1; done
  ss -ltn | grep -q ":8771 " && echo "speech server (early decode 200 ms): up" || { echo "second speech server FAILED, see $D/logs/speech-ed.log"; exit 1; }
fi
cd pipeline && nohup uv run python -m voice_pipeline.agent_server --ipv4 --hedge -1 --router-url http://127.0.0.1:8089 \
  > "$D/logs/agent.log" 2>&1 &
echo $! > "$D/pids/agent"
for i in $(seq 120); do grep -q "agent server on" "$D/logs/agent.log" && break; sleep 1; done
grep "agent server on" "$D/logs/agent.log" && echo "agent server: up (the Pi tools need pi_up.sh)" || { echo "agent server FAILED, see $D/logs/agent.log"; exit 1; }
