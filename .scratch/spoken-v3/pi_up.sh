#!/usr/bin/env bash
# PI side: the loudspeaker (pi_speaker) and the device tools (pi_device_mcp). Both stop by themselves after 2 h.
# Run ONLY after the owner says "go start".
source "$(dirname "$0")/env.sh"
[ -n "$CABLE_IF" ] || { echo "no cable interface"; exit 1; }
LOCAL_SUM=$(sha256sum ~/.config/lan_speech_token | cut -c1-12)
PI_SUM=$(pi_ssh 'sha256sum ~/.config/lan_speech_token | cut -c1-12') || { echo "cannot reach the Pi over ssh"; exit 1; }
[ "$LOCAL_SUM" = "$PI_SUM" ] && echo "session token: same on both sides" || { echo "TOKEN MISMATCH (make a fresh pair, docs/PI-BRIDGE.md)"; exit 1; }
pi_ssh 'cd ~/Documents/AI-231-ME3 && export LAN_SPEECH_TOKEN=$(cat ~/.config/lan_speech_token) LD_LIBRARY_PATH=$HOME/.local/portaudio/usr/lib/aarch64-linux-gnu
  nohup timeout 7200 .venv/bin/python -u scripts/pi_speaker.py --device CD002 --rate 48000 > /tmp/pi_speaker.log 2>&1 < /dev/null &
  echo $! > /tmp/pi_speaker.pid
  nohup timeout 7200 .venv/bin/python -u scripts/pi_device_mcp.py --port 8790 > /tmp/pi_device.log 2>&1 < /dev/null &
  echo $! > /tmp/pi_device.pid
  sleep 4; tail -n 3 /tmp/pi_speaker.log; tail -n 3 /tmp/pi_device.log'
ss -ltn >/dev/null; for i in 1 2 3 4 5; do (exec 3<>/dev/tcp/$PI_V4/8780) 2>/dev/null && { echo "pi speaker :8780 reachable"; break; }; sleep 1; done
for i in 1 2 3 4 5; do (exec 3<>/dev/tcp/$PI_V4/8790) 2>/dev/null && { echo "pi device tools :8790 reachable"; break; }; sleep 1; done
