"""Phase 1 gate for ticket 21: laptop-CPU STT/TTS feasibility.

Run: uv run --group laptop python scripts/laptop_speech_bench.py
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import onnxruntime as ort
import sherpa_onnx
from fetch_laptop_models import KOKORO_DIR, SHERPA_DIR, verify_all
from kokoro_onnx import Kokoro

from audio_engine import AudioClip
from audio_engine.wav import read_wav, resample, write_wav

SR = 16000
CHUNK_S = 0.1
TAIL_PAD_S = 0.66
SHERPA_STEM = "epoch-99-avg-1-chunk-16-left-128"
RENDER_VOICES = ("af_heart", "am_michael", "bf_emma", "bm_george", "af_bella", "am_adam")

TTS_SHORT = (
    "Sure, the kitchen lights are now turned on.",
    "It is sunny and warm outside today.",
    "Your timer for ten minutes has started now.",
)
TTS_LONG = (
    "The capital of France is Paris, a city famous for its museums, its cafes, "
    "and the tall iron tower built for a world fair.",
    "A black hole is a region of space where gravity is so strong that nothing, "
    "not even light, can escape from inside it.",
    "Mount Everest is about eight thousand eight hundred meters tall, which makes it "
    "the highest mountain above sea level on Earth.",
)

GATE = {
    "stt_final_p50_ms": 300.0,
    "stt_wer": 0.15,
    "tts_first_p50_ms": 400.0,
    "tts_rtf": 0.6,
    "concurrent_ratio": 1.5,
}

Samples = npt.NDArray[np.float32]


@dataclass
class Utterance:
    id: str
    text: str
    samples: Samples


@dataclass
class SttResult:
    final_ms: list[float] = field(default_factory=list)
    compute_s: float = 0.0
    audio_s: float = 0.0
    edits: int = 0
    ref_words: int = 0
    hyps: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class TtsResult:
    first_ms: list[float] = field(default_factory=list)
    compute_s: float = 0.0
    audio_s: float = 0.0


def _normalize(text: str) -> list[str]:
    return re.sub(r"[^a-z' ]+", " ", text.lower()).split()


def word_edits(ref: Sequence[str], hyp: Sequence[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def pct(xs: Sequence[float], q: float) -> float:
    return float(np.percentile(np.asarray(xs, dtype=np.float64), q)) if xs else float("nan")


def make_recognizer(models: Path, threads: int, int8: bool) -> sherpa_onnx.OnlineRecognizer:
    d = models / SHERPA_DIR
    sfx = ".int8.onnx" if int8 else ".onnx"
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(d / "tokens.txt"),
        encoder=str(d / f"encoder-{SHERPA_STEM}{sfx}"),
        decoder=str(d / f"decoder-{SHERPA_STEM}{sfx}"),
        joiner=str(d / f"joiner-{SHERPA_STEM}{sfx}"),
        num_threads=threads,
        sample_rate=SR,
        feature_dim=80,
        decoding_method="greedy_search",
        provider="cpu",
    )


def make_kokoro(models: Path, threads: int, int8: bool) -> Kokoro:
    d = models / KOKORO_DIR
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    name = "kokoro-v1.0.int8.onnx" if int8 else "kokoro-v1.0.onnx"
    sess = ort.InferenceSession(str(d / name), opts, providers=["CPUExecutionProvider"])
    return Kokoro.from_session(sess, str(d / "voices-v1.0.bin"))


def split_clauses(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[,;:.!?])\s+", text)]
    return [p for p in parts if p]


def synth_stream(tts: Kokoro, text: str, voice: str) -> Iterator[Samples]:
    for clause in split_clauses(text):
        audio, _ = tts.create(clause, voice=voice, lang="en-us")
        yield audio.astype(np.float32)


def render_utterances(spec: Path, tts: Kokoro, cache: Path) -> list[Utterance]:
    turns = json.loads(spec.read_text(encoding="utf-8"))["turns"]
    texts = [t["text"] for t in turns if t["kind"] == "turn"]
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    for i, text in enumerate(texts):
        uid = f"turn{i:02d}"
        voice = RENDER_VOICES[i % len(RENDER_VOICES)]
        path = cache / f"{uid}-{voice}.wav"
        if path.exists():
            clip = read_wav(path)
        else:
            audio, sr = tts.create(text, voice=voice, lang="en-us")
            clip = resample(AudioClip(audio.astype(np.float32), sr), SR)
            write_wav(path, clip)
        out.append(Utterance(uid, text, clip.samples.astype(np.float32)))
    return out


def load_mic_utterances(mic_dir: Path) -> list[Utterance]:
    out = []
    for wav in sorted(mic_dir.glob("*.wav")):
        ref = wav.with_suffix(".txt")
        if not ref.exists():
            continue
        clip = read_wav(wav)
        if clip.sample_rate != SR:
            clip = resample(clip, SR)
        out.append(Utterance(f"mic:{wav.stem}", ref.read_text().strip(), clip.samples))
    return out


def run_stt(rec: sherpa_onnx.OnlineRecognizer, utts: Sequence[Utterance]) -> SttResult:
    res = SttResult()
    chunk = round(CHUNK_S * SR)
    tail = np.zeros(round(TAIL_PAD_S * SR), dtype=np.float32)
    for u in utts:
        stream = rec.create_stream()
        t0 = time.perf_counter()
        n_chunks = (len(u.samples) + chunk - 1) // chunk
        for k in range(n_chunks):
            due = t0 + (k + 1) * CHUNK_S
            delay = due - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            c0 = time.perf_counter()
            stream.accept_waveform(SR, u.samples[k * chunk : (k + 1) * chunk])
            while rec.is_ready(stream):
                rec.decode_stream(stream)
            res.compute_s += time.perf_counter() - c0
        t_last = time.perf_counter()
        stream.accept_waveform(SR, tail)
        stream.input_finished()
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        hyp = rec.get_result(stream)
        t_final = time.perf_counter()
        res.compute_s += t_final - t_last
        res.final_ms.append((t_final - t_last) * 1000)
        res.audio_s += len(u.samples) / SR
        ref_w, hyp_w = _normalize(u.text), _normalize(hyp)
        res.edits += word_edits(ref_w, hyp_w)
        res.ref_words += len(ref_w)
        res.hyps.append((u.id, u.text, hyp))
    return res


def run_tts(tts: Kokoro, texts: Sequence[str], voice: str, runs: int) -> TtsResult:
    res = TtsResult()
    for _ in range(runs):
        for text in texts:
            t0 = time.perf_counter()
            first = True
            for audio in synth_stream(tts, text, voice):
                if first:
                    res.first_ms.append((time.perf_counter() - t0) * 1000)
                    first = False
                res.audio_s += len(audio) / 24000
            res.compute_s += time.perf_counter() - t0
    return res


def stt_summary(r: SttResult) -> dict[str, float]:
    return {
        "final_p50_ms": pct(r.final_ms, 50),
        "final_p95_ms": pct(r.final_ms, 95),
        "rtf": r.compute_s / r.audio_s,
        "wer": r.edits / max(r.ref_words, 1),
        "n": float(len(r.final_ms)),
    }


def tts_summary(r: TtsResult) -> dict[str, float]:
    return {
        "first_p50_ms": pct(r.first_ms, 50),
        "first_p95_ms": pct(r.first_ms, 95),
        "rtf": r.compute_s / r.audio_s,
        "n": float(len(r.first_ms)),
    }


def hardware() -> dict[str, str]:
    def read(p: str) -> str:
        try:
            return Path(p).read_text().strip()
        except OSError:
            return "n/a"

    lscpu = subprocess.run(["lscpu"], capture_output=True, text=True, check=False).stdout
    info = dict(line.split(":", 1) for line in lscpu.splitlines() if ":" in line)
    ac = [read(str(p / "online")) for p in Path("/sys/class/power_supply").glob("AC*")]
    return {
        "machine": read("/sys/class/dmi/id/product_name"),
        "cpu": info.get("Model name", "?").strip(),
        "cores": info.get("Core(s) per socket", "?").strip(),
        "threads": info.get("CPU(s)", "?").strip(),
        "platform_profile": read("/sys/firmware/acpi/platform_profile"),
        "governor": read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "epp": read("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"),
        "ac_online": ",".join(ac) or "n/a",
        "os": platform.platform(),
        "sherpa_onnx": sherpa_onnx.__version__,
        "onnxruntime": ort.__version__,
    }


def gate(alone: dict[str, Any], both: dict[str, Any]) -> dict[str, bool]:
    s, t = alone["stt"], alone["tts"]
    bs, bt = both["stt"], both["tts"]
    ratio = GATE["concurrent_ratio"]
    return {
        "stt_final_p50": s["final_p50_ms"] <= GATE["stt_final_p50_ms"],
        "stt_wer": s["wer"] <= GATE["stt_wer"],
        "tts_first_p50": t["first_p50_ms"] <= GATE["tts_first_p50_ms"],
        "tts_rtf": t["rtf"] <= GATE["tts_rtf"],
        "both_stt_final_p50": bs["final_p50_ms"] <= ratio * s["final_p50_ms"],
        "both_stt_rtf": bs["rtf"] <= ratio * s["rtf"],
        "both_tts_first_p50": bt["first_p50_ms"] <= ratio * t["first_p50_ms"],
        "both_tts_rtf": bt["rtf"] <= ratio * t["rtf"],
    }


def run_concurrent(
    rec: sherpa_onnx.OnlineRecognizer,
    tts: Kokoro,
    utts: Sequence[Utterance],
    voice: str,
) -> tuple[SttResult, TtsResult]:
    tts_res = TtsResult()
    stop = threading.Event()

    def speaker() -> None:
        i = 0
        while not stop.is_set():
            part = run_tts(tts, [TTS_LONG[i % len(TTS_LONG)]], voice, 1)
            tts_res.first_ms += part.first_ms
            tts_res.compute_s += part.compute_s
            tts_res.audio_s += part.audio_s
            i += 1

    th = threading.Thread(target=speaker, daemon=True)
    th.start()
    stt_res = run_stt(rec, utts)
    stop.set()
    th.join()
    return stt_res, tts_res


def markdown(report: dict[str, Any]) -> str:
    a, b = report["alone"], report["both"]
    lines = [
        "| Metric | Alone | Both at once | Ratio | Threshold |",
        "|---|---|---|---|---|",
    ]

    def row(label: str, key: str, part: str, thr: str, fmt: str = "{:.0f}") -> None:
        x, y = a[part][key], b[part][key]
        lines.append(f"| {label} | {fmt.format(x)} | {fmt.format(y)} | {y / x:.2f}x | {thr} |")

    row("STT final-after-end p50 (ms)", "final_p50_ms", "stt", "≤ 300 alone, ≤ 1.5x")
    row("STT final-after-end p95 (ms)", "final_p95_ms", "stt", "—")
    row("STT real-time factor", "rtf", "stt", "≤ 1.5x", "{:.3f}")
    row("STT WER", "wer", "stt", "≤ 0.15 alone", "{:.3f}")
    row("TTS first-audio p50 (ms)", "first_p50_ms", "tts", "≤ 400 alone, ≤ 1.5x")
    row("TTS first-audio p95 (ms)", "first_p95_ms", "tts", "—")
    row("TTS real-time factor", "rtf", "tts", "≤ 0.6 alone, ≤ 1.5x", "{:.3f}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="laptop_speech_bench")
    ap.add_argument("--models", type=Path, default=Path("models"))
    ap.add_argument("--spec", type=Path, default=Path("bench/turns/v1.json"))
    ap.add_argument("--out", type=Path, default=Path("out/laptop_bench"))
    ap.add_argument("--mic-dir", type=Path, default=None)
    ap.add_argument("--stt-threads", type=int, default=2)
    ap.add_argument("--tts-threads", type=int, default=4)
    ap.add_argument("--stt-int8", action="store_true")
    ap.add_argument("--tts-int8", action="store_true")
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--tts-runs", type=int, default=5)
    ap.add_argument("--tag", default="default")
    args = ap.parse_args(argv)

    problems = verify_all(args.models)
    if problems:
        for p in problems:
            print(f"model check failed: {p}")
        return 2

    rec = make_recognizer(args.models, args.stt_threads, args.stt_int8)
    tts = make_kokoro(args.models, args.tts_threads, args.tts_int8)
    render_tts = make_kokoro(args.models, args.tts_threads, int8=False)
    utts = render_utterances(args.spec, render_tts, args.out / "renders")
    if args.mic_dir:
        utts += load_mic_utterances(args.mic_dir)

    run_tts(tts, TTS_SHORT[:1], args.voice, 1)
    run_stt(rec, utts[:1])

    stt_alone = run_stt(rec, utts)
    tts_alone = TtsResult()
    for texts in (TTS_SHORT, TTS_LONG):
        part = run_tts(tts, texts, args.voice, args.tts_runs)
        tts_alone.first_ms += part.first_ms
        tts_alone.compute_s += part.compute_s
        tts_alone.audio_s += part.audio_s
    tts_short = tts_summary(run_tts(tts, TTS_SHORT, args.voice, args.tts_runs))
    tts_long = tts_summary(run_tts(tts, TTS_LONG, args.voice, args.tts_runs))
    stt_both, tts_both = run_concurrent(rec, tts, utts, args.voice)

    report: dict[str, Any] = {
        "tag": args.tag,
        "config": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "hardware": hardware(),
        "alone": {"stt": stt_summary(stt_alone), "tts": tts_summary(tts_alone)},
        "tts_by_length": {"8_words": tts_short, "25_words": tts_long},
        "both": {"stt": stt_summary(stt_both), "tts": tts_summary(tts_both)},
        "stt_hyps": stt_alone.hyps,
        "stt_final_ms": stt_alone.final_ms,
        "gate_thresholds": GATE,
    }
    checks = gate(report["alone"], report["both"])
    report["gate"] = checks
    report["decision"] = "GO" if all(checks.values()) else "NO-GO"
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"results-{args.tag}.json"
    out.write_text(json.dumps(report, indent=1, default=asdict), encoding="utf-8")

    print(markdown(report))
    print(
        f"\n8-word TTS first-audio p50 {tts_short['first_p50_ms']:.0f} ms, "
        f"25-word {tts_long['first_p50_ms']:.0f} ms"
    )
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    print(f"decision: {report['decision']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
