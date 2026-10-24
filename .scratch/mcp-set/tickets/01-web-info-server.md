# Ticket 01: weather + Wikipedia MCP server (keyless public APIs)

**Status:** delivered-for-review
**Date:** 2026-10-24
**Blocked by:** None (tickets 01 and 02 are independent: disjoint files, no data flows between them)

A real MCP server next to the sample `search_docs`: `weather(place, day)` through Open-Meteo (geocoding + forecast) and `wiki_summary(topic)` through Wikipedia's REST API, both without API keys, answering in one or two sentences the assistant can say. Network problems come back as a sentence. Same fastmcp 2.14.7 setup as `src/voice_agent/mcp_servers/docs.py`. Checked against the live APIs by the assistant (Wikimedia refuses user agents without a contact, hence the repository URL in it).

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `src/voice_agent/mcp_servers/web_info.py` | new, verbatim | **131** |
| `tests/mcp_servers/test_web_info.py` | new, verbatim | **100** |

**Non-goals / touch nothing else:** no change to any existing file (the agent wiring is ticket 03); no new dependency (httpx and fastmcp are installed); no network access from the tests.

**Security-sensitive:** no secrets; sends the place name or topic the user asked for to Open-Meteo and Wikipedia.

## New files, verbatim

Create each file with exactly this content (byte-for-byte, final newline included).

### `src/voice_agent/mcp_servers/web_info.py`

````python
"""Weather and Wikipedia MCP server (fastmcp): keyless public APIs, short answers for a voice.

uv run --group laptop python -m voice_agent.mcp_servers.web_info [--home "Quezon City"]

Open-Meteo (geocoding and forecast) and Wikipedia's REST API need no key. Answers are one or two
sentences the assistant can say as they are; a network problem comes back as a sentence too, so
the model can tell the user instead of failing the turn.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Sequence
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
    home: str = HOME_PLACE, transport: httpx.AsyncBaseTransport | None = None
) -> FastMCP:
    """A FastMCP server with weather and wiki_summary; `transport` is for tests."""
    server = FastMCP("web_info")

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}
        )

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
        try:
            async with client() as http:
                geo = await http.get(GEOCODE_URL, params={"name": name, "count": 1})
                found = geo.json().get("results") or []
                if not found:
                    return f"I couldn't find a place called {name}."
                where = found[0]
                params: dict[str, Any] = {
                    "latitude": where["latitude"],
                    "longitude": where["longitude"],
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max",
                    "timezone": "auto",
                    "forecast_days": 2,
                }
                daily = (await http.get(FORECAST_URL, params=params)).json()["daily"]
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
        try:
            async with client() as http:
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

### `tests/mcp_servers/test_web_info.py`

````python
import httpx
from fastmcp import Client

from voice_agent.mcp_servers.web_info import build_server, first_sentences

GEO = {"results": [{"name": "Quezon City", "latitude": 14.65, "longitude": 121.05}]}
FORECAST = {
    "daily": {
        "weather_code": [61, 0],
        "temperature_2m_max": [31.4, 33.0],
        "temperature_2m_min": [24.6, 25.2],
        "precipitation_probability_max": [80, 5],
    }
}
SEARCH = {"pages": [{"key": "Blood_Meridian", "title": "Blood Meridian"}]}
SUMMARY = {
    "extract": "Blood Meridian is a 1985 novel by Cormac McCarthy. It follows the Glanton gang. "
    "The novel is set in the borderlands. It is often called his masterpiece."
}


def _transport(
    routes: dict[str, object], seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        for prefix, body in routes.items():
            if str(request.url).startswith(prefix):
                if isinstance(body, int):
                    return httpx.Response(body)
                return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


async def _call(transport: httpx.MockTransport, tool: str, args: dict[str, object]) -> str:
    async with Client(build_server("Quezon City", transport)) as client:
        result = await client.call_tool(tool, args)
    return str(result.structured_content["result"])


async def test_lists_the_two_tools() -> None:
    async with Client(build_server("Quezon City", _transport({}))) as client:
        names = sorted(t.name for t in await client.list_tools())
    assert names == ["weather", "wiki_summary"]


async def test_weather_today_at_home_is_one_sayable_sentence() -> None:
    seen: list[httpx.Request] = []
    routes = {"https://geocoding-api": GEO, "https://api.open-meteo.com": FORECAST}
    text = await _call(_transport(routes, seen), "weather", {})
    assert text == (
        "Today in Quezon City: light rain, 25 to 31 degrees Celsius, 80 percent chance of rain."
    )
    assert seen[0].url.params["name"] == "Quezon City"  # no place given: the home place


async def test_weather_tomorrow_uses_the_second_day() -> None:
    routes = {"https://geocoding-api": GEO, "https://api.open-meteo.com": FORECAST}
    text = await _call(_transport(routes), "weather", {"place": "Quezon City", "day": "Tomorrow"})
    assert text.startswith("Tomorrow in Quezon City: clear skies, 25 to 33 degrees")


async def test_weather_unknown_place_and_outage_are_sentences() -> None:
    routes = {"https://geocoding-api": {"results": []}}
    assert await _call(_transport(routes), "weather", {"place": "Xyzzy"}) == (
        "I couldn't find a place called Xyzzy."
    )
    down = {"https://geocoding-api": 503}
    assert "did not answer" in await _call(_transport(down), "weather", {})


async def test_wiki_summary_is_cut_to_three_sentences() -> None:
    routes = {
        "https://en.wikipedia.org/w/rest.php": SEARCH,
        "https://en.wikipedia.org/api": SUMMARY,
    }
    text = await _call(_transport(routes), "wiki_summary", {"topic": "blood meridian"})
    assert text == (
        "Blood Meridian is a 1985 novel by Cormac McCarthy. It follows the Glanton gang. "
        "The novel is set in the borderlands."
    )


async def test_wiki_summary_no_article_and_outage_are_sentences() -> None:
    routes = {"https://en.wikipedia.org/w/rest.php": {"pages": []}}
    assert await _call(_transport(routes), "wiki_summary", {"topic": "qwzx"}) == (
        "I couldn't find a Wikipedia article about qwzx."
    )
    down = {"https://en.wikipedia.org/w/rest.php": 500}
    assert "did not answer" in await _call(_transport(down), "wiki_summary", {"topic": "x"})


def test_first_sentences_caps_the_length() -> None:
    assert first_sentences("One. Two. Three. Four.") == "One. Two. Three."
    long = "word " * 200
    out = first_sentences(long, limit=50)
    assert len(out) <= 50 and out.endswith("...")
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `uv run --offline --group laptop ruff check src/voice_agent/mcp_servers/web_info.py tests/mcp_servers/test_web_info.py` | All checks passed! |
| 2 | `uv run --offline --group laptop ruff format --check src/voice_agent/mcp_servers/web_info.py tests/mcp_servers/test_web_info.py` | 2 files already formatted |
| 3 | `uv run --offline --group laptop mypy` | Success: no issues found in 55 source files |
| 4 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/mcp_servers/test_web_info.py` | 7 passed |

## Deviations

none

## Execution Log

### 2026-10-24 agy: DONE
- Proof: 1 ✅ (`uv run --offline --group laptop ruff check src/voice_agent/mcp_servers/web_info.py tests/mcp_servers/test_web_info.py`: All checks passed!) 2 ✅ (`uv run --offline --group laptop ruff format --check src/voice_agent/mcp_servers/web_info.py tests/mcp_servers/test_web_info.py`: 2 files already formatted) 3 ✅ (`uv run --offline --group laptop mypy`: Success: no issues found in 55 source files) 4 ✅ (`uv run --offline --group laptop pytest -q -p no:cacheprovider tests/mcp_servers/test_web_info.py`: 7 passed, 1 warning in 0.84s)
- Files:
  - `src/voice_agent/mcp_servers/web_info.py` (131 / 131)
  - `tests/mcp_servers/test_web_info.py` (100 / 100)
- Deviations: none
- Notes for reviewer: none


### 2026-10-24 reviewer (assistant): ACCEPTED
- `diff -rq orig work`: only this wave's four new files and the tickets changed.
- `cmp` against the tested skeleton: byte-identical. Deviations: none.
- Integrated into the repository; gate: ruff and mypy clean, pytest 634 passed, 9 skipped, 2 xfailed, 1 failed (the known `test_flow` case that needs a speech server).
