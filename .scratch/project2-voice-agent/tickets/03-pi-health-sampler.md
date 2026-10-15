# 03: Pi health sampler

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 01
**Status:** ready-for-agent

## Files
- `src/voice_agent/telemetry/pi_health.py`
- `tests/telemetry/test_pi_health.py`, `tests/fixtures/pi_health/` (fake `/proc` + `/sys` trees)

## Action
A 1 Hz sampler recording Pi health during every benchmark and live run: SoC temperature, ARM clock, throttling flags, system CPU %, available memory, and the RSS + CPU % of one target process. ME2 did this with an external `pi_agent` (see `.scratch/vendor/AI-222-Machine-Exercises/ME2/docs/VCM-BENCHMARK-RESULTS.md`, "Pi metrics" row, and `ME2/docs/RASPBERRY-PI.md`, "Keep the Pi cool"); here it is our own module, run as `python -m voice_agent.telemetry.pi_health --pid <PID> --out out/health/<run>.jsonl`. Read from files where possible (fast, no subprocess): `/sys/class/thermal/thermal_zone0/temp`, `/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq`, `/proc/stat`, `/proc/meminfo`, `/proc/<pid>/stat`, `/proc/<pid>/status`. Throttling needs `vcgencmd get_throttled` (subprocess, once per sample). All file reads go through a configurable `root` so tests use a fake tree.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| not on a Pi (no `vcgencmd`, no thermal zone) | those fields are `null`; sampler still runs; one warning on stderr at start |
| `vcgencmd` hangs | 0.5 s subprocess timeout → `throttled: null` for that sample |
| target PID exits | sample records `proc: null`, sampler keeps running until stopped (the run may restart it) |
| `--pid` not given | `proc` omitted; system-level fields only |
| SIGINT / SIGTERM | writes a final `{"event":"health_closed","samples":N}` and exits 0 |
| sampling overruns 1 s | next tick scheduled from monotonic start (`t0 + k*interval`), skipping missed ticks; `overruns` counted |
| throttled value non-zero | written as hex string *and* decoded booleans (`under_voltage_now`, `freq_capped_now`, `throttled_now`, `soft_temp_limit_now`, and the four `*_since_boot`) |

### C — Contracts
```python
# src/voice_agent/telemetry/pi_health.py
@dataclass(frozen=True)
class ThrottleFlags:
    raw: str                       # e.g. "0x50005"
    under_voltage_now: bool
    freq_capped_now: bool
    throttled_now: bool
    soft_temp_limit_now: bool
    under_voltage_since_boot: bool
    freq_capped_since_boot: bool
    throttled_since_boot: bool
    soft_temp_limit_since_boot: bool

def decode_throttled(raw: str) -> ThrottleFlags: ...    # bits 0-3 now, 16-19 since boot

@dataclass(frozen=True)
class ProcSample:
    pid: int
    rss_mb: float
    cpu_pct: float                 # % of one core since previous sample

@dataclass(frozen=True)
class HealthSample:
    t_mono_ns: int
    t_wall: float
    temp_c: float | None
    arm_mhz: float | None
    throttled: ThrottleFlags | None
    cpu_pct: float | None          # all cores, 0-100
    mem_available_mb: float | None
    proc: ProcSample | None

class HealthSampler:
    def __init__(self, *, root: Path = Path("/"), pid: int | None = None,
                 run_cmd: Callable[[list[str], float], str | None] = ..., interval_s: float = 1.0) -> None: ...
    def sample(self) -> HealthSample: ...           # first call primes CPU deltas; cpu fields None
    def run(self, out: Path, stop: threading.Event) -> int: ...   # returns samples written

def main(argv: Sequence[str] | None = None) -> int: ...
```
JSONL line: `{"v":1,"event":"health", ...HealthSample fields flattened...}`; first line `{"v":1,"event":"health_opened","host":...,"pid":...,"interval_s":1.0}`.

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `vcgencmd get_throttled` prints `throttled=0x...` | Raspberry Pi docs (WebFetch "vcgencmd get_throttled"); bit layout matches A1 before writing `decode_throttled` |
| A2 | `/proc/<pid>/stat` fields 14/15 (utime/stime) in clock ticks; `os.sysconf("SC_CLK_TCK")` gives the tick rate | `man 5 proc` / run on dev host |
| A3 | `scaling_cur_freq` is in kHz | Linux cpufreq docs; fake tree uses kHz |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_decode_throttled` — `0x0` all false; `0x50005` sets under_voltage_now, throttled_now, under_voltage_since_boot, throttled_since_boot | `uv run pytest -q tests/telemetry/test_pi_health.py` |
| P2 | `test_sample_from_fake_tree` — fake tree + two snapshots of `/proc/stat` give cpu_pct within 0.1 of hand-computed value; temp 61.5; mhz 1500 | same |
| P3 | `test_off_pi_fields_null` — empty root: all optional fields `None`, no exception | same |
| P4 | `test_pid_exit_gives_null_proc` | same |
| P5 | `test_run_writes_jsonl_and_closes` — `interval_s=0.01`, stop after 5 samples, file has opened + ≥5 health + closed lines | same |
| P6 | `test_main_on_dev_host` — `main(["--out", tmp, "--duration", "0.05"])` exits 0 on the real `/` | same |

## Unknowns
None

## Dependencies/license
None (stdlib only).

## Acceptance Criteria
- [ ] Contracts implemented; CLI documented in `--help`
- [ ] All Proof rows green; gate suite green

## Non-Goals
- No plotting, no alerting, no remote collection

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- Output consumed by ticket 14 (`bench.analyze` joins health JSONL)

## Execution Log
