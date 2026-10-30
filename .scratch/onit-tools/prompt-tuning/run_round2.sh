#!/bin/bash
# Round 2: P1 (committed prompt) against P3 (prompt + arithmetic line + new tool descriptions), settled harness, three repetitions.
# usage: run_round2.sh COND   (P1 or P3); both are started together.
R=/mnt/School/PhD/AI231/AI-231-ME3-agy
T=$R/.scratch/onit-tools/prompt-tuning
FH=$HOME/.cache/onit-dry-home
c=$1
declare -A FILE=([m]=config/eval/agent_prompts.jsonl [d]=config/eval/agent_prompts_delegate.jsonl [r]=$T/recheck.jsonl [g]=config/eval/agent_prompts_heldout2.jsonl)
if [ "$c" = P3 ]; then extra=(--system $T/system_v3.txt --tool-text $T/tools_v3.json); else extra=(--system $T/system_v1.txt); fi
cd $R/pipeline
for n in 1 2 3; do
  for k in g m r d; do
    env HOME=$FH UV_CACHE_DIR=$HOME/.cache/uv UV_OFFLINE=1 uv run --offline python -m voice_pipeline.dryrun --pi-url '' --run-id T${c}_${k}${n} --prompts ${FILE[$k]} "${extra[@]}" > $FH/T${c}_${k}${n}.log 2>&1
  done
  echo "done $c rep $n"
done
echo "ROUND2-DONE $c"
