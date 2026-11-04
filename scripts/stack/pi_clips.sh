#!/usr/bin/env bash
# Regenerate tests/fixtures/pi_echo (deterministic) and rsync it to the Pi. Laptop side.
source "$(dirname "$0")/lib.sh"
load_env
cd "$STACK_REPO" || exit 1
command -v rsync >/dev/null || die "rsync is not installed on the laptop"
run uv run --group laptop python scripts/make_pi_clips.py || exit 1
run rsync -a --delete -e "ssh -i $PI_KEY -o BatchMode=yes -o UserKnownHostsFile=$PI_HOSTS_FILE -o HostKeyAlias=pi-cable" \
  tests/fixtures/pi_echo/ "$PI_USER@$PI_V4:$PI_REPO/tests/fixtures/pi_echo/" || die "rsync failed (is rsync installed on the Pi?)"
