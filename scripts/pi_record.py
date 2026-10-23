"""Record a microphone to a 16 kHz mono WAV, for replaying end-of-turn rules offline.

Run on the Pi (same device setup as scripts/pi_bridge.py), or on the laptop with
``--in-device default --in-rate 16000`` to compare the two microphones. Nothing is played and
no server is needed, so the recording has no assistant echo in it.

    .venv/bin/python scripts/pi_record.py --out out/pi_bridge/eot-session.wav --seconds 240

Read the phrases with at least 3 s of silence between them; scripts/eot_replay.py cuts the
recording into phrases at those gaps and asks each end-of-turn rule whether it kept every phrase
in one piece.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pi_bridge import DecimatingMicHub, _device  # noqa: E402

from doc_chat.speech import MicHub  # noqa: E402


async def record(args: argparse.Namespace) -> None:
    import sounddevice as sd

    sd.default.device = (_device(args.in_device), None)
    hub = DecimatingMicHub(args.in_rate) if args.in_rate != 16000 else MicHub()
    hub.start()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"recording {args.seconds:.0f} s to {out}; read a phrase, then stay silent 3 s", flush=True
    )
    try:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            for i in range(int(args.seconds * 10)):  # 100 ms blocks
                w.writeframes(await hub.get())
                if i % 100 == 99:
                    print(f"  {(i + 1) / 10:.0f} s", flush=True)
    finally:
        hub.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=240.0)
    ap.add_argument("--in-device", default="UACDemo")
    ap.add_argument("--in-rate", type=int, default=48000)
    args = ap.parse_args(argv)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(record(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
