# ruff: noqa: E501  (wide report lines)
"""Simulate "end early when the words look finished, ask 'anything to add?' when they do not".

Events, from the recorded command clips (the speaker's own voice, known text):
  finished   the end of each clip: the user is done
  trailing   a pause after a word that cannot end a request ("play something by"): the user will
             carry on, after a pause of L seconds (we try L = 0.4, 0.8, 1.5)

Policy (compared with today's fixed hold H = 0.6 s):
  1. at a 200 ms pause, the text model reads Kroko's interim words; if P(finished) > t1 the
     Parakeet decode starts now (early decode) instead of at H
  2. Parakeet's words are checked again; if P(finished) < t2 the turn is NOT sent: at H the agent
     plays a short prerecorded "Got it - anything to add?" and the user's next speech is stitched on
  3. otherwise the transcript goes to the LLM (at 200 ms + decode, or at H + decode)

Outcomes counted per event: sent early / sent at the normal time / asked to add; for trailing
events also "cut off" (the fixed rule would also cut the user off) vs "recovered" (asked instead) vs
"clean" (user resumed before anything happened). The collected signals are cached in
out/confirm_stitch_events.json; thresholds can be re-swept without decoding again.

Stitching is measured separately: the two halves joined as audio (decode once) vs joined as text
(decode each half), word error against what was said.

    uv run --with transformers --group laptop python scripts/confirm_stitch_sim.py [--refresh]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import score_tries as st  # noqa: E402
import smart_turn_dangling as sd  # noqa: E402
import smart_turn_eval as se  # noqa: E402
from text_turn_eval import TextTurn  # noqa: E402
from text_turn_kroko import kroko_interim  # noqa: E402

from lan_speech.engines import SherpaSTTEngine  # noqa: E402
from lan_speech.hotwords import load_entities  # noqa: E402
from lan_speech.hybrid_stt import pad_with_zeros, trim_silence  # noqa: E402

SR = 16000
HOLD = 0.6  # today's fixed end-of-turn hold
EARLY = 0.2  # pause at which the early decode would start
CACHE = Path("out/confirm_stitch_events.json")


def dip_candidates(pcm: np.ndarray, end: float, nxt: float, reach: float = 0.3) -> list[int]:
    """Samples at energy dips (20 ms frames) within `reach` s of the boundary between two words,
    nearest to the boundary first. The recogniser's word times drift, so the caller checks them."""
    mid = (min(end, nxt) + nxt) / 2
    frame = 320
    n = len(pcm) // frame
    lv = np.sqrt(np.mean(pcm[: n * frame].reshape(n, frame) ** 2, axis=1))
    lo, hi = (
        max(int(mid * SR / frame - reach * SR / frame), 1),
        min(int(mid * SR / frame + reach * SR / frame), n - 2),
    )
    dips = [i for i in range(lo, hi) if lv[i] <= lv[i - 1] and lv[i] <= lv[i + 1]]
    dips.sort(key=lambda i: abs(i * frame / SR - mid))
    return [i * frame + frame // 2 for i in dips[:6]]


def load_labelled() -> list[tuple[str, np.ndarray, str]]:
    sc = st.load_compare()
    out = []
    for items, folder in (
        ("bench/stt_hard.json", "bench/mic_hard"),
        ("bench/stt_heldout.json", "bench/mic_heldout"),
    ):
        for it in json.loads(Path(items).read_text()):
            if it["kind"] != "noise" and it["text"]:
                pcm = sc.read_wav(Path(folder) / f"{it['id']}.wav").astype(np.float32) / 32768
                out.append((f"{folder}/{it['id']}", pcm, it["text"]))
    return out


def collect(args: argparse.Namespace) -> dict[str, Any]:
    sc = st.load_compare()
    plain = st.recognizer([], None, False, 4)  # word times only
    hot = st.recognizer([e.name for e in load_entities(Path("config/entities.json"))], 1.0, True, 4)
    kroko = SherpaSTTEngine("models", threads=2, model="kroko")
    tt = TextTurn(args.model_dir)
    rng = np.random.default_rng(0)

    def decode(pcm: np.ndarray) -> tuple[str, float]:
        x = pad_with_zeros(trim_silence(pcm, SR, 0.3, 0.4), SR, 0.5, 1.0)
        t0 = time.perf_counter()
        s = hot.create_stream()
        s.accept_waveform(SR, x)
        hot.decode_stream(s)
        return str(s.result.text).strip(), (time.perf_counter() - t0) * 1000

    nw = sc.norm_words
    rejected = 0

    def plain_decode(pcm: np.ndarray) -> str:
        x = pad_with_zeros(trim_silence(pcm, SR, 0.3, 0.4), SR, 0.5, 1.0)
        s = plain.create_stream()
        s.accept_waveform(SR, x)
        plain.decode_stream(s)
        return str(s.result.text).strip()

    events: list[dict[str, Any]] = []
    stitch: list[dict[str, Any]] = []
    for name, pcm, truth in load_labelled():
        words = sd.words_with_times(plain, pcm)
        if not words:
            continue
        _, noise = se.segments(pcm)
        cuts = [("finished", int(words[-1][2] * SR), len(words))]
        for k, (w, _a, end) in enumerate(words[:-1]):
            if w not in sd.DANGLING:
                continue
            rejected_before = rejected
            want = nw(" ".join(x[0] for x in words[: k + 1]))
            for cand in dip_candidates(pcm, end, words[k + 1][1]):
                if nw(plain_decode(pcm[:cand])) == want:  # the prefix really ends on that word
                    cuts.append(("trailing", cand, k + 1))
                    break
            else:
                rejected += 1
            assert rejected >= rejected_before
        whole, _ = decode(pcm)
        for kind, point, _n_words in cuts:
            quiet = rng.normal(0, noise, int(EARLY * SR)).astype(np.float32)
            ktext = kroko_interim(kroko, np.concatenate([pcm[:point], quiet]))
            p1 = tt.prob(ktext.lower()) if ktext.strip() else 0.0
            ptext, ms = decode(pcm[:point])
            p2 = tt.prob(ptext) if ptext else 0.0
            events.append(
                dict(clip=name, kind=kind, p1=p1, p2=p2, kroko=ktext, parakeet=ptext, decode_ms=ms)
            )
            if kind == "trailing":
                gap = rng.normal(0, noise, int(0.6 * SR)).astype(np.float32)
                joined, _ = decode(np.concatenate([pcm[:point], gap, pcm[point:]]))
                a, _ = decode(pcm[:point])
                b, _ = decode(pcm[point:])
                stitch.append(
                    dict(
                        clip=name,
                        truth=truth,
                        audio_join=joined,
                        text_join=f"{a} {b}",
                        whole=whole,
                    )
                )
    return {"events": events, "stitch": stitch, "rejected_cuts": rejected}


def plain_text(text: str) -> str:
    """Lower case, no punctuation (apostrophes and hyphens kept): no full stop to hint 'finished'."""
    keep = "".join(c.lower() if c.isalnum() or c in "'- " else " " for c in text)
    return " ".join(keep.split())


def report(data: dict[str, Any], args: argparse.Namespace) -> None:
    ev = data["events"]
    if args.text_form == "plain":
        tt = TextTurn(args.model_dir)
        for e in ev:
            e["p2"] = tt.prob(plain_text(e["parakeet"])) if e["parakeet"] else 0.0
    fin = [e for e in ev if e["kind"] == "finished"]
    nw = st.load_compare().norm_words
    truth = {n: t for n, _, t in load_labelled()}
    # a prefix cannot have as many words as the whole clip; the pause position was off for those
    tra_all = [e for e in ev if e["kind"] == "trailing"]
    tra = [e for e in tra_all if len(nw(e["parakeet"])) < len(nw(truth[e["clip"]]))]
    print(
        f"dropped cuts: {data.get('rejected_cuts', 0)} with no clean pause position, "
        f"{len(tra_all) - len(tra)} that still decoded as the whole clip"
    )
    print(
        f"{len(fin)} finished turns, {len(tra)} trailing-off pauses; fixed hold {HOLD} s, early pause {EARLY} s\n"
    )
    print("## Finished turns (user is done)")
    print(
        "| t1 (start early) | t2 (ask below) | sent early | sent at the normal time | asked 'anything to add?' (unneeded) | mean ready time vs today |"
    )
    print("|---|---|---|---|---|---|")
    for t1 in args.t1:
        for t2 in args.t2:
            early = normal = asked = 0
            saved = []
            for e in fin:
                if e["p2"] < t2:
                    asked += 1
                elif e["p1"] > t1:
                    early += 1
                    saved.append(HOLD - EARLY)
                else:
                    normal += 1
                    saved.append(0.0)
            n = len(fin)
            print(
                f"| {t1} | {t2} | {early / n:.0%} ({early}) | {normal / n:.0%} ({normal}) | {asked / n:.0%} ({asked}) | -{np.mean(saved) * 1000:.0f} ms |"
            )
    print(
        "\n## Trailing-off pauses (user pauses after e.g. 'play something by', then carries on after L)"
    )
    print(
        "| t1 | t2 | L | fixed hold: cut off | policy: cut off | policy: recovered by asking | policy: clean, speculation wasted |"
    )
    print("|---|---|---|---|---|---|---|")
    n = len(tra)
    for t1 in args.t1:
        for t2 in args.t2:
            for lng in args.L:
                if lng < HOLD:
                    wasted = sum(e["p1"] > t1 and e["p2"] >= t2 for e in tra)
                    print(f"| {t1} | {t2} | {lng} s | 0% | 0% | 0% | {wasted / n:.0%} |")
                    continue
                asked = sum(e["p2"] < t2 for e in tra)
                print(
                    f"| {t1} | {t2} | {lng} s | 100% | {(n - asked) / n:.0%} | {asked / n:.0%} | 0% |"
                )
    print(
        "\n(L < 0.6 s: nothing is sent either way; the early decode, if it started, was wasted. L >= 0.6 s: today's fixed hold already cut the user off.)"
    )

    sti = data["stitch"]
    sc = st.load_compare()
    nw = sc.norm_words
    words = sum(len(nw(s["truth"])) for s in sti)
    print(
        f"\n## Stitching the halves ({len(sti)} splits of {len(set(s['clip'] for s in sti))} clips, a 0.6 s pause in between)"
    )
    print("| how the halves are joined | word error vs what was said |")
    print("|---|---|")
    for key, label in (
        ("whole", "no split (reference)"),
        ("audio_join", "audio joined, decoded once"),
        ("text_join", "each half decoded, texts joined"),
    ):
        edits = sum(sc.word_edits(nw(s["truth"]), nw(s[key])) for s in sti)
        print(f"| {label} | {edits / words:.1%} |")
    same = sum(nw(s["audio_join"]) == nw(s["whole"]) for s in sti)
    same_t = sum(nw(s["text_join"]) == nw(s["whole"]) for s in sti)
    print(
        f"\nidentical to the unsplit decode: audio join {same}/{len(sti)}, text join {same_t}/{len(sti)}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=Path, default=Path("models/turn-detector"))
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument(
        "--text-form",
        choices=["written", "plain"],
        default="plain",
        help="Parakeet's words as written (capitals, full stop) or lower case without punctuation",
    )
    ap.add_argument("--t1", type=float, nargs="+", default=[0.5, 0.7])
    ap.add_argument("--t2", type=float, nargs="+", default=[0.2, 0.3, 0.5])
    ap.add_argument("--L", type=float, nargs="+", default=[0.4, 0.8, 1.5])
    args = ap.parse_args()
    if args.refresh or not CACHE.exists():
        data = collect(args)
        CACHE.write_text(json.dumps(data, indent=1))
    else:
        data = json.loads(CACHE.read_text())
    report(data, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
