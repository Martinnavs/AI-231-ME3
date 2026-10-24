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
