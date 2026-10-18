"""Measure how much of the assistant's own voice the microphone hears, with and without AEC.

Plays a speech clip through the speakers (with paplay) while recording the mic, and reports how
often the VAD would have fired on it (a self-interruption). Stay quiet while it runs (about a
minute).

  uv run --group laptop python scripts/echo_check.py --clip some_speech.wav [--gain-db -24]

It runs scripts/aec.sh itself and always switches echo cancellation back off when done.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

import numpy as np

SR = 16000
BLOCK = 800  # 50 ms
AEC = Path(__file__).with_name("aec.sh")
VAD_MODEL = Path("models/silero-vad/silero_vad.onnx")


def make_vad(threshold: float = 0.6) -> object:
    import sherpa_onnx as so

    cfg = so.VadModelConfig()
    cfg.silero_vad.model = str(VAD_MODEL)
    cfg.silero_vad.threshold = threshold
    cfg.silero_vad.min_speech_duration = 0.05
    cfg.silero_vad.min_silence_duration = 0.25
    cfg.silero_vad.window_size = 512
    cfg.sample_rate = SR
    cfg.num_threads = 1
    return so.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)


def vad_flags(x: np.ndarray) -> np.ndarray:
    vad = make_vad()
    flags = []
    xf = x.astype(np.float32) / 32768.0
    for i in range(0, len(xf) - BLOCK + 1, BLOCK):
        vad.accept_waveform(xf[i : i + BLOCK])  # type: ignore[attr-defined]
        flags.append(vad.is_speech_detected())  # type: ignore[attr-defined]
        while not vad.empty():  # type: ignore[attr-defined]
            vad.pop()  # type: ignore[attr-defined]
    return np.array(flags)


def level_db(x: np.ndarray) -> float:
    from scipy.signal import butter, sosfilt

    y = sosfilt(butter(4, 100, "highpass", fs=SR, output="sos"), x.astype(np.float64))
    return float(20 * np.log10(max(np.sqrt(np.mean(y**2)) / 32768, 1e-6)))


def longest_run_ms(flags: np.ndarray) -> int:
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best * 50


def record(seconds: float, device: str) -> np.ndarray:
    import sounddevice as sd

    frames: list[bytes] = []
    with sd.RawInputStream(
        samplerate=SR,
        channels=1,
        dtype="int16",
        blocksize=BLOCK,
        device=device,
        callback=lambda indata, n, t, s: frames.append(bytes(indata)),
    ):
        sd.sleep(int(seconds * 1000))
    return np.frombuffer(b"".join(frames), dtype=np.int16)


def play_and_record(wav: Path, sink: str | None, device: str) -> np.ndarray:
    """Play through paplay (so PortAudio's per-app volume memory cannot hide the echo)."""
    import sounddevice as sd

    frames: list[bytes] = []
    with sd.RawInputStream(
        samplerate=SR,
        channels=1,
        dtype="int16",
        blocksize=BLOCK,
        device=device,
        callback=lambda indata, n, t, s: frames.append(bytes(indata)),
    ):
        sd.sleep(500)
        cmd = ["paplay", *(["--device", sink] if sink else []), str(wav)]
        subprocess.run(cmd, check=True)
        sd.sleep(1000)
    return np.frombuffer(b"".join(frames), dtype=np.int16)


def measure(
    label: str, wav: Path, clip_len_s: float, sink: str | None, device: str, runs: int
) -> list[list[str]]:
    floor = level_db(record(2.0, device)[SR // 2 :])
    rows = []
    for k in range(1, runs + 1):
        rec = play_and_record(wav, sink, device)
        play = rec[int(0.5 * SR) : int(0.5 * SR) + int(clip_len_s * SR)]
        flags = vad_flags(play)
        pct = 100 * float(flags.mean()) if flags.size else 0.0
        longest = longest_run_ms(flags)
        rows.append(
            [
                label,
                f"run {k}",
                f"{floor:.1f}",
                f"{level_db(play):.1f}",
                f"{level_db(play) - floor:+.1f}",
                f"{pct:.0f}%",
                f"{longest}",
                "YES" if longest >= 250 else "no",
                "YES" if longest >= 600 else "no",
            ]
        )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="echo_check")
    ap.add_argument("--clip", type=Path, required=True, help="speech WAV to play")
    ap.add_argument("--gain-db", type=float, default=-24.0, help="playback level (default -24)")
    ap.add_argument("--runs", type=int, default=2, help="plays per condition (default 2)")
    ap.add_argument("--seconds", type=float, default=8.0, help="use only the first N seconds")
    args = ap.parse_args(argv)

    import soundfile as sf

    data, clip_sr = sf.read(str(args.clip), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data[: int(args.seconds * clip_sr)] * (10 ** (args.gain_db / 20))
    wav = Path(tempfile.mkdtemp()) / "echo_check.wav"
    sf.write(str(wav), data, clip_sr)
    clip_len_s = len(data) / clip_sr

    header = [
        "condition",
        "play",
        "room dB",
        "mic during playback dB",
        "above room",
        "VAD speech",
        "longest run ms",
        "self-trigger 250ms",
        "self-trigger 600ms",
    ]
    rows: list[list[str]] = []
    try:
        subprocess.run([str(AEC), "off"], check=True, capture_output=True)
        rows += measure("AEC off", wav, clip_len_s, None, "pulse", args.runs)
        subprocess.run([str(AEC), "on"], check=True, capture_output=True)
        os.environ["PULSE_SOURCE"] = "va_aec_source"
        os.environ["PULSE_SINK"] = "va_aec_sink"
        rows += measure("AEC on", wav, clip_len_s, "va_aec_sink", "pulse", args.runs)
    finally:
        subprocess.run([str(AEC), "off"], capture_output=True)
        os.environ.pop("PULSE_SOURCE", None)
        os.environ.pop("PULSE_SINK", None)

    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for r in [header, *rows]:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
