# 17: Test-engineer pass — coverage audit, end-to-end and regression tests

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 01–16, 18, 19, 20 (all developer tickets)
**Status:** ready-for-agent
**Agent:** `test-engineer` (not `developer`)

## Files
- `tests/e2e/test_voice_agent_e2e.py`, `tests/e2e/test_bench_e2e.py`, `tests/e2e/test_mcp_e2e.py`
- any existing test file where the audit finds a gap (additive changes only)

## Action
Independent audit of tickets 01–16 and 18–20's own tests, then the cross-cutting tests no single ticket owns. **Every end-to-end test calls the real entry points** — `voice_agent.__main__.main`, `bench.truth.main`, `bench.run.main`, `bench.analyze.main`, the MCP servers as spawned processes — never a hand-reassembled call graph of library functions (rules/agents.md, Shared Context Files).

1. **Audit:** for each ticket, check every RECAP Edge row has a test that would fail if the behavior were removed; add the missing ones. Record a table (ticket → edges covered / added) in the Execution Log.
2. **E2E voice loop:** `main(["--fake", "--with-mcp", "--input-wav", <3-turn fixture>, ...])` where turn 2 drives `home__control_lights` against a stub dashboard (a tiny local HTTP server in the test) and turn 3 triggers `delegate` (scripted subagent using `code__write_file` + `code__run_python`; `run_python` returns "sandbox unavailable" on this host, and the agent must still answer). Assert: stage order per turn, a `filler_played` only on turn 3, a spoken announcement after turn 3, no zombie processes. Second fixture, the professor's example: "play some Taylor Swift" → media plays (fake player) → while it plays, "no, I want Opalite" → `music_ducked` during the user's speech, `now_playing` becomes Opalite, music restored after the reply → "set a timer for 3 seconds" → chime + announcement in the output WAV → "stop the music" → stopped.
3. **E2E benchmark:** `bench.truth.main` (fake TTS, 12 trials incl. 2 barge-in, 2 noise, 2 pause) → `bench.run.main --dry-run` → assert `results.json` has n = trials, no `missed` on turn trials, barge-in stop and duck latencies present, premature-takeover rate present (0 with the fake STT's end-of-turn), and first-audio p50 within the fake pipeline's analytic bounds.
4. **MCP protocol E2E:** all three servers spawned via `McpClientManager` from the **shipped** `config/agent.toml` with test overrides only for backends/paths; every tool listed in `docs/MCP-SERVERS.md` exists with the documented name.
5. **Regression guards (numeric, Operating Principle 10):** `speech_bounds` ≤ 10 ms on fixtures (ticket 13), onset recovery ≤ 10 ms (ticket 14), `emit()` < 50 µs (ticket 02), barge-in mute < 100 ms in file mode (ticket 12), duck depth 15 ± 2 dB and fade to ≤ −60 dB (ticket 12), one ONNX thread per model (ticket 11), fast-model verdict thresholds and task-suite coverage (ticket 18), pause-gap layout ≤ 10 ms (ticket 13), STT end-of-turn vs fallback timing (ticket 11 P9). Confirm each exists and asserts a threshold; add any missing.
6. Run the full suite twice (flake check); report counts, durations, skipped tests with reasons.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| a test is flaky across the two runs | fixed (deterministic seeds/timeouts) or reported as a finding to the orchestrator with the failing output — never `@pytest.mark.skip`ped silently |
| a gap is a code defect, not a missing test | stop, report it as a defect for `developer` (do not fix production code) |
| `live`/`pi` tests | listed as "human-run", not executed |

### C — Contracts
No production code. Test names above are the contract; E2E tests live under `tests/e2e/` and run in the default `pytest` invocation (< 90 s total for `tests/e2e`).

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | The shipped config can be overridden for tests without editing it (via `load_config(env=...)` or a temp copy) | read ticket 01's loader |

### P — Proof
| # | Item | Command |
|---|---|---|
| P1 | Audit table complete; every Edge row across 01–16 and 18–20 mapped to a test | Execution Log |
| P2 | `tests/e2e/*` green | `uv run pytest -q tests/e2e` |
| P3 | Full suite green twice, same counts | `uv run pytest -q` ×2 |
| P4 | Gate suite green | RUNNER.md gate commands |

## Unknowns
None

## Dependencies/license
None new.

## Acceptance Criteria
- [ ] P1–P4 done with actual command output pasted (counts) in the Execution Log
- [ ] Defects found are reported, not fixed

## Non-Goals
- Changing production code
- Running `live` or `pi` tests

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`, `DESIGN.md`
- `docs/EVENT-LOG.md`, `docs/AUDIO-ENGINE.md`, `docs/MCP-SERVERS.md`, `docs/BENCHMARK.md`

## Execution Log
