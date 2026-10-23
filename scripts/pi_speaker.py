"""Pi-side speaker: plays the audio another machine sends, and goes silent when told to.

Run on the Pi. The laptop's doc_chat (``--play-remote ws://<pi>:8780``) does the listening,
speech recognition and speech synthesis; this is only the loudspeaker. No model runs here.

    LAN_SPEECH_TOKEN=... .venv/bin/python scripts/pi_speaker.py --device CD002 --rate 48000

Protocol (see doc_chat.playback.RemoteSpeaker): on connect we send {"type": "hello", "rate": N};
then {"type": "begin"}, binary 16-bit mono PCM frames at N, and {"type": "end"} (finish playing)
or {"type": "abort"} (go silent now; what is queued is dropped).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doc_chat.playback import Player  # noqa: E402


def _device(arg: str | None) -> str | int | None:
    return None if not arg else (int(arg) if arg.isdigit() else arg)


async def serve_speaker(
    host: str,
    port: int,
    token: str | None,
    rate: int,
    make_player: Callable[[int], Any] = Player,
    ready: asyncio.Event | None = None,
) -> None:
    """Serve forever. ``make_player(rate)`` returns an object with push/abort/close."""
    from websockets.asyncio.server import serve

    def authorise(connection: Any, request: Any) -> Any:
        if token is None:
            return None
        parts = (request.headers.get("Authorization") or "").split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1] == token:
            return None
        return connection.respond(401, "unauthorized\n")

    async def handle(ws: Any) -> None:
        await ws.send(json.dumps({"type": "hello", "rate": rate}))
        player: Any = None
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    if player is not None:
                        player.push(msg)
                    continue
                data = json.loads(msg)
                kind = data.get("type")
                if kind == "begin":
                    if player is not None:
                        await asyncio.to_thread(player.close)
                    player = make_player(rate)
                elif kind == "abort" and player is not None:
                    player.abort()
                    await asyncio.to_thread(player.close)
                    player = None
                elif kind == "gain" and player is not None:
                    player.set_gain(float(data["db"]))
                elif kind == "end" and player is not None:
                    await asyncio.to_thread(player.close)
                    player = None
        finally:
            if player is not None:
                player.abort()
                await asyncio.to_thread(player.close)

    async with serve(handle, host, port, process_request=authorise):
        print(f"speaker ready on {host}:{port} at {rate} Hz", flush=True)
        if ready is not None:
            ready.set()
        await asyncio.Future()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="0.0.0.0")  # the cable and Wi-Fi; the token guards it
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--device", default="CD002", help="sounddevice name substring or index")
    ap.add_argument("--rate", type=int, default=48000, help="rate the speaker opens at")
    args = ap.parse_args(argv)
    token = os.environ.get("LAN_SPEECH_TOKEN")
    if not token:
        print("LAN_SPEECH_TOKEN is not set", file=sys.stderr)
        return 2
    import sounddevice as sd

    sd.default.device = (None, _device(args.device))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve_speaker(args.host, args.port, token, args.rate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
