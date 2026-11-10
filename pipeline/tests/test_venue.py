"""The venue: the barangay looked up once at boot, and how "where are we" words it."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from voice_pipeline import fast_path, venue

NOMINATIM = {
    "address": {
        "quarter": "Banawa",
        "suburb": "Guadalupe",
        "city": "Cebu City",
        "country": "Philippines",
    }
}


@pytest.fixture(autouse=True)
def _fresh_venue(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("VOICE_AGENT_HOME_COORDS", raising=False)
    before = venue.current
    venue.current = venue.Venue("Cebu City")
    yield
    venue.current = before


def _client(body: Any, seen: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def test_the_barangay_is_the_suburb_before_the_quarter() -> None:
    assert venue.barangay_from(NOMINATIM["address"]) == "Guadalupe"
    assert venue.barangay_from({"quarter": "Banawa"}) == "Banawa"
    assert venue.barangay_from({"city": "Cebu City"}) is None


async def test_the_city_centre_gives_an_around_answer() -> None:
    seen: list[httpx.Request] = []
    await venue.resolve(_client(NOMINATIM, seen))
    assert seen[0].url.params["lat"] == "10.3157" and seen[0].url.params["zoom"] == "14"
    assert venue.spoken() == "We're around Barangay Guadalupe, Cebu City."
    assert venue.spoken(asked_barangay=True) == venue.spoken()
    line = venue.prompt_line()
    assert line is not None and "around Barangay Guadalupe" in line


async def test_given_coordinates_are_used_and_the_answer_says_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_AGENT_HOME_COORDS", "10.2980, 123.9000")
    seen: list[httpx.Request] = []
    await venue.resolve(_client(NOMINATIM, seen))
    assert (seen[0].url.params["lat"], seen[0].url.params["lon"]) == ("10.298", "123.9")
    assert venue.spoken() == "We're in Barangay Guadalupe, Cebu City."


@pytest.mark.parametrize("body", [503, {"address": {}}, {"error": "Unable to geocode"}])
async def test_a_failed_lookup_leaves_the_city_answer(body: Any) -> None:
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
    await venue.resolve(_client(NOMINATIM))
    for said in ("Which barangay am I in?", "Can you tell me what barangay we are in?"):
        found = fast_path.match(said)
        assert found is not None and found.tool == "clock", said
        assert "Barangay Guadalupe" in fast_path.local_answer(found, None)  # type: ignore[arg-type]


def test_the_venue_reaches_the_web_info_child_on_its_command_line() -> None:
    from voice_pipeline.agent_tools import web_info_command

    plain = web_info_command({})
    assert plain[-2:] == ["--home", "Cebu City"]
    pinned = web_info_command(
        {"VOICE_AGENT_HOME_PLACE": "Mandaue", "VOICE_AGENT_HOME_COORDS": "10.33,123.94"}
    )
    assert pinned[-4:] == ["--home", "Mandaue", "--coords", "10.33,123.94"]
