"""Protocol message shapes and serialization for LAN speech server (ticket 21)."""

from __future__ import annotations

import json
import re
import struct
from dataclasses import asdict, dataclass
from typing import Any


class ProtocolError(ValueError):
    """Raised when parsing an invalid or unknown message."""


@dataclass(frozen=True)
class StartMessage:
    sample_rate: int = 16000
    type: str = "start"

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "sample_rate": self.sample_rate})

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> StartMessage:
        obj = json.loads(data) if isinstance(data, str) else data
        if not isinstance(obj, dict) or obj.get("type") != "start":
            raise ProtocolError(f"Expected type 'start', got {obj.get('type')!r}")
        return cls(sample_rate=int(obj.get("sample_rate", 16000)))


@dataclass(frozen=True)
class FinalizeMessage:
    type: str = "finalize"

    def to_json(self) -> str:
        return json.dumps({"type": self.type})

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> FinalizeMessage:
        obj = json.loads(data) if isinstance(data, str) else data
        if not isinstance(obj, dict) or obj.get("type") != "finalize":
            raise ProtocolError(f"Expected type 'finalize', got {obj.get('type')!r}")
        return cls()


@dataclass(frozen=True)
class TranscriptMessage:
    text: str
    is_final: bool
    end_of_turn: bool
    t_audio_end_s: float | None = None
    type: str = "transcript"

    def to_json(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "text": self.text,
                "is_final": self.is_final,
                "end_of_turn": self.end_of_turn,
                "t_audio_end_s": self.t_audio_end_s,
            }
        )

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> TranscriptMessage:
        obj = json.loads(data) if isinstance(data, str) else data
        if not isinstance(obj, dict) or obj.get("type") != "transcript":
            raise ProtocolError(f"Expected type 'transcript', got {obj.get('type')!r}")
        t_end = obj.get("t_audio_end_s")
        return cls(
            text=str(obj["text"]),
            is_final=bool(obj["is_final"]),
            end_of_turn=bool(obj["end_of_turn"]),
            t_audio_end_s=float(t_end) if t_end is not None else None,
        )


@dataclass(frozen=True)
class ErrorMessage:
    status: int
    reason: str
    type: str = "error"

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "status": self.status, "reason": self.reason})

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> ErrorMessage:
        obj = json.loads(data) if isinstance(data, str) else data
        if not isinstance(obj, dict) or obj.get("type") != "error":
            raise ProtocolError(f"Expected type 'error', got {obj.get('type')!r}")
        return cls(status=int(obj["status"]), reason=str(obj["reason"]))


@dataclass(frozen=True)
class ReadyMessage:
    sample_rate: int
    type: str = "ready"

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "sample_rate": self.sample_rate})

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> ReadyMessage:
        obj = json.loads(data) if isinstance(data, str) else data
        if not isinstance(obj, dict) or obj.get("type") != "ready":
            raise ProtocolError(f"Expected type 'ready', got {obj.get('type')!r}")
        return cls(sample_rate=int(obj["sample_rate"]))


@dataclass(frozen=True)
class SpeakMessage:
    context: int
    text: str
    voice: str | None = None
    type: str = "speak"

    def to_json(self) -> str:
        return json.dumps(
            {"type": self.type, "context": self.context, "text": self.text, "voice": self.voice}
        )

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> SpeakMessage:
        obj = json.loads(data) if isinstance(data, str) else data
        if not isinstance(obj, dict) or obj.get("type") != "speak":
            raise ProtocolError(f"Expected type 'speak', got {obj.get('type')!r}")
        return cls(
            context=int(obj["context"]),
            text=str(obj["text"]),
            voice=str(obj["voice"]) if obj.get("voice") is not None else None,
        )


@dataclass(frozen=True)
class CancelMessage:
    context: int
    type: str = "cancel"

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "context": self.context})

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> CancelMessage:
        obj = json.loads(data) if isinstance(data, str) else data
        if not isinstance(obj, dict) or obj.get("type") != "cancel":
            raise ProtocolError(f"Expected type 'cancel', got {obj.get('type')!r}")
        return cls(context=int(obj["context"]))


@dataclass(frozen=True)
class DoneMessage:
    context: int
    type: str = "done"

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "context": self.context})

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> DoneMessage:
        obj = json.loads(data) if isinstance(data, str) else data
        if not isinstance(obj, dict) or obj.get("type") != "done":
            raise ProtocolError(f"Expected type 'done', got {obj.get('type')!r}")
        return cls(context=int(obj["context"]))


@dataclass(frozen=True)
class VoiceItem:
    id: str
    name: str
    language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VoiceItem:
        return cls(
            id=str(d["id"]),
            name=str(d["name"]),
            language=str(d["language"]) if d.get("language") is not None else None,
        )


@dataclass(frozen=True)
class HealthzResponse:
    ok: bool
    stt: str
    tts: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HealthzResponse:
        return cls(ok=bool(d["ok"]), stt=str(d["stt"]), tts=str(d["tts"]))


Message = (
    StartMessage
    | FinalizeMessage
    | TranscriptMessage
    | ErrorMessage
    | ReadyMessage
    | SpeakMessage
    | CancelMessage
    | DoneMessage
)

_PARSERS: dict[str, type[Any]] = {
    "start": StartMessage,
    "finalize": FinalizeMessage,
    "transcript": TranscriptMessage,
    "error": ErrorMessage,
    "ready": ReadyMessage,
    "speak": SpeakMessage,
    "cancel": CancelMessage,
    "done": DoneMessage,
}


def parse_message(data: str | dict[str, Any]) -> Message:
    obj = json.loads(data) if isinstance(data, str) else data
    if not isinstance(obj, dict) or "type" not in obj:
        raise ProtocolError(f"Malformed message: expected dict with 'type', got {obj!r}")
    mtype = obj["type"]
    parser = _PARSERS.get(mtype)
    if parser is None:
        raise ProtocolError(f"Unknown message type: {mtype!r}")
    return parser.from_json(obj)  # type: ignore[no-any-return]


def pack_tts_audio(context: int, pcm_bytes: bytes) -> bytes:
    """Pack a 4-byte big-endian context id with raw PCM S16LE audio."""
    return struct.pack(">I", context) + pcm_bytes


def unpack_tts_audio(data: bytes) -> tuple[int, bytes]:
    """Unpack a 4-byte big-endian context id and raw PCM S16LE audio."""
    if len(data) < 4:
        raise ProtocolError("TTS audio frame too short for header")
    (context,) = struct.unpack(">I", data[:4])
    return context, data[4:]


COMMA_PAUSE_S = 0.12
CLAUSE_PAUSE_S = 0.22
SENTENCE_PAUSE_S = 0.40

_BOUNDARY = re.compile(r"[,;:.!?][\"')\]\u201d\u2019]*(?=\s|$)")
_ABBREVIATIONS = frozenset(
    ["mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "mt"]
    + ["gen", "col", "capt", "lt", "sgt", "rev", "hon", "e.g", "i.e"]
)
_INITIALS = re.compile(r"(?:[A-Z]\.)*[A-Z]")


def _is_boundary(text: str, match: re.Match[str]) -> bool:
    if text[match.start()] != ".":
        return True
    before = text[: match.start()].split()
    if not before:
        return True
    token = before[-1].lstrip("\"'([\u201c\u2018")
    if token.lower() in _ABBREVIATIONS:
        return False
    return not (_INITIALS.fullmatch(token) and token != "I")


def _pause_for(mark: str, comma_s: float, sentence_s: float) -> float:
    if mark == ",":
        return comma_s
    if mark in ";:":
        return (comma_s + sentence_s) / 2
    return sentence_s


def split_sentences(text: str) -> list[str]:
    """Split at sentence ends (. ! ?), keeping initials like J.K. and Dr. intact."""
    sentences: list[str] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        if text[m.start()] in ".!?" and _is_boundary(text, m):
            sentences.append(text[start : m.end()].strip())
            start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return [x for x in sentences if x]


def split_speech_chunks(
    text: str,
    comma_pause_s: float = COMMA_PAUSE_S,
    sentence_pause_s: float = SENTENCE_PAUSE_S,
) -> list[tuple[str, float]]:
    """Split speak text into (chunk, pause_after_s) pairs, in speaking order.

    Chunks end at `, ; : . ! ?` followed by whitespace; periods after initials or
    abbreviations do not count. A first chunk longer than 6 words is split again
    after word 6 (no pause there). The last chunk has no trailing pause.
    """
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks: list[tuple[str, float]] = []
    start = 0
    for m in _BOUNDARY.finditer(cleaned):
        if not _is_boundary(cleaned, m):
            continue
        piece = cleaned[start : m.end()].strip()
        if piece:
            chunks.append((piece, _pause_for(cleaned[m.start()], comma_pause_s, sentence_pause_s)))
        start = m.end()
    tail = cleaned[start:].strip()
    if tail:
        chunks.append((tail, 0.0))
    if not chunks:
        chunks = [(cleaned, 0.0)]
    first, first_pause = chunks[0]
    words = first.split()
    if len(words) > 6:
        chunks[:1] = [(" ".join(words[:6]), 0.0), (" ".join(words[6:]), first_pause)]
    chunks[-1] = (chunks[-1][0], 0.0)
    return chunks


def split_clauses(text: str) -> list[str]:
    return [chunk for chunk, _pause in split_speech_chunks(text)]
