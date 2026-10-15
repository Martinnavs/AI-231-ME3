# 01: Project scaffold, config loader and gate suite

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** None (can start immediately)
**Status:** done

## Files
- `pyproject.toml`, `uv.lock`, `.python-version`
- `.gitignore`, `.pre-commit-config.yaml`, `Makefile`, `.env.example`
- `src/voice_agent/__init__.py`, `src/voice_agent/config.py`
- `src/audio_engine/__init__.py`, `src/bench/__init__.py`
- `config/agent.toml`
- `tests/conftest.py`, `tests/test_config.py`

## Action
Create the Python project every later ticket builds on. `git init` the repo (it is not one yet) and commit. Three importable packages under `src/`: `voice_agent` (harness, MCP servers, agent, pipeline), `audio_engine` (STT/TTS drivers shared by Pipecat and MCP), `bench` (benchmark tooling). Configure ruff, mypy strict, pytest with `live` and `pi` markers that are **skipped unless** `VOICE_AGENT_LIVE=1` / `VOICE_AGENT_PI=1` are set. Write a typed config loader that reads `config/agent.toml` and overlays secrets from the environment (and `.env` if present).

`.gitignore` must include `.env`, `.venv/`, `out/`, `.scratch/vendor/`, `agent_sandbox/`, `*.wav` (with `!tests/fixtures/**/*.wav`).

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| `config/agent.toml` missing | `load_config()` raises `ConfigError` naming the path |
| unknown key in TOML | `ConfigError` naming the key (catch typos early) |
| secret env var unset | field is `None`; nothing fails until a component that needs it starts (that component raises) |
| `.env` absent | silently skipped |
| test marked `live` without `VOICE_AGENT_LIVE=1` | skipped with reason "set VOICE_AGENT_LIVE=1 (paid API)" |
| `repr(config)` / logging the config | secrets render as `'***'` |

### C — Contracts
```python
# src/voice_agent/config.py
class ConfigError(Exception): ...

@dataclass(frozen=True)
class Secrets:
    anthropic_api_key: str | None      # ANTHROPIC_API_KEY
    deepgram_api_key: str | None       # DEEPGRAM_API_KEY
    cartesia_api_key: str | None       # CARTESIA_API_KEY
    def __repr__(self) -> str: ...     # masks every set value as '***'

@dataclass(frozen=True)
class AudioConfig:
    sample_rate_in: int = 16000
    sample_rate_out: int = 16000
    input_device: str | None = None    # ALSA name, e.g. "plughw:CARD=Speakerphone,DEV=0"
    output_device: str | None = None

@dataclass(frozen=True)
class TurnConfig:
    vad_stop_secs: float = 0.2         # VAD speech-stop events (barge-in gate, telemetry); not the end of turn
    endpointing: str = "stt"           # "stt": cloud STT end-of-turn signal decides; "vad": silence only (no model on the Pi but VAD)
    vad_fallback_stop_secs: float = 0.8   # "stt" mode: silence that ends the turn if the STT signal never comes
    vad_turn_stop_secs: float = 0.5    # "vad" mode: silence that ends the turn
    min_barge_in_ms: int = 250
    barge_in_duck_db: float = -15.0    # bot gain while a possible barge-in is pending (ticket 12)
    barge_in_fade_ms: int = 15         # fade-out on a real barge-in; also the restore ramp is 3x this
    speculative_llm: bool = False
    speculative_silence_ms: int = 180

@dataclass(frozen=True)
class AgentConfig:
    fast_model: str = "claude-haiku-4-5"
    slow_model: str = "claude-sonnet-5-5"
    history_turns: int = 6
    stt_backend: str = "deepgram"      # "fake" in tests
    tts_backend: str = "cartesia"
    tts_voice: str | None = None
    fast_tool_prefixes: tuple[str, ...] = ("home__", "media__", "assistant__")   # quick tools the fast agent calls inline
    hidden_tools: tuple[str, ...] = ("media__duck", "assistant__due_events")    # called by the pipeline, never shown to a model

@dataclass(frozen=True)
class PathsConfig:
    sandbox_root: Path                 # default ~/agent_sandbox
    media_root: Path                   # default ~/agent_sandbox/media
    music_root: Path                   # default ~/Music (local music library, ticket 19)
    sites_root: Path                   # default ~/agent_sites (published sandbox websites, ticket 08)
    state_dir: Path                    # default ~/.local/state/voice_agent (assistant timers/alarms/reminders, ticket 20)
    log_dir: Path                      # default ./out/logs

@dataclass(frozen=True)
class AppConfig:
    audio: AudioConfig
    turn: TurnConfig
    agent: AgentConfig
    paths: PathsConfig
    mcp_servers: dict[str, "McpServerSpec"]   # filled from [mcp_servers.<name>] tables
    home_dashboard_url: str = "http://127.0.0.1:8000"
    sites_host: str = "raspberrypi.local"   # how other machines reach the Pi (ticket 08 publish_site URLs)
    sites_port: int = 8080
    location: str = "Manila"                 # default place for weather (ticket 20)
    timezone: str = "Asia/Manila"
    secrets: Secrets = ...

@dataclass(frozen=True)
class McpServerSpec:
    command: str
    args: tuple[str, ...]
    env: dict[str, str]                # values may reference ${VAR}; expanded at spawn time, not here

def load_config(path: Path = Path("config/agent.toml"), env: Mapping[str, str] | None = None) -> AppConfig: ...
```
`config/agent.toml` ships with every section populated with the defaults above and three `[mcp_servers.*]` entries (`speech`, `home`, `code`) pointing at `uv run python -m voice_agent.mcp_servers.<name>` — modules that later tickets create.

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `uv` is on PATH and can create a Python 3.12 venv | first step: `uv --version && uv python find 3.12` |
| A2 | `tomllib` (stdlib, 3.11+) is enough; no TOML dependency | `uv run python -c "import tomllib"` |
| A3 | mypy strict passes on an empty `src/` layout with `mypy_path = "src"` and `packages = ["voice_agent","audio_engine","bench"]` | run `uv run mypy` after scaffolding |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_load_defaults` — shipped `config/agent.toml` loads, values equal the dataclass defaults | `uv run pytest -q tests/test_config.py` |
| P2 | `test_unknown_key_raises` | same |
| P3 | `test_missing_file_raises` | same |
| P4 | `test_secrets_from_env_and_masked` — env mapping fills secrets; `repr()` and `str(config)` contain no secret value | same |
| P5 | `test_live_marker_skipped_by_default` — a dummy `live` test is reported skipped (use `pytester`) | same |
| P6 | Gate suite green on the empty project | `uv run ruff check . && uv run ruff format --check . && uv run mypy` |

## Unknowns
None

## Dependencies/license
Dev group only: `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `pre-commit` (all MIT/Apache-2.0). Runtime: `python-dotenv` (BSD-3).

## Acceptance Criteria
- [ ] `git init` done; first commit contains the scaffold
- [ ] `uv sync` from a clean checkout works
- [ ] All Proof rows green; gate suite green
- [ ] `make check` runs the gate suite; `make test` runs pytest

## Non-Goals
- No runtime dependencies beyond `python-dotenv` (later tickets add their own)
- No CI configuration

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md` (protocol, gates, HITL rules)
- `.scratch/project2-voice-agent/DESIGN.md` (architecture, decisions)

## Execution Log

### 2026-10-15 claude: DONE
- Assumptions verified: A1 uv 0.12.13, python 3.12.3; A2 tomllib in stdlib 3.12; A3 `uv run mypy` clean
- Proof: P1–P5 ✅ (`uv run pytest -q` → 6 passed); P6 ✅
- Gate suite: ruff ✅ format ✅ mypy ✅ pytest ✅
- Deviations: added `test_dotenv_overlay_and_absent`; ruff `.scratch` excluded; PEP 695 generic for `_build`
