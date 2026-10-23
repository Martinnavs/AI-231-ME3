.PHONY: check test pipeline-sync pipeline-check pipeline-test agent-dryrun agent-server check-all test-all

# Root project: speech side (lan_speech, doc_chat, audio_engine, bench, voice_agent).
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest -q

# pipeline/: the Pipecat side, a separate uv project with its own lock and venv, because
# pipecat-ai pins onnxruntime~=1.24 and soundfile~=0.13 (see pipeline/pyproject.toml).
pipeline-sync:
	cd pipeline && uv sync

pipeline-check:
	cd pipeline && uv run ruff check . && uv run ruff format --check . && uv run mypy

pipeline-test:
	cd pipeline && uv run pytest -q

# The agent (native Gemini, smart_home + the docs MCP server); reads API keys from ~/.secrets.
# Stress dry run over pipeline/config/eval/agent_prompts.jsonl (costs cents). LIMIT=<n prompts>
agent-dryrun:
	cd pipeline && uv run python -m voice_pipeline.dryrun $(if $(LIMIT),--limit $(LIMIT))

# Agent server for `doc_chat --llm agent` (ws://127.0.0.1:8781). LIVE=<ME2 dashboard URL, optional>
agent-server:
	cd pipeline && uv run python -m voice_pipeline.agent_server $(if $(LIVE),--live $(LIVE))

check-all: check pipeline-check

test-all: test pipeline-test
