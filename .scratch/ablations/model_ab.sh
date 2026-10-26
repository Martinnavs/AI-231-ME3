#!/usr/bin/env bash
# control = our current model, candidate = gemini-3.5-flash-lite; A B A B so provider conditions average out
cd /mnt/School/PhD/AI231/AI-231-ME3-agy/pipeline || exit 1
for round in 1 2; do
  for m in gemini-3.1-flash-lite gemini-3.5-flash-lite; do
    uv run python -m voice_pipeline.dryrun --model $m --run-id "model-${m}-r${round}" --pi-url '' > "../.scratch/ablations/model-${m}-r${round}.log" 2>&1
    echo "done $m r$round $(date +%H:%M)"
  done
done
