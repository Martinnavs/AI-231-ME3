"""Replay end-of-turn rules over a recorded session and count how often they cut a phrase in two.

    uv run --group laptop python scripts/eot_replay.py out/pi_bridge/eot-session.wav

The recording (scripts/pi_record.py) holds phrases separated by at least ``--gap`` seconds of
silence. A phrase is a run of activity (Silero confidence >= 0.3 or loud enough) with no gap of
that length inside it, so the real dips between words stay inside it. Each rule sees the audio as
the live client would (32 ms Silero windows, 100 ms loudness blocks) and says when the turn ends.

A rule is judged on two numbers: phrases it cut in two (a fire before the phrase ended) and the
delay from the phrase's last activity to its fire. The delay includes the rule's own hold.
"""

from __future__ import annotations

import argparse
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doc_chat.bargein import SileroOnset  # noqa: E402
from lan_speech.pause import PauseDetector  # noqa: E402

WIN = 512 / 16000  # one Silero window, seconds
BLOCK = 0.1  # one loudness block, seconds


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1:
            raise SystemExit("need a 16 kHz mono WAV (scripts/pi_record.py writes one)")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


@dataclass
class Signals:
    conf: np.ndarray  # Silero confidence per 32 ms window
    rms: np.ndarray  # loudness per 100 ms block (0-1)

    @property
    def seconds(self) -> float:
        return len(self.conf) * WIN


def compute(pcm: np.ndarray, model: str) -> Signals:
    v = SileroOnset(model, keep_history=True)
    raw = pcm.tobytes()
    step = 3200
    for i in range(0, len(raw), step):
        v.feed(raw[i : i + step])
    n = len(pcm) // 1600
    x = pcm[: n * 1600].astype(np.float32).reshape(n, 1600) / 32768.0
    return Signals(np.array(v.history or [], dtype=np.float32), np.sqrt((x**2).mean(axis=1)))


def phrases(sig: Signals, gap_s: float, conf: float = 0.3) -> list[tuple[float, float]]:
    """(start, end) of each phrase: Silero activity at least ``conf``, merged across short gaps."""
    t = np.nonzero(sig.conf >= conf)[0] * WIN
    out: list[tuple[float, float]] = []
    for s in t:
        if out and s - out[-1][1] < gap_s:
            out[-1] = (out[-1][0], float(s + WIN))
        else:
            out.append((float(s), float(s + WIN)))
    return [p for p in out if p[1] - p[0] >= 0.3]  # clicks and coughs are not phrases


# ---- rules: each returns the times (s) at which it would end a turn ----


def rule_loud(sig: Signals, hold: float) -> list[float]:
    """What the live client does today: loudness above 2.5 x room noise, `hold` s of quiet."""
    d = PauseDetector(hold, noise=0.015)
    return [(i + 1) * BLOCK for i, r in enumerate(sig.rms) if d.feed(float(r), BLOCK)[1]]


def _quiet_hold(speech: np.ndarray, hold: float) -> list[float]:
    fires, quiet, heard = [], 0.0, False
    for i, s in enumerate(speech):
        if s:
            heard, quiet = True, 0.0
        elif heard:
            quiet += WIN
            if quiet >= hold:
                fires.append((i + 1) * WIN)
                heard, quiet = False, 0.0
    return fires


def rule_silero(sig: Signals, hold: float, theta: float) -> list[float]:
    """Silero says speech when confidence >= theta; `hold` s without it ends the turn."""
    return _quiet_hold(sig.conf >= theta, hold)


def rule_smooth(sig: Signals, hold: float, theta: float, k: int, n: int) -> list[float]:
    """Speech while at least k of the last n windows were >= theta (rides over single dips)."""
    hit = (sig.conf >= theta).astype(int)
    csum = np.concatenate(([0], np.cumsum(hit)))
    window = csum[n:] - csum[:-n]
    speech = np.concatenate((np.zeros(n - 1, dtype=bool), window >= k))
    return _quiet_hold(speech, hold)


def rule_either(sig: Signals, hold: float, theta: float, db_over_floor: float) -> list[float]:
    """Speech if Silero >= theta OR the block is `db_over_floor` dB over the room (10th pct)."""
    floor = float(np.percentile(sig.rms, 10)) or 1e-4
    loud_blocks = sig.rms > floor * 10 ** (db_over_floor / 20)
    loud = np.repeat(loud_blocks, int(BLOCK / WIN * 1000) // 1000 or 1)[: len(sig.conf)]
    idx = (np.arange(len(sig.conf)) * WIN / BLOCK).astype(int).clip(0, len(loud_blocks) - 1)
    return _quiet_hold((sig.conf >= theta) | loud_blocks[idx], hold) if loud.size else []


def score(fires: list[float], spans: list[tuple[float, float]]) -> dict[str, float]:
    split, delays = 0, []
    for start, end in spans:
        inside = [f for f in fires if start <= f < end - 0.05]
        if inside:
            split += 1
        after = [f for f in fires if f >= end - 0.05]
        if after and (after[0] - end) < 6.0:
            delays.append(after[0] - end)
    d = np.array(delays) if delays else np.array([np.nan])
    return {
        "split": split,
        "phrases": len(spans),
        "ended": len(delays),
        "delay_p50": float(np.nanmedian(d)),
        "delay_p95": float(np.nanpercentile(d, 95)),
        "stray": sum(1 for f in fires if not any(s <= f < e + 6.0 for s, e in spans)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("wav", type=Path)
    ap.add_argument("--model", default="assets/silero_vad.onnx")
    ap.add_argument("--gap", type=float, default=2.0, help="silence (s) that separates phrases")
    ap.add_argument("--min-phrase", type=float, default=0.3)
    args = ap.parse_args(argv)

    sig = compute(read_wav(args.wav), args.model)
    spans = phrases(sig, args.gap)
    print(f"{args.wav.name}: {sig.seconds:.0f} s, {len(spans)} phrases")
    for i, (s, e) in enumerate(spans, 1):
        print(f"  {i:>2}: {s:6.1f}-{e:6.1f} s  ({e - s:.1f} s)")

    rules: list[tuple[str, list[float]]] = []
    for h in (0.6, 0.8, 1.0, 1.2):
        rules.append((f"loud hold={h}", rule_loud(sig, h)))
    for th in (0.5, 0.35, 0.25):
        for h in (0.5, 0.8, 1.2):
            rules.append((f"silero th={th} hold={h}", rule_silero(sig, h, th)))
    for th, k, n in ((0.5, 3, 10), (0.35, 3, 10), (0.35, 2, 8)):
        for h in (0.4, 0.6, 0.8):
            rules.append((f"smooth th={th} {k}/{n} hold={h}", rule_smooth(sig, h, th, k, n)))
    for th in (0.5, 0.35):
        for h in (0.5, 0.8):
            rules.append((f"either th={th} +6dB hold={h}", rule_either(sig, h, th, 6.0)))

    print(f"\n{'rule':<34}{'split':>8}{'ended':>7}{'stray':>7}{'delay p50':>11}{'p95':>7}")
    for name, fires in rules:
        r = score(fires, spans)
        print(
            f"{name:<34}{r['split']:>4}/{r['phrases']:<3}{r['ended']:>7}{r['stray']:>7}"
            f"{r['delay_p50']:>10.2f}s{r['delay_p95']:>6.2f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
