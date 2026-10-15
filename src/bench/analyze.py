from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import soundfile as sf
from scipy.ndimage import minimum_filter1d
from scipy.signal import correlate

from audio_engine.types import AudioClip
from audio_engine.wav import resample as wav_resample
from voice_agent.telemetry.events import Stage, read_events
from voice_agent.telemetry.pi_health import decode_throttled

from .signal import frame_rms_dbfs, make_chirp
from .truth import TruthSet, load_truth

_HOP_MS = 1.0
_FRAME_MS = 10.0


@dataclass(frozen=True)
class TrialResult:
    id: str
    kind: str
    status: Literal["ok", "missed", "crosstalk", "invalid"]
    t0_rec_s: float
    first_audio_s: float | None  # T4 - T0
    first_answer_audio_s: float | None
    barge_in_stop_s: float | None
    barge_in_duck_s: float | None  # None if the bot never ducked
    false_interruption: bool | None
    premature_takeover: bool | None  # pause trials only


@dataclass(frozen=True)
class MetricStats:
    n: int
    p50: float | None
    p95: float | None
    mean: float | None
    low_n: bool


@dataclass(frozen=True)
class Analysis:
    offset_s: float
    drift_ppm: float
    noise_floor_dbfs: float
    margin_db: float
    trials: list[TrialResult]
    first_audio: MetricStats
    first_answer_audio: MetricStats
    barge_in_stop: MetricStats
    barge_in_duck: MetricStats
    false_interruption_rate: float | None
    premature_takeover_rate: float | None
    counts: dict[str, int]  # ok/missed/crosstalk/invalid
    stages: dict[str, MetricStats] | None  # "endpoint->stt_final", ...
    health: dict[str, float | bool] | None
    filler_rate: dict[str, float] | None = None

    @property
    def filler_rates(self) -> dict[str, float] | None:
        return self.filler_rate


def _calc_metric_stats(values: Sequence[float]) -> MetricStats:
    n = len(values)
    if n == 0:
        return MetricStats(n=0, p50=None, p95=None, mean=None, low_n=True)
    arr = np.asarray(values, dtype=np.float64)
    return MetricStats(
        n=n,
        p50=float(np.percentile(arr, 50)),
        p95=float(np.percentile(arr, 95)),
        mean=float(np.mean(arr)),
        low_n=bool(n < 20),
    )


def _read_multichannel_wav(path: Path) -> tuple[npt.NDArray[np.float32], int]:
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    arr = np.asarray(data, dtype=np.float32)
    if arr.shape[1] < 2:
        raise ValueError("need bot + loopback channels")
    return arr, int(rate)


def _read_stimulus(path: Path, target_sr: int) -> npt.NDArray[np.float32]:
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.asarray(data[:, 0], dtype=np.float32)
    if int(rate) != target_sr:
        clip = AudioClip(samples=mono, sample_rate=int(rate))
        resampled = wav_resample(clip, target_sr)
        return resampled.samples
    return mono


def _correlate_normalized(
    x: npt.NDArray[np.float32], y: npt.NDArray[np.float32]
) -> tuple[int, float]:
    m = len(y)
    if len(x) < m:
        return 0, 0.0
    c = correlate(x, y, mode="valid", method="fft")
    cs = np.pad(np.cumsum(x.astype(np.float64) ** 2), (1, 0))
    ex = cs[m:] - cs[:-m]
    ey = float(np.sum(y.astype(np.float64) ** 2))
    denom = np.sqrt(np.maximum(ex * ey, 1e-12))
    mask = denom > 1e-4
    r = np.zeros_like(c)
    r[mask] = c[mask] / denom[mask]
    best_idx = int(np.argmax(r))
    peak = float(r[best_idx])
    return best_idx, peak


def _find_onset(
    rms: npt.NDArray[np.float64],
    hop_s: float,
    start_s: float,
    end_s: float,
    thresh_dbfs: float,
    hold_s: float = 0.030,
) -> float | None:
    start_idx = max(0, int(np.ceil(start_s / hop_s)))
    end_idx = min(len(rms), int(np.floor(end_s / hop_s)))
    if start_idx >= end_idx:
        return None
    hold_frames = max(1, int(round(hold_s / hop_s)))
    above = rms[start_idx:end_idx] >= thresh_dbfs
    if len(above) < hold_frames:
        return None
    k = hold_frames
    sums = np.convolve(above.astype(np.int32), np.ones(k, dtype=np.int32), mode="valid")
    hits = np.flatnonzero(sums == k)
    if len(hits) == 0:
        return None
    return float((start_idx + hits[0]) * hop_s)


def _find_stop(
    rms: npt.NDArray[np.float64],
    hop_s: float,
    start_s: float,
    end_s: float,
    thresh_dbfs: float,
    hold_s: float = 0.200,
) -> float | None:
    start_idx = max(0, int(np.ceil(start_s / hop_s)))
    end_idx = min(len(rms), int(np.floor(end_s / hop_s)))
    if start_idx >= end_idx:
        return None
    hold_frames = max(1, int(round(hold_s / hop_s)))
    below = rms[start_idx:end_idx] < thresh_dbfs
    if len(below) < hold_frames:
        return None
    k = hold_frames
    sums = np.convolve(below.astype(np.int32), np.ones(k, dtype=np.int32), mode="valid")
    hits = np.flatnonzero(sums == k)
    if len(hits) == 0:
        return None
    return float((start_idx + hits[0]) * hop_s)


def _find_duck(
    rms: npt.NDArray[np.float64],
    hop_s: float,
    event_s: float,
    end_s: float,
    pre_window_s: float = 0.200,
    duck_db: float = 10.0,
    hold_s: float = 0.030,
) -> float | None:
    pre_start_idx = max(0, int(round((event_s - pre_window_s) / hop_s)))
    pre_end_idx = max(pre_start_idx + 1, int(round(event_s / hop_s)))
    if pre_start_idx >= len(rms):
        return None
    pre_rms = float(np.median(rms[pre_start_idx:pre_end_idx]))
    duck_thresh = pre_rms - duck_db

    start_idx = int(round(event_s / hop_s))
    end_idx = min(len(rms), int(np.floor(end_s / hop_s)))
    if start_idx >= end_idx:
        return None
    hold_frames = max(1, int(round(hold_s / hop_s)))
    below = rms[start_idx:end_idx] <= duck_thresh
    if len(below) < hold_frames:
        return None
    k = hold_frames
    sums = np.convolve(below.astype(np.int32), np.ones(k, dtype=np.int32), mode="valid")
    hits = np.flatnonzero(sums == k)
    if len(hits) == 0:
        return None
    return float((start_idx + hits[0]) * hop_s)


def _check_crosstalk(
    rms: npt.NDArray[np.float64],
    hop_s: float,
    start_s: float,
    end_s: float,
    thresh_dbfs: float,
) -> bool:
    start_idx = max(0, int(round(start_s / hop_s)))
    end_idx = min(len(rms), int(round(end_s / hop_s)))
    if start_idx >= end_idx:
        return False
    return bool(np.max(rms[start_idx:end_idx]) >= thresh_dbfs)


def _align(
    ch1: npt.NDArray[np.float32],
    stim: npt.NDArray[np.float32],
    rec_sr: int,
    truth: TruthSet,
) -> tuple[float, float, float]:
    chirp = make_chirp(rec_sr, duration_s=0.5)
    best_idx, peak = _correlate_normalized(ch1, chirp)
    if peak < 0.3:
        raise ValueError("loopback channel has no chirp: check In 2 wiring")
    offset_s = best_idx / rec_sr

    drift_ppm = 0.0
    drift_ratio = 0.0

    if truth.trials:
        last_trial = truth.trials[-1]
        t_start_s = last_trial.t_start_s
        t_end_s = last_trial.t_speech_end_s
        s_start = round(t_start_s * rec_sr)
        s_end = round(t_end_s * rec_sr)
        if s_end > s_start and s_end <= len(stim):
            template = stim[s_start:s_end]
            s_exp = int(round((offset_s + t_start_s) * rec_sr))
            search_margin = int(round(2.0 * rec_sr))
            slice_start = max(0, s_exp - search_margin)
            slice_end = min(len(ch1), s_exp + len(template) + search_margin)
            ch1_slice = ch1[slice_start:slice_end]
            if len(ch1_slice) >= len(template):
                c_last = correlate(ch1_slice, template, mode="valid", method="fft")
                best_in_slice = int(np.argmax(c_last))
                s_rec_last = slice_start + best_in_slice
                t_rec_last = s_rec_last / rec_sr
                offset_last = t_rec_last - t_start_s
                disagreement = offset_last - offset_s
                if abs(disagreement) > 0.005 and t_start_s > 0:
                    drift_ratio = disagreement / t_start_s
                    drift_ppm = drift_ratio * 1e6

    return offset_s, drift_ppm, drift_ratio


def _measure_noise_floor(ch0: npt.NDArray[np.float32], rec_sr: int, offset_s: float) -> float:
    s_chirp = int(round(offset_s * rec_sr))
    lead_start = max(0, s_chirp - rec_sr)
    lead_slice = ch0[lead_start:s_chirp] if s_chirp > lead_start else ch0[:rec_sr]
    lead_rms = frame_rms_dbfs(lead_slice, rec_sr, frame_ms=_FRAME_MS, hop_ms=_HOP_MS)
    return float(np.median(lead_rms))


def _parse_health_log(path: Path) -> dict[str, float | bool] | None:
    if not path.exists():
        return None
    temps: list[float] = []
    arm_freqs: list[float] = []
    cpus: list[float] = []
    rsss: list[float] = []
    any_throttled = False

    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("event") != "health":
                continue
            t_val = rec.get("temp_c")
            if isinstance(t_val, (int, float)):
                temps.append(float(t_val))
            arm_val = rec.get("arm_mhz")
            if isinstance(arm_val, (int, float)):
                arm_freqs.append(float(arm_val))
            cpu_val = rec.get("cpu_pct")
            if isinstance(cpu_val, (int, float)):
                cpus.append(float(cpu_val))
            proc_val = rec.get("proc")
            if isinstance(proc_val, dict) and isinstance(proc_val.get("rss_mb"), (int, float)):
                rsss.append(float(proc_val["rss_mb"]))

            thr = rec.get("throttled")
            if thr is not None:
                if isinstance(thr, dict):
                    raw = str(thr.get("raw", "0x0"))
                    if any(
                        thr.get(k) is True
                        for k in (
                            "under_voltage_now",
                            "freq_capped_now",
                            "throttled_now",
                            "soft_temp_limit_now",
                            "under_voltage_since_boot",
                            "freq_capped_since_boot",
                            "throttled_since_boot",
                            "soft_temp_limit_since_boot",
                        )
                    ):
                        any_throttled = True
                    else:
                        clean = raw.removeprefix("throttled=").strip()
                        try:
                            if int(clean, 16) != 0:
                                any_throttled = True
                        except ValueError:
                            pass
                elif isinstance(thr, str):
                    tf = decode_throttled(thr)
                    if any(
                        getattr(tf, k)
                        for k in (
                            "under_voltage_now",
                            "freq_capped_now",
                            "throttled_now",
                            "soft_temp_limit_now",
                            "under_voltage_since_boot",
                            "freq_capped_since_boot",
                            "throttled_since_boot",
                            "soft_temp_limit_since_boot",
                        )
                    ):
                        any_throttled = True

    if not (temps or arm_freqs or cpus or rsss):
        return None

    return {
        "max_temp_c": float(max(temps)) if temps else 0.0,
        "min_arm_mhz": float(min(arm_freqs)) if arm_freqs else 0.0,
        "throttled": any_throttled,
        "median_cpu_pct": float(np.median(cpus)) if cpus else 0.0,
        "peak_rss_mb": float(max(rsss)) if rsss else 0.0,
    }


def analyze(
    recording: Path,
    stimulus: Path,
    truth: Path,
    *,
    stage_log: Path | None = None,
    health_log: Path | None = None,
    margin_db: float = 12.0,
) -> Analysis:
    rec_audio, rec_sr = _read_multichannel_wav(recording)
    ch0 = rec_audio[:, 0]
    ch1 = rec_audio[:, 1]

    stim_audio = _read_stimulus(stimulus, rec_sr)
    truth_set = load_truth(truth)

    offset_s, drift_ppm, drift_ratio = _align(ch1, stim_audio, rec_sr, truth_set)

    def to_rec(t: float) -> float:
        return offset_s + t * (1.0 + drift_ratio)

    noise_floor_dbfs = _measure_noise_floor(ch0, rec_sr, offset_s)
    thresh_dbfs = noise_floor_dbfs + margin_db
    rms_ch0 = frame_rms_dbfs(ch0, rec_sr, frame_ms=_FRAME_MS, hop_ms=_HOP_MS)
    hop_s = _HOP_MS / 1000.0

    turn_events_map: dict[int, list[Any]] = {}
    if stage_log is not None and stage_log.exists():
        events, _ = read_events(stage_log)
        for ev in events:
            turn_events_map.setdefault(ev.turn_id, []).append(ev)

    results: list[TrialResult] = []
    counts: dict[str, int] = {"ok": 0, "missed": 0, "crosstalk": 0, "invalid": 0}

    for idx, tr in enumerate(truth_set.trials, 1):
        t0_rec = to_rec(tr.t_speech_end_s)
        t_start_rec = to_rec(tr.t_start_s)
        win_end_rec = to_rec(tr.window_end_s)
        t_event_rec = to_rec(tr.t_event_s) if tr.t_event_s is not None else None

        has_crosstalk = _check_crosstalk(rms_ch0, hop_s, t_start_rec, t0_rec, thresh_dbfs)

        status: Literal["ok", "missed", "crosstalk", "invalid"] = "ok"
        first_audio_s: float | None = None
        first_answer_audio_s: float | None = None
        barge_in_stop_s: float | None = None
        barge_in_duck_s: float | None = None
        false_interruption: bool | None = None
        premature_takeover: bool | None = None

        if tr.kind == "barge_in":
            assert t_event_rec is not None
            ev_idx = int(round(t_event_rec / hop_s))
            bot_active = ev_idx < len(rms_ch0) and rms_ch0[ev_idx] >= thresh_dbfs
            if not bot_active:
                status = "invalid"
            else:
                stop_time = _find_stop(
                    rms_ch0, hop_s, t_event_rec, win_end_rec, thresh_dbfs, hold_s=0.200
                )
                if stop_time is not None:
                    barge_in_stop_s = stop_time - t_event_rec
                duck_end = stop_time if stop_time is not None else win_end_rec
                duck_time = _find_duck(
                    rms_ch0, hop_s, t_event_rec, duck_end, duck_db=10.0, hold_s=0.030
                )
                if duck_time is not None:
                    barge_in_duck_s = duck_time - t_event_rec

        elif tr.kind == "noise":
            assert t_event_rec is not None
            ev_idx = int(round(t_event_rec / hop_s))
            bot_active = ev_idx < len(rms_ch0) and rms_ch0[ev_idx] >= thresh_dbfs
            if not bot_active:
                status = "invalid"
            else:
                stop_time = _find_stop(
                    rms_ch0,
                    hop_s,
                    t_event_rec,
                    min(win_end_rec, t_event_rec + 1.0),
                    thresh_dbfs,
                    hold_s=0.200,
                )
                false_interruption = stop_time is not None
                duck_end = stop_time if stop_time is not None else win_end_rec
                duck_time = _find_duck(
                    rms_ch0, hop_s, t_event_rec, duck_end, duck_db=10.0, hold_s=0.030
                )
                if duck_time is not None:
                    barge_in_duck_s = duck_time - t_event_rec

        elif tr.kind == "pause":
            assert t_event_rec is not None
            takeover_time = _find_onset(
                rms_ch0, hop_s, t_event_rec, t0_rec, thresh_dbfs, hold_s=0.030
            )
            premature_takeover = takeover_time is not None

        if status != "invalid":
            if has_crosstalk:
                status = "crosstalk"
            else:
                if tr.kind != "pause" or premature_takeover is False:
                    t4 = _find_onset(rms_ch0, hop_s, t0_rec, win_end_rec, thresh_dbfs, hold_s=0.030)
                    if t4 is None:
                        status = "missed"
                    else:
                        first_audio_s = t4 - t0_rec
                        first_answer_audio_s = first_audio_s

                        turn_evs = turn_events_map.get(idx, [])
                        filler_evs = [e for e in turn_evs if e.stage == Stage.FILLER_PLAYED]
                        filler_evs.sort(key=lambda e: e.t_mono_ns)
                        if filler_evs:
                            cur_onset: float | None = t4
                            filler_end: float = t4
                            for f_idx, f_ev in enumerate(filler_evs):
                                if f_idx > 0:
                                    cur_onset = _find_onset(
                                        rms_ch0,
                                        hop_s,
                                        filler_end,
                                        win_end_rec,
                                        thresh_dbfs,
                                        hold_s=0.030,
                                    )
                                if cur_onset is None:
                                    break
                                filler_dur_s = float(f_ev.data.get("duration_ms", 0)) / 1000.0
                                clip_path = f_ev.data.get("clip")
                                filler_end = cur_onset + filler_dur_s
                                if clip_path and Path(clip_path).exists():
                                    try:
                                        clip_data, clip_sr = sf.read(
                                            str(clip_path), dtype="float32", always_2d=True
                                        )
                                        clip_mono = clip_data[:, 0]
                                        if int(clip_sr) != rec_sr:
                                            clip_obj = AudioClip(
                                                samples=clip_mono,
                                                sample_rate=int(clip_sr),
                                            )
                                            resampled = wav_resample(clip_obj, rec_sr)
                                            clip_mono = resampled.samples
                                        filler_dur_s = len(clip_mono) / rec_sr
                                        f_start_idx = max(0, int(round((cur_onset - 0.1) * rec_sr)))
                                        f_end_idx = min(
                                            len(ch0),
                                            int(round((cur_onset + filler_dur_s + 0.5) * rec_sr)),
                                        )
                                        search_slice = ch0[f_start_idx:f_end_idx]
                                        if len(search_slice) >= len(clip_mono):
                                            c_clip = correlate(
                                                search_slice,
                                                clip_mono,
                                                mode="valid",
                                                method="fft",
                                            )
                                            f_pos = f_start_idx + int(np.argmax(c_clip))
                                            filler_end = (f_pos / rec_sr) + filler_dur_s
                                        else:
                                            filler_end = cur_onset + filler_dur_s
                                    except Exception:
                                        pass

                            ans_t4 = _find_onset(
                                rms_ch0,
                                hop_s,
                                filler_end,
                                win_end_rec,
                                thresh_dbfs,
                                hold_s=0.030,
                            )
                            first_answer_audio_s = ans_t4 - t0_rec if ans_t4 is not None else None

        counts[status] += 1
        results.append(
            TrialResult(
                id=tr.id,
                kind=tr.kind,
                status=status,
                t0_rec_s=float(t0_rec),
                first_audio_s=first_audio_s,
                first_answer_audio_s=first_answer_audio_s,
                barge_in_stop_s=barge_in_stop_s,
                barge_in_duck_s=barge_in_duck_s,
                false_interruption=false_interruption,
                premature_takeover=premature_takeover,
            )
        )

    valid_first_audio = [
        r.first_audio_s
        for r in results
        if r.status == "ok"
        and r.first_audio_s is not None
        and not (r.kind == "pause" and r.premature_takeover is True)
    ]
    valid_first_ans = [
        r.first_answer_audio_s
        for r in results
        if r.status == "ok"
        and r.first_answer_audio_s is not None
        and not (r.kind == "pause" and r.premature_takeover is True)
    ]
    valid_barge_stop = [
        r.barge_in_stop_s
        for r in results
        if r.status == "ok" and r.kind == "barge_in" and r.barge_in_stop_s is not None
    ]
    valid_barge_duck = [
        r.barge_in_duck_s
        for r in results
        if r.status == "ok" and r.kind == "barge_in" and r.barge_in_duck_s is not None
    ]

    valid_noise = [r for r in results if r.kind == "noise" and r.status != "invalid"]
    false_interruptions = [r for r in valid_noise if r.false_interruption is True]
    false_int_rate = len(false_interruptions) / len(valid_noise) if valid_noise else None

    valid_pause = [r for r in results if r.kind == "pause" and r.status != "invalid"]
    takeovers = [r for r in valid_pause if r.premature_takeover is True]
    takeover_rate = len(takeovers) / len(valid_pause) if valid_pause else None

    stages_dict: dict[str, MetricStats] | None = None
    if turn_events_map:
        stage_deltas: dict[str, list[float]] = {}
        for ev_list in turn_events_map.values():
            endpoint_ev = next((e for e in ev_list if e.stage == Stage.ENDPOINT), None)
            if endpoint_ev is None:
                continue
            seen_stages: set[Stage] = set()
            for ev in sorted(ev_list, key=lambda e: e.t_mono_ns):
                if ev.t_mono_ns > endpoint_ev.t_mono_ns and ev.stage not in seen_stages:
                    seen_stages.add(ev.stage)
                    key = f"endpoint->{ev.stage.value}"
                    delta_s = (ev.t_mono_ns - endpoint_ev.t_mono_ns) / 1e9
                    stage_deltas.setdefault(key, []).append(delta_s)
        if stage_deltas:
            stages_dict = {k: _calc_metric_stats(v) for k, v in stage_deltas.items()}

    filler_rate_dict: dict[str, float] | None = None
    if turn_events_map:
        kinds: set[str] = {"delegate", "stall", "notice"}
        kind_turns: dict[str, set[int]] = {k: set() for k in kinds}
        for turn_id, ev_list in turn_events_map.items():
            for ev in ev_list:
                if ev.stage == Stage.FILLER_PLAYED:
                    kind = str(ev.data.get("kind", "unknown"))
                    kind_turns.setdefault(kind, set()).add(turn_id)
        total_trials = len(truth_set.trials)
        if total_trials > 0:
            filler_rate_dict = {
                k: len(turns) / total_trials for k, turns in sorted(kind_turns.items())
            }

    health_dict: dict[str, float | bool] | None = None
    if health_log is not None:
        health_dict = _parse_health_log(health_log)

    return Analysis(
        offset_s=float(offset_s),
        drift_ppm=float(drift_ppm),
        noise_floor_dbfs=float(noise_floor_dbfs),
        margin_db=float(margin_db),
        trials=results,
        first_audio=_calc_metric_stats(valid_first_audio),
        first_answer_audio=_calc_metric_stats(valid_first_ans),
        barge_in_stop=_calc_metric_stats(valid_barge_stop),
        barge_in_duck=_calc_metric_stats(valid_barge_duck),
        false_interruption_rate=false_int_rate,
        premature_takeover_rate=takeover_rate,
        counts=counts,
        stages=stages_dict,
        health=health_dict,
        filler_rate=filler_rate_dict,
    )


def calibrate(recording_bot_disabled: Path, stimulus: Path, truth: Path) -> float:
    rec_audio, rec_sr = _read_multichannel_wav(recording_bot_disabled)
    ch0 = rec_audio[:, 0]
    ch1 = rec_audio[:, 1]
    stim_audio = _read_stimulus(stimulus, rec_sr)
    truth_set = load_truth(truth)

    offset_s, _, _ = _align(ch1, stim_audio, rec_sr, truth_set)
    noise_floor_dbfs = _measure_noise_floor(ch0, rec_sr, offset_s)

    rms = frame_rms_dbfs(ch0, rec_sr, frame_ms=_FRAME_MS, hop_ms=_HOP_MS)
    k = max(1, int(round(0.030 / (_HOP_MS / 1000.0))))
    if len(rms) < k:
        return 0.0

    sustained = minimum_filter1d(rms, size=k, origin=-(k // 2))
    max_sustained = float(np.max(sustained))
    min_margin = max(0.0, max_sustained - noise_floor_dbfs)
    safe_margin = float(np.round(min_margin + 0.1, 1))
    return safe_margin


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.analyze")
    subparsers = parser.add_subparsers(dest="command", required=True)

    score_p = subparsers.add_parser("score", help="Score a rig recording")
    score_p.add_argument("recording", type=Path, help="Rig recording WAV (>=2 ch)")
    score_p.add_argument("stimulus", type=Path, help="Stimulus WAV")
    score_p.add_argument("truth", type=Path, help="Truth JSON")
    score_p.add_argument("--stage-log", type=Path, default=None, help="Stage events JSONL")
    score_p.add_argument("--health-log", type=Path, default=None, help="Health JSONL")
    score_p.add_argument("--margin-db", type=float, default=12.0, help="Onset margin in dB")
    score_p.add_argument("--out-dir", type=Path, default=Path("out/bench"), help="Output dir")
    score_p.add_argument("--title", type=str, default="Benchmark Analysis", help="Report title")

    cal_p = subparsers.add_parser("calibrate", help="Calibrate margin on bot-disabled recording")
    cal_p.add_argument("recording", type=Path, help="Recording with bot disabled")
    cal_p.add_argument("stimulus", type=Path, help="Stimulus WAV")
    cal_p.add_argument("truth", type=Path, help="Truth JSON")

    args = parser.parse_args(argv)

    if args.command == "calibrate":
        margin = calibrate(args.recording, args.stimulus, args.truth)
        print(f"Smallest safe margin_db: {margin:.1f} dB")
        return 0
    elif args.command == "score":
        from .report import write_report

        res = analyze(
            args.recording,
            args.stimulus,
            args.truth,
            stage_log=args.stage_log,
            health_log=args.health_log,
            margin_db=args.margin_db,
        )
        json_path, md_path = write_report(res, args.out_dir, title=args.title)
        print(f"Results written to:\n  {json_path}\n  {md_path}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
