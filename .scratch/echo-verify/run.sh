#!/usr/bin/env bash
# one search-only agy fact-check per topic, all in parallel; plan mode, no --dangerously-skip-permissions
cd /mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/echo-verify || exit 1
for t in A-webrtc B-frameworks C-neural D-practical; do
  (
    agy --mode plan --model gemini-3.8-flash-high --print-timeout 45m \
      -p "$(cat header.txt claims-$t.txt)" > AGY-VERIFY-$t.md 2> agy-$t.err
    echo "exit $?" > agy-$t.done
  ) &
done
wait
echo all done
