"""Try the speech server's STT: stream the microphone or a WAV file and see what comes back.

Start the server in one terminal:

    uv run --group laptop python -m lan_speech --stt-model parakeet --insecure-loopback \
        --endpoint-silence 0.7

Then, in another:

    uv run --group laptop python scripts/try_stt.py                 # live microphone
    uv run --group laptop python scripts/try_stt.py --wav clip.wav  # a 16 kHz mono WAV
    uv run --group laptop python scripts/try_stt.py --slot contact --risk high

Prints interim text while you speak (Kroko), then the final line (Parakeet with hotwords), the
raw text before name repair, and what the repair ladder did or wants to ask. The final's delay
is measured from the last moment the microphone heard speech, so it includes the server's
end-of-turn wait (--endpoint-silence), not only recognition time. Without --slot the server
guesses it from the first word ("call" = a contact, "play" = music).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # run without PYTHONPATH

from lan_speech.pause import PauseDetector  # noqa: E402

SR = 16000
CHUNK = 1600  # 100 ms
CHUNK_S = CHUNK / SR


def dbfs(x: float) -> float:
    return 20 * float(np.log10(max(x, 1e-6)))


def show_final(msg: dict[str, Any], timing: str, streamed: str = "") -> None:
    sys.stdout.write("\r\033[K")
    rep = msg.get("repair")
    raw = msg.get("raw_text")
    print(f"FINAL  {msg['text']!r}")
    if streamed and _words(streamed) != _words(msg["text"]):
        print(f"  Kroko had streamed: {streamed!r}  (Parakeet re-decoded the finished turn)")
    elif raw is not None:
        print("  Parakeet re-decoded the turn; same words as the streamed text")
    if raw is not None and raw != msg["text"]:
        print(f"  raw (Parakeet, before name repair): {raw!r}")
    if rep:
        print(f"  ladder: {rep['rung']}  (slot {rep['slot']}, risk {rep['risk']})")
        for m in rep["matches"]:
            print(
                f"    heard {m['heard']!r} -> {m['entity']}  "
                f"[{m['rung']}, distance {m['distance']}, margin {m['margin']}]"
            )
        if rep["echo"]:
            print(f"  say back: {', '.join(rep['echo'])}")
        if rep["question"]:
            print(f"  ASK: {rep['question']}")
    elif raw is not None:
        print(
            "  name repair: not applied (only call/text, play, book/order and weather-style "
            "requests get a name list; --slot forces one)"
        )
    if timing:
        print(f"  {timing}")
    print()


def _words(text: str) -> list[str]:
    """Words only, lower case: ignore punctuation, quotes and capitals when comparing texts."""
    return "".join(c.lower() if c.isalnum() or c == " " else " " for c in text).split()


def timing_note(state: dict[str, Any], hold_s: float, now: float) -> str:
    """Say who ended the turn and what the numbers mean; no claim when they would be misleading."""
    fin = state["finalize_sent"]
    if fin is not None and now - fin > 5.0:
        fin = None  # a finalize the server never answered (it had already ended the turn)
    if fin is None:
        sound = state["last_voiced"]
        since = f", {(now - sound) * 1000:.0f} ms after your last sound" if sound else ""
        return (
            "turn ended by the server's own end-of-turn detector (this client's pause detector "
            f"had not fired){since}"
        )
    if state["last_voiced"] is not None and state["last_voiced"] > fin:
        return "you were already speaking again when the answer came, so no delay is shown"
    server = (now - fin) * 1000
    return (
        f"turn ended by this client after {hold_s * 1000:.0f} ms of quiet; the server answered "
        f"{server:.0f} ms later (about {hold_s * 1000 + server:.0f} ms from the end of speech)"
    )


def take_turn_audio(buffer: bytearray, cut: int | None, answered_finalize: bool) -> bytes:
    """Remove and return the audio that belongs to a final transcript.

    If the final answers a `finalize` we sent, only the audio up to that moment is this turn's:
    what was recorded while the server worked (about half a second) is the start of the next
    turn. A final the server produced on its own takes everything recorded so far.
    """
    if answered_finalize and cut is not None:
        taken = bytes(buffer[:cut])
        del buffer[:cut]
        return taken
    taken = bytes(buffer)
    buffer.clear()
    return taken


def save_turn(save_dir: Path, n: int, pcm: bytes, msg: dict[str, Any]) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    stem = save_dir / f"turn-{time.strftime('%H%M%S')}-{n:02d}"
    with wave.open(f"{stem}.wav", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    Path(f"{stem}.json").write_text(json.dumps(msg, indent=1, ensure_ascii=False))


async def run(args: argparse.Namespace) -> int:
    from websockets.asyncio.client import connect

    headers = {}
    token = args.token or os.environ.get("LAN_SPEECH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    start: dict[str, Any] = {"type": "start", "sample_rate": SR}
    if args.slot:
        start["slot"] = args.slot
    if args.risk:
        start["risk"] = args.risk

    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    state: dict[str, Any] = {
        "last_voiced": None,
        "audio_done": None,
        "finalize_sent": None,
        "finals": 0,
        "turn_pcm": bytearray(),
        "last_interim": "",
        "unsent_s": 0.0,
        "cut": None,
    }
    detector = PauseDetector(args.vad_hold)

    async with connect(args.url, additional_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps(start))

        async def receive() -> None:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "error":
                    print(f"\nserver error: {msg}")
                    continue
                if msg.get("type") != "transcript":
                    continue
                if msg["is_final"]:
                    now = time.monotonic()
                    note = timing_note(state, args.vad_hold, now)
                    # the audio up to the moment we asked for the end of the turn belongs to
                    # this final; what was recorded while the server worked belongs to the next
                    cut = state["cut"]
                    fresh = (
                        state["finalize_sent"] is not None and now - state["finalize_sent"] <= 5.0
                    )
                    if fresh and cut is not None:
                        audio = bytes(state["turn_pcm"][:cut])
                        del state["turn_pcm"][:cut]
                    else:
                        audio = bytes(state["turn_pcm"])
                        state["turn_pcm"].clear()
                    state["cut"] = None
                    state["finalize_sent"] = None
                    streamed, state["last_interim"] = state["last_interim"], ""
                    # warn only if more than the silence we appended ourselves was left unsent
                    if state["unsent_s"] > args.tail + 0.2:
                        note += (
                            "\n  NOTE: the pause ended the turn mid-clip; "
                            f"{state['unsent_s']:.1f} s of audio after it was not sent "
                            "(try --vad-hold 1.0)"
                        )
                    if msg["text"].strip():
                        show_final(msg, note, streamed)
                        state["finals"] += 1
                        if args.save:
                            save_turn(args.save, state["finals"], audio, msg)
                    if args.wav and msg["end_of_turn"]:
                        return
                else:
                    state["last_interim"] = msg["text"]
                    sys.stdout.write(f"\r\033[K  ... {msg['text']}")
                    sys.stdout.flush()

        async def send() -> None:
            while (chunk := await queue.get()) is not None:
                x = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768
                rms = float(np.sqrt(np.mean(x**2))) if x.size else 0.0
                voiced, pause = detector.feed(rms, CHUNK_S)
                if args.levels:
                    bar = "#" * min(int(rms / max(detector.threshold, 1e-6) * 10), 40)
                    sys.stdout.write(
                        f"\r\033[K[{dbfs(rms):6.1f} dB | noise {dbfs(detector.noise):6.1f} | "
                        f"bar {'speech' if voiced else '      '}] {bar}"
                    )
                    sys.stdout.flush()
                if voiced:
                    state["last_voiced"] = time.monotonic()
                state["turn_pcm"] += chunk
                await ws.send(chunk)
                if args.wav:
                    await asyncio.sleep(len(chunk) / 2 / SR)  # real-time pacing
                if pause:
                    # what the Pi's VAD does: the user paused, so end the turn now
                    state["finalize_sent"] = time.monotonic()
                    state["cut"] = len(state["turn_pcm"])
                    await ws.send(json.dumps({"type": "finalize"}))
                    if args.wav:
                        left = (queue.qsize() - 1) * CHUNK_S  # minus the end marker
                        state["unsent_s"] = max(left, 0.0)
                        break
            state["audio_done"] = time.monotonic()
            if args.finalize and args.wav and state["finalize_sent"] is None:
                state["finalize_sent"] = time.monotonic()
                await ws.send(json.dumps({"type": "finalize"}))

        recv = asyncio.create_task(receive())
        send_task = asyncio.create_task(send())

        if args.wav:
            with wave.open(str(args.wav)) as w:
                if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
                    print("need a 16 kHz mono 16-bit WAV", file=sys.stderr)
                    return 2
                pcm = w.readframes(w.getnframes())
            # a second of silence first (the pause detector measures the room), a tail after
            pcm = b"\x00\x00" * SR + pcm + b"\x00\x00" * int(args.tail * SR)
            for i in range(0, len(pcm), CHUNK * 2):
                queue.put_nowait(pcm[i : i + CHUNK * 2])
            queue.put_nowait(None)
            await send_task
            try:
                await asyncio.wait_for(recv, timeout=20)
            except TimeoutError:
                print("\nno final transcript within 20 s")
            return 0

        import sounddevice as sd

        loop = asyncio.get_running_loop()

        def on_audio(indata: Any, frames: int, t: Any, status: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        print(
            "Stay quiet for a second (measuring the room), then speak. A pause of "
            f"{args.vad_hold:g} s ends your turn; Ctrl-C to quit.\n"
        )
        with (
            sd.RawInputStream(
                samplerate=SR,
                channels=1,
                dtype="int16",
                blocksize=CHUNK,
                device=args.device,
                callback=on_audio,
            ),
            contextlib.suppress(asyncio.CancelledError),
        ):
            await recv
        queue.put_nowait(None)
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8765/stt")
    ap.add_argument("--wav", type=Path)
    ap.add_argument("--slot", help="kind of name expected: contact, music, place, brand")
    ap.add_argument("--risk", choices=["low", "high"])
    ap.add_argument("--token", help="default: $LAN_SPEECH_TOKEN")
    ap.add_argument("--device", type=int, help="sounddevice input index (python -m sounddevice)")
    ap.add_argument(
        "--vad-hold",
        type=float,
        default=0.5,
        help="seconds of quiet after speech before this client sends 'finalize', like the Pi's "
        "VAD would (default 0.5; 0 = wait for the server's own end-of-turn detection, which on "
        "Kroko lags 1.5-2.5 s)",
    )
    ap.add_argument("--finalize", action="store_true", help="WAV mode: send finalize at the end")
    ap.add_argument("--save", type=Path, help="keep each turn's audio and result in this folder")
    ap.add_argument(
        "--levels", action="store_true", help="show the microphone level and noise floor"
    )
    ap.add_argument("--tail", type=float, default=2.0, help="WAV mode: seconds of silence appended")
    args = ap.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
