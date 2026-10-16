# 21: Laptop speech server — CPU feasibility benchmark, then a LAN STT/TTS WebSocket server

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 04 (done). Does NOT need D1. Phase 2 needs the Phase 1 GO decision recorded in the Execution Log.
**Status:** parked (Phase 1 NO-GO 2026-10-16; see Execution Log)

## Why
The professor allows any ASR/TTS (paid or open) as long as TTS is interruptible, but "no model on the Pi except VAD" still holds. A speech server on the user's laptop (same LAN as the Pi, later maybe a wired link) is a dev harness now and a possible deployed backend later. The cloud adapters (ticket 05) stay the graded path; this ticket never replaces them.

## Files
- Phase 1: `scripts/laptop_speech_bench.py`, `docs/LAPTOP-SPEECH.md` (Phase 1 results section)
- Phase 2: `src/lan_speech/__init__.py`, `src/lan_speech/protocol.py`, `src/lan_speech/server.py`, `src/lan_speech/engines.py` (sherpa-onnx STT, Kokoro TTS, plus fake engines), `src/lan_speech/__main__.py`
- `scripts/fetch_laptop_models.py` (downloads model files into `models/`, verifies SHA-256, prints licenses)
- `pyproject.toml` (new dependency group `laptop` only; the default group and the Pi install must not change), `.gitignore` (`models/`)
- `docs/LAPTOP-SPEECH.md` (protocol section = the shared contract for ticket 22)
- `tests/lan_speech/test_protocol.py`, `tests/lan_speech/test_server.py`, `tests/lan_speech/test_live_engines.py` (marked `live`)

## Action
**Phase 1 — benchmark (gate).** Standalone script, no server. On the laptop CPU only (record `lscpu` model, cores, power mode):
- STT: sherpa-onnx streaming Zipformer (English) fed the `bench/turns/v1.json` utterances as pre-rendered 16 kHz WAVs (record a few yourself too, if a mic is available) in 100 ms chunks, paced at real time. Report time from last audio sample to final text (p50/p95), real-time factor, and word error rate against the known text.
- TTS: Kokoro (kokoro-onnx) for 8-word and 25-word sentences. Report time to first audio chunk (p50/p95) and real-time factor.
- Both running at the same time (a TTS reply while STT hears the next utterance) and the same numbers again.
- Write the table to `docs/LAPTOP-SPEECH.md`. **GO** only if: STT final-after-end p50 ≤ 300 ms and WER ≤ 0.15 on the turn set; TTS first-audio p50 ≤ 400 ms and RTF ≤ 0.6; both-at-once numbers each within 1.5x of alone. Otherwise record NO-GO with the numbers, set Status to `parked`, and stop. Do not tune beyond one pass (threads, int8 model variants).

**Phase 2 — server (only after GO).** One process, `python -m lan_speech --host H --port P`:
- WebSocket `/stt` and `/tts`, plus `GET /voices` and `GET /healthz`. Message shapes are defined once in `protocol.py` as dataclasses with `to_json()/from_json()`, and documented in `docs/LAPTOP-SPEECH.md` (see Contracts).
- STT end of turn comes from sherpa's endpoint rules (tunable via flags) and is sent as `end_of_turn=true`; there is no local turn-taking model beyond that.
- TTS: one context per `speak`; `cancel` stops synthesis and drops queued audio within one chunk; nothing from a cancelled context is ever sent afterwards.
- Auth: a shared token is required by default (`Authorization: Bearer <token>` on the WebSocket upgrade and on `GET /voices`); the token comes from env `LAN_SPEECH_TOKEN`, never from argv or logs. Missing token at start = refuse to start unless `--insecure-loopback` and the bind address is 127.0.0.1. Bind address defaults to 127.0.0.1; any non-loopback bind logs a warning naming the interface.
- Limits: max frame 64 KiB, max text 2000 chars per `speak`, max 4 concurrent connections, idle timeout 60 s with WebSocket ping.
- Engines are behind a small `Engine` protocol so tests run with fake engines and no model files.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| Phase 1 numbers miss the GO thresholds | record NO-GO, status `parked`, stop; no Phase 2 files created |
| model file missing / checksum mismatch | `fetch_laptop_models.py` and server startup exit 2 naming the file; no partial load |
| client sends no `start` before audio | `error` message `status=400 reason="start required"`, socket closed |
| client sends audio after `finalize` without a new `start` | treated as a new turn on the same stream |
| two `speak` contexts at once | rejected with `error status=409 reason="context busy"`; first continues |
| `cancel` for an unknown/finished context | ignored silently |
| frame larger than 64 KiB, text over 2000 chars, 5th connection | `error` with `status` 413 / 413 / 503, then close for the 5th |
| wrong or missing token | HTTP 401 on upgrade; no body details; a log line without the token |
| non-loopback bind | startup warning; token required (no `--insecure-loopback` override) |
| engine raises | `error status=500 reason="engine error"` only; stack trace to the server log, never to the client |
| client disconnects mid-synthesis | synthesis stops; no leaked task (test asserts the task set is empty) |

### C — Contracts
```text
STT  ws://H:P/stt
  C->S text   {"type":"start","sample_rate":16000}          # PCM is S16LE mono
  C->S binary raw PCM chunks
  C->S text   {"type":"finalize"}                           # flush; socket stays open
  S->C text   {"type":"transcript","text":str,"is_final":bool,"end_of_turn":bool,"t_audio_end_s":float|null}
  S->C text   {"type":"error","status":int,"reason":str}    # reason is short and fixed-vocabulary

TTS  ws://H:P/tts
  S->C text   {"type":"ready","sample_rate":int}            # sent once on connect
  C->S text   {"type":"speak","context":int,"text":str,"voice":str|null}
  C->S text   {"type":"cancel","context":int}
  S->C binary 4-byte big-endian context id + raw PCM S16LE
  S->C text   {"type":"done","context":int}
  S->C text   {"type":"error","status":int,"reason":str}

GET /voices -> [{"id":str,"name":str,"language":str|null}]
GET /healthz -> {"ok":true,"stt":str,"tts":str}             # engine names only
```
`protocol.py` is the single source for these shapes; ticket 22 imports it (producer first, Operating Principle 11).

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `sherpa-onnx` and `kokoro-onnx` install on the laptop's Python via `uv sync --group laptop` and run on CPU only | Phase 1 start: `uv run --group laptop python -c "import sherpa_onnx, kokoro_onnx"` |
| A2 | Both libraries and the chosen model files have licenses allowing this use (class project, no redistribution of models) | `fetch_laptop_models.py` prints each license; record in `docs/LAPTOP-SPEECH.md`; if a license is unclear, stop and report |
| A3 | sherpa-onnx streaming recognizers expose endpoint detection usable as end-of-turn | read the installed package's docs/examples; quote the config in `docs/LAPTOP-SPEECH.md` |
| A4 | Kokoro-onnx can yield audio incrementally per sentence/chunk (needed for first-audio latency and cancel) | Phase 1; if only whole-utterance synthesis exists, split text at clause boundaries and say so in the results |
| A5 | The default (Pi) dependency group stays free of onnx/torch-sized packages | `uv sync` without `--group laptop` then `uv pip list | grep -i sherpa` finds nothing |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | Phase 1 table present in `docs/LAPTOP-SPEECH.md` with hardware, thresholds and a GO/NO-GO line | `grep -n "GO" docs/LAPTOP-SPEECH.md` |
| P2 | `test_protocol_roundtrip` — every message type serializes and parses; unknown `type` rejected | `uv run pytest -q tests/lan_speech/test_protocol.py` |
| P3 | `test_stt_stream_and_finalize` — fake engine; interim, final, one `end_of_turn`, two turns on one socket | `uv run pytest -q tests/lan_speech/test_server.py` |
| P4 | `test_tts_cancel_no_stale_audio` — cancel after the first chunk; no chunk of that context arrives afterwards; next context clean | same |
| P5 | `test_auth_and_limits` — 401 without token, token never in logs (caplog), 413 and 503 paths | same |
| P6 | `test_disconnect_leaves_no_tasks` | same |
| P7 | `test_live_engines_roundtrip` (`live`, human-run): Kokoro synthesizes "turn on the lights", sherpa transcribes it back, WER ≤ 0.2 | `VOICE_AGENT_LIVE=1 uv run --group laptop pytest -q -m live tests/lan_speech/test_live_engines.py` — leave for the human |
| P8 | default install stays light (A5) and the Pi gate suite passes without the `laptop` group | `uv sync && make check && uv run pytest -q` |

## Unknowns
- Exact model names/URLs and checksums: choose at build time, record in `fetch_laptop_models.py` and the doc.
- Whether the laptop's CPU meets the GO thresholds: that is the point of Phase 1.

## Dependencies/license
`sherpa-onnx` (Apache-2.0), `kokoro-onnx` (MIT; model weights Apache-2.0, verify per A2), `websockets` (BSD-3, already planned). `laptop` dependency group only.

## Acceptance Criteria
- [ ] Phase 1 results and a GO/NO-GO decision are recorded in `docs/LAPTOP-SPEECH.md` and the Execution Log
- [ ] On GO: P2–P8 green, gate suite green, Pi install unchanged
- [ ] On NO-GO: ticket parked with the numbers; no server code committed
- [ ] No token, key or model path outside `models/` appears in logs or exceptions

## Non-Goals
- No change to `audio_engine` or to the cloud adapters; no `lan` client here (ticket 22)
- No diarization, no voice cloning, no GPU path
- Not run on the Pi

## Security-sensitive
yes (a network listener with auth): security-auditor pass after Phase 2.

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`, `docs/AUDIO-ENGINE.md`, `bench/turns/v1.json`

## Execution Log

### 2026-10-16 developer: DONE (Phase 1) — NO-GO, ticket parked
- Decision: **NO-GO**. TTS first-audio p50 was 1023 ms (fp32) and 871 ms (int8 tuning pass) against a ≤ 400 ms limit; under concurrency TTS first-audio was 1.72x / 1.74x (limit 1.5x). STT passed: final-after-end p50 57 / 48 ms, WER 0.118, RTF 0.13 / 0.11. Full tables are in `docs/LAPTOP-SPEECH.md`. HP EliteBook 840 G8, i5-1145G7 4C/8T, **on battery**, governor powersave, EPP balance_performance.
- Cause: Kokoro has a fixed cost of about 400 ms per call, plus RTF about 0.45 on the first clause, so clause splitting cannot reach 400 ms. Re-running on AC is a human call, not extra tuning.
- Assumptions verified: A1 `uv run --group laptop python -c "import sherpa_onnx, kokoro_onnx"` → ok, CPU providers only (after pinning sherpa-onnx-core, see Deviations); A2 HF/GitHub license APIs → Zipformer Apache-2.0, Kokoro-82M Apache-2.0, sherpa-onnx Apache-2.0, kokoro-onnx MIT; A3 read `sherpa_onnx/online_recognizer.py` → `enable_endpoint_detection` + rule1/2/3 + `is_endpoint` (quoted in doc); A4 read `kokoro_onnx/__init__.py` → `create_stream` batches at 510 phonemes only, so the bench splits clauses itself; A5 `uv sync && uv pip list | grep -iE "sherpa|kokoro|onnx"` → nothing.
- Proof: P1 ✅ (`grep -n "GO" docs/LAPTOP-SPEECH.md`). P2–P7 n/a (Phase 2 not built, per NO-GO edge). P8 ✅ default install light; gate suite green.
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (241 passed, 7 skipped, 2 xfailed)
- Files: `scripts/laptop_speech_bench.py`, `scripts/fetch_laptop_models.py`, `docs/LAPTOP-SPEECH.md`, `pyproject.toml` (`laptop` group), `uv.lock`, `.gitignore` (`models/`)
- Deviations:
  - Added `sherpa-onnx-core==1.13.8` to the `laptop` group. uv resolves sherpa-onnx from sdist metadata, which drops this required wheel and leaves `libonnxruntime.so` missing. Same project and license as sherpa-onnx.
  - Kokoro fp32 is fetched from HF `onnx-community/Kokoro-82M-v1.0-ONNX` because the GitHub release stalled at about 10 KB/s. kokoro-onnx loads it unchanged.
  - The STT inputs are Kokoro-rendered rather than recorded (no cloud TTS allowed, no mic run), so WER is optimistic. `--mic-dir` exists for human recordings.
- Commit: see `git log` (`21: laptop speech Phase 1 benchmark — NO-GO, parked`)
- Notes for reviewer: no Phase 2 files (`src/lan_speech`, `tests/lan_speech`) were created. The models (≈ 850 MB) stay in git-ignored `models/`. Tickets 22/23 depend on this ticket and are now effectively blocked.

### 2026-10-16 developer: Supertonic 3 follow-up evaluation (human-directed, after the NO-GO)
- Tried Supertonic 3 (OpenRAIL-M weights, MIT SDK) as the Kokoro replacement, in an isolated env with no project dependency change. Best setting is 2 threads, 4 steps, on AC in the performance profile: first-audio "Sure," 271 ms, 8 words 412 ms, 25 words 1083 ms. 8 steps gives 8 words in 717 ms. More threads is slower; the power profile helps about 15% only on AC. Full table in `docs/LAPTOP-SPEECH.md` § Phase 1b.
- Human listening verdict: the 4-step F1 voice is "decent, though still sounding robotic".
- Not yet measured: Supertonic running alongside STT (the 1.5x check); WER round-trip.
- Added `scripts/power_mode.sh` (performance/balanced/power-saver via powerprofilesctl).
- Status stays `parked` until the human amends the ticket: the TTS choice, the clause-chunking first-audio rule, and acceptance of the OpenRAIL-M license.
