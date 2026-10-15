from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from .types import AudioClip, Transcript, Voice


class TranscriptionStream(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def send(self, pcm: bytes) -> None: ...

    @abstractmethod
    async def finalize(self) -> None: ...

    @abstractmethod
    def results(self) -> AsyncIterator[Transcript]: ...

    @abstractmethod
    async def aclose(self) -> None: ...


class Transcriber(ABC):
    sample_rate: int

    @abstractmethod
    async def transcribe(self, clip: AudioClip) -> Transcript: ...

    @abstractmethod
    def open_stream(self) -> TranscriptionStream: ...

    async def aclose(self) -> None:
        return None


class Synthesizer(ABC):
    sample_rate: int

    @abstractmethod
    async def synthesize(self, text: str, voice: str | None = None) -> AudioClip: ...

    @abstractmethod
    def stream_synthesize(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]: ...

    @abstractmethod
    async def list_voices(self) -> list[Voice]: ...

    async def warm_up(self) -> None:
        return None

    async def cancel(self) -> None:
        return None

    async def aclose(self) -> None:
        return None
