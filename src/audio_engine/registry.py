from __future__ import annotations

from importlib import import_module
from typing import Any

from .base import Synthesizer, Transcriber

_STT: dict[str, type[Transcriber] | str] = {}
_TTS: dict[str, type[Synthesizer] | str] = {}


def register_transcriber(name: str, cls: type[Transcriber] | str) -> None:
    _STT[name] = cls


def register_synthesizer(name: str, cls: type[Synthesizer] | str) -> None:
    _TTS[name] = cls


def _resolve(table: dict[str, Any], kind: str, name: str) -> Any:
    if name not in table:
        raise KeyError(f"unknown {kind} backend {name!r}; registered: {sorted(table)}")
    target = table[name]
    if isinstance(target, str):
        module, _, attr = target.partition(":")
        target = getattr(import_module(module), attr)
    return target


def create_transcriber(name: str, **kwargs: Any) -> Transcriber:
    cls: type[Transcriber] = _resolve(_STT, "stt", name)
    return cls(**kwargs)


def create_synthesizer(name: str, **kwargs: Any) -> Synthesizer:
    cls: type[Synthesizer] = _resolve(_TTS, "tts", name)
    return cls(**kwargs)


def list_backends() -> dict[str, list[str]]:
    return {"stt": sorted(_STT), "tts": sorted(_TTS)}
