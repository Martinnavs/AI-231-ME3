#!/usr/bin/env bash
# search-only agy research runs, in parallel; plan mode, no --dangerously-skip-permissions
cd /mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/latency-research || exit 1
for t in A-tools B-preemptive C-gemini; do
  ( agy --mode plan --model gemini-3.8-flash-high --print-timeout 45m \
      -p "$(cat header.txt q-$t.txt)" > AGY-$t.md 2> agy-$t.err
    echo "exit $?" > agy-$t.done ) &
done
wait
echo all done
