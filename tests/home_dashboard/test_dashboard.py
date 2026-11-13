"""The home dashboard: open slot values, the 17 intents, the clock-driven state and the HTTP API."""

import ast
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from home_dashboard import parse
from home_dashboard.server import make_handler
from home_dashboard.state import INTENTS, AppState, CommandError

ROOT = Path(__file__).resolve().parents[2]
CONTACTS = {
    "mom": {"name": "Mom", "relation": "mother"},
    "anna": {"name": "Anna", "relation": "sister"},
}
START = datetime(2026, 11, 13, 14, 0, 0)


def make_state() -> tuple[AppState, list[datetime]]:
    clock = [START]
    return AppState(CONTACTS, now=lambda: clock[0]), clock


@pytest.mark.parametrize(
    ("text", "want"),
    [("40 percent", 40), ("forty percent", 40), ("twenty five", 25), ("100%", 100), ("loud", None)],
)
def test_percent_is_digits_or_words(text: str, want: int | None) -> None:
    assert parse.percent(text) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("45 minutes", 2700),
        ("1 hour 30 minutes", 5400),
        ("an hour", 3600),
        ("half an hour", 1800),
        ("90 seconds", 90),
        ("5:00", 300),
        ("1:30:00", 5400),
        ("ten minutes", 600),
        ("banana", None),
    ],
)
def test_durations_in_any_wording(text: str, want: int | None) -> None:
    assert parse.duration_s(text) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("6:30 AM", (6, 30)),
        ("7pm", (19, 0)),
        ("18:45", (18, 45)),
        ("6.30 p.m.", (18, 30)),
        ("noon", (12, 0)),
        ("12 AM", (0, 0)),
        ("13 PM", None),
        ("x", None),
    ],
)
def test_clock_times_in_any_wording(text: str, want: tuple[int, int] | None) -> None:
    assert parse.clock(text) == want


def test_any_colour_is_accepted_and_unknown_words_show_grey() -> None:
    assert parse.css_color("Warm White") == "#ffd59e"
    assert parse.css_color("#ABC") == "#abc"
    assert parse.css_color("zorp") == parse.UNKNOWN_COLOR
    state, _ = make_state()
    reply = state.command("COLOR", {"COLOR": "Zorp Green"})
    assert reply["ok"] and reply["state"]["lights"]["on"]


def test_the_seventeen_intents_are_the_harness_ones() -> None:
    source = (ROOT / "pipeline/src/voice_pipeline/tools/smart_home.py").read_text()
    harness = ast.literal_eval(
        source.split("INTENTS: tuple[str, ...] = ", 1)[1].split("\n)\n", 1)[0] + "\n)"
    )
    assert tuple(INTENTS) == harness


def test_lights_brightness_temperature_and_their_limits() -> None:
    state, _ = make_state()
    assert (
        state.command("BRIGHTNESS", {"PERCENT": "forty percent"})["state"]["lights"]["brightness"]
        == 40
    )
    assert state.snapshot()["lights"]["on"]
    assert (
        state.command("TEMPERATURE", {"DEGREES": "27 degrees"})["state"]["thermostat"]["degrees"]
        == 27
    )
    for intent, slots in (
        ("TEMPERATURE", {"DEGREES": "90"}),
        ("BRIGHTNESS", {"PERCENT": "150"}),
        ("BRIGHTNESS", {}),
        ("OPEN_DOOR", {}),
    ):
        with pytest.raises(CommandError):
            state.command(intent, slots)
    assert state.snapshot()["indicator"]["history"][0]["ok"] is False
    assert state.snapshot()["indicator"]["status"]


def test_timer_counts_down_and_is_replaced_by_a_new_one() -> None:
    state, clock = make_state()
    timer = state.command("TIMER", {"DURATION": "1 hour 30 minutes"})["state"]["timer"]
    assert (timer["state"], timer["remaining_s"], timer["display"]) == ("running", 5400, "1:30:00")
    clock[0] += timedelta(seconds=90)
    assert state.snapshot()["timer"]["display"] == "1:28:30"
    state.command("TIMER", {"DURATION": "10 seconds"})
    clock[0] += timedelta(seconds=11)
    assert state.snapshot()["timer"]["state"] == "done"
    clock[0] += timedelta(seconds=40)
    assert state.snapshot()["timer"]["state"] == "idle"


def test_alarm_is_set_rings_and_goes_quiet() -> None:
    state, clock = make_state()
    assert state.command("ALARM", {"ALARM_TIME": "2:01 PM"})["state"]["alarm"]["state"] == "set"
    clock[0] += timedelta(seconds=70)
    assert state.snapshot()["alarm"]["state"] == "ringing"
    clock[0] += timedelta(seconds=70)
    assert state.snapshot()["alarm"]["state"] == "idle"
    assert state.command("ALARM", {"ALARM_TIME": "1:00 PM"})["state"]["alarm"]["time"] == "1:00 PM"


def test_phone_needs_a_contact_from_the_phonebook() -> None:
    state, clock = make_state()
    phone = state.command("CALL", {"CONTACT": "mom"})["state"]["phone"]
    assert (phone["state"], phone["contact"], phone["relation"]) == ("calling", "Mom", "mother")
    with pytest.raises(CommandError, match="not in the phonebook"):
        state.command("MESSAGE", {"CONTACT": "Zed"})
    state.command("MESSAGE", {"CONTACT": "Anna"})
    clock[0] += timedelta(seconds=20)
    assert state.snapshot()["phone"]["state"] == "idle"


def test_reminders_are_any_text_newest_first_and_a_double_is_one() -> None:
    state, clock = make_state()
    assert state.command("CREATE_REMINDER", {"TASK": "buy milk"})["changed"]
    assert not state.command("CREATE_REMINDER", {"TASK": "Buy milk"})["changed"]
    clock[0] += timedelta(seconds=90)
    state.command("CREATE_REMINDER", {"TASK": "call the dentist about the crown"})
    reminders = state.command("LIST_REMINDERS", {})["state"]["reminders"]
    assert [r["task"] for r in reminders["items"]] == [
        "call the dentist about the crown",
        "buy milk",
    ]
    assert reminders["expanded"] and reminders["count"] == 2


def test_music_is_not_played_here() -> None:
    state, _ = make_state()
    reply = state.command("PLAY_MUSIC", {"QUERY": "Taylor Swift"})
    assert reply["ok"] and not reply["changed"] and "Spotify" in reply["message"]
    assert state.snapshot()["indicator"]["history"] == []


@pytest.fixture
def api() -> Any:
    state, _ = make_state()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base
    server.shutdown()


def call(url: str, body: dict[str, Any] | None = None, raw: bytes | None = None) -> tuple[int, Any]:
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    request = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_http_command_state_reset_and_errors(api: str) -> None:
    status, reply = call(api + "/api/command", {"intent": "COLOR", "slots": {"COLOR": "purple"}})
    assert status == 200 and reply["state"]["lights"]["css"] == "#8e24aa"
    assert call(api + "/api/state")[1]["lights"]["color"] == "purple"
    status, reply = call(
        api + "/api/command", {"intent": "TEMPERATURE", "slots": {"DEGREES": "90"}}
    )
    assert status == 400 and "10 to 35" in reply["error"]
    assert call(api + "/api/command", raw=b"not json")[0] == 400
    assert call(api + "/nope")[0] == 404
    assert call(api + "/api/reset", {})[1]["state"]["lights"]["on"] is False
    for page in ("/", "/style.css", "/app.js"):
        with urllib.request.urlopen(api + page) as response:
            assert response.status == 200 and len(response.read()) > 100
