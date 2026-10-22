"""The live barge-in pilot: the gate's table from ticket 12, the player, and a simulated session."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from doc_chat import pi_check as pc
from doc_chat import pi_pilot as pp

FIX = Path("tests/fixtures/pi_echo")
CLIPS = sorted(FIX.glob("reply-*.wav"))
SILERO = "assets/silero_vad.onnx"


def run_gate(windows_of_speech: int, then_quiet: int = 3) -> list[str]:
    gate = pp.Gate()
    out = []
    for conf in [0.95] * windows_of_speech + [0.05] * then_quiet:
        ev = gate.step(conf, speaking=True)
        if ev:
            out.append(ev)
    return out


@pytest.mark.parametrize("windows", [3, 6, 7])  # 96, 192, 224 ms: under the 250 ms gate
def test_short_speech_ducks_then_restores(windows: int) -> None:
    assert run_gate(windows) == ["duck", "restore"]


@pytest.mark.parametrize("windows", [8, 13])  # 256 ms and 416 ms
def test_long_speech_ducks_then_interrupts_once(windows: int) -> None:
    assert run_gate(windows) == ["duck", "interrupt"]


def test_gate_is_silent_while_the_assistant_is_not_speaking() -> None:
    gate = pp.Gate()
    assert [gate.step(0.99, speaking=False) for _ in range(20)] == [None] * 20


def test_a_dropped_window_resets_the_count() -> None:
    gate = pp.Gate()
    events = [gate.step(c, True) for c in [0.9] * 5 + [0.1] + [0.9] * 5]
    assert events.count("interrupt") == 0


def test_assistant_ducks_15_db_then_fades_out_within_15_ms() -> None:
    rate = 16000
    tone = (0.5 * np.sin(2 * np.pi * 440 * np.arange(rate * 3) / rate)).astype(np.float32)
    a = pp.Assistant({"tone": tone}, rate, lead_s=0.0, gap_s=(0.0, 0.0))
    a.render(160, 0.0)  # starts the clip
    assert a.speaking
    a.command("duck", 0.0, 0.0)
    a.render(160, 0.01)
    block = a.render(160, 0.02)
    ratio = float(np.abs(block).max() / 0.5)
    assert ratio == pytest.approx(10 ** (-15 / 20), abs=0.03)
    a.command("interrupt", 0.0, 0.0)
    a.render(480, 0.03)
    assert a.state == "waiting"
    stopped = next(e for e in a.events if e["kind"] == "stopped")
    applied = next(e for e in a.events if e["kind"] == "interrupt")
    assert stopped["t"] - applied["t_applied"] <= 0.016


@pytest.mark.skipif(not CLIPS, reason="run scripts/make_pi_clips.py")
def test_simulated_session_detects_the_talker_and_writes_files(tmp_path: Path) -> None:
    rate = 48000
    speech = pc.resample(pc.read_wav(CLIPS[1]), pc.SR, rate)
    starts = [8.0, 17.0]

    def talker(t: float) -> np.ndarray:
        out = np.zeros(rate // 50, np.float32)
        for s in starts:
            lo = int((t - s) * rate)
            if 0 <= lo < len(speech):
                chunk = speech[lo : lo + len(out)]
                out[: len(chunk)] += chunk
        return out

    hold = pp.KEY_CODES["RIGHTCTRL"]
    dur = len(speech) / rate
    keys = pp.ScriptedKeys(
        [e for s in starts for e in (pp.KeyEvent(s - 0.1, hold, 1), pp.KeyEvent(s + dur, hold, 0))]
    )
    clips = {p.name: pc.read_wav(p) for p in CLIPS}
    pilot = pp.Pilot(
        rate, None, None, 2, keys, SILERO, pp.Assistant(clips, rate),
        stream_factory=pc.simulated_stream_factory(echo_db=-200.0, talker=talker),
        threaded=False,
    )  # fmt: skip
    pilot.run(24.0)
    pilot.save(tmp_path, {"label": "t"})
    res = pp.analyze(tmp_path)
    assert res["holds"] == 2
    assert res["interrupted"] == 2
    assert 0.2 < res["interrupt_latency_s"]["median"] < 0.8
    assert res["false_counts"] == {"duck": 0, "interrupt": 0}
    for name in ("mic.wav", "events.json", "conf.json", "run.json"):
        assert (tmp_path / name).exists()


@pytest.mark.skipif(not CLIPS, reason="run scripts/make_pi_clips.py")
def test_a_silent_room_never_fires_the_gate(tmp_path: Path) -> None:
    rate = 48000
    clips = {p.name: pc.read_wav(p) for p in CLIPS}
    pilot = pp.Pilot(
        rate, None, None, 2, pp.ScriptedKeys([]), SILERO, pp.Assistant(clips, rate),
        stream_factory=pc.simulated_stream_factory(echo_db=-200.0),
        threaded=False,
    )  # fmt: skip
    pilot.run(20.0)
    pilot.save(tmp_path, {"label": "t"})
    res = pp.analyze(tmp_path)
    assert res["false_counts"] == {"duck": 0, "interrupt": 0}
    assert res["assistant_speech_min"] > 0.1


def test_calibration_offsets_from_a_synthetic_run(tmp_path: Path) -> None:
    import json

    hold = pp.KEY_CODES["RIGHTCTRL"]
    t = [round(0.032 * (i + 1), 3) for i in range(300)]
    conf = [0.9 if 1.5 <= x - 0.032 <= 2.5 else 0.05 for x in t]  # speech starts at 1.5 s
    (tmp_path / "run.json").write_text(json.dumps({"hold_key": hold, "noise_key": 49}))
    (tmp_path / "events.json").write_text(json.dumps([
        {"kind": "key", "code": hold, "value": 1, "t": 1.2},
        {"kind": "key", "code": hold, "value": 0, "t": 2.6},
    ]))  # fmt: skip
    (tmp_path / "conf.json").write_text(
        json.dumps({"t": t, "conf": conf, "speaking": [False] * 300})
    )
    cal = pp.key_to_voice_offsets(tmp_path)
    assert cal["n"] == 1
    assert cal["median_s"] == pytest.approx(0.3, abs=0.05)


def test_key_chatter_is_one_hold() -> None:
    # the pattern seen on the real keyboard: release/press pairs 10 ms apart while a key is held
    code = pp.KEY_CODES["RIGHTCTRL"]
    stamps = [
        (22.8, 1),
        (23.02, 0),
        (23.03, 1),
        (23.17, 0),
        (23.18, 1),
        (23.3, 0),
        (23.31, 1),
        (25.0, 0),
    ]
    events = [{"kind": "key", "code": code, "value": v, "t": t} for t, v in stamps]
    events += [{"kind": "key", "code": code, "value": v, "t": t} for t, v in [(30.0, 1), (31.0, 0)]]
    assert pp._holds(events, code) == [(22.8, 25.0), (30.0, 31.0)]


def run_kofn(pattern: list[float]) -> list[str]:
    gate = pp.Gate(pp.GateConfig(confidence=0.5, rule="kofn", k=7, n=10))
    return [ev for c in pattern if (ev := gate.step(c, speaking=True))]


def test_kofn_interrupts_despite_dips() -> None:
    # 9 speech windows with two dips inside: 7 of the last 10 are speech at the 9th window
    assert run_kofn([0.9, 0.9, 0.1, 0.9, 0.9, 0.9, 0.1, 0.9, 0.9])[-1] == "interrupt"


def test_kofn_ignores_a_short_blip() -> None:
    assert "interrupt" not in run_kofn([0.9] * 6 + [0.1] * 6)


def test_kofn_needs_a_speech_window_to_fire() -> None:
    # 7 of 10 are speech but the newest window is a miss: no interrupt on that window
    events = run_kofn([0.9] * 6 + [0.1])
    assert events == ["duck", "restore"]
