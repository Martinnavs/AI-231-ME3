# 06: Speech MCP server (stdio) — transcribe / synthesize / list voices

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 04 (05 for real backends; tests use the fake)
**Status:** ready-for-agent

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
