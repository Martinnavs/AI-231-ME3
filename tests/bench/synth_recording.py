from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from audio_engine.types import AudioClip
from audio_engine.wav import resample as wav_resample
from bench.truth import TruthSet

FillerSpec = tuple[Path | None, float, float]


def _make_bot_audio(
    duration_s: float, sr: int, level_dbfs: float = -20.0, seed: int = 42
) -> np.ndarray:
    n = max(1, int(round(duration_s * sr)))
    t = np.arange(n) / sr
    # Mix sines with pseudo-speech frequencies for realistic RMS stability
    s = (
        0.5 * np.sin(2 * np.pi * 300 * t)
        + 0.3 * np.sin(2 * np.pi * 700 * t)
        + 0.2 * np.sin(2 * np.pi * 1500 * t)
    )
    rms = float(np.sqrt(np.mean(s**2)))
    target_rms = float(10 ** (level_dbfs / 20.0))
    s = s * (target_rms / max(rms, 1e-12))
    return s.astype(np.float32)


def build_synth_recording(
    truth: TruthSet,
    stimulus_wav: Path,
    out_path: Path,
    *,
    playback_delay_s: float = 0.0,
    drift_ppm: float = 0.0,
    noise_floor_dbfs: float = -60.0,
    noise_fluctuation_db: float = 0.0,
    bot_latencies: dict[str, float] | None = None,
    default_bot_latency: float | None = None,
    bot_durations: dict[str, float] | None = None,
    bot_level_dbfs: float = -20.0,
    crosstalk_db: dict[str, float] | None = None,
    barge_in_stops: dict[str, float] | None = None,
    barge_in_ducks: dict[str, float] | None = None,
    false_interruptions: set[str] | None = None,
    pause_takeovers: dict[str, float] | None = None,
    fillers: dict[str, FillerSpec | list[FillerSpec]] | None = None,
    rec_sr: int | None = None,
    seed: int = 1234,
) -> Path:
    stim_data, stim_sr = sf.read(str(stimulus_wav), dtype="float32", always_2d=True)
    stim = stim_data[:, 0]
    sr = rec_sr if rec_sr is not None else int(stim_sr)
    if sr != int(stim_sr):
        stim_clip = AudioClip(samples=stim, sample_rate=int(stim_sr))
        stim = wav_resample(stim_clip, sr).samples
    drift_ratio = drift_ppm * 1e-6

    # Calculate total duration needed in recording time
    total_stim_s = len(stim) / sr
    total_rec_s = playback_delay_s + total_stim_s * (1.0 + drift_ratio) + 2.0
    n_rec = int(np.ceil(total_rec_s * sr))

    # Channel 1: Loopback
    t_rec_samples = np.arange(n_rec) / sr
    t_stim_eval = (t_rec_samples - playback_delay_s) / (1.0 + drift_ratio)
    ch1 = np.interp(t_stim_eval, np.arange(len(stim)) / sr, stim, left=0.0, right=0.0).astype(
        np.float32
    )

    # Channel 0: Bot channel
    rng = np.random.default_rng(seed)
    noise_amp = float(10 ** (noise_floor_dbfs / 20.0))
    noise = rng.normal(0, noise_amp, size=n_rec).astype(np.float32)
    if noise_fluctuation_db > 0:
        fluct = 10 ** ((noise_fluctuation_db * np.sin(2 * np.pi * 0.5 * t_rec_samples)) / 20.0)
        noise = (noise * fluct).astype(np.float32)

    ch0 = noise

    def to_rec(stim_t: float) -> float:
        return playback_delay_s + stim_t * (1.0 + drift_ratio)

    def add_ch0(start_t: float, signal: np.ndarray) -> None:
        s_idx = int(round(start_t * sr))
        e_idx = s_idx + len(signal)
        if s_idx < 0:
            signal = signal[-s_idx:]
            s_idx = 0
        if s_idx >= n_rec:
            return
        if e_idx > n_rec:
            signal = signal[: n_rec - s_idx]
            e_idx = n_rec
        ch0[s_idx:e_idx] += signal

    for tr in truth.trials:
        t0 = to_rec(tr.t_speech_end_s)
        t_start = to_rec(tr.t_start_s)
        t_event = to_rec(tr.t_event_s) if tr.t_event_s is not None else None

        # Crosstalk injection
        if crosstalk_db and tr.id in crosstalk_db:
            leak_level = crosstalk_db[tr.id]
            stim_start_idx = int(round(tr.t_start_s * sr))
            stim_end_idx = int(round(tr.t_speech_end_s * sr))
            speech_clip = stim[stim_start_idx:stim_end_idx]
            sp_rms = float(np.sqrt(np.mean(speech_clip**2)))
            target_rms = float(10 ** (leak_level / 20.0))
            scaled_speech = speech_clip * (target_rms / max(sp_rms, 1e-12))
            add_ch0(t_start, scaled_speech)

        # Pause takeover
        if pause_takeovers and tr.id in pause_takeovers:
            dt = pause_takeovers[tr.id]
            assert t_event is not None
            takeover_t = t_event + dt
            takeover_audio = _make_bot_audio(1.0, sr, level_dbfs=bot_level_dbfs)
            add_ch0(takeover_t, takeover_audio)
            continue

        # Fillers
        if fillers and tr.id in fillers:
            spec_val = fillers[tr.id]
            filler_list = spec_val if isinstance(spec_val, list) else [spec_val]
            lat = (
                bot_latencies.get(tr.id, default_bot_latency or 0.5)
                if bot_latencies
                else (default_bot_latency or 0.5)
            )
            cur_filler_t = t0 + lat
            for clip_path, filler_dur, gap in filler_list:
                if clip_path and Path(clip_path).exists():
                    c_data, c_sr = sf.read(str(clip_path), dtype="float32", always_2d=True)
                    c_mono = c_data[:, 0]
                    if int(c_sr) != sr:
                        c_clip = AudioClip(samples=c_mono, sample_rate=int(c_sr))
                        c_mono = wav_resample(c_clip, sr).samples
                    f_audio = c_mono
                else:
                    f_audio = _make_bot_audio(filler_dur, sr, level_dbfs=bot_level_dbfs)
                add_ch0(cur_filler_t, f_audio)
                cur_filler_t += len(f_audio) / sr + gap
            ans_audio = _make_bot_audio(1.5, sr, level_dbfs=bot_level_dbfs)
            add_ch0(cur_filler_t, ans_audio)
            continue

        # Normal bot speech
        lat = (
            bot_latencies.get(tr.id, default_bot_latency) if bot_latencies else default_bot_latency
        )
        if lat is None:
            continue

        bot_start = t0 + lat
        dur = bot_durations.get(tr.id, 2.0) if bot_durations else 2.0

        if tr.kind in ("barge_in", "noise") and t_event is not None:
            # Bot must be active at t_event
            if bot_start > t_event:
                bot_start = max(t0, t_event - 0.5)
            dur = max(dur, (t_event - bot_start) + 2.5)

        bot_audio = _make_bot_audio(dur, sr, level_dbfs=bot_level_dbfs)

        if tr.kind == "barge_in" and t_event is not None:
            rel_event = t_event - bot_start
            duck_s = barge_in_ducks.get(tr.id) if barge_in_ducks else None
            stop_s = barge_in_stops.get(tr.id) if barge_in_stops else None

            if duck_s is not None:
                duck_idx = int(round((rel_event + duck_s) * sr))
                if duck_idx < len(bot_audio):
                    bot_audio[duck_idx:] *= float(10 ** (-15.0 / 20.0))

            if stop_s is not None:
                stop_idx = int(round((rel_event + stop_s) * sr))
                if stop_idx < len(bot_audio):
                    bot_audio[stop_idx:] = 0.0

        elif tr.kind == "noise" and t_event is not None:
            rel_event = t_event - bot_start
            duck_s = barge_in_ducks.get(tr.id) if barge_in_ducks else None
            is_false = (tr.id in false_interruptions) if false_interruptions else False

            if duck_s is not None:
                duck_idx = int(round((rel_event + duck_s) * sr))
                recover_idx = int(round((rel_event + duck_s + 0.3) * sr))
                if duck_idx < len(bot_audio):
                    duck_slice = bot_audio[duck_idx:recover_idx]
                    bot_audio[duck_idx:recover_idx] = duck_slice * float(10 ** (-15.0 / 20.0))

            if is_false:
                stop_idx = int(round((rel_event + 0.2) * sr))
                if stop_idx < len(bot_audio):
                    bot_audio[stop_idx:] = 0.0

        add_ch0(bot_start, bot_audio)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rec_data = np.stack([ch0, ch1], axis=1)
    sf.write(str(out_path), rec_data, sr, subtype="PCM_16")
    return out_path
