"""fast_path: exact phrasings of common home commands, answered without the LLM."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from voice_pipeline import fast_path
from voice_pipeline.fast_path import Match, clock, match, number, spoken_result

PROMPTS = Path(__file__).resolve().parents[1] / "config" / "eval" / "agent_prompts.jsonl"


@pytest.mark.parametrize(
    ("said", "tool", "args", "say"),
    [
        ("Turn on the lights.", "smart_home", {"intent": "LIGHT_ON"}, "The lights are on."),
        ("Please turn the lights off, thanks", "smart_home", {"intent": "LIGHT_OFF"},
         "The lights are off."),
        ("Kill the lights!", "smart_home", {"intent": "LIGHT_OFF"}, "The lights are off."),
        ("Play some music.", "smart_home", {"intent": "PLAY_MUSIC"}, "Playing music."),
        ("Pause the music.", "smart_home", {"intent": "PAUSE"}, "Paused."),
        ("Stop the music.", "smart_home", {"intent": "STOP"}, "Music stopped."),
        ("Skip this song", "smart_home", {"intent": "NEXT"}, "Skipping to the next one."),
        ("Turn it up.", "smart_home", {"intent": "VOLUME_UP"}, "Turning it up."),
        ("Lower the volume.", "smart_home", {"intent": "VOLUME_DOWN"}, "Turning it down."),
        ("Set a timer for ten minutes.", "smart_home",
         {"intent": "TIMER", "slot": "10 minutes"}, "Timer set for 10 minutes."),
        ("Timer for forty five seconds", "smart_home",
         {"intent": "TIMER", "slot": "45 seconds"}, "Timer set for 45 seconds."),
        ("Set a timer for 1 hour.", "smart_home", {"intent": "TIMER", "slot": "1 hour"},
         "Timer set for 1 hour."),
        ("Set an alarm for 6:30 AM.", "smart_home", {"intent": "ALARM", "slot": "6:30 AM"},
         "Alarm set for 6:30 AM."),
        ("Wake me up at seven pm", "smart_home", {"intent": "ALARM", "slot": "7:00 PM"},
         "Alarm set for 7:00 PM."),
        ("Set the speaker volume to 40 percent.", "set_volume", {"percent": 40}, None),
        ("Set the volume to twenty percent", "set_volume", {"percent": 20}, None),
    ],
)  # fmt: skip
def test_the_listed_phrasings_match(
    said: str, tool: str, args: dict[str, object], say: str | None
) -> None:
    assert match(said) == Match(tool, args, say)


@pytest.mark.parametrize(
    "said",
    [
        "Turn on the lights and play some music.",  # two commands: the LLM
        "Can you turn on the lights?",  # a question
        "Turn on the kitchen lights.",  # not an exact phrasing
        "Make it warmer.",
        "Set the lights to purple.",
        "Set a timer.",  # no value
        "Set a timer for a bit.",
        "Set an alarm for later.",
        "Set the volume to 150 percent.",  # out of range
        "Stop.",  # a stop word: doc_chat's job
        "What time is it in Paris?",  # another city: the time tool
        "",
    ],
)
def test_everything_else_goes_to_the_llm(said: str) -> None:
    assert match(said) is None


def test_numbers_and_clock_times() -> None:
    assert [number(w) for w in ("7", "ten", "forty five", "ninety", "a hundred")] == [
        7, 10, 45, 90, 100,
    ]  # fmt: skip
    assert number("a bit") is None and number("twenty twenty") is None
    assert clock("6:30 am") == "6:30 AM" and clock("seven pm") == "7:00 PM"
    assert clock("six thirty am") == "6:30 AM"
    assert clock("13 pm") is None and clock("later") is None


def test_what_is_said_after_the_tool() -> None:
    lights = Match("smart_home", {"intent": "LIGHT_ON"}, "The lights are on.")
    assert spoken_result(lights, {"ok": True, "message": "recorded"}) == "The lights are on."
    assert spoken_result(lights, {"ok": False, "message": "home dashboard unreachable"}) == (
        "Sorry, that didn't work: home dashboard unreachable."
    )
    volume = Match("set_volume", {"percent": 40}, None)
    assert spoken_result(volume, "The speaker volume is now 40 percent.") == (
        "The speaker volume is now 40 percent."
    )


def test_on_the_dry_run_prompts_every_match_is_the_expected_call() -> None:
    matched = 0
    for line in PROMPTS.read_text().splitlines():
        prompt = json.loads(line)
        if len(prompt["turns"]) != 1:
            continue
        found = match(prompt["turns"][0])
        if found is None:
            continue
        matched += 1
        expect = prompt["expect"][0]
        assert len(expect) == 1, prompt["id"]
        if found.tool == "clock":
            continue  # answered from the clock; the dry run still expects the time tool
        if found.tool == "smart_home":
            assert expect[0].get("intent") == found.args["intent"], prompt["id"]
            assert (expect[0].get("slot") or None) == found.args.get("slot"), prompt["id"]
        else:
            assert expect[0].get("tool") == found.tool, prompt["id"]
    assert matched >= 8  # the canonical commands are covered


@pytest.mark.parametrize(
    ("said", "what"),
    [
        ("What time is it?", "time"),
        ("What's the time?", "time"),
        ("Please tell me the time", "time"),
        ("What's today's date?", "date"),
        ("What day is it?", "date"),
        ("what is the date today", "date"),
    ],
)
def test_clock_questions_are_answered_locally(said: str, what: str) -> None:
    found = match(said)
    assert found is not None and found.tool == "clock" and found.args == {"what": what}


@pytest.mark.parametrize(
    "said",
    ["What time is it in Tokyo?", "What time is it in London?", "What time does the shop open?"],
)
def test_other_cities_and_other_questions_still_go_to_the_model(said: str) -> None:
    assert match(said) is None


def test_what_the_clock_says() -> None:
    from datetime import datetime

    from voice_pipeline.fast_path import local_answer

    at = datetime(2026, 10, 26, 9, 5)
    assert local_answer(match("what time is it"), at) == "It's 9:05 AM."  # type: ignore[arg-type]
    assert local_answer(match("what time is it"), datetime(2026, 10, 26, 0, 7)) == "It's 12:07 AM."  # type: ignore[arg-type]
    assert local_answer(match("what time is it"), datetime(2026, 10, 26, 15, 30)) == "It's 3:30 PM."  # type: ignore[arg-type]
    assert local_answer(match("what day is it"), at) == "Today is Monday, October 26."  # type: ignore[arg-type]


def test_the_preloaded_phrase_list_matches_the_fixed_confirmations() -> None:
    """doc_chat plays these from memory (config/fast_phrases.json): the file lists exactly them."""
    from voice_pipeline.fast_path import FIXED

    listed = json.loads((PROMPTS.parents[3] / "config" / "fast_phrases.json").read_text())[
        "phrases"
    ]
    from voice_pipeline.agent_server import LOOKUP_SAY

    assert sorted(listed) == sorted({confirm for _intent, confirm in FIXED.values()} | {LOOKUP_SAY})


def test_what_a_question_to_the_clock_wants() -> None:
    ask = fast_path.clock_question
    assert ask("What's the day's date?") == "date"
    assert ask("What day of the week is it in Tokyo?") == "date"
    assert ask("What month is it?") == "date"
    assert ask("What's the date and time?") == "both"
    assert ask("What time is it in London?") == "time"
    assert ask("whatever") == "time"


def test_the_clock_answers_the_date_the_time_or_both() -> None:
    now = datetime(2026, 11, 3, 14, 8, tzinfo=ZoneInfo(fast_path.TIMEZONE))
    said = {w: fast_path.local_answer(fast_path.Match("clock", {"what": w}, None), now)
            for w in ("time", "date", "both")}  # fmt: skip
    assert said["time"] == "It's 2:08 PM."
    assert said["date"] == "Today is Tuesday, November 3."
    assert said["both"] == "It's 2:08 PM. Today is Tuesday, November 3."


def test_where_are_we_is_answered_with_the_venue_and_no_llm() -> None:
    from voice_pipeline import fast_path

    for said in (
        "Where are we right now?",
        "Can you tell me where we are right now?",
        "where am I",
        "What city are we in?",
    ):
        found = fast_path.match(said)
        assert found is not None and found.tool == "clock", said
        assert fast_path.local_answer(found, datetime(2026, 11, 12, 9, 0)) == (
            f"We're in {fast_path.HOME_PLACE}."
        )
    assert fast_path.match("where are we in the build") is None  # only the exact phrasings
