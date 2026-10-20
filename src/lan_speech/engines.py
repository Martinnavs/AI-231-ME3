"""STT and TTS engine interfaces and implementations for LAN speech server (ticket 21)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from lan_speech.protocol import VoiceItem

logger = logging.getLogger(__name__)

SHERPA_DIR = "sherpa-zipformer-en-2023-06-26"
KROKO_DIR = "kroko-en"
SHERPA_STEM = "epoch-99-avg-1-chunk-16-left-128"
SUPERTONIC_DIR = "supertonic-3"


FINALIZE_TAIL_S = 1.5
_TRIM_THRESHOLD = 0.01
_TRIM_MARGIN_S = 0.04


class STTDecodeResult(NamedTuple):
    text: str
    is_final: bool
    end_of_turn: bool
    raw_text: str | None = None  # model output before entity repair (final results only)
    repair: dict[str, Any] | None = None  # rung, question, echo, matches (see transcript_repair)


class STTStream(Protocol):
    def accept_waveform(self, sample_rate: int, pcm_bytes: bytes) -> None: ...
    def decode(self) -> STTDecodeResult | None: ...
    def finalize(self) -> str: ...
    def reset(self) -> None: ...


class STTEngine(Protocol):
    @property
    def name(self) -> str: ...
    def create_stream(self, sample_rate: int = 16000) -> STTStream: ...


class TTSEngine(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def sample_rate(self) -> int: ...
    def list_voices(self) -> list[VoiceItem]: ...
    def synthesize_chunk(self, text: str, voice: str | None = None) -> bytes: ...


class SherpaSTTStream:
    """Wrapper around sherpa-onnx OnlineStream."""

    def __init__(self, recognizer: Any, stream: Any) -> None:
        self._rec = recognizer
        self._stream = stream
        self._last_text = ""

    def accept_waveform(self, sample_rate: int, pcm_bytes: bytes) -> None:
        import numpy as np

        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self._stream.accept_waveform(sample_rate, samples)

    def decode(self) -> STTDecodeResult | None:
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)

        if self._rec.is_endpoint(self._stream):
            text = str(self._rec.get_result(self._stream)).strip()
            self._rec.reset(self._stream)
            self._last_text = ""
            return STTDecodeResult(text=text, is_final=True, end_of_turn=True)

        text = str(self._rec.get_result(self._stream)).strip()
        if text and text != self._last_text:
            self._last_text = text
            return STTDecodeResult(text=text, is_final=False, end_of_turn=False)
        return None

    def finalize(self) -> str:
        import numpy as np

        tail = np.zeros(int(FINALIZE_TAIL_S * 16000), dtype=np.float32)
        self._stream.accept_waveform(16000, tail)
        self._stream.input_finished()
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        text = str(self._rec.get_result(self._stream)).strip()
        self._rec.reset(self._stream)
        self._last_text = ""
        return text

    def reset(self) -> None:
        self._rec.reset(self._stream)
        self._last_text = ""


class SherpaSTTEngine:
    """sherpa-onnx streaming transducer STT engine (Kroko or the LibriSpeech Zipformer)."""

    def __init__(
        self,
        models_dir: Path | str,
        threads: int = 2,
        int8: bool = True,
        rule1_trailing_silence: float = 2.4,
        rule2_trailing_silence: float = 1.2,
        rule3_utterance_length: float = 20.0,
        model: str = "kroko",
    ) -> None:
        import sherpa_onnx

        if model not in ("kroko", "zipformer"):
            raise ValueError(f"unknown STT model: {model}")
        self._model = model
        if model == "kroko":
            d = Path(models_dir) / KROKO_DIR
            files = {"encoder": "encoder.onnx", "decoder": "decoder.onnx", "joiner": "joiner.onnx"}
        else:
            d = Path(models_dir) / SHERPA_DIR
            sfx = ".int8.onnx" if int8 else ".onnx"
            files = {k: f"{k}-{SHERPA_STEM}{sfx}" for k in ("encoder", "decoder", "joiner")}
        self._rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"),
            encoder=str(d / files["encoder"]),
            decoder=str(d / files["decoder"]),
            joiner=str(d / files["joiner"]),
            num_threads=threads,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=rule1_trailing_silence,
            rule2_min_trailing_silence=rule2_trailing_silence,
            rule3_min_utterance_length=rule3_utterance_length,
            provider="cpu",
        )

    @property
    def name(self) -> str:
        return f"sherpa-{self._model}"

    def create_stream(self, sample_rate: int = 16000) -> STTStream:
        stream = self._rec.create_stream()
        return SherpaSTTStream(self._rec, stream)


class SupertonicTTSEngine:
    """Supertonic-3 TTS engine."""

    def __init__(
        self,
        model_dir: Path | str,
        total_steps: int = 4,
        intra_op_threads: int = 2,
        default_voice: str = "F1",
    ) -> None:
        from supertonic import TTS

        self._tts = TTS(
            model="supertonic-3",
            model_dir=Path(model_dir),
            auto_download=False,
            intra_op_num_threads=intra_op_threads,
            inter_op_num_threads=1,
        )
        self._total_steps = total_steps
        self._default_voice = default_voice
        self._styles: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "supertonic-3"

    @property
    def sample_rate(self) -> int:
        return int(self._tts.sample_rate)

    def list_voices(self) -> list[VoiceItem]:
        return [
            VoiceItem(id=name, name=name, language="en") for name in self._tts.voice_style_names
        ]

    def synthesize_chunk(self, text: str, voice: str | None = None) -> bytes:
        import numpy as np

        voice_name = voice or self._default_voice
        if voice_name not in self._styles:
            self._styles[voice_name] = self._tts.get_voice_style(voice_name)
        style = self._styles[voice_name]

        wav, _dur = self._tts.synthesize(
            text, voice_style=style, total_steps=self._total_steps, lang="en"
        )
        clipped = np.clip(wav.squeeze(0), -1.0, 1.0)
        voiced = np.flatnonzero(np.abs(clipped) > _TRIM_THRESHOLD)
        if voiced.size:
            margin = round(_TRIM_MARGIN_S * self.sample_rate)
            clipped = clipped[max(voiced[0] - margin, 0) : voiced[-1] + margin]
        pcm_bytes = (clipped * 32767.0).astype(np.int16).tobytes()
        return bytes(pcm_bytes)


class FakeSTTStream:
    """Fake STT stream for testing without models."""

    def __init__(
        self,
        responses: Sequence[STTDecodeResult] | None = None,
        finalize_text: str = "final transcript",
    ) -> None:
        self._responses = list(responses) if responses is not None else None
        self._finalize_text = finalize_text
        self._chunks_received = 0
        self._interim_sent = False

    def accept_waveform(self, sample_rate: int, pcm_bytes: bytes) -> None:
        self._chunks_received += 1

    def decode(self) -> STTDecodeResult | None:
        if self._responses is not None:
            if self._responses:
                return self._responses.pop(0)
            return None
        # Default behavior:
        # chunk 1 -> interim "turn on"
        # chunk 2 -> final/endpoint "turn on the lights"
        if self._chunks_received == 1 and not self._interim_sent:
            self._interim_sent = True
            return STTDecodeResult(text="turn on", is_final=False, end_of_turn=False)
        elif self._chunks_received >= 2 and self._interim_sent:
            self._interim_sent = False
            self._chunks_received = 0
            return STTDecodeResult(text="turn on the lights", is_final=True, end_of_turn=True)
        return None

    def finalize(self) -> str:
        text = self._finalize_text
        self._chunks_received = 0
        self._interim_sent = False
        return text

    def reset(self) -> None:
        self._chunks_received = 0
        self._interim_sent = False


class FakeSTTEngine:
    """Fake STT engine for tests."""

    def __init__(
        self,
        stream_factory: Callable[[], STTStream] | None = None,
        raise_on_create: bool = False,
    ) -> None:
        self._stream_factory: Callable[[], STTStream] = stream_factory or (lambda: FakeSTTStream())
        self._raise_on_create = raise_on_create

    @property
    def name(self) -> str:
        return "fake-stt"

    def create_stream(self, sample_rate: int = 16000) -> STTStream:
        if self._raise_on_create:
            raise RuntimeError("Engine simulated failure")
        return self._stream_factory()


class FakeTTSEngine:
    """Fake TTS engine for tests without model files."""

    def __init__(
        self,
        sample_rate: int = 24000,
        delay_s: float = 0.0,
        raise_on_synth: bool = False,
    ) -> None:
        self._sample_rate = sample_rate
        self._delay_s = delay_s
        self._raise_on_synth = raise_on_synth
        self.synth_calls: list[str] = []

    @property
    def name(self) -> str:
        return "fake-tts"

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def list_voices(self) -> list[VoiceItem]:
        return [
            VoiceItem(id="F1", name="Fake F1", language="en"),
            VoiceItem(id="M1", name="Fake M1", language="en"),
        ]

    def synthesize_chunk(self, text: str, voice: str | None = None) -> bytes:
        import time

        self.synth_calls.append(text)
        if self._raise_on_synth:
            raise RuntimeError("Engine simulated synthesis failure")
        if self._delay_s > 0:
            time.sleep(self._delay_s)
        # Return 100 ms of dummy 16-bit PCM audio (zeros)
        samples = int(self._sample_rate * 0.1)
        return b"\x00\x00" * samples
