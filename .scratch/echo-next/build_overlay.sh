#!/usr/bin/env bash
# Rebuild the step-2 overlay from the repo, apply edits_step2.py, add the skeleton, run the gate.
# Usage: build_overlay.sh <overlay dir> [--no-gate]
set -euo pipefail
R=/mnt/School/PhD/AI231/AI-231-ME3-agy
O=$1
mkdir -p "$O"
for f in src/doc_chat/__main__.py src/doc_chat/bargein.py src/doc_chat/aec.py; do cp "$R/$f" "$O/$f"; done
python3 "$R/.scratch/echo-fix/apply_edits.py" "$R/.scratch/echo-next/edits_step2.py" "$O"
rsync -a "$R/.scratch/echo-next/skeleton/" "$O/"
[ "${2:-}" = "--no-gate" ] && exit 0
cd "$O"
FILES=(src/doc_chat/__main__.py src/doc_chat/bargein.py src/doc_chat/aec.py
       tests/doc_chat/test_stop_pause.py tests/doc_chat/test_echo_prefix.py
       tests/doc_chat/test_aec_lead.py tests/doc_chat/test_guard_warmup.py)
uv run --offline --group laptop ruff check "${FILES[@]}"
uv run --offline --group laptop ruff format --check "${FILES[@]}"
uv run --offline --group laptop mypy
uv run --offline --group laptop pytest -q -p no:cacheprovider
