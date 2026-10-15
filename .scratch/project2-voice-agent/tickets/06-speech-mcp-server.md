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
