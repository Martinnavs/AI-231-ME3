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

SR = 16000
CHUNK = 1600  # 100 ms
VOICED_RMS = 0.02


def show_final(msg: dict[str, Any], delay_ms: float | None, server_ms: float | None) -> None:
    sys.stdout.write("\r\033[K")
    rep = msg.get("repair")
    raw = msg.get("raw_text")
    print(f"FINAL  {msg['text']!r}")
    if raw is not None and raw != msg["text"]:
        print(f"  raw (Parakeet): {raw!r}")
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
        print("  ladder: not applied (no name list for this kind of request)")
    if delay_ms is not None:
        extra = ""
        if server_ms is not None:
            extra = f" (client pause {delay_ms - server_ms:.0f} ms + server {server_ms:.0f} ms)"
        print(f"  arrived {delay_ms:.0f} ms after the last speech was heard{extra}")
    print()


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
    }

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
                    ref = state["last_voiced"] or state["audio_done"]
                    delay = (now - ref) * 1000 if ref else None
                    fin = state["finalize_sent"]
                    server = (now - fin) * 1000 if fin else None
                    state["finalize_sent"] = None
                    if msg["text"].strip():
                        show_final(msg, delay, server)
                        state["finals"] += 1
                    if args.wav and msg["end_of_turn"]:
                        return
                else:
                    sys.stdout.write(f"\r\033[K  ... {msg['text']}")
                    sys.stdout.flush()

        async def send() -> None:
            heard = False
            quiet = 0.0
            while (chunk := await queue.get()) is not None:
                x = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768
                voiced = x.size > 0 and float(np.sqrt(np.mean(x**2))) > VOICED_RMS
                if voiced:
                    state["last_voiced"] = time.monotonic()
                    heard, quiet = True, 0.0
                elif heard:
                    quiet += len(chunk) / 2 / SR
                await ws.send(chunk)
                if args.wav:
                    await asyncio.sleep(len(chunk) / 2 / SR)  # real-time pacing
                if args.vad_hold > 0 and heard and quiet >= args.vad_hold:
                    # what the Pi's VAD does: the user paused, so end the turn now
                    state["finalize_sent"] = time.monotonic()
                    await ws.send(json.dumps({"type": "finalize"}))
                    heard, quiet = False, 0.0
                    if args.wav:
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
            pcm += b"\x00\x00" * int(args.tail * SR)  # silence so the server ends the turn
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

        print("Speak. The server ends your turn after a pause; Ctrl-C to quit.\n")
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
    ap.add_argument("--tail", type=float, default=2.0, help="WAV mode: seconds of silence appended")
    args = ap.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
