from __future__ import annotations

from pathlib import Path

from bench.analyze import Analysis, MetricStats, TrialResult
from bench.report import load_results, write_report


def test_report_files(tmp_path: Path) -> None:
    trials = [
        TrialResult(
            id="t001",
            kind="turn",
            status="ok",
            t0_rec_s=2.5,
            first_audio_s=0.450,
            first_answer_audio_s=0.450,
            barge_in_stop_s=None,
            barge_in_duck_s=None,
            false_interruption=None,
            premature_takeover=None,
        ),
        TrialResult(
            id="t002",
            kind="barge_in",
            status="ok",
            t0_rec_s=10.5,
            first_audio_s=0.520,
            first_answer_audio_s=0.520,
            barge_in_stop_s=0.310,
            barge_in_duck_s=0.075,
            false_interruption=None,
            premature_takeover=None,
        ),
        TrialResult(
            id="t003",
            kind="noise",
            status="ok",
            t0_rec_s=18.0,
            first_audio_s=0.480,
            first_answer_audio_s=0.480,
            barge_in_stop_s=None,
            barge_in_duck_s=0.082,
            false_interruption=False,
            premature_takeover=None,
        ),
        TrialResult(
            id="t004",
            kind="pause",
            status="ok",
            t0_rec_s=26.0,
            first_audio_s=0.510,
            first_answer_audio_s=0.510,
            barge_in_stop_s=None,
            barge_in_duck_s=None,
            false_interruption=None,
            premature_takeover=False,
        ),
        TrialResult(
            id="t005",
            kind="turn",
            status="missed",
            t0_rec_s=34.0,
            first_audio_s=None,
            first_answer_audio_s=None,
            barge_in_stop_s=None,
            barge_in_duck_s=None,
            false_interruption=None,
            premature_takeover=None,
        ),
        TrialResult(
            id="t006",
            kind="turn",
            status="crosstalk",
            t0_rec_s=42.0,
            first_audio_s=None,
            first_answer_audio_s=None,
            barge_in_stop_s=None,
            barge_in_duck_s=None,
            false_interruption=None,
            premature_takeover=None,
        ),
    ]

    analysis = Analysis(
        offset_s=0.1373,
        drift_ppm=215.0,  # > 200 ppm triggers warning
        noise_floor_dbfs=-61.2,
        margin_db=12.0,
        trials=trials,
        first_audio=MetricStats(n=4, p50=0.495, p95=0.518, mean=0.490, low_n=True),
        first_answer_audio=MetricStats(n=4, p50=0.495, p95=0.518, mean=0.490, low_n=True),
        barge_in_stop=MetricStats(n=1, p50=0.310, p95=0.310, mean=0.310, low_n=True),
        barge_in_duck=MetricStats(n=2, p50=0.078, p95=0.081, mean=0.078, low_n=True),
        false_interruption_rate=0.0,
        premature_takeover_rate=0.0,
        counts={"ok": 4, "missed": 1, "crosstalk": 1, "invalid": 0},
        stages={
            "endpoint->stt_final": MetricStats(n=4, p50=0.120, p95=0.145, mean=0.125, low_n=True),
            "endpoint->tts_first_chunk": MetricStats(
                n=4, p50=0.350, p95=0.410, mean=0.360, low_n=True
            ),
        },
        health={
            "max_temp_c": 52.4,
            "min_arm_mhz": 1800.0,
            "throttled": False,
            "median_cpu_pct": 34.5,
            "peak_rss_mb": 142.8,
        },
    )

    out_dir = tmp_path / "report"
    json_path, md_path = write_report(analysis, out_dir, title="Test Benchmark Run")

    assert json_path.exists()
    assert md_path.exists()

    # Round-trip check
    loaded = load_results(json_path)
    assert loaded == analysis

    # Check report.md contents
    md_text = md_path.read_text(encoding="utf-8")
    assert "# Test Benchmark Run" in md_text
    assert "First Audio (T4 - T0)" in md_text
    assert "0.495" in md_text
    assert "0.518" in md_text
    assert "**OK:** 4" in md_text
    assert "**Missed:** 1" in md_text
    assert "**Crosstalk:** 1" in md_text
    assert "**Invalid:** 0" in md_text
    assert "Missed Trials" in md_text
    assert "t005" in md_text
    assert "WARNING" in md_text
    assert "drift > 200 ppm" in md_text
    assert "Stage Breakdown" in md_text
    assert "endpoint->stt_final" in md_text
    assert "Pi Health Summary" in md_text
    assert "52.4 °C" in md_text
