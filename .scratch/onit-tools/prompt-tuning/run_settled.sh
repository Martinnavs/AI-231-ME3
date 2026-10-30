#!/bin/bash
# Re-run with the settled harness. One process per condition, all started together, three repetitions each.
# usage: run_settled.sh COND "packs"   (packs: letters from m d r h)
R=/mnt/School/PhD/AI231/AI-231-ME3-agy
T=$R/.scratch/onit-tools/prompt-tuning
FH=$HOME/.cache/onit-dry-home
c=$1; packs=$2
declare -A FILE=([m]=config/eval/agent_prompts.jsonl [d]=config/eval/agent_prompts_delegate.jsonl [r]=$FH/recheck.jsonl [h]=config/eval/agent_prompts_heldout.jsonl)
extra=()
[ "$c" != P0 ] && extra+=(--system $T/system_v1.txt)
[ "$c" = P2 ] && extra+=(--tool-text $T/tools_v2.json)
cd $R/pipeline
for n in 1 2 3; do
  for k in $(echo $packs | sed 's/./& /g'); do
    env HOME=$FH UV_CACHE_DIR=$HOME/.cache/uv UV_OFFLINE=1 uv run --offline python -m voice_pipeline.dryrun --pi-url '' --run-id S${c}_${k}${n} --prompts ${FILE[$k]} "${extra[@]}" > $FH/S${c}_${k}${n}.log 2>&1
  done
  echo "done $c rep $n"
done
echo "SETTLED-DONE $c"
