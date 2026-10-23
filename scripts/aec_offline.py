"""Would echo cancellation using the audio we send to the Pi as its reference help? (offline)

    uv run --group laptop --with livekit python scripts/aec_offline.py out/remote_echo/fresh80 \
        --delays 0,300,500 [--stt]

Reads a `scripts/remote_echo_check.py` recording (mic.wav, result.json, trace.npz). The clips the
Pi played are the fixed files in tests/fixtures/pi_echo (with the beep mixed in for the talk-over
clips), so the reference signal is rebuilt from them and lined up with the microphone by
cross-correlation. The microphone then goes through WebRTC's echo canceller (LiveKit's
AudioProcessingModule) with that reference fed `--delays` ms *ahead* of the echo, as it would be
live (the laptop has the audio before the Pi plays it and the room returns it).

Per clip: how much quieter the microphone gets while the Pi speaks (dB), whether Silero at the
guard's settings (confidence 0.5, 7 of 10 windows) still fires on the echo, and, for talk-over
clips, how long after the beep it fires on you. With ``--stt`` the speech server transcribes the
echo-only clips and the talk-over clips (a few seconds after the beep), before and after, so you
can read what the assistant would have heard.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from remote_echo_check import with_beep  # noqa: E402

from doc_chat.bargein import SileroOnset  # noqa: E402

RATE = 16000
FRAME = 160  # 10 ms, what the canceller takes
CLIPS = HERE.parent / "tests" / "fixtures" / "pi_echo"


def read_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()


def write_pcm(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(x.astype(np.int16).tobytes())


def envelope(x: np.ndarray) -> np.ndarray:
    n = len(x) // FRAME
    return np.sqrt((x[: n * FRAME].astype(np.float32).reshape(n, FRAME) ** 2).mean(axis=1))


def best_lag(env_ref: np.ndarray, env_mic: np.ndarray, max_lag: int) -> int:
    """Frames by which the reference envelope must be delayed to match the microphone's."""
    best, best_lag_ = -1.0, 0
    for lag in range(max_lag):
        m = env_mic[lag : lag + len(env_ref)]
        r = env_ref[: len(m)]
        c = float(np.dot(m, r) / (np.linalg.norm(m) * np.linalg.norm(r) + 1e-9))
        if c > best:
            best, best_lag_ = c, lag
    return best_lag_


def refine(ref: np.ndarray, mic: np.ndarray, start: int, span: int) -> int:
    """Sample offset of `ref` in `mic` near `start` (+/- `span`): phase-transform correlation.

    The cross-spectrum is whitened (only its phase is kept), which gives one sharp peak even for
    speech through a reverberant room, where plain correlation gives a broad hump."""
    a = max(start - span, 0)
    seg = mic[a : start + span + len(ref)].astype(np.float32)
    r = ref.astype(np.float32)
    n = 1 << int(np.ceil(np.log2(len(seg) + len(r))))
    cross = np.fft.rfft(seg, n) * np.conj(np.fft.rfft(r, n))
    c = np.fft.irfft(cross / (np.abs(cross) + 1e-9), n)[: len(seg) - len(r) + 1]
    return a + int(np.argmax(c))


def stretch(x: np.ndarray, ppm: float) -> np.ndarray:
    """`x` as the microphone's clock sees it when the speaker's clock is `ppm` slower."""
    if ppm == 0:
        return x
    i = np.arange(len(x), dtype=np.float64)
    return np.interp(i / (1 + ppm * 1e-6), i, x.astype(np.float64)).astype(np.int16)


def build_reference(
    rec: Path, mic: np.ndarray, result: dict[str, Any], pushes: np.ndarray, ppm: float = 0.0
) -> tuple[np.ndarray, list[tuple[int, int]], list[float]]:
    """The reference lined up with the microphone: (signal, [(start, end) per clip], delays s)."""
    n = len(result["clips"])
    talk = sum(c["beep_at_s"] is not None for c in result["clips"])
    files = sorted(CLIPS.glob("reply-*.wav"))[:6]
    pcms = [read_pcm(files[k % len(files)]) for k in range(n)]
    pcms = [
        np.frombuffer(with_beep(p.tobytes()), dtype=np.int16) if k < talk else p
        for k, p in enumerate(pcms)
    ]
    env_mic = envelope(mic)
    approx = np.zeros(int(pushes[-1, 0] * RATE) + len(pcms[-1]) + RATE, dtype=np.int16)
    for (t, _), p in zip(pushes, pcms, strict=True):
        i = int(t * RATE)
        approx[i : i + len(p)] = p
    lag = best_lag(envelope(approx), env_mic, int(30 * 100)) * FRAME  # samples
    ref = np.zeros(len(mic), dtype=np.int16)
    spans, delays = [], []
    for (t, _), p in zip(pushes, pcms, strict=True):
        p = stretch(p, ppm)
        start = refine(p, mic, lag + int(t * RATE), int(0.6 * RATE))
        delays.append(start / RATE - (lag + int(t * RATE)) / RATE)
        end = min(start + len(p), len(ref))
        ref[start:end] = p[: end - start]
        spans.append((start, end))
    return ref, spans, delays


def cancel(mic: np.ndarray, ref: np.ndarray, ahead_ms: int, ns: bool, hpf: bool) -> np.ndarray:
    """Run the canceller; the render stream is fed `ahead_ms` ahead of the echo it belongs to."""
    from livekit import rtc

    apm = rtc.AudioProcessingModule(
        echo_cancellation=True, noise_suppression=ns, high_pass_filter=hpf
    )
    apm.set_stream_delay_ms(ahead_ms)
    ahead = ahead_ms * RATE // 1000
    padded = np.concatenate([ref, np.zeros(ahead + FRAME, dtype=np.int16)])
    out = np.zeros(len(mic) // FRAME * FRAME, dtype=np.int16)
    for i in range(len(out) // FRAME):
        a = i * FRAME
        render = rtc.AudioFrame(padded[a + ahead : a + ahead + FRAME].tobytes(), RATE, 1, FRAME)
        capture = rtc.AudioFrame(mic[a : a + FRAME].tobytes(), RATE, 1, FRAME)
        apm.process_reverse_stream(render)
        apm.process_stream(capture)
        out[a : a + FRAME] = np.frombuffer(capture.data, dtype=np.int16)
    return out


def db(x: np.ndarray) -> float:
    return 20 * float(np.log10(max(float(np.sqrt(np.mean((x / 32768.0) ** 2))), 1e-6)))


def silero_fire(model: str, x: np.ndarray, after_s: float = 0.0) -> float | None:
    """Seconds into `x` at which Silero (0.5, 7 of 10) first fires, at or after `after_s`."""
    v = SileroOnset(model, confidence=0.5, kofn=(7, 10))
    raw = x.astype(np.int16).tobytes()
    for i in range(0, len(raw) - 3199, 3200):
        if v.feed(raw[i : i + 3200]) and i / 2 / RATE >= after_s:
            return round(i / 2 / RATE + 0.1, 2)
    return None


async def transcribe(items: list[tuple[str, np.ndarray]], url: str) -> dict[str, str]:
    """Transcripts by name. A segment with no speech never gets a final transcript from the
    server, so each one has its own connection and a timeout, and a timeout reads as '(nothing)'."""
    from doc_chat.speech import DocChatSpeechClient

    out = {}
    with tempfile.TemporaryDirectory() as d:
        for name, x in items:
            p = Path(d) / "x.wav"
            write_pcm(p, x)
            client = DocChatSpeechClient(server_url=url, token=os.environ.get("LAN_SPEECH_TOKEN"))
            await client.connect()
            try:
                out[name] = (await asyncio.wait_for(client.hear_wav(p), len(x) / RATE + 6.0)).text
            except TimeoutError:
                out[name] = "(nothing)"
            finally:
                await asyncio.shield(client.close())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rec", type=Path, help="a scripts/remote_echo_check.py output folder")
    ap.add_argument("--delays", default="0,300,500", help="ms the reference is fed ahead")
    ap.add_argument("--vad-model", default="assets/silero_vad.onnx")
    ap.add_argument(
        "--ppm",
        type=float,
        default=0.0,
        help="speaker clock slower than the mic by this (probe: 137)",
    )
    ap.add_argument("--ns", action="store_true", help="also switch on noise suppression")
    ap.add_argument("--hpf", action="store_true", help="also switch on the high-pass filter")
    ap.add_argument("--stt", action="store_true", help="transcribe before and after")
    ap.add_argument("--server", default="ws://169.254.145.121:8770")
    ap.add_argument("--save", action="store_true", help="write the cancelled audio next to mic.wav")
    args = ap.parse_args(argv)

    mic = read_pcm(args.rec / "mic.wav")
    result = json.loads((args.rec / "result.json").read_text())
    pushes = np.load(args.rec / "trace.npz")["pushes"]
    ref, spans, delays = build_reference(args.rec, mic, result, pushes, args.ppm)
    print(
        f"{result['label']}: {len(spans)} clips; Pi speaker to microphone delay: median "
        f"{np.median(delays) * 1000 + 0:.0f} ms from the send time, spread "
        f"{(max(delays) - min(delays)) * 1000:.0f} ms over the recording"
    )
    outputs: dict[int, np.ndarray] = {}
    for d in [int(x) for x in args.delays.split(",")]:
        outputs[d] = cancel(mic, ref, d, args.ns, args.hpf)
        if args.save:
            write_pcm(args.rec / f"aec_ahead{d}.wav", outputs[d])
    stt_items: list[tuple[str, np.ndarray]] = []
    rows: list[dict[str, Any]] = []
    for k, ((a, b), clip) in enumerate(zip(spans, result["clips"], strict=True)):
        b = min(b + RATE, len(mic))
        beep = clip["beep_at_s"]
        row: dict[str, Any] = {"k": k + 1, "kind": "talk" if beep is not None else "echo"}
        seg = mic[a:b]
        row["mic_db"] = round(db(seg), 1)
        row["mic_silero"] = silero_fire(args.vad_model, seg, beep or 0.0)
        for d, y in outputs.items():
            s = y[a:b]
            row[f"cut_db@{d}"] = round(db(seg) - db(s), 1)
            row[f"silero@{d}"] = silero_fire(args.vad_model, s, beep or 0.0)
        rows.append(row)
        if args.stt:
            lo = a if beep is None else a + int((beep + 0.2) * RATE)
            hi = b if beep is None else min(lo + 4 * RATE, len(mic))
            stt_items.append((f"{k + 1} mic", mic[lo:hi]))
            for d, y in outputs.items():
                stt_items.append((f"{k + 1} @{d}", y[lo:hi]))
    keys = ["k", "kind", "mic_db", "mic_silero"] + [
        f"{p}@{d}" for d in outputs for p in ("cut_db", "silero")
    ]
    print(
        "silero = seconds into the clip (echo) or after the beep (talk) at which Silero 0.5, "
        "7-of-10 fires; - = never"
    )
    print(" | ".join(f"{k:>10}" for k in keys))
    for r in rows:
        print(" | ".join(f"{('-' if r.get(k) is None else r[k])!s:>10}" for k in keys))
    echo = [r for r in rows if r["kind"] == "echo"]
    talk = [r for r in rows if r["kind"] == "talk"]
    for d in outputs:
        cuts = [r[f"cut_db@{d}"] for r in rows]
        print(
            f"ahead {d:>3} ms: echo reduced by median {np.median(cuts):.1f} dB "
            f"(min {min(cuts):.1f});"
            f" echo-only clips where Silero still fires: "
            f"{sum(r[f'silero@{d}'] is not None for r in echo)}/{len(echo)}"
            f" (mic alone {sum(r['mic_silero'] is not None for r in echo)}/{len(echo)});"
            f" talk-over clips where it fires after the beep: "
            f"{sum(r[f'silero@{d}'] is not None for r in talk)}/{len(talk)}"
            f" (mic alone {sum(r['mic_silero'] is not None for r in talk)}/{len(talk)})"
        )
    if args.stt:
        text = asyncio.run(transcribe(stt_items, args.server))
        print("\ntranscripts (echo clips: whole clip; talk clips: 4 s after the beep):")
        for k, r in enumerate(rows, 1):
            print(f"clip {k} ({r['kind']})")
            for name in [f"{k} mic"] + [f"{k} @{d}" for d in outputs]:
                print(f"   {name.split(' ', 1)[1]:>6}: {text[name]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
