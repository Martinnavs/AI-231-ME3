from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from audio_engine import FakeSynthesizer
from bench.analyze import analyze, calibrate
from bench.truth import TruthSet, build_truth_set, load_truth

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from synth_recording import build_synth_recording  # noqa: E402

_FLOOR_DBFS = -60.0
_FLUCT_DB = 2.5

Env = tuple[Path, Path, Path, TruthSet]


def _sustained_peak_dbfs(wav: Path) -> float:
    data, sr = sf.read(str(wav), dtype="float64", always_2d=True)
    ch0 = data[:, 0]
    win = int(round(0.010 * sr))
    hop = int(round(0.001 * sr))
    n = (len(ch0) - win) // hop + 1
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    rms = 20 * np.log10(np.maximum(np.sqrt(np.mean(ch0[idx] ** 2, axis=1)), 1e-6))
    sustained = np.lib.stride_tricks.sliding_window_view(rms, 30).min(axis=1)
    return float(sustained.max())


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Env:
    d = tmp_path_factory.mktemp("cal")
    turns = [{"kind": "turn", "text": f"turn {i}"} for i in range(6)]
    (d / "spec.json").write_text(json.dumps({"version": 1, "turns": turns}), encoding="utf-8")
    build_truth_set(d / "spec.json", FakeSynthesizer(), d / "truth", answer_window_s=6.0)
    truth_json = d / "truth" / "truth.json"
    return d, d / "truth" / "stimulus.wav", truth_json, load_truth(truth_json)


def _rec(env: Env, name: str, **kw: object) -> Path:
    d, stim, _, truth = env
    args: dict[str, object] = {
        "playback_delay_s": 0.05,
        "noise_floor_dbfs": _FLOOR_DBFS,
        "noise_fluctuation_db": _FLUCT_DB,
        "default_bot_latency": None,
    }
    args.update(kw)
    return build_synth_recording(truth, stim, d / name, **args)  # type: ignore[arg-type]


def test_margin_matches_known_peak_of_fixture(env: Env) -> None:
    _, stim, truth_json, _ = env
    rec = _rec(env, "quiet.wav")
    m = calibrate(rec, stim, truth_json)
    floor = analyze(rec, stim, truth_json, margin_db=m).noise_floor_dbfs
    expected = _sustained_peak_dbfs(rec) - floor + 0.1
    assert abs(m - expected) <= 0.1
    assert _FLUCT_DB - 0.5 <= m <= _FLUCT_DB + 1.5
    assert m == pytest.approx(2.7, abs=0.2)


def test_calibrated_margin_gives_no_false_ok_onsets_but_smaller_margin_does(env: Env) -> None:
    _, stim, truth_json, _ = env
    rec = _rec(env, "quiet2.wav")
    m = calibrate(rec, stim, truth_json)
    at_m = analyze(rec, stim, truth_json, margin_db=m)
    below = analyze(rec, stim, truth_json, margin_db=m - 1.0)
    assert at_m.counts["ok"] == 0
    assert at_m.counts["missed"] == 5
    assert below.counts["ok"] >= 1


@pytest.mark.xfail(
    strict=True,
    reason="R23: _check_crosstalk uses a single 1 ms frame max, so calibrate's 30 ms-sustained "
    "margin still yields a crosstalk status on a bot-disabled noise-only recording",
)
def test_calibrated_margin_gives_no_crosstalk_on_noise_only(env: Env) -> None:
    _, stim, truth_json, _ = env
    rec = _rec(env, "quiet3.wav")
    m = calibrate(rec, stim, truth_json)
    at_m = analyze(rec, stim, truth_json, margin_db=m)
    assert at_m.counts["crosstalk"] == 0


def test_crosstalk_level_sets_margin_numerically(env: Env) -> None:
    _, stim, truth_json, truth = env
    leak_dbfs = -50.0
    rec = _rec(env, "xtalk.wav", crosstalk_db={truth.trials[0].id: leak_dbfs})
    m = calibrate(rec, stim, truth_json)
    floor = analyze(rec, stim, truth_json).noise_floor_dbfs
    assert floor == pytest.approx(_FLOOR_DBFS, abs=0.5)
    assert leak_dbfs - floor - 1.0 <= m <= leak_dbfs - floor + 1.0
    assert 8.5 <= m <= 10.5
    assert abs(m - (_sustained_peak_dbfs(rec) - floor + 0.1)) <= 0.1


def test_bot_active_recording_passed_as_disabled_yields_implausibly_large_margin(env: Env) -> None:
    _, stim, truth_json, _ = env
    rec = _rec(env, "bot_on.wav", default_bot_latency=0.3, bot_level_dbfs=-20.0)
    m = calibrate(rec, stim, truth_json)
    assert m >= 35.0
    assert m > 10 * calibrate(_rec(env, "quiet4.wav"), stim, truth_json)


def test_calibrate_rejects_recording_without_loopback_chirp(env: Env, tmp_path: Path) -> None:
    _, stim, truth_json, _ = env
    rec = _rec(env, "quiet5.wav")
    data, sr = sf.read(str(rec), dtype="float32", always_2d=True)
    data[:, 1] = 0.0
    bad = tmp_path / "no_loopback.wav"
    sf.write(str(bad), data, sr, subtype="PCM_16")
    with pytest.raises(ValueError, match="chirp"):
        calibrate(bad, stim, truth_json)


@pytest.mark.xfail(
    strict=True,
    reason="R23: digitally silent lead-in gives a -120 dBFS floor, so threshold = floor + margin "
    "is far below any real signal; no clamp to >= -90 dBFS",
)
def test_digitally_silent_bot_channel_does_not_yield_subnoise_threshold(env: Env) -> None:
    _, stim, truth_json, _ = env
    rec = _rec(env, "silent.wav", noise_floor_dbfs=-200.0, noise_fluctuation_db=0.0)
    m = calibrate(rec, stim, truth_json)
    floor = analyze(rec, stim, truth_json, margin_db=m).noise_floor_dbfs
    assert floor + m >= -90.0
