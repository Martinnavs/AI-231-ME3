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
