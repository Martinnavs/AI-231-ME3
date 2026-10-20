# Laptop speech server

A possible LAN STT/TTS backend running on the user's laptop (ticket 21). The cloud adapters remain the graded path; nothing here replaces them. Phase 1 is a CPU feasibility gate, and the server (Phase 2) is only built after a GO.

## Phase 1 results

**Decision: NO-GO.** TTS first-audio latency misses the gate by about 2x, even after the one allowed tuning pass. STT passes easily. The ticket is `parked`, and no Phase 2 server code exists.

### Hardware and setup

| | |
|---|---|
| Machine | HP EliteBook 840 G8 |
| CPU | Intel Core i5-1145G7 @ 2.60 GHz, 4 cores / 8 threads |
| Power | **on battery** (AC offline), governor `powersave`, EPP `balance_performance`, no ACPI platform profile exposed |
| OS / runtimes | Linux 7.2.2 (Arch), Python 3.12, sherpa-onnx 1.13.8, onnxruntime 1.31.0 (CPU provider only), kokoro-onnx 0.6.1 |
| STT model | sherpa-onnx streaming Zipformer `en-2023-06-26` (chunk-16, left-128), greedy search, 2 threads |
| TTS model | Kokoro-82M v1.0 (kokoro-onnx), voice `af_heart`, onnxruntime intra-op 4 threads |
| Date | 2026-10-16 |

### Method

- **STT:** each of the 40 `turn` utterances in `bench/turns/v1.json` is fed as 16 kHz PCM in 100 ms chunks, paced at real time, and decoded as chunks arrive. After the last sample, the script appends 0.66 s of zero tail padding (sherpa's usual flush) and calls `input_finished()`. *Final-after-end* is the wall time from the last real sample being fed to the final text being available, which is the `finalize` path. RTF is decode compute time divided by audio duration. WER is corpus-level, computed after lowercasing and stripping punctuation.
- **TTS:** three 8-word and three 25-word sentences, 5 runs each. Text is split at clause punctuation (A4, see below), and *first-audio* is the time until the first clause's audio returns. RTF is total synthesis time divided by audio duration.
- **Both at once:** the STT pass is repeated while a second thread synthesizes 25-word replies back to back.
- **Gate:** STT final p50 ≤ 300 ms, WER ≤ 0.15. TTS first-audio p50 ≤ 400 ms, RTF ≤ 0.6. Under concurrency, each of those four latency/RTF numbers must stay within 1.5x of its alone value.

### Results — pass 1 (fp32 models)

| Metric | Alone | Both at once | Ratio | Threshold |
|---|---|---|---|---|
| STT final-after-end p50 (ms) | 57 | 96 | 1.69x ❌ | ≤ 300 alone ✅, ≤ 1.5x |
| STT final-after-end p95 (ms) | 77 | 127 | 1.65x | — |
| STT real-time factor | 0.132 | 0.176 | 1.34x | ≤ 1.5x |
| STT WER | 0.118 | 0.118 | 1.00x | ≤ 0.15 ✅ |
| TTS first-audio p50 (ms) | **1023** | 1759 | 1.72x ❌ | **≤ 400 ❌**, ≤ 1.5x |
| TTS first-audio p95 (ms) | 1892 | 2192 | 1.16x | — |
| TTS real-time factor | 0.483 | 0.557 | 1.15x | ≤ 0.6 ✅ |

TTS first-audio p50 by sentence length: 8-word 1033 ms, 25-word 1583 ms.

### Results — pass 2, the one tuning pass (int8 STT + int8 TTS, same threads)

| Metric | Alone | Both at once | Ratio | Threshold |
|---|---|---|---|---|
| STT final-after-end p50 (ms) | 48 | 70 | 1.47x | ≤ 300 alone ✅, ≤ 1.5x ✅ |
| STT final-after-end p95 (ms) | 61 | 94 | 1.56x | — |
| STT real-time factor | 0.107 | 0.133 | 1.25x | ≤ 1.5x ✅ |
| STT WER | 0.118 | 0.118 | 1.00x | ≤ 0.15 ✅ |
| TTS first-audio p50 (ms) | **871** | 1514 | 1.74x ❌ | **≤ 400 ❌**, ≤ 1.5x |
| TTS first-audio p95 (ms) | 1582 | 1935 | 1.22x | — |
| TTS real-time factor | 0.407 | 0.463 | 1.14x | ≤ 0.6 ✅ |

TTS first-audio p50 by sentence length: 8-word 811 ms, 25-word 1351 ms.

**NO-GO.** The failing checks are TTS first-audio p50 (alone) and TTS first-audio under concurrency. Raw numbers are in `out/laptop_bench/results-{fp32,int8}.json` (git-ignored); reproduce with `uv run --group laptop python scripts/laptop_speech_bench.py [--stt-int8 --tts-int8]`.

### Why TTS misses

Each Kokoro call has a fixed cost of about 400 ms even for a one-word clause ("Sure," takes 467 ms fp32 / 384 ms int8). On top of that, it runs at RTF about 0.4–0.5, so the first clause of an 8-word sentence with no comma (about 2 s of audio) takes about 0.8–1.0 s. Clause splitting cannot get under the 400 ms floor. Running on AC power with a performance profile would likely help somewhat, but a 2x gap is unlikely to close that way. Re-running on AC would be a change of conditions, so it is left to a human decision rather than counted as more tuning.

### Caveats

- **The STT inputs are synthetic.** No paid/cloud TTS was allowed and no local alternative existed, so the 40 utterances were rendered with Kokoro fp32, rotating six voices (`af_heart, am_michael, bf_emma, bm_george, af_bella, am_adam`), and resampled to 16 kHz. Clean TTS speech is easier than real mic speech, so the 0.118 WER is optimistic. No mic recordings were made in this run; `--mic-dir DIR` accepts `name.wav` + `name.txt` pairs for a human to add.
- The WER includes `turn38`, a Spanish sentence an English-only model cannot transcribe. Other errors include LibriSpeech-style splits (`GOLD FISH`, `PHOTO SYNTHESIS`, `D IN A`).
- The concurrent STT ratio is measured on small absolute numbers (57 → 96 ms on fp32). That exceeds 1.5x but stays far below the 300 ms limit.

## Assumptions checked

| # | Result |
|---|---|
| A1 | `uv run --group laptop python -c "import sherpa_onnx, kokoro_onnx"` works and onnxruntime reports only CPU providers. uv resolved `sherpa-onnx` 1.13.8 from sdist metadata and dropped its required `sherpa-onnx-core` wheel, so `libonnxruntime.so` was missing at import. `sherpa-onnx-core==1.13.8` is therefore pinned explicitly in the `laptop` group. |
| A2 | All licenses allow this use. Model weights: Zipformer `en-2023-06-26` is Apache-2.0 (HF model card) and Kokoro-82M is Apache-2.0 (hexgrad). Libraries: sherpa-onnx is Apache-2.0 and kokoro-onnx is MIT. Model files are never redistributed; `models/` is git-ignored. |
| A3 | Yes, endpoint detection exists. `OnlineRecognizer.from_transducer(enable_endpoint_detection=False, rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=1.2, rule3_min_utterance_length=20.0, ...)` (defaults shown), polled via `recognizer.is_endpoint(stream)`. It went unused in Phase 1 because the server never got built. |
| A4 | Only partly. `Kokoro.create_stream()` yields one chunk per ≤510-phoneme batch, so a normal sentence is a single batch. The benchmark splits text at clause punctuation itself. |
| A5 | Yes, the default install stays light. After a plain `uv sync`, `uv pip list \| grep -i -E "sherpa\|kokoro\|onnx"` finds nothing. |

## Model files

`uv run python scripts/fetch_laptop_models.py` downloads into `models/` and verifies SHA-256 (`--check` verifies only). It exits 2 naming the file on a missing download or checksum mismatch, and it prints licenses. Sources: sherpa from `huggingface.co/csukuangfj/...-2023-06-26`, Kokoro fp32 from `huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX` (`onnx/model.onnx`, saved as `kokoro-v1.0.onnx`), and Kokoro int8 + `voices-v1.0.bin` from the `thewh1teagle/kokoro-onnx` `model-files-v1.0` GitHub release.

## Phase 1b — Supertonic 3 as the TTS alternative (2026-10-16)

This was a follow-up evaluation after the Kokoro NO-GO, done at the user's request outside the ticket's original plan. Supertonic 3 (Supertone, about 99M parameters, ONNX Runtime CPU, Python SDK `supertonic` 1.3.1) was run in an isolated `uv run --no-project --with supertonic` environment, so the project's dependencies are unchanged. Voice `F1` was used, and each figure is the time for one whole `synthesize()` call.

**License:** the SDK is MIT, but the model weights are **BigScience OpenRAIL-M**. That license is not Apache: it carries use-based restrictions (Attachment A). The human must accept it explicitly (A2).

### Thread count × steps, 4C/8T i5-1145G7

| Condition | Threads | Steps | "Sure," | 8 words | 25 words |
|---|---|---|---|---|---|
| battery, balanced | 1 | 4 | 475 | 766 | — |
| battery, balanced | **2** | 4 | **320** | **483** | — |
| battery, balanced | 4 | 4 | 410 | 591 | — |
| battery, balanced | 8 | 4 | 735 | 947 | — |
| battery, balanced | 2 | 8 | 564 | 860 | — |
| battery, performance | 2 | 4 | 316 | 490 | — |
| **AC, performance** | 2 | 2 | 136 | 213 | 631 |
| **AC, performance** | 2 | **4** | **271** | **412** | 1083 |
| **AC, performance** | 2 | 8 | 516 | 717 | 1873 |

All times are p50 in ms. Battery rows are the median of 7 runs; AC rows are the median of 11.

- **2 threads is fastest.** More intra-op threads make every row slower, and all 8 threads is about 2x worse than 2.
- **The power profile barely matters on battery** (±5%). On AC with `performance` it is about 15% faster than battery at 2 threads.
- **Quality, judged by the user by ear:** 4 steps with voice F1 is "decent, though still sounding robotic". 2 steps was not chosen.
- **Long text at 4 steps:** a 132-word paragraph synthesized in 6.7 s for 45.4 s of audio (RTF 0.147, battery, library default threads).
- **Against the gate:** at 4 steps, 2 threads, on AC, the TTS first-audio p50 is at the 400 ms gate when the first chunk is ≤ 8 words (412 ms). It passes with a short first clause (271 ms for "Sure,"). Whole 25-word sentences miss. Concurrency with STT has **not** been measured for Supertonic.

`scripts/power_mode.sh max|balanced|save|status` switches the profile via `powerprofilesctl` without sudo and prints AC state, governor, EPP, turbo and average MHz.

## Phase 2 — Supertonic through the server (2026-10-16)

Measured on AC with `scripts/power_mode.sh max` (profile `performance`, EPP `performance`), running through the real `lan_speech` WebSocket server on `127.0.0.1` (`scripts/laptop_speech_bench.py --tts supertonic --via-server`). STT uses sherpa-onnx int8 streaming Zipformer (2 threads); TTS uses Supertonic 3 (4 steps, 2 threads, voice F1) with mandatory clause chunking.

### Results

| Metric | Alone | Both at once | Ratio | Threshold |
|---|---|---|---|---|
| STT final-after-end p50 (ms) | 62 | 103 | 1.67x | ≤ 300 alone ✅, ≤ 1.5x |
| STT final-after-end p95 (ms) | 74 | 139 | 1.88x | — |
| STT real-time factor | 0.032 | 0.055 | 1.73x | ≤ 1.5x |
| STT WER | 0.106 | 0.106 | 1.00x | ≤ 0.15 alone ✅ |
| TTS first-audio p50 (ms) | 320 | 462 | 1.44x ✅ | ≤ 400 alone ✅ (P9), ≤ 1.5x ✅ |
| TTS first-audio p95 (ms) | 407 | 506 | 1.24x | — |
| TTS real-time factor | 0.148 | 0.175 | 1.18x ✅ | ≤ 0.6 alone ✅, ≤ 1.5x ✅ |

TTS first-audio p50 by sentence length: 8-word 339 ms, 25-word 425 ms.

- **Gate P9 passed:** TTS first-audio p50 alone is **320 ms** (threshold ≤ 400 ms) with clause chunking through the real server.
- **Concurrency check P10:** TTS first-audio under concurrency is 1.44x (within 1.5x limit). STT final-after-end under concurrency is 1.67x (62 ms alone → 103 ms both); per amendment, this concurrency miss on low absolute numbers is recorded and non-fatal.
- **Decision: GO.**

## Protocol Specification (Shared Contract for Ticket 22)

The server binds to `127.0.0.1` by default and exposes WebSocket and HTTP endpoints. Dataclass shapes are defined in `src/lan_speech/protocol.py`.

### Endpoints

#### 1. STT Stream — `ws://H:P/stt`
- **C->S text:** `{"type": "start", "sample_rate": 16000}` (PCM is S16LE mono). Optional hints for the Parakeet engine: `"slot"` (`contact`, `music`, `place`, `brand`, or two joined with `+`) and `"risk"` (`low` or `high`); see `STT-HOSTING.md`.
- **C->S binary:** raw PCM S16LE chunks (typically 100 ms chunks). Audio sent before `start` triggers `error status=400 reason="start required"` and socket closure. Audio sent after `finalize` is treated as a new turn on the same stream.
- **C->S text:** `{"type": "finalize"}` flushes current turn; socket stays open.
- **S->C text:** `{"type": "transcript", "text": str, "is_final": bool, "end_of_turn": bool, "t_audio_end_s": float | null}`. End of turn comes from sherpa's endpoint detection or finalize. With `--stt-model parakeet`, final transcripts may also carry `"raw_text"` and `"repair"` (both omitted otherwise).
- **S->C text:** `{"type": "error", "status": int, "reason": str}`.

#### 2. TTS Stream — `ws://H:P/tts`
- **S->C text:** `{"type": "ready", "sample_rate": int}` sent once on connect (Supertonic 3 sample rate is 44100).
- **C->S text:** `{"type": "speak", "context": int, "text": str, "voice": str | null}`. Text is chunked at `, ; : . ! ?` (and after word 6 if the first chunk is longer than 6 words). Synthesized chunk by chunk. If a previous context is actively synthesizing, returns `error status=409 reason="context busy"`.
- **C->S text:** `{"type": "cancel", "context": int}` stops synthesis and drops queued audio within one chunk; unknown or finished context is ignored silently.
- **S->C binary:** 4-byte big-endian unsigned context id + raw PCM S16LE audio.
- **S->C text:** `{"type": "done", "context": int}` sent when all chunks for the context have finished.
- **S->C text:** `{"type": "error", "status": int, "reason": str}`.

#### 3. Voices List — `GET /voices`
- Requires `Authorization: Bearer <token>`.
- Returns HTTP 200 with JSON list:
  ```json
  [{"id": "F1", "name": "F1", "language": "en"}, ...]
  ```

#### 4. Health Check — `GET /healthz`
- Unauthenticated. Returns HTTP 200:
  ```json
  {"ok": true, "stt": "sherpa-zipformer", "tts": "supertonic-3"}
  ```

### Authentication & Security
- Token comes from environment variable `LAN_SPEECH_TOKEN` (never logged, never in argv).
- Header: `Authorization: Bearer <token>`.
- Missing or invalid token on `/voices`, `/stt`, `/tts` returns HTTP 401 without body details.
- Refuses to start without a token unless `--insecure-loopback` is explicitly set AND the bind address is loopback (`127.0.0.1`).
- Max frame size: 64 KiB (status 413).
- Max text length: 2000 characters per `speak` (status 413).
- Max concurrent connections: 4 (5th connection receives status 503 and is closed).


## Phase 3 — STT model: Kroko (2026-10-16)

The server's speech recognizer is now Kroko, chosen on real microphone speech: 9.8% word error rate against 15.5% for the earlier LibriSpeech Zipformer, about 35 ms after speech ends, 1.2x slowdown while Supertonic runs. `--stt-model zipformer` restores the old one. See `docs/STT-COMPARE.md` for the comparison and `docs/DOC-CHAT.md` for running the chat client. The Phase 2 table above was measured with the older Zipformer.
