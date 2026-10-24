# Ticket 05: web_info speed (one kept connection, remembered places, cached forecasts)

**Status:** delivered-for-review
**Date:** 2026-10-24
**Blocked by:** None (ticket 01 landed; independent of ticket 03: disjoint files, and the tool names and arguments do not change)

In the dry run the weather tool took about 3 s: Open-Meteo is about 270 ms away from here and every request opened a fresh TLS connection (about 0.6 s), twice per question. The server now keeps one HTTP client for its life, remembers place lookups, and reuses a forecast for 10 minutes. Measured by the assistant on the live APIs: a repeat question 5 ms, a new place on a warm connection 0.55 s, Wikipedia 0.45-0.76 s; the first weather question of a session is still cold (about 3 s). Answers and error sentences unchanged.

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `src/voice_agent/mcp_servers/web_info.py` | replace whole file, verbatim | **154** (now 131) |
| `tests/mcp_servers/test_web_info_cache.py` | new, verbatim | **59** |

**Non-goals / touch nothing else:** no change to `tests/mcp_servers/test_web_info.py` (it must still pass unchanged), the tool names, arguments or sentences; no warm-up at start; no network in the tests.

**Security-sensitive:** no.

## Whole files, verbatim

Replace the existing file entirely, and create the new one (byte-for-byte, final newline included).

### `src/voice_agent/mcp_servers/web_info.py`

````python
"""Weather and Wikipedia MCP server (fastmcp): keyless public APIs, short answers for a voice.

uv run --group laptop python -m voice_agent.mcp_servers.web_info [--home "Quezon City"]

Open-Meteo (geocoding and forecast) and Wikipedia's REST API need no key. Answers are one or two
sentences the assistant can say as they are; a network problem comes back as a sentence too, so
the model can tell the user instead of failing the turn.

Speed: Open-Meteo's servers are about 270 ms away from the Philippines and a fresh TLS handshake
costs about 0.6 s, so one HTTP client (one open connection per host) is kept for the server's
life, place lookups are remembered, and a forecast is reused for FORECAST_TTL_S.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from fastmcp import FastMCP

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WIKI_SEARCH_URL = "https://en.wikipedia.org/w/rest.php/v1/search/title"
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
HOME_PLACE = os.environ.get("VOICE_AGENT_HOME_PLACE", "Quezon City")
# Wikimedia refuses requests whose user agent has no contact (403, "robot policy")
USER_AGENT = "me3-voice-agent/0.1 (https://github.com/Martinnavs/AI-231-ME3; course project)"
TIMEOUT_S = 4.0
FORECAST_TTL_S = 600.0
MAX_SENTENCES = 3
MAX_CHARS = 600

# WMO weather interpretation codes, as Open-Meteo returns them
WEATHER_WORDS = {
    0: "clear skies", 1: "mainly clear skies", 2: "partly cloudy skies", 3: "overcast skies",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 80: "light showers", 81: "showers", 82: "heavy showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}  # fmt: skip


def first_sentences(text: str, n: int = MAX_SENTENCES, limit: int = MAX_CHARS) -> str:
    """The first `n` sentences of `text`, cut at `limit` characters."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out = " ".join(sentences[:n])
    return out if len(out) <= limit else out[: limit - 3].rsplit(" ", 1)[0] + "..."


def build_server(
    home: str = HOME_PLACE,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastMCP:
    """A FastMCP server with weather and wiki_summary; `transport` and `clock` are for tests."""
    server = FastMCP("web_info")
    clients: list[httpx.AsyncClient] = []  # made on first use, inside the server's event loop
    places: dict[str, dict[str, Any]] = {}
    forecasts: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}

    def client() -> httpx.AsyncClient:
        if not clients:
            clients.append(
                httpx.AsyncClient(
                    transport=transport, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}
                )
            )
        return clients[0]

    @server.tool(
        description=(
            f"The weather for a place, today or tomorrow: conditions, temperature range and the "
            f"chance of rain. Leave `place` empty for the user's home ({home}). `day` is "
            "'today' or 'tomorrow'. Say the answer as it is."
        )
    )
    async def weather(place: str | None = None, day: str = "today") -> str:
        name = (place or home).strip()
        offset = 1 if day.strip().lower() == "tomorrow" else 0
        http = client()
        try:
            where = places.get(name.lower())
            if where is None:
                geo = await http.get(GEOCODE_URL, params={"name": name, "count": 1})
                found = geo.json().get("results") or []
                if not found:
                    return f"I couldn't find a place called {name}."
                where = places[name.lower()] = found[0]
            key = (where["latitude"], where["longitude"])
            cached = forecasts.get(key)
            if cached is not None and clock() - cached[0] < FORECAST_TTL_S:
                daily = cached[1]
            else:
                params: dict[str, Any] = {
                    "latitude": where["latitude"],
                    "longitude": where["longitude"],
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max",
                    "timezone": "auto",
                    "forecast_days": 2,
                }
                daily = (await http.get(FORECAST_URL, params=params)).json()["daily"]
                forecasts[key] = (clock(), daily)
        except (httpx.HTTPError, KeyError, ValueError):
            return "The weather service did not answer, so I can't say right now."
        sky = WEATHER_WORDS.get(int(daily["weather_code"][offset]), "mixed weather")
        low = round(daily["temperature_2m_min"][offset])
        high = round(daily["temperature_2m_max"][offset])
        rain = daily["precipitation_probability_max"][offset]
        when = "Tomorrow" if offset else "Today"
        return (
            f"{when} in {where['name']}: {sky}, {low} to {high} degrees Celsius, "
            f"{rain} percent chance of rain."
        )

    @server.tool(
        description=(
            "A short encyclopedia summary (Wikipedia) of a person, place, book, film or thing. "
            "Use it for general-knowledge questions; write `topic` as the name of the subject."
        )
    )
    async def wiki_summary(topic: str) -> str:
        http = client()
        try:
            hits = await http.get(WIKI_SEARCH_URL, params={"q": topic, "limit": 1})
            pages = hits.json().get("pages") or []
            if not pages:
                return f"I couldn't find a Wikipedia article about {topic}."
            page = await http.get(WIKI_SUMMARY_URL + pages[0]["key"])
            extract = page.json().get("extract", "")
        except (httpx.HTTPError, KeyError, ValueError):
            return "Wikipedia did not answer, so I can't look that up right now."
        if not extract:
            return f"I couldn't find a Wikipedia article about {topic}."
        return first_sentences(extract)

    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--home", default=HOME_PLACE, help="place used when none is given")
    args = parser.parse_args(argv)
    build_server(args.home).run(show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
````

### `tests/mcp_servers/test_web_info_cache.py`

````python
"""web_info keeps one connection, remembers places and reuses a forecast for ten minutes."""

import httpx
from fastmcp import Client

from voice_agent.mcp_servers.web_info import FORECAST_TTL_S, build_server

GEO = {"results": [{"name": "Quezon City", "latitude": 14.65, "longitude": 121.05}]}
FORECAST = {
    "daily": {
        "weather_code": [61, 0],
        "temperature_2m_max": [31.4, 33.0],
        "temperature_2m_min": [24.6, 25.2],
        "precipitation_probability_max": [80, 5],
    }
}


class Counting(httpx.MockTransport):
    """Answers geocoding and forecast requests and counts them by host."""

    def __init__(self) -> None:
        self.hosts: list[str] = []
        super().__init__(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if request.url.host.startswith("geocoding"):
            return httpx.Response(200, json=GEO)
        return httpx.Response(200, json=FORECAST)


async def test_a_place_is_looked_up_once_and_a_forecast_reused() -> None:
    now = [1000.0]
    transport = Counting()
    async with Client(build_server("Quezon City", transport, clock=lambda: now[0])) as client:
        first = await client.call_tool("weather", {})
        again = await client.call_tool("weather", {"day": "tomorrow"})
        now[0] += FORECAST_TTL_S + 1
        later = await client.call_tool("weather", {"place": "quezon city"})
    assert transport.hosts == ["geocoding-api.open-meteo.com", "api.open-meteo.com",
                               "api.open-meteo.com"]  # fmt: skip
    assert str(first.structured_content["result"]).startswith("Today in Quezon City")
    assert str(again.structured_content["result"]).startswith("Tomorrow in Quezon City")
    assert str(later.structured_content["result"]).startswith("Today in Quezon City")


async def test_an_unknown_place_is_not_remembered() -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handle)
    async with Client(build_server("Quezon City", transport)) as client:
        await client.call_tool("weather", {"place": "Xyzzy"})
        await client.call_tool("weather", {"place": "Xyzzy"})
    assert calls == ["geocoding-api.open-meteo.com"] * 2
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `uv run --offline --group laptop ruff check src/voice_agent/mcp_servers/web_info.py tests/mcp_servers/test_web_info_cache.py` | All checks passed! |
| 2 | `uv run --offline --group laptop ruff format --check src/voice_agent/mcp_servers/web_info.py tests/mcp_servers/test_web_info_cache.py` | 2 files already formatted |
| 3 | `uv run --offline --group laptop mypy` | Success: no issues found in 55 source files |
| 4 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/mcp_servers/test_web_info.py tests/mcp_servers/test_web_info_cache.py` | 9 passed |

## Deviations

none

## Execution Log

### 2026-10-24 agy: DONE
- Proof: 1 ✅ (`uv run --offline --group laptop ruff check src/voice_agent/mcp_servers/web_info.py tests/mcp_servers/test_web_info_cache.py`: All checks passed!) 2 ✅ (`uv run --offline --group laptop ruff format --check src/voice_agent/mcp_servers/web_info.py tests/mcp_servers/test_web_info_cache.py`: 2 files already formatted) 3 ✅ (`uv run --offline --group laptop mypy`: Success: no issues found in 55 source files) 4 ✅ (`uv run --offline --group laptop pytest -q -p no:cacheprovider tests/mcp_servers/test_web_info.py tests/mcp_servers/test_web_info_cache.py`: 9 passed, 1 warning in 1.25s)
- Files:
  - `src/voice_agent/mcp_servers/web_info.py` (154 / 154)
  - `tests/mcp_servers/test_web_info_cache.py` (59 / 59)
- Deviations: none
- Notes for reviewer: none


### 2026-10-24 reviewer (assistant): ACCEPTED
- `diff -rq orig work`: only this ticket's files (and the tickets) changed; `cmp` against the tested reference: byte-identical. Deviations: none.
- Integrated (backup `.scratch/mcp-set/backup-153842/`). Root gate: ruff and mypy clean, pytest 636 passed, 9 skipped, 2 xfailed, 1 failed (the known `test_flow` case). `pipeline/` gate: ruff and mypy clean, pytest 24 passed.
