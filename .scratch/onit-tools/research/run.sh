#!/usr/bin/env bash
# 2 axes x (agy plan-mode search, codex read-only live search). Results: AGY-<axis>.md, CODEX-<axis>.md; *.done files mark the end.
cd /mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/onit-tools/research || exit 1
REPO=/mnt/School/PhD/AI231/AI-231-ME3-agy
for a in or gem; do
  ( agy --mode plan --model gemini-3.8-flash-high --print-timeout 45m -p "$(cat agy-$a.txt)" > AGY-$a.md 2> agy-$a.err
    echo "exit $?" > agy-$a.done ) &
  ( timeout 3300 codex exec -m gpt-6-luna -c model_reasoning_effort=high -c web_search=live \
      -s read-only --skip-git-repo-check --ephemeral -C "$REPO" \
      -o "$PWD/CODEX-$a.md" "$(cat common.txt q-$a.txt)" > codex-$a.log 2> codex-$a.err
    echo "exit $?" > codex-$a.done ) &
done
wait
