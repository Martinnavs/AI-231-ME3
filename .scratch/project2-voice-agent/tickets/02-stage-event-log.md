# 02: Stage event log (JSONL) — schema, writer, reader

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 01
**Status:** ready-for-agent

## Files
- `src/voice_agent/telemetry/__init__.py`, `src/voice_agent/telemetry/events.py`
- `docs/EVENT-LOG.md` (shared contract; this ticket owns it)
- `tests/telemetry/test_events.py`

## Action
Build the harness's timestamped stage log: one JSON object per line, consumed later by the benchmark analysis (ticket 14). Pattern comes from ME2's JSONL event log: read `.scratch/vendor/AI-222-Machine-Exercises/ME2/docs/STREAMING-CONTRACT.md` §5 and `ME2/src/app/forward.py` (stdlib-only consumer, ignores malformed lines). Writing must never block the audio loop: the writer is a bounded in-memory queue drained by a background thread to a file (and optionally stdout). Write `docs/EVENT-LOG.md` documenting the schema and every stage name below — it is the shared contract for tickets 11, 12 and 14.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| queue full (disk stalled) | drop the event, increment `dropped` counter; never block the caller |
| `close()` called | flushes remaining events, writes a final `{"event":"log_closed","dropped":N}` line |
| unknown stage name passed to `emit` | `ValueError` (stage names are a closed set) |
| `data` contains non-JSON values | coerced with `default=str`; never raises in the hot path |
| reader meets a malformed or truncated last line | skipped, counted in `ReadStats.malformed` |
| reader meets a newer `v` | `UnsupportedVersion` raised |
| two loggers write the same path | not supported; the second `open` raises `FileExistsError` (path includes a run id) |

### C — Contracts
```python
# src/voice_agent/telemetry/events.py
SCHEMA_VERSION = 1

class Stage(StrEnum):
    VAD_SPEECH_START = "vad_speech_start"
    VAD_SPEECH_STOP = "vad_speech_stop"
    ENDPOINT = "endpoint"                    # turn judged complete; data: {"source": "stt" | "vad_fallback" | "vad"}
    STT_INTERIM = "stt_interim"
    STT_FINAL = "stt_final"
    LLM_REQUEST = "llm_request"
    LLM_FIRST_TOKEN = "llm_first_token"
    LLM_CANCELLED = "llm_cancelled"          # speculative request abandoned (ticket 12)
    TOOL_CALL = "tool_call"
    DELEGATION_STARTED = "delegation_started"
    DELEGATION_FINISHED = "delegation_finished"
    TTS_REQUEST = "tts_request"
    TTS_FIRST_CHUNK = "tts_first_chunk"
    FILLER_PLAYED = "filler_played"           # data: {"clip": str, "duration_ms": int, "kind": "delegate"|"stall"|"notice"}
    AUDIO_OUT_FIRST = "audio_out_first"       # first bot frame handed to the output transport
    BARGE_IN_DETECTED = "barge_in_detected"
    BARGE_IN_MUTE = "barge_in_mute"           # bot output actually stopped/flushed
    BOT_DUCKED = "bot_ducked"                 # possible barge-in: bot gain lowered (ticket 12); data: {"gain_db": float}
    BOT_RESTORED = "bot_restored"             # blip ended before the gate fired; gain ramped back to 0 dB
    TURN_DONE = "turn_done"

@dataclass(frozen=True)
class StageEvent:
    stage: Stage
    turn_id: int
    t_mono_ns: int          # time.monotonic_ns() at emit
    t_wall: float           # time.time() at emit
    data: Mapping[str, Any]

class StageLogger:
    def __init__(self, path: Path, *, run_id: str, echo_stdout: bool = False, max_queue: int = 10_000) -> None: ...
    def emit(self, stage: Stage, turn_id: int, **data: Any) -> None: ...   # non-blocking
    @property
    def dropped(self) -> int: ...
    def close(self) -> None: ...
    def __enter__(self) -> "StageLogger": ...
    def __exit__(self, *exc: object) -> None: ...

class NullStageLogger(StageLogger): ...      # emit() is a no-op; for tests and disabled telemetry

@dataclass(frozen=True)
class ReadStats:
    events: int
    malformed: int

class UnsupportedVersion(Exception): ...

def read_events(path: Path) -> tuple[list[StageEvent], ReadStats]: ...
```
Line format (exact keys): `{"v":1,"event":"stage","run_id":"...","stage":"stt_final","turn_id":3,"t_mono_ns":123,"t_wall":1760000000.123,"data":{...}}`. The first line of every file is `{"v":1,"event":"log_opened","run_id":"...","t_mono_ns":...,"t_wall":...,"host":"<hostname>"}`.

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `json.dumps` + a bounded `queue.Queue` with `put_nowait` keeps `emit()` under 50 µs on the dev host | P4 measures it |
| A2 | `time.monotonic_ns()` is consistent across threads in one process (Linux CLOCK_MONOTONIC) | documented Python behavior; note in `docs/EVENT-LOG.md` that `t_mono_ns` is comparable only within one process/boot |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_roundtrip` — emit every `Stage`, close, `read_events` returns them in order with equal fields | `uv run pytest -q tests/telemetry/test_events.py` |
| P2 | `test_queue_full_drops_not_blocks` — `max_queue=2`, writer thread paused via monkeypatched file, 100 emits return immediately, `dropped == 98`, closing line reports it | same |
| P3 | `test_reader_skips_truncated_last_line` | same |
| P4 | `test_emit_is_cheap` — median of 10 000 `emit` calls < 50 µs (asserted threshold) | same |
| P5 | `test_unknown_stage_rejected`, `test_newer_version_rejected` | same |

## Unknowns
None

## Dependencies/license
None (stdlib only).

## Acceptance Criteria
- [ ] Contracts implemented exactly; `docs/EVENT-LOG.md` lists every stage with one line on when it fires
- [ ] All Proof rows green; gate suite green

## Non-Goals
- No log rotation, no network shipping of logs
- Not wiring into Pipecat (ticket 11)

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- Producer of `docs/EVENT-LOG.md`, consumed by tickets 11, 12, 14

## Execution Log
