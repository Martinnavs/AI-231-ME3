import subprocess
import sys

import pytest

from audio_engine import (
    FakeSynthesizer,
    FakeTranscriber,
    Synthesizer,
    Transcriber,
    create_synthesizer,
    create_transcriber,
    list_backends,
)


def test_abc_enforced() -> None:
    class NoMethods(Transcriber):
        pass

    class HalfSynth(Synthesizer):
        async def synthesize(self, text, voice=None):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    with pytest.raises(TypeError):
        NoMethods()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        HalfSynth()  # type: ignore[abstract]


def test_registry_fake_and_unknown() -> None:
    assert isinstance(create_transcriber("fake"), FakeTranscriber)
    assert isinstance(create_synthesizer("fake", ms_per_char=10), FakeSynthesizer)
    assert "fake" in list_backends()["stt"] and "fake" in list_backends()["tts"]
    with pytest.raises(KeyError, match="fake"):
        create_transcriber("nope")
    with pytest.raises(KeyError, match="fake"):
        create_synthesizer("nope")


def test_importing_audio_engine_has_no_vendor_imports() -> None:
    code = "import sys, audio_engine; assert 'websockets' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)
