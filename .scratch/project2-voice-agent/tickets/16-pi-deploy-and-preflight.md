# 16: Pi deployment — preflight check, systemd user services, setup runbook

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 12
**Status:** ready-for-agent

## Files
- `src/voice_agent/preflight.py`
- `deploy/systemd/voice-agent.service`, `deploy/systemd/voice-agent-health.service`, `deploy/systemd/voice-agent-sites.service`, `deploy/install.sh`
- `docs/PI-SETUP.md`
- `tests/test_preflight.py`, `tests/fixtures/preflight/` (fake roots), `tests/test_deploy_units.py`

## Action
Make the Pi side repeatable. `python -m voice_agent.preflight` checks everything the earlier tickets assumed about the Pi and prints a pass/fail table with the fix for each failure. `deploy/` runs the agent headless as **systemd user services with linger**, the ME2 pattern (`.scratch/vendor/AI-222-Machine-Exercises/ME2/deploy/systemd/*.service`, `ME2/deploy/install-demo.sh`, `ME2/docs/UI-DEMO.md`). `docs/PI-SETUP.md` is the human runbook: from a fresh Debian 13 arm64 Pi to a running agent, carrying over ME2's Pi lessons (`ME2/docs/RASPBERRY-PI.md`: 64-bit OS, stable mic names `plughw:CARD=<name>,DEV=0`, cooling, one benchmark at a time, the `pkill -f` pitfall) and the new prerequisites from tickets 08 and 11 (bubblewrap, memory cgroup, PortAudio). Add a **"Pi 4 hardware rules"** section (`FULL-DUPLEX-RESEARCH.md` §7.4–7.5): plug the USB speakerphone into a **USB 2.0 (black) port**; use the official 5.1 V / 3 A (15.3 W) supply; active cooling; headless `multi-user.target`; ALSA at 16 kHz S16_LE mono with 20 ms periods (320 frames) and 3 periods per buffer, and how to confirm it (`/proc/asound/card*/pcm*/sub0/hw_params` while the agent runs). State each rule's reason in one line.

The agent writes the files; **it does not install or enable anything** (that's the human, on the Pi).

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| run on a non-Pi host | checks that need Pi tools report `skip (not a Pi)`, not `fail`; exit code reflects only real failures |
| `vcgencmd get_throttled` ≠ `0x0` | fail, decoded flags (reuse `decode_throttled` from ticket 03), fix: "better PSU / cooling" |
| memory controller missing | fail; fix: add `cgroup_enable=memory` to `/boot/firmware/cmdline.txt`, reboot |
| bwrap missing | fail; fix: `sudo apt install bubblewrap` |
| PortAudio missing | fail; fix: `sudo apt install libportaudio2` |
| configured `audio.input_device` not in `arecord -l` | fail listing the cards found |
| default route over `wlan*` | warn: "use Ethernet for benchmark runs" (review §3) |
| an API key env var unset | fail naming the variable; **never print values** |
| `uname -m` ≠ `aarch64` on a Pi | fail: 64-bit OS required |
| `--json` | machine-readable output (used by `bench.run` meta in future) |

### C — Contracts
```python
# src/voice_agent/preflight.py
@dataclass(frozen=True)
class Check:
    name: str
    status: Literal["pass", "warn", "fail", "skip"]
    detail: str
    fix: str | None

def run_checks(config: AppConfig, *, root: Path = Path("/"),
               run_cmd: Callable[[list[str], float], tuple[int, str]] = ..., env: Mapping[str, str] | None = None) -> list[Check]: ...
def main(argv: Sequence[str] | None = None) -> int: ...   # exit 1 if any fail
```
Checks (names fixed, in this order): `arch`, `os_release`, `python`, `throttled`, `temperature`, `memory_cgroup`, `bwrap`, `sandbox_layout` (reports `/lib64` and merged-usr, from ticket 08 `detect_layout`), `portaudio`, `input_device`, `output_device`, `network_route`, `alsa_period` (warn only: reads `hw_params` for the configured devices while open, warns if `period_size` ≠ 320 at 16 kHz; `skip` when no stream is open), `secrets`, `home_dashboard` (HTTP GET `/api/state`, 1 s timeout, warn only), `disk_free` (≥ 2 GB).

`deploy/systemd/voice-agent.service`: `WorkingDirectory=%h/ME3`, `ExecStartPre` runs preflight, `ExecStart` runs `.venv/bin/python -m voice_agent`, `Restart=on-failure`, `RestartSec=3`, `EnvironmentFile=%h/ME3/.env`. `voice-agent-sites.service`: `python -m http.server <sites_port> --directory <sites_root> --bind 0.0.0.0`, read-only by construction (http.server only serves GET/HEAD), `NoNewPrivileges=yes`. `voice-agent-health.service`: runs the ticket-03 sampler against the agent's main PID (`--pid` via `systemctl --user show -p MainPID voice-agent`, resolved in a small wrapper in `install.sh`'s generated script). `install.sh` copies units to `~/.config/systemd/user/`, prints the `loginctl enable-linger` and `systemctl --user enable --now` commands **for the human to run** (does not run them).

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | Debian 13 `/etc/os-release` has `VERSION_ID="13"` | Debian docs; fixture uses that |
| A2 | `arecord -l` output format `card N: <id> [<name>], device M: ...` | ME2 `RASPBERRY-PI.md` + `man arecord`; parse defensively |
| A3 | `systemd-analyze verify` can lint user units offline | try on dev host; if unavailable, P4 does a structural parse instead (note which) |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_checks_on_fake_pi_all_pass` — fake root + fake `run_cmd` for a healthy Pi → every check `pass` (or `warn` for `home_dashboard`) | `uv run pytest -q tests/test_preflight.py` |
| P2 | `test_each_failure_has_fix` — table: `alsa_period` with `period_size: 1024` → `warn`; throttled `0x50005`, no memory controller, no bwrap, no PortAudio, missing device, wlan route, missing secret → correct status + non-empty fix; secret values never appear in output | same |
| P3 | `test_non_pi_host_skips` — real dev-host root: no `fail` caused by Pi-only checks | same |
| P4 | `test_units_lint` — `systemd-analyze verify` (or structural parse per A3) on all three units; the sites unit serves only `sites_root` | `uv run pytest -q tests/test_deploy_units.py` |
| P5 | `test_install_sh_dry` — `bash -n deploy/install.sh` and a run with `HOME=<tmp>` copies units and only *prints* linger/enable commands | same |

## Unknowns
None

## Dependencies/license
None new.

## Acceptance Criteria
- [ ] `python -m voice_agent.preflight` runs on the dev host without failures caused by Pi-only checks
- [ ] `docs/PI-SETUP.md` has the "Pi 4 hardware rules" section and covers a fresh Pi → running agent, including the AEC device as default capture/playback and running the sandbox `pi` tests (ticket 08 P6)
- [ ] All Proof rows green; gate suite green

## Non-Goals
- Running anything on a real Pi; enabling services; `sudo`
- Cold-boot verification (human; ME2 never verified it either — say so in the runbook)

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/MCP-SERVERS.md` (sandbox prerequisites), `docs/BENCHMARK.md`

## Execution Log
