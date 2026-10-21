# ruff: noqa: E501  (wide report lines)
"""Measure interruptions with no language model and no human: how fast, how reliable, how fooled.

What is said, on both sides, is random but seeded text (doc_chat.phrases), so the words the
recogniser heard can be compared with what was said. The reply is a run of random sentences read by
the real speech synthesiser. Each trial runs the real doc_chat loop (--llm echo) against a running Parakeet speech server, with
a scripted voice on the microphone and a speaker that plays at real speed, makes no sound, and
leaks what it "plays" back into the microphone at a chosen level (that is the echo).

  barge    the user talks over the reply, `offset` s after it started       -> time to go quiet,
                                                                              and was it heard right
  cutoff   the user pauses `pause` s mid-sentence, then goes on             -> are the parts joined
  echo     nobody speaks; the speaker leaks into the mic at `echo_db` dB    -> does it stop itself
  noise    nobody speaks; a burst of noise (a cough, a knock) of `burst` s  -> does it stop itself

Start the server first:

    uv run --group laptop python -W ignore -m lan_speech --stt-model parakeet \\
        --insecure-loopback --port 8767

    uv run --group laptop python scripts/interrupt_bench.py [--quick] [--reps 3]
    uv run --group laptop python scripts/interrupt_bench.py --only echo noise \\
        --extra-args "--barge-ratio 6"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SR = 16000
PHRASES = {"stop": "Stop.", "wait": "Wait, wait."}  # short words; otherwise a random sentence


# =============================== one trial (runs in its own process) ===============================


def run_trial(spec: dict[str, Any]) -> dict[str, Any]:
    import threading
    import types
    from math import gcd

    import numpy as np
    from scipy.signal import resample_poly

    sys.path.insert(0, "src")
    import random

    import doc_chat.speech as speech
    from doc_chat.__main__ import Session, main
    from doc_chat.phrases import phrases, split_in_two
    from lan_speech.engines import SupertonicTTSEngine

    kind: str = spec["kind"]
    rng = np.random.default_rng(spec.get("seed", 0))
    tts = SupertonicTTSEngine(Path("models/supertonic-3"), total_steps=4, intra_op_threads=2)
    clock: dict[str, float] = {}
    writes: list[tuple[float, float]] = []  # (start, end) of every slice the speaker took
    last_sample = {"v": 0.0, "at_abort": 0.0}
    echo_gain = 10 ** (spec.get("echo_db", -200) / 20)
    echo_q: list[Any] = []
    lock = threading.Lock()

    def say(text: str) -> Any:
        x = np.frombuffer(tts.synthesize_chunk(text), np.int16).astype(np.float32) / 32768
        g = gcd(SR, tts.sample_rate)
        return resample_poly(x, SR // g, tts.sample_rate // g).astype(np.float32)

    def quiet(seconds: float) -> Any:
        return rng.normal(0, 0.003, int(seconds * SR)).astype(np.float32)

    # ---- a speaker that plays at real speed, leaks into the mic, and obeys abort() on its thread
    class RawOutputStream:
        def __init__(self, samplerate: int, **kw: Any) -> None:
            self.rate = samplerate
            self.aborted = False
            self.writer: int | None = None

        def start(self) -> None: ...
        def stop(self) -> None: ...
        def close(self) -> None: ...

        def abort(self) -> None:
            assert threading.get_ident() == self.writer, "abort from a thread that is not writing"
            self.aborted = True
            last_sample["at_abort"] = last_sample["v"]
            clock.setdefault("abort", time.perf_counter())
            with lock:
                echo_q.clear()  # the device buffer is flushed

        def write(self, data: bytes) -> None:
            self.writer = threading.get_ident()
            t_start = time.perf_counter()
            x = np.frombuffer(data, np.int16).astype(np.float32) / 32768
            last_sample["v"] = float(x[-1]) if len(x) else 0.0
            g = gcd(SR, self.rate)
            with lock:
                echo_q.append(
                    resample_poly(x, SR // g, self.rate // g).astype(np.float32) * echo_gain
                )
            end = time.perf_counter() + len(data) / 2 / self.rate
            while time.perf_counter() < end and not self.aborted:
                time.sleep(0.004)
            writes.append((t_start, time.perf_counter()))
            if self.aborted:
                raise RuntimeError("aborted")

    mod = types.ModuleType("sounddevice")
    mod.RawOutputStream = RawOutputStream  # type: ignore[attr-defined]
    sys.modules["sounddevice"] = mod

    # ---- the scripted microphone
    truth: dict[str, str] = {}
    ev: dict[tuple[str, int], asyncio.Event] = {}
    st = {"resp": 0}

    def event(name: str, n: int) -> asyncio.Event:
        return ev.setdefault((name, n), asyncio.Event())

    async def feeder(hub: speech.MicHub) -> None:
        def mix(block: Any) -> Any:
            out = block.copy()
            with lock:
                taken = 0
                while echo_q and taken < len(out):
                    chunk = echo_q[0]
                    n = min(len(chunk), len(out) - taken)
                    out[taken : taken + n] += chunk[:n]
                    taken += n
                    if n == len(chunk):
                        echo_q.pop(0)
                    else:
                        echo_q[0] = chunk[n:]
            return out

        async def play(audio: Any) -> None:
            step = SR // 10
            for i in range(0, len(audio), step):
                block = np.zeros(step, np.float32)
                block[: len(audio[i : i + step])] = audio[i : i + step]
                pcm = (np.clip(mix(block), -1, 1) * 32767).astype(np.int16)
                hub._queue.put_nowait(pcm.tobytes())
                await asyncio.sleep(0.1)

        async def until(name: str, n: int) -> None:
            while not event(name, n).is_set():
                await play(quiet(0.1))

        async def wait(seconds: float) -> None:
            end = time.perf_counter() + seconds
            while time.perf_counter() < end:
                await play(quiet(0.1))

        seed = spec.get("seed", 0)
        if kind == "cutoff":
            sentence = next(t for t in phrases(f"cut{seed}", 12) if len(t.split()) >= 8)
            part1, part2 = split_in_two(sentence, random.Random(seed))
            truth["cutoff"] = sentence
            first = part1
        else:
            first = phrases(f"first{seed}")[0]
        await play(np.concatenate([quiet(1.4), say(first)]))
        if kind == "cutoff":
            await play(np.concatenate([quiet(spec["pause"]), say(part2), quiet(0.2)]))
            await until("done", 1)
            await wait(0.3)
        elif kind == "barge":
            await until("answer", 1)
            await wait(spec["offset"])
            said = PHRASES.get(spec.get("phrase", "")) or phrases(f"barge{seed}")[0]
            truth["barge"] = said
            audio = say(said)
            lv = [
                float(np.sqrt(np.mean(audio[i : i + 1600] ** 2)))
                for i in range(0, len(audio) - 1600, 1600)
            ]
            lead = (
                next((i for i, v in enumerate(lv) if v > 0.02), 0) * 0.1
            )  # quiet before the voice
            clock["user_start"] = time.perf_counter() + lead  # when the voice itself begins
            await play(audio)
            await until("answer", 2)
            await wait(0.4)
        else:  # echo, noise: the user says nothing more
            await until("answer", 1)
            if kind == "noise":
                await wait(spec["at"])
                burst = rng.normal(0, 0.12, int(spec["burst"] * SR)).astype(np.float32)
                clock["burst"] = time.perf_counter()
                await play(burst)
            await until("done", 1)
            await wait(0.3)
        await play(say("quit"))
        while True:
            await play(quiet(1.0))

    def patched_start(self: speech.MicHub) -> None:
        self._task = asyncio.get_running_loop().create_task(feeder(self))

    speech.MicHub.start = patched_start  # type: ignore[method-assign]
    speech.MicHub.stop = lambda self: self._task.cancel()  # type: ignore[method-assign]

    original_chunk = Session._chunk
    original_respond = Session.respond

    def chunk(self: Session, player: object):  # type: ignore[no-untyped-def]
        inner = original_chunk(self, player)

        def push(pcm: bytes) -> None:
            inner(pcm)
            event("answer", st["resp"]).set()

        return push

    async def respond(self: Session, *a, **k):  # type: ignore[no-untyped-def]
        st["resp"] += 1
        n = st["resp"]
        try:
            return await original_respond(self, *a, **k)
        finally:
            event("done", n).set()

    Session._chunk = chunk  # type: ignore[method-assign]
    Session.respond = respond  # type: ignore[method-assign]

    out = Path(spec["out"])
    out.mkdir(parents=True, exist_ok=True)
    argv = [
        "--mic", "--play", "--server", spec["server"], "--no-docs", "--llm", "echo", "--stream",
        "--echo-random", "6", "--echo-delay-ms", "300", "--record", str(out / "run"),
        "--vad-hold", "0.6", *spec.get("extra", []),
    ]  # fmt: skip

    def result(code: int) -> dict[str, Any]:
        turns = out / "run" / "turns.jsonl"
        rows = (
            [json.loads(line) for line in turns.read_text().splitlines()] if turns.exists() else []
        )
        return {
            "spec": spec,
            "exit": code,
            "rows": rows,
            "react_ms": (
                (clock["abort"] - clock["user_start"]) * 1000
                if "abort" in clock and "user_start" in clock
                else None
            ),
            "aborted": "abort" in clock,
            "truth": truth,
            # pauses inside the reply, between two slices of audio: what is heard as a stutter
            "gaps_ms": [
                round((b[0] - a[1]) * 1000, 1)
                for a, b in zip(writes, writes[1:], strict=False)
                if (b[0] - a[1]) > 0.005 and (not clock.get("abort") or b[0] < clock["abort"])
            ],
            "step_at_cut": abs(last_sample["at_abort"]) if "abort" in clock else None,
        }

    def watchdog() -> None:  # a trial that never ends (a self-interrupting loop) is still a result
        time.sleep(spec.get("limit_s", 55))
        print("TRIAL_RESULT " + json.dumps(result(-1)), flush=True)
        os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()
    code = 0
    try:
        code = main(argv)
    except SystemExit as exc:
        code = int(exc.code or 0)
    return result(code)


# =============================== orchestration ===============================


def trial(spec: dict[str, Any], timeout: float = 100) -> dict[str, Any] | None:
    spec = {**spec, "out": tempfile.mkdtemp(prefix="intr_")}
    try:
        proc = subprocess.run(
            [sys.executable, "-W", "ignore", __file__, "--trial", json.dumps(spec)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("TRIAL_RESULT "):
            return json.loads(line[len("TRIAL_RESULT ") :])
    print(f"  trial failed: {proc.stderr.strip().splitlines()[-1:]}", file=sys.stderr)
    return None


def pct(v: list[float], q: float) -> float:
    s = sorted(v)
    return s[min(int(round(q * (len(s) - 1))), len(s) - 1)]


def words(text: str) -> set[str]:
    return {w.strip(".,?!").lower() for w in text.split()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--trial", help=argparse.SUPPRESS)
    ap.add_argument("--server", default="ws://127.0.0.1:8767")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--quick", action="store_true", help="one repetition, fewer settings")
    ap.add_argument("--only", nargs="+", choices=["barge", "bargeecho", "cutoff", "echo", "noise"])
    ap.add_argument("--extra-args", default="", help="more doc_chat flags, e.g. '--barge-ratio 6'")
    ap.add_argument("--write", type=Path, help="save the results as JSON")
    args = ap.parse_args()

    if args.trial:
        res = run_trial(json.loads(args.trial))
        print("TRIAL_RESULT " + json.dumps(res))
        return 0

    extra = args.extra_args.split()
    reps = 1 if args.quick else args.reps
    todo = args.only or ["barge", "bargeecho", "cutoff", "echo", "noise"]
    base = {"server": args.server, "extra": extra}
    results: dict[str, list[dict[str, Any]]] = {}

    def run(kind: str, label: str, **p: Any) -> None:
        for rep in range(reps):
            r = trial({**base, "kind": kind, "seed": rep, **p})
            print(f"  {kind} {label} rep {rep + 1}: {'ok' if r else 'no result'}", flush=True)
            if r:
                results.setdefault(f"{kind}|{label}", []).append(r)

    if "barge" in todo:
        for off in [1.0] if args.quick else [0.5, 2.0, 5.0]:
            run("barge", f"a sentence, {off} s in", offset=off)
        for ph in [] if args.quick else ["stop", "wait"]:
            run("barge", f"'{ph}' alone, 2.0 s in", offset=2.0, phrase=ph)
    if "bargeecho" in todo:
        for db in [-12] if args.quick else [-24, -18, -12]:
            run("barge", f"a sentence, 2.0 s in, echo {db} dB", offset=2.0, echo_db=db)
    if "cutoff" in todo:
        for pause in [1.0] if args.quick else [0.7, 1.0, 1.5, 2.5]:
            run("cutoff", f"pause {pause} s", pause=pause)
    if "echo" in todo:
        for db in [-12] if args.quick else [-30, -24, -18, -12, -6]:
            run("echo", f"{db} dB", echo_db=db)
    if "noise" in todo:
        for burst in [0.2] if args.quick else [0.15, 0.3, 0.6]:
            run("noise", f"burst {burst} s", burst=burst, at=1.5)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import score_tries as st

    sc = st.load_compare()

    def wer(truth: str, heard: str) -> tuple[int, int]:
        ref = sc.norm_words(truth)
        return sc.word_edits(ref, sc.norm_words(heard)), len(ref)

    def rows_after_first(r: dict[str, Any]) -> list[dict[str, Any]]:
        return [x for x in r["rows"] if x.get("question")][1:]

    print(
        f"\n# Interruption bench ({reps} rep(s) each; extra flags: {' '.join(extra) or 'none'})\n"
    )
    if "barge" in todo or "bargeecho" in todo:
        print("## Talking over the reply")
        print(
            "| you say / when | n | it stopped | stops after (ms): p50 / max | what you said, word error |"
        )
        print("|---|---|---|---|---|")
        for key, rs in results.items():
            if not key.startswith("barge|"):
                continue
            react = [r["react_ms"] for r in rs if r["react_ms"] is not None]
            edits = total = 0
            for r in rs:
                truth = r["truth"].get("barge", "")
                best = min(
                    (wer(truth, x["question"]) for x in rows_after_first(r)),
                    default=(len(sc.norm_words(truth)), len(sc.norm_words(truth))),
                )
                edits, total = edits + best[0], total + best[1]
            stat = f"{pct(react, 0.5):.0f} / {max(react):.0f}" if react else "-"
            print(
                f"| {key.split('|')[1]} | {len(rs)} | {len(react)}/{len(rs)} | {stat} | {edits / max(total, 1):.0%} ({edits}/{total} words) |"
            )
        print(
            "(the time is from the moment your voice starts; the detector itself needs speech for --barge-ms first)\n"
        )
    if "cutoff" in todo:
        print("## Pausing mid-sentence, then going on")
        print(
            "| pause | n | cut-off noticed and joined | word error of the joined question | answered more than once |"
        )
        print("|---|---|---|---|---|")
        for key, rs in results.items():
            if not key.startswith("cutoff|"):
                continue
            joined = twice = edits = total = 0
            for r in rs:
                truth = r["truth"].get("cutoff", "")
                answered = [
                    x
                    for x in r["rows"]
                    if not x.get("interrupted") and x.get("answer_words", 0) > 0
                ]
                resumed = any(x.get("interrupted") == "resumed" for x in r["rows"])
                last = answered[0]["question"] if answered else ""
                e, n = wer(truth, last)
                edits, total = edits + e, total + n
                joined += resumed and e <= max(1, n // 6)
                twice += len(answered) > 1
            print(
                f"| {key.split('|')[1]} | {len(rs)} | {joined}/{len(rs)} | {edits / max(total, 1):.0%} | {twice}/{len(rs)} |"
            )
        print(
            "(a pause longer than the wait for the first answer, about 2 s, is a barge-in: nothing to join)\n"
        )
    for kind, title in (
        ("echo", "Its own voice leaking into the microphone (nobody speaks)"),
        ("noise", "A burst of noise while it speaks (nobody speaks)"),
    ):
        if kind in todo:
            print(f"## {title}")
            print("| setting | n | it stopped itself |")
            print("|---|---|---|")
            for key, rs in results.items():
                if key.startswith(kind + "|"):
                    stopped = sum(any(x.get("interrupted") for x in r["rows"]) for r in rs)
                    print(f"| {key.split('|')[1]} | {len(rs)} | {stopped}/{len(rs)} |")
            print()
    allr = [r for rs in results.values() for r in rs]
    if allr:
        gaps = [g for r in allr for g in r["gaps_ms"]]
        steps = [r["step_at_cut"] for r in allr if r["step_at_cut"] is not None]
        print("## Smoothness of the reply (all trials)")
        print(
            f"- pauses inside a reply (a slice of audio arriving late): {len(gaps)} over {len(allr)} trials; "
            + (
                f"p50 {pct(gaps, 0.5):.0f} ms, p95 {pct(gaps, 0.95):.0f} ms, max {max(gaps):.0f} ms"
                if gaps
                else "none"
            )
        )
        if steps:
            print(
                f"- size of the jump at the moment it is cut off (0 = clean, 1 = full scale; a click): p50 {pct(steps, 0.5):.3f}, max {max(steps):.3f}"
            )
        print()
    if args.write:
        args.write.write_text(
            json.dumps(
                {
                    k: [
                        {
                            kk: r[kk]
                            for kk in ("react_ms", "rows", "truth", "gaps_ms", "step_at_cut")
                        }
                        for r in v
                    ]
                    for k, v in results.items()
                },
                indent=1,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
