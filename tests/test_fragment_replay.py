"""Ticket 04: gap reconstruction and latency projection on a hand-checked three-row log."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "fragment_replay.py"
FIXTURE = Path(__file__).parent / "fixtures" / "fragments"
spec = importlib.util.spec_from_file_location("fragment_replay", SCRIPT)
assert spec is not None and spec.loader is not None
fr = importlib.util.module_from_spec(spec)
sys.modules["fragment_replay"] = fr
spec.loader.exec_module(fr)


def p_of(text: str) -> float | None:
    return 0.1 if text.endswith("play") else 0.9


def load() -> "fr.Session":
    (session,) = fr.load_sessions(FIXTURE)
    return session


def test_the_session_is_a_pi_session() -> None:
    assert load().harness == "pi"
    assert fr.harness_of({"agent_url": "ws://127.0.0.1:8781"}) == "laptop"
    assert fr.harness_of({}) == "laptop"


def test_the_gap_is_hold_plus_server_plus_the_guard_delay_minus_the_onset() -> None:
    session = load()
    pause, server = fr.pause_median(session.rows), fr.server_median(session.rows)
    assert (pause, server) == (0.64, 0.56)
    # 0.64 hold + 0.56 server + 0.30 after t_final - 0.30 onset = 1.20 s of silence
    (f,) = fr.fragments_of(session, p_of)
    assert f.kind == "resumed" and f.text == "Can you play"
    assert f.gap_s == pytest.approx(1.20)
    assert f.speech_s == pytest.approx(0.99)  # 3 words x 0.33 s, estimated
    assert f.estimated


def test_the_gap_is_never_shorter_than_the_hold() -> None:
    row = {"interrupt_after_final_ms": 10.0}
    assert fr.reconstruct_gap(row, 0.64, 0.2, 0.3) == pytest.approx(0.64)


def test_a_longer_hold_catches_the_fragment_only_when_it_outlasts_the_gap() -> None:
    (f,) = fr.fragments_of(load(), p_of)
    by_name = {r.name: r for r in fr.RULES}
    assert not fr.fragment_caught(by_name["R0 today (0.6 s)"], f)
    assert not fr.fragment_caught(by_name["R1 fixed 1.0 s"], f)  # 1.20 s >= 1.04
    assert fr.fragment_caught(by_name["R1 fixed 1.2 s"], f)  # 1.20 s < 1.24
    r3 = by_name["R3 dangling or P<0.3, hold 1.2"]
    assert fr.fragment_caught(r3, f)  # P(finished) 0.1: fires, and 1.2 s hold outlasts it
    assert not fr.fragment_caught(by_name["R2 >2 s speech, hold 1.2"], f)  # 0.99 s of speech


def test_the_projection_adds_the_hold_one_to_one_to_the_turns_the_rule_fires_on() -> None:
    turns = fr.turns_of(load(), p_of)
    assert [(t.first_sound_s, t.speech_s) for t in turns] == [
        (1.5, pytest.approx(3.0)),
        (1.0, pytest.approx(1.0)),
    ]
    by_name = {r.name: r for r in fr.RULES}
    assert fr.project(by_name["R0 today (0.6 s)"], turns) == (0, 1.25, pytest.approx(1.45))
    # R1 1.0 s: both turns +0.4 -> 1.9 and 1.4: median 1.65, p90 = 1.4 + 0.9 * 0.5 = 1.85
    assert fr.project(by_name["R1 fixed 1.0 s"], turns) == (
        2,
        pytest.approx(1.65),
        pytest.approx(1.85),
    )
    # R2 >2 s, hold 1.0: only the 3.0 s turn (1.5 -> 1.9): median 1.65, p90 1.85 -- the partial
    # of the first turn ends in "by" (dangling) but R2 does not look at it
    delayed, median, p90 = fr.project(by_name["R2 >2 s speech, hold 1.0"], turns)
    assert (delayed, median, p90) == (1, pytest.approx(1.45), pytest.approx(1.81))
    # R3 hold 1.0: "can you play something by" ends in a dangling word: that turn only
    assert fr.project(by_name["R3 dangling or P<0.3, hold 1.0"], turns)[0] == 1
