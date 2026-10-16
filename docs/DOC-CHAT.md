# Voice Doc-Chat Latency Evaluation

This document describes the voice doc-chat retrieval harness (`doc_chat`) and reports the end-to-end voice loop latency (speech in → text → answer retrieval → speech out) measured through the LAN speech server on the laptop CPU.

## How to Run

### 1. Start the Speech Server
In one terminal, start the speech server on loopback:
```bash
uv run --group laptop python -m lan_speech --host 127.0.0.1 --port 8765 --insecure-loopback
```
Or with token authentication:
```bash
export LAN_SPEECH_TOKEN="your-secret-token"
uv run --group laptop python -m lan_speech --host 127.0.0.1 --port 8765
```

### 2. Run Voice Doc-Chat
Run over pre-rendered audio files (WAV inputs):
```bash
uv run --group laptop python -m doc_chat \
    --server ws://127.0.0.1:8765 \
    --wav out/laptop_bench/renders/*.wav \
    --docs docs/ \
    --out out/doc_chat/ \
    --log out/doc_chat/turns.jsonl
```

Run with interactive text query (skipping STT):
```bash
uv run --group laptop python -m doc_chat \
    --server ws://127.0.0.1:8765 \
    --text "What is the audio configuration?" \
    --docs docs/
```

Run with microphone and live audio playback (requires sounddevice):
```bash
uv run --group laptop python -m doc_chat \
    --server ws://127.0.0.1:8765 \
    --mic \
    --play \
    --docs docs/
```

## Timing Fields

Each turn logs a JSON record to `--log` with client-measured timestamps using `time.perf_counter()`:
- `stt_final_ms`: Time from the last audio sample sent to the final transcript received.
- `retrieval_ms`: Time spent in BM25 retrieval and markdown stripping / answer extraction.
- `tts_first_audio_ms`: Time from `speak` message sent to the first binary audio chunk received from the server.
- `tts_total_ms`: Time from `speak` message sent until the `done` message is received.
- `speech_end_to_first_audio_ms`: Time from the last audio sample sent to the first TTS byte received: the user-perceived turnaround latency.
- `cancel_to_silence_ms`: Time from `cancel` message sent to playback stopped / confirmation (null unless cancelled).

## Talking with the microphone

Start the server (a shorter `--endpoint-silence` ends your turn sooner; the default is 1.2 s):
```bash
uv run --group laptop python -m lan_speech --insecure-loopback --endpoint-silence 0.7
```
Then, once `curl http://127.0.0.1:8765/healthz` answers:
```bash
uv run --group laptop python -W ignore -m doc_chat --mic --play --docs corpus/harry-potter
```
Speak a question and pause. The terminal shows `hearing:` while you talk, then `you:` and `bot:` lines, and the answer is spoken. The loop repeats until you say "quit" or press Ctrl-C.

- It is half-duplex: the mic is closed while the answer plays, so use speakers or headphones without worrying about echo. Barge-in is not supported in mic mode.
- In mic mode `speech_end_to_first_audio_ms` runs from the moment the end-of-turn transcript arrived, so the server's trailing-silence wait (`--endpoint-silence`) is on top of it, and `stt_final_ms` is null.

## Answering with Qwen

By default the answer is the best-matching passage. To have Qwen answer instead, add `--llm qwen`:
```bash
export ONIT_API_KEY=...   # the same variable opencode's upd-ai provider reads
uv run --group laptop python -W ignore -m doc_chat --mic --play --docs corpus/harry-potter --llm qwen
```
The server URL and model id are read at runtime from `~/.config/opencode/opencode.json` (provider `upd-ai`); the key only ever comes from `ONIT_API_KEY`.

- **One shot, no memory:** every question is a fresh call with just a system prompt, the top 2 retrieved passages as context, and the question.
- Thinking is switched off (`enable_thinking: false`), replies are capped at 160 tokens and 30 s, and the answer is cut to `--max-words` at a sentence end.
- If the call fails (timeout, bad key, server down) the retrieved passage is spoken instead and the turn log records `llm_error`.
- Each turn log line gains `llm_ms`, and the summary prints an `llm_ms` row. `speech_end_to_first_audio_ms` includes it.
- Not covered by automated tests against the real server: that needs your key. Tests use a local fake OpenAI-compatible endpoint.

## How speech is chunked and paced

The server speaks an answer chunk by chunk, in order. A chunk ends at `, ; : . ! ?` followed by a space or the end of the text, optionally with a closing quote or bracket (so `."` ends a sentence). A period after initials or an abbreviation (`J.K.`, `Dr.`, `Mr.`, `e.g.`) is not a boundary, so "J.K. Rowling" is read as one. A first chunk over 6 words is split after word 6 so audio starts sooner.

Supertonic pads every chunk with 0.3–0.6 s of silence, which made gaps of about 1 s. The server trims that and inserts its own pause after each chunk, shortest for commas:

| After | Default pause | Flag |
|---|---|---|
| `,` | 0.12 s | `--comma-pause` |
| `;` `:` | midpoint (0.26 s) | derived |
| `.` `!` `?` | 0.40 s | `--sentence-pause` |

Measured gaps include about 80 ms of edge margin on top. Speech becomes audible about 0.5 s after the request instead of about 0.9 s.

## Latency Results (40 Synthetic Turns)

- **Hardware:** HP EliteBook 840 G8, Intel Core i5-1145G7 (4C/8T)
- **Power Configuration:** Connected to AC power, `powerprofilesctl` set to `performance` via `scripts/power_mode.sh max`
- **Speech Server:** Sherpa-ONNX Zipformer (int8) + Supertonic 3 (total_steps=4, 2 threads, clause chunking) over WebSocket loopback (`127.0.0.1:8765`)
- **Caveat on Input Audio:** The input consists of **synthetic TTS audio** (40 pre-rendered turns from `bench/turns/v1.json` rendered via Kokoro/Supertonic in `out/laptop_bench/renders/*.wav`).

| Timing Field | p50 (ms) | p95 (ms) | Description |
|---|---|---|---|
| `stt_final_ms` | 28.6 | 37.4 | Final transcript after last audio frame |
| `retrieval_ms` | 0.4 | 0.5 | BM25 retrieval over document corpus |
| `tts_first_audio_ms` | 310.9 | 416.6 | First TTS audio chunk from speak request |
| `tts_total_ms` | 1135.3 | 2244.5 | Full utterance synthesis |
| `speech_end_to_first_audio_ms` | 343.7 | 445.4 | End of user speech to first audible answer |

The user-perceived voice turnaround latency (`speech_end_to_first_audio_ms`) achieves a p50 of **343.7 ms** and p95 of **445.4 ms**, comfortably beating typical 1-second conversational responsiveness targets.
