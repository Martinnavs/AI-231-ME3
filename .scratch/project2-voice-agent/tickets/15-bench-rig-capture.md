# 15: Benchmark rig capture — play stimulus, record bot + loopback, run end to end

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 14, 11 (dry-run drives `python -m voice_agent`)
**Status:** ready-for-agent

## Files
- `src/bench/rig.py`, `src/bench/run.py`
- `docs/BENCHMARK.md` (append "Running the rig" runbook)
- `tests/bench/test_rig.py`, `tests/bench/test_run_dry.py`

## Action
The host side of the rig (review §8): a benchmark host with a multi-channel audio interface. **Out 1** plays `stimulus.wav` through a speaker into the Pi's mic (through the air, so AEC and the real room are in the test); **In 1** records the Pi's speaker; **In 2** records Out 1 as played (loopback). `rig.py` does one synchronized play+record with `sounddevice` (`sd.playrec` on one duplex stream, so play and record share a clock), with a configurable lead-in and tail.

`run.py` is the one command a human runs per benchmark session: checks devices, records, then calls `bench.analyze` and `bench.report` and writes everything under `out/bench/<run-id>/`. Pi-side logs (stage + health JSONL) are pulled by path arguments (the human copies them with `scp`; no SSH automation in plan 1). Also a `--dry-run` that skips audio hardware: it **synthesizes** the recording by running the stimulus through `python -m voice_agent --fake --input-wav stimulus.wav --output-wav bot.wav` (the real entry point) and stacking `[bot, stimulus]` as ch0/ch1 — so the whole chain (truth → agent → analysis → report) is testable on the dev host.

Review §8 also asks: run at different times of day (cloud latency varies) and record health every run — put both in the runbook, plus ME2's lessons (`.scratch/vendor/AI-222-Machine-Exercises/ME2/docs/RASPBERRY-PI.md`, "Soak-run gotchas"): one run at a time, `vcgencmd get_throttled` = `0x0` before and after, the `pkill -f` pitfall.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| `sounddevice`/PortAudio not importable | `rig` commands exit 2 with "install PortAudio (libportaudio2)"; `--dry-run` still works (no import at module top) |
| device lacks 2 input channels or 1 output | exit 2 listing the device's channel counts |
| device sample rate unsupported | try 48000 then 44100; stimulus resampled to the chosen rate |
| input clipping (any sample at ±1.0 on ch0 or ch1) | warning with count and channel; result still written |
| recording shorter than stimulus + tail | error (buffer underrun); nothing analyzed |
| output dir exists | refuse unless `--overwrite` |
| stage/health logs not given | analysis runs without breakdowns; report says so |

### C — Contracts
```python
# src/bench/rig.py
@dataclass(frozen=True)
class RigConfig:
    device: str | int
    sample_rate: int = 48000
    out_channel: int = 1            # 1-based, as labelled on the interface
    bot_in_channel: int = 1
    loopback_in_channel: int = 2
    lead_in_s: float = 1.5          # silence before playback (noise-floor window for analysis)
    tail_s: float = 10.0

def list_devices() -> str: ...
def check_device(cfg: RigConfig) -> list[str]: ...                 # problems, empty if OK
def play_and_record(stimulus: Path, cfg: RigConfig, out_wav: Path) -> Path: ...   # writes 2-ch WAV: ch0 bot, ch1 loopback

# src/bench/run.py
def main(argv: Sequence[str] | None = None) -> int: ...
#   python -m bench.run --truth-dir out/bench/v1 --device "<name>" [--stage-log F] [--health-log F] --run-id ID
#   python -m bench.run --truth-dir out/bench/v1 --dry-run --run-id ID
#   writes out/bench/<run-id>/{recording.wav, results.json, report.md, meta.json}
#   meta.json: run-id, UTC start, host, device, rate, git commit, truth sha256, notes from --note
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `sounddevice.playrec` with `input_mapping`/`output_mapping` gives sample-aligned duplex I/O on one device | sounddevice docs (WebFetch); the loopback alignment in ticket 14 measures any residual offset anyway |
| A2 | Dry-run's `python -m voice_agent --fake` output WAV shares the input's timeline | ticket 11 P4 guarantees it |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_play_and_record_mapping` — monkeypatched `sounddevice` module: asserts mappings, rate, lead-in/tail lengths, channel order in the written WAV | `uv run pytest -q tests/bench/test_rig.py` |
| P2 | `test_device_checks` — fake device list with 1 input → problem reported | same |
| P3 | `test_dry_run_end_to_end` — tiny truth set (5 turns, fake TTS) → `bench.run --dry-run` writes results with all 5 turns `ok` and first-audio p50 within the fake pipeline's expected range (assert bounds derived from the fake's configured latencies + VAD stop_secs) | `uv run pytest -q tests/bench/test_run_dry.py` |
| P4 | `test_refuses_existing_dir` | same |

## Unknowns
- Which audio interface the team has (any class-compliant USB interface with ≥ 2 inputs works); the runbook lists wiring, not a model.

## Dependencies/license
`sounddevice` (MIT) as an **optional** extra `bench` in `pyproject.toml` (`uv add --optional bench sounddevice`).

## Acceptance Criteria
- [ ] Dry run works on the dev host through the real `voice_agent` entry point
- [ ] Runbook in `docs/BENCHMARK.md`: wiring diagram (text), calibration step (ticket 14 `calibrate` with the bot disabled), per-run checklist, time-of-day guidance
- [ ] All Proof rows green; gate suite green

## Non-Goals
- SSH orchestration of the Pi; automatic log pulling
- Running on real hardware (human checkpoint 6)

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/BENCHMARK.md`

## Execution Log
