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

## Analysis

Offline scoring of a rig recording (`bench.analyze`). Inputs:
- The rig recording (WAV with $\ge 2$ channels: **ch0 = bot output** (In 1), **ch1 = loopback of what was played** (In 2))
- `stimulus.wav` and `truth.json` (from `bench.truth`)
- Optional stage event log (`--stage-log`, JSONL from ticket 02)
- Optional Pi health log (`--health-log`, JSONL from ticket 03)

### Alignment and Clock Drift

1. **Chirp Cross-Correlation:** The 0.5 s linear chirp at the start of `stimulus.wav` is cross-correlated against loopback `ch1` using FFT cross-correlation (`scipy.signal.correlate`). The normalized peak correlation must be $\ge 0.3$; if lower, analysis aborts (`"loopback channel has no chirp: check In 2 wiring"`). The peak location determines `offset_s` (recording time of stimulus $t=0$).
2. **Drift Confirmation:** A second cross-correlation locates the user speech of the final trial on `ch1`. If the implied offset disagrees with `offset_s` by $> 5$ ms, clock drift (in ppm) is calculated and corrected linearly across all trial timelines. If drift exceeds 200 ppm, a warning is raised in `report.md`.

### T0 and T4 Definitions

- **T0:** The true end of user speech on the recording timeline:
  $$T0 = \text{offset}_s + t_{\text{speech\_end\_s}} \times (1 + \text{drift})$$
- **Noise Floor:** Median 10 ms frame RMS (in dBFS) on `ch0` measured over the 1.0 s silence preceding the chirp.
- **T4:** The start of the first 10 ms frame after $T0$ where `ch0` RMS reaches $\ge \text{floor} + \text{margin\_db}$ (default 12 dB) and remains above threshold for $\ge 30$ ms.
- **First Audio Latency:** $T4 - T0$. If no onset occurs before `window_end_s`, the trial is marked `missed`.

### Crosstalk Guard

During user speech $[t_{\text{start}}, T0]$, `ch0` RMS is monitored. If signal level on `ch0` reaches $\text{floor} + \text{margin\_db}$, user voice acoustic or electrical leakage into the bot microphone channel could trigger a false onset. The trial is flagged with status `crosstalk` and excluded from latency stats.
The `calibrate` subcommand determines the smallest safe margin with zero false onsets on a recording made with the bot disabled:
```bash
uv run python -m bench.analyze calibrate <recording_bot_disabled.wav> stimulus.wav truth.json
```

### First Answer Audio and Fillers

If the stage log records `filler_played` events for the turn:
- All `filler_played` events for the turn are collected in chronological order.
- The search is chained: each subsequent filler is located after the previous filler's end.
- When clip audio is available, the clip is resampled to the recording sample rate with `audio_engine.wav.resample` before measuring duration and cross-correlating.
- The first bot onset *after* the last filler ends is taken as the answer audio ($T_{\text{answer}}$).
- `first_answer_audio_s` $= T_{\text{answer}} - T0$. Notice/stall fillers never count as the answer.
- If no filler was played, `first_answer_audio_s` equals `first_audio_s`.
- Per-kind filler rates (`delegate`, `stall`, `notice`) are recorded in `results.json` and formatted in `report.md`.

### Barge-In and Interruption Metrics

- **Bot Active Check:** For `barge_in` and `noise` trials, the bot must be actively speaking (`ch0` RMS $\ge \text{floor} + \text{margin\_db}$) at $t_{\text{event\_rec}}$; otherwise the trial is marked `invalid`.
- **Barge-in Stop Latency:** Elapsed time from $t_{\text{event\_rec}}$ until `ch0` falls below $\text{floor} + \text{margin\_db}$ and stays below for $\ge 200$ ms.
- **Barge-in Duck Latency:** Elapsed time from $t_{\text{event\_rec}}$ until `ch0` RMS drops $\ge 10$ dB below its median over the preceding 200 ms, holding for $\ge 30$ ms. Evaluated before bot mute. Headline `barge_in_duck` statistics include `barge_in` trials only.
- **Noise Trials (False Interruption):** A noise burst trial is a `false_interruption` if the bot stops within 1.0 s of the burst. A noise burst where the bot ducks and recovers is intended behavior. The duck latency is recorded on the `TrialResult` but excluded from the `barge_in_duck` summary stats. False interruption rate is $\text{false\_interruptions} / \text{valid\_noise\_trials}$.
- **Pause Trials (Premature Takeover):** If a bot onset starts inside the silent gap $[t_{\text{event\_rec}}, T0)$, `premature_takeover = True` (bot answered half a sentence) and the trial is excluded from first-audio latency stats. A pause trial without takeover is scored for T4 after $T0$ normally. Takeover rate is $\text{takeovers} / \text{valid\_pause\_trials}$.

### Trial Statuses

- `ok`: Valid trial scored successfully.
- `missed`: Bot onset was not detected within `window_end_s`. Counted in `n_missed`, excluded from percentile latencies, listed by trial id in `report.md`.
- `crosstalk`: Leaked speech exceeded floor + margin during user speech. Excluded from latency percentiles.
- `invalid`: Bot was not active at the time of an interruption event (`barge_in` or `noise`).

### Report Metrics and Interpretation

`bench.report` generates `results.json` and human-readable `report.md`:
- **Headline Table:**
  - `p50 (s)` / `p95 (s)`: 50th and 95th percentiles of valid trials.
  - `Mean (s)`: Average latency across valid trials.
  - `low n`: Flagged when fewer than 20 valid trials exist for a metric.
  - `False Interruption Rate`: Fraction of valid noise bursts that caused the bot to mute.
  - `Premature Takeover Rate`: Fraction of valid pause trials where the bot cut in mid-sentence.
- **Filler Rates:** Per-kind breakdown (`delegate`, `stall`, `notice`) showing the proportion of trials with that filler kind played.
- **Stage Breakdown:** Per-turn latency deltas measured from the `endpoint` stage event to the first occurrence of each later stage (`stt_final`, `llm_first_token`, `tts_first_chunk`, `audio_out_first`).
- **Pi Health Summary:** Peak temperature (°C), minimum ARM core frequency (MHz), CPU throttle flags set, median system CPU %, and peak process RSS (MB).

### CLI Usage

```bash
# Score a benchmark recording
uv run python -m bench.analyze score recording.wav stimulus.wav truth.json \
  --stage-log stage_events.jsonl \
  --health-log pi_health.jsonl \
  --out-dir out/bench/run1

# Calibrate onset margin on quiet recording with bot disabled
uv run python -m bench.analyze calibrate recording_bot_disabled.wav stimulus.wav truth.json
```
