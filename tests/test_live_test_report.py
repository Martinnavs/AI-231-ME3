import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "live_test_report", ROOT / "scripts/live_test_report.py"
)
assert spec and spec.loader
report = importlib.util.module_from_spec(spec)
sys.modules["live_test_report"] = report
spec.loader.exec_module(report)

SCRIPT = {
    "about": "x",
    "blocks": [
        {
            "id": "A",
            "title": "Quiet",
            "setup": "s",
            "lines": [
                {"id": "A1", "kind": "ADDRESS", "say": "What time is it?"},
                {"id": "A2", "kind": "SIDE", "say": "Hey Jamie, did you feed the dog already?"},
                {"id": "A3", "kind": "ADDRESS", "say": "How tall is Mount Everest?"},
                {"id": "A4", "kind": "ADDRESS", "say": "Set a timer for two minutes."},
            ],
        },
        {
            "id": "B",
            "title": "Music",
            "setup": "s",
            "lines": [{"id": "B1", "kind": "ADDRESS", "say": "What day is it today?"}],
        },
    ],
}


def turn(text: str, words: int = 10, **extra: Any) -> dict[str, Any]:
    return {"question": text, "answer_words": words, **extra}


def test_lines_are_matched_in_order_despite_misheard_words() -> None:
    turns = [
        turn("what time is it"),
        turn("how tall is mount everist"),
        turn("set a time for two minutes"),
    ]
    found = report.match_lines([ln for b in SCRIPT["blocks"] for ln in b["lines"]], turns)
    assert found == {"A1": 0, "A2": None, "A3": 1, "A4": 2, "B1": None}


def test_status_reads_the_gates_and_the_answer() -> None:
    assert report.status(turn("x")) == "answered"
    assert report.status(turn("x", 0, level_gate={"far": True})) == "gate"
    assert report.status(turn("x", 0, addressee="ignore")) == "text"
    assert report.status(turn("x", 0)) == "silent"


def test_the_report_counts_answers_ignored_side_talk_and_background() -> None:
    turns = [
        turn("What time is it?"),
        turn("Hey Jamie did you feed the dog already", 0, addressee="ignore"),  # ignored: good
        turn(
            "Breaking news tonight in the studio", 0, level_gate={"far": True}
        ),  # background, blocked
        turn(
            "How tall is Mount Everest?",
            0,
            level_gate={"far": True, "turn_db": -30.0, "reference_db": -19.0},
        ),
        turn("Welcome back to the show", 12),  # background answered: a false trigger
        turn("Set a timer for two minutes."),
        turn("What day is it today?"),  # block B
    ]
    blocks = report.evaluate(SCRIPT, turns)
    a = blocks["A"]
    assert [r["status"] for r in a["address"]] == ["answered", "gate", "answered"]
    assert [r["status"] for r in a["side"]] == ["text"]
    assert [r["status"] for r in a["background"]] == ["gate", "answered"]
    text = report.report(blocks)
    assert "addressed answered 2/3" in text and "side ignored 1/1" in text
    assert (
        "A3 should have been ANSWERED, was gate" in text
        and "[level -30.0 vs your -19.0 dBFS]" in text
    )
    assert 'background ANSWERED: "Welcome back to the show"' in text
    assert "ALL  addressed answered 3/4" in text


def test_a_side_line_the_microphone_never_heard_counts_as_ignored() -> None:
    blocks = report.evaluate(SCRIPT, [turn("What time is it?")])
    assert blocks["A"]["side"][0]["status"] == "not heard"
    assert "side ignored 1/1" in report.report(blocks)
