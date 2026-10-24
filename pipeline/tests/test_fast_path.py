"""fast_path: exact phrasings of common home commands, answered without the LLM."""

import json
from pathlib import Path

import pytest

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
        "What time is it?",
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
        if found.tool == "smart_home":
            assert expect[0].get("intent") == found.args["intent"], prompt["id"]
            assert (expect[0].get("slot") or None) == found.args.get("slot"), prompt["id"]
        else:
            assert expect[0].get("tool") == found.tool, prompt["id"]
    assert matched >= 8  # the canonical commands are covered
