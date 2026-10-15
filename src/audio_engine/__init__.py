from .base import Synthesizer, Transcriber, TranscriptionStream
from .fake import FakeSynthesizer, FakeTranscriber
from .registry import (
    create_synthesizer,
    create_transcriber,
    list_backends,
    register_synthesizer,
    register_transcriber,
)
from .types import AudioClip, AudioFileError, Transcript, Voice

register_transcriber("fake", FakeTranscriber)
register_synthesizer("fake", FakeSynthesizer)

__all__ = [
    "AudioClip",
    "AudioFileError",
    "FakeSynthesizer",
    "FakeTranscriber",
    "Synthesizer",
    "Transcriber",
    "TranscriptionStream",
    "Transcript",
    "Voice",
    "create_synthesizer",
    "create_transcriber",
    "list_backends",
    "register_synthesizer",
    "register_transcriber",
]
