"""Replay the echo guard over a recording made by scripts/remote_echo_check.py.

    uv run --group laptop python scripts/guard_replay.py out/remote_echo/guard60 [more dirs]

Each directory holds mic.wav and trace.npz (per 100 ms block: mic level, the loudness of what was
being played, and the pushes and beeps). The guard sees exactly what it saw live, so settings can
be compared without recording again. A "false fire" is a fire inside a clip's window (push to end
plus 1 s) with nobody talking; with ``--talk-over`` clips a fire after the beep is a hit.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doc_chat.bargein import BargeInGuard, EchoModel, SileroOnset  # noqa: E402


def load(run: Path) -> tuple[list[bytes], dict[str, np.ndarray]]:
    tr = dict(np.load(run / "trace.npz"))
    with wave.open(str(run / "mic.wav"), "rb") as w:
        raw = w.readframes(w.getnframes())
    n = len(tr["t"])
    blocks = [raw[i : i + 3200] for i in range(0, len(raw) - 3199, 3200)][-n:]  # the guarded tail
    return blocks, tr


def replay(
    blocks: list[bytes],
    tr: dict[str, np.ndarray],
    *,
    mode: str,
    ratio: float,
    margin: float,
    prior: float,
    min_s: float,
    confidence: float,
    model: str,
    silero_kofn: tuple[int, int] | None = None,
    level_kofn: tuple[int, int] | None = None,
    min_bar: float = 0.0,
) -> np.ndarray:
    silero = (
        SileroOnset(model, confidence=confidence, min_s=min_s, kofn=silero_kofn)
        if mode != "energy"
        else None
    )
    g = BargeInGuard(
        float(tr["noise"]),
        ratio=ratio,
        min_s=min_s,
        mode=mode,
        silero=silero,
        echo=EchoModel(prior=prior, margin=margin),
        level_kofn=level_kofn,
        min_bar=min_bar,
    )
    fired = np.zeros(len(blocks), dtype=bool)
    starts = [float(p) for p in tr["pushes"][:, 0]]  # the live harness resets once per reply
    next_start = 0
    for i, b in enumerate(blocks):
        t = float(tr["t"][i])
        if next_start < len(starts) and t >= starts[next_start]:
            g.reset()
            next_start += 1
        fired[i] = g.feed(b, float(tr["played"][i]), answering=True)
    return fired


def score(fired: np.ndarray, tr: dict[str, np.ndarray]) -> tuple[int, int, list[float]]:
    """(clips with a false fire, clips scored, seconds from beep to fire for talk-over clips).

    ``pushes`` and ``beeps`` are both in seconds from the first push (not from each clip's own
    push); the beep is the cue to talk, so a fire before it is only the echo (a false fire)."""
    t = tr["t"]
    false, scored, lat = 0, 0, []
    for (push, dur), beep in zip(tr["pushes"], tr["beeps"], strict=True):
        win = (t >= push) & (t <= push + dur + 1.0)
        idx = np.nonzero(fired & win)[0]
        if np.isnan(beep):
            scored += 1
            false += bool(len(idx))
        else:
            before = [i for i in idx if t[i] < beep]
            if before:
                false += 1
                scored += 1
            after = [float(t[i] - beep) for i in idx if t[i] >= beep]
            lat.append(after[0] if after else float("nan"))
    return false, scored, lat


def sweep(args: argparse.Namespace) -> int:
    """Wider grid on all runs together: false fires and hits summed, best first."""
    import itertools

    runs = [load(r) for r in args.runs]
    rows = []
    for ratio, margin, lk, sk, conf in itertools.product(
        (2.0, 2.5, 3.0, 4.0),
        (1.0, 1.4, 1.8),
        ((2, 6), (3, 6), (3, 8), (4, 8)),
        ((5, 10), (7, 10)),
        (0.35, 0.5),
    ):
        false = scored = hits = talk = 0
        lats: list[float] = []
        for blocks, tr in runs:
            fired = replay(
                blocks,
                tr,
                mode="both",
                ratio=ratio,
                margin=margin,
                prior=0.5,
                min_s=0.3,
                confidence=conf,
                model=args.model,
                silero_kofn=sk,
                level_kofn=lk,
            )
            f, sc, lat = score(fired, tr)
            false, scored, talk = false + f, scored + sc, talk + len(lat)
            hits += sum(not np.isnan(v) for v in lat)
            lats += [v for v in lat if not np.isnan(v)]
        rows.append(
            (
                false,
                -hits,
                float(np.median(lats)) if lats else 9.9,
                ratio,
                margin,
                lk,
                sk,
                conf,
                scored,
                talk,
            )
        )
    rows.sort()
    print(f"{'false':>6}{'hits':>7}{'p50 s':>7}  setting")
    for false, nh, p50, ratio, margin, lk, sk, conf, scored, talk in rows[:14]:
        print(
            f"{false:>3}/{scored:<2}{-nh:>4}/{talk:<2}{p50:>7.2f}  "
            f"ratio={ratio} margin={margin} level_kofn={lk} silero_kofn={sk} conf={conf}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--model", default="assets/silero_vad.onnx")
    ap.add_argument("--sweep", action="store_true", help="a wider grid, one line per setting")
    args = ap.parse_args(argv)
    if args.sweep:
        return sweep(args)

    base: dict[str, Any] = dict(ratio=4.0, margin=1.8, prior=0.5, min_s=0.3, confidence=0.7)
    k710 = dict(silero_kofn=(7, 10), confidence=0.5)
    grid: list[tuple[str, dict[str, Any]]] = [
        ("energy", base),
        ("both", base),
        ("both", {**base, **k710}),
        ("both", {**base, **k710, "level_kofn": (3, 8)}),
        ("both", {**base, **k710, "level_kofn": (3, 6)}),
        ("both", {**base, **k710, "level_kofn": (2, 6)}),
        ("both", {**base, **k710, "level_kofn": (3, 8), "ratio": 3.0}),
        ("both", {**base, **k710, "level_kofn": (3, 8), "margin": 1.2}),
        ("both", {**base, "level_kofn": (3, 8)}),
        ("silero", {**base, **k710}),
        ("silero", base),
    ]
    for run in args.runs:
        blocks, tr = load(run)
        talk = int(np.sum(~np.isnan(tr["beeps"])))
        print(f"\n{run.name}: {len(blocks)} blocks, {len(tr['pushes'])} clips, {talk} talk-over")
        print(f"{'guard':<58}{'false fires':>12}{'hits':>8}{'p50 s':>8}")
        for mode, kw in grid:
            fired = replay(blocks, tr, mode=mode, model=args.model, **kw)  # type: ignore[arg-type]
            false, scored, lat = score(fired, tr)
            hits = [v for v in lat if not np.isnan(v)]
            diff = {k: v for k, v in kw.items() if base.get(k) != v}
            label = f"{mode} " + (" ".join(f"{k}={v}" for k, v in diff.items()) or "defaults")
            p50 = f"{np.median(hits):.2f}" if hits else "-"
            hit_s = f"{len(hits)}/{talk}" if talk else "-"
            print(f"{label:<58}{false:>6}/{scored:<5}{hit_s:>8}{p50:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
