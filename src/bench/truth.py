from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import numpy.typing as npt

from audio_engine import AudioClip, Synthesizer, create_synthesizer
from audio_engine.wav import resample, write_wav

from .signal import longest_internal_gap_s, make_chirp, noise_burst, speech_bounds

SAMPLE_RATE = 16000
_KINDS = ("turn", "barge_in", "noise", "pause")
_LEAD_SILENCE_S = 1.0
_NOISE_MS = 150.0
_NOISE_LEVEL_DBFS = -12.0
_PEAK_LIMIT = 10 ** (-1 / 20)
_PEAK_TARGET = 10 ** (-3 / 20)
_BARGE_MARGIN_S = 1.0
_PAUSE_RANGE_S = (0.3, 1.5)

Kind = Literal["turn", "barge_in", "noise", "pause"]
Samples = npt.NDArray[np.float32]


@dataclass(frozen=True)
class Trial:
    id: str
    kind: Kind
    text: str
    t_start_s: float
    t_speech_end_s: float
    t_event_s: float | None
    pause_s: float | None
    event_text: str | None
    window_end_s: float


@dataclass(frozen=True)
class TruthSet:
    version: int
    sample_rate: int
    seed: int
    chirp_s: float
    stimulus_sha256: str
    synthesizer: str
    trials: list[Trial]


def _prepare(clip: AudioClip, label: str, answer_window_s: float) -> Samples:
    if clip.sample_rate != SAMPLE_RATE:
        clip = resample(clip, SAMPLE_RATE)
    x = clip.samples
    try:
        start, end = speech_bounds(x, SAMPLE_RATE)
        gap = longest_internal_gap_s(x, SAMPLE_RATE)
    except ValueError as exc:
        raise ValueError(f"{label}: {exc}") from exc
    if gap > answer_window_s:
        raise ValueError(f"{label}: internal pause {gap:.2f}s exceeds answer window")
    x = x[round(start * SAMPLE_RATE) : round(end * SAMPLE_RATE)].copy()
    peak = float(np.max(np.abs(x)))
    if peak > _PEAK_LIMIT:
        x *= np.float32(_PEAK_TARGET / peak)
    return x


def _load_spec(spec: Path) -> list[dict[str, Any]]:
    data = json.loads(spec.read_text(encoding="utf-8"))
    turns = data["turns"]
    if not isinstance(turns, list):
        raise ValueError("spec 'turns' must be a list")
    for i, t in enumerate(turns, 1):
        kind = t.get("kind")
        label = f"t{i:03d}"
        if kind not in _KINDS:
            raise ValueError(f"{label}: unknown trial kind {kind!r}")
        if kind == "pause":
            parts = t.get("parts")
            pause_s = t.get("pause_s")
            ok = isinstance(parts, list) and len(parts) == 2
            ok = ok and isinstance(pause_s, int | float)
            if not ok or not _PAUSE_RANGE_S[0] <= pause_s <= _PAUSE_RANGE_S[1]:
                raise ValueError(
                    f"{label}: pause trial needs exactly two parts and pause_s in "
                    f"{_PAUSE_RANGE_S[0]}-{_PAUSE_RANGE_S[1]} s"
                )
        elif not isinstance(t.get("text"), str):
            raise ValueError(f"{label}: missing text")
        if kind == "barge_in" and not isinstance(t.get("event_text"), str):
            raise ValueError(f"{label}: barge_in trial needs event_text")
    return cast("list[dict[str, Any]]", turns)


def build_truth_set(
    spec: Path,
    synthesizer: Synthesizer,
    out_dir: Path,
    *,
    seed: int = 0,
    answer_window_s: float = 8.0,
    event_delay_s: float = 2.5,
    voice: str | None = None,
) -> TruthSet:
    entries = _load_spec(spec)
    sr = SAMPLE_RATE

    def synth(text: str, label: str) -> Samples:
        clip = asyncio.run(synthesizer.synthesize(text, voice))
        return _prepare(clip, label, answer_window_s)

    def n(seconds: float) -> int:
        return round(seconds * sr)

    chirp = make_chirp(sr)
    pieces: list[tuple[int, Samples]] = [(0, chirp)]
    trials: list[Trial] = []
    cursor = len(chirp) + n(_LEAD_SILENCE_S)
    for i, e in enumerate(entries, 1):
        tid = f"t{i:03d}"
        kind: Kind = e["kind"]
        t_event: int | None = None
        pause_s: float | None = None
        event_text: str | None = None
        start = cursor
        if kind == "pause":
            p1 = synth(e["parts"][0], f"{tid} part 1")
            p2 = synth(e["parts"][1], f"{tid} part 2")
            gap = n(float(e["pause_s"]))
            pause_s = gap / sr
            t_event = start + len(p1)
            pieces += [(start, p1), (t_event + gap, p2)]
            speech_end = t_event + gap + len(p2)
            text = f"{e['parts'][0]} {e['parts'][1]}"
            window_end = speech_end + n(answer_window_s)
        else:
            text = e["text"]
            x = synth(text, tid)
            pieces.append((start, x))
            speech_end = start + len(x)
            window_end = speech_end + n(answer_window_s)
            if kind == "barge_in":
                event_text = e["event_text"]
                u = synth(event_text, f"{tid} event")
                t_event = speech_end + n(event_delay_s)
                pieces.append((t_event, u))
                window_end = max(window_end, t_event + len(u) + n(_BARGE_MARGIN_S))
            elif kind == "noise":
                t_event = speech_end + n(event_delay_s)
                burst = noise_burst(sr, _NOISE_MS, seed * 100003 + i, _NOISE_LEVEL_DBFS)
                pieces.append((t_event, burst))
        trials.append(
            Trial(
                id=tid,
                kind=kind,
                text=text,
                t_start_s=start / sr,
                t_speech_end_s=speech_end / sr,
                t_event_s=None if t_event is None else t_event / sr,
                pause_s=pause_s,
                event_text=event_text,
                window_end_s=window_end / sr,
            )
        )
        cursor = window_end

    timeline = np.zeros(cursor, dtype=np.float32)
    for at, piece in pieces:
        timeline[at : at + len(piece)] += piece
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / "stimulus.wav"
    write_wav(wav_path, AudioClip(np.clip(timeline, -1.0, 1.0), sr))
    truth = TruthSet(
        version=1,
        sample_rate=sr,
        seed=seed,
        chirp_s=0.0,
        stimulus_sha256=hashlib.sha256(wav_path.read_bytes()).hexdigest(),
        synthesizer=f"{type(synthesizer).__name__}:{voice or 'default'}",
        trials=trials,
    )
    (out_dir / "truth.json").write_text(
        json.dumps(asdict(truth), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return truth


def load_truth(path: Path) -> TruthSet:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("version") != 1:
        raise ValueError(f"unsupported truth version {raw.get('version')!r}")
    trials = [Trial(**t) for t in raw.pop("trials")]
    return TruthSet(trials=trials, **raw)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.truth")
    ap.add_argument("--spec", type=Path, default=Path("bench/turns/v1.json"))
    ap.add_argument("--backend", default="fake")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--out", type=Path, default=Path("out/bench/v1"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--answer-window", type=float, default=8.0)
    ap.add_argument("--event-delay", type=float, default=2.5)
    args = ap.parse_args(argv)
    synthesizer = create_synthesizer(args.backend)
    truth = build_truth_set(
        args.spec,
        synthesizer,
        args.out,
        seed=args.seed,
        answer_window_s=args.answer_window,
        event_delay_s=args.event_delay,
        voice=args.voice,
    )
    dur = truth.trials[-1].window_end_s
    print(
        f"{len(truth.trials)} trials, {dur:.1f}s, sha256 {truth.stimulus_sha256[:12]} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
