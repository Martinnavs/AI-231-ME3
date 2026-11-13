"""The venue: the barangay looked up once at boot, and how "where are we" words it."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from voice_pipeline import fast_path, venue

# Nominatim responses for test data
NOMINATIM_CEBU = {
    "address": {
        "quarter": "Banawa",
        "suburb": "Guadalupe",
        "city": "Cebu City",
        "country": "Philippines",
    }
}
NOMINATIM_MANILA = {
    "address": {
        "suburb": "Ermita",
        "city": "Manila",
        "country": "Philippines",
    }
}


@pytest.fixture(autouse=True)
def _fresh_venue(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("VOICE_AGENT_HOME_COORDS", raising=False)
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "0")
    before = venue.current
    yield
    venue.current = before


def _client(body: Any, seen: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def test_the_barangay_is_the_suburb_before_the_quarter() -> None:
    assert venue.barangay_from(NOMINATIM_CEBU["address"]) == "Guadalupe"
    assert venue.barangay_from({"quarter": "Banawa"}) == "Banawa"
    assert venue.barangay_from({"city": "Cebu City"}) is None


async def test_default_venue_offline_no_request_made() -> None:
    seen: list[httpx.Request] = []
    await venue.resolve(_client(NOMINATIM_CEBU, seen))
    assert seen == [], "no HTTP request should be made for the known default venue"
    assert venue.spoken() == (
        "We're at the University of the Philippines Cebu, in Barangay Lahug, Cebu City."
    )
    assert venue.spoken(asked_barangay=True) == "We're in Barangay Lahug, Cebu City."
    line = venue.prompt_line()
    assert line is not None and "in Barangay Lahug" in line and "exact" not in line


async def test_given_coordinates_are_used_and_the_answer_says_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_AGENT_HOME_COORDS", "10.2980, 123.9000")
    seen: list[httpx.Request] = []
    await venue.resolve(_client(NOMINATIM_CEBU, seen))
    assert (seen[0].url.params["lat"], seen[0].url.params["lon"]) == ("10.298", "123.9")
    assert venue.spoken() == "We're in Barangay Guadalupe, Cebu City."


@pytest.mark.parametrize("body", [503, {"address": {}}, {"error": "Unable to geocode"}])
async def test_a_failed_lookup_leaves_the_city_answer(
    monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    monkeypatch.setenv("VOICE_AGENT_HOME_COORDS", "10.2, 123.8")
    await venue.resolve(_client(body))
    assert venue.spoken() == "We're in Cebu City."
    assert venue.spoken(asked_barangay=True) == "I don't know the barangay, but we're in Cebu City."
    assert venue.prompt_line() is None


def test_an_unknown_place_without_coordinates_has_nothing_to_look_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert venue.coordinates({}, "Atlantis") is None
    assert venue.coordinates({"VOICE_AGENT_HOME_COORDS": "oops"}, "Atlantis") is None
    assert venue.coordinates({"VOICE_AGENT_HOME_COORDS": "1.5,2.5"}, "Atlantis") == (1.5, 2.5, True)


async def test_which_barangay_is_answered_by_the_fast_path() -> None:
    await venue.resolve(_client(NOMINATIM_CEBU))
    for said in ("Which barangay am I in?", "Can you tell me what barangay we are in?"):
        found = fast_path.match(said)
        assert found is not None and found.tool == "clock", said
        assert "Barangay Lahug" in fast_path.local_answer(found, None)  # type: ignore[arg-type]


def test_the_venue_reaches_the_web_info_child_on_its_command_line() -> None:
    from voice_pipeline.agent_tools import web_info_command

    plain = web_info_command({})
    assert plain[-4:] == ["--home", "Cebu City", "--coords", "10.3225319,123.8988885"]
    pinned = web_info_command(
        {"VOICE_AGENT_HOME_PLACE": "Mandaue", "VOICE_AGENT_HOME_COORDS": "10.33,123.94"}
    )
    assert pinned[-4:] == ["--home", "Mandaue", "--coords", "10.33,123.94"]


# New tests for updated venue behavior


async def test_geoip_ph_far_result_then_nominatim_gives_around_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Geoip returns a far PH point (Manila), then Nominatim lookup happens."""
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "1")

    def mock_geoip(request: httpx.Request) -> httpx.Response:
        # Return a Manila geoip result (far from default Lahug)
        return httpx.Response(
            200,
            json={
                "success": True,
                "country_code": "PH",
                "latitude": 14.5995,
                "longitude": 120.9842,
                "city": "Manila",
            },
        )

    def mock_nominatim(request: httpx.Request) -> httpx.Response:
        # Return Manila Nominatim result
        return httpx.Response(200, json=NOMINATIM_MANILA)

    def handle(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return mock_nominatim(request)
        return mock_geoip(request)

    seen: list[httpx.Request] = []

    def handle_with_tracking(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handle(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle_with_tracking))
    await venue.resolve(client)
    assert len(seen) >= 2, "should make both geoip and Nominatim requests"
    assert any("nominatim" in str(r.url) for r in seen), "Nominatim request should be made"
    assert venue.spoken() == "We're around Barangay Ermita, Manila."
    assert venue.prompt_line() is not None and "around" in venue.prompt_line()


async def test_geoip_non_ph_result_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Geoip returns a non-PH result, default stays unchanged."""
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "1")

    def handle(request: httpx.Request) -> httpx.Response:
        # Return a non-PH country
        return httpx.Response(
            200,
            json={
                "success": True,
                "country_code": "US",  # Not Philippines
                "latitude": 40.7128,
                "longitude": -74.006,
                "city": "New York",
            },
        )

    seen: list[httpx.Request] = []

    def handle_with_tracking(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handle(request)

    await venue.resolve(httpx.AsyncClient(transport=httpx.MockTransport(handle_with_tracking)))
    # Should make geoip request but ignore non-PH result, no Nominatim request
    assert any("ipwho" in str(r.url) or "ipapi" in str(r.url) for r in seen), "geoip request made"
    assert not any("nominatim" in str(r.url) for r in seen), "Nominatim not called for non-PH"
    assert venue.spoken() == (
        "We're at the University of the Philippines Cebu, in Barangay Lahug, Cebu City."
    )


async def test_geoip_failure_keeps_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Geoip HTTP failures (500 on both URLs) keeps default."""
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "1")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    await venue.resolve(httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    assert venue.spoken() == (
        "We're at the University of the Philippines Cebu, in Barangay Lahug, Cebu City."
    )


async def test_pin_wins_over_geoip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin takes precedence, no geoip request is made."""
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "1")
    monkeypatch.setenv("VOICE_AGENT_HOME_COORDS", "10.2, 123.8")

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=NOMINATIM_CEBU)

    await venue.resolve(httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    # Should make Nominatim request but NOT geoip
    assert not any("ipwho" in str(r.url) or "ipapi" in str(r.url) for r in seen)
    assert any("nominatim" in str(r.url) for r in seen), "Nominatim called for pin"


async def test_geoip_disabled_makes_no_geoip_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """VOICE_AGENT_GEOIP=0 skips geoip entirely."""
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "0")

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise AssertionError("No request should be made with geoip disabled and default venue")

    await venue.resolve(httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    assert seen == [], "no requests with geoip=0 and default venue"
    assert venue.spoken() == (
        "We're at the University of the Philippines Cebu, in Barangay Lahug, Cebu City."
    )


async def test_nominatim_failure_after_geoip_gives_city_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After geoip finds far point, Nominatim failure leaves city name only."""
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "1")

    def handle(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return httpx.Response(503)  # Nominatim fails
        return httpx.Response(
            200,
            json={
                "success": True,
                "country_code": "PH",
                "latitude": 14.5995,
                "longitude": 120.9842,
                "city": "Manila",
            },
        )

    await venue.resolve(httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    assert venue.spoken() == "We're in Manila."
    assert venue.spoken(asked_barangay=True) == "I don't know the barangay, but we're in Manila."


async def test_geoip_near_default_keeps_default_no_nominatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Geoip result within 25km of default Lahug ignores it, no Nominatim request."""
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "1")

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # Return a nearby point (10.3167, 123.8907 is near Lahug at 10.3225319, 123.8988885)
        return httpx.Response(
            200,
            json={
                "success": True,
                "country_code": "PH",
                "latitude": 10.3167,
                "longitude": 123.8907,
                "city": "Cebu City",
            },
        )

    await venue.resolve(httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    # Should make geoip request but NOT Nominatim (near default)
    assert any("ipwho" in str(r.url) or "ipapi" in str(r.url) for r in seen)
    assert not any("nominatim" in str(r.url) for r in seen)
    assert venue.spoken() == (
        "We're at the University of the Philippines Cebu, in Barangay Lahug, Cebu City."
    )


async def test_coords_arg_uses_current_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    """coords_arg returns the detected/pinned coordinates."""
    # Default venue
    assert venue.coords_arg({}) == "10.3225319,123.8988885"

    # Pin overrides
    coords = venue.coords_arg({"VOICE_AGENT_HOME_COORDS": "10.2, 123.8"})
    assert coords == "10.2, 123.8"


def test_web_info_command_uses_coords_arg() -> None:
    """web_info_command includes --coords from coords_arg."""
    from voice_pipeline.agent_tools import web_info_command

    # Default venue has coords
    cmd = web_info_command({})
    assert "--coords" in cmd
    coords_idx = cmd.index("--coords")
    assert cmd[coords_idx + 1] == "10.3225319,123.8988885"

    # Pin overrides coords
    cmd = web_info_command({"VOICE_AGENT_HOME_COORDS": "10.2, 123.8"})
    assert cmd[cmd.index("--coords") + 1] == "10.2, 123.8"


def _two_services(ipwho: dict[str, Any] | int, ipapi: dict[str, Any] | int) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return httpx.Response(200, json=NOMINATIM_MANILA)
        body = ipwho if "ipwho" in str(request.url) else ipapi
        return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def _ph(lat: float, lon: float, city: str) -> dict[str, Any]:
    return {"country_code": "PH", "latitude": lat, "longitude": lon, "city": city}


async def test_two_services_that_disagree_keep_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cebu City from one service, Iloilo City from the other (2026-11-13): neither is believed."""
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "1")
    client = _two_services(_ph(14.5995, 120.9842, "Manila"), _ph(10.6969, 122.5644, "Iloilo City"))
    await venue.resolve(client)
    assert "University of the Philippines Cebu" in venue.spoken()


async def test_one_service_alone_is_not_believed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_AGENT_GEOIP", "1")
    client = _two_services(_ph(14.5995, 120.9842, "Manila"), 500)
    await venue.resolve(client)
    assert "University of the Philippines Cebu" in venue.spoken()
