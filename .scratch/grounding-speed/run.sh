#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
( timeout 3300 codex exec -m gpt-6-luna -c model_reasoning_effort=xhigh -c web_search=live -s read-only \
    --skip-git-repo-check --ephemeral -C "$PWD" -o CODEX-grounding.md "$(cat brief.txt)" > codex.log 2> codex.err
  echo "exit $?" > codex.done ) &
wait
