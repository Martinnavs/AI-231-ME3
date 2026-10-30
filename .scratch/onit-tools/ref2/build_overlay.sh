#!/usr/bin/env bash
# Wave 2: rebuild the scratch overlay (repo src, tests, config, pipeline + edits + skeleton), then lint, type-check and test it.
set -euo pipefail
R=/mnt/School/PhD/AI231/AI-231-ME3-agy
O=/tmp/claude-1000/-mnt-School-PhD-AI231-AI-231-ME3-agy/e3e00ee2-dc78-4726-921d-62420406350d/scratchpad/overlay2
rm -rf "$O"; mkdir -p "$O"
cp -r "$R/src" "$O/src"; cp -r "$R/tests" "$O/tests"; cp -r "$R/config" "$O/config"; cp "$R/pyproject.toml" "$O/"
mkdir -p "$O/finetune/data"; cp -r "$R/finetune/spec" "$O/finetune/spec"; cp "$R/finetune/data/format.py" "$O/finetune/data/"
mkdir -p "$O/pipeline"; cp -r "$R/pipeline/src" "$R/pipeline/tests" "$R/pipeline/config" "$R/pipeline/pyproject.toml" "$O/pipeline/"
find "$O" -name __pycache__ -prune -exec rm -rf {} +
python3 "$R/.scratch/onit-tools/ref2/apply_edits.py" "$R/.scratch/onit-tools/agy_work2/orig" "$O"
cp -r "$R/.scratch/onit-tools/ref2/skeleton/." "$O/"
cd "$R"
ROOTF="$O/src/voice_agent/agent/jobs.py $O/src/voice_agent/mcp_servers/delegate.py $O/tests/agent/test_jobs.py $O/tests/mcp_servers/test_delegate_server.py"
PIPEF="$O/pipeline/src/voice_pipeline/notices.py $O/pipeline/src/voice_pipeline/agent_tools.py $O/pipeline/src/voice_pipeline/agent_server.py $O/pipeline/tests/test_notices.py $O/pipeline/tests/test_agent_server_notices.py $O/pipeline/tests/test_agent_tools.py"
echo "== ruff (root)"; .venv/bin/ruff check --extend-ignore I001 --config "$O/pyproject.toml" $ROOTF; .venv/bin/ruff format --config "$O/pyproject.toml" --check $ROOTF || true
echo "== ruff (pipeline)"; pipeline/.venv/bin/ruff check --extend-ignore I001 --config "$O/pipeline/pyproject.toml" $PIPEF; pipeline/.venv/bin/ruff format --config "$O/pipeline/pyproject.toml" --check $PIPEF || true
echo "== mypy (root)"; MYPYPATH="$O/src" PYTHONPATH="$O/src" .venv/bin/python -m mypy --ignore-missing-imports "$O/src/voice_agent/agent/jobs.py" "$O/src/voice_agent/mcp_servers/delegate.py" 2>&1 | tail -4
echo "== mypy (pipeline)"; (cd "$O/pipeline" && PYTHONPATH="$O/pipeline/src" "$R/pipeline/.venv/bin/python" -m mypy src/voice_pipeline/notices.py src/voice_pipeline/agent_server.py src/voice_pipeline/agent_tools.py 2>&1 | tail -4)
echo "== pytest (root)"; PYTHONPATH="$O/src" .venv/bin/python -m pytest -q -p no:cacheprovider --rootdir="$O" "$O/tests/agent" "$O/tests/mcp_servers/test_delegate_server.py"
echo "== pytest (pipeline)"; (cd "$O/pipeline" && PYTHONPATH="$O/pipeline/src" "$R/pipeline/.venv/bin/python" -m pytest -q -p no:cacheprovider --rootdir="$O/pipeline" tests)
