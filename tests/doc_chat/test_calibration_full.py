import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from doc_chat import calibrate


def tone(level: float, seconds: float, rate: int = 16000) -> bytes:
    n = int(rate * seconds)
    x = level * np.sqrt(2) * np.sin(2 * np.pi * 440 * np.arange(n) / rate)  # RMS = level
    return (x * 32768).astype(np.int16).tobytes()


def test_the_gate_is_half_the_distance_to_the_background_voice_within_bounds() -> None:
    assert calibrate.recommend_gate_db(-20.0, -40.0) == 13.3
    assert calibrate.recommend_gate_db(-20.0, -33.0) == 8.7
    assert calibrate.recommend_gate_db(-20.0, -70.0) == 14.0  # capped
    assert calibrate.recommend_gate_db(-20.0, -29.0) is None  # under 12 dB: no gate can separate


def test_advice_names_the_problems_and_stays_empty_when_all_is_well() -> None:
    good = {"user_db": -20.0, "music_db": -45.0, "noise_music_db": -45.0, "user_music_db": -20.0}
    assert calibrate.advice(good) == []
    loud_music = {**good, "music_db": -25.0}
    assert "within 10 dB of your voice" in calibrate.advice(loud_music)[0]
    noisy = {**good, "noise_music_db": -30.0}
    assert "only 10 dB above the noise" in calibrate.advice(noisy)[0]
    masked = {"user_db": -20.0, "music_db": -45.0, "noise_music_db": -45.0}
    assert "expect misheard words" in calibrate.advice(masked)[0]
    gated = calibrate.advice({"user_db": -20.0, "bg_voice_db": -40.0, "gate_db": 10.0})
    assert "--voice-gate-db auto (10 dB" in gated[0]
    ungated = calibrate.advice({"user_db": -20.0, "bg_voice_db": -26.0})
    assert "cannot tell it from you" in ungated[0]


def test_load_calibration_reads_the_new_keys_and_old_files_still_load(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"when": "t", "noise_db": -58.2, "user_db": -19.1}))
    assert calibrate.load_calibration(old) == {"when": "t", "noise_db": -58.2, "user_db": -19.1}
    new = tmp_path / "new.json"
    new.write_text(
        json.dumps(
            {"when": "t", "noise_db": -58.0, "user_db": -19.0, "music_db": -40.0,
             "noise_music_db": -41.0, "user_music_db": -21.0, "bg_voice_db": -35.0, "gate_db": 8.0}
        )
    )  # fmt: skip
    cal = calibrate.load_calibration(new)
    assert cal is not None and cal["gate_db"] == 8.0 and cal["bg_voice_db"] == -35.0
    assert "gate 8 dB" in calibrate.describe(cal)


class FakeBridge(calibrate.SpotifyBridge):
    def __init__(self, url: str | None, playing: bool = True) -> None:
        super().__init__(url)
        self.playing = playing
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def wait_playing(self, seconds: float = 20.0) -> bool:
        self.calls.append("wait")
        return self.playing

    def stop(self) -> None:
        self.calls.append("stop")


def scripted(recordings: list[bytes]) -> Any:
    queue = iter(recordings)
    return lambda seconds: next(queue)


def test_the_music_stage_pipes_to_the_pi_measures_the_leak_and_your_voice_over_it() -> None:
    said: list[str] = []
    bridge = FakeBridge("ws://pi:8780")
    out = calibrate.measure_music(
        scripted([tone(0.01, 8), tone(0.1, 3.5), tone(0.1, 3.5), tone(0.1, 3.5)]),
        lambda prompt: "",
        said.append,
        bridge,
        ("a", "b", "c"),
        -60.0,
    )
    assert bridge.calls == ["start", "wait", "stop"]
    assert out["music_db"] == pytest.approx(-40.0, abs=1.0)
    assert out["noise_music_db"] == pytest.approx(-40.0, abs=1.0)
    assert out["user_music_db"] == pytest.approx(-20.0, abs=1.0)
    assert any("playing through the Pi speaker" in line for line in said)


def test_without_the_music_in_the_pi_path_it_says_so_and_without_a_url_it_stays_quiet() -> None:
    said: list[str] = []
    calibrate.measure_music(
        scripted([tone(0.01, 8)] + [tone(0.1, 3.5)] * 3),
        lambda p: "", said.append, FakeBridge("ws://pi:8780", playing=False),
        ("a", "b", "c"), -60.0,
    )  # fmt: skip
    assert any("not found in the Pi path" in line for line in said)
    said.clear()
    calibrate.measure_music(
        scripted([tone(0.01, 8)] + [tone(0.1, 3.5)] * 3),
        lambda p: "", said.append, FakeBridge(None), ("a", "b", "c"), -60.0,
    )  # fmt: skip
    assert not any("Pi" in line for line in said)


def test_your_voice_not_above_the_music_is_reported_not_crashed() -> None:
    out = calibrate.measure_music(
        scripted([tone(0.1, 8)] + [tone(0.1, 3.5)] * 3),  # music as loud as you
        lambda p: "", print, FakeBridge(None), ("a", "b", "c"), -60.0,
    )  # fmt: skip
    assert "user_music_db" not in out and "music_db" in out


def test_the_background_voice_is_measured_the_way_the_gate_measures_a_turn() -> None:
    level = calibrate.measure_background(scripted([tone(0.022, 8)]), lambda p: "", print)
    assert level == pytest.approx(-33.0, abs=1.0)
    assert calibrate.measure_background(scripted([tone(0.0, 8)]), lambda p: "", print) is None


def test_main_full_writes_every_key_and_the_recommended_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recordings = (
        [tone(0.001, 3.0)]  # the quiet room (-60)
        + [tone(0.1, 3.5)] * 3  # your voice (-20)
        + [tone(0.01, 8.0)]  # the music leak (-40)
        + [tone(0.1, 3.5)] * 3  # your voice over the music
        + [tone(0.022, 8.0)]  # a background voice (-33)
    )
    monkeypatch.setattr(calibrate, "_record", lambda seconds, device=None: next(it))
    it = iter(recordings)
    monkeypatch.setattr(calibrate, "_input_name", lambda device: "test mic")
    monkeypatch.setattr(calibrate, "SpotifyBridge", lambda url, token=None: FakeBridge(url))
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    out = tmp_path / "cal.json"
    assert calibrate.main(["--full", "--play-remote", "ws://pi:8780", "--out", str(out)]) == 0
    cal = json.loads(out.read_text())
    assert cal["noise_db"] == pytest.approx(-60.0, abs=1.0)
    assert cal["user_db"] == pytest.approx(-20.0, abs=1.0)
    assert cal["music_db"] == pytest.approx(-40.0, abs=1.0)
    assert cal["user_music_db"] == pytest.approx(-20.0, abs=1.0)
    assert cal["bg_voice_db"] == pytest.approx(-33.0, abs=1.0)
    assert cal["gate_db"] == pytest.approx(8.7, abs=0.6)
    assert "--voice-gate-db auto" in capsys.readouterr().out


def fake_pactl(sinks: str, load_ok: bool = True) -> Any:
    from types import SimpleNamespace

    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        calls.append(argv)
        if argv[:3] == ["pactl", "list", "short"]:
            return SimpleNamespace(stdout=sinks, returncode=0)
        return SimpleNamespace(stdout="", returncode=0 if load_ok else 1)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_the_spotify_sink_is_made_when_the_stack_has_not_and_left_alone_when_it_has() -> None:
    from doc_chat.music import ensure_spotify_sink

    has = fake_pactl("329\tme3_spotify\tPipeWire\tfloat32le 2ch 48000Hz\tRUNNING\n")
    assert ensure_spotify_sink(has) is True and len(has.calls) == 1  # nothing loaded
    missing = fake_pactl("56\talsa_output.pci\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED\n")
    assert ensure_spotify_sink(missing) is True
    load = missing.calls[-1]
    assert load[:3] == ["pactl", "load-module", "module-null-sink"]
    assert "sink_name=me3_spotify" in load
    assert ensure_spotify_sink(fake_pactl("", load_ok=False)) is False


def test_the_bridge_makes_the_sink_before_it_starts_piping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from doc_chat import music

    made: list[str] = []
    monkeypatch.setattr(music, "ensure_spotify_sink", lambda *a, **k: made.append("sink") or True)
    monkeypatch.setattr(music, "MusicPlayer", lambda connect: object())
    bridge = calibrate.SpotifyBridge("ws://pi:8780")
    bridge.start()
    assert made == ["sink"] and bridge.player is not None
    quiet = calibrate.SpotifyBridge(None)
    quiet.start()
    assert made == ["sink"] and quiet.player is None  # no Pi URL: nothing is piped


FULL = {
    "when": "2026-11-12T10:00:00",
    "noise_db": -39.0,
    "user_db": -20.0,
    "music_db": -34.0,
    "bg_voice_db": -36.0,
    "gate_db": 8.0,
}


def at(hours_after: float) -> float:
    import time

    return time.mktime(time.strptime(FULL["when"], "%Y-%m-%dT%H:%M:%S")) + hours_after * 3600


def test_status_says_missing_old_short_or_fine() -> None:
    assert calibrate.status(None)[0] == 1
    code, line = calibrate.status(FULL, 12.0, now=at(0.05))
    assert code == 0 and "3 min old" in line and "gate 8 dB" in line
    assert calibrate.status(FULL, 12.0, now=at(5.0)) == (0, "calibration is 5.0 h old, gate 8 dB")
    code, line = calibrate.status(FULL, 12.0, now=at(13.0))
    assert code == 2 and "13 h old" in line and "make calibrate" in line
    short = {k: v for k, v in FULL.items() if k not in ("music_db", "bg_voice_db", "gate_db")}
    assert calibrate.status(short, 12.0, now=at(1.0))[0] == 3
    assert calibrate.status({**FULL, "when": "yesterday"})[0] == 2
    no_gate = {k: v for k, v in FULL.items() if k != "gate_db"}
    assert "no gate (voice too close)" in calibrate.status(no_gate, 12.0, now=at(1.0))[1]


def test_check_exits_with_the_status_and_never_touches_the_microphone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_mic(*a: Any, **k: Any) -> None:
        raise AssertionError("--check must not record")

    monkeypatch.setattr(calibrate, "_record", no_mic)
    path = tmp_path / "cal.json"
    assert calibrate.main(["--check", "--out", str(path)]) == 1  # missing
    path.write_text(json.dumps({**FULL, "when": "2026-01-01T00:00:00"}))
    assert calibrate.main(["--check", "--out", str(path)]) == 2  # months old
    assert calibrate.main(["--check", "--max-age-h", "1000000", "--out", str(path)]) == 0
    assert "gate 8 dB" in capsys.readouterr().out
