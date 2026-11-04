#!/usr/bin/env bash
# Run ON the Pi: install the speaker and device-tool services as user systemd units that start at boot.
# Needs config/pi.env here first (laptop: make pi-detect pi-env). Enables them; starting is a separate, explicit step.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
"$repo/scripts/stack/pi_check.sh" || { echo "fix the problems above first" >&2; exit 1; }
mkdir -p ~/.config/systemd/user
for u in pi-speaker pi-device-mcp; do
  sed "s|@REPO@|$repo|g" "$here/systemd/$u.service" >~/.config/systemd/user/$u.service
done
systemctl --user daemon-reload
systemctl --user enable pi-speaker.service pi-device-mcp.service
loginctl enable-linger "$USER"   # run user services at boot without a login
echo "Installed. Start now: systemctl --user start pi-speaker pi-device-mcp  (laptop: make up PI_START=systemd)"
echo "Logs: journalctl --user -u pi-speaker -u pi-device-mcp -f"
