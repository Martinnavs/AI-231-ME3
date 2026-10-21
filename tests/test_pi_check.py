"""The Raspberry Pi echo checks, run against a simulated sound card."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from doc_chat import pi_check as pc

FIX = Path("tests/fixtures/pi_echo")
CLIPS = sorted(FIX.glob("reply-*.wav"))
SILERO = "assets/silero_vad.onnx"

pytestmark = pytest.mark.skipif(not CLIPS, reason="run scripts/make_pi_clips.py")


def duplex(echo_db: float, delay_ms: float = 60.0, talker=None) -> pc.Duplex:  # type: ignore[no-untyped-def]
    return pc.Duplex(
        48000, None, None, 2, pc.simulated_stream_factory(echo_db, delay_ms, talker=talker)
    )


# ---- helpers


def test_wav_roundtrip_and_resampling(tmp_path: Path) -> None:
    x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(16000) / 16000)).astype(np.float32)
    pc.write_wav(tmp_path / "a.wav", x)
    y = pc.read_wav(tmp_path / "a.wav")
    assert len(y) == 16000 and np.abs(x - y).max() < 1e-3
    assert len(pc.resample(x, 16000, 48000)) == 48000
    assert len(pc.read_wav(tmp_path / "a.wav", rate=8000)) == 8000


def test_the_round_trip_delay_is_measured() -> None:
    probe = pc.chirp()
    played = np.concatenate([np.zeros(8000, np.float32), probe])
    recorded = np.zeros(32000, dtype=np.float32)
    recorded[8000 + 1280 : 8000 + 1280 + len(probe)] = 0.2 * probe  # 80 ms later, quieter
    recorded += np.random.default_rng(0).normal(0, 0.002, len(recorded)).astype(np.float32)
    assert pc.find_delay_ms(played, recorded) == pytest.approx(80.0, abs=1.0)


def test_no_delay_is_reported_when_nothing_came_back() -> None:
    probe = pc.chirp()
    noise = np.random.default_rng(1).normal(0, 0.01, 32000).astype(np.float32)
    assert pc.find_delay_ms(probe, noise) is None


# ---- detectors on a recording


def test_silence_trips_nothing() -> None:
    quiet = np.random.default_rng(0).normal(0, 0.003, 16000 * 5).astype(np.float32)
    det = pc.run_detectors(quiet, 0.003, SILERO)
    assert all(v["onsets"] == [] for v in det.values())


def test_a_voice_trips_every_detector() -> None:
    voice = np.concatenate([np.zeros(8000, np.float32), pc.read_wav(CLIPS[0])])
    voice += np.random.default_rng(0).normal(0, 0.003, len(voice)).astype(np.float32)
    det = pc.run_detectors(voice, 0.003, SILERO)
    assert (
        det["energy"]["onsets"] and det["silero_250ms"]["onsets"] and det["silero_150ms"]["onsets"]
    )
    assert det["silero_150ms"]["onsets"][0] <= det["silero_250ms"]["onsets"][0]
    assert det["silero_250ms"]["ms_per_window"] < 32  # faster than real time, with a lot to spare


# ---- the sound card


def test_playback_comes_back_through_the_microphone_with_the_configured_delay() -> None:
    clip = pc.read_wav(CLIPS[1])
    rec, under = duplex(-12.0, delay_ms=60.0).play_and_record(clip, len(clip) / pc.SR + 1.0)
    assert under == 0 and len(rec) >= len(clip)
    delay = pc.find_delay_ms(clip, rec)
    assert delay is not None and 55 <= delay <= 100  # 60 ms + one 20 ms block


# ---- the checks


def test_a_headset_like_leak_is_quiet(tmp_path: Path) -> None:
    res = pc.self_trigger_check(CLIPS, duplex(-60.0), tmp_path, SILERO, floor_s=2.0, save=False)
    assert res["summary"]["energy"]["clips_with_a_false_onset"] == "0/6"
    assert res["echo_above_floor_db_median"] < 3
    assert res["round_trip_delay_ms"] is None or res["round_trip_delay_ms"] < 150


def test_loud_open_speakers_trip_the_loudness_detector_on_every_clip(tmp_path: Path) -> None:
    res = pc.self_trigger_check(CLIPS, duplex(-6.0), tmp_path, SILERO, floor_s=2.0, save=False)
    assert res["summary"]["energy"]["clips_with_a_false_onset"] in ("6/6", "5/6")
    assert res["summary"]["silero_250ms"]["clips_with_a_false_onset"] in ("6/6", "5/6")
    assert res["echo_above_floor_db_median"] > 12
    assert res["round_trip_delay_ms"] == pytest.approx(80.0, abs=25)
    assert len(res["clips"]) == 6 and res["clips"][0]["first_clip"] is True


def test_the_recordings_are_saved_for_later(tmp_path: Path) -> None:
    pc.self_trigger_check(CLIPS[:2], duplex(-20.0), tmp_path, None, floor_s=1.5)
    assert (tmp_path / "mic-floor.wav").exists() and (tmp_path / "mic-reply-01.wav").exists()


def test_double_talk_separates_its_own_voice_from_the_person(tmp_path: Path) -> None:
    person = pc.read_wav(CLIPS[2], rate=48000)  # stands in for someone reading at the beep
    beep_t = 1.0 + 3.0

    def talker(t: float):  # type: ignore[no-untyped-def]
        block = 960
        i = int((t - beep_t - 0.4) * 48000)  # a 0.4 s reaction time
        out = np.zeros(block, dtype=np.float32)
        if i + block > 0 and i < len(person):
            lo = max(i, 0)
            seg = person[lo : i + block]
            out[lo - i : lo - i + len(seg)] = seg * 0.8
        return out

    lines: list[str] = []
    res = pc.double_talk_trials(
        CLIPS,
        ["a prompt"],
        duplex(-50.0, talker=talker),
        tmp_path,
        SILERO,
        floor_rms=0.003,
        ask=lambda _: "",
        say=lines.append,
    )
    t = res["trials"][0]
    assert t["before_beep"]["energy"] == []  # its own voice did not trip it
    assert t["after_beep"]["silero_250ms"] and 0.2 < t["after_beep"]["silero_250ms"][0] < 1.5
    assert (tmp_path / "trial-01.wav").exists() and (tmp_path / "trials.json").exists()
    assert any("BEEP" in line for line in lines)


# ---- the command line


def test_the_cli_runs_a_whole_selftest_without_hardware(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location("pi_echo_check", "scripts/pi_echo_check.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pi_echo_check", "selftest", "--label", "unit test", "--simulate-echo-db", "-40",
         "--out", str(tmp_path), "--no-save"],
    )  # fmt: skip
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "selftest: unit test" in out and "round trip" in out
    run = next(tmp_path.glob("*/run.json"))
    assert run.with_name("run.md").exists()

    monkeypatch.setattr(sys, "argv", ["pi_echo_check", "compare", str(run.parent)])
    assert mod.main() == 0
    assert "unit test" in capsys.readouterr().out
