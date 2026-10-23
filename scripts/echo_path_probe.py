"""What is the path from the laptop, through the Pi's speaker, back to the laptop microphone?

    LAN_SPEECH_TOKEN=... uv run --group laptop python scripts/echo_path_probe.py \
        --url ws://<pi>:8780 --out out/remote_echo/probe-80

Stay silent for the 45 s it runs. It sends known test signals through the Pi speaker and records
the microphone: silence (the room), two bursts of band-limited noise 12 dB apart, and a 1 kHz
tone. From them it reports, for the noise bursts, how steady the delay is (if it wanders by
milliseconds a linear echo canceller cannot follow), how much of the microphone signal is a
linear copy of what was sent (coherence, by band; 1.0 is a perfect copy), whether the gain
changes with level (a compressing or clipping speaker), the distortion of the tone, and how far
the echo is above the room. `analyse` also works on any recording made with the same signals.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import butter, coherence, sosfilt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

RATE = 16000
FLOOR_S, NOISE_S, TONE_S, GAP_S = 6.0, 10.0, 3.0, 1.0
LOUD_RMS, QUIET_RMS = 0.08, 0.02  # -22 and -34 dBFS: a reply is about -22


def make_signals() -> dict[str, np.ndarray]:
    """name -> samples (float, +-1): the loud noise, the quiet noise, the 1 kHz tone."""
    rng = np.random.default_rng(7)
    sos = butter(4, [150, 6000], btype="band", fs=RATE, output="sos")
    noise = sosfilt(sos, rng.normal(size=int(NOISE_S * RATE)))
    noise /= np.sqrt(np.mean(noise**2))
    t = np.arange(int(TONE_S * RATE)) / RATE
    return {
        "loud": noise * LOUD_RMS,
        "quiet": noise * QUIET_RMS,
        "tone": 0.2 * np.sin(2 * np.pi * 1000 * t),
    }


def layout() -> list[tuple[str, float]]:
    """(signal name, start second in the played stream)."""
    out, t = [], 0.0
    for name, dur in (("loud", NOISE_S), ("quiet", NOISE_S), ("tone", TONE_S)):
        out.append((name, t))
        t += dur + GAP_S
    return out


def played_stream() -> np.ndarray:
    sig = make_signals()
    total = int((layout()[-1][1] + TONE_S + GAP_S) * RATE)
    x = np.zeros(total)
    for name, start in layout():
        i = int(start * RATE)
        x[i : i + len(sig[name])] = sig[name]
    return x


def lag_of(ref: np.ndarray, mic: np.ndarray, search: int | None = None) -> int:
    """Samples by which `mic` is later than `ref` (peak of the cross-correlation)."""
    n = 1 << int(np.ceil(np.log2(len(mic) + len(ref))))
    c = np.fft.irfft(np.fft.rfft(mic, n) * np.conj(np.fft.rfft(ref, n)), n)
    c = c[: len(mic)] if search is None else c[:search]
    return int(np.argmax(c))


def parabolic(c: np.ndarray, i: int) -> float:
    if 0 < i < len(c) - 1:
        a, b, d = c[i - 1], c[i], c[i + 1]
        denom = a - 2 * b + d
        if denom != 0:
            return i + 0.5 * (a - d) / denom
    return float(i)


def analyse(mic: np.ndarray, played: np.ndarray | None = None) -> dict[str, Any]:
    """mic: float samples at 16 kHz of a run of `played_stream` (default)."""
    played = played_stream() if played is None else played
    sig = make_signals()
    lay = dict(layout())
    # where the stream starts in the recording: the loud noise is found anywhere in it
    start = lag_of(sig["loud"], mic) - int(lay["loud"] * RATE)
    res: dict[str, Any] = {"stream_start_s": round(start / RATE, 3)}
    floor = mic[: max(start - RATE // 2, RATE)]
    floor_rms = float(np.sqrt(np.mean(floor**2)))
    res["room_dbfs"] = round(20 * np.log10(max(floor_rms, 1e-6)), 1)
    seg = {}
    for name in ("loud", "quiet", "tone"):
        a = start + int(lay[name] * RATE)
        seg[name] = (a, a + len(sig[name]))
    # delay track: per 0.5 s block of the loud noise, peak of the cross-correlation near the start
    a, b = seg["loud"]
    track = []
    for s in range(0, len(sig["loud"]) - 8000, 8000):
        r = sig["loud"][s : s + 8000]
        m = mic[a + s - 160 : a + s + 8000 + 160]
        n = 1 << 14
        c = np.fft.irfft(np.fft.rfft(m, n) * np.conj(np.fft.rfft(r, n)), n)[:321]
        i = int(np.argmax(c))
        track.append(parabolic(c, i) - 160)
    track_a = np.array(track)
    res["delay_track_samples"] = [round(float(v), 1) for v in track_a]
    res["delay_std_ms"] = round(float(np.std(track_a)) / RATE * 1000, 3)
    res["delay_range_ms"] = round(float(np.ptp(track_a)) / RATE * 1000, 3)
    res["delay_drift_ms_per_min"] = round(
        float(np.polyfit(np.arange(len(track_a)) * 0.5, track_a, 1)[0]) / RATE * 1000 * 60, 2
    )
    # coherence by band, loud noise, at the median delay
    d = int(round(float(np.median(track_a))))
    x = sig["loud"]
    y = mic[a + d : a + d + len(x)]
    f, g2 = coherence(x, y, fs=RATE, nperseg=2048)
    res["coherence"] = {
        f"{lo}-{hi}": round(float(np.median(g2[(f >= lo) & (f < hi)])), 2)
        for lo, hi in (
            (150, 300),
            (300, 600),
            (600, 1000),
            (1000, 2000),
            (2000, 4000),
            (4000, 6000),
        )
    }
    # gain at the two levels (cross-spectrum over auto-spectrum), 300-3000 Hz
    gains = {}
    for name in ("loud", "quiet"):
        a2, _ = seg[name]
        m = len(sig[name]) // 2048 * 2048
        win = np.hanning(2048)
        X = np.fft.rfft(sig[name][:m].reshape(-1, 2048) * win, axis=1)
        Y = np.fft.rfft(mic[a2 + d : a2 + d + m].reshape(-1, 2048) * win, axis=1)
        freqs = np.fft.rfftfreq(2048, 1 / RATE)
        band = (freqs >= 300) & (freqs <= 3000)
        h = (np.conj(X) * Y).mean(axis=0) / ((np.abs(X) ** 2).mean(axis=0) + 1e-12)
        gains[name] = 20 * np.log10(np.median(np.abs(h[band])) + 1e-9)
    res["gain_db"] = {k: round(float(v), 1) for k, v in gains.items()}
    res["gain_change_loud_minus_quiet_db"] = round(float(gains["loud"] - gains["quiet"]), 1)
    # distortion of the 1 kHz tone
    a3, b3 = seg["tone"]
    t = mic[a3 + d + RATE // 2 : b3 + d - RATE // 4]
    spec = np.abs(np.fft.rfft(t * np.hanning(len(t))))
    fr = np.fft.rfftfreq(len(t), 1 / RATE)

    def level(f0: float) -> float:
        return float(spec[(fr > f0 - 25) & (fr < f0 + 25)].max())

    fund = level(1000)
    res["tone_harmonics_db_below_fundamental"] = {
        f"{k}k": round(20 * float(np.log10(fund / max(level(1000 * k), 1e-9))), 1)
        for k in (2, 3, 4)
    }
    # how far above the room
    for name in ("loud", "quiet"):
        a2, b2 = seg[name]
        e = mic[a2 + d : b2 + d]
        res[f"{name}_above_room_db"] = round(
            20 * float(np.log10(np.sqrt(np.mean(e**2)) / max(floor_rms, 1e-6))), 1
        )
    return res


def verdict(r: dict[str, Any]) -> list[str]:
    out = []
    c = r["coherence"]
    speech = np.mean([c["300-600"], c["600-1000"], c["1000-2000"]])
    out.append(
        f"A linear canceller can remove at best about "
        f"{-10 * np.log10(max(1 - speech, 1e-3)):.1f} dB (coherence {speech:.2f} over 300-2000 Hz)."
    )
    if r["delay_std_ms"] > 0.3:
        out.append(
            f"The delay is not steady: std {r['delay_std_ms']} ms, range {r['delay_range_ms']} ms."
        )
    else:
        out.append(f"The delay is steady: std {r['delay_std_ms']} ms.")
    if abs(r["gain_change_loud_minus_quiet_db"]) > 1.5:
        out.append(
            f"The gain changes with level by {r['gain_change_loud_minus_quiet_db']} dB: the "
            "speaker (or the microphone chain) compresses or clips."
        )
    h = r["tone_harmonics_db_below_fundamental"]
    if min(h.values()) < 30:
        out.append(f"The tone is distorted: harmonics only {h} dB below the fundamental.")
    return out


async def record(args: argparse.Namespace) -> np.ndarray:
    from doc_chat.playback import RemotePlayer, RemoteSpeaker
    from doc_chat.speech import MicHub

    hub = MicHub()
    hub.start()
    blocks: list[bytes] = []

    async def collect() -> None:
        while True:
            blocks.append(await hub.get())

    task = asyncio.create_task(collect())
    speaker = await asyncio.to_thread(RemoteSpeaker, args.url, os.environ.get("LAN_SPEECH_TOKEN"))
    await asyncio.sleep(FLOOR_S)
    pcm = (np.clip(played_stream(), -1, 1) * 32767).astype(np.int16).tobytes()
    player = RemotePlayer(speaker, RATE)
    player.push(pcm)
    await asyncio.to_thread(player.close)
    await asyncio.sleep(2.0)
    task.cancel()
    hub.stop()
    speaker.close()
    return np.frombuffer(b"".join(blocks), dtype=np.int16).astype(np.float64) / 32768.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", help="ws://<pi>:8780 (scripts/pi_speaker.py); to record")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--analyse", action="store_true", help="analyse OUT/mic.wav, do not record")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.analyse:
        with wave.open(str(args.out / "mic.wav"), "rb") as w:
            mic = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16) / 32768.0
    else:
        mic = asyncio.run(record(args))
        with wave.open(str(args.out / "mic.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.clip(mic, -1, 1) * 32767).astype(np.int16).tobytes())
    res = analyse(mic.astype(np.float64))
    (args.out / "result.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "delay_track_samples"}, indent=1))
    print("delay track (samples, 0.5 s blocks):", res["delay_track_samples"])
    for line in verdict(res):
        print("-", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
