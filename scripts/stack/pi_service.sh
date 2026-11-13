#!/usr/bin/env bash
# pi_service.sh speaker|device|agent: run one Pi service in the foreground (used by the systemd units; env from config/pi.env).
# The token is read from ~/.config/lan_speech_token here, never stored in the env file or the unit.
here=$(cd "$(dirname "$0")/../.." && pwd)
cd "$here" || exit 1
[ -n "${PI_AUDIO_DEVICE:-}" ] || { set -a; . config/pi.env; set +a; }
LAN_SPEECH_TOKEN=$(cat "$HOME/.config/lan_speech_token") || exit 1
export LAN_SPEECH_TOKEN
[ -n "${PI_LD_LIBRARY_PATH:-}" ] && export LD_LIBRARY_PATH=$PI_LD_LIBRARY_PATH
case "${1:-}" in
  speaker) exec .venv/bin/python -u scripts/pi_speaker.py --device "$PI_AUDIO_DEVICE" --rate "${PI_SPEAKER_RATE:-48000}" --port "${PORT_PI_SPEAKER:-8780}" ;;
  device)  exec .venv/bin/python -u scripts/pi_device_mcp.py --port "${PORT_PI_DEVICE:-8790}" ;;
  agent)
    # the harness: apps server in the background (cable address only), agent server in the foreground;
    # the MCP children run in .venv (MCP_PYTHON), the harness itself in .venv-agent
    : "${PI_V4:?config/pi.env has no PI_V4}" "${LAPTOP_IP:?config/pi.env has no LAPTOP_IP}"
    export MCP_PYTHON=$here/.venv-mcp/bin/python PYTHONPATH=$here/pipeline/src:$here/src
    export ROUTER_URL=http://$LAPTOP_IP:${PORT_ROUTER:-8089} MUSIC_RESOLVER_URL=http://$LAPTOP_IP:${PORT_RESOLVER:-8796}
    export APPS_PUBLIC_HOST=$PI_V4 PORT_APPS=${PORT_APPS:-8795}
    export SPEECH_LAN_URL=ws://$LAPTOP_IP:${PORT_SPEECH:-8770}
    mkdir -p "$HOME/agent_sandbox/workspace/apps"
    # at boot the link-local cable address comes up after network-online: wait for it (up to 2 min)
    for _ in $(seq 1 120); do ip -o addr show | grep -q " $PI_V4/" && break; sleep 1; done
    .venv-mcp/bin/python -u -m voice_agent.apps_server --host "$PI_V4" --port "$PORT_APPS" &
    trap 'kill $! 2>/dev/null' EXIT
    .venv-agent/bin/python -u -m voice_pipeline.agent_server --host "$PI_V4" --port "${PORT_AGENT:-8781}" \
      --pi-url local --apps-host "$PI_V4" --ipv4 --hedge 0 ;;
  *) echo "usage: pi_service.sh speaker|device|agent" >&2; exit 2 ;;
esac
