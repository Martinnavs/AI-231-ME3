# 09: MCP client manager — spawn stdio servers, expose tools to the agent

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 06, 07, 08 (needs the real servers to test against)
**Status:** done

## Files
- `src/voice_agent/agent/__init__.py`, `src/voice_agent/agent/mcp_clients.py`
- `tests/agent/test_mcp_clients.py`

## Action
The harness side of MCP: at startup, spawn every server in `AppConfig.mcp_servers` over stdio, keep the sessions open for the life of the app (startup cost paid once, not per turn), list their tools, and present them as one `ToolRegistry` the agent (ticket 10) can turn into Anthropic tool definitions and call. This is the `mcp_chatbot.py` pattern from the lesson notebooks (`connect_to_server`, `server_config.json`, tool discovery, call dispatch) — see `resources/Create-MCP-Resource-Prompt.ipynb`, "Using Resources and Prompts in the MCP Chatbot" — made async-safe and long-lived with `contextlib.AsyncExitStack`.

Tool names are namespaced `<server>__<tool>` so two servers can't collide. Each agent role sees a subset: the fast agent gets `AgentConfig.fast_tool_prefixes` (`home__`, `media__`, `assistant__`); the subagent gets `code__*` and `speech__*`; `AgentConfig.hidden_tools` are excluded from every model's view but stay callable by the pipeline (filtering is the caller's job; the registry offers `subset(prefixes, exclude=...)`). Servers named in config but not yet built (media, assistant land in tickets 19–20) are simply absent until then.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| a server fails to start or initialize within 10 s | logged to stderr; that server's tools are absent; the others still load; `registry.unavailable` lists it with the reason |
| a server dies mid-session | the next call returns `ToolOutcome(is_error=True, text="server <name> unavailable")`; one automatic restart attempt on that call; never raises into the agent loop |
| tool call exceeds its timeout (default 30 s; `code__run_python` 130 s) | `ToolOutcome(is_error=True, text="timed out")` |
| tool result content has several blocks / non-text blocks | text blocks joined with `\n`; non-text blocks summarized as `[<type> omitted]` |
| `${VAR}` in a server's `env` | expanded from the process environment at spawn; unset var → server marked unavailable with reason (secrets never logged) |
| duplicate namespaced name | impossible by construction; asserted |
| `aclose()` | all sessions closed, child processes reaped (no zombies) |

### C — Contracts
```python
# src/voice_agent/agent/mcp_clients.py
@dataclass(frozen=True)
class ToolSpec:
    name: str                          # "<server>__<tool>"
    server: str
    tool: str                          # name on the server
    description: str
    input_schema: dict[str, Any]       # JSON Schema from the server, unchanged

@dataclass(frozen=True)
class ToolOutcome:
    text: str
    is_error: bool
    duration_s: float

class ToolRegistry:
    @property
    def tools(self) -> list[ToolSpec]: ...                 # sorted by name (stable order → prompt caching)
    @property
    def unavailable(self) -> dict[str, str]: ...           # server -> reason
    def subset(self, prefixes: Sequence[str], *, exclude: Sequence[str] = ()) -> "ToolRegistry": ...   # exclude: exact names (AgentConfig.hidden_tools)
    def anthropic_tools(self) -> list[dict[str, Any]]: ... # [{"name","description","input_schema"}], same order as tools
    async def call(self, name: str, arguments: Mapping[str, Any], *, timeout_s: float | None = None) -> ToolOutcome: ...

class McpClientManager:
    def __init__(self, servers: Mapping[str, McpServerSpec], *, start_timeout_s: float = 10.0) -> None: ...
    async def __aenter__(self) -> ToolRegistry: ...
    async def __aexit__(self, *exc: object) -> None: ...
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `mcp` 1.x client API: `StdioServerParameters(command, args, env)`, `stdio_client(params)` async CM yielding `(read, write)`, `ClientSession(read, write)` with `initialize()`, `list_tools()`, `call_tool(name, arguments)` | `uv run python -c "import mcp, inspect; from mcp.client.stdio import stdio_client; print(inspect.signature(stdio_client))"` and the SDK README for the pinned version |
| A2 | `call_tool` result has `.content` (list of blocks with `.type`) and `.isError` | inspect the installed `mcp.types.CallToolResult` |
| A3 | `config/agent.toml`'s three servers start with `--backend fake`-style flags for tests | tests build their own `McpServerSpec`s with fake flags; don't depend on the shipped config |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_loads_three_real_servers` — speech (fake backends), home (MockTransport not possible across processes → point at an unused port; tools still list), code (tmp root) → namespaced, sorted tool list | `uv run pytest -q tests/agent/test_mcp_clients.py` |
| P2 | `test_one_broken_server_does_not_block_others` — a spec with `command="false"` | same |
| P3 | `test_call_roundtrip_speech_fake` — `speech__synthesize_speech_to_file` then `speech__transcribe_audio_file` | same |
| P4 | `test_server_killed_mid_session_restarts_once` | same |
| P5 | `test_subset_and_anthropic_tools_shape` — includes `exclude` removing an exact name while `call()` on the full registry still reaches it | same |
| P6 | `test_no_zombies_after_close` — child PIDs gone after `__aexit__` | same |

## Unknowns
None

## Dependencies/license
None new (`mcp` from 06).

## Acceptance Criteria
- [ ] Contracts implemented
- [ ] All Proof rows green; gate suite green

## Non-Goals
- No MCP prompts/resources consumption by the agent in plan 1 (tools only)
- No remote (HTTP) MCP transports

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/MCP-SERVERS.md`

## Execution Log

### 2026-10-15 developer: DONE
- Assumptions verified: A1 `uv run python -c "...inspect.signature(stdio_client)..."` → mcp 1.30.0, `stdio_client(server: StdioServerParameters, errlog=...)`, params fields command/args/env/cwd; `ClientSession.call_tool(name, arguments, read_timeout_seconds=...)`. A2 `CallToolResult.model_fields` → meta, content, structuredContent, isError. A3 tests build their own `McpServerSpec`s (sys.executable, fake backends, unused port, tmp roots); shipped config not used.
- Proof: P1 ✅ P2 ✅ P3 ✅ P4 ✅ P5 ✅ P6 ✅ plus env/timeout/render/unrecoverable-death tests (command: `uv run pytest -q tests/agent/test_mcp_clients.py` → 11 passed)
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (177 passed, 6 skipped)
- Files: src/voice_agent/agent/__init__.py, src/voice_agent/agent/mcp_clients.py, tests/agent/test_mcp_clients.py
- Deviations: each server is owned by one dedicated asyncio task (not a shared AsyncExitStack) so anyio scopes open/close in one task and a single server can be stopped/restarted/timed out independently; `subset()` registries only call their own tools (hidden tools stay callable on the full registry); unknown tool returns an error outcome; docs/MCP-SERVERS.md not touched (not in Files list).
- Commit: see git log (`09: ...`)
- Notes for reviewer: `code` server was only listed/spawned, never `run_python` (no bwrap here). Tests pass `start_timeout_s=60` because the speech import takes 3-4 s on this loaded host and the default 10 s flaked once with three servers booting in parallel. Per-call timeout is enforced both via `read_timeout_seconds` and `asyncio.wait_for`. Process reaping checked by scanning /proc for children of the test process (Linux only). Restart is serialised per server by a lock, so concurrent calls to one server queue.

### 2026-10-16 tech-lead: Review Feedback (main @ 92f3edb)
- R1 [R1] seam with 08: default MCP env drops XDG_RUNTIME_DIR (fix lands in runner; E2E proof package G). R15 [R3] timeout race returns McpError text. R16 [R3] tool output uncapped.
Full detail (root cause, evidence, fix): `handoff/REVIEW-main-92f3edb.md`. Not auto-fixed; retries need human approval.

### 2026-10-16 developer: fix package D (R15, R16 render cap): DONE
- R15: `ToolRegistry.call` maps `McpError` code 408 to "timed out". mcp 1.30 raises its read timeout with `httpx.codes.REQUEST_TIMEOUT` (408); `mcp.types.REQUEST_TIMEOUT` is commented out, so the review's constant doesn't exist. Used `HTTPStatus.REQUEST_TIMEOUT`.
- R16: `_render` caps at `MAX_OUTPUT_CHARS` (16 KiB) and appends `[truncated N chars]`. Error outcomes were already capped at 500 chars.
- Tests added (3, fail on old code): McpError 408 -> "timed out"; oversized output capped with marker; at-limit output untouched.
- R1: no change needed in mcp_clients.py (fix is in runner `_scrubbed_env`, package A). Not touched.
- R16 `list_files` entry cap remains package A.
- Gate: make check green; full pytest 180 passed, 6 skipped.
