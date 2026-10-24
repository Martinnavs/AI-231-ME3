#!/usr/bin/env bash
# Run agy inside a bubblewrap fence: the whole filesystem read-only except work/ (the copy),
# agy's own state (~/.gemini) and caches (~/.cache, uv); ~/.secrets and ~/.ssh hidden. Usage: fence.sh <brief file> <out prefix>
set -euo pipefail
T=/mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/mcp-set/agy_work
W=/mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/mcp-set/agy_work/work
BRIEF=$(realpath "$1"); OUT=$2
cd "$W"
# Retry only agy's start-up eligibility check: this network's IPv6 path to some Google
# front ends resets connections now and then, and agy does not retry it itself.
for attempt in 1 2 3 4; do
env -i HOME="$HOME" PATH="$PATH" TERM=dumb \
  bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
    --bind "$W" "$W" \
    --bind "$HOME/.gemini" "$HOME/.gemini" --bind "$HOME/.cache" "$HOME/.cache" \
    --tmpfs "$HOME/.secrets" --tmpfs "$HOME/.ssh" \
    --die-with-parent --chdir "$W" \
  agy --sandbox --mode accept-edits --dangerously-skip-permissions \
    --model gemini-3.8-flash-high --print-timeout 60m -p "$(cat "$BRIEF")" \
  > "$T/$OUT.md" 2> "$T/$OUT.err" || true
  if [ -s "$T/$OUT.md" ] || ! grep -q "Eligibility check failed" "$T/$OUT.err"; then break; fi
  echo "attempt $attempt: eligibility check reset, retrying" >&2; sleep 5
done
echo done > "$T/$OUT.done"
