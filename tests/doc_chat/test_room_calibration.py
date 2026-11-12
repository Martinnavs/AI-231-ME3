import json
import math
from pathlib import Path

import numpy as np
import pytest

from doc_chat import calibrate, speech


def pcm(level: float, seconds: float = 1.0, rate: int = 16000) -> bytes:
    n = int(rate * seconds)
    x = level * np.sqrt(2) * np.sin(2 * np.pi * 440 * np.arange(n) / rate)  # RMS = level
    return (x * 32768).astype(np.int16).tobytes()


def test_dbfs_math() -> None:
    assert calibrate.dbfs(1.0) == 0.0
    assert calibrate.dbfs(0.1) == pytest.approx(-20.0)
    assert calibrate.dbfs(0.0) == pytest.approx(-120.0)


def test_block_levels_noise_speech_and_music() -> None:
    levels = calibrate.block_levels(pcm(0.01, 2.0))
    assert len(levels) == 20 and levels[0] == pytest.approx(0.01, rel=0.02)
    room = calibrate.block_levels(pcm(0.01, 3.0))
    talk = calibrate.block_levels(pcm(0.1, 1.0))
    noise = calibrate.noise_db(room + talk)
    assert noise == pytest.approx(-40.0, abs=0.3)
    assert calibrate.speech_db(room + talk, noise) == pytest.approx(-20.0, abs=0.3)
    assert calibrate.music_db(calibrate.block_levels(pcm(0.03, 2.0))) == pytest.approx(
        -30.5, abs=0.3
    )
    with pytest.raises(ValueError):
        calibrate.speech_db(room, calibrate.noise_db(room))


def test_the_music_gate_is_halfway_between_user_and_music() -> None:
    assert calibrate.music_gate_db({"user_db": -20.0, "music_db": -40.0}) == -30.0
    assert calibrate.music_gate_db({"user_db": -20.0}) is None
    assert calibrate.music_gate_db(None) is None


def test_missing_and_corrupt_files_fall_back(tmp_path: Path) -> None:
    assert calibrate.load_calibration(tmp_path / "none.json") is None
    for text in (
        "",
        "not json",
        "[]",
        '{"when": "x"}',
        '{"when": "x", "noise_db": "loud", "user_db": 1}',
    ):
        bad = tmp_path / "bad.json"
        bad.write_text(text)
        assert calibrate.load_calibration(bad) is None


def test_a_good_file_loads_and_is_described(tmp_path: Path) -> None:
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"when": "t", "noise_db": -45.0, "user_db": -22.0, "music_db": -38.0}))
    cal = calibrate.load_calibration(f)
    assert cal == {"when": "t", "noise_db": -45.0, "user_db": -22.0, "music_db": -38.0}
    assert "noise -45" in calibrate.describe(cal) and "music -38" in calibrate.describe(cal)
    assert calibrate.describe(None).startswith("no room calibration")


def test_the_noise_hint_uses_the_file_only_when_it_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert speech.room_noise_hint() == speech.DEFAULT_NOISE_HINT
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "calibration.json").write_text(
        json.dumps({"when": "t", "noise_db": -40.0, "user_db": -20.0})
    )
    assert speech.room_noise_hint() == pytest.approx(0.01)
    (tmp_path / "out" / "calibration.json").write_text("garbage")
    assert speech.room_noise_hint() == speech.DEFAULT_NOISE_HINT
    assert math.isfinite(speech.DEFAULT_NOISE_HINT)


def test_the_music_pause_ignores_speech_quieter_than_the_gate() -> None:
    from doc_chat.music import MusicVoice

    class Player:
        audible = True
        paused = 0

        def user_speech(self) -> None:
            self.paused += 1

    class Vad:
        def feed(self, block: bytes) -> bool:
            return True

        def reset(self) -> None:
            pass

    quiet, loud = pcm(0.01, 0.1), pcm(0.1, 0.1)
    p = Player()
    MusicVoice(p, "", vad=Vad(), gate_db=-30.0).feed(quiet)  # type: ignore[arg-type]
    assert p.paused == 0
    MusicVoice(p, "", vad=Vad(), gate_db=-30.0).feed(loud)  # type: ignore[arg-type]
    assert p.paused == 1
    MusicVoice(p, "", vad=Vad()).feed(quiet)  # type: ignore[arg-type]
    assert p.paused == 2  # no gate: today's behaviour


def _fake_main_run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> list[object]:
    devices: list[object] = []

    def fake_record(seconds: float, device: object = None) -> bytes:
        devices.append(device)
        return pcm(0.01 if seconds == 3.0 else 0.1, seconds)

    monkeypatch.setattr(calibrate, "_record", fake_record)
    monkeypatch.setattr(calibrate, "_input_name", lambda device: f"fake input {device}")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert calibrate.main(argv) == 0
    return devices


def test_baseline_records_six_phrases_from_the_chosen_device_and_keeps_the_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out, wav = tmp_path / "cal.json", tmp_path / "baseline.wav"
    devices = _fake_main_run(
        monkeypatch,
        ["--out", str(out), "--baseline", "--baseline-wav", str(wav), "--mic-device", "13"],
    )
    assert devices == [13] * 7  # 3 s of quiet, then the six baseline phrases
    cal = json.loads(out.read_text())
    assert cal["noise_db"] == pytest.approx(-40.0, abs=0.5)
    assert cal["user_db"] == pytest.approx(-20.0, abs=0.5)
    import wave

    with wave.open(str(wav)) as w:
        assert w.getframerate() == 16000 and w.getnframes() == int(16000 * (3.0 + 6 * 3.5))


def test_default_run_keeps_the_three_prompts_and_writes_no_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out, wav = tmp_path / "cal.json", tmp_path / "baseline.wav"
    devices = _fake_main_run(monkeypatch, ["--out", str(out), "--baseline-wav", str(wav)])
    assert devices == [None] * 4 and out.exists() and not wav.exists()
