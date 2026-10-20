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

from lan_speech.engines import SherpaSTTEngine, STTEngine, SupertonicTTSEngine
from lan_speech.hotwords import load_entities
from lan_speech.hybrid_stt import ParakeetHybridSTTEngine
from lan_speech.models import required_dirs, verify_all
from lan_speech.server import LanSpeechServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("lan_speech")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lan_speech")
    ap.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    ap.add_argument("--models", type=Path, default=Path("models"), help="Path to models directory")
    ap.add_argument(
        "--tts-steps", type=int, default=4, help="Supertonic synthesis steps (default: 4)"
    )
    ap.add_argument(
        "--tts-threads", type=int, default=2, help="Supertonic intra-op threads (default: 2)"
    )
    ap.add_argument(
        "--stt-model",
        choices=["kroko", "zipformer", "parakeet"],
        default="kroko",
        help="STT model (default: kroko; zipformer is the older LibriSpeech model; parakeet = "
        "Kroko for streaming and turn ends plus Parakeet with hotwords and name repair for finals)",
    )
    ap.add_argument(
        "--entities",
        type=Path,
        default=Path("config/entities.json"),
        help="names for --stt-model parakeet to bias toward and repair against: JSON "
        '{"Taylor Swift": "music", ...} or a list (default: config/entities.json)',
    )
    ap.add_argument("--parakeet-threads", type=int, default=2, help="Parakeet threads (default: 2)")
    ap.add_argument(
        "--hotword-score", type=float, default=1.0, help="Hotword boost (default: 1.0; >=1.5 hurts)"
    )
    ap.add_argument(
        "--no-repair",
        action="store_true",
        help="with --stt-model parakeet: hotwords only, skip the name repair ladder",
    )
    ap.add_argument("--stt-threads", type=int, default=2, help="Sherpa STT threads (default: 2)")
    ap.add_argument(
        "--comma-pause",
        type=float,
        default=0.12,
        help="Seconds of silence after a comma chunk (default: 0.12)",
    )
    ap.add_argument(
        "--sentence-pause",
        type=float,
        default=0.40,
        help="Seconds of silence after . ! ? (default: 0.40); ; and : use the midpoint",
    )
    ap.add_argument(
        "--endpoint-silence",
        type=float,
        default=1.2,
        help="Seconds of trailing silence after speech that ends a turn (default: 1.2)",
    )
    ap.add_argument(
        "--stt-int8",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="int8 model files for --stt-model zipformer (kroko has only one variant)",
    )
    ap.add_argument(
        "--insecure-loopback",
        action="store_true",
        help="Allow running without auth token on 127.0.0.1 loopback only",
    )
    args = ap.parse_args(argv)

    # 1. Verify model files; exit 2 naming the file if missing or corrupted
    problems = verify_all(args.models, required_dirs(args.stt_model))
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 2

    # 2. Initialize engines
    try:
        stt: STTEngine
        if args.stt_model == "parakeet":
            entities = load_entities(args.entities) if args.entities.is_file() else []
            if not entities:
                logger.warning(
                    "No names loaded from %s: Parakeet runs without hotwords", args.entities
                )
            logger.info("Parakeet hybrid: %d names from %s", len(entities), args.entities)
            stt = ParakeetHybridSTTEngine(
                args.models,
                entities,
                kroko_threads=args.stt_threads,
                parakeet_threads=args.parakeet_threads,
                hotword_score=args.hotword_score,
                endpoint_silence=args.endpoint_silence,
                repair=not args.no_repair,
            )
        else:
            stt = SherpaSTTEngine(
                models_dir=args.models,
                threads=args.stt_threads,
                int8=args.stt_int8,
                model=args.stt_model,
                rule2_trailing_silence=args.endpoint_silence,
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
            comma_pause_s=args.comma_pause,
            sentence_pause_s=args.sentence_pause,
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
