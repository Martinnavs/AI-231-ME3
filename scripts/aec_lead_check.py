"""Does the canceller's reference lead hurt? The live EchoCanceller path, offline. (no sound)

    uv run --group laptop --with livekit python scripts/aec_lead_check.py out/remote_echo/fresh80 \
        [--leads 0,30,50,100] [--paths 100,200,650]

Why: WebRTC AEC3 resets its render buffer when render (reference) audio stays more than 8 blocks
(32 ms) ahead of capture for a second (`render_delay_buffer.cc`, `DetectExcessRenderBlocks`,
`buffering.max_allowed_excess_render_blocks = 8`). `doc_chat.aec.EchoCanceller` feeds `lead_ms`
of reference ahead when a reply starts (50 ms live), which by that reading triggers a reset about
a second into every reply. And AEC3's delay search reaches roughly 0.5 s, less than the ~0.65 s
seen after the Pi's sound card has idled.

How: the real `EchoCanceller` runs over a `remote_echo_check.py` recording (`fresh80`: 6
talk-over and 6 echo-only clips at 80%) exactly as live: 100 ms microphone blocks through
`process`, and the clip's audio handed to `reference` in 50 ms slices at the moments
`RemotePlayer` would send them (as fast as possible up to 0.25 s ahead of the speaker, then at
playing speed). Where each clip's echo really starts in the microphone is found by phase
correlation (`aec_offline.build_reference`); the simulated `path` puts the start of sending that
far (plus the 0.25 s cushion) before it, i.e. `path` is the speaker-plus-room-plus-capture
latency the canceller must find. The reference is the clip as sent (no drift correction).

Per lead and path: echo-only clips where Silero (0.5, 7 of 10) still fires; the residual's level
above the room by time since the echo starts (median over clips, dB); talk-over clips where Silero
fires on the speaker after the beep.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from aec_offline import CLIPS, build_reference, read_pcm, silero_fire  # noqa: E402
from remote_echo_check import with_beep  # noqa: E402

from doc_chat.aec import EchoCanceller  # noqa: E402

RATE = 16000
BLOCK = 1600  # 100 ms, as MicHub delivers
SLICE = 800  # 50 ms, as RemotePlayer sends
CUSHION_S = 0.25  # playback.REMOTE_LEAD_S
WINDOWS = ((0.0, 1.0), (1.0, 2.0), (2.0, 99.0))


def level_db(x: np.ndarray) -> float:
    return 20 * float(np.log10(max(float(np.sqrt(np.mean((x / 32768.0) ** 2))), 1e-6)))


def run(
    mic: np.ndarray, clips: list[np.ndarray], starts: list[int], lead_ms: int, path_ms: int
) -> np.ndarray:
    """The microphone after the live canceller path."""
    sends: list[tuple[float, bytes]] = []  # (time in the recording, slice)
    for pcm, start in zip(clips, starts, strict=True):
        s0 = start / RATE - path_ms / 1000
        for j in range(0, len(pcm), SLICE):
            t = max(s0, s0 + j / RATE - CUSHION_S)
            sends.append((t, pcm[j : j + SLICE].tobytes()))
    sends.sort(key=lambda p: p[0])
    ec = EchoCanceller(lead_ms=lead_ms)
    out = np.zeros(len(mic) // BLOCK * BLOCK, dtype=np.int16)
    k = 0
    for b in range(len(out) // BLOCK):
        end_t = (b + 1) * BLOCK / RATE  # the block is delivered when it is complete
        while k < len(sends) and sends[k][0] <= end_t:
            ec.reference(sends[k][1])
            k += 1
        blk = mic[b * BLOCK : (b + 1) * BLOCK].tobytes()
        out[b * BLOCK : (b + 1) * BLOCK] = np.frombuffer(ec.process(blk), dtype=np.int16)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rec", type=Path)
    ap.add_argument("--leads", default="0,30,50,100")
    ap.add_argument("--paths", default="100,200,650")
    ap.add_argument("--vad-model", default="assets/silero_vad.onnx")
    args = ap.parse_args(argv)

    mic = read_pcm(args.rec / "mic.wav")
    result = json.loads((args.rec / "result.json").read_text())
    pushes = np.load(args.rec / "trace.npz")["pushes"]
    _, spans, _ = build_reference(args.rec, mic, result, pushes)
    n = len(result["clips"])
    talk = sum(c["beep_at_s"] is not None for c in result["clips"])
    files = sorted(CLIPS.glob("reply-*.wav"))[:6]
    clips = [read_pcm(files[k % len(files)]) for k in range(n)]
    clips = [
        np.frombuffer(with_beep(p.tobytes()), dtype=np.int16).copy() if k < talk else p
        for k, p in enumerate(clips)
    ]
    starts = [a for a, _ in spans]
    room = level_db(mic[: max(starts[0] - RATE // 2, RATE)])
    print(f"{result['label']}: {n} clips ({talk} talk-over); room {room:.1f} dBFS")
    print("residual = level above the room, median over echo-only clips, by time since the echo")
    print("starts; fires = echo-only clips where Silero still fires (when, s); talk = talk-over")
    print("clips where it fires on the speaker after the beep")
    raw_fires = [
        silero_fire(args.vad_model, mic[a : min(b + RATE, len(mic))])
        for (a, b), c in zip(spans, result["clips"], strict=True)
        if c["beep_at_s"] is None
    ]
    print(f"mic alone: echo-only fires {sum(f is not None for f in raw_fires)}/{n - talk}")
    for path in [int(x) for x in args.paths.split(",")]:
        for lead in [int(x) for x in args.leads.split(",")]:
            y = run(mic, clips, starts, lead, path)
            res: list[list[float]] = [[] for _ in WINDOWS]
            fires, talk_hits = [], 0
            for (a, b), c in zip(spans, result["clips"], strict=True):
                b = min(b + RATE, len(y))
                seg = y[a:b]
                beep = c["beep_at_s"]
                f = silero_fire(args.vad_model, seg, beep or 0.0)
                if beep is not None:
                    talk_hits += f is not None
                    continue
                fires.append(f)
                for w, (lo, hi) in enumerate(WINDOWS):
                    part = seg[int(lo * RATE) : int(hi * RATE)]
                    if len(part) > RATE // 10:
                        res[w].append(level_db(part) - room)
            med = " ".join(
                f"{lo:g}-{hi if hi < 99 else 'end'}s {np.median(r):+5.1f}"
                for (lo, hi), r in zip(WINDOWS, res, strict=True)
            )
            fired = [f for f in fires if f is not None]
            print(
                f"path {path:>3} ms lead {lead:>3} ms: residual {med} dB; fires "
                f"{len(fired)}/{len(fires)} {fired}; talk {talk_hits}/{talk}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
