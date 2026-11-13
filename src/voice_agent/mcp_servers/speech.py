from __future__ import annotations

import argparse
import importlib
import logging
import math
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote, quote_plus

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
    set_trust_anchor,
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
MAX_VOICE_CHARS = 128
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
    lan_url: str = ""
    lan_token: str | None = None
    max_audio_s: float = DEFAULT_MAX_AUDIO_S
    transcriber: Transcriber | None = None
    synthesizer: Synthesizer | None = None


_state = _State(media_root=PathsConfig().media_root)


def _backend_kwargs(name: str) -> dict[str, object]:
    if name == "fake":
        return {}
    if name == "lan":
        return {"url": _state.lan_url, "token": _state.lan_token}
    try:
        secrets = load_config().secrets
    except ConfigError:
        return {}
    key = {"deepgram": secrets.deepgram_api_key, "cartesia": secrets.cartesia_api_key}.get(name)
    return {"api_key": key}


_MIN_PREFIX = 6
_URL_QUERY = re.compile(r"(\w+://[^\s?#]*)[?#]\S*")
_KV_SECRET = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|token|key|authorization)=[^&\s]+")


def _redact(text: str) -> str | None:
    try:
        s = load_config().secrets
    except Exception:
        return None
    text = _URL_QUERY.sub(r"\1", text)
    text = _KV_SECRET.sub(r"\1=[redacted]", text)
    forms: set[str] = set()
    for secret in (s.deepgram_api_key, s.cartesia_api_key, s.anthropic_api_key):
        if secret:
            for variant in (secret, quote(secret, safe=""), quote_plus(secret)):
                forms.update(variant[:n] for n in range(_MIN_PREFIX, len(variant) + 1))
    if forms:
        pattern = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
        text = re.sub(pattern, "[redacted]", text)
    return text


def _backend_failure(exc: Exception) -> ValueError:
    reason = f"speech backend error: {type(exc).__name__}"
    status = getattr(exc, "status", None) if isinstance(exc, SpeechBackendError) else None
    if isinstance(status, int) and not isinstance(status, bool):
        reason += f" (status {status})"
    detail = _redact(str(exc))
    if detail is None:
        log.error("%s", reason)
    else:
        log.error("%s: %s", reason, detail)
    return ValueError(reason)


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
    except OSError:
        raise ValueError(f"path not accessible: {rel}") from None


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
    except Exception as exc:
        raise _backend_failure(exc) from None
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
    if voice is not None:
        if len(voice) > MAX_VOICE_CHARS:
            raise ValueError(f"voice too long: {len(voice)} chars exceeds {MAX_VOICE_CHARS}")
        if not voice.isprintable() or "/" in voice or "\\" in voice:
            raise ValueError("invalid voice id")
    dest = _resolve(path, must_exist=False)
    if not path.lower().endswith(".wav"):
        raise ValueError(f"output path must end in .wav: {path}")
    try:
        is_dir = dest.is_dir()
        exists = dest.exists()
    except OSError:
        raise ValueError(f"path not accessible: {path}") from None
    if is_dir:
        raise ValueError(f"output path is a directory: {path}")
    if exists and not overwrite:
        raise ValueError(f"output file exists: {path} (pass overwrite=true)")
    try:
        clip = await _synthesizer().synthesize(text, voice)
    except Exception as exc:
        raise _backend_failure(exc) from None
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
    except Exception as exc:
        raise _backend_failure(exc) from None


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
    parser.add_argument("--lan-url")
    parser.add_argument("--lan-token-file", type=Path)
    parser.add_argument("--max-audio-s", type=float, default=DEFAULT_MAX_AUDIO_S)
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_audio_s) or args.max_audio_s <= 0:
        parser.error("--max-audio-s must be a finite number > 0")
    try:
        cfg = load_config()
        agent, paths = cfg.agent, cfg.paths
    except ConfigError:
        agent, paths = AgentConfig(), PathsConfig()
    set_trust_anchor(paths.sandbox_root.expanduser())
    _state.stt_backend = args.stt_backend or agent.stt_backend
    _state.tts_backend = args.tts_backend or agent.tts_backend
    _state.media_root = (args.media_root or paths.media_root).expanduser()
    _state.max_audio_s = args.max_audio_s
    _state.lan_url = args.lan_url or ""
    _state.lan_token = (
        args.lan_token_file.expanduser().read_text().strip() if args.lan_token_file else None
    )
    _state.transcriber = None
    _state.synthesizer = None
    log.info("speech server: media_root=%s", _state.media_root)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
