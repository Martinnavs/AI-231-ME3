"""scripts/score_stress.py: order-free matching, number words, greeting replies, extras."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "score_stress.py"


@pytest.fixture
def ss(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("score_stress", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "score_stress", mod)
    spec.loader.exec_module(mod)
    return mod


def test_number_words_become_digits(ss) -> None:  # type: ignore[no-untyped-def]
    assert ss.digits_for_words("It is one hundred sixty-eight.") == "It is 168 ."
    assert ss.digits_for_words("one hundred and five") == "105"
    assert ss.digits_for_words("seventy five") == "75"
    assert ss.digits_for_words("two thousand twenty three") == "2023"
    assert ss.digits_for_words("ten minutes") == "10 minutes"
    assert ss.digits_for_words("hello there") == "hello there"


def test_matching_is_by_text_not_order(ss) -> None:  # type: ignore[no-untyped-def]
    lines = ["Set a timer.", "Call Gary.", "What is five plus five?"]
    qs = ["What is five plus five?", "Please call Gary", "Set a timer", "Set a timer"]
    turn_of, extras = ss.match_turns(qs, lines)
    assert turn_of == {0: 2, 1: 1, 2: 0}
    assert extras == [3]


def _write(path: Path, rows: list[dict]) -> None:  # type: ignore[type-arg]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_scores_a_shuffled_session(
    ss, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:  # type: ignore[no-untyped-def]
    script = tmp_path / "script.jsonl"
    _write(
        script,
        [
            {"id": 1, "say": "Good morning.", "route": "router"},
            {"id": 2, "say": "What's fourteen times twelve?", "text_re": "168"},
            {"id": 3, "say": "Call Gary.", "tools_forbid": ["CALL"]},
            {"id": 4, "say": "Turn on the kitchen lights.", "intents_any": ["LIGHT_ON"]},
        ],
    )
    sess = tmp_path / "sess"
    sess.mkdir()
    turns = [
        ("What's fourteen times twelve?", "It's one hundred sixty-eight.", []),
        ("Good morning.", "Good morning!", []),
        ("Call Gary.", "Gary is not in the phonebook.", []),
        ("Tell me a joke about cats.", "Cats are liquid.", []),
    ]
    _write(
        sess / "turns.jsonl", [{"turn": n + 1, "question": q} for n, (q, _, _) in enumerate(turns)]
    )
    _write(
        sess / "agent.jsonl",
        [
            {"question": q, "text": t, "calls": c, "routed": True, "router_lines": "none"}
            for q, t, c in turns
        ],
    )
    monkeypatch.setattr(ss, "SCRIPT", script)
    monkeypatch.setattr(sys, "argv", ["x", str(sess), str(sess / "agent.jsonl")])
    assert ss.main() == 0
    out = capsys.readouterr().out
    assert " 1 PASS" in out
    assert " 2 PASS" in out
    assert " 3 PASS" in out
    assert " 4 NOT RUN" in out
    assert "3 pass" in out and "1 not run" in out
    assert "PARROTED" not in out
    extras = out.split("matched no script line")[1]
    assert "Tell me a joke about cats" in extras and "Cats are liquid" in extras
