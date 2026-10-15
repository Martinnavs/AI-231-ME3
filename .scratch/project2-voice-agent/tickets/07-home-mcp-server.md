# 07: Home MCP server — lights, thermostat and phone on the ME2 dashboard

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 06 (reuses its `docs/MCP-SERVERS.md` and server conventions)
**Status:** done

## Files
- `src/voice_agent/mcp_servers/home.py`
- `docs/MCP-SERVERS.md` (append "home" section)
- `tests/mcp_servers/test_home_server.py`

## Action
The "real-world task" the fast agent can do directly: an MCP server wrapping the ME2 dashboard's HTTP API (review §4, last-but-one row). No ME2 model is involved; the dashboard is just a visible IoT target. Read the real endpoints and bodies in `.scratch/vendor/AI-222-Machine-Exercises/ME2/src/app/main.py` (body models near the top, routes from `_no_body_route(...)` onward, `_respond` returns `{"changed","message","state"}`) and the closed vocabularies at `GET /api/vocab`. Base URL from `AppConfig.home_dashboard_url` (default `http://127.0.0.1:8000`, ME2's `make app` default) or `--base-url`.

Validate arguments against `/api/vocab` (fetched once at startup, refreshed on a 4xx) so the LLM gets a precise error listing allowed values instead of a dashboard 422. Keep tools few and coarse: one tool per device with an `action` enum beats 25 tiny tools for a small model's tool-choice accuracy and prompt size.

**Scope after the professor's brief (2026-10-15).** The base tasks must work for any phrasing and any value, but the dashboard only accepts closed vocabularies (three alarm times, fixed reminder tasks, a few track names). So this server covers only the devices where a fixed set is a believable device limit: **lights, thermostat and phone (call, hang up, message)**. Music moves to the media server (ticket 19, real playback); timers, alarms and reminders move to the assistant server (ticket 20, free values). The dashboard's own music, timer, alarm, reminder, weather and time widgets are not driven by the agent.

The phone is simulated (the dashboard has no contacts): `call` and `send_message` take a contact name and message text, post to the dashboard's phone routes so the demo shows it, and append `{"t", "action", "contact", "text"}` to `paths.state_dir/phone.jsonl` so an evaluator can check what was "sent".

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| dashboard unreachable at startup | server starts anyway; vocab empty; each tool returns error "home dashboard unreachable at <url>" until it comes up |
| request timeout (> 2 s) | tool error "dashboard timed out"; no retry (the user can repeat) |
| value outside vocab (e.g. color "purple") | tool error listing allowed values; no HTTP call |
| dashboard returns `changed: false` | success result with `changed: false` and the dashboard's `message` (e.g. "lights already off") |
| dashboard returns 422 | tool error with the validation detail; vocab refreshed |
| unknown `action` | rejected by the tool's `Literal` type (schema) |
| `phone("call")` without a contact, or `send_message` with empty text | tool error "who should I call?" / "what should the message say?" — no HTTP call, so the model asks the user |
| `phone.jsonl` not writable | tool still succeeds on the dashboard; result carries `logged: false` |

### C — Contracts
```python
# src/voice_agent/mcp_servers/home.py   (run: python -m voice_agent.mcp_servers.home [--base-url URL])
mcp = FastMCP("home")

@mcp.tool()
async def get_home_state() -> dict[str, object]: ...             # GET /api/state

@mcp.tool()
async def control_lights(action: Literal["on", "off", "brighter", "dimmer", "set_color", "set_brightness"],
                         color: str | None = None, level: int | None = None) -> dict[str, object]: ...
@mcp.tool()
async def set_thermostat(degrees: int) -> dict[str, object]: ...
@mcp.tool()
async def phone(action: Literal["call", "hang_up"], contact: str | None = None) -> dict[str, object]: ...   # call needs contact
@mcp.tool()
async def send_message(contact: str, text: str) -> dict[str, object]: ...

@mcp.resource("home://vocab")
async def vocab_resource() -> str: ...

# every action tool returns {"changed": bool, "message": str, "state": {...}} (the dashboard's own _respond shape)
```
Map each action to the exact ME2 route found in `main.py` (e.g. `on` → `POST /api/light/power_on`); record the mapping table in `docs/MCP-SERVERS.md`.

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | The routes and body field names listed in this ticket match `ME2/src/app/main.py` at commit `3d68dbb` | read the file before coding; any mismatch → follow the file, log a minor deviation |
| A2 | `/api/vocab` keys are `reminder_tasks, alarm_times, timer_presets_s, light_colors, light_levels, thermostat_degrees, tracks` | same read |
| A3 | The phone routes take no body and return the usual `_respond` shape | same read |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_action_routes` — `httpx.MockTransport` records method+path+body for every tool/action; table-driven against the A1 mapping | `uv run pytest -q tests/mcp_servers/test_home_server.py` |
| P2 | `test_vocab_validation_rejects_without_http` | same |
| P3 | `test_unreachable_dashboard_is_tool_error` | same |
| P4 | `test_against_real_me2_app` — start ME2's FastAPI app in-process if its deps import (`fastapi`, `uvicorn` are not ours; **skip** with reason if not importable) and run `control_lights("off")` → `state.lights` off | same |
| P5 | `test_list_tools_over_stdio` — real stdio client lists exactly `get_home_state`, `control_lights`, `set_thermostat`, `phone`, `send_message` + the resource | same |
| P6 | `test_phone_log` — `send_message("Anna", "running late")` posts the phone route and appends one JSON line with contact and text to a tmp `state_dir` | same |

## Unknowns
None

## Dependencies/license
`httpx` (BSD-3; already added in 05).

## Acceptance Criteria
- [x] Contracts implemented; mapping table in `docs/MCP-SERVERS.md`
- [x] Proof rows green (P4 may skip); gate suite green

## Non-Goals
- No changes to ME2; no auth layer (dashboard stays on a trusted network — note it in the docs)
- No email tools (out of scope, D3); no real telephony
- No music, timer, alarm or reminder tools (tickets 19, 20)

## Security-sensitive
no (trusted-LAN HTTP client; writes one append-only log under `state_dir`)

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/MCP-SERVERS.md`

## Execution Log

### 2026-10-15 agy: DONE
- Assumptions verified: A1 read ME2/src/app/main.py at commit 3d68dbb; route and body field names matched ticket specifications; A2 read ME2/src/app/main.py; /api/vocab keys reminder_tasks, alarm_times, timer_presets_s, light_colors, light_levels, thermostat_degrees, tracks matched; A3 read ME2/src/app/main.py; phone routes take no body and return _respond shape {"changed", "message", "state"}
- Proof: P1 ✅ P2 ✅ P3 ✅ P4 ⏭️ P5 ✅ P6 ✅ (command: `uv run pytest -q tests/mcp_servers/test_home_server.py` → 7 passed, 1 skipped)
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (97 passed, 1 skipped)
- Files: src/voice_agent/mcp_servers/home.py, docs/MCP-SERVERS.md, tests/mcp_servers/test_home_server.py
- Deviations: none
- Commit: b2f4422
- Notes for reviewer: P4 skipped with reason because fastapi/uvicorn are not part of voice-agent dependencies; table-driven MockTransport and real stdio client test all contracts, actions, routes, vocab validation, timeout/reachability errors, and phone logging.

