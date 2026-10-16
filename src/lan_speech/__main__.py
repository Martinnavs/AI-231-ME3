"""CLI entrypoint for LAN speech server (ticket 21)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from lan_speech.engines import SherpaSTTEngine, SupertonicTTSEngine
from lan_speech.models import verify_all
from lan_speech.server import LanSpeechServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("lan_speech")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lan_speech")
    ap.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    ap.add_argument("--models", type=Path, default=Path("models"), help="Path to models directory")
    ap.add_argument(
        "--tts-steps", type=int, default=4, help="Supertonic synthesis steps (default: 4)"
    )
    ap.add_argument(
        "--tts-threads", type=int, default=2, help="Supertonic intra-op threads (default: 2)"
    )
    ap.add_argument("--stt-threads", type=int, default=2, help="Sherpa STT threads (default: 2)")
    ap.add_argument(
        "--stt-int8",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use int8 quantized models for STT (default: True)",
    )
    ap.add_argument(
        "--insecure-loopback",
        action="store_true",
        help="Allow running without auth token on 127.0.0.1 loopback only",
    )
    args = ap.parse_args(argv)

    # 1. Verify model files; exit 2 naming the file if missing or corrupted
    problems = verify_all(args.models)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 2

    # 2. Initialize engines
    try:
        stt = SherpaSTTEngine(
            models_dir=args.models,
            threads=args.stt_threads,
            int8=args.stt_int8,
        )
        tts = SupertonicTTSEngine(
            model_dir=args.models / "supertonic-3",
            total_steps=args.tts_steps,
            intra_op_threads=args.tts_threads,
        )
    except Exception:
        logger.exception("Failed to initialize speech engines")
        return 1

    # 3. Create server instance
    try:
        server = LanSpeechServer(
            stt_engine=stt,
            tts_engine=tts,
            host=args.host,
            port=args.port,
            insecure_loopback=args.insecure_loopback,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    async def run() -> None:
        await server.start()
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
        await server.stop()

    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
