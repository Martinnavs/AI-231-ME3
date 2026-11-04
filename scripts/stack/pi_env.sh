#!/usr/bin/env bash
# Copy config/pi.env to the Pi's repo (the systemd units and pi-check read it there). Laptop side.
source "$(dirname "$0")/lib.sh"
load_env
cd "$STACK_REPO" || exit 1
command -v rsync >/dev/null || die "rsync is not installed on the laptop"
run rsync -a -e "ssh -i $PI_KEY -o BatchMode=yes -o UserKnownHostsFile=$PI_HOSTS_FILE -o HostKeyAlias=pi-cable" \
  "$PI_ENV_FILE" "$PI_USER@$PI_V4:$PI_REPO/config/pi.env" || die "rsync failed (is rsync installed on the Pi?)"
