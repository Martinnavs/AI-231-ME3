# 20: Assistant MCP server — time, weather, timers, alarms, reminders (any value), spoken when due

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 19 (shares the `PipelineDeps.tools` hook it adds)
**Status:** ready-for-agent

## Amendment 2026-10-23 (direction change: local speech, laptop mic + Pi loudspeaker, `doc_chat` findings)
Unaffected by the new topology: due-event announcements go through the same TTS path and count as played audio for the guard. Last in the order.

## Files
- `src/voice_agent/assistant/__init__.py`, `src/voice_agent/assistant/store.py`, `src/voice_agent/assistant/weather.py`
- `src/voice_agent/mcp_servers/assistant.py`
- `src/voice_agent/pipeline/assistant_events.py`; wire it in `src/voice_agent/pipeline/app.py`
- `src/voice_agent/agent/session.py` (add `add_context_note`)
- `src/voice_agent/telemetry/events.py` + `docs/EVENT-LOG.md` (add `ASSISTANT_EVENT`)
- `config/agent.toml` (`[mcp_servers.assistant]`)
- `docs/MCP-SERVERS.md` (assistant section)
- `tests/assistant/test_store.py`, `tests/assistant/test_weather.py`, `tests/assistant/test_live_weather.py` (`live`), `tests/mcp_servers/test_assistant_server.py`, `tests/pipeline/test_assistant_events.py`

## Action
ME2's base tasks include time, weather, timers, alarms and reminders, and the professor wants them to work for **any phrasing and any value** (2026-10-15). The ME2 dashboard only accepts a few preset values for these, so they live in our own server, which keeps their state on the Pi and tells the pipeline when something is due.

1. **Time:** `get_time(timezone=None)` in `AppConfig.timezone` (`zoneinfo`).
2. **Weather:** Open-Meteo (free, no API key): geocode the place name (default `AppConfig.location`), then fetch current conditions or the daily forecast for today/tomorrow. WebFetch the Open-Meteo forecast and geocoding API docs first; record the URLs and parameters in `docs/MCP-SERVERS.md`. Map WMO weather codes to plain words. 3 s timeout; cache per (place, when) for 10 minutes.
3. **Timers, alarms, reminders:** stored in `paths.state_dir/assistant.json` (written atomically: temp file + rename). Free values: a timer of any duration from 1 s to 24 h, an alarm at any local time (today if still ahead, else tomorrow, unless a date is given), reminders with any text and an optional time. **The model converts what the user said into these formats** (ticket 10 prompt rule 5); tools validate strictly and return errors that say the expected format, so the model can fix the call itself.
4. **Due events:** the hidden tool `due_events()` returns everything that fell due since the last call (timers done, alarms ringing, timed reminders), each delivered exactly once, and removes finished timers and one-shot alarms.
5. **Pipeline hook** (`assistant_events.py`): poll `assistant__due_events` once a second. For each event, wait until the user isn't mid-turn (no VAD speech and no turn in progress; at most 10 s), then play a short chime (two-tone, 300 ms, generated in code) and speak a fixed template ("Your five minute timer for the eggs is done.", "It's seven fifteen. Here's your alarm.", "Reminder: call mom.") through the TTS directly — no LLM call, so it's instant. Then `session.add_context_note(text)` so the model knows what happened if the user responds ("snooze it", "thanks"). Emit `ASSISTANT_EVENT`. A barge-in stops the announcement like any bot speech.
6. **`AgentSession.add_context_note(text)`:** notes are added as a separate text block at the start of the next user message (`[event] …`), then cleared, never as a stray assistant message, so turn order stays valid.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| weather place not found | tool error "I couldn't find <place>" |
| weather service unreachable / timeout | tool error "weather service unreachable"; no retry inside the call |
| `when="tomorrow"` | daily forecast for tomorrow in the place's timezone: min/max temperature, condition, rain chance |
| timer duration ≤ 0 or > 24 h | tool error stating the valid range |
| alarm time not `HH:MM` 24-hour, or date not `YYYY-MM-DD` | tool error stating the format with an example |
| alarm time already passed today, no date | set for tomorrow; result says which day |
| two timers running | both tracked; `list` returns both with time remaining; cancel by `label` or `id`; an ambiguous label → error listing them |
| cancel something that doesn't exist | tool error "no timer called <label>" |
| `assistant.json` corrupt at start | renamed to `assistant.json.bad-<ts>`; start empty; warn |
| process restarted while a timer runs | timer reloaded with its original due time; if already past, delivered by the next `due_events()` |
| event due while the user is speaking | announcement waits until the turn ends (≤ 10 s), then plays |
| two events due together | announced back to back, in due-time order |
| TTS fails for an announcement | chime still plays; error logged; event still noted in the session |

### C — Contracts
```python
# src/voice_agent/assistant/store.py
@dataclass(frozen=True)
class Timer:   id: str; label: str | None; due_at: datetime; created_at: datetime
@dataclass(frozen=True)
class Alarm:   id: str; label: str | None; due_at: datetime
@dataclass(frozen=True)
class Reminder: id: str; text: str; due_at: datetime | None; fired: bool
@dataclass(frozen=True)
class DueEvent: kind: Literal["timer", "alarm", "reminder"]; id: str; label: str | None; text: str | None; due_at: datetime

class AssistantStore:
    def __init__(self, path: Path, *, tz: ZoneInfo, now: Callable[[], datetime] = ...) -> None: ...
    def add_timer(self, duration_s: int, label: str | None) -> Timer: ...
    def add_alarm(self, time_hhmm: str, date: str | None, label: str | None) -> Alarm: ...
    def add_reminder(self, text: str, at: str | None) -> Reminder: ...   # at: "YYYY-MM-DDTHH:MM" local, optional
    def cancel(self, kind: str, ref: str) -> None: ...                   # ref: id or label
    def list(self, kind: str) -> list[Timer] | list[Alarm] | list[Reminder]: ...
    def pop_due(self) -> list[DueEvent]: ...

# src/voice_agent/assistant/weather.py
@dataclass(frozen=True)
class WeatherReport:
    place: str
    when: Literal["now", "today", "tomorrow"]
    condition: str
    temperature_c: float | None
    min_c: float | None
    max_c: float | None
    precipitation_chance_pct: int | None
async def get_weather(place: str, when: str, *, client: httpx.AsyncClient) -> WeatherReport: ...
def wmo_to_words(code: int) -> str: ...

# src/voice_agent/mcp_servers/assistant.py   (run: python -m voice_agent.mcp_servers.assistant [--state-dir DIR])
mcp = FastMCP("assistant")
@mcp.tool()
async def get_time(timezone: str | None = None) -> dict[str, object]: ...      # {"iso","weekday","date","time_12h","timezone"}
@mcp.tool()
async def get_weather(place: str | None = None, when: Literal["now", "today", "tomorrow"] = "now") -> dict[str, object]: ...
@mcp.tool()
async def timer(action: Literal["start", "cancel", "list"], duration_s: int | None = None, label: str | None = None) -> dict[str, object]: ...
@mcp.tool()
async def alarm(action: Literal["set", "cancel", "list"], time: str | None = None, date: str | None = None, label: str | None = None) -> dict[str, object]: ...
@mcp.tool()
async def reminder(action: Literal["add", "list", "remove"], text: str | None = None, at: str | None = None, ref: str | None = None) -> dict[str, object]: ...
@mcp.tool()
async def due_events() -> list[dict[str, object]]: ...    # hidden (AgentConfig.hidden_tools); pipeline only

# src/voice_agent/pipeline/assistant_events.py
def announcement_text(event: DueEvent, tz: ZoneInfo) -> str: ...
def chime(sample_rate: int) -> AudioClip: ...
class AssistantEventLoop:
    def __init__(self, *, tools: ToolRegistry, synthesizer: Synthesizer, session: AgentSession,
                 is_busy: Callable[[], bool], play: Callable[[AudioClip | AsyncIterator[bytes]], Awaitable[None]],
                 poll_s: float = 1.0, max_wait_s: float = 10.0, stage_logger: StageLogger | None = None) -> None: ...
    async def run(self) -> None: ...

# src/voice_agent/agent/session.py (addition)
#   def add_context_note(self, text: str) -> None: ...
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | Open-Meteo forecast and geocoding APIs need no key and return the fields used above | WebFetch the docs; quote one response of each in the docs; P3 mocks those shapes |
| A2 | WMO weather-code meanings | the Open-Meteo docs' code table; copy it into `wmo_to_words` |
| A3 | The Pi has tzdata for `zoneinfo` | Debian ships `tzdata`; ticket 16's preflight `os_release` covers Debian 13; add `tzdata` to the runbook prerequisites |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_store_values_and_persistence` — fake clock: timer 90 s, alarm "07:15" at 08:00 → tomorrow, reminder with and without time; reload from disk equals; corrupt file → backed up, empty store | `uv run pytest -q tests/assistant/test_store.py` |
| P2 | `test_pop_due_once` — advance fake clock past two dues → both events once, in due order; second call empty; finished timer gone from `list` | same |
| P3 | `test_weather_mocked` — `httpx.MockTransport` with the A1 shapes: now/today/tomorrow reports; unknown place → error; timeout → error; second call within 10 min served from cache (transport hit count) | `uv run pytest -q tests/assistant/test_weather.py` |
| P4 | `test_tool_validation_messages` — bad time formats, out-of-range durations, ambiguous cancel → errors that state the expected format / list the candidates | `uv run pytest -q tests/mcp_servers/test_assistant_server.py` |
| P5 | `test_list_tools_and_hidden` — stdio client lists the 6 tools; the fast agent's subset excludes `assistant__due_events` | same |
| P6 | `test_timer_announced_in_file_mode` — `main(["--fake", "--with-mcp", ...])` with a scripted turn that starts a 2 s timer: output WAV has the chime then announcement audio 2 ± 1.1 s after the tool call (poll granularity); `assistant_event` logged; the next user turn's request contains the `[event]` note | `uv run pytest -q tests/pipeline/test_assistant_events.py` |
| P7 | `test_announcement_waits_for_user` — event due while the fixture user is speaking → announcement starts after that turn's `endpoint`, not before | same |
| P8 | `test_live_weather` (`live`, human-run): real Open-Meteo call for `AppConfig.location` | `VOICE_AGENT_LIVE=1 uv run pytest -q -m live tests/assistant/test_live_weather.py` — **do not run** |

## Unknowns
None

## Dependencies/license
None new (`httpx` already present; `zoneinfo` is stdlib).

## Acceptance Criteria
- [ ] Assistant server, event loop and `add_context_note` implemented; docs section written
- [ ] P1–P7 green; P8 written and skipped; tickets 10–12 and 19 tests still green; gate suite green

## Non-Goals
- Recurring alarms, snooze as a tool (the model can set a new timer), calendar integration
- Mirroring onto the ME2 dashboard widgets

## Security-sensitive
no (one outbound HTTPS API without credentials; writes one JSON file under `state_dir`)

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/MCP-SERVERS.md`, `docs/EVENT-LOG.md`

## Execution Log
