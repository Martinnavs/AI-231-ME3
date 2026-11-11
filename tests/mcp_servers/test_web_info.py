import asyncio
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


async def test_lists_the_four_tools() -> None:
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


def _google(name: str, lat: float, rating: float | None, count: int = 100) -> dict[str, object]:
    row: dict[str, object] = {
        "displayName": {"text": name},
        "location": {"latitude": lat, "longitude": 123.8854},
        "userRatingCount": count,
    }
    if rating is not None:
        row["rating"] = rating
    return row


async def test_nearby_uses_google_ratings_for_food_and_skips_unrated_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key.txt"
    key.write_text("test-key\n")
    monkeypatch.setattr(web_info, "GOOGLE_KEY_FILE", key)
    google = {
        "places": [
            _google("Far Fave", 10.3250, 4.5),
            _google("Casa Verde", 10.3167, 4.4),
            _google("Pin With No Rating", 10.3158, None),
            _google("Two Reviews", 10.3159, 5.0, count=2),
        ]
    }
    seen: list[httpx.Request] = []
    routes = {"https://geocoding-api": CEBU, "https://places.googleapis.com/": google}
    text = await _call_home(_transport(routes, seen), "nearby", {"kind": "restaurant"})
    assert text == (
        "The closest restaurants to Cebu City are Casa Verde, 100 meters, rated 4.4, "
        "and Far Fave, 1.1 kilometers, rated 4.5."
    )  # unrated and barely-rated pins dropped, nearest first
    sent = seen[-1]
    assert sent.headers["X-Goog-Api-Key"] == "test-key" and "key=" not in str(sent.url)
    assert b'"POPULARITY"' in sent.content


async def test_a_pharmacy_never_goes_to_google(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key.txt"
    key.write_text("test-key")
    monkeypatch.setattr(web_info, "GOOGLE_KEY_FILE", key)
    seen: list[httpx.Request] = []
    routes = {"https://geocoding-api": CEBU, PHOTON_URL: PHOTON}
    text = await _call_home(_transport(routes, seen), "nearby", {"kind": "pharmacy"})
    assert text.startswith("The closest pharmacies")
    assert not any(r.url.host == "places.googleapis.com" for r in seen)


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


async def test_nearby_uses_the_configured_venue_coordinates() -> None:
    seen: list[httpx.Request] = []
    routes = {PHOTON_URL: PHOTON}  # no geocoder route: the pin is enough
    async with Client(
        build_server(
            "Cebu City", _transport(routes, seen), ping_every_s=None, coords="10.2980, 123.9000"
        )
    ) as client:
        await client.call_tool("nearby", {"kind": "restaurant"})
    assert (seen[-1].url.params["lat"], seen[-1].url.params["lon"]) == ("10.298", "123.9")


def test_split_kind_separates_a_qualifier_from_the_kind() -> None:
    assert web_info.split_kind("restaurants") == ("restaurant", "")
    assert web_info.split_kind("Sushi restaurant") == ("restaurant", "sushi")
    assert web_info.split_kind("korean fast food") == ("fast food", "korean")
    assert web_info.split_kind("spaceship") is None
    assert web_info.split_kind("sushi place") is None  # not guessed


def test_spoken_distance_switches_to_kilometers_consistently() -> None:
    assert web_info.spoken_distance(20) == "50 meters"
    assert web_info.spoken_distance(640) == "650 meters"
    assert web_info.spoken_distance(980) == "1 kilometer"  # rounds to 1000
    assert web_info.spoken_distance(1043) == "1.1 kilometers"
    assert web_info.spoken_distance(1300) == "1.3 kilometers"


async def test_a_cuisine_is_not_pretended_to_be_filtered() -> None:
    routes = {"https://geocoding-api": CEBU, PHOTON_URL: PHOTON}
    text = await _call_home(_transport(routes), "nearby", {"kind": "sushi restaurant"})
    assert text.startswith(
        "I can't search for sushi ones, but the closest restaurants to Cebu City"
    )


async def test_nearby_keeps_the_venue_point_after_the_weather_geocoded_the_city() -> None:
    other = {"results": [{"name": "Cebu City", "latitude": 10.31672, "longitude": 123.89071}]}
    forecast = {"https://api.open-meteo.com": FORECAST}
    seen: list[httpx.Request] = []
    routes = {"https://geocoding-api": other, PHOTON_URL: PHOTON, **forecast}
    async with Client(build_server("Cebu City", _transport(routes, seen), ping_every_s=None)) as c:
        await c.call_tool("weather", {})  # remembers the geocoder's point for the city
        await c.call_tool("nearby", {"kind": "restaurant"})
    params = seen[-1].url.params
    assert (params["lat"], params["lon"]) == ("10.3157", "123.8854")


async def test_the_common_kinds_are_fetched_at_boot_and_answered_from_memory() -> None:
    seen: list[httpx.Request] = []
    routes = {
        "https://geocoding-api": CEBU,
        PHOTON_URL: PHOTON,
        "https://api.open-meteo.com": FORECAST,
    }
    transport = _transport(routes, seen)
    async with Client(build_server("Cebu City", transport, ping_every_s=60.0)) as client:
        for _ in range(100):  # the background refresh runs on the server's own event loop
            if sum(r.url.host == "photon.komoot.io" for r in seen) >= len(web_info.WARM_KINDS):
                break
            await asyncio.sleep(0.05)
        before = len(seen)
        text = await client.call_tool("nearby", {"kind": "pharmacy"})
    assert "The closest pharmacies" in str(text.structured_content["result"])
    assert len(seen) == before  # no new request: it came from memory


async def test_a_key_google_refuses_is_tried_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key.txt"
    key.write_text("not-enabled")
    monkeypatch.setattr(web_info, "GOOGLE_KEY_FILE", key)
    seen: list[httpx.Request] = []
    routes = {
        "https://geocoding-api": CEBU,
        "https://places.googleapis.com/": 403,
        PHOTON_URL: PHOTON,
    }
    async with Client(build_server("Cebu City", _transport(routes, seen), ping_every_s=None)) as c:
        await c.call_tool("nearby", {"kind": "restaurant"})
        await c.call_tool("nearby", {"kind": "cafe"})
    assert sum(r.url.host == "places.googleapis.com" for r in seen) == 1
    assert sum(r.url.host == "photon.komoot.io" for r in seen) == 2  # both still answered


GOOGLE_TEXT = {
    "places": [
        _google("Jollibee Far", 10.3500, 4.2),
        _google("Jollibee Lahug", 10.3320, 4.1),
        _google("Chowking Lahug", 10.3311, 4.0),  # near, but another name: not a match
    ]
}


async def test_nearest_picks_the_closest_place_with_the_name_from_google(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key.txt"
    key.write_text("test-key")
    monkeypatch.setattr(web_info, "GOOGLE_KEY_FILE", key)
    seen: list[httpx.Request] = []
    routes = {"https://geocoding-api": CEBU, "https://places.googleapis.com/": GOOGLE_TEXT}
    text = await _call_home(_transport(routes, seen), "nearby", {"name": "Jollibee"})
    assert text == "The nearest Jollibee is Jollibee Lahug, 1.8 kilometers away, rated 4.1."
    assert b'"textQuery":"Jollibee"' in seen[-1].content.replace(b" ", b"")


async def test_nearest_falls_back_to_photon_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_info, "GOOGLE_KEY_FILE", tmp_path / "missing.txt")
    photon = {
        "features": [
            _place("Jollibee Far", 123.8854, 10.3500),
            _place("Jollibee Guadalupe", 123.8854, 10.3170, "Vicente Rama Avenue"),
            _place("Mang Inasal", 123.8854, 10.3158),
        ]
    }
    routes = {"https://geocoding-api": CEBU, "https://photon.komoot.io/api/": photon}
    text = await _call_home(_transport(routes), "nearby", {"name": "jollibee"})
    assert (
        text
        == "The nearest jollibee is Jollibee Guadalupe, 150 meters away, on Vicente Rama Avenue."
    )


async def test_nearest_says_when_nothing_by_that_name_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_info, "GOOGLE_KEY_FILE", tmp_path / "missing.txt")
    routes = {"https://geocoding-api": CEBU, "https://photon.komoot.io/api/": {"features": []}}
    text = await _call_home(_transport(routes), "nearby", {"name": "Nowhere Burger"})
    assert text == "I couldn't find a Nowhere Burger near Cebu City."


def test_name_matches_needs_every_word() -> None:
    assert web_info.name_matches("jollibee", "Jollibee Lahug")
    assert web_info.name_matches("mercury drug", "Mercury Drug - Osmeña")
    assert not web_info.name_matches("jollibee", "Chowking")
    assert not web_info.name_matches("", "Anything")
