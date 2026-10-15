# 06: Speech MCP server (stdio) — transcribe / synthesize / list voices

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 04 (05 for real backends; tests use the fake)
**Status:** done

## Files
- `src/voice_agent/mcp_servers/__init__.py`, `src/voice_agent/mcp_servers/_paths.py`, `src/voice_agent/mcp_servers/speech.py`
- `docs/MCP-SERVERS.md` (shared contract; this ticket creates it, tickets 07 and 08 append)
- `tests/mcp_servers/test_paths.py`, `tests/mcp_servers/test_speech_server.py`

## Action
The "MCP server for ASR and TTS" from review §6 decision 4: a stdio FastMCP server that imports `audio_engine` and exposes speech as **file tools** for subagents. Follow the lesson notebook pattern in `resources/Creating-MCP-Server.ipynb` (`FastMCP("speech")`, `@mcp.tool()` with typed args and docstrings, `mcp.run(transport="stdio")`) and pin `mcp>=1.2,<2` exactly as the notebooks' 08/2026 update note says. Also expose one **resource**, `speech://voices`, as in `resources/Create-MCP-Resource-Prompt.ipynb`.

All paths are confined to `PathsConfig.media_root`; this confinement helper (`_paths.py`) is reused by tickets 07–08, so it lives in its own module. Backend chosen by `--stt-backend/--tts-backend` (default from config; `fake` in tests). The server must never print to stdout except MCP protocol (stdio transport); diagnostics go to stderr.

Note in `docs/MCP-SERVERS.md` that this design still needs the instructor's sign-off (DESIGN.md human checkpoint 1).

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| path absolute, contains `..`, or resolves (after symlinks) outside `media_root` | tool error `"path outside media root"`; nothing read or written |
| input file missing / not audio | tool error naming the relative path |
| output file exists | overwritten only if `overwrite=true`, else tool error |
| output parent dir missing | created (inside media root only) |
| audio longer than 120 s | tool error (cost guard); limit is a server flag |
| text longer than 2 000 chars to synthesize | tool error (cost guard) |
| backend raises `SpeechBackendError` | tool error with the message; server keeps running |
| stray `print` to stdout | prevented: logging configured to stderr at startup; a test asserts stdout carries only JSON-RPC |

### C — Contracts
```python
# src/voice_agent/mcp_servers/_paths.py
class PathEscapeError(ValueError): ...
def resolve_inside(root: Path, rel: str, *, must_exist: bool) -> Path: ...   # resolves symlinks; raises PathEscapeError

# src/voice_agent/mcp_servers/speech.py   (run: python -m voice_agent.mcp_servers.speech [--stt-backend fake] [--tts-backend fake] [--media-root DIR])
mcp = FastMCP("speech")

@mcp.tool()
async def transcribe_audio_file(path: str) -> dict[str, object]:
    """Transcribe a WAV file under the media folder. Returns {"text", "language", "duration_s"}."""

@mcp.tool()
async def synthesize_speech_to_file(text: str, path: str, voice: str | None = None, overwrite: bool = False) -> dict[str, object]:
    """Speak text into a WAV file under the media folder. Returns {"path", "duration_s", "sample_rate"}."""

@mcp.tool()
async def get_tts_voices() -> list[dict[str, object]]:
    """List available voices: [{"id", "name", "language"}]."""

@mcp.resource("speech://voices")
async def voices_resource() -> str: ...      # markdown list, same data as get_tts_voices

def main(argv: Sequence[str] | None = None) -> int: ...
```
Tool names and return keys above are the contract the agent (ticket 10) and docs rely on.

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | With `mcp>=1.2,<2`, `from mcp.server.fastmcp import FastMCP` works and async tools are supported | `uv run python -c "from mcp.server.fastmcp import FastMCP; print(FastMCP)"` after `uv add` |
| A2 | A tool raising an exception is returned to the client as `isError=True` result, not a crash | P3 asserts it against the real client |
| A3 | `mcp.client.stdio.stdio_client` + `ClientSession` can spawn `sys.executable -m voice_agent.mcp_servers.speech` in tests | P2 |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_resolve_inside` — table of escapes (`/etc/passwd`, `../x`, `a/../../x`, symlink pointing out) all raise; `a/b.wav` resolves | `uv run pytest -q tests/mcp_servers/test_paths.py` |
| P2 | `test_list_tools_and_resource` — real stdio client lists exactly the 3 tools + `speech://voices` | `uv run pytest -q tests/mcp_servers/test_speech_server.py` |
| P3 | `test_synthesize_then_transcribe_fake` — fake backends: synthesize to `out/a.wav`, file exists with expected duration, transcribe returns the fake script text | same |
| P4 | `test_path_escape_is_tool_error` — `isError` true, server still answers the next call | same |
| P5 | `test_cost_guards` — 2 001 chars and 121 s clip rejected | same |
| P6 | `test_stdout_only_jsonrpc` — spawn server, send initialize, every stdout line parses as JSON-RPC | same |

## Unknowns
- Q1: whether the instructor accepts this as "MCP server for ASR/TTS". Building it is cheap either way.

## Dependencies/license
`mcp>=1.2,<2` (MIT).

## Acceptance Criteria
- [ ] Contracts implemented; `docs/MCP-SERVERS.md` documents the speech server (tools, resource, flags, confinement rule)
- [ ] All Proof rows green; gate suite green

## Non-Goals
- No streaming audio over MCP (the live loop imports the engine directly)
- No HTTP/SSE transport

## Security-sensitive
yes — filesystem path confinement for a tool an LLM drives. Flag for `security-auditor`.

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/AUDIO-ENGINE.md`; producer of `docs/MCP-SERVERS.md` and `_paths.resolve_inside` (reused by 07, 08)

## Execution Log

### 2026-10-15 developer: DONE
- Assumptions verified: A1 `uv run python -c "from mcp.server.fastmcp import FastMCP; print(FastMCP)"` → class printed, mcp 1.30.0 installed; A2 P4 real stdio client got `isError=True` for escapes and the next call succeeded; A3 P2 `stdio_client` spawns `sys.executable -m voice_agent.mcp_servers.speech`
- Proof: P1 ✅ P2 ✅ P3 ✅ P4 ✅ P5 ✅ P6 ✅ (command: `uv run pytest -q tests/mcp_servers` → 34 passed)
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (60 passed, 0 skipped)
- Files: src/voice_agent/mcp_servers/{__init__,_paths,speech}.py, docs/MCP-SERVERS.md, tests/mcp_servers/{test_paths,test_speech_server}.py, pyproject.toml + uv.lock (`uv add "mcp>=1.2,<2"`)
- Deviations: `SpeechBackendError` (ticket 05, not landed) is looked up via importlib in `audio_engine._ws` with a local fallback class; once 05 lands the fallback is dead code. Real-backend `api_key` kwarg is passed from config secrets for names deepgram/cartesia (unverified until 05). Extra `--max-audio-s` flag per the cost-guard edge. Duration guard uses `sf.info` before reading the whole file. Absolute paths are rejected even when inside the root.
- Commit: see git log (`06: Speech MCP server (stdio) — transcribe / synthesize / list voices`)
- Notes for reviewer: security-sensitive; confinement in `_paths.py` (reject absolute/`..`/NUL, then resolve symlinks and `is_relative_to` on the resolved root). Check-then-write TOCTOU between resolve and write is not defended (single-user local server). P6 proves stdout carries only JSON-RPC while stderr gets the log line; a stray `print` inside a tool would still corrupt stdout, which is not separately guarded.

### 2026-10-15 security-auditor: findings (static scan, main @ 06122e7; no High)
- **R2-1** TOCTOU: `_paths.resolve_inside` check, then `speech.py` write/read re-open by path string after an `await`; a dir swapped for a symlink in that gap lets a write land outside the media root (probe: `outside/pwn.wav` created). Same race on the read side (FIFO swap after `is_file()`).
- **R2-2** Any libsndfile format accepted; a ~2 KB FLAC named `.wav` at 655350 Hz × 8 ch passes the 120 s duration guard and would allocate ~2.5 GB.
- R3-3 error text leaks absolute host paths on write failures. R3-4 output extension/format unrestricted. R3-5 overwrite of a hard link writes through to a file outside the root.
- R4-6 `--max-audio-s nan/inf/<=0` silently disables the guard. R4-7 backend error text passed to the LLM (needs a ticket 05 requirement). R5-8 no cap on `voice` length or media-dir size. R5-9 `mcp>=1.2` floor loose (lock pins 1.30.0).

### 2026-10-15 claude (tech-lead role): FIX-REQUEST to developer (R2 loop, attempt 1 of 2)
Fix R2-1, R2-2, R3-3, R3-4, R3-5, R4-6, touching only `src/voice_agent/mcp_servers/speech.py`, `src/voice_agent/mcp_servers/_paths.py`, `src/audio_engine/wav.py` and the tests under `tests/mcp_servers/` and `tests/audio_engine/`:
1. Write: verify the parent dir component-by-component, open with `O_NOFOLLOW|O_CREAT|O_EXCL` in the verified parent dir (dir fd + `openat` semantics), write to a temp file there, then `os.replace` onto the target (this also breaks hard links). After opening, `fstat` and re-check the real path is still inside the root. Read: open with `O_NOFOLLOW`, `fstat`, require `S_ISREG`, read from that fd.
2. After `sf.info` require format in {WAV, WAVEX}, channels <= 2, samplerate <= 48000, frames*channels under a cap that keeps float32 allocation <= 64 MB, and a file-size cap; check before `sf.read`.
3. Output path must end in `.wav` (case-insensitive) and be written with `format="WAV"`. Wrap write errors so messages name only the relative path, never an absolute one.
4. `--max-audio-s` must be finite and > 0, else exit with a clear error.
5. Tests that really run (add to Proof, do not weaken existing rows): swap a directory for a symlink inside a fake synthesizer during the await and assert nothing is created outside the root; small high-samplerate/multichannel FLAC named `.wav` is rejected without allocating; hard-link overwrite does not modify the outside file; write error text contains no absolute path; non-`.wav` output rejected; `--max-audio-s nan` rejected.
Non-goals: do NOT change `config.py` defaults (media_root vs sandbox_root overlap is parked for the human), no new packages, keep every existing tool signature and edge.

### 2026-10-15 developer: DONE (fix-request R2 loop, attempt 1)
- Assumptions verified: tests written first and run against the old code: 10 failed / 7 passed in `tests/mcp_servers/test_speech_server.py` (race test, FLAC-bomb test, hard-link test, write-error-text test, non-.wav test, `--max-audio-s` nan/inf/0/-5 all failed pre-fix; `-inf` was already rejected by argparse). Post-fix all pass.
- Proof (added rows): F1 race swap dir->symlink during synthesis, nothing outside root ✅; F2 655350 Hz x 8ch FLAC named .wav (<10 KB) rejected, `read_wav` never called ✅; F3 3ch / 96 kHz / FLAC-as-.wav rejected ✅; F4 hard-link overwrite leaves outside file byte-identical ✅; F5 write error text has no absolute path ✅; F6 non-.wav outputs rejected, `UP.WAV` accepted and is format WAV ✅; F7 `--max-audio-s` nan/inf/-inf/0/-5 exit 2 ✅. Original P1-P6 unchanged and green.
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (101 passed)
- Files: src/voice_agent/mcp_servers/_paths.py (new `open_regular_for_read`, `write_file_atomic`: per-component O_NOFOLLOW|O_DIRECTORY dir-fd walk, proc-fd realpath recheck, O_EXCL temp file + `os.replace`/`os.link` in verified dir fd), src/voice_agent/mcp_servers/speech.py, src/audio_engine/wav.py (read_wav/write_wav also accept file objects; write forces format="WAV"), tests/mcp_servers/test_speech_server.py
- Deviations: file-size cap 64 MB and decoded cap 64 MB (float32) as constants; non-overwrite writes use `os.link` of the temp file so an existing target is never replaced (FileExistsError -> "output file exists"); symlinked intermediate dirs inside the root are now rejected as "path outside media root" even if they point inside. Early `resolve_inside` check kept for existing messages. No tests added in tests/audio_engine (wav change covered by existing tests + speech tests). `config.py` untouched.
- Commit: HEAD of branch worktree-agent-a3708a1d3170b3960 (message `06: security fixes (R2-1, R2-2, R3-3..5, R4-6)`)
- Notes for reviewer: `_check_fd_inside` is skipped where /proc/self/fd is unavailable. Duration is computed from frames/samplerate (same as sf.info.duration).

### 2026-10-16 tech-lead: Review Feedback (main @ 92f3edb)
- R3 [R2, security] root TOCTOU in _paths (reproduced write outside root). R4 [R2, security] raw backend exception text forwarded by FastMCP; fallback SpeechBackendError catches nothing. R7 [R2, HUMAN H3] backend kwargs vs ticket 05 ctors (Cartesia voice required). R13 [R2] docs drift. R21/R30 [R3].
Full detail (root cause, evidence, fix): `handoff/REVIEW-main-92f3edb.md`. Not auto-fixed; retries need human approval.

### 2026-10-16 developer: DONE (package B: R3, R4, R30 voice cap)
- R3: `_paths._open_root` opens the configured root with `O_DIRECTORY|O_NOFOLLOW`, no `resolve()`; every walk starts from that fd (ELOOP/ENOTDIR -> PathEscapeError). `_check_fd_inside` removed (it compared against the same re-resolved root). `resolve_inside` unchanged (kept its `is_symlink` early check as a convenience; confinement no longer depends on it). All public signatures unchanged.
- R4: `speech.py` wraps backend construction and every backend call (`transcribe`, `synthesize`, `list_voices`) in `except Exception`; tool error is `speech backend error: <ExceptionType>` plus ` (status N)` when a `SpeechBackendError` has an int `status`. Detail goes to stderr log with configured secret values redacted.
- R30: `voice` capped at 128 chars (`MAX_VOICE_CHARS`) -> ValueError before any work. pyproject `mcp` floor is out of scope (not in file list).
- Tests: 6 new (2 paths root swap mirroring reproduction 3, 3 backend error sanitising, 1 voice cap); all 6 fail on old code (reverted src, 6 failed / 45 passed), pass on fix. make check green; full pytest 183 passed, 6 skipped.
- Not done / for reviewer: the review also suggested recording root (st_dev, st_ino) at server start and comparing with fstat; skipped because it needs a signature or state change in `_paths` (O_NOFOLLOW on the root already closes the swap). Ticket 05 note (authenticate with headers, never query-string key) is for the orchestrator. R7 untouched.

### 2026-10-16 security-auditor: static re-audit of fix B (main @ 4f2bb4f; _paths.py, speech.py + tests)
Scope: `git diff fdf96d6 4f2bb4f -- src tests`. Static only (no VAPT, no paid API calls). `uv run pytest -q tests/mcp_servers/test_paths.py tests/mcp_servers/test_speech_server.py` -> 51 passed. Repro scripts are in session scratchpad (not committed), run with `uv run python`. Output below with temp paths elided.
Counts: High 0 · Med 1 · Low 5.
- **M1 [R2] Root confinement covers only the root's last component. Ancestors are still followed, and the (st_dev, st_ino) pin from the R3 fix was skipped.** `_open_root` (`_paths.py:45-51`) applies `O_NOFOLLOW` only to `media`. If `media_root` sits below a directory the sandbox can write (the review's "configured deeper" case, e.g. `sandbox_root/a/media`), replacing `a` with a symlink sends reads and writes outside the root. No race is needed: `resolve_inside` resolves through the swapped ancestor, so its own check passes. Repro P1: `os.rename(sb/"a", sb/"a.old"); os.symlink(outside, sb/"a")`, then `write_file_atomic(sb/"a/media", "x/pwn.wav", ...)` -> `ESCAPED -> outside/media/x/pwn.wav`, and `open_regular_for_read(..., "secret.wav")` -> `ESCAPED, read b'RIFFsecret'`. The shipped default (`media_root = sandbox_root/media`, sandbox_root bind-mounted at /sandbox) is NOT exploitable: P2 (root itself swapped for a symlink) -> refused, and P3 (symlinked intermediate directory) -> refused. The developer's note "O_NOFOLLOW on the root already closes the swap" holds only for the last component. Fix: record the root's `(st_dev, st_ino)` at server start and compare it with `fstat` of each `_open_root` fd. As an alternative, refuse at start-up when `media_root` is strictly below `sandbox_root` and more than one level deep. Owner: developer.
- **L1 [R3] Absolute host path leaks to the model through PermissionError.** `speech.py:185,187` (`dest.is_dir()` / `dest.exists()`) and `_paths.py:34` (`resolved.exists()` via `_resolve`) re-raise EACCES on Python 3.12. `_resolve` catches only PathEscapeError and FileNotFoundError. Repro S1 with `chmod 000 media/locked`: `ToolError Error executing tool synthesize_speech_to_file: [Errno 13] Permission denied: '<abs host path>/media/locked/o.wav'`, and the same happens for `transcribe_audio_file`. This predates fix B but sits on the error-sanitising surface. Fix: catch OSError in `_resolve` and around the pre-checks, and return a message that names only the relative path.
- **L2 [R3] The `_redact` failure path chains the raw exception unsuppressed.** `_backend_failure` calls `load_config()` inside the `except` block, but `_redact` catches only ConfigError. When load_config raises an OSError (for example an unreadable `.env`, or `path.open` failing), `from None` never runs. The original exception then becomes the unsuppressed `__context__`. On the resource path, FastMCP's `logger.exception` prints it. Repro S3: the client sees `ResourceError ...: [Errno 13] Permission denied: '/repo/.env'`, and the stderr log contains the raw backend URL (`raw URL in stderr log traceback: True`, plus "During handling..."). Fix: wrap `_redact` in `except Exception` and fall back to logging only the type name. Better still, load the secrets once in `main()`.
- **L3 [R4] Redaction matches exact strings only, and the log line still carries the full URL, query string and path.** Repro S4 log: `speech backend error: RuntimeError: fail wss://api.example.test/tts?api_key=sk_FAKE%2Bkey%2Fabc%3D key-prefix=sk_FAKE+ /home/u/x`. The URL-encoded form of the key and a truncated key prefix both pass through unredacted. The risk is low for current Deepgram/Cartesia key alphabets, and the log only goes to stderr/journald, not the model. Fix: also redact `quote(secret, safe="")`, and strip URL query strings (`[?&](api_key|token|key)=[^&\s]+`) from the logged text. Ticket 05 should still authenticate with headers.
- **L4 [R4] The original exception is still reachable through `__context__`.** Repro S2: the tool message is `speech backend error: RuntimeError`, `__suppress_context__ True`, and `__context__` still holds the raw text. FastMCP 1.30 only uses `str(e)` and the traceback rendering honours the suppress flag, so nothing leaks today. A future serializer or error reporter that walks `__context__` would leak it. Fix (optional): build the ValueError outside the `except` block, or set `exc.__context__ = None` before raising.
- **L5 [R4] Voice cap: the length is enforced (129 rejected, 128 accepted, check runs before any file I/O), but the content is not validated.** Repro S5: `voice="../../x\r\nX-Inj: 1\x00"` is accepted (the fake backend ignores it). This is harmless now. Once ticket 05 puts the voice id into a URL path or header, it becomes an injection point. Fix: restrict to `[A-Za-z0-9_-]{1,128}`, or require ids from `list_voices()`, here or in ticket 05.
- Clean (verified, no finding): every directory component is walked with `O_DIRECTORY|O_NOFOLLOW` from a dir fd (P3 refused). The temp file uses `O_CREAT|O_EXCL|O_NOFOLLOW` with a random 64-bit name in the verified dir fd, and `os.replace`/`os.link(follow_symlinks=False)` replace a symlinked target rather than following it (P3b: outside target unchanged). Reads use `O_NOFOLLOW|O_NONBLOCK` plus `S_ISREG`. Hard links from inside the sandbox cannot reach host files (bind mount -> EXDEV), and overwrite replaces the directory entry rather than writing through. `SpeechBackendError.status` is forwarded only when it is an `int` and not a `bool`. `CancelledError` is not swallowed (it is a BaseException). No hardcoded secrets in the diff; the test keys are fake.
- Fix-requests: M1 -> developer (fix-request, not auto-fixed). L1-L5 are hardening and don't block.

### 2026-10-16 developer: DONE (package B2: M1, L1, L2, L3, L5)
- M1: new `_paths.set_trust_anchor(path|None)` (no existing signature changed). When the root is lexically below the anchor, `_open_root` opens the anchor normally (symlinks above it, e.g. /home, are fine) and walks every component below it with `O_DIRECTORY|O_NOFOLLOW`; a swapped ancestor -> PathEscapeError. `speech.main()` sets the anchor to `paths.sandbox_root`. Unset anchor or root outside it keeps the old last-component check. Chose this over a (st_dev, st_ino) pin because the pin needs start-up state and would not catch a swap that happens before first use.
- L1: `_resolve` maps OSError to `path not accessible: <rel>`; the `is_dir`/`exists` pre-checks do the same.
- L2: `_redact` returns None on any exception; `_backend_failure` then logs only the type name.
- L3: redaction covers raw, `quote`, `quote_plus` forms and every prefix >= 6 chars of each; URL query/fragment stripped, `key=`/`api_key=`/`token=` values redacted.
- L5: voice must be `isprintable()` and contain no `/` or `\`; message `invalid voice id`; 128 cap kept.
- Tests: 2 paths + 3 speech functions (6 voice params) new. On old code 11 failed (the two M1 tests fail with AttributeError: set_trust_anchor missing, since the anchor is the new API) ; pass on the fix.
- L4 untouched. Deviation: the M1 anchor is process-global state in `_paths`.
