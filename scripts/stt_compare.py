"""Compare streaming STT models on your own microphone recordings.

Every clip is padded with 0.5 s of silence before and 1.5 s after, as in live use: the cache-based
streaming models drop words at the very start or end of a tightly trimmed clip.

uv run --group laptop python scripts/stt_compare.py record [--device N]
uv run --group laptop python scripts/stt_compare.py run [--only NAME ...] [--fast]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SR = 16000
CHUNK_S = 0.1
LEAD_S = 0.5
TAIL_S = 1.5
NOISE_CLIP_S = 4.0
AUDIO_DIR = Path("bench/mic_turns")
MODELS = Path("models")

DOMAIN = [
    ("Who is the headmaster of Hogwarts school?", ["headmaster", "hogwarts"]),
    ("Explain the rules of Quidditch and what the Golden Snitch does.", ["quidditch", "snitch"]),
    ("Where is the entrance to the Chamber of Secrets?", ["chamber", "secrets"]),
    (
        "What spell did Hermione Granger use on Neville Longbottom?",
        ["hermione", "granger", "neville", "longbottom"],
    ),
    (
        "How did Albus Dumbledore defeat Gellert Grindelwald?",
        ["albus", "dumbledore", "gellert", "grindelwald"],
    ),
    ("What are the four houses of Hogwarts besides Gryffindor?", ["hogwarts", "gryffindor"]),
    ("Who guarded the Sorcerer's Stone on the third floor?", ["sorcerers", "stone"]),
    ("What happens to a student who is sent to Azkaban prison?", ["azkaban"]),
    ("Did Severus Snape teach Defence Against the Dark Arts?", ["severus", "snape"]),
    ("Where can I buy a wand in Diagon Alley?", ["diagon", "alley"]),
]
CHAT = [
    "Hey, can you tell me what the weather is like today?",
    "I was wondering if you could explain how a black hole forms.",
    "What's the difference between a virus and a bacteria?",
    "Can you give me a quick tip for studying at night?",
    "How long does it take to boil an egg, roughly?",
    "Tell me something interesting about octopuses.",
    "Why is the sky blue during the day but red at sunset?",
    "What time zone is Tokyo in, and what time is it there now?",
    "Okay, thanks, that was really helpful.",
    "Could you say that again, a little more slowly?",
]
NOISE = [
    "Stay silent and just type on the keyboard",
    "Stay silent and breathe normally, shift in your chair",
    "Stay completely silent",
    "Stay silent and tap the desk a couple of times",
]


def norm_words(text: str) -> list[str]:
    text = text.lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", text).split()


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


def dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64))))) / 32768.0
    return 20 * np.log10(max(rms, 1e-6))


def write_wav(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.astype(np.int16).tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def record_until(stop: Callable[[], bool], device: int | None) -> np.ndarray:
    import sounddevice as sd

    frames: list[bytes] = []
    with sd.RawInputStream(
        samplerate=SR,
        channels=1,
        dtype="int16",
        blocksize=1600,
        device=device,
        callback=lambda indata, n, t, s: frames.append(bytes(indata)),
    ):
        stop()
    return np.frombuffer(b"".join(frames), dtype=np.int16)


def level_note(pcm: np.ndarray) -> str:
    peak = int(np.abs(pcm).max()) if pcm.size else 0
    notes = []
    if peak < 3000:
        notes.append("QUIET - move closer or raise input gain")
    if peak > 32000:
        notes.append("CLIPPING - lower input gain")
    return f"peak {peak}/32767, level {dbfs(pcm):.0f} dBFS" + (
        f"  <-- {'; '.join(notes)}" if notes else ""
    )


def cmd_record(args: argparse.Namespace) -> int:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    print("Room check: stay silent for 3 seconds after pressing Enter.")
    input("  Enter to start > ")
    quiet = record_until(lambda: time.sleep(3.0), args.device)
    print(f"  noise floor {dbfs(quiet):.0f} dBFS (below -55 is quiet, above -40 is noisy)\n")

    items: list[dict[str, Any]] = []
    for text, ents in DOMAIN:
        items.append({"kind": "domain", "text": text, "entities": ents})
    for text in CHAT:
        items.append({"kind": "chat", "text": text, "entities": []})
    for text in NOISE:
        items.append({"kind": "noise", "text": "", "instruction": text, "entities": []})

    print(f"{len(items)} clips. Speak naturally at your normal distance from the laptop.")
    print("For sentences: press Enter, read the line, press Enter again to stop.\n")
    for i, item in enumerate(items, 1):
        item["id"] = f"{i:02d}"
        path = AUDIO_DIR / f"{item['id']}.wav"
        if item["kind"] == "noise":
            print(f"[{i}/{len(items)}] {item['instruction']} ({NOISE_CLIP_S:.0f} s)")
            input("  Enter to start > ")
            pcm = record_until(lambda: time.sleep(NOISE_CLIP_S), args.device)
        else:
            print(f'[{i}/{len(items)}] Read aloud:  "{item["text"]}"')
            input("  Enter to start > ")
            pcm = record_until(lambda: input("  recording... Enter to stop > "), args.device)
        write_wav(path, pcm)
        print(f"  saved {path.name}: {len(pcm) / SR:.1f} s, {level_note(pcm)}\n")
    (AUDIO_DIR / "refs.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    print("Done. Next: uv run --group laptop python scripts/stt_compare.py run")
    return 0


def transducer(folder: str, enc: str, dec: str, joi: str, threads: int) -> Any:
    import sherpa_onnx

    d = MODELS / folder
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(d / "tokens.txt"),
        encoder=str(d / enc),
        decoder=str(d / dec),
        joiner=str(d / joi),
        num_threads=threads,
        sample_rate=SR,
        feature_dim=80,
        decoding_method="greedy_search",
        provider="cpu",
    )


def candidates(threads: int) -> dict[str, Callable[[], Any]]:
    z = "sherpa-zipformer-en-2023-06-26"
    st = "epoch-99-avg-1-chunk-16-left-128.int8.onnx"
    return {
        "baseline": lambda: transducer(
            z, f"encoder-{st}", f"decoder-{st}", f"joiner-{st}", threads
        ),
        "kroko": lambda: transducer(
            "stt-candidates/kroko-en", "encoder.onnx", "decoder.onnx", "joiner.onnx", threads
        ),
        "nemo-fc-480ms": lambda: transducer(
            "stt-candidates/nemo-fc-480ms",
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            threads,
        ),
        "nemo-fc-80ms": lambda: transducer(
            "stt-candidates/nemo-fc-80ms",
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            threads,
        ),
        "nemotron-160ms": lambda: transducer(
            "stt-candidates/nemotron-160ms",
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            threads,
        ),
    }


@dataclass
class Result:
    hyps: dict[str, str] = field(default_factory=dict)
    final_ms: list[float] = field(default_factory=list)
    compute_s: float = 0.0
    audio_s: float = 0.0


def transcribe(rec: Any, pcm: np.ndarray, paced: bool) -> tuple[str, float, float]:
    x = np.concatenate(
        [np.zeros(round(LEAD_S * SR), dtype=np.float32), pcm.astype(np.float32) / 32768.0]
    )
    chunk = round(CHUNK_S * SR)
    stream = rec.create_stream()
    t0 = time.perf_counter()
    compute = 0.0
    n = (len(x) + chunk - 1) // chunk
    for k in range(n):
        if paced:
            delay = t0 + (k + 1) * CHUNK_S - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        c0 = time.perf_counter()
        stream.accept_waveform(SR, x[k * chunk : (k + 1) * chunk])
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        compute += time.perf_counter() - c0
    t_last = time.perf_counter()
    stream.accept_waveform(SR, np.zeros(round(TAIL_S * SR), dtype=np.float32))
    stream.input_finished()
    while rec.is_ready(stream):
        rec.decode_stream(stream)
    result = rec.get_result(stream)
    t_end = time.perf_counter()
    text = result if isinstance(result, str) else getattr(result, "text", str(result))
    return text.strip(), (t_end - t_last) * 1000.0, compute + (t_end - t_last)


def evaluate(
    name: str, build: Callable[[], Any], items: list[dict[str, Any]], paced: bool
) -> dict[str, Any]:
    try:
        rec = build()
    except Exception as exc:
        first_line = str(exc).strip().splitlines()[0][:160] if str(exc).strip() else ""
        return {"name": name, "error": f"{type(exc).__name__}: {first_line}"}
    transcribe(rec, read_wav(AUDIO_DIR / f"{items[0]['id']}.wav")[: SR * 2], paced=False)

    edits = ref_words = ent_hit = ent_total = 0
    noise_words = noise_clips = 0
    lat: list[float] = []
    compute = audio = 0.0
    rows = []
    for item in items:
        pcm = read_wav(AUDIO_DIR / f"{item['id']}.wav")
        text, final_ms, comp = transcribe(rec, pcm, paced)
        compute += comp
        audio += len(pcm) / SR
        hyp = norm_words(text)
        if item["kind"] == "noise":
            noise_words += len(hyp)
            noise_clips += 1 if hyp else 0
            rows.append((item["id"], "noise", "", text))
            continue
        lat.append(final_ms)
        ref = norm_words(item["text"])
        edits += word_edits(ref, hyp)
        ref_words += len(ref)
        for ent in item["entities"]:
            ent_total += 1
            ent_hit += 1 if ent in hyp else 0
        rows.append((item["id"], item["kind"], item["text"], text))
    return {
        "name": name,
        "wer": edits / max(ref_words, 1),
        "entity_recall": ent_hit / max(ent_total, 1),
        "noise_words": noise_words,
        "noise_clips_with_output": noise_clips,
        "final_p50_ms": pct(lat, 50),
        "final_p95_ms": pct(lat, 95),
        "rtf": compute / max(audio, 1e-9),
        "rows": rows,
    }


def cmd_run(args: argparse.Namespace) -> int:
    refs_path = AUDIO_DIR / "refs.json"
    if not refs_path.exists():
        print("no recordings: run `record` first", file=sys.stderr)
        return 2
    items = json.loads(refs_path.read_text(encoding="utf-8"))
    speech = sum(1 for i in items if i["kind"] != "noise")
    print(f"{speech} speech clips + {len(items) - speech} noise clips; threads={args.threads}\n")
    registry = candidates(args.threads)
    names = args.only or list(registry)
    results = []
    for name in names:
        print(f"== {name} ...", flush=True)
        res = evaluate(name, registry[name], items, paced=not args.fast)
        results.append(res)
        if "error" in res:
            print(f"   could not run: {res['error']}")
        else:
            print(
                f"   WER {res['wer']:.3f}  entities {res['entity_recall']:.0%}  "
                f"noise words {res['noise_words']} ({res['noise_clips_with_output']} clips)  "
                f"final p50/p95 {res['final_p50_ms']:.0f}/{res['final_p95_ms']:.0f} ms  "
                f"RTF {res['rtf']:.2f}"
            )

    ok = [r for r in results if "error" not in r]
    print("\n| model | WER | entities | noise words | final p50 ms | final p95 ms | RTF |")
    print("|---|---|---|---|---|---|---|")
    for r in ok:
        print(
            f"| {r['name']} | {r['wer']:.3f} | {r['entity_recall']:.0%} | {r['noise_words']} | "
            f"{r['final_p50_ms']:.0f} | {r['final_p95_ms']:.0f} | {r['rtf']:.2f} |"
        )
    for r in results:
        if "error" in r:
            print(f"| {r['name']} | could not run: {r['error']} |")

    print("\nWhat each model heard (domain + chat):")
    by_id: dict[str, dict[str, str]] = {}
    for r in ok:
        for cid, kind, ref, hyp in r["rows"]:
            by_id.setdefault(cid, {"ref": ref, "kind": kind})[r["name"]] = hyp
    for cid, d in by_id.items():
        if d["kind"] == "noise":
            if any(d.get(r["name"]) for r in ok):
                print(
                    f"\n[{cid}] (silence/noise clip) "
                    + " | ".join(f"{r['name']}: '{d.get(r['name'], '')}'" for r in ok)
                )
            continue
        print(f"\n[{cid}] SAID:  {d['ref']}")
        for r in ok:
            print(f"      {r['name']:<15} {d.get(r['name'], '')}")
    out = Path("out/stt_compare")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"results-{args.tag}.json").write_text(
        json.dumps(results, indent=1, default=str), encoding="utf-8"
    )
    print(f"\nsaved out/stt_compare/results-{args.tag}.json")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stt_compare")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--device", type=int, default=None, help="sounddevice input device index")
    rn = sub.add_parser("run")
    rn.add_argument("--only", nargs="+", help="candidate names")
    rn.add_argument("--threads", type=int, default=2)
    rn.add_argument("--fast", action="store_true", help="do not pace audio at real time")
    rn.add_argument("--tag", default="mic")
    rn.add_argument("--audio-dir", type=Path, default=AUDIO_DIR)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        globals()["AUDIO_DIR"] = args.audio_dir
    return cmd_record(args) if args.cmd == "record" else cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
