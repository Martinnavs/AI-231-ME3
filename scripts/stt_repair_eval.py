"""Score the entity matcher and repair ladder on recorded clips.

Decodes each clip with Parakeet (hotwords beam search, plus a greedy pass for word confidence),
caches the results, then applies ``lan_speech.transcript_repair`` and reports:

* entity recall before and after automatic repair, and with asks counted as resolved
* false fixes (a name inserted that was not said), damage (a correct name broken)
* how often it asks, and how often the question offered the right name

Thresholds are tuned on the hard set and reported on the held-out set. The name list is the
frozen ``bench/hotwords_v1.txt``; held-out names that are not in it test false positives.

    uv run --group laptop python scripts/stt_repair_eval.py
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import itertools
import json
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from lan_speech.transcript_repair import (
    Entity,
    EntityMatcher,
    Repair,
    RepairConfig,
    Risk,
    Rung,
    Word,
    repair,
)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))  # run without PYTHONPATH
CACHE = Path("out/stt_compare/repair-cache.json")
SETS = {
    "hard": (Path("bench/stt_hard.json"), Path("bench/mic_hard")),
    "heldout": (Path("bench/stt_heldout.json"), Path("bench/mic_heldout")),
}


from lan_speech.hotwords import write_hotwords, write_vocab  # noqa: E402


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def decode_all(sets: dict[str, Any], names: list[str], threads: int) -> dict[str, Any]:
    ev = _load("stt_hotwords_eval")
    sc = ev.load_compare()
    tmp = Path(tempfile.mkdtemp(prefix="repair_"))
    write_vocab(ev.MODEL / "tokens.txt", tmp / "pk.vocab")
    write_hotwords(names, 1.0, tmp / "hot.txt")
    hot = ev.recognizer(
        ev.MODEL,
        threads,
        decoding_method="modified_beam_search",
        max_active_paths=4,
        hotwords_file=str(tmp / "hot.txt"),
        hotwords_score=1.0,
        modeling_unit="bpe",
        bpe_vocab=str(tmp / "pk.vocab"),
    )
    greedy = ev.recognizer(ev.MODEL, threads, decoding_method="greedy_search")
    out: dict[str, Any] = {}
    for set_name, (items_path, audio_dir) in sets.items():
        items = [i for i in json.loads(items_path.read_text()) if i["kind"] != "noise"]
        for it in items:
            pcm = sc.read_wav(audio_dir / f"{it['id']}.wav").astype(np.float32) / 32768
            x = np.concatenate([np.zeros(8000, np.float32), pcm, np.zeros(24000, np.float32)])
            row = {}
            for key, rec in (("hot", hot), ("greedy", greedy)):
                st = rec.create_stream()
                st.accept_waveform(16000, x)
                rec.decode_stream(st)
                r = st.result
                row[key] = {
                    "text": r.text,
                    "tokens": list(r.tokens),
                    "lps": [float(v) for v in r.ys_log_probs],
                }
            out[f"{set_name}/{it['id']}"] = row
        print(f"decoded {set_name}: {len(items)} clips", flush=True)
    return out


def to_words(dec: dict[str, Any]) -> list[tuple[str, float]]:
    """Group sub-word tokens into words with a geometric-mean probability each."""
    words: list[tuple[str, list[float]]] = []
    for tok, lp in zip(dec["tokens"], dec["lps"], strict=False):
        if tok.startswith(" ") or not words:
            words.append((tok.strip(), [lp]))
        else:
            words[-1] = (words[-1][0] + tok, words[-1][1] + [lp])
    return [(w, float(np.exp(np.mean(lps)))) for w, lps in words if w]


def words_with_conf(row: dict[str, Any], sc: Any, use_conf: bool) -> list[Word]:
    hot = to_words(row["hot"])
    if not use_conf:
        return [Word(w) for w, _ in hot]
    gre = to_words(row["greedy"])
    h_norm = [" ".join(sc.norm_words(w)) for w, _ in hot]
    g_norm = [" ".join(sc.norm_words(w)) for w, _ in gre]
    conf = [0.5] * len(hot)  # a word the greedy pass did not also hear counts as unsure
    for tag, i1, i2, j1, _ in difflib.SequenceMatcher(
        a=h_norm, b=g_norm, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                conf[i1 + k] = min(1.0, gre[j1 + k][1])
    return [Word(w, c) for (w, _), c in zip(hot, conf, strict=True)]


def risk_of(text: str) -> Risk:
    first = text.lower().split()[0] if text.split() else ""
    return Risk.HIGH if first in {"call", "text", "message", "send", "email"} else Risk.LOW


@dataclass
class Tally:
    clips: int = 0
    truth: int = 0
    base_hit: int = 0
    auto_hit: int = 0
    ask_resolved: int = 0
    fixed: int = 0
    false_fix: int = 0
    damage: int = 0
    asks: int = 0
    asks_right: int = 0
    targeted: int = 0
    targeted_on_miss: int = 0
    edits_before: int = 0
    edits_after: int = 0
    ref_words: int = 0

    def line(self) -> str:
        pct = lambda a, b: f"{a / b:.0%}" if b else "-"  # noqa: E731
        return (
            f"recall {pct(self.base_hit, self.truth)} -> auto {pct(self.auto_hit, self.truth)} -> "
            f"+asks {pct(self.auto_hit + self.ask_resolved, self.truth)} | fixed {self.fixed}, "
            f"false {self.false_fix}, damage {self.damage} | asks {self.asks} "
            f"(right name {self.asks_right}), slot asks {self.targeted} (entity truly missed "
            f"{self.targeted_on_miss}) | WER {self.edits_before / self.ref_words:.1%} -> "
            f"{self.edits_after / self.ref_words:.1%}"
        )

    def utility(self) -> float:
        return (
            self.fixed
            + 0.7 * self.asks_right
            - 3 * self.false_fix
            - 3 * self.damage
            - 0.4 * (self.asks - self.asks_right)
        )


def evaluate(
    set_name: str,
    items: list[dict[str, Any]],
    cache: dict[str, Any],
    matchers: dict[str, EntityMatcher],
    cfg: RepairConfig,
    sc: Any,
    use_conf: bool,
    slot_mode: bool,
    verbose: bool = False,
) -> Tally:
    t = Tally()
    for it in items:
        row = cache[f"{set_name}/{it['id']}"]
        words = words_with_conf(row, sc, use_conf)
        base_text = " ".join(w.text for w in words)
        slot = it.get("slot", "none") if slot_mode else None
        matcher = matchers.get(slot or "all")
        if matcher is None:  # slot mode and the sentence is not about a name: leave it alone
            res = Repair(base_text, Rung.NONE)
        else:
            res = repair(
                words,
                matcher,
                risk=risk_of(it["text"]),
                expected_category=slot if slot_mode and slot != "none" else None,
                cfg=cfg,
            )
        truth = {" ".join(sc.norm_words(e)) for e in it["entities"]}

        def has(text: str, ent: str) -> bool:
            return f" {ent} " in " " + " ".join(sc.norm_words(text)) + " "

        t.clips += 1
        for e in truth:
            t.truth += 1
            before, after = has(base_text, e), has(res.text, e)
            t.base_hit += before
            t.auto_hit += after
            t.damage += before and not after
            t.fixed += (not before) and after
        if res.rung is Rung.TARGETED:
            t.targeted += 1
            t.targeted_on_miss += any(not has(res.text, e) for e in truth)
        auto = [m for m in res.matches if m.rung in (Rung.SILENT, Rung.IMPLICIT)]
        asks = [m for m in res.matches if m.rung is Rung.CONFIRM]
        for m in auto:
            if " ".join(sc.norm_words(m.entity.name)) not in truth:
                t.false_fix += 1
        for m in asks:
            name = " ".join(sc.norm_words(m.entity.name))
            right = name in truth and not has(res.text, name)
            t.asks += 1
            t.asks_right += name in truth
            t.ask_resolved += right
        ref = sc.norm_words(it["text"])
        t.ref_words += len(ref)
        t.edits_before += sc.word_edits(ref, sc.norm_words(base_text))
        t.edits_after += sc.word_edits(ref, sc.norm_words(res.text))
        if verbose and res.matches:
            for m in res.matches:
                mark = "ok " if " ".join(sc.norm_words(m.entity.name)) in truth else "BAD"
                print(
                    f"  [{set_name}/{it['id']}] {m.rung.name:8} {mark} heard '{m.heard}' -> "
                    f"{m.entity.name} (d={m.distance:.2f}, margin={m.margin:.2f})"
                )
    return t


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-decode instead of using the cache")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--names", type=Path, default=Path("bench/hotwords_v1.txt"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    names = [n.strip() for n in args.names.read_text().splitlines() if n.strip()]
    if args.refresh or not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(decode_all(SETS, names, args.threads)))
    cache = json.loads(CACHE.read_text())
    sc = _load("stt_hotwords_eval").load_compare()
    import cmudict

    known = frozenset(cmudict.words())
    cats: dict[str, str] = json.loads(Path("bench/entities_v1.json").read_text())
    ents = [Entity(n.title(), cats.get(n, "other")) for n in names]
    matchers = {"all": EntityMatcher(ents, known_words=known)}
    for cat in ("music", "contact", "place", "brand"):
        matchers[cat] = EntityMatcher([e for e in ents if e.category == cat], known_words=known)
    items = {
        s: [i for i in json.loads(p.read_text()) if i["kind"] != "noise"]
        for s, (p, _) in SETS.items()
    }

    base = RepairConfig()
    for slot_mode in (False, True):
        mode = (
            "slot-aware (name list restricted to the slot the agent expects)"
            if slot_mode
            else "global list"
        )
        for use_conf in (False, True):
            label = "with greedy-pass confidence" if use_conf else "no confidence"
            print(f"\n=== {mode}; default thresholds; {label}")
            for s_ in SETS:
                tl = evaluate(s_, items[s_], cache, matchers, base, sc, use_conf, slot_mode)
                print(f"{s_:8} {tl.line()}")

    print("\n=== sweep on the hard set (dev); best config then reported on held-out (test)")
    grid = list(
        itertools.product(
            (0.08, 0.12, 0.16, 0.20),
            (0.20, 0.26, 0.32),
            (0.30, 0.34, 0.40),
            (0.04, 0.08),
        )
    )
    for slot_mode in (False, True):
        for use_conf in (False, True):
            best: tuple[float, RepairConfig] | None = None
            for sil, imp, con, mar in grid:
                if not sil <= imp <= con:
                    continue
                cfg = replace(
                    base, silent_max=sil, implicit_max=imp, confirm_max=con, margin_min=mar
                )
                u = evaluate(
                    "hard", items["hard"], cache, matchers, cfg, sc, use_conf, slot_mode
                ).utility()
                if best is None or u > best[0]:
                    best = (u, cfg)
            assert best
            u, cfg = best
            print(
                f"\n[{'slot' if slot_mode else 'global'}, {'conf' if use_conf else 'no conf'}] "
                f"silent {cfg.silent_max}, implicit {cfg.implicit_max}, confirm {cfg.confirm_max}, "
                f"margin {cfg.margin_min} (dev utility {u:.1f})"
            )
            for s_ in SETS:
                tl = evaluate(
                    s_,
                    items[s_],
                    cache,
                    matchers,
                    cfg,
                    sc,
                    use_conf,
                    slot_mode,
                    args.verbose and s_ == "heldout",
                )
                print(f"{s_:8} {tl.line()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
