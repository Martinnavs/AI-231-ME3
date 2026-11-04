#!/usr/bin/env bash
# pi_service.sh speaker|device: run one Pi service in the foreground (used by the systemd units; env from config/pi.env).
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
  *) echo "usage: pi_service.sh speaker|device" >&2; exit 2 ;;
esac
