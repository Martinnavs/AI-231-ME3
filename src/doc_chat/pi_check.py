"""Echo and interruption checks for the Raspberry Pi with its USB speakerphone.

No speech server and no language model: this plays known clips through a speaker, records the
microphone at the same time, and measures what a voice detector would do with the recording. The
question it answers on the real hardware is: *does the assistant's own voice trip the detector,
and how loud is it compared with the room?* (On the laptop, with software echo cancellation, the
answer was "no"; without it, "yes, in 91-92% of blocks".)

The CLI is scripts/pi_echo_check.py; the runbook is docs/PI-TEST-PLAN.md. Everything here takes an
injectable ``stream_factory`` so it can be tested without a sound card.
"""

from __future__ import annotations

import json
import platform
import subprocess
import threading
import time
import wave
from collections.abc import Callable
from math import gcd
from pathlib import Path
from typing import Any

import numpy as np

from doc_chat.bargein import OnsetDetector, SileroOnset

SR = 16000  # analysis rate: what Silero and the speech server use
BLOCK = SR // 10  # 100 ms
SILERO_DEFAULT = "assets/silero_vad.onnx"


# ----------------------------------------------------------------------------- audio helpers


def read_wav(path: Path | str, rate: int = SR) -> Any:
    """A WAV file as float32 mono at ``rate``."""
    with wave.open(str(path), "rb") as w:
        src, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path}: need 16-bit audio")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return resample(x, src, rate)


def write_wav(path: Path | str, x: Any, rate: int = SR) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def resample(x: Any, src: int, dst: int) -> Any:
    if src == dst or len(x) == 0:
        return x.astype(np.float32)
    from scipy.signal import resample_poly

    g = gcd(src, dst)
    return resample_poly(x, dst // g, src // g).astype(np.float32)


def rms(x: Any) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def db(x: float) -> float:
    return 20.0 * float(np.log10(max(x, 1e-9)))


def block_levels(x: Any, block: int = BLOCK) -> Any:
    n = len(x) // block
    return np.sqrt(np.mean(x[: n * block].reshape(n, block) ** 2, axis=1)) if n else np.zeros(0)


def beep(rate: int = SR, hz: float = 1000.0, seconds: float = 0.15, level: float = 0.3) -> Any:
    t = np.arange(int(rate * seconds)) / rate
    env = np.minimum(1.0, np.minimum(t, seconds - t) / 0.01)  # 10 ms ramps: no click
    return (level * env * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def chirp(rate: int = SR, seconds: float = 0.1, f0: float = 500.0, f1: float = 4000.0) -> Any:
    t = np.arange(int(rate * seconds)) / rate
    phase = 2 * np.pi * (f0 * t + (f1 - f0) * t**2 / (2 * seconds))
    return (0.5 * np.sin(phase) * np.hanning(len(t))).astype(np.float32)


def find_delay_ms(
    played: Any, recorded: Any, rate: int = SR, max_ms: float = 1000.0
) -> float | None:
    """How long after the speaker was fed the microphone heard it (device buffers + air).

    Cross-correlation peak; None if there is no clear peak (nothing came back)."""
    if len(played) == 0 or len(recorded) < len(played):
        return None
    n = len(recorded) + len(played)
    size = 1 << (n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(recorded, size) * np.conj(np.fft.rfft(played, size)), size)
    window = corr[: int(max_ms / 1000 * rate)]
    peak = int(np.argmax(np.abs(window)))
    if np.abs(window[peak]) < 8 * (np.median(np.abs(window)) + 1e-12):
        return None
    return peak / rate * 1000.0


# ----------------------------------------------------------------------------- detectors


def _edges(flags: list[bool], step_s: float) -> list[float]:
    """Times (s) at which a detector went from quiet to firing."""
    out, prev = [], False
    for i, on in enumerate(flags):
        if on and not prev:
            out.append(round(i * step_s, 3))
        prev = on
    return out


def run_detectors(mic: Any, floor_rms: float, silero_model: str | None) -> dict[str, Any]:
    """Replay a 16 kHz recording through the detectors the way the live loop feeds them.

    Returns, per detector, the times of its onsets; for Silero also the share of 32 ms windows it
    called speech and how long it took per window (the Pi's CPU cost)."""
    out: dict[str, Any] = {}
    energy = OnsetDetector(noise=max(floor_rms, 1e-4), ratio=4.0, min_s=0.3)
    flags = [energy.feed(float(lv), 0.1) for lv in block_levels(mic)]
    out["energy"] = {"onsets": _edges(flags, 0.1)}
    if silero_model is None or not Path(silero_model).exists():
        return out
    pcm = (np.clip(mic, -1, 1) * 32767).astype(np.int16)
    for name, min_s in (("silero_250ms", 0.25), ("silero_150ms", 0.15)):
        v = SileroOnset(silero_model, min_s=min_s, keep_history=True)
        fired: list[bool] = []
        t0 = time.perf_counter()
        for i in range(0, len(pcm) - BLOCK + 1, BLOCK):
            fired.append(v.feed(pcm[i : i + BLOCK].tobytes()))
        spent = time.perf_counter() - t0
        hist = np.array(v.history or [0.0])
        out[name] = {
            "onsets": _edges(fired, 0.1),
            "speech_windows": round(float(np.mean(hist >= v.confidence)), 4),
            "max_confidence": round(float(hist.max()), 3),
            "ms_per_window": round(spent / max(len(hist), 1) * 1000, 3),
        }
    return out


def analyze_playback(
    mic: Any, floor_rms: float, play_start_s: float, play_end_s: float, silero_model: str | None
) -> dict[str, Any]:
    """What the microphone heard while the speaker played, against the empty-room level."""
    lv = block_levels(mic)
    i0, i1 = int(play_start_s * 10), max(int(play_end_s * 10), int(play_start_s * 10) + 1)
    during = lv[i0:i1]
    first = lv[i0 : i0 + 10]
    over = np.array([db(float(v)) - db(floor_rms) for v in during]) if len(during) else np.zeros(1)
    first_over = (
        np.array([db(float(v)) - db(floor_rms) for v in first]) if len(first) else np.zeros(1)
    )
    det = run_detectors(mic, floor_rms, silero_model)
    rel = {
        k: [
            round(t - play_start_s, 2) for t in v["onsets"] if play_start_s <= t <= play_end_s + 1.0
        ]
        for k, v in det.items()
    }
    return {
        "play_s": round(play_end_s - play_start_s, 2),
        "mic_dbfs_median": round(db(float(np.median(during))) if len(during) else -120.0, 1),
        "above_floor_db_median": round(float(np.median(over)), 1),
        "above_floor_db_p95": round(float(np.percentile(over, 95)), 1),
        "above_floor_db_first_second": round(float(np.max(first_over)), 1),
        "onsets_after_playback_start_s": rel,
        "silero": {
            k: {kk: vv for kk, vv in v.items() if kk != "onsets"}
            for k, v in det.items()
            if k.startswith("silero")
        },
    }


# ----------------------------------------------------------------------------- the sound card


class Duplex:
    """Play a signal and record the microphone at the same time, on one stream.

    One stream, not two: with a speakerphone it keeps playback and capture on the same clock,
    which is what its echo canceller expects."""

    def __init__(
        self,
        rate: int,
        in_device: Any,
        out_device: Any,
        out_channels: int = 1,
        stream_factory: Callable[..., Any] | None = None,
    ) -> None:
        if stream_factory is None:
            import sounddevice as sd

            stream_factory = sd.Stream
        self.rate = rate
        self.in_device = in_device
        self.out_device = out_device
        self.out_channels = out_channels
        self._factory = stream_factory

    def play_and_record(self, signal16: Any, seconds: float) -> tuple[Any, int]:
        """Play a 16 kHz mono signal (zeros after it ends) for ``seconds``; return the recording
        at 16 kHz and the number of buffer underruns reported by the device."""
        play = resample(signal16.astype(np.float32), SR, self.rate)
        total = int(seconds * self.rate)
        out = np.zeros(total, dtype=np.float32)
        out[: min(len(play), total)] = play[:total]
        rec = np.zeros(total, dtype=np.float32)
        pos = [0]
        under = [0]
        done = threading.Event()

        def callback(indata: Any, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
            i = pos[0]
            n = min(frames, total - i)
            if n > 0:
                rec[i : i + n] = indata[:n, 0]
                outdata[:n, :] = out[i : i + n, None]
            if n < frames:
                outdata[max(n, 0) :, :] = 0
            if status and getattr(status, "output_underflow", False):
                under[0] += 1
            pos[0] = i + frames
            if pos[0] >= total:
                done.set()

        stream = self._factory(
            samplerate=self.rate,
            blocksize=self.rate // 50,  # 20 ms
            device=(self.in_device, self.out_device),
            channels=(1, self.out_channels),
            dtype="float32",
            callback=callback,
        )
        with stream:
            done.wait(timeout=seconds + 10)
        return resample(rec, self.rate, SR), under[0]


def hardware_info() -> dict[str, Any]:
    """What this machine is, for the record (never fails)."""
    info: dict[str, Any] = {"platform": platform.platform(), "python": platform.python_version()}

    def run(cmd: list[str]) -> str | None:
        try:
            return (
                subprocess.run(
                    cmd, capture_output=True, text=True, timeout=3, check=False
                ).stdout.strip()
                or None
            )
        except (OSError, subprocess.SubprocessError):
            return None

    try:
        info["model"] = Path("/proc/device-tree/model").read_bytes().rstrip(b"\x00").decode()
    except OSError:
        info["model"] = None
    info["temperature"] = run(["vcgencmd", "measure_temp"])
    info["throttled"] = run(["vcgencmd", "get_throttled"])
    try:
        import onnxruntime
        import sounddevice

        info["onnxruntime"] = onnxruntime.__version__
        info["sounddevice"] = sounddevice.__version__
    except Exception:
        pass
    return info


# ----------------------------------------------------------------------------- the checks


def self_trigger_check(
    clips: list[Path],
    duplex: Duplex,
    out_dir: Path,
    silero_model: str | None,
    floor_s: float = 4.0,
    lead_s: float = 1.0,
    tail_s: float = 1.5,
    save: bool = True,
) -> dict[str, Any]:
    """Nobody speaks. Measure the empty room, the round-trip delay, then play each clip and see
    what the microphone and the detectors make of the assistant's own voice."""
    out_dir.mkdir(parents=True, exist_ok=True)
    floor_rec, _ = duplex.play_and_record(np.zeros(1, dtype=np.float32), floor_s)
    floor = rms(floor_rec[SR // 2 :])  # skip the first half second (stream start-up)
    if save:
        write_wav(out_dir / "mic-floor.wav", floor_rec)

    probe = chirp()
    lat_rec, _ = duplex.play_and_record(np.concatenate([np.zeros(SR // 2, np.float32), probe]), 2.0)
    delay = find_delay_ms(np.concatenate([np.zeros(SR // 2, np.float32), probe]), lat_rec)

    results = []
    for k, path in enumerate(clips):
        clip = read_wav(path)
        sig = np.concatenate([np.zeros(int(lead_s * SR), np.float32), clip])
        total = len(sig) / SR + tail_s
        rec, under = duplex.play_and_record(sig, total)
        if save:
            write_wav(out_dir / f"mic-{path.stem}.wav", rec)
        res = analyze_playback(rec, floor, lead_s, lead_s + len(clip) / SR, silero_model)
        res.update(clip=path.name, underruns=under, first_clip=(k == 0))
        results.append(res)
        time.sleep(0.5)

    detectors = sorted({d for r in results for d in r["onsets_after_playback_start_s"]})
    summary: dict[str, Any] = {}
    for d in detectors:
        hit = [bool(r["onsets_after_playback_start_s"].get(d)) for r in results]
        summary[d] = {
            "clips_with_a_false_onset": f"{sum(hit)}/{len(hit)}",
            "first_clip_false_onset": hit[0] if hit else None,
            "false_onsets_total": sum(
                len(r["onsets_after_playback_start_s"].get(d, [])) for r in results
            ),
        }
    return {
        "floor_dbfs": round(db(floor), 1),
        "round_trip_delay_ms": None if delay is None else round(delay, 1),
        "echo_above_floor_db_median": round(
            float(np.median([r["above_floor_db_median"] for r in results])), 1
        ),
        "echo_above_floor_db_worst": round(max(r["above_floor_db_p95"] for r in results), 1),
        "underruns_total": sum(r["underruns"] for r in results),
        "summary": summary,
        "clips": results,
    }


def double_talk_trials(
    clips: list[Path],
    prompts: list[str],
    duplex: Duplex,
    out_dir: Path,
    silero_model: str | None,
    floor_rms: float,
    cue_s: float = 3.0,
    ask: Callable[[str], Any] = input,
    say: Callable[[str], None] = print,
) -> dict[str, Any]:
    """A person reads a prompt over the playing reply. The reply starts, a beep sounds ``cue_s``
    seconds in, and the person starts reading at the beep. Onsets before the beep are the
    assistant's own voice tripping the detector; onsets after it are the person's interruption."""
    out_dir.mkdir(parents=True, exist_ok=True)
    trials = []
    for k, prompt in enumerate(prompts):
        reply = np.concatenate(
            [read_wav(clips[(2 * k) % len(clips)]), read_wav(clips[(2 * k + 1) % len(clips)])]
        )
        lead = 1.0
        sig = np.concatenate([np.zeros(int(lead * SR), np.float32), reply])
        b = beep()
        at = int((lead + cue_s) * SR)
        sig[at : at + len(b)] += b
        say(f"\nTrial {k + 1}/{len(prompts)}. At the BEEP, read this aloud at normal volume:")
        say(f"    {prompt}\n")
        ask("Press Enter to start (the reply begins after a second)... ")
        rec, under = duplex.play_and_record(sig, len(sig) / SR + 1.0)
        write_wav(out_dir / f"trial-{k + 1:02d}.wav", rec)
        det = run_detectors(rec, floor_rms, silero_model)
        beep_t = lead + cue_s
        before: dict[str, list[float]] = {}
        after: dict[str, list[float]] = {}
        for d, v in det.items():
            before[d] = [t for t in v["onsets"] if t < beep_t]
            after[d] = [round(t - beep_t, 2) for t in v["onsets"] if t >= beep_t]
        trials.append(
            {
                "trial": k + 1,
                "prompt": prompt,
                "file": f"trial-{k + 1:02d}.wav",
                "beep_at_s": beep_t,
                "underruns": under,
                "before_beep": before,
                "after_beep": after,
            }
        )
        say("  its own voice tripped: " + ", ".join(f"{d} {len(v)}x" for d, v in before.items()))
        say(
            "  you, seconds after the beep: "
            + ", ".join(f"{d} {v[:1] or '-'}" for d, v in after.items())
        )
    (out_dir / "trials.json").write_text(json.dumps(trials, indent=1))
    return {"trials": trials}


# ----------------------------------------------------------------------------- no hardware


def simulated_stream_factory(
    echo_db: float = -200.0,
    delay_ms: float = 60.0,
    noise_rms: float = 0.003,
    talker: Callable[[float], Any] | None = None,
    seed: int = 0,
) -> Callable[..., Any]:
    """A fake sound card: the microphone hears the room noise plus the speaker's output, delayed
    and scaled by ``echo_db`` (-40 is a headset, -6 is loud open speakers), plus optionally a
    person (``talker(t)`` returns that person's samples for the next block starting at time t).
    It runs faster than real time. For tests and for trying the tool without hardware."""
    rng = np.random.default_rng(seed)
    gain = 10 ** (echo_db / 20)

    def factory(**kw: Any) -> Any:
        rate: int = kw["samplerate"]
        block: int = kw["blocksize"]
        callback = kw["callback"]
        out_ch = kw["channels"][1]
        delay = int(rate * delay_ms / 1000)

        class Stream:
            def __enter__(self) -> Stream:
                self.history = np.zeros(delay + block, dtype=np.float32)
                self.t = 0.0
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()
                return self

            def __exit__(self, *exc: object) -> None:
                self.stop = True
                self.thread.join(timeout=5)

            stop = False

            def run(self) -> None:
                while not self.stop:
                    indata = np.zeros((block, 1), dtype=np.float32)
                    outdata = np.zeros((block, out_ch), dtype=np.float32)
                    # the microphone block: noise + the speaker's earlier output + a person
                    mic = rng.normal(0, noise_rms, block).astype(np.float32)
                    mic += gain * self.history[:block]
                    if talker is not None:
                        voice = talker(self.t)
                        mic[: len(voice)] += voice[:block]
                    indata[:, 0] = mic
                    callback(indata, outdata, block, None, None)
                    self.history = np.concatenate([self.history[block:], outdata[:, 0]])
                    self.t += block / rate

        return Stream()

    return factory
