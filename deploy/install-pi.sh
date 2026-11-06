#!/usr/bin/env bash
# Run ON the Pi: install the speaker, device-tool and (HARNESS=pi) agent services as user systemd units that start at boot.
# With --venvs it first builds .venv-agent and adds the MCP packages to .venv (pip; no uv on the Pi).
# Needs config/pi.env here first (laptop: make pi-detect pi-env). Enables them; starting is a separate, explicit step.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
if [ "${1:-}" = "--venvs" ]; then
  cd "$repo"
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/pip install -r deploy/pi-mcp-requirements.txt
  python3 -m venv .venv-agent
  .venv-agent/bin/pip install -r deploy/pi-agent-requirements.txt
fi
"$repo/scripts/stack/pi_check.sh" || { echo "fix the problems above first" >&2; exit 1; }
mkdir -p ~/.config/systemd/user
for u in pi-speaker pi-device-mcp pi-agent; do
  sed "s|@REPO@|$repo|g" "$here/systemd/$u.service" >~/.config/systemd/user/$u.service
done
systemctl --user daemon-reload
systemctl --user enable pi-speaker.service pi-device-mcp.service pi-agent.service
loginctl enable-linger "$USER"   # run user services at boot without a login
echo "Installed. Start now: systemctl --user start pi-speaker pi-agent  (HARNESS=pi; pi-device-mcp only for HARNESS=laptop) (laptop: make up PI_START=systemd)"
echo "Logs: journalctl --user -u pi-speaker -u pi-agent -f"
