"""STT and TTS engine interfaces and implementations for LAN speech server (ticket 21)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from lan_speech.protocol import VoiceItem
from lan_speech.speakable import speakable

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

        text = speakable(text)
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


# Supertonic F1 at 4 steps, the voice the echo gate and ducking were tuned on, speaks at -21.3 dBFS
# active RMS (mean of 39 real sentences, sd 1.1 dB). Kitten Micro is 4.4 dB louder (-16.9 dBFS) with
# peaks near 0 dBFS, so its echo sat above that tuning: bring every Kitten chunk to the same level.
KITTEN_TARGET_RMS_DBFS = -21.3
KITTEN_GAIN_LIMITS_DB = (-9.0, 3.0)
_ACTIVE_THRESHOLD = 0.01


def match_level(wav: Any, target_db: float = KITTEN_TARGET_RMS_DBFS) -> Any:
    """Scale a float waveform so its active speech (samples above 0.01) has RMS `target_db` dBFS."""
    import numpy as np

    active = wav[np.abs(wav) > _ACTIVE_THRESHOLD]
    if active.size < 100:
        return wav
    level_db = 20.0 * np.log10(float(np.sqrt(np.mean(active**2))) + 1e-9)
    low, high = KITTEN_GAIN_LIMITS_DB
    scaled = wav * 10.0 ** (float(np.clip(target_db - level_db, low, high)) / 20.0)
    peak = float(np.max(np.abs(scaled)))
    return scaled * (0.95 / peak) if peak > 0.95 else scaled


_KITTEN_YEAR = re.compile(
    r"(?<![\d$,.])(1[1-9]\d\d|20\d\d)(?!\d|,\d|\.\d|\s?(?:%|percent|degrees|km|kilometers|meters|minutes|kg))"
)
_KITTEN_AMPM = re.compile(r"\b([AP])\.?M\b")
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_KITTEN_DATE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?![\d:])")
_KITTEN_RANGE = re.compile(
    r"(?<=\d)\s?\u2013\s?(?=\d)"
)  # a score or range: "3\u20133", "22\u201325"
_KITTEN_BIG = re.compile(r"(?<![\w:.,$])(\$?)(\d{1,3}(?:,\d{3})+|\d{4,})(\.\d+)?(?![\w:]|,\d|\.\d)")
_SCALES = re.compile(r"\b(thousand|million) (?=\w)")


# Words Kitten mispronounces -> a spelling it speaks correctly, picked by round trip through
# Parakeet without hotwords (2026-11-04): EDSA was heard as "Adiasur"/"Andisa", Duterte as "Dudert".
KITTEN_RESPELLINGS = {"EDSA": "Ed sah", "Duterte": "Du-tair-tay"}
_KITTEN_RESPELL = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in KITTEN_RESPELLINGS) + r")\b", re.IGNORECASE
)
_KITTEN_RESPELL_BY_LOWER = {w.lower(): r for w, r in KITTEN_RESPELLINGS.items()}


def big_number_words(match: re.Match[str], say_number: Callable[[int], str]) -> str:
    """Words for a number of 1,000 or more, in the form Kitten reads back correctly (round trip
    through Parakeet, 2026-11-04): a comma after each scale word, a fraction rounded away
    ("eight thousand, eight hundred forty-nine" for 8,848.86), and billions as "one point four
    billion" (spelled out, "one billion four hundred million" is spoken as a trillion), and "and"
    instead of the comma when only 1-99 follows the thousands (29,004: 7 of 200 wrong -> 0)."""
    value = float(match[2].replace(",", "") + (match[3] or ""))
    unit = " dollars" if match[1] else ""
    if value >= 1e9:
        billions = round(value / 1e9, 2)
        tenths = f"{billions:.2f}".split(".")[1].rstrip("0")
        point = " point " + " ".join(say_number(int(d)) for d in tenths) if tenths else ""
        return f"{say_number(int(billions))}{point} billion{unit}"
    whole = round(value)
    words = say_number(whole).replace(" and ", " ")
    if whole < 1_000_000 and 0 < whole % 1000 < 100:  # "thousand, four" is heard as "thousand 4"
        return _SCALES.sub(r"\1 and ", words) + unit
    return _SCALES.sub(r"\1, ", words) + unit


def kitten_text(
    text: str,
    cleaner: Any,
    year_words: Callable[[int], str],
    say_number: Callable[[int], str] | None = None,
    say_ordinal: Callable[[int], str] | None = None,
) -> str:
    """Prepare reply text for Kitten.

    Its default cleaner lowercases everything, drops commas and periods, and turns "person's"
    into "person s" (read "person ess"). Keep case, apostrophes and punctuation instead; say years
    as years ("nineteen thirty-three"), AM/PM as letters (its time expander turns them into the
    word "am"; "ay em" is spoken as "I M", "A M" as the letters), dates with an ordinal day, and
    big numbers in the form it reads back correctly (`big_number_words`), and respell the words in
    `KITTEN_RESPELLINGS`. The number steps need
    `say_number` / `say_ordinal` (num2words) and are skipped without them.
    """
    text = _KITTEN_RESPELL.sub(lambda m: _KITTEN_RESPELL_BY_LOWER[m[1].lower()], text)
    text = _KITTEN_RANGE.sub(" to ", text)
    if say_ordinal is not None:
        text = _KITTEN_DATE.sub(lambda m: f"{m[1]} {say_ordinal(int(m[2]))}", text)
    text = _KITTEN_YEAR.sub(lambda m: year_words(int(m[1])), text)
    text = _KITTEN_AMPM.sub(lambda m: "A M" if m[1] == "A" else "pee em", text)
    if say_number is not None:
        text = _KITTEN_BIG.sub(lambda m: big_number_words(m, say_number), text)
    text = re.sub(r"\bUSD\b", "dollars", text)
    out = str(cleaner(text))
    return re.sub(
        r"(\w)(A M|pee em)\b", r"\1 \2", out
    )  # the time expander eats the space before it


class KittenTTSEngine:
    """Kitten TTS 0.8 (StyleTTS2-derived, ONNX, 24 kHz).

    ``kittentts`` is not a project dependency: set ``KITTEN_SITE`` to a site-packages directory that
    holds it (the isolated bake-off environment), which is appended to ``sys.path``.
    """

    def __init__(
        self,
        repo: str = "KittenML/kitten-tts-micro-0.8",
        intra_op_threads: int = 2,
        default_voice: str = "Jasper",
    ) -> None:
        import os
        import sys

        import onnxruntime as ort

        site = os.environ.get("KITTEN_SITE")
        if site and site not in sys.path:
            sys.path.append(site)
        from kittentts import KittenTTS  # type: ignore[import-not-found,unused-ignore]
        from kittentts.preprocess import TextPreprocessor  # type: ignore[import-not-found,unused-ignore]
        from num2words import num2words  # type: ignore[import-not-found,unused-ignore]

        original = ort.InferenceSession

        class _Limited(original):  # type: ignore[misc, valid-type]
            def __init__(self, path: Any, sess_options: Any = None, *a: Any, **k: Any) -> None:
                options = sess_options or ort.SessionOptions()
                options.intra_op_num_threads = intra_op_threads
                options.inter_op_num_threads = 1
                super().__init__(path, sess_options=options, *a, **k)  # noqa: B026

        ort.InferenceSession = _Limited  # kittentts builds its own session: cap its threads
        try:
            self._model = KittenTTS(repo)
        finally:
            ort.InferenceSession = original
        self._repo = repo
        self._default_voice = default_voice
        self._cleaner = TextPreprocessor(
            lowercase=False, expand_contractions=False, remove_punctuation=False
        )
        self._year_words: Callable[[int], str] = lambda n: str(num2words(n, to="year")).replace(
            " and ", " "
        )
        self._say_number: Callable[[int], str] = lambda n: str(num2words(n))
        self._say_ordinal: Callable[[int], str] = lambda n: str(num2words(n, to="ordinal"))

    @property
    def name(self) -> str:
        return self._repo.rsplit("/", 1)[-1]

    @property
    def sample_rate(self) -> int:
        return 24000

    def _voices(self) -> list[str]:
        inner = self._model.model
        return list(inner.voice_aliases) or list(inner.available_voices)

    def list_voices(self) -> list[VoiceItem]:
        return [VoiceItem(id=v, name=v, language="en") for v in self._voices()]

    def synthesize_chunk(self, text: str, voice: str | None = None) -> bytes:
        import numpy as np

        # clients send their configured voice (Supertonic's "F1"): use ours when it is unknown
        inner = self._model.model
        known = {*inner.voice_aliases, *inner.available_voices}
        name = voice if voice in known else self._default_voice
        spoken = kitten_text(
            speakable(text), self._cleaner, self._year_words, self._say_number, self._say_ordinal
        )
        wav = np.asarray(inner.generate(spoken, voice=name, clean_text=False))
        clipped = match_level(np.clip(wav.squeeze(), -1.0, 1.0))
        voiced = np.flatnonzero(np.abs(clipped) > _TRIM_THRESHOLD)
        if voiced.size:
            margin = round(_TRIM_MARGIN_S * self.sample_rate)
            clipped = clipped[max(voiced[0] - margin, 0) : voiced[-1] + margin]
        return bytes((clipped * 32767.0).astype(np.int16).tobytes())


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
