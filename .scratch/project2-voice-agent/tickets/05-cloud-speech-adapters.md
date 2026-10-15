# 05: Cloud speech adapters — Deepgram streaming STT, Cartesia streaming TTS

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 04, and decision **D1** recorded as confirmed in `DESIGN.md` (if D1 still says "proposed", stop and report BLOCKED)
**Status:** ready-for-agent

## Files
- `src/audio_engine/deepgram.py`, `src/audio_engine/cartesia.py`, `src/audio_engine/_ws.py` (shared reconnecting WebSocket helper)
- `src/audio_engine/registry.py` (register the two lazily, by import path)
- `docs/AUDIO-ENGINE.md` (append a "Backends" section)
- `tests/audio_engine/test_deepgram.py`, `tests/audio_engine/test_cartesia.py`, `tests/audio_engine/test_live_vendors.py` (marked `live`)
- `tests/audio_engine/ws_fake_server.py` (local fake WebSocket server fixture)

## Action
Implement the two vendor backends behind the ticket-04 ABCs, talking to the vendors' documented **WebSocket** APIs directly with `websockets` (lean on the Pi, and we control connection lifetime: open at startup, keep alive, reuse across turns — review §3 "open connections at startup"). WebFetch the current vendor docs before writing message formats; record the doc URLs and the API version you coded against in `docs/AUDIO-ENGINE.md`.

- **Deepgram STT:** streaming endpoint with `encoding=linear16`, `sample_rate=16000`, `channels=1`, `interim_results=true`, `punctuate=true`, model from config (default the current general streaming model per the docs). `finalize()` sends the vendor's finalize/flush control message (not close) so the socket survives across turns. Send the vendor's KeepAlive message when no audio has been sent for a while (interval from the docs). **End of turn comes from the cloud** (the professor confirmed no model runs on the Pi except VAD, so no local turn model): enable the vendor's turn-detection signal and yield `Transcript(end_of_turn=True)` when it fires. Read the current docs and pick what they offer for conversational end-of-turn: a turn-taking model with explicit end-of-turn events if one is available, otherwise the endpointing / utterance-end options of the standard streaming model. Record the choice, the parameters and their values in `docs/AUDIO-ENGINE.md`. `transcribe()` (batch) uses the vendor's pre-recorded REST endpoint via `httpx`.
- **Cartesia TTS:** WebSocket generation with raw `pcm_s16le` output at `sample_rate_out`, one context per utterance so `cancel()` cancels that context and drops buffered audio without closing the socket. `synthesize()` collects the stream into an `AudioClip`. `list_voices()` uses the REST voices endpoint.
- Both: API key from `AppConfig.secrets`; missing key → `ConfigError` at construction, naming the env var. Reconnect with backoff (0.2, 0.5, 1, 2 s, cap 5 attempts) when the socket drops between turns; a drop *mid-turn* surfaces as `SpeechBackendError` so the pipeline can apologise rather than hang.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| API key missing | `ConfigError("DEEPGRAM_API_KEY not set")` at construction; no network |
| socket closed by server while idle | next `send`/`stream_synthesize` reconnects transparently (backoff) |
| socket drops mid-utterance | current iterator raises `SpeechBackendError`; next call reconnects |
| 5 reconnect attempts fail | `SpeechBackendError` with the last cause |
| HTTP 401/403 from vendor | `SpeechAuthError` (subclass), never retried |
| vendor end-of-turn event | one `Transcript(end_of_turn=True)` yielded per user turn; duplicate events for the same turn are dropped |
| idle > keepalive interval (STT) | KeepAlive control message sent; connection stays open |
| `cancel()` during TTS stream | vendor context cancelled; iterator ends within one chunk; no stale chunks leak into the next utterance (chunks tagged by context id and filtered) |
| vendor returns an error frame | `SpeechBackendError(message)`; logged without the API key |
| interim result arrives after `finalize()` | still yielded as interim; the turn's final is the one flagged final by the vendor |

### C — Contracts
```python
# src/audio_engine/deepgram.py
class DeepgramTranscriber(Transcriber):
    sample_rate = 16000
    def __init__(self, *, api_key: str | None, model: str | None = None, language: str = "en",
                 url: str = "<from docs>", keepalive_s: float = <from docs>) -> None: ...

# src/audio_engine/cartesia.py
class CartesiaSynthesizer(Synthesizer):
    def __init__(self, *, api_key: str | None, voice: str | None, model: str | None = None,
                 sample_rate: int = 16000, url: str = "<from docs>", api_version: str = "<from docs>") -> None: ...

# src/audio_engine/_ws.py
class SpeechBackendError(Exception): ...
class SpeechAuthError(SpeechBackendError): ...
class ReconnectingSocket:
    def __init__(self, url: str, headers: Mapping[str, str], *, backoff_s: Sequence[float] = (0.2, 0.5, 1.0, 2.0, 2.0)) -> None: ...
    async def connect(self) -> None: ...
    async def send(self, msg: str | bytes) -> None: ...
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...
    async def aclose(self) -> None: ...

# registry: create_transcriber("deepgram", ...) / create_synthesizer("cartesia", ...)
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | Deepgram streaming supports a finalize/flush control message that keeps the socket open, and a KeepAlive message | WebFetch Deepgram streaming docs; quote the message JSON in `docs/AUDIO-ENGINE.md` |
| A2 | Cartesia WebSocket supports per-context cancel and raw `pcm_s16le` at 16 kHz | WebFetch Cartesia TTS WebSocket docs; quote the cancel message |
| A3 | `websockets` (asyncio client) installs on aarch64 / Python 3.13 | `uv pip install --dry-run --python-platform aarch64-manylinux_2_28 --python-version 3.13 websockets httpx` |
| A5 | Deepgram exposes a streaming end-of-turn signal (a turn-taking model's end-of-turn event, or endpointing / utterance-end events) usable for conversational turns | WebFetch the streaming docs; quote the event JSON in `docs/AUDIO-ENGINE.md`; if the turn-taking model needs a different endpoint or model, record it as the D1 STT model |
| A4 | The fake server reproduces the message shapes from A1/A2 | write the fake from the quoted shapes, not from memory |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_deepgram_stream_interim_and_final` — fake WS server scripted with vendor-shaped frames → interims then one final `Transcript` | `uv run pytest -q tests/audio_engine/test_deepgram.py` |
| P2 | `test_deepgram_finalize_keeps_socket` — two turns over one connection (fake server counts connections == 1) | same |
| P3 | `test_deepgram_keepalive_sent_when_idle` — `keepalive_s=0.05`, idle 0.2 s → ≥2 KeepAlive frames | same |
| P4 | `test_deepgram_reconnect_after_idle_close`, `test_auth_error_not_retried` | same |
| P8 | `test_deepgram_end_of_turn` — fake server sends the end-of-turn event shape from A5 (twice for one turn) → exactly one `Transcript(end_of_turn=True)` | `uv run pytest -q tests/audio_engine/test_deepgram.py` |
| P5 | `test_cartesia_stream_and_cancel` — cancel after first chunk; fake server receives the cancel frame; next utterance gets no chunk of the cancelled context | `uv run pytest -q tests/audio_engine/test_cartesia.py` |
| P6 | `test_missing_key_raises_config_error` (both) | both files |
| P7 | `test_live_roundtrip` (`live`, human-run only): Cartesia synthesizes "turn on the lights", Deepgram transcribes it back, WER ≤ 0.2; prints TTFB of each and the delay from clip end to the end-of-turn event | `VOICE_AGENT_LIVE=1 uv run pytest -q -m live tests/audio_engine/test_live_vendors.py` — **do not run; leave for the human** |

## Unknowns
- Exact current endpoint URLs, model names and API-version header values: take them from the docs at build time (A1/A2).

## Dependencies/license
`websockets` (BSD-3), `httpx` (BSD-3).

## Added requirement (security audit of 06, R4-7)
`SpeechBackendError` messages carry only the HTTP/WS status and a short reason, never URLs, headers, response bodies or keys; add a test asserting a fake key never appears in the message or in the speech MCP tool output.

## Acceptance Criteria
- [ ] Both backends implement the ticket-04 ABCs; registered lazily; `audio_engine` import still pulls no vendor modules (ticket 04 P7 still green)
- [ ] Proof P1–P6 green; P7 written and skipped by default; gate suite green
- [ ] No API key in any log line or exception message (grep the test output)

## Non-Goals
- No other vendors; no local STT/TTS models
- No Pipecat wiring

## Security-sensitive
no (secrets handling is covered by the "never log keys" criterion)

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/AUDIO-ENGINE.md` (from ticket 04)

## Execution Log
