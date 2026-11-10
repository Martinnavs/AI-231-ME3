from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from voice_agent.mcp_servers import web_info
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


async def _call_home(transport: httpx.MockTransport, tool: str, args: dict[str, object]) -> str:
    async with Client(build_server("Cebu City", transport, ping_every_s=None)) as client:
        result = await client.call_tool(tool, args)
    return str(result.structured_content["result"])


async def _call(transport: httpx.MockTransport, tool: str, args: dict[str, object]) -> str:
    async with Client(build_server("Quezon City", transport, ping_every_s=None)) as client:
        result = await client.call_tool(tool, args)
    return str(result.structured_content["result"])


async def test_lists_the_three_tools() -> None:
    async with Client(build_server("Quezon City", _transport({}), ping_every_s=None)) as client:
        names = sorted(t.name for t in await client.list_tools())
    assert names == ["nearby", "weather", "wiki_summary"]


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


CEBU = {"results": [{"name": "Cebu City", "latitude": 10.3157, "longitude": 123.8854}]}


def _place(name: str, lon: float, lat: float, street: str | None = None) -> dict[str, object]:
    props: dict[str, object] = {"name": name}
    if street:
        props["street"] = street
    return {"geometry": {"coordinates": [lon, lat]}, "properties": props}


PHOTON = {
    "features": [
        _place("Far Cafe", 123.8854, 10.3200),
        _place("Lantaw", 123.8854, 10.3160, "Maria Christina Street"),
        _place("Lantaw", 123.8854, 10.3170),
        _place("Nearby Grill", 123.8854, 10.3180),
        {"geometry": {"coordinates": [123.8854, 10.3158]}, "properties": {}},
    ]
}
PHOTON_URL = "https://photon.komoot.io/"


async def test_nearby_defaults_to_the_venue_and_sorts_by_distance() -> None:
    seen: list[httpx.Request] = []
    routes = {"https://geocoding-api": CEBU, PHOTON_URL: PHOTON}
    text = await _call_home(_transport(routes, seen), "nearby", {"kind": "restaurants"})
    assert text == (
        "The closest restaurants to Cebu City are Lantaw, 50 meters, on Maria Christina Street, "
        "Nearby Grill, 250 meters, and Far Cafe, 500 meters."
    )  # unnamed items dropped, a duplicate name kept once, nearest first
    params = seen[-1].url.params
    assert (params["lat"], params["lon"], params["osm_tag"]) == (
        "10.3157",
        "123.8854",
        "amenity:restaurant",
    )


async def test_nearby_knows_the_venue_when_the_geocoder_is_down() -> None:
    routes = {"https://geocoding-api": 500, PHOTON_URL: PHOTON}
    text = await _call_home(_transport(routes), "nearby", {"kind": "cafe"})
    assert text.startswith("The closest cafes to Cebu City are Lantaw")


async def test_nearby_asks_for_a_known_kind_and_says_when_nothing_is_there() -> None:
    routes = {"https://geocoding-api": CEBU, PHOTON_URL: {"features": []}}
    assert "I can look up restaurants" in await _call_home(
        _transport(routes), "nearby", {"kind": "spaceship"}
    )
    assert await _call_home(_transport(routes), "nearby", {"kind": "pharmacy"}) == (
        "I couldn't find any pharmacies near Cebu City."
    )


async def test_nearby_failure_is_a_sentence_the_model_can_recover_from() -> None:
    routes = {"https://geocoding-api": CEBU, PHOTON_URL: 504}
    text = await _call_home(_transport(routes), "nearby", {"kind": "restaurant"})
    assert text == "The places service did not answer, so I can't look that up right now."


async def test_nearby_uses_google_with_ratings_when_a_key_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key.txt"
    key.write_text("test-key\n")
    monkeypatch.setattr(web_info, "GOOGLE_KEY_FILE", key)
    google = {
        "places": [
            {
                "displayName": {"text": "Casa Verde"},
                "rating": 4.5,
                "location": {"latitude": 10.3167, "longitude": 123.8854},
            }
        ]
    }
    seen: list[httpx.Request] = []
    routes = {"https://geocoding-api": CEBU, "https://places.googleapis.com/": google}
    text = await _call_home(_transport(routes, seen), "nearby", {"kind": "restaurant"})
    assert text == "The closest restaurants to Cebu City are Casa Verde, 100 meters, rated 4.5."
    sent = seen[-1]
    assert sent.headers["X-Goog-Api-Key"] == "test-key" and "key=" not in str(sent.url)


async def test_a_google_failure_falls_back_to_photon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key.txt"
    key.write_text("bad-key")
    monkeypatch.setattr(web_info, "GOOGLE_KEY_FILE", key)
    routes = {
        "https://geocoding-api": CEBU,
        "https://places.googleapis.com/": 403,
        PHOTON_URL: PHOTON,
    }
    text = await _call_home(_transport(routes), "nearby", {"kind": "restaurant"})
    assert text.startswith("The closest restaurants to Cebu City are Lantaw")


def test_nearby_kind_accepts_spoken_variants() -> None:
    assert web_info.nearby_kind("Coffee shops") == "cafe"
    assert web_info.nearby_kind("drug-store") is None  # not guessed
    assert web_info.nearby_kind("places to eat") == "restaurant"
    assert (
        web_info.HOME_PLACE == "Cebu City" or "VOICE_AGENT_HOME_PLACE" in __import__("os").environ
    )
