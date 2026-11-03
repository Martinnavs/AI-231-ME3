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
import asyncio
import contextlib
import os
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
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
KEEPALIVE_S = 120.0  # keep the connection open between turns (httpx closes it after 5 s)
FORECAST_TTL_S = 600.0
# A lookup of a new place on cold connections took 2.7-5.7 s (2026-11-03, Manila): the geocoding
# and forecast hosts each cost 0.8-1.2 s of TCP and TLS, against 0.55-1.0 s on warm ones. So both
# connections are opened at start and a request to each host's root (no forecast is fetched) goes
# out every PING_EVERY_S while the server is in use.
PING_URLS = ("https://geocoding-api.open-meteo.com/", "https://api.open-meteo.com/")
PING_EVERY_S = 20.0
PING_WHILE_ACTIVE_S = 600.0  # stop pinging after this long without a lookup
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


async def wiki_text(http: httpx.AsyncClient, topic: str) -> str:
    """The first sentences of the Wikipedia article on `topic`, or a sentence saying why not."""
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


def build_server(
    home: str = HOME_PLACE,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
    ping_every_s: float | None = PING_EVERY_S,
) -> FastMCP:
    """A FastMCP server with weather and wiki_summary; `transport`, `clock` and `ping_every_s`
    (None: no pings) are for tests."""
    clients: list[httpx.AsyncClient] = []  # made on first use, inside the server's event loop
    places: dict[str, dict[str, Any]] = {}
    forecasts: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}

    def client() -> httpx.AsyncClient:
        if not clients:
            clients.append(
                httpx.AsyncClient(
                    transport=transport,
                    timeout=TIMEOUT_S,
                    headers={"User-Agent": USER_AGENT},
                    limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S),  # httpx's default is 5 s
                )
            )
        return clients[0]

    last_use = [clock()]

    async def keep_warm(every: float) -> None:
        """Open both connections now, then touch them while lookups are happening."""
        while True:
            if clock() - last_use[0] < PING_WHILE_ACTIVE_S:
                for url in PING_URLS:
                    with contextlib.suppress(httpx.HTTPError):
                        await client().get(url, timeout=5.0)
            await asyncio.sleep(every)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        task = asyncio.create_task(keep_warm(ping_every_s)) if ping_every_s else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            for c in clients:
                await c.aclose()

    server = FastMCP("web_info", lifespan=lifespan)

    @server.tool(
        description=(
            f"The weather for a place, today or tomorrow: conditions, temperature range and the "
            f"chance of rain. Leave `place` empty for the user's home ({home}). `day` is "
            "'today' or 'tomorrow'. Say the answer as it is."
        )
    )
    async def weather(place: str | None = None, day: str = "today") -> str:
        last_use[0] = clock()
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
            "Fallback only: a short Wikipedia summary by subject name. Prefer web_answer, and "
            "never call both for one question."
        )
    )
    async def wiki_summary(topic: str) -> str:
        return await wiki_text(client(), topic)

    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--home", default=HOME_PLACE, help="place used when none is given")
    args = parser.parse_args(argv)
    build_server(args.home).run(show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
