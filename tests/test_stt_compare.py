"""scripts/stt_compare.py: recording gate, script mode and the hands-free record loop."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stt_compare.py"


@pytest.fixture
def sc(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("stt_compare", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "stt_compare", mod)
    spec.loader.exec_module(mod)
    return mod


def test_gate_waits_then_stops_after_a_second_of_quiet(sc: types.ModuleType) -> None:
    gate = sc.VoiceGate(floor_db=-60.0)
    levels = [-62.0] * 5 + [-25.0] * 12 + [-62.0] * 12
    states = [gate.feed(x) for x in levels]
    assert states[:5] == ["waiting"] * 5
    assert states[5] == "waiting" and states[6] == "speaking"
    assert states.index("done") == 5 + 12 + 10 - 1
    assert gate.first_loud == 5


def test_gate_ignores_a_single_loud_block_and_times_out(sc: types.ModuleType) -> None:
    gate = sc.VoiceGate(floor_db=-60.0)
    states = [gate.feed(-20.0 if i == 3 else -62.0) for i in range(130)]
    assert gate.first_loud is None
    assert states[-1] == "timeout"


def test_pauses_inside_a_sentence_do_not_end_the_clip(sc: types.ModuleType) -> None:
    gate = sc.VoiceGate(floor_db=-60.0)
    levels = [-25.0] * 5 + [-62.0] * 6 + [-25.0] * 5 + [-62.0] * 12
    states = [gate.feed(x) for x in levels]
    assert states.index("done") == 5 + 6 + 5 + 10 - 1


def test_capitalized_names_skip_sentence_start_and_i(sc: types.ModuleType) -> None:
    assert sc.capitalized_names("Elena met Arthur on Saturday, and I smiled.") == [
        "arthur",
        "saturday",
    ]
    assert sc.capitalized_names("The baker's name is Arthur.") == ["arthur"]


def test_script_mode_builds_one_clip_per_sentence_plus_noise(
    sc: types.ModuleType, tmp_path: Path
) -> None:
    script = tmp_path / "s.txt"
    script.write_text("Elena walked home. She met Arthur. Then she slept.")
    args = argparse.Namespace(script=str(script), no_noise=False)
    items = sc.build_items(args)
    kinds = [i["kind"] for i in items]
    assert kinds == ["script"] * 3 + ["noise"] * 4
    assert [i["id"] for i in items][:3] == ["01", "02", "03"]
    assert items[1]["entities"] == ["arthur"]
    assert sc.build_items(argparse.Namespace(script=str(script), no_noise=True)) == items[:3]


def _fake_sounddevice() -> types.ModuleType:
    mod = types.ModuleType("sounddevice")

    class RawInputStream:
        def __init__(self, **kw: Any) -> None:
            self._cb = kw["callback"]
            self._stop = threading.Event()

        def _feed(self) -> None:
            rng = np.random.default_rng(1)
            for i in range(10_000):
                if self._stop.is_set():
                    return
                loud = 6 <= i < 22
                level = 6000 if loud else 8
                block = (rng.normal(0, level, 1600)).astype(np.int16)
                self._cb(block.tobytes(), 1600, None, None)
                time.sleep(0.0005)

        def __enter__(self) -> RawInputStream:
            threading.Thread(target=self._feed, daemon=True).start()
            return self

        def __exit__(self, *exc: object) -> None:
            self._stop.set()

    mod.RawInputStream = RawInputStream  # type: ignore[attr-defined]
    return mod


def test_hands_free_record_loop_writes_trimmed_clips(
    sc: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice())
    monkeypatch.setattr("builtins.input", lambda *_: "")
    monkeypatch.setattr(sc, "AUDIO_DIR", tmp_path / "clips")
    monkeypatch.setattr(
        sc, "record_until", lambda stop, device: np.zeros(48000, dtype=np.int16) + 3
    )
    monkeypatch.setattr(sc.time, "sleep", lambda s: None)
    script = tmp_path / "s.txt"
    script.write_text("Elena walked home. She met Arthur.")

    rc = sc.cmd_record(
        argparse.Namespace(device=None, script=str(script), no_noise=True, manual=False, only=None)
    )

    assert rc == 0
    refs = json.loads((tmp_path / "clips" / "refs.json").read_text())
    assert [r["text"] for r in refs] == ["Elena walked home.", "She met Arthur."]
    for r in refs:
        pcm = sc.read_wav(tmp_path / "clips" / f"{r['id']}.wav")
        assert 2.0 < len(pcm) / sc.SR < 3.6
        assert np.abs(pcm).max() > 3000


def test_highpass_removes_hum_but_keeps_speech_band(sc: types.ModuleType) -> None:
    t = np.arange(16000) / sc.SR
    hum = 3000 * np.sin(2 * np.pi * 50 * t)
    voice = 3000 * np.sin(2 * np.pi * 1000 * t)
    hum_out = sc.highpass_blocks(hum)[0][4000:]
    voice_out = sc.highpass_blocks(voice)[0][4000:]
    assert sc.dbfs(hum_out) < sc.dbfs(hum) - 20
    assert abs(sc.dbfs(voice_out) - sc.dbfs(voice)) < 1.0


def test_filter_state_carries_across_blocks(sc: types.ModuleType) -> None:
    x = np.random.default_rng(0).normal(0, 1000, 3200)
    whole = sc.highpass_blocks(x)[0]
    first, zi = sc.highpass_blocks(x[:1600])
    second, _ = sc.highpass_blocks(x[1600:], zi)
    assert np.allclose(whole, np.concatenate([first, second]))


def test_spelling_variants_are_not_errors(sc: types.ModuleType) -> None:
    assert sc.norm_words("A cosy neighbourhood.") == sc.norm_words("a cozy neighborhood")
