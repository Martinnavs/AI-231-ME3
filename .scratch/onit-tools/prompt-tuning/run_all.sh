#!/bin/bash
# Interleaved dry runs of three conditions (P0 committed, P1 prompt only, P2 prompt + tool text), three repetitions.
R=/mnt/School/PhD/AI231/AI-231-ME3-agy
T=$R/.scratch/onit-tools/prompt-tuning
FH=$HOME/.cache/onit-dry-home
cd $R/pipeline
packs() {  # cond rep
  c=$1; n=$2; extra=()
  [ "$c" != P0 ] && extra+=(--system $T/system_v1.txt)
  [ "$c" = P2 ] && extra+=(--tool-text $T/tools_v2.json)
  for pack in "m:config/eval/agent_prompts.jsonl" "d:config/eval/agent_prompts_delegate.jsonl" "r:$FH/recheck.jsonl"; do
    k=${pack%%:*}; f=${pack#*:}
    env HOME=$FH UV_CACHE_DIR=$HOME/.cache/uv UV_OFFLINE=1 uv run --offline python -m voice_pipeline.dryrun --pi-url '' --run-id ${c}_${k}${n} --prompts $f "${extra[@]}" > $FH/${c}_${k}${n}.log 2>&1
  done
  echo "done ${c} rep ${n}"
}
for n in 1 2 3; do
  case $n in 1) order="P0 P1 P2";; 2) order="P1 P2 P0";; 3) order="P2 P0 P1";; esac
  for c in $order; do packs $c $n; done
done
echo TUNING-DONE
