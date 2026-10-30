#!/usr/bin/env bash
# Rebuild the scratch overlay (edited repo files + skeleton), then run ruff, mypy and the new tests against it.
set -euo pipefail
R=/mnt/School/PhD/AI231/AI-231-ME3-agy
O=/tmp/claude-1000/-mnt-School-PhD-AI231-AI-231-ME3-agy/e3e00ee2-dc78-4726-921d-62420406350d/scratchpad/overlay
rm -rf "$O"; mkdir -p "$O"
cp -r "$R/src" "$O/src"; find "$O/src" -name __pycache__ -prune -exec rm -rf {} +
python3 "$R/.scratch/onit-tools/ref/apply_edits.py" "$R" "$O"
cp "$R/pyproject.toml" "$O/"; cp -r "$R/tests" "$O/tests"; find "$O/tests" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$R/.scratch/onit-tools/ref/skeleton/." "$O/"
cd "$R"
FILES=$( (cd "$R/.scratch/onit-tools/ref/skeleton" && find . -name '*.py'; echo ./src/voice_agent/mcp_servers/code.py; echo ./src/voice_agent/sandbox/runner.py) | sed "s#^\.#$O#")
.venv/bin/ruff check --extend-ignore I001 --config "$O/pyproject.toml" $FILES
.venv/bin/ruff format --config "$O/pyproject.toml" --check $FILES || true
MYPYPATH="$O/src" PYTHONPATH="$O/src" .venv/bin/python -m mypy --ignore-missing-imports "$O/src/voice_agent/mcp_servers/shell_policy.py" "$O/src/voice_agent/agent/subagent.py" "$O/src/voice_agent/mcp_servers/code.py" "$O/src/voice_agent/sandbox/runner.py" 2>&1 | tail -5
PYTHONPATH="$O/src" .venv/bin/python -m pytest -q -p no:cacheprovider --rootdir="$O" \
  "$O/tests/mcp_servers/test_shell_policy.py" "$O/tests/mcp_servers/test_shell_tools.py" \
  "$O/tests/mcp_servers/test_code_server.py" "$O/tests/sandbox" "$O/tests/agent/test_subagent.py"
