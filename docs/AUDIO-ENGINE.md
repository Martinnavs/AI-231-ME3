# audio_engine: STT/TTS contract

One engine, two consumers: the Pipecat processors (live loop) and the speech MCP server import the same `audio_engine` package. Nothing in `audio_engine` imports Pipecat, `websockets` or any vendor SDK at import time.

## Wire formats

| Where | Format |
|---|---|
| Streaming audio (STT in, TTS out) | PCM S16LE mono `bytes`, at `Transcriber.sample_rate` / `Synthesizer.sample_rate` |
| Batch results and files | `AudioClip`: float32 mono numpy in [-1, 1] plus `sample_rate` |

`audio_engine.wav` converts: `pcm16_to_float`, `float_to_pcm16`, `read_wav` (stereo is averaged to mono; unreadable file → `AudioFileError`), `write_wav` (PCM_16), `resample` (polyphase). Odd-length PCM raises `ValueError`.

## Interfaces (`audio_engine.base`)

- `Transcriber`: `transcribe(clip)` (batch, final only), `open_stream()` → `TranscriptionStream`, `aclose()`.
- `TranscriptionStream`: `start()` (open or reuse the connection at app start), `send(pcm)`, `finalize()` (end of user turn), `results()` (async iterator of interim and final `Transcript`s until `aclose()`), `aclose()`.
- `Synthesizer`: `synthesize(text, voice)` (batch `AudioClip`), `stream_synthesize(text, voice)` (async iterator of S16LE chunks), `list_voices()`, `warm_up()`, `cancel()`, `aclose()`.

`Transcript.end_of_turn=True` means the backend judged the user's turn complete (cloud turn detection; no model runs on the Pi). `t_audio_end_s` is audio time, not wall time.

**Cancel:** after `await synth.cancel()` returns, an in-flight `stream_synthesize` yields no further chunk. Cancelling the consuming task does the same. Backends keep their connection when they can.
**Empty text** yields nothing and makes no network call.

## Registry

`register_transcriber(name, cls_or_import_path)` / `register_synthesizer(...)`; `create_transcriber(name, **kw)` / `create_synthesizer(...)`; `list_backends()` → `{"stt": [...], "tts": [...]}`. An unknown name raises `KeyError` listing the registered names. A registered value may be a `"module:Class"` string, imported on first `create_*`, so vendor backends never load until used. `"fake"` is registered for both at import.

## Fakes (deterministic, no network)

- `FakeTranscriber(script=("hello",), interim_every_ms=100, latency_ms=0, eot_silence_ms=300)`: one interim per `interim_every_ms` of audio sent; `finalize()` yields the next scripted text (the last one repeats); an `end_of_turn` result is emitted once speech (frame RMS > -40 dBFS) is followed by `eot_silence_ms` of quieter audio, stamped with the audio time at which the silence completed. `eot_silence_ms=None` disables it.
- `FakeSynthesizer(sample_rate=16000, ms_per_char=60, chunk_ms=20, first_chunk_latency_ms=0)`: a 220 Hz tone at -12 dBFS, `len(text) * ms_per_char` long, in `chunk_ms` chunks.

## Adding a backend

1. Subclass `Transcriber`/`TranscriptionStream` or `Synthesizer` in `src/audio_engine/backends/<name>.py`. ABCs, not Protocols: a missing method fails at instantiation. Do heavy and vendor imports inside the class or its methods.
2. Return float32 numpy for batch methods and S16LE bytes for streams. Honour `cancel()` within one chunk.
3. Register by import path in `audio_engine/__init__.py` (`register_synthesizer("name", "audio_engine.backends.name:NameSynthesizer")`).
4. Test with the fake-style pattern: monkeypatch the vendor client, cover the happy path, cancel, empty text and error mapping. No live calls outside tests marked `live`.
