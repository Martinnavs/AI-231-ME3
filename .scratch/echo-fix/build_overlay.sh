#!/usr/bin/env bash
# Rebuild the scratch overlay from the repo's files, apply edit lists A then B, copy the skeleton in.
set -euo pipefail
R=/mnt/School/PhD/AI231/AI-231-ME3-agy
O=/tmp/claude-1000/-mnt-School-PhD-AI231-AI-231-ME3-agy/e59fc068-3692-4d4a-9b82-d45b21f1e72e/scratchpad/overlay2
for f in src/doc_chat/__main__.py src/doc_chat/speech.py src/doc_chat/playback.py src/doc_chat/bargein.py scripts/pi_speaker.py; do cp "$R/$f" "$O/$f"; done
python3 "$R/.scratch/echo-fix/apply_edits.py" "$R/.scratch/echo-fix/edits_a.py" "$O"
python3 "$R/.scratch/echo-fix/apply_edits.py" "$R/.scratch/echo-fix/edits_b.py" "$O"
rsync -a "$R/.scratch/echo-fix/skeleton/" "$O/"
