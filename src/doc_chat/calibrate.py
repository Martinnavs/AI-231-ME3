"""Measure the room once: noise floor, the user's speech level, optionally the music's leak.

Run on the laptop with the client's microphone settings:
    python -m doc_chat.calibrate [--music]
Writes out/calibration.json (keys `when`, `noise_db`, `user_db`, optional `music_db`; dBFS).
`load_calibration()` returns it, or None when the file is missing or unreadable, and every
consumer then behaves exactly as without calibration.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

PATH = Path("out/calibration.json")
RATE = 16000
BLOCK_S = 0.1
PROMPTS = (
    "What is the weather like in Davao today?",
    "Set a timer for ten minutes, please.",
    "Can you play something relaxing?",
)
# --baseline: the phrases every microphone comparison uses (docs/HEADSET-MIC.md), recorded to a WAV
BASELINE_PROMPTS = (
    "What's the weather tomorrow?",
    "Dad, can you pass the salt?",
    "Play some jazz.",
    "Hey Jamie, dinner's ready.",
    "Set a timer for ten minutes.",
    "I forgot to water the herbs.",
)
BASELINE_WAV = Path("out/calibration-baseline.wav")


def dbfs(level: float) -> float:
    """A linear RMS level (1.0 = full scale) in dBFS."""
    return 20.0 * math.log10(max(level, 1e-6))


def percentile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def block_levels(pcm: bytes, rate: int = RATE, block_s: float = BLOCK_S) -> list[float]:
    """RMS of each block of 16-bit mono PCM, as a fraction of full scale."""
    import numpy as np

    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float64) / 32768.0
    n = int(rate * block_s)
    return [float(np.sqrt(np.mean(x[i : i + n] ** 2))) for i in range(0, len(x) - n + 1, n)]


def noise_db(levels: list[float]) -> float:
    """The 10th percentile of the block levels, as the pause detector computes the room."""
    return dbfs(percentile(levels, 0.10))


def speech_db(levels: list[float], noise: float) -> float:
    """RMS level of the blocks at least 10 dB above the noise floor (the speech), in dBFS."""
    floor = 10 ** ((noise + 10.0) / 20.0)
    active = [lv for lv in levels if lv >= floor]
    if not active:
        raise ValueError("no speech above the noise floor")
    return dbfs(math.sqrt(sum(lv * lv for lv in active) / len(active)))


def music_db(levels: list[float]) -> float:
    """The music's level at the mic: the median block level."""
    return dbfs(percentile(levels, 0.5))


def load_calibration(path: Path = PATH) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
        out: dict[str, Any] = {"when": str(data["when"])}
        for key in ("noise_db", "user_db", "music_db"):
            if key in data:
                out[key] = float(data[key])
        if "noise_db" not in out or "user_db" not in out:
            return None
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return None


def describe(cal: dict[str, Any] | None) -> str:
    if cal is None:
        return "no room calibration (out/calibration.json): defaults"
    music = f", music {cal['music_db']:.0f}" if "music_db" in cal else ""
    return (
        f"room calibration from {cal['when']}: noise {cal['noise_db']:.0f}, "
        f"speech {cal['user_db']:.0f}{music} dBFS"
    )


def music_gate_db(cal: dict[str, Any] | None) -> float | None:
    """Speech must be louder than this to pause the music: halfway between the user's level and
    the music's leak. None without a calibration that has both."""
    if cal is None or "music_db" not in cal:
        return None
    return float((cal["user_db"] + cal["music_db"]) / 2.0)


def _record(seconds: float, device: int | str | None = None) -> bytes:
    import sounddevice as sd

    pcm = sd.rec(
        int(seconds * RATE),
        samplerate=RATE,
        channels=1,
        dtype="int16",
        blocking=True,
        device=device,
    )
    return bytes(pcm.tobytes())


def _input_name(device: int | str | None) -> str:
    import sounddevice as sd

    return str(sd.query_devices(device, kind="input")["name"])


def _save_wav(path: Path, pcm: bytes) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def main(argv: list[str] | None = None) -> int:
    from doc_chat.speech import mic_device

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--music", action="store_true", help="also measure the music leak (8 s)")
    ap.add_argument("--out", default=str(PATH))
    ap.add_argument(
        "--mic-device",
        default=None,
        help="input device, a sounddevice index or part of its name (default: the system default)",
    )
    ap.add_argument(
        "--baseline",
        action="store_true",
        help="say the six baseline phrases instead of the three prompts and keep the audio "
        f"({BASELINE_WAV})",
    )
    ap.add_argument("--baseline-wav", default=str(BASELINE_WAV))
    args = ap.parse_args(argv)
    device = mic_device(args.mic_device)
    print(f"Microphone: {_input_name(device)}", flush=True)
    print("Quiet, please: measuring the room for 3 s...", flush=True)
    quiet = _record(3.0, device)
    noise = noise_db(block_levels(quiet))
    print(f"  noise floor {noise:.1f} dBFS")
    speech: list[float] = []
    kept = bytearray(quiet)
    for line in BASELINE_PROMPTS if args.baseline else PROMPTS:
        input(f'Press Enter, then say: "{line}" ')
        pcm = _record(3.5, device)
        kept += pcm
        speech += block_levels(pcm)
    user = speech_db(speech, noise)
    print(f"  your speech {user:.1f} dBFS")
    result: dict[str, Any] = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "noise_db": round(noise, 1),
        "user_db": round(user, 1),
    }
    if args.music:
        input("Start a song at your usual volume, stay quiet, press Enter... ")
        result["music_db"] = round(music_db(block_levels(_record(8.0, device))), 1)
        print(f"  music leak {result['music_db']:.1f} dBFS")
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {path}")
    if args.baseline:
        _save_wav(Path(args.baseline_wav), bytes(kept))
        print(f"wrote {args.baseline_wav} (3 s quiet, then the six phrases at 3.5 s each)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
