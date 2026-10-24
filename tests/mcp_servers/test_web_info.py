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
