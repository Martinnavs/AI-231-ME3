# 22: `lan` backend — audio_engine client for the laptop speech server

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 21 (done); does not need 05 (create the two error types as in the note)
**Status:** ready-for-agent

## Amendment 2026-10-23 (direction change: local speech, laptop mic + Pi loudspeaker, `doc_chat` findings)
Move to the front of the queue. Changes:
- **Lift, don't rewrite:** `src/doc_chat/speech.py` already talks to `lan_speech` (STT stream, finalize, TTS stream, token header). Start from it and re-express it behind the `Transcriber` / `Synthesizer` ABCs. Read it first and say in the log what was lifted.
- TTS output is 44.1 kHz; the Pi speaker is 48 kHz and `RemoteSpeaker` resamples, so `Synthesizer.sample_rate_out` must report the server's rate and not assume the config's.
- Pass-through of entity / hotword config to the server (`config/entities-*.json`) as `lan` options.
- Create `src/audio_engine/_ws.py` with `SpeechBackendError` and `SpeechAuthError` only, unless 05 landed first.
- Cancel semantics: `TTS.cancel()` and the late-chunk filter must be tested against the barge-in sequence (cancel while a clause is mid-synthesis).

## Files
- `src/audio_engine/lan.py` (`LanTranscriber`, `LanSynthesizer`)
- `src/audio_engine/registry.py` (register `lan` lazily, by import path)
- `src/voice_agent/config.py`, `config/agent.toml`, `tests/test_config.py` (additive config only, see Contracts)
- `docs/AUDIO-ENGINE.md` (append a "lan backend" section)
- `tests/audio_engine/test_lan.py`, `tests/audio_engine/test_lan_vs_server.py`

## Action
Implement the `audio_engine` ABCs against the ticket-21 server over WebSocket, reusing the connection across turns (open at startup, ping, reconnect with the same backoff as ticket 05). `Transcript(end_of_turn=True)` is yielded from the server's flag. `TTS.cancel()` sends `cancel` for the live context and filters any late chunk by context id. `transcribe()` and `synthesize()` (batch) are built on the streaming path. Import message shapes from `lan_speech.protocol` only if that does not pull the `laptop` dependency group into the Pi install; if it would, the shapes live in a tiny dependency-free `lan_speech/protocol.py` (it must stay stdlib-only; test it).

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| `LAN_SPEECH_TOKEN` unset and host not loopback | `ConfigError` naming the env var; no network |
| server unreachable at startup | `SpeechBackendError("lan speech server unreachable")`; no host/IP in the message |
| socket drops mid-turn | iterator raises `SpeechBackendError`; next call reconnects with backoff |
| HTTP 401 on upgrade | `SpeechAuthError`, never retried |
| server `error` message | `SpeechBackendError` carrying only `status` and the fixed `reason` |
| `cancel()` with no live context | no-op |
| late binary frame from a cancelled context | dropped |
| default config (`stt_backend="deepgram"`) | behavior unchanged; `lan` is only used when selected |

### C — Contracts
```python
# src/audio_engine/lan.py
class LanTranscriber(Transcriber):
    sample_rate = 16000
    def __init__(self, *, url: str, token: str | None) -> None: ...
class LanSynthesizer(Synthesizer):
    def __init__(self, *, url: str, token: str | None, voice: str | None = None) -> None: ...
# registry: create_transcriber("lan", ...) / create_synthesizer("lan", ...)
```
Config amendment (additive only; every existing field and default unchanged; human-acknowledged when this ticket is dispatched): `AppConfig.lan_speech_url: str = "ws://127.0.0.1:8765"` (top-level scalar) and `Secrets.lan_speech_token: str | None` from env `LAN_SPEECH_TOKEN` (masked in repr like the other secrets).

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `lan_speech.protocol` imports without the `laptop` group | `uv run python -c "import lan_speech.protocol"` in a default install |
| A2 | The ticket-21 fake engines let the real server run in-process in tests | read `src/lan_speech/engines.py` |
| A3 | `SpeechBackendError` from 05 exists | `grep -n SpeechBackendError src/audio_engine/_ws.py`; if absent use the 06 fallback |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_lan_vs_server_roundtrip` — the real ticket-21 server with fake engines in-process; `LanTranscriber` + `LanSynthesizer` through the real classes (not a hand-built dict) | `uv run pytest -q tests/audio_engine/test_lan_vs_server.py` |
| P2 | `test_two_turns_one_connection` (server counts connections == 1) | same |
| P3 | `test_cancel_drops_stale_chunks` | `uv run pytest -q tests/audio_engine/test_lan.py` |
| P4 | `test_auth_error_not_retried`, `test_missing_token_config_error` | same |
| P5 | `test_error_message_has_no_host_or_token` (token and host sentinel never in message or log) | same |
| P6 | `test_registry_lazy` — `import audio_engine` pulls no `lan` module; ticket 04 P7 still green | `uv run pytest -q tests/audio_engine` |

## Unknowns
- None beyond what ticket 21 defines.

## Dependencies/license
`websockets` (already planned in 05).

## Acceptance Criteria
- [ ] Both classes implement the ABCs; registered lazily; `audio_engine` import unchanged
- [ ] P1–P6 green; gate suite green; default config behavior unchanged
- [ ] No host, IP or token in any message or log line

## Non-Goals
- No change to the server (ticket 21) or to the cloud adapters; no discovery/mDNS; no TLS (LAN only, token auth)

## Security-sensitive
no (client side; the token handling is covered by the criteria)

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`, `docs/LAPTOP-SPEECH.md`, `docs/AUDIO-ENGINE.md`

## Execution Log
