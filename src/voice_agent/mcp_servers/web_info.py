"""Weather, nearby places and Wikipedia MCP server (fastmcp): short answers for a voice.

uv run --group laptop python -m voice_agent.mcp_servers.web_info [--home "Cebu City"]

Open-Meteo (geocoding and forecast), Photon (OpenStreetMap, nearby places) and Wikipedia's REST
API need no key; a Google Maps key file, when present, adds ratings to nearby places.
Answers are one or two sentences the assistant can say as they are; a network problem comes
back as a sentence too, so the model can tell the user instead of failing the turn.

Speed: Open-Meteo's servers are about 270 ms away from the Philippines and a fresh TLS handshake
costs about 0.6 s, so one HTTP client (one open connection per host) is kept for the server's
life, place lookups are remembered, and a forecast is reused for FORECAST_TTL_S.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import math
import os
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WIKI_SEARCH_URL = "https://en.wikipedia.org/w/rest.php/v1/search/title"
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
HOME_PLACE = os.environ.get("VOICE_AGENT_HOME_PLACE", "Cebu City")  # the demo venue
# the venue's point (Guadalupe, near the user; the geocoder's Cebu City point is in Calamba), so
# "near me" and the boot-time barangay lookup agree and resolve with no geocoder
KNOWN_PLACES = {"cebu city": {"name": "Cebu City", "latitude": 10.3157, "longitude": 123.8854}}
# Photon's reverse search: the nearest places with an OSM tag, no text match (Overpass took 24 s or
# timed out from Cebu on 2026-11-11; this answers in about 1.3 s)
PHOTON_URL = "https://photon.komoot.io/reverse"
GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"
# never read by an assistant; the server reads it when a nearby search runs (a Maps demo key)
GOOGLE_KEY_FILE = Path(
    os.environ.get("GOOGLE_MAPS_KEY_FILE", "~/.secrets/google/maps-key.txt")
).expanduser()
# "lat,lon" of the venue, copied from a map pin: "near me" then means near it, not the city centre
HOME_COORDS = os.environ.get("VOICE_AGENT_HOME_COORDS", "")
NEARBY_RADIUS_M = 1500
NEARBY_COUNT = 3
NEARBY_TTL_S = 3600.0  # places hardly change; the venue's common kinds are fetched at boot
WARM_KINDS = ("restaurant", "cafe", "pharmacy", "atm")
NEARBY_TIMEOUT_S = 8.0
# kind: (OSM tag as key:value, Google place type, spoken plural)
NEARBY_KINDS: dict[str, tuple[str, str, str]] = {
    "restaurant": ("amenity:restaurant", "restaurant", "restaurants"),
    "cafe": ("amenity:cafe", "cafe", "cafes"),
    "fast food": ("amenity:fast_food", "fast_food_restaurant", "fast food places"),
    "bar": ("amenity:bar", "bar", "bars"),
    "pharmacy": ("amenity:pharmacy", "pharmacy", "pharmacies"),
    "atm": ("amenity:atm", "atm", "ATMs"),
    "hospital": ("amenity:hospital", "hospital", "hospitals"),
    "supermarket": ("shop:supermarket", "supermarket", "supermarkets"),
    "convenience store": ("shop:convenience", "convenience_store", "convenience stores"),
    "gas station": ("amenity:fuel", "gas_station", "gas stations"),
    "hotel": ("tourism:hotel", "hotel", "hotels"),
}
NEARBY_ALIASES = {
    "restaurants": "restaurant", "food": "restaurant", "place to eat": "restaurant",
    "places to eat": "restaurant", "eat": "restaurant", "cafes": "cafe", "coffee": "cafe",
    "coffee shop": "cafe", "coffee shops": "cafe", "bars": "bar", "pharmacies": "pharmacy",
    "drugstore": "pharmacy", "drugstores": "pharmacy", "atms": "atm", "cash machine": "atm",
    "hospitals": "hospital", "clinic": "hospital", "supermarkets": "supermarket",
    "grocery": "supermarket", "grocery store": "supermarket",
    "convenience stores": "convenience store", "gas": "gas station",
    "petrol station": "gas station", "fuel": "gas station", "hotels": "hotel",
    "fast food restaurant": "fast food",
}  # fmt: skip
# Wikimedia refuses requests whose user agent has no contact (403, "robot policy")
USER_AGENT = "me3-voice-agent/0.1 (https://github.com/Martinnavs/AI-231-ME3; course project)"
TIMEOUT_S = 4.0
KEEPALIVE_S = 120.0  # keep the connection open between turns (httpx closes it after 5 s)
FORECAST_TTL_S = 3600.0  # an hour; an older one (up to STALE_S) is used when the service is down
STALE_S = 24 * 3600.0
HOME_REFRESH_S = 3000.0  # the home forecast is fetched in the background, so it works offline
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


def nearby_kind(text: str) -> str | None:
    """The NEARBY_KINDS key a spoken kind of place means, or None."""
    said = " ".join(text.lower().replace("-", " ").split())
    said = NEARBY_ALIASES.get(said, said)
    return said if said in NEARBY_KINDS else None


def split_kind(text: str) -> tuple[str, str] | None:
    """(kind, qualifier): "restaurants" -> ("restaurant", ""), "sushi restaurant" ->
    ("restaurant", "sushi"); None when no known kind ends the phrase."""
    words = " ".join(text.lower().replace("-", " ").split()).split()
    for size in (3, 2, 1):
        if size > len(words):
            continue
        kind = nearby_kind(" ".join(words[-size:]))
        if kind:
            return kind, " ".join(words[:-size])
    return None


def metres_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + (
        math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 12_742_000 * math.asin(math.sqrt(a))


def spoken_distance(metres: float) -> str:
    near = max(50, round(metres / 50) * 50)
    if near >= 1000:
        km = f"{near / 1000:.1f}".removesuffix(".0")
        return f"{km} kilometer{'' if km == '1' else 's'}"
    return f"{near} meters"


def nearby_sentence(plural: str, place: str, items: list[tuple[str, float, str]]) -> str:
    """'The closest cafes to Cebu City are A, 300 meters, B, ... and C, ...' for a voice."""
    parts = [
        f"{name}, {spoken_distance(m)}{', ' + extra if extra else ''}" for name, m, extra in items
    ]
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f", and {parts[-1]}"
    return f"The closest {plural} to {place} are {listed}."


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
    coords: str = HOME_COORDS,
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

    async def fetch(name: str) -> tuple[dict[str, Any], dict[str, Any]] | str:
        """(place, daily forecast) or a sentence; a forecast up to STALE_S old stands in when the
        service does not answer (a demo on a poor connection)."""
        http = client()
        where = places.get(name.lower())
        key = (where["latitude"], where["longitude"]) if where else None
        cached = forecasts.get(key) if key else None
        if cached is not None and clock() - cached[0] < FORECAST_TTL_S:
            return where, cached[1]  # type: ignore[return-value]
        try:
            if where is None:
                geo = await http.get(GEOCODE_URL, params={"name": name, "count": 1})
                found = geo.json().get("results") or []
                if not found:
                    return f"I couldn't find a place called {name}."
                where = places[name.lower()] = found[0]
                key = (where["latitude"], where["longitude"])
            params: dict[str, Any] = {
                "latitude": where["latitude"],
                "longitude": where["longitude"],
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 2,
            }
            daily = (await http.get(FORECAST_URL, params=params)).json()["daily"]
            forecasts[(where["latitude"], where["longitude"])] = (clock(), daily)
            return where, daily
        except (httpx.HTTPError, KeyError, ValueError):
            stale = forecasts.get(key) if key else None
            if where is not None and stale is not None and clock() - stale[0] < STALE_S:
                return where, stale[1]
            return "The weather service did not answer, so I can't say right now."

    nearby_cache: dict[tuple[str, float, float], tuple[float, list[tuple[str, float, str]]]] = {}

    async def locate(name: str) -> dict[str, Any] | None:
        """A place's name and coordinates: remembered, the venue's own, else geocoded."""
        if name.lower() == home.lower() and coords:
            with contextlib.suppress(ValueError):
                lat, lon = (float(part) for part in coords.split(","))
                return {"name": home, "latitude": lat, "longitude": lon}
        # the venue's own point first: the geocoder's point for "Cebu City" is in another barangay
        # than the one the agent server looks up at boot (voice_pipeline.venue.CITY_CENTRES)
        known = KNOWN_PLACES.get(name.lower()) or places.get(name.lower())
        if known is not None:
            return known
        try:
            geo = await client().get(GEOCODE_URL, params={"name": name, "count": 1})
            found = geo.json().get("results") or []
        except (httpx.HTTPError, ValueError):
            return None
        if found:
            places[name.lower()] = found[0]
        return found[0] if found else None

    async def google_nearby(
        kind: str, lat: float, lon: float
    ) -> list[tuple[str, float, str]] | None:
        """Nearest places with their ratings from Google, or None (no key, or it did not work)."""
        try:
            key = GOOGLE_KEY_FILE.read_text().strip()
        except OSError:
            return None
        if not key:
            return None
        body = {
            "includedTypes": [NEARBY_KINDS[kind][1]],
            "maxResultCount": NEARBY_COUNT,
            "rankPreference": "DISTANCE",
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": float(NEARBY_RADIUS_M),
                }
            },
        }
        headers = {
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": "places.displayName,places.rating,places.location",
        }
        try:
            r = await client().post(
                GOOGLE_PLACES_URL, json=body, headers=headers, timeout=NEARBY_TIMEOUT_S
            )
            r.raise_for_status()
            rows = r.json().get("places") or []
            out = []
            for row in rows[:NEARBY_COUNT]:
                where = row["location"]
                rating = row.get("rating")
                out.append(
                    (
                        row["displayName"]["text"],
                        metres_between(lat, lon, where["latitude"], where["longitude"]),
                        f"rated {rating}" if rating else "",
                    )
                )
            return out
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return None

    async def photon_nearby(kind: str, lat: float, lon: float) -> list[tuple[str, float, str]]:
        r = await client().get(
            PHOTON_URL,
            params={
                "lat": lat,
                "lon": lon,
                "radius": NEARBY_RADIUS_M / 1000,
                "limit": 20,
                "osm_tag": NEARBY_KINDS[kind][0],
            },
            timeout=NEARBY_TIMEOUT_S,
        )
        r.raise_for_status()
        seen: dict[str, tuple[str, float, str]] = {}
        for feature in r.json()["features"]:
            props = feature.get("properties") or {}
            name = str(props.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            there, here = feature["geometry"]["coordinates"]
            street = str(props.get("street") or "").strip()
            seen[name.lower()] = (
                name,
                metres_between(lat, lon, here, there),
                f"on {street}" if street else "",
            )
        return sorted(seen.values(), key=lambda item: item[1])[:NEARBY_COUNT]

    async def nearby_items(kind: str, lat: float, lon: float) -> list[tuple[str, float, str]]:
        """The closest places of a kind, remembered for NEARBY_TTL_S."""
        key = (kind, round(lat, 3), round(lon, 3))
        cached = nearby_cache.get(key)
        if cached is not None and clock() - cached[0] < NEARBY_TTL_S:
            return cached[1]
        items = await google_nearby(kind, lat, lon) or await photon_nearby(kind, lat, lon)
        nearby_cache[key] = (clock(), items)
        return items

    async def warm_nearby() -> None:
        """Fetch the common kinds around the venue now: a places service answers in 1 to 6 s."""
        where = await locate(home)
        if where is None:
            return
        for kind in WARM_KINDS:
            with contextlib.suppress(Exception):
                await nearby_items(kind, float(where["latitude"]), float(where["longitude"]))

    async def refresh_home() -> None:
        while True:
            with contextlib.suppress(Exception):
                await fetch(home)
            await warm_nearby()
            await asyncio.sleep(HOME_REFRESH_S)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        task = asyncio.create_task(keep_warm(ping_every_s)) if ping_every_s else None
        homely = asyncio.create_task(refresh_home()) if ping_every_s else None
        try:
            yield
        finally:
            if homely is not None:
                homely.cancel()
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
        got = await fetch(name)
        if isinstance(got, str):
            return got
        where, daily = got
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
            "The closest places of one kind to the user: restaurant, cafe, fast food, bar, "
            "pharmacy, atm, hospital, supermarket, convenience store, gas station or hotel. "
            f"Leave `place` empty for 'near me', 'nearby' and 'here' (the user is in {home}); "
            "give it only when the user names a city or area. Say the answer as it is; if the "
            "kind is unclear (something to eat), ask which kind first."
        )
    )
    async def nearby(kind: str, place: str | None = None) -> str:
        last_use[0] = clock()
        split = split_kind(kind)
        if split is None:
            return "I can look up restaurants, cafes, pharmacies, ATMs, hospitals, hotels and more."
        which, qualifier = split
        name = (place or home).strip()
        where = await locate(name)
        if where is None:
            return f"I couldn't find a place called {name}."
        lat, lon = float(where["latitude"]), float(where["longitude"])
        try:
            items = await nearby_items(which, lat, lon)
        except (httpx.HTTPError, KeyError, ValueError):
            return "The places service did not answer, so I can't look that up right now."
        plural = NEARBY_KINDS[which][2]
        if not items:
            return f"I couldn't find any {plural} near {where['name']}."
        said = nearby_sentence(plural, where["name"], items)
        if qualifier:  # the data has no cuisine filter: say so instead of passing it off
            return f"I can't search for {qualifier} ones, but {said[0].lower()}{said[1:]}"
        return said

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
    parser.add_argument("--coords", default=HOME_COORDS, help='the venue as "lat,lon"')
    args = parser.parse_args(argv)
    build_server(args.home, coords=args.coords).run(show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
