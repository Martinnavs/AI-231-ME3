"""WebSocket and HTTP LAN speech server implementation (ticket 21)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Set
from typing import Any

from lan_speech.engines import STTEngine, STTStream, TTSEngine
from lan_speech.protocol import (
    CancelMessage,
    DoneMessage,
    ErrorMessage,
    FinalizeMessage,
    HealthzResponse,
    ReadyMessage,
    SpeakMessage,
    StartMessage,
    TranscriptMessage,
    pack_tts_audio,
    parse_message,
    split_clauses,
)

logger = logging.getLogger("lan_speech.server")

MAX_FRAME_SIZE = 64 * 1024  # 64 KiB
MAX_TEXT_LENGTH = 2000
MAX_CONNECTIONS = 4

try:
    import websockets
    from websockets.asyncio.server import Server, ServerConnection, serve
    from websockets.datastructures import Headers
    from websockets.http11 import Request, Response
except ImportError:
    websockets = None  # type: ignore[assignment]
    Server = None  # type: ignore[assignment,misc]
    ServerConnection = None  # type: ignore[assignment,misc]
    serve = None  # type: ignore[assignment]
    Headers = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]
    Response = None  # type: ignore[assignment,misc]


class LanSpeechServer:
    """Async LAN speech server serving /stt, /tts, /voices, /healthz."""

    def __init__(
        self,
        stt_engine: STTEngine,
        tts_engine: TTSEngine,
        host: str = "127.0.0.1",
        port: int = 8765,
        token: str | None = None,
        insecure_loopback: bool = False,
    ) -> None:
        self.stt_engine = stt_engine
        self.tts_engine = tts_engine
        self.host = host
        self.port = port
        self.insecure_loopback = insecure_loopback

        is_loopback = host in ("127.0.0.1", "localhost", "::1")
        if not is_loopback:
            logger.warning("Binding to non-loopback address %s; token required", host)

        # Auth validation
        token_from_env = os.environ.get("LAN_SPEECH_TOKEN")
        self.token = token if token is not None else token_from_env

        if not self.token:
            if insecure_loopback and is_loopback:
                logger.info("Running without auth token (--insecure-loopback on loopback)")
            else:
                raise ValueError(
                    "Missing LAN_SPEECH_TOKEN (required unless --insecure-loopback on 127.0.0.1)"
                )

        self._server: Server | None = None
        self._active_connections: set[Any] = set()
        self._active_tasks: set[asyncio.Task[Any]] = set()

    @property
    def active_tasks(self) -> Set[asyncio.Task[Any]]:
        return self._active_tasks

    @property
    def active_connections(self) -> Set[Any]:
        return self._active_connections

    def _check_auth(self, request: Any) -> bool:
        if self.insecure_loopback and self.host in ("127.0.0.1", "localhost", "::1"):
            return True
        if not self.token:
            return False
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return False
        parts = auth_header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        return bool(parts[1] == self.token)

    def _process_request(self, connection: Any, request: Any) -> Any:
        path = request.path.split("?")[0]

        # 1. /healthz: no auth required
        if path == "/healthz":
            resp = HealthzResponse(
                ok=True, stt=self.stt_engine.name, tts=self.tts_engine.name
            ).to_dict()
            body = json.dumps(resp).encode("utf-8")
            return Response(
                200,
                "OK",
                Headers(
                    [
                        ("Content-Type", "application/json"),
                        ("Content-Length", str(len(body))),
                        ("Connection", "close"),
                    ]
                ),
                body,
            )

        # 2. Auth check for all other routes
        if not self._check_auth(request):
            logger.warning("Unauthorized request to %s: missing or invalid token", path)
            return Response(
                401,
                "Unauthorized",
                Headers(
                    [
                        ("WWW-Authenticate", "Bearer"),
                        ("Content-Length", "0"),
                        ("Connection", "close"),
                    ]
                ),
                b"",
            )

        # 3. GET /voices
        if path == "/voices":
            voices = [v.to_dict() for v in self.tts_engine.list_voices()]
            body = json.dumps(voices).encode("utf-8")
            return Response(
                200,
                "OK",
                Headers(
                    [
                        ("Content-Type", "application/json"),
                        ("Content-Length", str(len(body))),
                        ("Connection", "close"),
                    ]
                ),
                body,
            )

        # 4. WebSocket endpoints
        if path in ("/stt", "/tts"):
            return None

        # 5. Not found
        return Response(
            404, "Not Found", Headers([("Content-Length", "0"), ("Connection", "close")]), b""
        )

    async def _handle_connection(self, ws: Any) -> None:
        if len(self._active_connections) >= MAX_CONNECTIONS:
            logger.warning("Rejecting connection: max concurrent connections reached")
            try:
                await ws.send(ErrorMessage(status=503, reason="server busy").to_json())
                await ws.close()
            except Exception:
                pass
            return

        self._active_connections.add(ws)
        try:
            path = ws.request.path.split("?")[0]
            if path == "/tts":
                await self._handle_tts(ws)
            elif path == "/stt":
                await self._handle_stt(ws)
            else:
                await ws.close()
        finally:
            self._active_connections.discard(ws)

    async def _handle_tts(self, ws: Any) -> None:
        # S->C ready message once on connect
        await ws.send(ReadyMessage(sample_rate=self.tts_engine.sample_rate).to_json())

        current_task: asyncio.Task[None] | None = None
        current_context: int | None = None
        cancelled_contexts: set[int] = set()

        async def synthesize_worker(ctx: int, text: str, voice: str | None) -> None:
            chunks = split_clauses(text)
            try:
                for chunk in chunks:
                    if ctx in cancelled_contexts:
                        break
                    pcm_bytes = await asyncio.to_thread(
                        self.tts_engine.synthesize_chunk, chunk, voice
                    )
                    if ctx in cancelled_contexts:
                        break
                    frame = pack_tts_audio(ctx, pcm_bytes)
                    await ws.send(frame)
                if ctx not in cancelled_contexts:
                    await ws.send(DoneMessage(context=ctx).to_json())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("TTS engine synthesis failed")
                with contextlib.suppress(Exception):
                    await ws.send(ErrorMessage(status=500, reason="engine error").to_json())

        try:
            async for raw in ws:
                if len(raw) > MAX_FRAME_SIZE:
                    await ws.send(ErrorMessage(status=413, reason="frame too large").to_json())
                    continue

                if not isinstance(raw, str):
                    continue

                try:
                    msg = parse_message(raw)
                except Exception as exc:
                    logger.debug("Malformed TTS message: %s", exc)
                    continue

                if isinstance(msg, SpeakMessage):
                    if len(msg.text) > MAX_TEXT_LENGTH:
                        await ws.send(ErrorMessage(status=413, reason="text too long").to_json())
                        continue

                    if current_task is not None and not current_task.done():
                        if current_context not in cancelled_contexts:
                            # Edge 5: two speak contexts at once -> rejected with 409
                            await ws.send(ErrorMessage(status=409, reason="context busy").to_json())
                            continue
                        else:
                            current_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await current_task

                    current_context = msg.context
                    task = asyncio.create_task(synthesize_worker(msg.context, msg.text, msg.voice))
                    current_task = task
                    self._active_tasks.add(task)
                    task.add_done_callback(self._active_tasks.discard)

                elif isinstance(msg, CancelMessage):
                    cancelled_contexts.add(msg.context)
                    if current_context == msg.context and current_task and not current_task.done():
                        current_task.cancel()
        finally:
            if current_task and not current_task.done():
                current_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await current_task

    async def _handle_stt(self, ws: Any) -> None:
        has_started = False
        sample_rate = 16000
        stream: STTStream | None = None
        total_samples = 0

        try:
            async for raw in ws:
                if len(raw) > MAX_FRAME_SIZE:
                    await ws.send(ErrorMessage(status=413, reason="frame too large").to_json())
                    continue

                if isinstance(raw, bytes):
                    if not has_started:
                        # Edge 3: audio before start -> 400 start required, socket closed
                        await ws.send(ErrorMessage(status=400, reason="start required").to_json())
                        await ws.close()
                        return

                    if stream is None:
                        try:
                            stream = self.stt_engine.create_stream(sample_rate)
                        except Exception:
                            logger.exception("STT engine failed to create stream")
                            await ws.send(ErrorMessage(status=500, reason="engine error").to_json())
                            continue

                    total_samples += len(raw) // 2
                    try:
                        await asyncio.to_thread(stream.accept_waveform, sample_rate, raw)
                        res = await asyncio.to_thread(stream.decode)
                        if res is not None:
                            t_end = total_samples / sample_rate if res.is_final else None
                            await ws.send(
                                TranscriptMessage(
                                    text=res.text,
                                    is_final=res.is_final,
                                    end_of_turn=res.end_of_turn,
                                    t_audio_end_s=t_end,
                                ).to_json()
                            )
                            if res.end_of_turn:
                                stream = None
                    except Exception:
                        logger.exception("STT stream decode failed")
                        await ws.send(ErrorMessage(status=500, reason="engine error").to_json())

                elif isinstance(raw, str):
                    try:
                        msg = parse_message(raw)
                    except Exception as exc:
                        logger.debug("Malformed STT message: %s", exc)
                        continue

                    if isinstance(msg, StartMessage):
                        has_started = True
                        sample_rate = msg.sample_rate
                        total_samples = 0
                        try:
                            stream = self.stt_engine.create_stream(sample_rate)
                        except Exception:
                            logger.exception("STT stream creation failed on start")
                            await ws.send(ErrorMessage(status=500, reason="engine error").to_json())

                    elif isinstance(msg, FinalizeMessage):
                        if stream is not None:
                            try:
                                final_text = await asyncio.to_thread(stream.finalize)
                                t_end = total_samples / sample_rate
                                await ws.send(
                                    TranscriptMessage(
                                        text=final_text,
                                        is_final=True,
                                        end_of_turn=True,
                                        t_audio_end_s=t_end,
                                    ).to_json()
                                )
                            except Exception:
                                logger.exception("STT stream finalize failed")
                                await ws.send(
                                    ErrorMessage(status=500, reason="engine error").to_json()
                                )
                            stream = None
        finally:
            if stream is not None:
                stream.reset()

    async def start(self) -> None:
        if serve is None:
            raise RuntimeError("websockets package is required to run LanSpeechServer")

        # Note: Set max_size to a large limit so we can receive frames up to MAX_FRAME_SIZE
        # and cleanly return our protocol ErrorMessage(413) when exceeded.
        self._server = await serve(
            self._handle_connection,
            host=self.host,
            port=self.port,
            process_request=self._process_request,
            ping_interval=20,
            ping_timeout=20,
            max_size=1024 * 1024,
        )
        logger.info("LanSpeechServer listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for task in list(self._active_tasks):
            task.cancel()
        if self._active_tasks:
            await asyncio.gather(*list(self._active_tasks), return_exceptions=True)
            self._active_tasks.clear()

        for conn in list(self._active_connections):
            with contextlib.suppress(Exception):
                await conn.close()
        self._active_connections.clear()
