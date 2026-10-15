from __future__ import annotations

import argparse
import importlib
import logging
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import soundfile as sf
from mcp.server.fastmcp import FastMCP

import audio_engine
from audio_engine import AudioFileError, Synthesizer, Transcriber, Voice
from audio_engine.wav import read_wav, write_wav
from voice_agent.config import AgentConfig, ConfigError, PathsConfig, load_config
from voice_agent.mcp_servers._paths import (
    PathEscapeError,
    open_regular_for_read,
    resolve_inside,
    write_file_atomic,
)


def _backend_error_type() -> type[Exception]:
    try:
        err = importlib.import_module("audio_engine._ws").SpeechBackendError
    except (ImportError, AttributeError):
        return type("SpeechBackendError", (Exception,), {})
    assert isinstance(err, type) and issubclass(err, Exception)
    return err


SpeechBackendError = _backend_error_type()

MAX_TEXT_CHARS = 2000
DEFAULT_MAX_AUDIO_S = 120.0
MAX_SAMPLE_RATE = 48000
MAX_CHANNELS = 2
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
_WAV_FORMATS = {"WAV", "WAVEX"}

log = logging.getLogger("voice_agent.mcp.speech")
mcp = FastMCP("speech")


@dataclass
class _State:
    media_root: Path
    stt_backend: str = "fake"
    tts_backend: str = "fake"
    max_audio_s: float = DEFAULT_MAX_AUDIO_S
    transcriber: Transcriber | None = None
    synthesizer: Synthesizer | None = None


_state = _State(media_root=PathsConfig().media_root)


def _backend_kwargs(name: str) -> dict[str, object]:
    if name == "fake":
        return {}
    try:
        secrets = load_config().secrets
    except ConfigError:
        return {}
    key = {"deepgram": secrets.deepgram_api_key, "cartesia": secrets.cartesia_api_key}.get(name)
    return {"api_key": key}


def _transcriber() -> Transcriber:
    if _state.transcriber is None:
        name = _state.stt_backend
        _state.transcriber = audio_engine.create_transcriber(name, **_backend_kwargs(name))
    return _state.transcriber


def _synthesizer() -> Synthesizer:
    if _state.synthesizer is None:
        name = _state.tts_backend
        _state.synthesizer = audio_engine.create_synthesizer(name, **_backend_kwargs(name))
    return _state.synthesizer


def _resolve(rel: str, *, must_exist: bool) -> Path:
    try:
        return resolve_inside(_state.media_root, rel, must_exist=must_exist)
    except PathEscapeError:
        raise ValueError("path outside media root") from None
    except FileNotFoundError:
        raise ValueError(f"file not found: {rel}") from None


def _open_audio(path: str) -> BinaryIO:
    try:
        return open_regular_for_read(_state.media_root, path)
    except PathEscapeError:
        raise ValueError("path outside media root") from None
    except FileNotFoundError:
        raise ValueError(f"file not found: {path}") from None
    except OSError:
        raise ValueError(f"not an audio file: {path}") from None


def _check_audio(fh: BinaryIO, path: str) -> None:
    bad = ValueError(f"not an audio file: {path}")
    if os.fstat(fh.fileno()).st_size > MAX_FILE_BYTES:
        raise ValueError(f"audio file too large: {path}")
    try:
        info = sf.info(fh)
    except (OSError, RuntimeError, sf.LibsndfileError):
        raise bad from None
    fh.seek(0)
    if (
        info.format not in _WAV_FORMATS
        or not 0 < info.channels <= MAX_CHANNELS
        or not 0 < info.samplerate <= MAX_SAMPLE_RATE
    ):
        raise bad
    if info.frames * info.channels * 4 > MAX_DECODED_BYTES:
        raise ValueError(f"audio too large: {path}")
    duration = info.frames / info.samplerate
    if duration > _state.max_audio_s:
        raise ValueError(f"audio too long: {duration:.1f}s exceeds {_state.max_audio_s:g}s limit")


@mcp.tool()
async def transcribe_audio_file(path: str) -> dict[str, object]:
    """Transcribe a WAV file under the media folder. Returns {"text", "language", "duration_s"}."""
    _resolve(path, must_exist=True)
    with _open_audio(path) as fh:
        _check_audio(fh, path)
        try:
            clip = read_wav(fh)
        except AudioFileError:
            raise ValueError(f"not an audio file: {path}") from None
    try:
        result = await _transcriber().transcribe(clip)
    except SpeechBackendError as exc:
        raise ValueError(f"speech backend error: {exc}") from exc
    return {"text": result.text, "language": result.language, "duration_s": clip.duration_s}


@mcp.tool()
async def synthesize_speech_to_file(
    text: str, path: str, voice: str | None = None, overwrite: bool = False
) -> dict[str, object]:
    """Speak text into a WAV file under the media folder.

    Returns {"path", "duration_s", "sample_rate"}.
    """
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"text too long: {len(text)} chars exceeds {MAX_TEXT_CHARS}")
    dest = _resolve(path, must_exist=False)
    if not path.lower().endswith(".wav"):
        raise ValueError(f"output path must end in .wav: {path}")
    if dest.is_dir():
        raise ValueError(f"output path is a directory: {path}")
    if dest.exists() and not overwrite:
        raise ValueError(f"output file exists: {path} (pass overwrite=true)")
    try:
        clip = await _synthesizer().synthesize(text, voice)
    except SpeechBackendError as exc:
        raise ValueError(f"speech backend error: {exc}") from exc
    try:
        write_file_atomic(
            _state.media_root, path, lambda fh: write_wav(fh, clip), overwrite=overwrite
        )
    except PathEscapeError:
        raise ValueError("path outside media root") from None
    except FileExistsError:
        raise ValueError(f"output file exists: {path} (pass overwrite=true)") from None
    except (OSError, RuntimeError):
        raise ValueError(f"cannot write output file: {path}") from None
    return {"path": path, "duration_s": clip.duration_s, "sample_rate": clip.sample_rate}


async def _voices() -> list[Voice]:
    try:
        return await _synthesizer().list_voices()
    except SpeechBackendError as exc:
        raise ValueError(f"speech backend error: {exc}") from exc


@mcp.tool()
async def get_tts_voices() -> list[dict[str, object]]:
    """List available voices: [{"id", "name", "language"}]."""
    return [{"id": v.id, "name": v.name, "language": v.language} for v in await _voices()]


@mcp.resource("speech://voices")
async def voices_resource() -> str:
    lines = ["# Voices", ""]
    lines += [f"- `{v.id}`: {v.name} ({v.language or 'any'})" for v in await _voices()]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    parser = argparse.ArgumentParser(prog="voice_agent.mcp_servers.speech")
    parser.add_argument("--stt-backend")
    parser.add_argument("--tts-backend")
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--max-audio-s", type=float, default=DEFAULT_MAX_AUDIO_S)
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_audio_s) or args.max_audio_s <= 0:
        parser.error("--max-audio-s must be a finite number > 0")
    try:
        cfg = load_config()
        agent, paths = cfg.agent, cfg.paths
    except ConfigError:
        agent, paths = AgentConfig(), PathsConfig()
    _state.stt_backend = args.stt_backend or agent.stt_backend
    _state.tts_backend = args.tts_backend or agent.tts_backend
    _state.media_root = (args.media_root or paths.media_root).expanduser()
    _state.max_audio_s = args.max_audio_s
    _state.transcriber = None
    _state.synthesizer = None
    log.info("speech server: media_root=%s", _state.media_root)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
