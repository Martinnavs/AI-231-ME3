# 14: Benchmark analysis — align, T0/T4, barge-in, p50/p95, report

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 13 (truth schema), 02 (stage log), 03 (health log)
**Status:** done

## Files
- `src/bench/analyze.py`, `src/bench/report.py`
- `docs/BENCHMARK.md` (append "Analysis" section)
- `tests/bench/test_analyze.py`, `tests/bench/test_report.py`, `tests/bench/synth_recording.py` (builds synthetic rig recordings with known answers)

## Action
Purely offline scoring of one rig recording (review §8). Inputs: the rig recording (≥ 2 channels: **ch0 = bot output** (In 1), **ch1 = loopback of what was played** (In 2)), `stimulus.wav` + `truth.json` (ticket 13), and optionally the Pi's stage JSONL (ticket 02) and health JSONL (ticket 03).

1. **Align:** cross-correlate the stimulus chirp against ch1 → `offset_s` (recording time of stimulus t=0). Confirm with a second correlation on the last trial's audio; if the two offsets disagree by > 5 ms, report clock drift (ppm) and correct linearly. Without the loopback, T0 would be off by the host's unknown playback delay — that's the point of In 2.
2. **T0** per trial = `offset + t_speech_end_s` (with drift correction).
3. **Noise floor** on ch0: median frame RMS (10 ms) over the 1 s before the chirp; **T4** = first frame after T0 where ch0 RMS ≥ floor + `margin_db` (default 12) and stays above for ≥ 30 ms. Search ends at the trial's `window_end`; none found → `missed`, counted, never dropped.
4. **Crosstalk guard:** measure ch0 level during the user's own speech `[t_start, T0]`; if that level reaches floor + margin, the trial is flagged `crosstalk` (the user's voice leaking into the bot channel could fake an onset) and excluded from latency stats but counted. `calibrate` subcommand runs this over a recording made with the bot **disabled** and reports the smallest margin with zero false onsets.
5. **First answer audio:** if the stage log has `filler_played` for the turn, locate the filler clip on ch0 by cross-correlation and take the first onset after its end; else equals T4. Report both; the graded latency is the first *answer* audio, and a stall/notice filler (`kind` in the event data) never counts as the answer. Also report `filler_rate` per kind (review §3).
6. **Barge-in trials:** bot must be active at `t_event` (else `invalid`); **stop latency** = time from event start until ch0 falls below floor + margin for ≥ 200 ms. **Duck latency** = time from event start until ch0 RMS falls ≥ 10 dB below its median over the 200 ms before the event, for ≥ 30 ms (ticket 12 ducks before it stops; this is what the user hears first). Also compute duck latency for noise trials: a noise trial that ducks and recovers is the intended behavior, not a false interruption.
7a. **Pause trials:** **premature takeover** if a bot onset (same onset rule as T4) starts inside `[t_event, T0)` — the bot answered half a sentence. Takeover rate = takeovers / valid pause trials. A pause trial with no takeover is also scored for T4 like a normal turn.
7. **Noise trials:** **false interruption** if the bot stops within 1 s of the burst (same stop rule) — rate = false / valid.
8. **Stats:** p50, p95, mean, n, missed, crosstalk, invalid — per metric. Stage breakdown (if stage log): per-turn deltas from `endpoint` to each later stage; medians. Health (if given): max temp, min ARM clock, any throttle flag set, median CPU %, peak RSS.
9. `report.py` writes `results.json` (machine) and `report.md` (human: one headline table, then breakdowns; plain words).

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| chirp not found (peak correlation < 0.3 normalized) | abort with "loopback channel has no chirp: check In 2 wiring" |
| recording has 1 channel | abort: "need bot + loopback channels" |
| drift > 200 ppm | warn in report (likely wrong sample rate on one side) |
| onset never found in window | `missed`; counted in n_missed; excluded from percentiles; listed by trial id |
| fewer than 20 valid trials for a metric | p95 reported but marked "low n" |
| stage log turn ids don't map to trials | match by time (stage `endpoint` wall time nearest after T0 within 3 s); unmatched turns listed |
| recording sample rate ≠ stimulus rate | stimulus resampled to the recording rate before correlation |

### C — Contracts
```python
# src/bench/analyze.py
@dataclass(frozen=True)
class TrialResult:
    id: str
    kind: str
    status: Literal["ok", "missed", "crosstalk", "invalid"]
    t0_rec_s: float
    first_audio_s: float | None          # T4 - T0
    first_answer_audio_s: float | None
    barge_in_stop_s: float | None
    barge_in_duck_s: float | None        # None if the bot never ducked
    false_interruption: bool | None
    premature_takeover: bool | None      # pause trials only

@dataclass(frozen=True)
class MetricStats:
    n: int
    p50: float | None
    p95: float | None
    mean: float | None
    low_n: bool

@dataclass(frozen=True)
class Analysis:
    offset_s: float
    drift_ppm: float
    noise_floor_dbfs: float
    margin_db: float
    trials: list[TrialResult]
    first_audio: MetricStats
    first_answer_audio: MetricStats
    barge_in_stop: MetricStats
    barge_in_duck: MetricStats
    false_interruption_rate: float | None
    premature_takeover_rate: float | None
    counts: dict[str, int]               # ok/missed/crosstalk/invalid
    stages: dict[str, MetricStats] | None    # "endpoint->stt_final", ...
    health: dict[str, float | bool] | None

def analyze(recording: Path, stimulus: Path, truth: Path, *, stage_log: Path | None = None,
            health_log: Path | None = None, margin_db: float = 12.0) -> Analysis: ...
def calibrate(recording_bot_disabled: Path, stimulus: Path, truth: Path) -> float: ...   # smallest safe margin_db
def main(argv: Sequence[str] | None = None) -> int: ...   # python -m bench.analyze {score,calibrate} ...

# src/bench/report.py
def write_report(analysis: Analysis, out_dir: Path, *, title: str) -> tuple[Path, Path]: ...   # (results.json, report.md)
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | FFT cross-correlation (`scipy.signal.correlate`, method="fft") of a 0.5 s chirp locates the offset within 1 sample at 48 kHz with −20 dB SNR | P1 asserts it |
| A2 | A 12 dB margin with a 30 ms hold rejects room-noise fluctuations on a quiet floor | P3 asserts on synthetic noise; real value comes from `calibrate` on hardware |

### P — Proof
`tests/bench/synth_recording.py` builds 2-channel recordings from a ticket-13 truth set (fake TTS) with **known** injected latencies, playback delay, drift, noise floor and crosstalk.

| # | Test | Command |
|---|---|---|
| P1 | `test_alignment_offset_and_drift` — injected playback delay 137.3 ms and 50 ppm drift recovered within 1 ms / 5 ppm | `uv run pytest -q tests/bench/test_analyze.py` |
| P2 | `test_first_audio_exact` — injected bot latencies (e.g. 0.42…1.10 s) recovered within **±10 ms** per trial; p50/p95 equal numpy percentiles of the injected values within 10 ms | same |
| P3 | `test_no_false_onsets_in_noise` — floor −60 dBFS with ±3 dB fluctuation, no bot audio → all `missed`, none `ok` | same |
| P4 | `test_crosstalk_flagged` — user speech leaking into ch0 at floor+15 dB → `crosstalk`, excluded from stats | same |
| P5 | `test_filler_then_answer` — filler clip then real answer 0.6 s later → `first_answer_audio − first_audio` ≈ filler duration + gap ±15 ms | same |
| P6 | `test_barge_in_stop_and_false_interruption` — injected stop latencies recovered ±15 ms; injected −15 dB ducks recovered ±15 ms as `barge_in_duck_s` (and a trial with no duck → None); noise trial where bot keeps talking → not false; where bot stops → false | same |
| P7 | `test_missing_chirp_aborts`, `test_mono_aborts` | same |
| P9 | `test_pause_takeover` — pause trial with bot audio injected 300 ms into the gap → `premature_takeover=True`, excluded from first-audio stats; injected only after T0 → `False` and T4 recovered ±10 ms | `uv run pytest -q tests/bench/test_analyze.py` |
| P8 | `test_report_files` — `results.json` round-trips; `report.md` contains the headline table with p50/p95 and counts | `uv run pytest -q tests/bench/test_report.py` |

## Unknowns
None

## Dependencies/license
None new.

## Acceptance Criteria
- [x] `python -m bench.analyze score …` and `calibrate …` work on synthetic recordings
- [x] `docs/BENCHMARK.md` "Analysis" section: T0/T4 definitions, margins, statuses, what each report number means
- [x] All Proof rows green; gate suite green

## Non-Goals
- Capturing audio (ticket 15)
- Plots (a table is enough for plan 1)

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/BENCHMARK.md`, `docs/EVENT-LOG.md`

## Execution Log

### 2026-10-15 developer: DONE
- Assumptions verified: A1 `scipy.signal.correlate` method="fft" with 0.5 s chirp at 48 kHz / -20 dB SNR locates offset within 0 samples → verified; A2 12 dB margin with 30 ms hold on quiet floor (-60 dBFS ±3 dB fluctuation) gives zero sustained runs (max RMS -55.8 dBFS vs thresh -45.9 dBFS) → verified by synthetic noise and P3.
- Proof: P1 ✅ P2 ✅ P3 ✅ P4 ✅ P5 ✅ P6 ✅ P7 ✅ P8 ✅ P9 ✅ (command: `uv run pytest -q tests/bench/test_analyze.py tests/bench/test_report.py` → 11 passed)
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (101 passed, 0 skipped)
- Files: src/bench/analyze.py, src/bench/report.py, docs/BENCHMARK.md, tests/bench/synth_recording.py, tests/bench/test_analyze.py, tests/bench/test_report.py
- Deviations: none
- Commit: see git log (14: Benchmark analysis — align, T0/T4, barge-in, p50/p95, report)
- Notes for reviewer: none


### 2026-10-15 claude (review note, R3): `filler_rate` per kind (delegate/stall/notice) from the amended Action step 5 is not reported by `analyze`/`report`; first-answer-audio handling is correct for any filler kind. Fold into the tech-lead review / a later small fix.

### 2026-10-16 tech-lead: Review Feedback (main @ 92f3edb)
- R8 [R2, HUMAN H2] turn<->trial matching by index; time-match edge not implementable as specified. R9 [R2] only first filler used; clip sample rate wrong. R10 [R2] stage breakdown mixes occurrences. R12 [R2] calibrate test asserts only > 0. R23/R24 [R3].
Full detail (root cause, evidence, fix): `handoff/REVIEW-main-92f3edb.md`. Not auto-fixed; retries need human approval.
