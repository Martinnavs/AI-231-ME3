import json
import time

import numpy as np
import pytest
from test_addressee import make_session

from doc_chat.voice_gate import MIN_REF_S, WINDOW_S, LoudnessGate, active_levels


def speech(db: float, seconds: float = 2.0, lead: float = 0.5, seed: int = 0) -> bytes:
    """Noise-like 'speech' at `db` dBFS RMS after `lead` seconds of a very quiet room."""
    rng = np.random.default_rng(seed)
    quiet = rng.normal(0, 10 ** (-60 / 20), int(16000 * lead))
    loud = rng.normal(0, 10 ** (db / 20), int(16000 * seconds))
    x = np.concatenate([quiet, loud, quiet])
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def constant(db: float, seconds: float = 3.0) -> bytes:
    """A turn with no quiet part at all (a TV)."""
    x = np.random.default_rng(1).normal(0, 10 ** (db / 20), int(16000 * seconds))
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def test_the_turn_level_is_the_speech_level_not_the_room() -> None:
    _, level = active_levels(speech(-20))
    assert level is not None and abs(level + 20) < 1.0


def test_a_turn_with_no_quiet_part_still_has_a_level() -> None:
    _, level = active_levels(constant(-35))
    assert level is not None and abs(level + 35) < 2.0


def test_a_very_short_turn_has_no_level() -> None:
    assert active_levels(speech(-20, seconds=0.2, lead=0.0))[1] is None


def test_a_turn_much_quieter_than_the_calibrated_user_is_far_and_the_user_is_not() -> None:
    gate = LoudnessGate(10.0, calibration_db=-19.0)
    assert not gate.check(speech(-19)).far
    assert not gate.check(speech(-27)).far  # 8 dB down: inside the gap
    quiet = gate.check(speech(-33))
    assert quiet.far and quiet.source == "calibration" and quiet.reference_db == -19.0
    assert gate.check(constant(-38)).far  # a TV with no quiet part


def test_without_a_reference_everything_passes() -> None:
    gate = LoudnessGate(10.0)
    verdict = gate.check(speech(-50))
    assert not verdict.far and verdict.source == "none" and verdict.reference_db is None


def test_the_window_replaces_the_calibration_once_it_holds_enough_speech() -> None:
    gate = LoudnessGate(10.0, calibration_db=-19.0)
    gate.accept(speech(-30, seconds=MIN_REF_S + 1))
    ref, source = gate.reference()
    assert source == "window" and ref is not None and abs(ref + 30) < 1.5
    assert not gate.check(speech(-33)).far  # the user is quieter now: the gate followed


def test_a_turn_that_is_checked_but_not_accepted_never_teaches_the_gate() -> None:
    gate = LoudnessGate(10.0, calibration_db=-19.0)
    for _ in range(5):
        assert gate.check(speech(-35)).far  # dropped turns are not accepted
    assert gate.reference() == (-19.0, "calibration")


def test_the_window_keeps_only_the_last_thirty_seconds_of_speech() -> None:
    gate = LoudnessGate(10.0, calibration_db=-19.0)
    for i in range(20):  # 20 x 4 s of speech at -40 dB, then 20 x 4 s at -20 dB
        gate.accept(speech(-40, seconds=4, seed=i))
    for i in range(20):
        gate.accept(speech(-20, seconds=4, seed=i))
    ref, source = gate.reference()
    assert source == "window" and ref is not None and abs(ref + 20) < 1.5
    assert gate._blocks * 0.1 <= WINDOW_S + 4.5


def test_one_very_long_turn_is_cut_to_the_window() -> None:
    gate = LoudnessGate(10.0, window_s=5.0)
    gate.accept(speech(-25, seconds=20))
    assert gate._blocks * 0.1 <= 5.0 + 0.11


def turn(text: str, pcm: bytes):  # type: ignore[no-untyped-def]
    from doc_chat.speech import HeardTurn

    now = time.perf_counter()
    return HeardTurn(text, None, None, now - 0.5, now, now - 0.1, pcm=pcm)


async def test_the_session_drops_a_far_turn_logs_it_and_never_learns_from_it(
    capsys: pytest.CaptureFixture[str], tmp_path: pytest.TempPathFactory
) -> None:
    s, chain, log = make_session(voice_gate_db=10.0, voice_gate_window=30.0)
    s.loudness_gate = LoudnessGate(10.0, calibration_db=-19.0)
    await s.handle(1, turn("Breaking news from the studio tonight.", speech(-34, seconds=3)))
    assert chain.questions == []
    rec = json.loads(log.getvalue())
    assert rec["answer_words"] == 0 and rec["level_gate"]["far"] is True
    assert "much quieter than you" in capsys.readouterr().out
    assert s.loudness_gate.reference() == (-19.0, "calibration")


async def test_the_session_answers_the_user_and_learns_their_level() -> None:
    s, chain, log = make_session(voice_gate_db=10.0, voice_gate_window=30.0)
    s.loudness_gate = LoudnessGate(10.0, calibration_db=-19.0)
    await s.handle(1, turn("Turn on the lights.", speech(-22, seconds=MIN_REF_S + 1)))
    assert chain.questions == ["Turn on the lights."]
    assert json.loads(log.getvalue())["level_gate"]["far"] is False
    assert s.loudness_gate.reference()[1] == "window"


async def test_an_answer_to_a_pending_question_is_never_dropped_for_being_quiet() -> None:
    from doc_chat.dialogue import Clarification

    s, chain, _log = make_session(voice_gate_db=10.0, voice_gate_window=30.0)
    s.loudness_gate = LoudnessGate(10.0, calibration_db=-19.0)
    s.pending = Clarification(kind="ask", question="Which contact was that?")
    await s.handle(1, turn("Call my sister please.", speech(-40, seconds=2)))
    assert chain.questions == ["Call my sister please."]


async def test_without_the_flag_the_gate_does_nothing() -> None:
    s, chain, log = make_session()
    await s.handle(1, turn("Breaking news from the studio tonight.", speech(-50, seconds=3)))
    assert chain.questions == ["Breaking news from the studio tonight."]
    assert "level_gate" not in json.loads(log.getvalue())
