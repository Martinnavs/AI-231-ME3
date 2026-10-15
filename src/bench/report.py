from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .analyze import Analysis, MetricStats, TrialResult


def _fmt(val: float | None, digits: int = 3) -> str:
    if val is None:
        return "-"
    return f"{val:.{digits}f}"


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val * 100:.1f}%"


def load_results(path: Path) -> Analysis:
    raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    trials = [TrialResult(**t) for t in raw["trials"]]
    first_audio = MetricStats(**raw["first_audio"])
    first_answer_audio = MetricStats(**raw["first_answer_audio"])
    barge_in_stop = MetricStats(**raw["barge_in_stop"])
    barge_in_duck = MetricStats(**raw["barge_in_duck"])
    stages = None
    if raw.get("stages") is not None:
        stages = {k: MetricStats(**v) for k, v in raw["stages"].items()}
    return Analysis(
        offset_s=float(raw["offset_s"]),
        drift_ppm=float(raw["drift_ppm"]),
        noise_floor_dbfs=float(raw["noise_floor_dbfs"]),
        margin_db=float(raw["margin_db"]),
        trials=trials,
        first_audio=first_audio,
        first_answer_audio=first_answer_audio,
        barge_in_stop=barge_in_stop,
        barge_in_duck=barge_in_duck,
        false_interruption_rate=(
            float(raw["false_interruption_rate"])
            if raw["false_interruption_rate"] is not None
            else None
        ),
        premature_takeover_rate=(
            float(raw["premature_takeover_rate"])
            if raw["premature_takeover_rate"] is not None
            else None
        ),
        counts=dict(raw["counts"]),
        stages=stages,
        health=dict(raw["health"]) if raw.get("health") is not None else None,
    )


def write_report(analysis: Analysis, out_dir: Path, *, title: str) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"
    report_path = out_dir / "report.md"

    results_path.write_text(json.dumps(asdict(analysis), indent=2) + "\n", encoding="utf-8")

    lines: list[str] = [f"# {title}", ""]

    if abs(analysis.drift_ppm) > 200:
        lines.extend(
            [
                "> [!WARNING]",
                f"> Clock drift > 200 ppm ({analysis.drift_ppm:.1f} ppm). "
                "Likely wrong sample rate on one side.",
                "",
            ]
        )

    lines.extend(
        [
            "## Summary",
            f"- **Recording offset (playback delay):** {analysis.offset_s * 1000:.1f} ms",
            f"- **Clock drift:** {analysis.drift_ppm:.1f} ppm",
            (
                f"- **Noise floor (ch0):** {analysis.noise_floor_dbfs:.1f} dBFS "
                f"(margin: {analysis.margin_db:.1f} dB)"
            ),
            "",
            "## Headline Results",
            "",
            "| Metric | n | p50 (s) | p95 (s) | Mean (s) | Notes |",
            "|---|---|---|---|---|---|",
        ]
    )

    def row(name: str, s: MetricStats) -> str:
        notes = "low n" if s.low_n and s.n > 0 else ("no trials" if s.n == 0 else "")
        return f"| {name} | {s.n} | {_fmt(s.p50)} | {_fmt(s.p95)} | {_fmt(s.mean)} | {notes} |"

    lines.append(row("First Audio (T4 - T0)", analysis.first_audio))
    lines.append(row("First Answer Audio", analysis.first_answer_audio))
    lines.append(row("Barge-in Stop", analysis.barge_in_stop))
    lines.append(row("Barge-in Duck", analysis.barge_in_duck))

    noise_valid = sum(1 for r in analysis.trials if r.kind == "noise" and r.status != "invalid")
    lines.append(
        f"| False Interruption Rate | {noise_valid} | - | - | "
        f"{_fmt_pct(analysis.false_interruption_rate)} | rate = false / valid |"
    )

    pause_valid = sum(1 for r in analysis.trials if r.kind == "pause" and r.status != "invalid")
    lines.append(
        f"| Premature Takeover Rate | {pause_valid} | - | - | "
        f"{_fmt_pct(analysis.premature_takeover_rate)} | rate = takeovers / valid |"
    )

    lines.extend(
        [
            "",
            "## Trial Counts",
            f"- **Total trials:** {len(analysis.trials)}",
            f"- **OK:** {analysis.counts.get('ok', 0)}",
            f"- **Missed:** {analysis.counts.get('missed', 0)}",
            f"- **Crosstalk:** {analysis.counts.get('crosstalk', 0)}",
            f"- **Invalid:** {analysis.counts.get('invalid', 0)}",
            "",
        ]
    )

    missed_ids = [r.id for r in analysis.trials if r.status == "missed"]
    if missed_ids:
        lines.extend(
            [
                "### Missed Trials",
                ", ".join(missed_ids),
                "",
            ]
        )

    if analysis.stages:
        lines.extend(
            [
                "## Stage Breakdown",
                "",
                "| Stage Delta | n | p50 (s) | p95 (s) | Mean (s) | Notes |",
                "|---|---|---|---|---|---|",
            ]
        )
        for stage_name, stat in sorted(analysis.stages.items()):
            lines.append(row(stage_name, stat))
        lines.append("")

    if analysis.health:
        temp_str = _fmt(float(analysis.health.get("max_temp_c", 0.0)), 1)
        arm_str = _fmt(float(analysis.health.get("min_arm_mhz", 0.0)), 0)
        cpu_str = _fmt(float(analysis.health.get("median_cpu_pct", 0.0)), 1)
        rss_str = _fmt(float(analysis.health.get("peak_rss_mb", 0.0)), 1)
        throttled_str = str(bool(analysis.health.get("throttled", False)))
        lines.extend(
            [
                "## Pi Health Summary",
                f"- **Max Temperature:** {temp_str} °C",
                f"- **Min ARM Clock:** {arm_str} MHz",
                f"- **Throttled flag set:** {throttled_str}",
                f"- **Median CPU:** {cpu_str}%",
                f"- **Peak RSS:** {rss_str} MB",
                "",
            ]
        )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return results_path, report_path
