# Stage Event Log (JSONL)

Shared contract for pipeline and benchmark stage telemetry across the voice agent harness (owned by ticket 02, consumed by tickets 11, 12, and 14).

## Overview

The stage event log captures timestamped pipeline lifecycle transitions and stage completions as newline-delimited JSON (`.jsonl`). It powers offline latency analysis, turn breakdown, barge-in response tracking, and benchmarking.

Key operational characteristics:
- **Non-blocking audio path:** `StageLogger.emit()` serializes events and enqueues them to a bounded in-memory queue (`max_queue=10_000` by default). It never blocks the caller.
- **Drop on full queue:** If disk I/O stalls and the queue fills, subsequent events are dropped and counted in the `dropped` counter.
- **Never raises on bad data:** `emit()` raises only `ValueError` for an unknown stage. If the record cannot be serialized (a `data` value that makes `json.dumps(..., default=str)` fail, such as a circular structure or a dict with tuple keys), the event is dropped and counted in `dropped`; nothing is raised into the audio path. Non-string scalar keys (int, float, bool, None) are coerced by JSON and are fine.
- **One writer per file:** the file is opened with exclusive creation (`"x"`), so opening a `StageLogger` on a path that already exists raises `FileExistsError`. Parent directories are created. Use a new path (for example one per `run_id`) for every run.
- **Close is bounded and idempotent:** `close()` (also `__exit__`) marks the logger closed, queues a sentinel, then waits for the writer thread. Both steps have a 2 s timeout, so it cannot hang on a stalled disk. If the queue is full when closing, the oldest queued line is discarded (counted in `dropped`) to make room for the sentinel. If the writer is still stuck after the timeout, `close()` returns anyway and the footer may be missing; the writer is a daemon thread. A second `close()` is a no-op. Calling `emit()` after `close()` does not write anything and increments `dropped`.
- **Crash tolerance:** Readers skip malformed or truncated trailing lines (e.g. from an abrupt process termination).

## Timestamp Semantics

Every stage event record includes two distinct timestamps:
- `t_mono_ns` (`int`): Monotonic timestamp in nanoseconds from `time.monotonic_ns()` captured at the moment `emit()` is invoked.
  > **Note on comparability:** In Linux (CLOCK_MONOTONIC), `t_mono_ns` provides nanosecond resolution and is consistent across threads within a single process. However, `t_mono_ns` is comparable only within one process/boot. It cannot be compared across different host boots or across distinct network nodes.
- `t_wall` (`float`): Wall-clock Unix timestamp in fractional seconds from `time.time()` at emit time. Used to correlate events with external clocks and recording rig stimulus channels.

## Line Formats (Schema Version 1)

Every file consists of three types of newline-delimited JSON records.

### 1. File Header (`log_opened`)

The very first line written to every log file upon initialization:

```json
{"v": 1, "event": "log_opened", "run_id": "bench-20261015-001", "t_mono_ns": 5540011991944184, "t_wall": 1760537028.123456, "host": "raspberrypi"}
```

- `v`: Integer schema version (`1`).
- `event`: Literal `"log_opened"`.
- `run_id`: Unique identifier for the logging run/session.
- `t_mono_ns`: Monotonic timestamp when the logger opened.
- `t_wall`: Wall-clock timestamp when the logger opened.
- `host`: Hostname where the process is running (`socket.gethostname()`).

### 2. Stage Events (`stage`)

Emitted during turn processing:

```json
{"v": 1, "event": "stage", "run_id": "bench-20261015-001", "stage": "stt_final", "turn_id": 3, "t_mono_ns": 5540012100000000, "t_wall": 1760537028.231000, "data": {"text": "hello world"}}
```

- `v`: Integer schema version (`1`).
- `event`: Literal `"stage"`.
- `run_id`: Run ID matching the log header.
- `stage`: Stage identifier string (one of the closed set of `Stage` enum values).
- `turn_id`: Integer. **Definition pending decision H2** (see "`turn_id` definition" below). The logger only stores the value it is given.
- `t_mono_ns`: Monotonic timestamp at emit.
- `t_wall`: Wall-clock timestamp at emit.
- `data`: Arbitrary JSON object containing stage-specific metadata. Non-JSON values are coerced using `default=str`.

### 3. File Footer (`log_closed`)

Written by the writer thread after `StageLogger.close()` releases it and the queued events have been written (see "Close is bounded" above for when it can be missing):

```json
{"event": "log_closed", "dropped": 0}
```

- `event`: Literal `"log_closed"`.
- `dropped`: Total number of events dropped during the run (full queue, unserializable data, emitted after close, or discarded at close).

## `turn_id` definition

**Pending decision H2.** What a `turn_id` counts, and how the benchmark analysis matches stage-log turns to rig trials, is not decided yet. Do not assume `turn_id` equals a trial index: a pipeline that numbers every turn it opens (barge-ins, false interruptions, a second turn after a pause takeover) will not line up with trial numbers. This section will be filled in when H2 is resolved.

## Stage Registry

Stage names belong to a closed set (`voice_agent.telemetry.events.Stage`). An unknown stage name passed to `emit()` raises `ValueError`.

| Stage Name | When It Fires |
|---|---|
| `vad_speech_start` | Voice activity detector detects user speech onset. |
| `vad_speech_stop` | Voice activity detector detects user speech cessation. |
| `endpoint` | Turn judged complete; `data: {"source": "stt" \| "vad_fallback" \| "vad"}`. |
| `stt_interim` | Interim speech-to-text transcript hypothesis received from STT stream. |
| `stt_final` | Final speech-to-text transcript received from STT stream after endpoint. |
| `llm_request` | Outgoing prompt request dispatched to the LLM backend. |
| `llm_first_token` | First response token delta received from the LLM stream. |
| `llm_cancelled` | Speculative or in-flight LLM request abandoned (ticket 12). |
| `tool_call` | LLM emits a tool call invocation request. |
| `delegation_started` | Task delegation to a background agent or subagent initiated. |
| `delegation_finished` | Task delegation completed and returned results. |
| `tts_request` | Text sentence or clause dispatched to the TTS synthesizer. |
| `tts_first_chunk` | First synthesized audio chunk received from the TTS synthesizer. |
| `filler_played` | Audio filler clip played; `data: {"clip": str, "duration_ms": int, "kind": "delegate" \| "stall" \| "notice"}`. |
| `audio_out_first` | First synthesized bot audio frame handed to the output audio transport. |
| `barge_in_detected` | Interruption detected while bot is speaking (speech energy or VAD threshold cleared). |
| `barge_in_mute` | Bot audio playback halted and output audio buffer flushed. |
| `bot_ducked` | Potential barge-in detected; bot audio gain lowered; `data: {"gain_db": float}`. |
| `bot_restored` | Audio blip ended before interruption threshold; bot gain ramped back to 0 dB. |
| `turn_done` | Turn completed; all bot playback finished or aborted. |

## Reader Contract (`read_events`)

The reader function `read_events(path: Path) -> tuple[list[StageEvent], ReadStats]` parses a log file:
- Returns a list of `StageEvent` records in the exact order logged.
- Skips header (`log_opened`) and footer (`log_closed`) records without counting them as malformed.
- Tolerates malformed, corrupted, or truncated trailing lines by skipping them and incrementing `ReadStats.malformed`.
- Raises `UnsupportedVersion` if any record has `v > SCHEMA_VERSION`.
- Raises `FileNotFoundError` if the log file path does not exist.
