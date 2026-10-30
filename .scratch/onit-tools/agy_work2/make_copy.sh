#!/usr/bin/env bash
# Usage: make_copy.sh <dest>. Copy of the repo's working tree (no .git, no .scratch) with its own offline environments.
set -euo pipefail
R=/mnt/School/PhD/AI231/AI-231-ME3-agy
D=$1
rm -rf "$D"; mkdir -p "$D"
rsync -a --exclude .git --exclude .scratch --exclude .venv --exclude pipeline/.venv --exclude __pycache__ \
  --exclude .mypy_cache --exclude .ruff_cache --exclude .pytest_cache --exclude finetune/runs --exclude models "$R/" "$D/"
cp -a "$R/.venv" "$D/.venv"; cp -a "$R/pipeline/.venv" "$D/pipeline/.venv"
cd "$D"
uv sync --offline --group laptop > /dev/null 2>&1
(cd pipeline && uv sync --offline > /dev/null 2>&1)
mkdir -p .scratch/onit-tools
cp -r "$R/.scratch/onit-tools/tickets" "$R/.scratch/onit-tools/developer-role.md" "$R/.scratch/onit-tools/WAVE-PLAN2.md" .scratch/onit-tools/
echo "copy ready: $D"
