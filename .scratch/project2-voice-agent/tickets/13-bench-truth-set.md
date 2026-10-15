# 13: Benchmark truth set — stimulus WAV + truth file with true end of speech (T0)

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 04
**Status:** ready-for-agent

## Files
- `src/bench/truth.py`, `src/bench/signal.py` (shared DSP helpers: frame RMS in dBFS, envelopes, chirp)
- `bench/turns/v1.json` (the turn spec: ~40 conversational turns, 10 barge-in trials, 10 noise-only trials, 10 mid-sentence pause trials)
- `docs/BENCHMARK.md` (shared contract: truth schema + method; this ticket creates it, 14 and 15 append)
- `tests/bench/test_signal.py`, `tests/bench/test_truth.py`

## Action
The input side of the latency benchmark (review §8, §4 first row). T0 must be **when the user actually stopped speaking**, taken from a truth file — not VAD `speech_end`, which fires after the silence wait and hides the biggest delay (review §2 item 1). ME2 did this with a long recording + force-aligned truth (`.scratch/vendor/AI-222-Machine-Exercises/ME2/docs/SOAK-TEST.md`, "The audio" and "Scoring"); we keep the shape (one long seeded recording, truth JSON, score by time range) but use conversational turns.

Decision **D2**: each turn is synthesized by an `audio_engine` `Synthesizer` (real TTS by the human; `fake` in tests), and its true end of speech is the energy boundary of the **clean** clip — exact because nothing else is in the clip. Then the clips are laid out on one timeline:

```
[sync chirp 0.5 s][1.0 s silence]
  [turn 1][answer window W s] [turn 2][answer window W s] ...          kind = "turn"
  [turn k][after D s of the bot's reply: barge-in utterance] ...       kind = "barge_in"
  [turn m][after D s: 150 ms noise burst / click]  ...                 kind = "noise"
  [part 1][pause P s][part 2][answer window W s] ...                  kind = "pause"
```

The answer window (default 8 s) leaves the bot room to reply before the next turn. Barge-in and noise events are scheduled at `t_speech_end + D` (default 2.5 s), which lands inside the bot's reply for normal answer lengths; ticket 14 checks the bot was actually speaking and marks the trial invalid if not. Everything is seeded.

**Pause trials** (professor: the agent must handle natural speech, e.g. "my birthday is… January first, nineteen ninety"; turn-taking benchmarks such as Full-Duplex-Bench score this as pause handling). One user turn spoken in two parts with a silent gap P (spec value, 0.5–1.0 s) between them. The bot must not take the turn during the gap. `t_event_s` = gap start, `t_speech_end_s` = end of part 2 (T0). Each part's boundary is measured on its own clean clip, like any turn.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| clip has leading/trailing silence from the TTS | trimmed before layout; `t_speech_end` measured on the trimmed clip and checked again after layout |
| clip with an internal pause > answer window | `ValueError` naming the turn (spec error) |
| TTS sample rate ≠ stimulus rate (16 kHz) | resampled with `audio_engine.wav.resample` before measuring |
| clip peak > −1 dBFS | normalized to −3 dBFS peak before layout (prevents clipping through the rig) |
| same seed + same clips | byte-identical stimulus WAV (sha256 in truth) |
| spec references an unknown trial kind | `ValueError` |
| pause trial without exactly two parts, or `pause_s` outside 0.3–1.5 s | `ValueError` naming the trial |
| spec turns contain non-ASCII | allowed; UTF-8 in JSON |

### C — Contracts
```python
# src/bench/signal.py
def frame_rms_dbfs(x: npt.NDArray[np.float32], sr: int, frame_ms: float = 10.0, hop_ms: float = 5.0) -> npt.NDArray[np.float64]: ...
def speech_bounds(x: npt.NDArray[np.float32], sr: int, *, rel_db: float = -35.0, hangover_ms: float = 50.0) -> tuple[float, float]: ...
    # first/last frame above (peak_dbfs + rel_db); returns seconds (start, end); end is the end of the last active frame
def make_chirp(sr: int, duration_s: float = 0.5, f0: float = 500.0, f1: float = 4000.0) -> npt.NDArray[np.float32]: ...
def noise_burst(sr: int, duration_ms: float, seed: int, level_dbfs: float = -12.0) -> npt.NDArray[np.float32]: ...

# src/bench/truth.py
@dataclass(frozen=True)
class Trial:
    id: str                       # "t001"
    kind: Literal["turn", "barge_in", "noise", "pause"]
    text: str                     # user turn text
    t_start_s: float              # turn audio start on the stimulus timeline
    t_speech_end_s: float         # T0 on the stimulus timeline
    t_event_s: float | None       # barge-in utterance start / noise burst start / pause start (None for "turn")
    pause_s: float | None         # "pause" trials: gap length
    event_text: str | None        # barge-in utterance text
    window_end_s: float           # start of the next trial (score range end)

@dataclass(frozen=True)
class TruthSet:
    version: int                  # 1
    sample_rate: int
    seed: int
    chirp_s: float                # chirp start (0.0)
    stimulus_sha256: str
    synthesizer: str              # backend name + voice
    trials: list[Trial]

def build_truth_set(spec: Path, synthesizer: Synthesizer, out_dir: Path, *, seed: int = 0,
                    answer_window_s: float = 8.0, event_delay_s: float = 2.5) -> TruthSet: ...
    # writes out_dir/stimulus.wav and out_dir/truth.json
def load_truth(path: Path) -> TruthSet: ...
def main(argv: Sequence[str] | None = None) -> int: ...   # python -m bench.truth --spec bench/turns/v1.json --backend fake --out out/bench/v1
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | TTS output of a clean voice has no internal silence longer than ~0.6 s for one short turn, so `speech_bounds` end = true end of speech | P2 numeric check on synthetic speech-like fixtures; the human spot-checks 5 real clips by ear/spectrogram after building with the real TTS (note in `docs/BENCHMARK.md`) |
| A2 | −35 dB relative threshold + 50 ms hangover catches soft word endings (fricatives like "-s") | P2 includes a fixture whose last 80 ms is a −30 dB-relative noise "fricative"; boundary must include it |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_frame_rms_and_chirp` — known-level sine gives expected dBFS ±0.2 dB | `uv run pytest -q tests/bench/test_signal.py` |
| P2 | `test_speech_bounds_accuracy` — synthetic fixtures with known boundaries (tone + decaying tail, tone + soft fricative tail, two words with 300 ms gap): **\|end − true_end\| ≤ 10 ms** for all (numeric threshold, Operating Principle 10) | same |
| P3 | `test_build_fake_deterministic` — two builds with seed 0 → identical sha256; truth trial count = spec count; trials ordered, non-overlapping, `t_start < t_speech_end < (t_event or window_end) ≤ window_end` | `uv run pytest -q tests/bench/test_truth.py` |
| P4 | `test_layout_t0_matches_clip` — for every trial, `speech_bounds` re-measured on the stimulus slice matches `t_speech_end_s` within 10 ms | same |
| P5 | `test_truth_roundtrip_json` | same |
| P6 | `test_pause_trial_layout` — gap measured on the stimulus between part 1's end and part 2's start equals `pause_s` within 10 ms; `t_event_s` within 10 ms of part 1's end | same |

## Unknowns
None

## Dependencies/license
None new (numpy/scipy/soundfile from 04).

## Acceptance Criteria
- [ ] `python -m bench.truth --backend fake …` builds a stimulus + truth
- [ ] `docs/BENCHMARK.md` documents the truth schema, D2, and "build with real TTS" steps for the human
- [ ] All Proof rows green; gate suite green

## Non-Goals
- Recording human speakers (later; the schema allows it)
- Room simulation / noise mixing (the rig plays through a real room)

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/AUDIO-ENGINE.md`; producer of `docs/BENCHMARK.md` and the truth schema consumed by 14

## Execution Log
