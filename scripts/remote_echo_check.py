"""Does the laptop microphone pick up the Pi speaker, and would a detector fire on it?

Plays the six standard reply clips through the Pi's speaker (scripts/pi_speaker.py, over the
LAN, via RemotePlayer) while the laptop's microphone records. Stay silent while it runs.

    LAN_SPEECH_TOKEN=... uv run --group laptop python scripts/remote_echo_check.py \
        --url ws://<pi>:8780 --label "pi 90%" --out out/remote_echo/pi90

Per clip: how far above the room the clip is at the microphone, whether the Silero VAD at the
usual settings (150 or 250 ms of speech, confidence 0.5 or 0.7) would have fired on it, and
whether the echo guard (`BargeInGuard`, the logic doc_chat uses: Silero AND louder than the echo
to expect from what is being played) fired.

With ``--talk-over N`` the first N clips are interrupted: a beep, mixed into the clip so it comes
out of the Pi, sounds 2.5 s in and you say a phrase over the clip at once; the guard should fire
within about a second of the beep.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doc_chat.bargein import BargeInGuard, EchoModel, SileroOnset  # noqa: E402
from doc_chat.playback import RemotePlayer, RemoteSpeaker  # noqa: E402
from doc_chat.speech import MicHub  # noqa: E402

SETTINGS = ((0.5, 0.15), (0.5, 0.25), (0.7, 0.15), (0.7, 0.25))  # (confidence, seconds of speech)


def with_beep(pcm: bytes, at_s: float = 2.5, length_s: float = 0.3) -> bytes:
    """The clip with a 880 Hz tone mixed in `at_s` seconds from the start: the cue to talk over it.

    The tone comes out of the Pi with the clip, so nothing is played on the laptop."""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    a, b = int(at_s * 16000), int((at_s + length_s) * 16000)
    t = np.arange(b - a) / 16000
    x[a:b] = np.clip(x[a:b] * 0.2 + 0.3 * 32767 * np.sin(2 * np.pi * 880 * t), -32768, 32767)
    return x.astype(np.int16).tobytes()


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


async def run(args: argparse.Namespace) -> dict[str, Any]:
    clips = sorted(Path(args.clips).glob("reply-*.wav"))[: args.n] * args.repeat
    hub = MicHub()
    hub.start()
    blocks: list[tuple[float, bytes]] = []  # (perf_counter when received, 100 ms of audio)
    active: list[RemotePlayer | None] = [None]  # the reply being played, for its loudness
    guard: list[BargeInGuard | None] = [None]  # built once the room's noise is known
    fires: list[float] = []  # perf_counter of every block the guard called "the user"
    # per block: (time, mic level, played level, fired, model hit, level hit, Silero confidence,
    # bar, ms model stage, ms level stage, ms both stages on two threads)
    trace: list[tuple[float, ...]] = []
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="guard")

    def timed(fn: Any, *a: Any) -> tuple[bool, float]:
        t = time.perf_counter()
        r = fn(*a)
        return bool(r), (time.perf_counter() - t) * 1000.0

    async def collect() -> None:
        while True:
            block = await hub.get()
            now = time.perf_counter()
            blocks.append((now, block))
            g = guard[0]
            if g is not None:
                p = active[0]
                played = p.level_recent() if p is not None else 0.0
                t_pair = time.perf_counter()
                # the two stages share no state, so they run side by side on two threads
                f_model = pool.submit(timed, g.model_stage, block)
                f_level = pool.submit(timed, g.level_stage, block, played, True)
                (model_hit, ms_m), (level_hit, ms_l) = f_model.result(), f_level.result()
                ms_pair = (time.perf_counter() - t_pair) * 1000.0
                hit = model_hit and level_hit
                conf = g.silero.last_confidence if g.silero is not None else 0.0
                trace.append(
                    (now, g.last_level, played, hit, model_hit, level_hit, conf, g.last_bar)
                    + (ms_m, ms_l, ms_pair)
                )
                if hit:
                    fires.append(now)

    collector = asyncio.create_task(collect())
    speaker = await asyncio.to_thread(RemoteSpeaker, args.url, os.environ.get("LAN_SPEECH_TOKEN"))
    await asyncio.sleep(args.floor_s)  # the room, speaker silent
    quiet = [
        float(np.sqrt(np.mean((np.frombuffer(b, dtype=np.int16) / 32768.0) ** 2)))
        for _, b in blocks[:-2]
    ]
    noise = float(np.median(quiet))
    floor_blocks_n = len(quiet)
    guard[0] = BargeInGuard(
        noise,
        ratio=4.0,
        min_s=0.3,
        mode="both",
        # settings fixed before the measurement (the Pi pilot's rules), not tuned on the result
        silero=SileroOnset(args.vad_model, confidence=0.5, kofn=(7, 10)),
        echo=EchoModel(),  # prior 0.5, margin 1.8
        level_kofn=(3, 8),
    )
    pushes: list[tuple[str, float, float]] = []  # (name, t_push, duration)
    beeps: dict[str, float] = {}
    for k, clip in enumerate(clips):
        pcm = read_wav(clip)
        if k < args.talk_over:
            pcm = with_beep(pcm)
        player = RemotePlayer(speaker, 16000)
        active[0] = player
        if guard[0] is not None:
            guard[0].reset()
        t_push = time.perf_counter()
        player.push(pcm)
        dur = len(pcm) / 2 / 16000
        label = f"{k + 1:02d} {clip.name}"  # unique per play: clips can repeat
        pushes.append((label, t_push, dur))
        if k < args.talk_over:
            beeps[label] = t_push + 2.5  # when it is sent; it is heard a little later
        await asyncio.to_thread(player.close)
        await asyncio.sleep(max(t_push + dur - time.perf_counter(), 0) + args.gap_s)
        active[0] = None
    collector.cancel()
    hub.stop()
    speaker.close()

    audio = b"".join(b for _, b in blocks)
    t0 = blocks[0][0] - 0.1  # the first block ended when it was received
    Path(args.out).mkdir(parents=True, exist_ok=True)
    with wave.open(str(Path(args.out) / "mic.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(audio)

    # the per-block record, in seconds from the first push, so rules can be replayed offline
    t_ref = pushes[0][1]
    np.savez(
        Path(args.out) / "trace.npz",
        t=np.array([r[0] - t_ref for r in trace]),
        level=np.array([r[1] for r in trace]),
        played=np.array([r[2] for r in trace]),
        fired=np.array([r[3] for r in trace]),
        model_hit=np.array([r[4] for r in trace]),
        level_hit=np.array([r[5] for r in trace]),
        conf=np.array([r[6] for r in trace]),
        bar=np.array([r[7] for r in trace]),
        ms_model=np.array([r[8] for r in trace]),
        ms_level=np.array([r[9] for r in trace]),
        ms_pair=np.array([r[10] for r in trace]),
        pushes=np.array([[t - t_ref, d] for _, t, d in pushes]),
        beeps=np.array([beeps.get(n, np.nan) - t_ref for n, _, _ in pushes]),
        noise=noise,
        silero_floor_blocks=floor_blocks_n,
    )
    x = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(x) // 1600
    rms = np.sqrt((x[: n * 1600].reshape(n, 1600) ** 2).mean(axis=1))
    floor_blocks = int(args.floor_s * 10) - 2
    floor = float(np.median(rms[:floor_blocks]))
    out: dict[str, Any] = {
        "label": args.label,
        "floor_dbfs": round(20 * np.log10(floor), 1),
        "floor_peak_dbfs": round(20 * np.log10(float(rms[:floor_blocks].max())), 1),
        "clips": [],
    }
    for name, t_push, dur in pushes:
        a = int((t_push - t0) * 10)
        b = int((t_push - t0 + dur + 1.0) * 10)
        seg = rms[a:b]
        above = 20 * np.log10(np.maximum(seg, 1e-6) / floor)
        onset = next((i for i, v in enumerate(above) if v > 6.0), None)
        raw = audio[a * 3200 : b * 3200]
        fired = {}
        for conf, secs in SETTINGS:
            v = SileroOnset(args.vad_model, confidence=conf, min_s=secs)
            fired[f"{conf}/{int(secs * 1000)}ms"] = any(
                v.feed(raw[i : i + 3200]) for i in range(0, len(raw), 3200)
            )
        in_window = [f for f in fires if t_push <= f <= t_push + dur + 1.0]
        stamps = np.array([r[0] for r in trace])
        in_clip = (stamps >= t_push) & (stamps <= t_push + dur + 1.0)
        model_any = bool(np.any(np.array([r[4] for r in trace])[in_clip]))
        level_any = bool(np.any(np.array([r[5] for r in trace])[in_clip]))
        beeped = beeps.get(name)
        out["clips"].append(
            {
                "clip": name,
                "guard_fired": bool(in_window),
                "model_stage_fired": model_any,
                "level_stage_fired": level_any,
                "guard_first_fire_s": round(in_window[0] - t_push, 2) if in_window else None,
                "beep_at_s": None if beeped is None else round(beeped - t_push, 2),
                "guard_after_beep_s": (
                    None
                    if beeped is None
                    else next((round(f - beeped, 2) for f in in_window if f >= beeped), None)
                ),
                "above_room_db_median": round(float(np.median(above[: int(dur * 10)])), 1),
                "above_room_db_p95": round(float(np.percentile(above, 95)), 1),
                "sound_arrived_ms": None if onset is None else onset * 100,
                "silero_fired": fired,
            }
        )
    out["compute_ms"] = {
        name: [round(float(np.median(v)), 3), round(float(np.percentile(v, 99)), 3)]
        for name, v in (
            ("silero_stage", [r[8] for r in trace]),
            ("level_stage", [r[9] for r in trace]),
            ("both_on_two_threads", [r[10] for r in trace]),
        )
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True, help="ws://<pi>:8780 (scripts/pi_speaker.py)")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--clips", default="tests/fixtures/pi_echo")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--talk-over", type=int, default=0, help="interrupt the first N clips")
    ap.add_argument("--repeat", type=int, default=1, help="play the clip set this many times")
    ap.add_argument("--vad-model", default="assets/silero_vad.onnx")
    ap.add_argument("--floor-s", type=float, default=8.0)
    ap.add_argument("--gap-s", type=float, default=1.5)
    args = ap.parse_args(argv)
    res = asyncio.run(run(args))
    Path(args.out, "result.json").write_text(json.dumps(res, indent=1))
    print(f"{res['label']}: room {res['floor_dbfs']} dBFS (peak {res['floor_peak_dbfs']})")
    print(
        "| clip | above room (median / p95 dB) | Silero stage | level stage | guard | after beep |"
    )
    for c in res["clips"]:
        guard = "no" if not c["guard_fired"] else f"yes @{c['guard_first_fire_s']} s"
        beep = "" if c["beep_at_s"] is None else str(c["guard_after_beep_s"])
        print(
            f"| {c['clip']} | {c['above_room_db_median']} / {c['above_room_db_p95']}"
            f" | {'fired' if c['model_stage_fired'] else '-'}"
            f" | {'fired' if c['level_stage_fired'] else '-'} | {guard} | {beep} |"
        )
    cm = res["compute_ms"]
    print("per-block compute, median / p99 ms:", {k: v for k, v in cm.items()})
    n = len(res["clips"])
    print(f"guard fired on {sum(c['guard_fired'] for c in res['clips'])} of {n} clips")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
