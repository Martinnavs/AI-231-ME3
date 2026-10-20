# ruff: noqa: E501  (wide table output lines)
"""Parakeet decode-time experiments on synthetic clips (speed only; never use for accuracy).

Experiments (each prints a table):
  threads    p50/p95 decode time by thread count, greedy vs hotwords
  duration   decode time by clip length
  padding    effect of lead/tail padding on decode time and on the text it produces
  early      would decoding at the first VAD pause already give the final text?
  load       decode time (and TTS throughput) when Supertonic runs on the same CPU

    uv run --group laptop python scripts/make_synth_clips.py
    uv run --group laptop python scripts/stt_latency_bench.py [--only threads load ...]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))  # run without PYTHONPATH
SR = 16000


from lan_speech.hotwords import write_hotwords, write_vocab  # noqa: E402


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def environment() -> dict[str, str]:
    def read(p: str) -> str:
        try:
            return Path(p).read_text().strip()
        except OSError:
            return "n/a"

    ac = read("/sys/class/power_supply/AC/online")
    return {
        "cpu": next(
            (
                line.split(":", 1)[1].strip()
                for line in read("/proc/cpuinfo").splitlines()
                if line.startswith("model name")
            ),
            platform.processor(),
        ),
        "cores": str(__import__("os").cpu_count()),
        "on_ac": "yes" if ac == "1" else ("no" if ac == "0" else "n/a"),
        "governor": read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "epp": read("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"),
    }


class Bench:
    def __init__(self, clips_dir: Path, names: list[str]) -> None:
        self.ev = _load("stt_hotwords_eval")
        self.sc = self.ev.load_compare()
        manifest = json.loads((clips_dir / "manifest.json").read_text())
        self.clips = []
        for m in manifest:
            pcm = self.sc.read_wav(clips_dir / m["file"]).astype(np.float32) / 32768
            self.clips.append((m["text"], pcm))
        self.tmp = Path(tempfile.mkdtemp(prefix="latbench_"))
        write_vocab(self.ev.MODEL / "tokens.txt", self.tmp / "pk.vocab")
        write_hotwords(names, 1.0, self.tmp / "hot.txt")

    def recognizer(self, threads: int, hotwords: bool) -> Any:
        if not hotwords:
            return self.ev.recognizer(self.ev.MODEL, threads, decoding_method="greedy_search")
        return self.ev.recognizer(
            self.ev.MODEL,
            threads,
            decoding_method="modified_beam_search",
            max_active_paths=4,
            hotwords_file=str(self.tmp / "hot.txt"),
            hotwords_score=1.0,
            modeling_unit="bpe",
            bpe_vocab=str(self.tmp / "pk.vocab"),
        )

    @staticmethod
    def pad(pcm: np.ndarray, lead: float, tail: float) -> np.ndarray:
        return np.concatenate(
            [np.zeros(int(lead * SR), np.float32), pcm, np.zeros(int(tail * SR), np.float32)]
        )

    @staticmethod
    def decode(rec: Any, x: np.ndarray) -> tuple[str, float]:
        t0 = time.perf_counter()
        st = rec.create_stream()
        st.accept_waveform(SR, x)
        rec.decode_stream(st)
        return st.result.text, (time.perf_counter() - t0) * 1000

    def run_all(
        self, rec: Any, lead: float = 0.5, tail: float = 1.5, limit: int | None = None
    ) -> list[tuple[str, float, float]]:
        self.decode(rec, self.pad(self.clips[0][1], lead, tail))  # warm-up
        out = []
        for _, pcm in self.clips[:limit]:
            text, ms = self.decode(rec, self.pad(pcm, lead, tail))
            out.append((text, ms, len(pcm) / SR))
        return out


def p(values: Sequence[float], q: float) -> float:
    return float(np.percentile(values, q))


def exp_threads(b: Bench) -> None:
    print("\n## Decode time by thread count (padding 0.5 s lead + 1.5 s tail)")
    print("| threads | greedy p50 / p95 ms | beam+hotwords p50 / p95 ms |")
    print("|---|---|---|")
    for th in (1, 2, 4, 8):
        cells = []
        for hot in (False, True):
            ms = [r[1] for r in b.run_all(b.recognizer(th, hot))]
            cells.append(f"{p(ms, 50):.0f} / {p(ms, 95):.0f}")
        print(f"| {th} | {cells[0]} | {cells[1]} |", flush=True)


def exp_duration(b: Bench) -> None:
    print("\n## Decode time by clip length (4 threads, hotwords, padding 0.5 + 1.5 s)")
    rows = b.run_all(b.recognizer(4, True))
    print("| speech length | clips | p50 ms | p95 ms | ms per second of speech |")
    print("|---|---|---|---|---|")
    for lo, hi in ((0, 2), (2, 3), (3, 4), (4, 99)):
        sel = [r for r in rows if lo <= r[2] < hi]
        if sel:
            ms = [r[1] for r in sel]
            dur = np.mean([r[2] for r in sel])
            print(
                f"| {lo}-{hi if hi < 99 else '+'} s | {len(sel)} | {p(ms, 50):.0f} | {p(ms, 95):.0f} | {np.mean(ms) / dur:.0f} |"
            )
    x = np.array([r[2] for r in rows])
    y = np.array([r[1] for r in rows])
    slope, icpt = np.polyfit(x, y, 1)
    print(
        f"\nlinear fit: decode ms = {icpt:.0f} + {slope:.0f} x speech seconds (with 2 s of padding included)"
    )


def exp_padding(b: Bench) -> None:
    print("\n## Padding: decode time and whether the text changes (4 threads, hotwords)")
    rec = b.recognizer(4, True)
    ref = b.run_all(rec, 0.5, 1.5)
    ref_text = [r[0] for r in ref]
    print(
        "| lead / tail s | p50 ms | clips whose text differs from 0.5/1.5 | WER vs script (synthetic: optimistic) |"
    )
    print("|---|---|---|---|")
    for lead, tail in ((0.5, 1.5), (0.5, 0.5), (0.2, 0.5), (0.0, 0.3), (0.0, 0.0)):
        rows = b.run_all(rec, lead, tail)
        diff = sum(a[0].strip() != r.strip() for a, r in zip(rows, ref_text, strict=True))
        edits = n = 0
        for (script, _), row in zip(b.clips, rows, strict=True):
            r_w = b.sc.norm_words(script)
            edits += b.sc.word_edits(r_w, b.sc.norm_words(row[0]))
            n += len(r_w)
        print(
            f"| {lead} / {tail} | {p([r[1] for r in rows], 50):.0f} | {diff} of {len(rows)} | {edits / n:.1%} |",
            flush=True,
        )


def exp_early(b: Bench) -> None:
    print(
        "\n## Early decode: text after a short pause vs after a long one (4 threads, hotwords, lead 0.2 s)"
    )
    rec = b.recognizer(4, True)
    final = b.run_all(rec, 0.2, 0.5)
    print("| tail already heard | p50 ms to decode | same words as the 0.5 s-tail decode |")
    print("|---|---|---|")
    for tail in (0.1, 0.2, 0.3, 0.5):
        rows = b.run_all(rec, 0.2, tail)
        words = sum(
            b.sc.norm_words(a[0]) == b.sc.norm_words(f[0]) for a, f in zip(rows, final, strict=True)
        )
        exact = sum(a[0].strip() == f[0].strip() for a, f in zip(rows, final, strict=True))
        print(
            f"| {tail} s | {p([r[1] for r in rows], 50):.0f} | {words} of {len(rows)} "
            f"(exact text incl. punctuation: {exact}) |",
            flush=True,
        )


def exp_load(b: Bench, models: Path) -> None:
    print("\n## Decode time while Supertonic TTS runs on the same CPU (hotwords)")
    from lan_speech.engines import SupertonicTTSEngine

    tts = SupertonicTTSEngine(models / "supertonic-3", total_steps=4, intra_op_threads=2)
    reply = "Sure, I found three results near you and the closest one is about ten minutes away from here."
    tts.synthesize_chunk(reply)
    stop = threading.Event()
    counts = {"n": 0, "secs": 0.0}

    def worker() -> None:
        while not stop.is_set():
            t0 = time.perf_counter()
            pcm = tts.synthesize_chunk(reply)
            counts["n"] += 1
            counts["secs"] += time.perf_counter() - t0 - 0.0 * len(pcm)

    # TTS alone
    t0 = time.perf_counter()
    for _ in range(6):
        tts.synthesize_chunk(reply)
    alone = (time.perf_counter() - t0) / 6 * 1000
    print(
        "| STT threads | STT alone p50 / p95 ms | STT with TTS running p50 / p95 ms | slowdown p50 | TTS call alone -> with STT ms |"
    )
    print("|---|---|---|---|---|")
    for th in (2, 4):
        rec = b.recognizer(th, True)
        solo = [r[1] for r in b.run_all(rec)]
        counts.update(n=0, secs=0.0)
        stop.clear()
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.5)
        under = [r[1] for r in b.run_all(rec)]
        stop.set()
        t.join()
        busy = counts["secs"] / max(counts["n"], 1) * 1000
        print(
            f"| {th} | {p(solo, 50):.0f} / {p(solo, 95):.0f} | {p(under, 50):.0f} / {p(under, 95):.0f} "
            f"| {p(under, 50) / p(solo, 50):.2f}x | {alone:.0f} -> {busy:.0f} |",
            flush=True,
        )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", type=Path, default=Path("out/synth"))
    ap.add_argument("--names", type=Path, default=Path("bench/hotwords_v1.txt"))
    ap.add_argument("--models", type=Path, default=Path("models"))
    ap.add_argument(
        "--only", nargs="+", choices=["threads", "duration", "padding", "early", "load"]
    )
    args = ap.parse_args(argv)
    names = [n.strip() for n in args.names.read_text().splitlines() if n.strip()]
    b = Bench(args.clips, names)
    env = environment()
    print("environment:", json.dumps(env))
    print(
        f"{len(b.clips)} synthetic clips, mean {np.mean([len(c) / SR for _, c in b.clips]):.2f} s"
    )
    todo = args.only or ["threads", "duration", "padding", "early", "load"]
    if "threads" in todo:
        exp_threads(b)
    if "duration" in todo:
        exp_duration(b)
    if "padding" in todo:
        exp_padding(b)
    if "early" in todo:
        exp_early(b)
    if "load" in todo:
        exp_load(b, args.models)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
