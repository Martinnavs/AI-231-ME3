.PHONY: check test pipeline-sync pipeline-check pipeline-test smart-home-dryrun check-all test-all

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

# Dry run of the smart_home tool against real LLMs (reads API keys from ~/.secrets; costs cents).
# MODELS=groq-8b,gemini-lite,groq-70b  LIMIT=<n prompts>  LIVE=<ME2 dashboard URL, optional>
MODELS ?= groq-8b,gemini-lite,groq-70b
smart-home-dryrun:
	cd pipeline && uv run python -m voice_pipeline.dryrun --models $(MODELS) \
		$(if $(LIMIT),--limit $(LIMIT)) $(if $(LIVE),--live $(LIVE))

check-all: check pipeline-check

test-all: test pipeline-test
