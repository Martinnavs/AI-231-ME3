# Latency benchmark: stimulus and truth

The input side of the benchmark. T0 is when the user actually stopped speaking, read from a truth file. It is not the VAD `speech_end` event, which fires after the silence wait and hides the biggest delay. Tickets 14 and 15 append their sections below.

## Method (decision D2)

Each turn is synthesized by an `audio_engine` `Synthesizer` (real TTS when the human builds the set, `fake` in tests). The true end of speech is the energy boundary of the clean clip, which is exact because nothing else is in the clip. The clips are then laid out on one 16 kHz timeline:

```
[sync chirp 0.5 s][1.0 s silence]
  [turn][answer window W]                          kind = "turn"
  [turn][after D s: barge-in utterance]            kind = "barge_in"
  [turn][after D s: 150 ms noise burst]            kind = "noise"
  [part 1][pause P][part 2][answer window W]       kind = "pause"
```

- `W` (default 8 s) leaves the bot room to reply before the next trial. `D` (default 2.5 s) places the barge-in or noise event at `t_speech_end + D`, inside the bot's reply for normal answer lengths. Consumers must check the bot was actually speaking then, and mark the trial invalid if not.
- A barge-in trial's window ends at `max(t_speech_end + W, event end + 1 s)`.
- Pause trials: one user turn in two parts with a silent gap `P` (0.3 to 1.5 s). The bot must not take the turn during the gap. `t_event_s` is the gap start and `t_speech_end_s` (T0) is the end of part 2.
- Each clip is resampled to 16 kHz, trimmed to its speech bounds, and scaled to a -3 dBFS peak if it peaks above -1 dBFS. A clip with an internal silence longer than `W` is a spec error (`ValueError` naming the trial).
- Speech bounds (`bench.signal.speech_bounds`): 10 ms frames, 5 ms hop, active when RMS is within 35 dB of the loudest frame, then refined to the first and last sample above the same threshold inside those frames. Same seed and same clips give a byte-identical `stimulus.wav`.

## Truth schema (`truth.json`, version 1)

```json
{
  "version": 1, "sample_rate": 16000, "seed": 0, "chirp_s": 0.0,
  "stimulus_sha256": "<sha256 of stimulus.wav>", "synthesizer": "FakeSynthesizer:default",
  "trials": [
    {"id": "t001", "kind": "turn", "text": "...", "t_start_s": 1.5, "t_speech_end_s": 3.1,
     "t_event_s": null, "pause_s": null, "event_text": null, "window_end_s": 11.1}
  ]
}
```

All times are seconds on the stimulus timeline. `t_event_s` is the barge-in utterance start, the noise burst start, or the pause start (null for `turn`). `window_end_s` is the start of the next trial and the end of this trial's score range. For `pause` trials `t_start_s < t_event_s < t_speech_end_s`. Python: `bench.truth.load_truth(path)` returns a `TruthSet`.

## Build

Spec: `bench/turns/v1.json` (40 turns, 10 barge-in, 10 noise-only, 10 mid-sentence pause trials; 70 trials, about 12 minutes).

```bash
uv run python -m bench.truth --spec bench/turns/v1.json --backend fake --out out/bench/v1
```

### Build with real TTS (human step)

1. Register a real synthesizer backend in `audio_engine` (a vendor backend ticket), with its API key in `.env`.
2. Run the command above with `--backend <name> --voice <voice-id> --out out/bench/v1-real`. This calls a paid API, so only the human runs it.
3. Spot-check about 5 clips by ear or spectrogram: the `t_speech_end_s` of each trial should sit at the last audible sound, including soft word endings such as "-s". Assumption A1 (no internal silence over about 0.6 s per short turn) is checked this way, since the synthetic fixtures in the tests cannot cover real TTS.
4. Record the `synthesizer` string and the stimulus `sha256` with the benchmark results.
