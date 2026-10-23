# 11: Live pipeline — Pipecat assembly, VAD + cloud end-of-turn, stage telemetry, entry point

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 02 (done), 22 (replaces 05); 10 only for the real agent: use `ScriptedLLMClient` first
**Status:** ready-for-agent

## Amendment 2026-10-23 (direction change: local speech, laptop mic + Pi loudspeaker, `doc_chat` findings)
Pipecat stays (human decision 2026-10-23). What `doc_chat` learned changes how it is assembled:
- **Audio topology:** input is the laptop mic. Output is a custom Pipecat output transport that plays through `RemoteSpeaker` (Pi loudspeaker, `docs/PI-REMOTE-SPEAKER.md`) or the local speaker. Do not use Pipecat's default interruption path for barge-in. Add a `BargeInProcessor` that wraps `doc_chat.bargein.BargeInGuard` (Silero 7-of-10 AND level above the learned echo bar, 3-of-8) and pushes Pipecat's interruption frame; the transport must feed it the played level (`RemotePlayer.level_recent`). Move `bargein.py` into `voice_agent` or import it; do not rewrite it.
- **Endpointing:** the end-of-turn rule is a pause hold (0.64 s measured) plus STT finalize (about 0.35 s). Keep `endpointing = "stt"` as the default shape but make the hold a config value; ticket 27 tunes it on spontaneous speech.
- **Turn-level text rules** move over from `doc_chat/__main__.py` as processors: stop phrases (`_is_stop_phrase`), pivots (`_strip_stop_prefix`), "wait for playback to finish before listening" (`close()` waits reply length).
- **Cue-beep mode and `--trace-guard`** are kept as options of the entry point (measurement of echo and latency).
- TTS starts on the first clause; ticket 25 refines the chunking.
- Pipecat version pin and thread checks (A1-A5, P7, P8) are unchanged.

## Files
- `src/voice_agent/pipeline/__init__.py`, `src/voice_agent/pipeline/processors.py`, `src/voice_agent/pipeline/file_transport.py`, `src/voice_agent/pipeline/app.py`
- `src/voice_agent/__main__.py`
- `tests/pipeline/test_processors.py`, `tests/pipeline/test_main_file_mode.py`
- `tests/fixtures/pipeline/two_turns.wav` (generate in the test setup from `FakeSynthesizer` + silence, or commit a small file ≤ 300 KB)

## Action
Assemble the live loop with **pruned Pipecat**: pipeline, local audio transport, Silero VAD, interruption handling — plus our own processors that adapt `audio_engine` and `AgentSession` (review "proposed design"). No Pipecat STT/LLM/TTS services; ours wrap the engine so the MCP server and the live loop share one code path (decision 4).

Pipeline order: `transport.input()` → `EngineSTTProcessor` → `AgentProcessor` → `EngineTTSProcessor` → `transport.output()`, with VAD configured on the input transport params, and a `StageTelemetryObserver` (or processor taps) emitting `docs/EVENT-LOG.md` stages.

- **Startup:** open STT stream, TTS `warm_up()`, LLM client, MCP manager **before** the first turn (review §3).
- **Endpointing — no model on the Pi except Silero VAD** (professor, 2026-10-15; this replaces the review's smart-turn idea). Do not install or enable Pipecat's smart-turn analyzer. Silero VAD runs with `stop_secs = TurnConfig.vad_stop_secs` (0.2) for speech start/stop events only. The turn ends:
  - `endpointing = "stt"` (default): at the STT stream's first `Transcript(end_of_turn=True)` after user speech, or after `vad_fallback_stop_secs` (0.8) of VAD silence if that never comes — whichever is first. A user who resumes speaking before either fires keeps the turn (pauses like "my birthday is… January first" must not end it).
  - `endpointing = "vad"`: after `vad_turn_stop_secs` (0.5) of VAD silence.
  - `ENDPOINT` is emitted at that moment with `data.source` (`stt`, `vad_fallback` or `vad`); then `finalize()`.
- **STT:** stream user audio into the open `TranscriptionStream`; on endpoint call `finalize()`; the final transcript triggers the agent.
- **Agent → TTS:** forward text deltas; TTS starts on the first sentence/clause boundary (`.`, `?`, `!`, `,` after ≥ 4 words) rather than waiting for the whole reply.
- **One ONNX thread per model** (ME2 lesson, review "Pi setup notes"; research §7.2): Silero VAD must run with `intra_op_num_threads = 1` and `inter_op_num_threads = 1`, so it never competes with the event loop for cores. Use the pinned Pipecat version's own setting if it has one; otherwise pass `SessionOptions` through whatever hook it offers. If it offers none, stop and report BLOCKED (do not monkeypatch Pipecat internals).
- **Interruptions:** enable Pipecat interruptions with its default behavior here; ticket 12 adds the min-speech rule, TTS cancel/flush and LLM cancel.
- **Delegation follow-ups:** after each turn, speak any `pending_announcements()`.
- **Entry point:** `python -m voice_agent` (mic/speaker) and a headless file mode `--input-wav IN --output-wav OUT [--fake]` that runs the same pipeline with `file_transport.py` (feeds input frames paced at real time or `--fast`, writes output frames on the same timeline, silence-filled). File mode is what tests and the benchmark dry-run use; it must go through `main()`, not a test-only path.

**Pipecat's API changes between releases.** Pin the version you install (`pipecat-ai[silero]==<x.y.z>`), record it in `DESIGN.md` decisions, and verify every class name you use (frames, transport params, VAD analyzer, interruption frames, observers) with `uv run python -c "import pipecat...; print(...)"` or the version's docs before coding against it.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| no input device / ALSA error at start | exit 2 with a message naming `audio.input_device` and suggesting `arecord -l` |
| STT end-of-turn never arrives (vendor doesn't send it, socket hiccup) | turn ends at `vad_fallback_stop_secs` of silence; `ENDPOINT.data.source = "vad_fallback"`; log a running count |
| user pauses 0.6 s mid-sentence, STT sends no end-of-turn | turn continues; one `ENDPOINT` after the user's real end |
| STT end-of-turn arrives while VAD still hears speech | ignored until VAD reports speech stop (the user is still talking) |
| STT final is empty / only noise words | turn dropped; no LLM call; `TURN_DONE` with `data.reason="empty"` |
| `SpeechBackendError` from STT or TTS mid-turn | speak a pre-made apology clip (or log if TTS itself failed); pipeline keeps running; STT stream reopened |
| agent yields `Error` | its `spoken_text` is synthesized and played |
| user speaks while a delegation runs | normal turn; the announcement waits until that turn ends |
| `--fake` | STT/TTS/LLM replaced by `FakeTranscriber`, `FakeSynthesizer`, `ScriptedLLMClient` (script from `--fake-script FILE` or a default 2-turn script); MCP servers not spawned unless `--with-mcp` |
| SIGINT | graceful: stop transport, close STT/TTS/MCP/logger; exit 0 within 2 s |
| file input ends | wait for the bot to finish the last reply (max 20 s), then exit 0 |

### C — Contracts
```python
# src/voice_agent/pipeline/app.py
@dataclass(frozen=True)
class PipelineDeps:
    transcriber: Transcriber
    synthesizer: Synthesizer
    session: AgentSession
    stage_logger: StageLogger

def build_deps(config: AppConfig, *, fake: bool, fake_script: Path | None, with_mcp: bool) -> AsyncContextManager[PipelineDeps]: ...
def build_pipeline_task(config: AppConfig, deps: PipelineDeps, transport: Any) -> Any: ...   # returns a pipecat PipelineTask
async def run(config: AppConfig, *, input_wav: Path | None, output_wav: Path | None,
              fake: bool, fake_script: Path | None, with_mcp: bool, realtime: bool) -> int: ...

# src/voice_agent/__main__.py
def main(argv: Sequence[str] | None = None) -> int: ...
#   --config PATH  --input-wav PATH --output-wav PATH  --fake  --fake-script PATH  --with-mcp  --fast  --run-id ID
#   stage log written to <paths.log_dir>/<run-id>.stages.jsonl ; run-id default = UTC timestamp

# src/voice_agent/pipeline/processors.py
class EngineSTTProcessor(FrameProcessor): ...
class AgentProcessor(FrameProcessor): ...
class EngineTTSProcessor(FrameProcessor): ...
def clause_boundary(buffer: str) -> int | None: ...   # end index of the first speakable clause (rule above), else None; public, reused by ticket 18
class StageTelemetry: ...          # observer or tap; emits VAD_SPEECH_START/STOP, ENDPOINT, STT_INTERIM/FINAL, TTS_REQUEST, TTS_FIRST_CHUNK, AUDIO_OUT_FIRST, TURN_DONE
                                   # (LLM_* come from AgentSession)

# src/voice_agent/pipeline/file_transport.py
class WavFileTransport: ...        # input(): paced InputAudioRawFrame stream from a WAV; output(): writes bot audio to a WAV on the input's timeline
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | Pipecat installs on Python 3.12 (dev) and 3.13/aarch64 (Pi) with the `silero` extra; local audio transport needs PortAudio (`pyaudio`) which on the Pi is a **system package** | `uv pip install --dry-run --python-platform aarch64-manylinux_2_28 --python-version 3.13 "pipecat-ai[silero,local]"`; record PortAudio as a Pi prerequisite for ticket 16 — do not install system packages |
| A2 | Pipecat lets custom transports/processors push `InputAudioRawFrame`s and receive `OutputAudioRawFrame`/`TTSAudioRawFrame`s (names per pinned version) | read the pinned version's frames module |
| A3 | Silero VAD in Pipecat runs on 16 kHz input with configurable `stop_secs` | read `VADParams` in the pinned version |
| A5 | Pipecat's Silero analyzer either defaults to one ONNX thread or accepts `SessionOptions`/a thread count | read the pinned version's source; record the file and line in the log |
| A4 | Pipecat's input transport does not enable a turn analyzer unless one is passed | read the pinned version's transport params; P8 asserts none is active |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_stt_processor_finalize_on_endpoint` — synthetic frames: speech start, audio, stop → `finalize()` called once, final transcript frame emitted | `uv run pytest -q tests/pipeline/test_processors.py` |
| P2 | `test_tts_starts_on_first_clause` — `clause_boundary` table ("Sure, turning off" → None until 4 words; "Okay." → 5) and agent text deltas "Sure, turning off" … → TTS request issued before the final delta arrives | same |
| P3 | `test_stage_order_one_turn` — via `main(["--input-wav", fx, "--output-wav", out, "--fake", "--fast"])`: stage log for turn 1 has `vad_speech_start < vad_speech_stop ≤ endpoint < stt_final < llm_request < llm_first_token < tts_request < tts_first_chunk ≤ audio_out_first < turn_done` | `uv run pytest -q tests/pipeline/test_main_file_mode.py` |
| P4 | `test_output_wav_has_bot_audio_after_each_turn` — output WAV energy rises after each user turn's end (fixture has 2 turns) and the onset is ≥ VAD stop_secs after the true end (asserted numeric bound) | same |
| P5 | `test_empty_transcript_no_llm_call` | same |
| P7 | `test_onnx_sessions_single_thread` — build the VAD analyzer exactly as `build_pipeline_task` does; its ONNX `InferenceSession.get_session_options()` reports intra = inter = 1 | `uv run pytest -q tests/pipeline/test_processors.py` |
| P8 | `test_only_vad_model_loaded` — after `build_pipeline_task`, the only ONNX/torch model objects alive are Silero's; no turn analyzer configured | same |
| P9 | `test_endpointing_modes` — via `main(... --fake ...)` on a fixture with a 600 ms mid-sentence pause: `stt` mode (FakeTranscriber `eot_silence_ms=700`) → one `endpoint` after the real end, `source="stt"`; `eot_silence_ms=None` → `source="vad_fallback"` 0.8 ± 0.05 s after the real end; `vad` mode → `source="vad"` 0.5 ± 0.05 s after it (and the pause itself, 0.6 s, does end the turn there — documented as the reason `stt` is the default) | `uv run pytest -q tests/pipeline/test_main_file_mode.py` |
| P6 | `test_speech_backend_error_speaks_apology` (fake STT raises once) | same |

## Unknowns
- Exact Pipecat class names in the pinned version (A2–A5) — verify, don't guess.

## Dependencies/license
`pipecat-ai[silero]` (BSD-2); `pipecat-ai[local]` extra for the mic transport (pulls `pyaudio`, MIT; needs system PortAudio on the Pi — documented in ticket 16, not installed here). No smart-turn extra (no model on the Pi except VAD).

## Acceptance Criteria
- [ ] `python -m voice_agent --fake --input-wav … --output-wav …` runs end to end on the dev host
- [ ] Pipecat version pinned and recorded in `DESIGN.md`
- [ ] All Proof rows green; gate suite green

## Non-Goals
- Min-speech barge-in, TTS cancel/flush, speculative LLM start, fillers (ticket 12)
- Running on real audio hardware (human checkpoint)

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/EVENT-LOG.md`, `docs/AUDIO-ENGINE.md`

## Execution Log
