from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from audio_engine import FakeSynthesizer
from bench.analyze import (
    analyze,
    calibrate,
    main,
)
from bench.truth import build_truth_set
from voice_agent.telemetry.events import Stage, StageLogger

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from synth_recording import build_synth_recording  # noqa: E402


@pytest.fixture
def truth_25(tmp_path: Path) -> tuple[Path, Path]:
    turns = [{"kind": "turn", "text": f"turn {i}"} for i in range(25)]
    spec_path = tmp_path / "spec_25.json"
    spec_path.write_text(json.dumps({"version": 1, "turns": turns}), encoding="utf-8")
    out_dir = tmp_path / "truth_25"
    build_truth_set(spec_path, FakeSynthesizer(), out_dir, answer_window_s=6.0)
    return out_dir / "stimulus.wav", out_dir / "truth.json"


@pytest.fixture
def truth_mixed(tmp_path: Path) -> tuple[Path, Path]:
    turns = [
        {"kind": "turn", "text": "hello first turn"},
        {"kind": "barge_in", "text": "tell me a story", "event_text": "stop please"},
        {"kind": "barge_in", "text": "sing a song", "event_text": "halt"},
        {"kind": "noise", "text": "what is the weather today"},
        {"kind": "noise", "text": "how far is the moon"},
        {
            "kind": "pause",
            "parts": ["my order number is", "five four three two"],
            "pause_s": 0.8,
        },
        {
            "kind": "pause",
            "parts": ["delivery address is", "main street ten"],
            "pause_s": 0.8,
        },
        {"kind": "turn", "text": "thank you goodbye"},
    ]
    spec_path = tmp_path / "spec_mixed.json"
    spec_path.write_text(json.dumps({"version": 1, "turns": turns}), encoding="utf-8")
    out_dir = tmp_path / "truth_mixed"
    build_truth_set(spec_path, FakeSynthesizer(), out_dir, answer_window_s=8.0)
    return out_dir / "stimulus.wav", out_dir / "truth.json"


def test_alignment_offset_and_drift(truth_25: tuple[Path, Path], tmp_path: Path) -> None:
    stim_wav, truth_json = truth_25
    from bench.truth import load_truth

    truth = load_truth(truth_json)

    injected_delay = 0.1373
    injected_drift = 50.0  # ppm
    rec_path = tmp_path / "rec_align.wav"
    build_synth_recording(
        truth,
        stim_wav,
        rec_path,
        playback_delay_s=injected_delay,
        drift_ppm=injected_drift,
        noise_floor_dbfs=-60.0,
        default_bot_latency=0.5,
    )

    analysis = analyze(rec_path, stim_wav, truth_json)
    assert abs(analysis.offset_s - injected_delay) < 0.001
    assert abs(analysis.drift_ppm - injected_drift) < 5.0


def test_first_audio_exact(truth_25: tuple[Path, Path], tmp_path: Path) -> None:
    stim_wav, truth_json = truth_25
    from bench.truth import load_truth

    truth = load_truth(truth_json)

    # Injected bot latencies (e.g. 0.42 ... 1.10 s)
    latencies = np.linspace(0.42, 1.10, len(truth.trials))
    bot_lat_dict = {tr.id: float(lat) for tr, lat in zip(truth.trials, latencies, strict=True)}

    rec_path = tmp_path / "rec_latencies.wav"
    build_synth_recording(
        truth,
        stim_wav,
        rec_path,
        playback_delay_s=0.1,
        noise_floor_dbfs=-60.0,
        bot_latencies=bot_lat_dict,
    )

    analysis = analyze(rec_path, stim_wav, truth_json)
    assert analysis.first_audio.n == len(truth.trials)
    assert analysis.counts["ok"] == len(truth.trials)

    for tr_res, injected in zip(analysis.trials, latencies, strict=True):
        assert tr_res.status == "ok"
        assert tr_res.first_audio_s is not None
        assert abs(tr_res.first_audio_s - injected) <= 0.010

    expected_p50 = float(np.percentile(latencies, 50))
    expected_p95 = float(np.percentile(latencies, 95))
    assert analysis.first_audio.p50 is not None
    assert analysis.first_audio.p95 is not None
    assert abs(analysis.first_audio.p50 - expected_p50) <= 0.010
    assert abs(analysis.first_audio.p95 - expected_p95) <= 0.010
    assert not analysis.first_audio.low_n


def test_no_false_onsets_in_noise(truth_mixed: tuple[Path, Path], tmp_path: Path) -> None:
    stim_wav, truth_json = truth_mixed
    from bench.truth import load_truth

    truth = load_truth(truth_json)

    rec_path = tmp_path / "rec_noise_only.wav"
    build_synth_recording(
        truth,
        stim_wav,
        rec_path,
        playback_delay_s=0.05,
        noise_floor_dbfs=-60.0,
        noise_fluctuation_db=3.0,
        default_bot_latency=None,  # No bot audio
    )

    analysis = analyze(rec_path, stim_wav, truth_json, margin_db=12.0)
    assert analysis.counts["ok"] == 0
    # Every trial where bot didn't speak should be missed or invalid
    assert analysis.counts["missed"] + analysis.counts["invalid"] == len(truth.trials)
    for tr in analysis.trials:
        assert tr.status in ("missed", "invalid")
        assert tr.status != "ok"


def test_crosstalk_flagged(truth_mixed: tuple[Path, Path], tmp_path: Path) -> None:
    stim_wav, truth_json = truth_mixed
    from bench.truth import load_truth

    truth = load_truth(truth_json)

    # Trial 1 has user speech leaking into ch0 at floor + 15 dB
    rec_path = tmp_path / "rec_crosstalk.wav"
    build_synth_recording(
        truth,
        stim_wav,
        rec_path,
        playback_delay_s=0.05,
        noise_floor_dbfs=-60.0,
        crosstalk_db={truth.trials[0].id: -45.0},
        default_bot_latency=0.5,
    )

    analysis = analyze(rec_path, stim_wav, truth_json, margin_db=12.0)
    t0_res = analysis.trials[0]
    assert t0_res.status == "crosstalk"
    assert analysis.counts["crosstalk"] >= 1
    # Crosstalk trial is excluded from first_audio stats
    assert analysis.first_audio.n < len(truth.trials)


def test_filler_then_answer(tmp_path: Path) -> None:
    spec = {
        "version": 1,
        "turns": [{"kind": "turn", "text": "what is your favorite movie"}],
    }
    spec_path = tmp_path / "spec_filler.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    truth = build_truth_set(spec_path, FakeSynthesizer(), tmp_path / "truth_filler")
    stim_wav = tmp_path / "truth_filler" / "stimulus.wav"
    truth_json = tmp_path / "truth_filler" / "truth.json"

    filler_dur = 0.400
    gap = 0.600
    fillers = {truth.trials[0].id: (None, filler_dur, gap)}

    rec_path = tmp_path / "rec_filler.wav"
    build_synth_recording(
        truth,
        stim_wav,
        rec_path,
        default_bot_latency=0.300,
        fillers=fillers,
    )

    stage_log = tmp_path / "stage_events.jsonl"
    logger = StageLogger(stage_log, run_id="test_filler")
    logger.emit(
        Stage.FILLER_PLAYED,
        turn_id=1,
        duration_ms=int(round(filler_dur * 1000)),
        kind="stall",
    )
    logger.close()

    analysis = analyze(rec_path, stim_wav, truth_json, stage_log=stage_log)
    tr = analysis.trials[0]
    assert tr.status == "ok"
    assert tr.first_audio_s is not None
    assert tr.first_answer_audio_s is not None
    diff = tr.first_answer_audio_s - tr.first_audio_s
    expected_diff = filler_dur + gap
    assert abs(diff - expected_diff) <= 0.015


def test_barge_in_stop_and_false_interruption(
    truth_mixed: tuple[Path, Path], tmp_path: Path
) -> None:
    stim_wav, truth_json = truth_mixed
    from bench.truth import load_truth

    truth = load_truth(truth_json)

    # trials:
    # 0: turn
    # 1: barge_in (duck 80ms, stop 350ms)
    # 2: barge_in (no duck, stop 500ms)
    # 3: noise (keeps talking -> not false)
    # 4: noise (stops -> false interruption)
    b_id1 = truth.trials[1].id
    b_id2 = truth.trials[2].id
    n_id1 = truth.trials[3].id
    n_id2 = truth.trials[4].id

    barge_stops = {b_id1: 0.350, b_id2: 0.500}
    barge_ducks = {b_id1: 0.080}
    false_ints = {n_id2}

    rec_path = tmp_path / "rec_barge.wav"
    build_synth_recording(
        truth,
        stim_wav,
        rec_path,
        default_bot_latency=0.400,
        barge_in_stops=barge_stops,
        barge_in_ducks=barge_ducks,
        false_interruptions=false_ints,
    )

    analysis = analyze(rec_path, stim_wav, truth_json)
    r_b1 = next(r for r in analysis.trials if r.id == b_id1)
    assert r_b1.status == "ok"
    assert r_b1.barge_in_stop_s is not None
    assert abs(r_b1.barge_in_stop_s - 0.350) <= 0.015
    assert r_b1.barge_in_duck_s is not None
    assert abs(r_b1.barge_in_duck_s - 0.080) <= 0.015

    r_b2 = next(r for r in analysis.trials if r.id == b_id2)
    assert r_b2.status == "ok"
    assert r_b2.barge_in_stop_s is not None
    assert abs(r_b2.barge_in_stop_s - 0.500) <= 0.015
    assert r_b2.barge_in_duck_s is None

    r_n1 = next(r for r in analysis.trials if r.id == n_id1)
    assert r_n1.status == "ok"
    assert r_n1.false_interruption is False

    r_n2 = next(r for r in analysis.trials if r.id == n_id2)
    assert r_n2.status == "ok"
    assert r_n2.false_interruption is True

    assert analysis.false_interruption_rate == 0.5


def test_missing_chirp_aborts(tmp_path: Path) -> None:
    stim_wav = tmp_path / "stim.wav"
    truth_json = tmp_path / "truth.json"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps({"version": 1, "turns": [{"kind": "turn", "text": "hello"}]}),
        encoding="utf-8",
    )
    build_truth_set(spec_path, FakeSynthesizer(), tmp_path / "truth")
    stim_wav = tmp_path / "truth" / "stimulus.wav"
    truth_json = tmp_path / "truth" / "truth.json"

    # 2-channel recording with silence / random noise (no chirp)
    rec_path = tmp_path / "no_chirp.wav"
    noise = np.random.default_rng(0).normal(0, 0.01, size=(16000 * 3, 2)).astype(np.float32)
    sf.write(str(rec_path), noise, 16000, subtype="PCM_16")

    with pytest.raises(ValueError, match="loopback channel has no chirp: check In 2 wiring"):
        analyze(rec_path, stim_wav, truth_json)


def test_mono_aborts(tmp_path: Path) -> None:
    stim_wav = tmp_path / "stim.wav"
    truth_json = tmp_path / "truth.json"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps({"version": 1, "turns": [{"kind": "turn", "text": "hello"}]}),
        encoding="utf-8",
    )
    build_truth_set(spec_path, FakeSynthesizer(), tmp_path / "truth")
    stim_wav = tmp_path / "truth" / "stimulus.wav"
    truth_json = tmp_path / "truth" / "truth.json"

    mono_path = tmp_path / "mono.wav"
    sf.write(str(mono_path), np.zeros(16000, dtype=np.float32), 16000, subtype="PCM_16")

    with pytest.raises(ValueError, match="need bot \\+ loopback channels"):
        analyze(mono_path, stim_wav, truth_json)


def test_pause_takeover(truth_mixed: tuple[Path, Path], tmp_path: Path) -> None:
    stim_wav, truth_json = truth_mixed
    from bench.truth import load_truth

    truth = load_truth(truth_json)

    # pause trials are trials[5] and trials[6]
    p_id1 = truth.trials[5].id
    p_id2 = truth.trials[6].id

    # p_id1 has premature takeover: injected 300ms into the gap
    pause_takeovers = {p_id1: 0.300}
    bot_latencies = {p_id2: 0.500}

    rec_path = tmp_path / "rec_pause.wav"
    build_synth_recording(
        truth,
        stim_wav,
        rec_path,
        bot_latencies=bot_latencies,
        pause_takeovers=pause_takeovers,
    )

    analysis = analyze(rec_path, stim_wav, truth_json)
    r_p1 = next(r for r in analysis.trials if r.id == p_id1)
    assert r_p1.premature_takeover is True
    # Excluded from first_audio stats: first_audio_s is None
    assert r_p1.first_audio_s is None

    r_p2 = next(r for r in analysis.trials if r.id == p_id2)
    assert r_p2.premature_takeover is False
    assert r_p2.first_audio_s is not None
    assert abs(r_p2.first_audio_s - 0.500) <= 0.010

    assert analysis.premature_takeover_rate == 0.5


def test_calibrate_and_cli(truth_mixed: tuple[Path, Path], tmp_path: Path) -> None:
    stim_wav, truth_json = truth_mixed
    from bench.truth import load_truth

    truth = load_truth(truth_json)

    rec_disabled = tmp_path / "rec_bot_disabled.wav"
    build_synth_recording(
        truth,
        stim_wav,
        rec_disabled,
        playback_delay_s=0.05,
        noise_floor_dbfs=-60.0,
        noise_fluctuation_db=2.5,
        default_bot_latency=None,
    )

    safe_margin = calibrate(rec_disabled, stim_wav, truth_json)
    assert safe_margin > 0.0

    # Test CLI calibrate
    rc_cal = main(["calibrate", str(rec_disabled), str(stim_wav), str(truth_json)])
    assert rc_cal == 0

    # Test CLI score
    out_dir = tmp_path / "cli_out"
    rc_score = main(
        [
            "score",
            str(rec_disabled),
            str(stim_wav),
            str(truth_json),
            "--out-dir",
            str(out_dir),
            "--margin-db",
            str(safe_margin + 5.0),
        ]
    )
    assert rc_score == 0
    assert (out_dir / "results.json").exists()
    assert (out_dir / "report.md").exists()
