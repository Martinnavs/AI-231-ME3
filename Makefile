.DEFAULT_GOAL := help
.PHONY: help check test pipeline-sync pipeline-check pipeline-test agent-dryrun agent-server check-all test-all \
        pi-detect up up-laptop up-pi restart-agent down status talk pi-check pi-clips pi-env pi-firewall pi-tune pi-netem \
        spotify-login calibrate

help: ## List documented targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:[^#]*## /\t/' | sort | awk -F'\t' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# Pi + laptop stack: settings. Override on the command line, e.g. make up ROUTER_MODEL=router-350m-kd-s0 DRY=1
ROUTER_MODEL ?= router-350m-kd-web4-s0-unverified
ROUTER_THREADS ?= 3
GGUF_TOOLS ?= $(HOME)/work/gguf-tools
ENTITIES ?= config/entities.json
KITTEN_VOICE ?= Jasper
KITTEN_SITE ?= $(HOME)/work/tts-bakeoff/venv/lib/python3.12/site-packages
SPEECH_ARGS ?=
AGENT_ARGS ?=
TALK_ARGS ?=
NOTE ?=
PI_START ?= ssh
PI_RUN_SECONDS ?= 7200
PI_ENV_MAX_AGE_MIN ?= 120
DRY ?= 0
export ROUTER_MODEL ROUTER_THREADS GGUF_TOOLS ENTITIES KITTEN_VOICE KITTEN_SITE SPEECH_ARGS AGENT_ARGS TALK_ARGS NOTE PI_START PI_RUN_SECONDS PI_ENV_MAX_AGE_MIN DRY
# pi-detect also takes PI_USER, PI_KEY, PI_REPO, PI_AUDIO_DEVICE, PI_AUDIO_PATTERN on the command line (exported by make).

pi-detect: ## (laptop) find the Pi on the cable, write config/pi.env; read-only on the Pi
	scripts/stack/pi_detect.sh

up: ## (laptop) router, speech server, Pi speaker and device tools, agent server; detects the Pi first if needed
	scripts/stack/up.sh all

up-laptop: ## (laptop) router, speech server, agent server only
	scripts/stack/up.sh laptop

up-pi: ## (laptop) start only the Pi speaker and device tools over ssh
	scripts/stack/up.sh pi

restart-agent: ## (laptop) restart only the agent server (after the Pi tools came up)
	scripts/stack/up.sh agent

down: ## (laptop) stop what make up started: laptop by PID file, then the Pi services
	scripts/stack/down.sh all

status: ## (laptop) read-only: cable, Pi ping, ports. PI_LOGS=1 adds the Pi logs and temperature
	scripts/stack/status.sh $(if $(PI_LOGS),pi)

talk: ## (laptop) the live loop in the foreground; needs the microphone
	scripts/stack/talk.sh

calibrate: ## (laptop) room calibration + the six baseline phrases; HEADSET=1 uses the headset microphone (else the laptop one); needs you at the mic
	scripts/calibrate.sh $(if $(HEADSET),--headset)

doctor-tools: ## (laptop) what the delegate sandbox can do with git, GitHub, Hugging Face (free; --offline skips the network)
	.venv/bin/python scripts/delegate_doctor.py

pi-check: ## (run ON the Pi) config/pi.env, venv, token mode, audio device, the six clips
	scripts/stack/pi_check.sh

pi-clips: ## (laptop) regenerate tests/fixtures/pi_echo and rsync it to the Pi
	scripts/stack/pi_clips.sh

pi-env: ## (laptop) copy config/pi.env to the Pi's repo
	scripts/stack/pi_env.sh

pi-firewall: ## (laptop, sudo) open the router, speech and music-resolver ports to the Pi in the cable's firewalld zone
	scripts/stack/pi_firewall.sh

pi-tune: ## (laptop, sudo on the Pi) latency: Pi CPU governor performance + Wi-Fi power save off (both persistent), laptop power profile performance
	scripts/stack/pi_tune.sh

pi-netem: ## (laptop, sudo on the Pi) simulate a poor link on the Pi's Wi-Fi: MODE=cellular|bad|offline|off|status (auto-removed after 30 min)
	scripts/stack/pi_netem.sh $(or $(MODE),status)

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
spotify-login: ## (laptop) one-time Spotify OAuth login via browser
	uv run --offline python scripts/spotify_login.py

