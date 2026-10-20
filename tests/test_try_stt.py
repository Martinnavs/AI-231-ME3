"""scripts/try_stt.py: the pause detector that stands in for the Pi's VAD."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "try_stt.py"


@pytest.fixture
def ts(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("try_stt", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "try_stt", mod)
    spec.loader.exec_module(mod)
    return mod


def run(det, levels: list[float]) -> list[tuple[int, bool, bool]]:  # type: ignore[no-untyped-def]
    return [(i, *det.feed(rms)) for i, rms in enumerate(levels)]


def pauses(det, levels: list[float]) -> list[int]:  # type: ignore[no-untyped-def]
    return [i for i, _, pause in run(det, levels) if pause]


def test_a_pause_after_speech_ends_the_turn(ts: types.ModuleType) -> None:
    det = ts.PauseDetector(hold_s=0.5)
    levels = [0.005] * 10 + [0.08] * 8 + [0.005] * 10
    assert pauses(det, levels) == [22]  # five quiet chunks after the last loud one


def test_steady_hum_above_the_old_fixed_bar_is_not_speech(ts: types.ModuleType) -> None:
    det = ts.PauseDetector(hold_s=0.5)
    # a noisy room at 0.03 RMS (the old fixed bar was 0.02) with speech bursts at 0.12
    levels = [0.03] * 12 + [0.12] * 6 + [0.03] * 12
    assert len(pauses(det, levels)) == 1


def test_nothing_is_reported_before_the_room_is_measured(ts: types.ModuleType) -> None:
    det = ts.PauseDetector(hold_s=0.5)
    assert [v for _, v, _ in run(det, [0.5] * 10)] == [False] * 10


def test_speech_that_never_stops_never_pauses(ts: types.ModuleType) -> None:
    det = ts.PauseDetector(hold_s=0.5)
    assert pauses(det, [0.005] * 10 + [0.08] * 40) == []


def test_a_short_gap_inside_a_sentence_does_not_end_the_turn(ts: types.ModuleType) -> None:
    det = ts.PauseDetector(hold_s=0.5)
    levels = [0.005] * 10 + [0.08] * 5 + [0.005] * 3 + [0.08] * 5 + [0.005] * 8
    assert len(pauses(det, levels)) == 1


def test_two_turns_give_two_pauses(ts: types.ModuleType) -> None:
    det = ts.PauseDetector(hold_s=0.3)
    levels = [0.005] * 10 + [0.08] * 5 + [0.005] * 6 + [0.08] * 5 + [0.005] * 6
    assert len(pauses(det, levels)) == 2


def test_a_hold_of_zero_never_fires(ts: types.ModuleType) -> None:
    det = ts.PauseDetector(hold_s=0)
    assert pauses(det, [0.005] * 10 + [0.08] * 5 + [0.005] * 20) == []


def test_the_timing_note_says_who_ended_the_turn(ts: types.ModuleType) -> None:
    server_ended = {"finalize_sent": None, "last_voiced": 9.0}
    assert "server's own end-of-turn" in ts.timing_note(server_ended, 0.5, 10.0)
    client_ended = {"finalize_sent": 10.0, "last_voiced": 9.4}
    note = ts.timing_note(client_ended, 0.5, 10.4)
    assert "turn ended by this client after 500 ms" in note and "400 ms later" in note
    spoke_again = {"finalize_sent": 10.0, "last_voiced": 10.2}
    assert "already speaking again" in ts.timing_note(spoke_again, 0.5, 10.4)


def test_the_final_shows_when_parakeet_replaced_the_streamed_text(
    ts: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    msg = {
        "text": "Can you tell me about Nietzsche?",
        "raw_text": "Can you tell me about Nietzsche?",
    }
    ts.show_final(msg, "", streamed="can you tell me about nitch")
    out = capsys.readouterr().out
    assert "Kroko had streamed: 'can you tell me about nitch'" in out
    assert "name repair: not applied" in out


def test_the_final_says_so_when_the_two_engines_agree(
    ts: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    msg = {"text": "Hello, how are you?", "raw_text": "Hello, how are you?"}
    ts.show_final(msg, "", streamed="hello how are you")
    out = capsys.readouterr().out
    assert "same words as the streamed text" in out and "Kroko had streamed" not in out


def test_a_finalize_the_server_never_answered_is_ignored(ts: types.ModuleType) -> None:
    stale = {"finalize_sent": 1.0, "last_voiced": 0.5}
    assert "server's own end-of-turn" in ts.timing_note(stale, 0.5, 20.0)


def test_audio_recorded_while_the_server_answers_goes_to_the_next_turn(
    ts: types.ModuleType,
) -> None:
    buf = bytearray(b"A" * 100)  # the turn
    cut = len(buf)
    buf += b"B" * 20  # the user keeps talking while the server finalises
    assert ts.take_turn_audio(buf, cut, answered_finalize=True) == b"A" * 100
    assert bytes(buf) == b"B" * 20


def test_a_final_the_server_produced_on_its_own_takes_everything(ts: types.ModuleType) -> None:
    buf = bytearray(b"A" * 100 + b"B" * 20)
    assert ts.take_turn_audio(buf, None, answered_finalize=False) == b"A" * 100 + b"B" * 20
    assert not buf
    # an unanswered finalize left a cut behind: it must not split the next server-made final
    buf = bytearray(b"C" * 50)
    assert ts.take_turn_audio(buf, 30, answered_finalize=False) == b"C" * 50
