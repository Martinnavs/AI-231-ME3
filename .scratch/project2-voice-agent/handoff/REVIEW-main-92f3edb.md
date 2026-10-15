# Tech-lead review: `main` @ 92f3edb (tickets 01, 02, 03, 04, 06, 07, 08, 09, 13, 14)

Date: 2026-10-16. Reviewer: tech-lead (Review mode). Read-only: no source edits, no merges, no paid/authenticated calls.

## Verdict

**CHANGES REQUESTED.** There is 1 blocker (R1) and 13 should-fix findings (R2). 18 notes (R3) don't block.

The gates are green, but green here does not mean safe. One cross-ticket seam (08 to 09) means the code subagent can never run code once the real harness spawns it. Two confinement gaps in the 06/08 security fixes are reproduced below. The benchmark analysis also mis-attributes fillers and stage events on any real run. Tickets 05 and 10 can start once their human decisions (H1-H3) are made. Packages A, B and C below should land before tickets 11, 12, 15 and 17 build on this code.

Counts: **R1 blocker: 1 · R2 should-fix: 13 · R3 note: 18**. Six findings need a human decision (marked **HUMAN**).

## Gate output (real)

```
$ make check
uv run ruff check .            -> All checks passed!
uv run ruff format --check .   -> 52 files already formatted
uv run mypy                    -> Success: no issues found in 25 source files
$ uv run pytest -q -rs
177 passed, 6 skipped, 1 warning in 92.63s
SKIPPED [1] tests/mcp_servers/test_home_server.py:270: ME2 app or dependencies not importable: No module named 'fastapi'
SKIPPED [5] tests/sandbox/test_run.py: set VOICE_AGENT_PI=1 (needs a Raspberry Pi)
warning: PytestDeprecationWarning asyncio_default_fixture_loop_scope unset (inside the pytester sub-run of test_live_marker_skipped_by_default; harmless)
```
`git status` was clean before and after the review. A temp directory my first repro script created in the repo root was removed. Every later repro ran in the session scratchpad.

## Reproductions run during this review (scratch only, no source edits)

1. **Code server under the MCP default env:** `env -i PATH=… HOME=… USER=… LOGNAME=… SHELL=… TERM=… systemd-run --user --scope --quiet true` prints `Failed to connect to bus: No medium found` and exits rc=1. With the normal env it exits rc=0 (dev host, systemd 255). The env-less case is the code server's env when `McpClientManager` spawns it (see R1).
2. **Media mask with a symlinked home:** I set `code._state.root=<symlinked-home>/agent_sandbox` and `media_root=<same>/media`. `code._media_rel()` returned `None`, so nothing was masked. The same layout under the real path returns `"media"`. The dev host's `/home` is itself a symlink to `/mnt/jfs_hpc/home` (see R2).
3. **Root TOCTOU in `_paths`:** I wrapped `resolve_inside` so it swaps `media/` for a symlink to `outside/` after its check, as sandboxed code could. `write_file_atomic(media, "x/pwn.wav", …)` then created `outside/x/pwn.wav` (see R3).

---

## Findings

Fields: id; severity; primary file:line / symbol; failure scenario (impact + evidence); fix; owner; human decision?; confidence.

### R1 [R1 blocker]: Code server never gets `XDG_RUNTIME_DIR`, so `run_python` is always "sandbox unavailable" under the harness
- **Where:** `src/voice_agent/sandbox/runner.py:16` (`_PASS_ENV`) and `:180` (`_scrubbed_env`). The cause sits in `src/voice_agent/agent/mcp_clients.py:122` (`env=env or None`) together with `config/agent.toml:47-49` (`[mcp_servers.code]` has no `env`).
- **Root cause:** when `env` is None, MCP's `stdio_client` starts the server with only `HOME, LOGNAME, PATH, SHELL, TERM, USER` (`mcp/client/stdio/__init__.py` `DEFAULT_INHERITED_ENV_VARS`). `_scrubbed_env` then has no `XDG_RUNTIME_DIR` or `DBUS_SESSION_BUS_ADDRESS` to pass on, so `systemd-run --user` can't reach the user manager.
- **Failure scenario:** on the Pi, the agent spawns the code server through `McpClientManager` with the shipped config. Every `code__run_python` call then fails: stderr starts with "Failed to connect to", so `run_in_sandbox` raises `SandboxUnavailable`. The professor's "random complex task" (build a website) can't run any code. Ticket 09's P1 only listed the code server's tools and never called `run_python`, and ticket 08's tests call the runner in-process with the developer's full env, so nothing caught it.
- **Evidence:** reproduction 1 above.
- **Fix:** in `runner._scrubbed_env`, when `XDG_RUNTIME_DIR` is unset, fall back to `/run/user/<uid>` if that directory exists and is owned by the uid. Also set `DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus` when that socket exists. Add a unit test that runs with an empty `os.environ` and asserts the env given to `create_subprocess_exec`. Optionally add `env = { XDG_RUNTIME_DIR = "${XDG_RUNTIME_DIR}" }` to `[mcp_servers.code]`, but don't rely on it: `_expand_env` marks the server unavailable when the var is unset (for example, under a system service).
- **Owner:** developer (package A). The cross-cutting proof is test-engineer's (package G). **Confidence:** high (reproduced on systemd 255; Debian 13's 257 behaves the same as far as I know; confirm on the Pi).

### R2 [R2, security]: Media mask fails open when the path to `sandbox_root` crosses a symlink
- **Where:** `src/voice_agent/mcp_servers/code.py:133-141` (`_media_rel`) and `:155-157` (the symlink check only runs when `hidden` is set).
- **Root cause:** `_media_rel` compares `os.path.abspath(media)` (symlinks not resolved) against `_state.root.resolve()` (symlinks resolved). When any ancestor is a symlink, `relative_to` raises, the function returns `None`, no `--tmpfs` mask is added, and the symlink refusal is skipped. `_overlaps_media` resolves both sides, so the two checks disagree.
- **Failure scenario:** on any host where `~` (or another ancestor) is a symlink (this dev host is one), sandboxed code sees `/sandbox/media` read-write. It can replace `media` with a symlink, or flip it back and forth while the speech server works (R3). The 08 M1 fix becomes a no-op without any warning.
- **Evidence:** reproduction 2.
- **Fix:** resolve both sides (`media.resolve(strict=False)` relative to `root.resolve()`), and fail closed. If the media path is inside the root by either the lexical or the resolved comparison, mask it. Refuse to run if any component of the hidden path is a symlink, checking every component, not just the last. Add a test with a symlinked ancestor.
- **Owner:** developer (package A). **Confidence:** high.

### R3 [R2, security]: Root-level TOCTOU in `_paths`: the root is checked as "not a symlink" and then re-resolved by path
- **Where:** `src/voice_agent/mcp_servers/_paths.py:24-26` (`root.is_symlink()` then `root.resolve()`), `:57-58` (`_open_parent` re-resolves the root and opens it by path), `:84` (`open_dir`), and `:45-50` (`_check_fd_inside` compares against that same re-resolved root, so it can't catch the swap).
- **Failure scenario:** `media_root` sits inside the sandbox (R14). When the mask is missing (R2) or `media_root` is configured deeper, sandboxed code can swap `media` for a symlink between the check and the open. The speech server then writes `.wav` files, and creates any missing parent directories, wherever the link points. It can also read any WAV-format file there. The subagent can run `code__run_python` and `speech__*` concurrently (ticket 10: parallel tool calls), and the speech server is a separate process outside the code server's lock.
- **Evidence:** reproduction 3 created `outside/x/pwn.wav`.
- **Fix:** open the configured root once per call with `os.open(str(root), O_DIRECTORY|O_NOFOLLOW)` (no `resolve()`), and walk from that fd. Record the root's `(st_dev, st_ino)` at server start and compare it with `fstat` of the opened root fd. Then `_check_fd_inside` no longer needs to re-resolve. Drop the separate `is_symlink()` pre-check. Add a test that swaps the root, mirroring reproduction 3.
- **Owner:** developer (package B). **Confidence:** high.

### R4 [R2, security / secret leak]: Speech server forwards raw backend exception text to the LLM, and its `SpeechBackendError` fallback catches nothing
- **Where:** `src/voice_agent/mcp_servers/speech.py:29-38` (fallback class), `:79`, `:86` (backend construction outside any try), and `:144`, `:168`, `:186` (only `SpeechBackendError` is caught). FastMCP passes the text on (`mcp/server/fastmcp/tools/base.py:116-117`: `ToolError(f"Error executing tool {name}: {e}")`).
- **Failure scenario:** until ticket 05 lands, `SpeechBackendError` is a local class nobody raises. After 05, `httpx`/`websockets` exceptions, `ConfigError` and `TypeError` still escape. Their text can include request URLs (Cartesia's WS API accepts `?api_key=` in the query), response bodies or absolute paths. All of it goes into the model context and the stderr logs. Ticket 05's R4-7 test only covers `SpeechBackendError` messages.
- **Fix:** wrap backend construction and every backend call in `except Exception`. Return a fixed tool error (`"speech backend error: <ExceptionType>"`, plus the status code when it is a `SpeechBackendError`). Log the detail to stderr after redacting any configured secret value. Add a test where a fake backend raises an exception whose message contains a fake key and a URL, and assert neither reaches the tool result. Also add to ticket 05: authenticate with headers, never a query-string key.
- **Owner:** developer (package B). The ticket 05 note is for the human/orchestrator. **Confidence:** high.

### R5 [R2, security / resource]: Disk and inode limits only apply after the run, and the post-run scan itself can break `run_python`
- **Where:** `src/voice_agent/sandbox/runner.py:213-225` (`_dir_usage`), `:295`; `src/voice_agent/mcp_servers/code.py:149-171` (no check before the run).
- **Failure scenarios:**
  - (a) A 120 s script can write GBs or millions of inodes to the SD card before the check runs, filling the Pi's root filesystem and taking down the agent, logs and the other services.
  - (b) The scan isn't checked before the next run, so an over-limit folder just keeps running.
  - (c) Sandboxed code can `chmod 000` a subdirectory. `os.scandir` then raises `PermissionError` with an absolute host path. The run result (stdout) is lost and the host path leaks to the LLM.
  - (d) The scan has no cap on entry count, so a million-file tree blocks the code server while it holds the lock.
- **Fix (worker part):**
  - Refuse to start a run when the folder is already over `max_dir_bytes`.
  - Make `_dir_usage` tolerant: catch `OSError` per entry, count unreadable entries as over the limit, cap the number of entries, and never put an absolute path in the message.
  - Add `prlimit --fsize=<max_dir_bytes>` or the bwrap equivalent so no single file can exceed the cap.
  - Add an entry-count cap.
- **HUMAN (H5):** a real hard cap needs a filesystem-level limit for `sandbox_root` on the Pi (a size-limited loop-mounted image, or a quota). That is a ticket 16 deploy decision.
- **Owner:** developer (package A). **Confidence:** high (by code reading; (c) follows directly from `scandir` semantics).

### R6 [R2, HUMAN]: Sandbox isolation and the L3 detection logic have never run on real bwrap
- **Where:** `tests/sandbox/test_run.py` (5 tests skipped); `runner.py:244-294`.
- **Failure scenario:** each of these is unverified on a host with bwrap:
  - bwrap starts with the generated bind list;
  - `--info-fd` survives `systemd-run --scope` and `timeout`. If it doesn't, `started` is always false: a real systemd failure is reported as a normal result, and `timed_out` falls back to wall-clock only;
  - the `--tmpfs` mask actually hides `media`;
  - `MemorySwapMax=0` and `TasksMax=64` take effect on a user scope;
  - `user@UID.service/memory.events` counts the scope's OOM kills;
  - no network, no `$HOME`, writes denied outside `/sandbox`.
  
  Every security claim in 08 depends on these.
- **Fix:** human checkpoint 5 on the Pi (`uv run python -m voice_agent.preflight` once ticket 16 lands, then `VOICE_AGENT_PI=1 uv run pytest -q tests/sandbox/test_run.py`). Add P6 cases for the media mask and for `started=True`. Record the output in ticket 08.
- **Owner:** human; test-engineer writes the extra P6 cases (package G). **Confidence:** n/a (verification gap).

### R7 [R2, HUMAN, contract drift for 05/11]: Speech server builds real backends with the wrong kwargs
- **Where:** `src/voice_agent/mcp_servers/speech.py:65-73` (`_backend_kwargs` passes only `api_key`), `:76-86`. The other side is ticket 05's contract: `CartesiaSynthesizer(*, api_key, voice, model=None, sample_rate=16000, …)`, where `voice` is keyword-only and **required**; `DeepgramTranscriber(*, api_key, model=None, language="en", …)`.
- **Failure scenario:** once 05 lands, the first `synthesize_speech_to_file` or `get_tts_voices` call raises `TypeError: missing required keyword-only argument 'voice'`, and the raw text goes to the LLM (R4). `AgentConfig.tts_voice` and `AudioConfig.sample_rate_out` are ignored. Ticket 11 has to build the same backends, so the two will drift unless they share one factory.
- **HUMAN (H3):** decide where per-backend constructor kwargs are assembled. Recommendation: one shared factory, `voice_agent.speech_backends.create_{transcriber,synthesizer}(cfg: AppConfig, name)`, owned by ticket 05 and used by both 06 and 11. Also give `voice` a default of `None` in ticket 05's Cartesia contract. Amend tickets 05 (Files/Contracts), 06 (speech.py switches to the factory) and 11.
- **Owner:** human decision, then developer (ticket 05, plus a two-line change in speech.py). **Confidence:** high.

### R8 [R2, HUMAN, contract]: Stage-log turns are mapped to truth trials by index, and the ticket's time-based match isn't implemented (and can't be, as specified)
- **Where:** `src/bench/analyze.py:377-381` and `:463` (`turn_events_map.get(idx, [])`, where `idx` is the 1-based trial index); `docs/EVENT-LOG.md` (`turn_id` semantics are undefined); ticket 14's edge "match by endpoint wall time nearest after T0 within 3 s"; ticket 15's `meta.json` (only a coarse UTC start, never passed to `analyze`).
- **Failure scenario:** in a real rig run the pipeline's `turn_id` (ticket 11) counts every turn it opens: barge-in utterances, false interruptions from noise trials, and two turns for a pause takeover. So turn N ≠ trial N. Filler correction (`first_answer_audio`, the **graded** latency) and the stage breakdown get attached to the wrong trials, silently. `analyze()` has no recording-start wall time, so the edge as written can't be built.
- **HUMAN (H2):** pick one:
  - (a) `analyze(…, rec_start_wall: float | None)`, filled from rig `meta.json`, matching `endpoint.t_wall` to `T0_wall` ± 3 s. This needs NTP-synced Pi and rig clocks, which ticket 15's runbook would check.
  - (b) Match by order inside each trial's `[t_start, window_end)`, using the stage log's own `vad_speech_start` events against stimulus onsets.
  
  Recommendation: (a), with a drift guard. Define `turn_id` in `EVENT-LOG.md` either way. This changes the contracts of 14 (`analyze` signature), 15 (meta → analyze) and 02's doc.
- **Owner:** human decision, then developer (package C2). **Confidence:** high.

### R9 [R2]: Filler handling: only the first filler per turn is used, and the clip's sample rate is wrong
- **Where:** `src/bench/analyze.py:464-504`. `next(...)` takes the first `FILLER_PLAYED` (line 466). `filler_dur_s = len(clip_mono) / rec_sr` uses the recording's rate and throws away the clip's own rate (around line 473-477); the correlation template isn't resampled either.
- **Failure scenarios:**
  - (a) Ticket 12 can play `stall` at 700 ms and then `notice` at 2500 ms in the same turn. The onset after the *first* filler is then the notice filler, which gets counted as the answer. That breaks the stated rule "a stall/notice filler never counts as the answer", which `docs/BENCHMARK.md:87` claims holds.
  - (b) A 16 kHz filler clip scored against a 48 kHz rig recording gets 1/3 of its real duration and a mis-shaped correlation template, so `first_answer_audio` comes out too early.
  
  P5 only covers a single filler with `clip=None`.
- **Fix:** collect every `FILLER_PLAYED` for the turn in time order. Chain the search so each filler is located after the previous one's end. Take the first onset after the last filler that precedes the answer. Read the clip's rate and resample with `audio_engine.wav.resample` before measuring duration and correlating. Add tests: two fillers (stall + notice), and a clip file at 16 kHz in a 48 kHz recording, both asserting ±15 ms.
- **Owner:** developer (package C). **Confidence:** high.

### R10 [R2]: Stage breakdown mixes every occurrence of a stage into one distribution
- **Where:** `src/bench/analyze.py:556-566`.
- **Failure scenario:** a turn has several `tts_request`, `tts_first_chunk`, `tool_call` and `stt_interim` events. All of them get appended to `endpoint->X`, so the median mixes first and later sentences. That breakdown is how the write-up will split the ~500 ms budget across stages, so the error would go straight into the write-up.
- **Fix:** per turn, take the **first** occurrence of each stage after the turn's first `endpoint`. Test with a turn that has three `tts_request` events.
- **Owner:** developer (package C). **Confidence:** high.

### R11 [R2, Principle 10, HUMAN fixture]: The T0 boundary (D2) is only proven on pure tones and synthetic noise
- **Where:** `tests/bench/test_signal.py:52-69`; `src/bench/signal.py:36-51`.
- **Failure scenario:** the whole benchmark's clock starts at `speech_bounds` end. Real TTS has breath, soft plosives and low-level tails. The ±10 ms claim is asserted only on tones plus Gaussian "fricatives", and A1 was left to a human spot-check that is never re-run. The next tweak to `rel_db` or the refinement step has no real-data guard.
- **Fix:** add one or two small real speech clips (≤ 3 s, license-clean, e.g. CC0 or CC-BY with attribution in `tests/fixtures/speech/README`) with hand-marked word boundaries. Assert `|end - true_end| ≤ 20 ms` (or the tightest value the marks support) and the same for start.
- **HUMAN (H4):** supply or approve the clips and their marked boundaries.
- **Owner:** test-engineer (package F). **Confidence:** high.

### R12 [R2, Principle 10]: `calibrate` is never checked against its own claim
- **Where:** `tests/bench/test_analyze.py:357-375` asserts only `safe_margin > 0.0`; `src/bench/analyze.py:590-609`.
- **Failure scenario:** `calibrate` sets the real hardware margin (14 A2). The test would pass even if `calibrate` returned 0.1 or 99. Nothing shows the margin it returns gives zero false onsets on the bot-disabled recording, or that a smaller margin wouldn't.
- **Fix:** on a synthetic recording with the bot disabled (noise ±2.5 dB plus crosstalk), assert that `analyze(margin_db=m)` gives no `ok` or `crosstalk` onsets, that `analyze(margin_db=m-1.0)` gives at least one false onset, and that `m` lies within a numeric bound of the fixture's known peak sustained level.
- **Owner:** test-engineer (package F, new file `tests/bench/test_calibrate.py`). **Confidence:** high.

### R13 [R2, docs]: `docs/MCP-SERVERS.md` contradicts the hardened 06/08 behavior
- **Where:**
  - `docs/MCP-SERVERS.md:14`: says every server returns "path outside media root"; code returns "sandbox root".
  - `:12`: doesn't mention that a symlinked root, or a symlinked intermediate dir inside the root, is refused, or the fd-based helpers `open_regular_for_read` / `write_file_atomic` / `open_dir`.
  - `:32-33`: missing the speech input limits (WAV/WAVEX only, ≤2 ch, ≤48 kHz, 64 MB file and decoded caps), `.wav`-only output, and validation of `--max-audio-s`.
  - `:53`: says only `run_python` takes the lock; every tool does now.
  - `:54-56`: missing the `publish_site` caps (≤100 dirs, depth ≤8), skipped dot entries, root/media rejection, and the "over the limit" error.
  - `:62`: the command lacks `TasksMax=64`, `MemorySwapMax=0`, the `--tmpfs` media mask, `--info-fd`, `--clearenv` and `--setenv`.
  - `:66`: the `oom_killed` and `timed_out` derivation is out of date.
  - `:67`: the env description is wrong. The child gets only `PATH=/usr/bin:/bin`, `HOME=/sandbox` and `LANG=C.UTF-8`; the listed vars go only to systemd-run. This also needs R1's XDG note.
  - `:74`: the memory-controller check is now `user@UID.service/cgroup.subtree_control` (with a `user.slice` fallback).
  - L2 (with `network=True`, host loopback and abstract sockets are reachable) isn't documented.
- **Failure scenario:** ticket 16 (preflight, runbook), ticket 17's E2E test ("every tool in docs/MCP-SERVERS.md exists") and the human Pi checkpoint would all work from a wrong security model.
- **Fix:** rewrite the path-confinement, speech, code and sandbox sections to match the code after packages A and B land.
- **Owner:** document-writer (package E). **Confidence:** high.

### R14 [R2, HUMAN, security design]: `media_root` default sits inside `sandbox_root` (parked since the 06 audit)
- **Where:** `src/voice_agent/config.py:66-67`, `config/agent.toml:32-33` (ticket 01's contract).
- **Failure scenario:** this overlap is the root of R2 and R3 and of the masking machinery in 08. It also lets the code tools (`write_file`, `read_file`, `list_files`) reach into `media/`, which goes against the masking intent. One example: `write_file("media", …)` when `media/` doesn't exist yet creates a regular *file*. bwrap then can't mount the tmpfs over it, so every later `run_python` fails, and the speech server breaks too.
- **HUMAN (H1):** move the default to a sibling (for example `~/agent_media`), or keep it and accept the mask as the only defense. If code needs speech output, the subagent can pass it explicitly (`read_file` / `write_file` don't apply to binary data anyway). If the default moves, also make `code.py` reject any path overlapping `media_root`.
- **Owner:** human decision, then developer (ticket 01 files: `config.py`, `agent.toml`, `tests/test_config.py`). **Confidence:** high.

### R15 [R3]: Tool-call timeout can return the wrong text
- **Where:** `mcp_clients.py:160` and `:214-218`.
- `read_timeout_seconds` equals the `wait_for` timeout. When the MCP read timeout fires first, the outcome is `"McpError: Timed out while waiting…"`, not the contract's `"timed out"`. `test_call_timeout` uses `timeout_s=0.0`, which never hits this race.
- **Fix:** map an `McpError` with code `REQUEST_TIMEOUT` to `"timed out"`, or set the read timeout to `timeout_s + 1`.
- Package D.

### R16 [R3, for ticket 10]: Tool output reaches the model uncapped
- `list_files` has no cap (L4), `read_file` returns up to 256 KiB, `run_python` up to 2×64 KiB, and home errors embed `res.text`.
- One call can add ~64k tokens to the LLM context.
- **Fix:** cap the text in `ToolRegistry._render` (e.g. 16 KiB with a truncation marker), or settle it in ticket 10. Cap the `list_files` entry count.
- Packages D and A.

### R17 [R3]: A cancelled `run_python` releases the lock before the sandbox is dead
- **Where:** `runner.py:273-276`.
- The cancel path calls `_kill_group` and re-raises without awaiting `proc.wait()`, so the next tool can run while bwrap is still dying.
- **Fix:** shield a short `await proc.wait()` after the kill.
- Package A.

### R18 [R3]: Forgeability residue in `timed_out` and `oom_killed`
- A child can exit 124 at about `timeout_s - ε` and pass the `duration >= timeout_s` test.
- `oom_killed` falls back to the exit code alone when `memory.events` is unreadable (QUEUE follow-up).
- `memory.events` at the `user@` level also counts OOM kills in sibling units.
- The impact is informational only (it misleads the model).
- **Fix:** read the scope's own `memory.events` if the scope name can be set (`systemd-run --unit=`), and require `duration >= timeout_s + 0.5` for the 124 case.
- Package A, optional.

### R19 [R3]: bwrap hardening left over from the audit
- `--disable-userns` (L9; bwrap ≥ 0.8; Debian 13 ships 0.11) isn't set, so nested user namespaces are allowed.
- Documenting L2 is package E.
- Package A, optional; add a preflight version check.

### R20 [R3]: `publish_site` copies synchronously on the event loop
- **Where:** `code.py:288-289`.
- Up to 20 MiB is copied on the event loop, and `_copy_tree` loads the whole directory listing, dot entries included, into memory before filtering.
- **Fix:** run `asyncio.to_thread`, and filter and cap entries while iterating.
- Package A, optional.

### R21 [R3]: `resolve_inside` now refuses a symlinked root
- **Where:** `_paths.py:24`.
- This is correct, but it surprises users. A Pi whose `~/agent_sandbox/media` or (ticket 19) `~/Music` is a symlink to a USB drive gets "path outside media root" on every call.
- **Fix:** document it, and have the ticket 16/19 preflight report it.
- Package E plus notes on tickets 16 and 19.

### R22 [R3]: Edge cases in `events.py`
- `close()` uses a blocking `queue.put(None)`, so it hangs if the queue is full and the writer is stalled.
- `json.dumps(record, default=str)` still raises in the hot path for non-str dict keys or circular data, despite the "never raises" edge.
- **Fix:** `put` with a timeout, and wrap `dumps` in try/except that counts the event as dropped.
- Package H, optional.

### R23 [R3]: Analysis robustness
- The noise floor can come out at -120 dBFS on a digitally silent lead-in, which puts the threshold at -108 dBFS.
- `_check_crosstalk` uses a single 1 ms frame maximum, while onsets need 30 ms of hold.
- The drift correlation has no peak-quality check.
- Health `throttled` treats the since-boot flags as "throttled during the run".
- **Fix:** clamp the floor to ≥ -90 dBFS, require hold for crosstalk, add a peak threshold for drift, and report now-flags separately from since-boot flags.
- Package C, optional.

### R24 [R3]: Analysis metric gaps
- `barge_in_duck` stats mix noise-trial ducks with barge-in ducks.
- `filler_rate` per kind (14's Action step 5) isn't reported (QUEUE note).
- Package C.

### R25 [R3]: `speech_bounds(hangover_ms=…)` is a contract parameter that does nothing
- **Where:** `signal.py:39-40` (QUEUE note).
- **Fix:** either honor it (extend the active region by the hangover before the sample refinement) or remove it in a ticket 13 contract revision.
- Human or tech-lead call; low priority.

### R26 [R3]: Home server
- Non-JSON or non-dict responses fail with an empty `AssertionError` or a raw `JSONDecodeError` (`home.py:103-104`, `:139-140`).
- Error texts embed the unbounded `res.text`.
- P4 (the real ME2 app) has never run: fastapi isn't a dependency. A human can run it once in a throwaway venv.
- Package outside this list (home.py and its tests); optional.

### R27 [R3]: `config._build` checks no types
- **Where:** `config.py:104-119`.
- `sites_port = "8080"` loads silently. Path and tuple coercion relies on the substrings "Path" and "tuple" in the annotation string, which is fragile for the fields tickets 12, 18 and 19 add (`Literal[...]` provider and so on).
- Tickets 12, 18 and 19 should add validation when they touch `config.py`.

### R28 [R3]: Servers load config relative to the working directory
- Each server's `load_config()` defaults to the cwd-relative `config/agent.toml`, and a `ConfigError` silently falls back to defaults.
- Under a different cwd the speech server gets no API keys, and the code server reports the wrong `sites_host`.
- **Fix:** set `cwd` in `StdioServerParameters`, or pass `--config <abs>` from the manager.
- Ticket 16 or 17 should cover it.

### R29 [R3, docs / contract drift]: Docs disagree with upcoming tickets
- `docs/AUDIO-ENGINE.md` "Adding a backend" says `src/audio_engine/backends/<name>.py` and register in `__init__.py`. Ticket 05's Files say `src/audio_engine/{deepgram,cartesia}.py` and register in `registry.py`. Reconcile before 05 is dispatched.
- `docs/EVENT-LOG.md` doesn't define `turn_id` (see R8) or say that a second open raises `FileExistsError`.
- Package E.

### R30 [R3]: QUEUE carry-overs
- Once 05 lands, confirm `speech.py` picks up the real `SpeechBackendError` (R4 makes this moot).
- `mcp>=1.2` floor is loose while the lock pins 1.30.0 (R5-9); raise it to `>=1.30,<2`.
- `voice` has no length cap (R5-8).
- Package B (voice cap) plus `pyproject.toml` (human or orchestrator).

### R31 [R3, tests that prove little]
- Two 08 tests passed on the code from before the fix and have no regression value (the FIFO-swap read test, the staging-private test). Keep them but label them.
- `test_call_timeout` uses `timeout_s=0.0` (see R15).
- `test_filler_then_answer` coincidentally has turn_id = trial index = 1, so it can't detect R8.

### R32 [R3, process]: DESIGN.md says execution is "strictly sequential"
- The overnight queue ran tickets in parallel. I checked the plan-1 diagram: every producer was merged before its consumer was dispatched (02, 13 and 03 before 14; 06 before 08; 06, 07 and 08 before 09). So there is no edge violation, but the status text is stale.

---

## Re-audit summary (06/08 security fixes)

- **TOCTOU:** the per-component `O_NOFOLLOW` walk, `O_EXCL` temp file and `os.replace`/`os.link`, and the `fstat` regular-file check close the original R2-1/H1/H2 races *below* the root. The root itself is still racy (R3). The `publish_site` fd walk is sound.
- **Symlinks:** intermediate and leaf symlinks are refused. The root symlink check is racy (R3). The mask fails open when an ancestor is a symlink (R2).
- **Resource limits:** memory, CPU, `TasksMax`, swap and the 64 KiB output caps are in the argv but unverified on the Pi (R6). There is no hard disk or inode limit, and the usage scan is fragile (R5).
- **Env leaks:** none found. MCP's default env plus `--clearenv` keep API keys out of the servers' and scripts' environments, and `.env` is outside `sandbox_root`. The same env scrubbing is what causes R1.
- **Secret leaks:** `Secrets.__repr__` masks values, and `_expand_env` never logs values. The open path is raw backend exception text through FastMCP (R4).

## Contract-drift check for unmerged tickets

- **05:** constructor kwargs (R7), backend file layout (R29), header auth and error mapping (R4).
- **10:** `ToolRegistry`/`ToolOutcome`/`subset`/`anthropic_tools` match. Output caps are still open (R16). The subagent's 180 s deadline vs the 130 s `run_python` timeout is fine.
- **11:** `StageLogger`, `Stage`, `FakeTranscriber` (`eot_silence_ms`) and `FakeSynthesizer` match. It must share the backend factory (R7) and define `turn_id` (R8).
- **12:** `FILLER_PLAYED.kind` plus two fillers per turn conflicts with analyze (R9). `TurnConfig` additions are fine.
- **14 ↔ 15:** analyze can't match turns without a wall-clock anchor (R8).
- **16:** must reuse `runner.preflight()` (the controller path changed), set up the disk cap (R5 / H5), mind cwd (R28), and allow for root-symlink refusal (R21).
- **17:** its E2E plan will hit R1 the moment it calls `run_python` through the shipped config, which is good: keep that test.
- **18:** config validation (R27).
- **19:** `music_root` symlink refusal (R21); adding `MUSIC_*` stages is fine.
- **20:** `state_dir` usage is consistent with home.

---

## FIX WORK LIST

Each package has disjoint files and is sized for one worker. **Security-sensitive** packages also need a `security-auditor` re-check after they land. An arrow means producer before consumer (Operating Principle 11).

| Pkg | Owner | Files | Covers | Security | Depends on |
|---|---|---|---|---|---|
| **B** Path confinement + speech | developer | `src/voice_agent/mcp_servers/_paths.py`, `src/voice_agent/mcp_servers/speech.py`, `tests/mcp_servers/test_paths.py`, `tests/mcp_servers/test_speech_server.py` | R3, R4, R30 (voice cap); R7 only after H3 | **yes** | none (first wave) |
| **A** Sandbox runtime + code server | developer | `src/voice_agent/sandbox/runner.py`, `src/voice_agent/mcp_servers/code.py`, `tests/sandbox/test_command.py`, `tests/mcp_servers/test_code_server.py` | **R1**, R2, R5 (worker part), R17; optional R16 (list cap), R18, R19, R20 | **yes** | **B → A** (A's code and tests use `_paths`). They could run in parallel only if B keeps every `_paths` public signature and the merge re-runs the full gate; default sequential |
| **C** Bench analysis | developer | `src/bench/analyze.py`, `src/bench/report.py`, `tests/bench/test_analyze.py`, `tests/bench/synth_recording.py`, `docs/BENCHMARK.md` (Analysis section) | R9, R10, R24; optional R23 | no | none (first wave) |
| **C2** Turn↔trial matching | developer | same files as C | R8 | no | **HUMAN H2**, then **C → C2** (same files). Alternatively fold into C if H2 is decided before C starts |
| **D** MCP client manager | developer | `src/voice_agent/agent/mcp_clients.py`, `tests/agent/test_mcp_clients.py` | R15, R16 (render cap) | no | none (first wave) |
| **H** Event log hardening (optional) | developer | `src/voice_agent/telemetry/events.py`, `tests/telemetry/test_events.py` | R22 | no | none (first wave) |
| **F** Principle-10 proofs | test-engineer | `tests/bench/test_signal.py`, `tests/bench/test_calibrate.py` (new), `tests/fixtures/speech/*` (new) | R11, R12 | no | **C → F** (calibrate's floor may change under R23). R11 waits on **HUMAN H4** (fixture) |
| **G** Harness E2E for code tools | test-engineer | `tests/agent/test_code_via_manager.py` (new), extra P6 cases in a new `tests/sandbox/test_run_mask.py` | R1 cross-cutting proof (spawn code server via `McpClientManager` with an empty env and stub `systemd-run`/`bwrap` scripts on `PATH`; assert the stub saw `XDG_RUNTIME_DIR`), R6 test additions | yes (tests only) | **A → G** |
| **E** Docs | document-writer | `docs/MCP-SERVERS.md`, `docs/AUDIO-ENGINE.md`, `docs/EVENT-LOG.md` | R13, R21 (doc), R29, L2 note | no | **A, B → E**; the `turn_id` part waits on H2 |

**Waves:** wave 1 = {B, C, D, H} in parallel (independent: disjoint files, no data flow between them). Wave 2 = {A (after B), F (after C)}. Wave 3 = {G (after A), E (after A+B), C2 (after H2)}.

### Needs a human (not a worker)

| ID | Decision | Unblocks |
|---|---|---|
| H1 | R14: move `media_root` out of `sandbox_root` (ticket 01 contract) or accept masking as the only defense | ticket 01 config change; simplifies A |
| H2 | R8: how stage-log turns match trials (`rec_start_wall` + NTP, or order-based); define `turn_id`; amend tickets 14, 15 and 02's doc | C2, ticket 15 |
| H3 | R7: one speech-backend factory from `AppConfig` (owned by 05, used by 06 and 11); give Cartesia's `voice` a default of `None` | ticket 05 dispatch, the R7 part of B |
| H4 | R11: supply or approve 1-2 license-clean real speech clips with marked boundaries | the R11 part of F |
| H5 | R5: hard disk/inode cap for `sandbox_root` on the Pi (loop image or quota; ticket 16) | ticket 16 |
| H6 | R6: run Pi checkpoint 5 (`VOICE_AGENT_PI=1` P6) after A lands; confirm `--info-fd`, the mask, swap/tasks limits, OOM detection | trusting the sandbox at evaluation |
| (D1) | still unconfirmed; tickets 05 and 10 stay BLOCKED regardless of this review | 05, 10 |

Review-finding handling: R1/R2 retries need human approval before dispatch, and are capped at 2 attempts per package. R3 notes are not retry work.
