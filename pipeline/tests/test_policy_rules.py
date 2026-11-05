"""W: negation, a long "Sorry", keyword gates, unknown places, the referent prompt line."""

from pathlib import Path

import pytest

from voice_pipeline import call_policy, place_check
from voice_pipeline.router import spec

AVAILABLE = {"smart_home", "weather", "web_answer", "get_current_time", "open_app"}


@pytest.fixture(autouse=True)
def no_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(call_policy, "known_apps", lambda: [])


def reason(lines: str, user: str) -> str | None:
    return call_policy.decide(spec.parse_output(lines), user, AVAILABLE).reason


# ---- W1 negation


@pytest.mark.parametrize(
    ("lines", "user"),
    [
        ("LIGHT_OFF", "Don't turn off the lights."),
        ("LIGHT_OFF", "Do not turn off the lights."),
        ("STOP", "I don't want music right now."),
        ("CALL|Anna", "Don't call my sister."),
        ("LIGHT_ON", "Never turn on the lights."),
        ("LIGHT_OFF", "No need to turn off the lights."),
        ("LIGHT_OFF", "Huwag mong patayin ang ilaw."),
        ("LIGHT_OFF", "'Wag patayin ang ilaw."),
        ("LIGHT_OFF", "Wag mong patayin ang ilaw."),
        ("CALL|Anna", "Stop calling my sister."),
    ],
)
def test_a_home_action_on_a_negated_turn_goes_to_gemini(lines: str, user: str) -> None:
    assert (reason(lines, user) or "").startswith("negation")


@pytest.mark.parametrize(
    ("lines", "user"),
    [
        ("STOP", "Stop."),
        ("STOP", "Stop the music."),
        ("STOP", "Stop playing the music."),
        ("PAUSE", "Stop playing for a bit."),
        ("LIGHT_ON", "Never mind, turn on the lights."),
        ("LIGHT_OFF", "Turn off the lights."),
        ("TIMER|5 minutes", "Set a timer for five minutes."),
    ],
)
def test_bare_stop_and_ordinary_commands_are_not_negation(lines: str, user: str) -> None:
    assert reason(lines, user) is None


# ---- W2 a long "Sorry"

SORRY = "none|Sorry, I didn't catch that."


def test_sorry_stays_with_the_router_on_a_blip_and_goes_to_gemini_on_a_sentence() -> None:
    assert reason(SORRY, "Mumble.") is None
    assert reason(SORRY, "Tawagan mo si.") is None  # 3 words
    assert reason(SORRY, "Tawagan mo si mama.") == "sorry on a sentence"  # 4 words
    assert reason(SORRY, "No, I mean the road.") == "sorry on a sentence"
    assert reason(SORRY, "Gisingin mo ako ng alas sais.") is not None  # 6 words
    assert reason("none|Okay.", "Thanks a lot.") is None  # other chat unchanged


# ---- W3 keyword gates


@pytest.mark.parametrize(
    ("lines", "user"),
    [
        ("weather", "Ano ang oras ngayon?"),
        ("weather", "The sky is blue."),
        ("weather|water at sea level", "What is the boiling point in Fahrenheit?"),
        ("get_current_time|Asia/Manila", "Fall Anna."),
        ("LIGHT_OFF", "Dim the lights."),
        ("LIGHT_OFF", "I'm going to dim it."),
        ("TEMPERATURE|50 degrees", "Set it to fifty."),
        ("NEXT", "Go back."),
        ("web_answer|What is the current time in Cebu?", "Anong lagay ng panahon sa Cebu?"),
    ],
)
def test_a_call_without_a_word_that_asks_for_it_goes_to_gemini(lines: str, user: str) -> None:
    assert reason(lines, user) is not None


@pytest.mark.parametrize(
    ("lines", "user"),
    [
        ("weather|Manila", "What's the weather in Manila?"),
        ("weather|Cebu", "Anong lagay ng panahon sa Cebu? Ulan ba?"),
        ("weather|Cebu", "Panahon sa Cebu."),
        ("weather|Baguio|tomorrow", "And tomorrow?"),
        ("weather|Baguio|tomorrow", "What about tomorrow?"),
        ("weather|Cebu", "And in Cebu?"),
        ("get_current_time", "What time is it?"),
        ("get_current_time", "Ano ang oras ngayon?"),
        ("get_current_time|Europe/London", "And in London?"),
        ("LIGHT_OFF", "Turn off the lights."),
        ("LIGHT_OFF", "Patayin mo ang ilaw."),
        ("LIGHT_OFF", "Kill the lights."),
        ("LIGHT_OFF", "Lights out."),
        ("TEMPERATURE|24 degrees", "Set the temperature to twenty four degrees."),
        ("TEMPERATURE|24 degrees", "Make it twenty four degrees."),
        ("NEXT", "Skip this song."),
        ("NEXT", "Next song."),
        ("NEXT", "Susunod na kanta."),
        ("web_answer|What is the time zone of Cebu?", "What time zone is Cebu in?"),
    ],
)
def test_the_gates_let_the_real_requests_through(lines: str, user: str) -> None:
    assert reason(lines, user) is None


# ---- W4 unknown place


def test_known_places_are_exact_or_near_a_name_in_the_entity_list() -> None:
    for place in ("Manila", "Cebu", "Davao", "Tokyo", "Dumagete", "Bagio", "Metro Manila", ""):
        assert place_check.known_place(place), place
    assert place_check.known_place("Davao City") and place_check.known_place("Baguio City")
    for place in ("Lisbon", "Naga", "Lima", "Banglok", "Nairobi", "Dumagati"):
        assert not place_check.known_place(place), place
    assert place_check.NEAR_MAX == 0.10


def test_the_weather_call_for_an_unknown_place_goes_to_gemini() -> None:
    assert reason("weather|Lisbon", "What's the weather in Lisbon?") == "unknown place"
    assert reason("weather|Manila", "What's the weather in Manila?") is None
    assert reason("weather||tomorrow", "What's the weather tomorrow?") is None  # home


def test_without_a_matcher_the_router_keeps_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(place_check, "_matcher", None)
    monkeypatch.setattr(place_check, "_failed", True)
    assert place_check.known_place("Lisbon")


# ---- W5 prompt


def test_the_prompt_asks_instead_of_guessing_a_referent() -> None:
    text = (Path(__file__).parents[1] / "config/eval/system_agent.txt").read_text()
    assert "no referent" in text and "ask what it refers to" in text and "do not guess" in text
