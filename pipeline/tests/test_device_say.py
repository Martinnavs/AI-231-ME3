import json

import pytest

from voice_pipeline.device_say import device_sentence, duration

STATUS = {
    "cpu_temperature_c": 54.5,
    "uptime_minutes": 22,
    "load_1min": 0.35,
    "free_memory_mb": 1730,
    "speaker_connected": True,
}


@pytest.mark.parametrize(
    ("question", "said"),
    [
        ("What's the Pi temperature?", "The Pi is at 54 degrees Celsius."),
        ("How hot is the Pi?", "The Pi is at 54 degrees Celsius."),
        ("Is the speaker connected?", "The speaker is connected."),
        ("How much memory does the Pi have left?", "It has 1.7 gigabytes of memory free."),
        ("How long has the Pi been running?", "It has been up for 22 minutes."),
        (
            "Is the speaker connected and how hot is the Pi?",
            "The Pi is at 54 degrees Celsius, and the speaker is connected.",
        ),
    ],
)
def test_answers_what_was_asked(question, said):
    assert device_sentence(STATUS, question) == said


def test_names_nothing_gives_everything():
    said = device_sentence(STATUS, "How is the Pi doing?")
    assert said is not None
    for part in ("54 degrees", "22 minutes", "1.7 gigabytes", "the speaker is connected"):
        assert part in said


def test_json_text_from_the_mcp_client():
    assert (
        device_sentence(json.dumps(STATUS), "pi temperature") == "The Pi is at 54 degrees Celsius."
    )
    assert device_sentence("[TextContent] " + json.dumps(STATUS), "pi temperature") is not None


def test_speaker_not_connected():
    got = device_sentence({**STATUS, "speaker_connected": False}, "is the speaker connected?")
    assert got == "The speaker is not connected."


@pytest.mark.parametrize("result", [None, "", "not json", [], {}, {"cpu_temperature_c": None}])
def test_nothing_usable_is_none(result):
    assert device_sentence(result, "pi temperature") is None


def test_a_missing_part_is_left_out():
    got = device_sentence({"cpu_temperature_c": 50.0, "speaker_connected": None}, "how is the pi?")
    assert got == "The Pi is at 50 degrees Celsius."


def test_duration():
    assert [duration(m) for m in (1, 22, 60, 125)] == [
        "1 minute",
        "22 minutes",
        "1 hour",
        "2 hours 5 minutes",
    ]


def test_small_memory_in_megabytes():
    assert (
        device_sentence({"free_memory_mb": 512}, "memory") == "It has 512 megabytes of memory free."
    )
