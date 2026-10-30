#!/bin/bash
# Held-out check: P0 (committed) against P1 (prompt only), three repetitions, order alternated.
R=/mnt/School/PhD/AI231/AI-231-ME3-agy
T=$R/.scratch/onit-tools/prompt-tuning
FH=$HOME/.cache/onit-dry-home
cd $R/pipeline
run() {  # cond rep
  c=$1; n=$2; extra=()
  [ "$c" != P0 ] && extra+=(--system $T/system_v1.txt)
  env HOME=$FH UV_CACHE_DIR=$HOME/.cache/uv UV_OFFLINE=1 uv run --offline python -m voice_pipeline.dryrun --pi-url '' --run-id ${c}_h${n} --prompts config/eval/agent_prompts_heldout.jsonl "${extra[@]}" > $FH/${c}_h${n}.log 2>&1
  echo "done $c rep $n"
}
for n in 1 2 3; do
  if [ $((n % 2)) = 1 ]; then run P0 $n; run P1 $n; else run P1 $n; run P0 $n; fi
done
echo HELDOUT-DONE
