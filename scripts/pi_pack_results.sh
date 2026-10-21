#!/usr/bin/env bash
# Copy the small summaries of the Pi echo runs (run.json + run.md) into docs/pi-results/ so they can be
# committed. The recordings (*.wav) stay in out/pi/ (git-ignored, large); copy those with scp if needed.
# Usage: scripts/pi_pack_results.sh [out/pi]
set -euo pipefail
src="${1:-out/pi}"
dst="docs/pi-results"
mkdir -p "$dst"
n=0
for d in "$src"/*/; do
    [[ -f "$d/run.json" ]] || continue
    name="$(basename "$d")"
    mkdir -p "$dst/$name"
    cp "$d/run.json" "$d/run.md" "$dst/$name/"
    [[ -f "$d/trials.json" ]] && cp "$d/trials.json" "$dst/$name/"
    n=$((n + 1))
done
echo "packed $n run(s) into $dst/ (recordings stay in $src/)"
