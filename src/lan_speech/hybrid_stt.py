"""Hybrid STT: Kroko streams and finds the end of the turn, Parakeet transcribes the finished turn.

Kroko is fast and streams partial text, but is the weaker recogniser (about 22% WER on agent
commands against Parakeet's 8%, 5% with hotwords). Parakeet is offline: it needs the whole turn.
So each stream does both:

* while the user speaks, interim transcripts are Kroko's, as before;
* when Kroko ends the turn (or the client sends ``finalize``), the buffered audio is
  re-decoded by Parakeet, biased toward a list of names, and the text goes through the entity
  repair ladder (``transcript_repair``).

The final transcript carries ``raw_text`` (Parakeet's words) and ``repair`` (what the ladder did
or wants to ask). Measurements and caveats: docs/STT-HOSTING.md.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from lan_speech.engines import SherpaSTTEngine, STTDecodeResult, STTStream
from lan_speech.hotwords import hotword_names, write_hotwords, write_vocab
from lan_speech.models import PARAKEET_DIR
from lan_speech.pause import NoiseFloor, PauseDetector
from lan_speech.transcript_repair import (
    Entity,
    EntityMatcher,
    RepairConfig,
    Risk,
    build_matchers,
    infer_context,
    repair_text,
    repair_to_dict,
)

logger = logging.getLogger(__name__)

# (samples as float32 in [-1, 1], sample rate) -> text
AudioTranscriber = Callable[[np.ndarray, int], str]


def trim_silence(samples: np.ndarray, sample_rate: int, lead_s: float, tail_s: float) -> np.ndarray:
    """Cut silence around the speech, keeping a margin: Parakeet's time grows with length."""
    frame = max(int(0.02 * sample_rate), 1)
    n = len(samples) // frame
    if n < 2:
        return samples
    rms = np.sqrt(np.mean(samples[: n * frame].reshape(n, frame) ** 2, axis=1))
    threshold = max(0.012, 2.5 * float(np.percentile(rms, 10)))
    voiced = np.flatnonzero(rms > threshold)
    if voiced.size == 0:
        return samples
    start = max(int(voiced[0] * frame - lead_s * sample_rate), 0)
    end = min(int((voiced[-1] + 1) * frame + tail_s * sample_rate), len(samples))
    return samples[start:end]


def pad_with_zeros(
    samples: np.ndarray, sample_rate: int, lead_s: float, tail_s: float
) -> np.ndarray:
    """Digital silence around the speech. Parakeet's words depend on it: on the labelled clips,
    trimming without it raised WER from 4.9% to 5.6% (hard set) and 8.7% to 11.6% (held-out)."""
    lead = np.zeros(int(lead_s * sample_rate), dtype=samples.dtype)
    tail = np.zeros(int(tail_s * sample_rate), dtype=samples.dtype)
    return np.concatenate([lead, samples, tail])


class ParakeetTranscriber:
    """Parakeet-TDT 0.6B (sherpa-onnx, int8) with optional hotwords; one shared recogniser."""

    def __init__(
        self,
        model_dir: Path,
        names: list[str],
        threads: int = 2,
        hotword_score: float = 1.0,
        max_active_paths: int = 4,
    ) -> None:
        import sherpa_onnx

        self._tmp = tempfile.TemporaryDirectory(prefix="lan_speech_hotwords_")
        extra: dict[str, Any] = {"decoding_method": "greedy_search"}
        if names:
            tmp = Path(self._tmp.name)
            write_vocab(model_dir / "tokens.txt", tmp / "parakeet.vocab")
            write_hotwords(names, hotword_score, tmp / "hotwords.txt")
            extra = {
                "decoding_method": "modified_beam_search",
                "max_active_paths": max_active_paths,
                "hotwords_file": str(tmp / "hotwords.txt"),
                "hotwords_score": hotword_score,
                "modeling_unit": "bpe",
                "bpe_vocab": str(tmp / "parakeet.vocab"),
            }
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_dir / "encoder.int8.onnx"),
            decoder=str(model_dir / "decoder.int8.onnx"),
            joiner=str(model_dir / "joiner.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=threads,
            sample_rate=16000,
            feature_dim=80,
            model_type="nemo_transducer",
            **extra,
        )
        self._lock = threading.Lock()
        self(np.zeros(8000, dtype=np.float32), 16000)  # warm-up

    def __call__(self, samples: np.ndarray, sample_rate: int) -> str:
        with self._lock:
            stream = self._rec.create_stream()
            stream.accept_waveform(sample_rate, samples)
            self._rec.decode_stream(stream)
            return str(stream.result.text).strip()


@dataclass
class _Speculation:
    """A Parakeet decode started at a pause, before the turn is known to be over."""

    future: Future[str] = field(default_factory=Future)
    cancelled: bool = False


class HybridSTTStream:
    """One user turn: forwards audio to the streaming stream and buffers it for Parakeet.

    With ``early_s`` set, the stream also watches the audio for a pause that long and starts the
    Parakeet decode right then, in the background. If the speaker carries on, the result is thrown
    away; if the turn ends instead, the answer is already (partly) computed.
    """

    def __init__(
        self,
        base: STTStream,
        transcribe: AudioTranscriber,
        matchers: dict[str, EntityMatcher],
        cfg: RepairConfig | None = None,
        *,
        lead_s: float = 0.3,
        tail_s: float = 0.4,
        pad_lead_s: float = 0.5,
        pad_tail_s: float = 1.0,
        max_seconds: float = 25.0,
        early_s: float | None = None,
        executor: Executor | None = None,
        noise: NoiseFloor | None = None,
    ) -> None:
        self._base = base
        self._transcribe = transcribe
        self._matchers = matchers
        self._cfg = cfg
        self._lead_s = lead_s
        self._tail_s = tail_s
        self._pad_lead_s = pad_lead_s
        self._pad_tail_s = pad_tail_s
        self._max_bytes = int(max_seconds * 16000) * 2
        self._pcm = bytearray()
        self._sample_rate = 16000
        self._slot: str | None = None
        self._risk: Risk | None = None
        self._noise = noise
        self._executor = executor
        self._spec: _Speculation | None = None
        self._pause: PauseDetector | None = None
        if early_s is not None and executor is not None:
            self._pause = PauseDetector(early_s, noise=noise.value if noise else None)

    def set_context(self, slot: str | None, risk: str | None) -> None:
        """What the agent expects to hear (contact, music, ...) and how risky a mistake is."""
        self._slot = slot
        self._risk = Risk(risk) if risk in ("low", "high") else None

    def accept_waveform(self, sample_rate: int, pcm_bytes: bytes) -> None:
        self._sample_rate = sample_rate
        self._pcm += pcm_bytes
        if len(self._pcm) > self._max_bytes:
            del self._pcm[: len(self._pcm) - self._max_bytes]
        self._base.accept_waveform(sample_rate, pcm_bytes)
        if self._pause is not None:
            self._watch(pcm_bytes, sample_rate)

    def _watch(self, pcm_bytes: bytes, sample_rate: int) -> None:
        """Look for the first short pause; a start of new speech cancels a decode begun at one."""
        assert self._pause is not None and self._executor is not None
        block = max(sample_rate // 10, 1) * 2  # 100 ms of 16-bit samples
        for i in range(0, len(pcm_bytes), block):
            part = np.frombuffer(pcm_bytes[i : i + block], dtype=np.int16)
            if part.size == 0:
                continue
            level = float(np.sqrt(np.mean((part.astype(np.float32) / 32768.0) ** 2)))
            voiced, pause = self._pause.feed(level, part.size / sample_rate)
            if self._spec is not None and (voiced or self._pause.above_floor(level)):
                # they kept talking, or the pause fell inside the soft end of a word: no use
                self._spec.cancelled = True
                self._spec = None
            if pause and self._spec is None:
                self._spec = self._start_speculation(bytes(self._pcm), sample_rate)

    def _start_speculation(self, audio: bytes, sample_rate: int) -> _Speculation:
        assert self._executor is not None
        spec = _Speculation()

        def run() -> str:
            if spec.cancelled:
                return ""
            return self._decode_audio(audio, sample_rate)

        spec.future = self._executor.submit(run)
        return spec

    def decode(self) -> STTDecodeResult | None:
        res = self._base.decode()
        if res is None or not res.is_final:
            return res
        return self._finish(res.text)

    def finalize(self) -> str:
        return self.finalize_result().text

    def finalize_result(self) -> STTDecodeResult:
        return self._finish(self._base.finalize())

    def reset(self) -> None:
        self._base.reset()
        self._pcm.clear()
        self._drop_speculation()

    def _drop_speculation(self) -> None:
        if self._spec is not None:
            self._spec.cancelled = True
            self._spec = None

    def _decode_audio(self, audio: bytes, sample_rate: int) -> str:
        """Trim, pad and transcribe a buffered turn; "" if Parakeet fails."""
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        samples = trim_silence(samples, sample_rate, self._lead_s, self._tail_s)
        samples = pad_with_zeros(samples, sample_rate, self._pad_lead_s, self._pad_tail_s)
        try:
            return self._transcribe(samples, sample_rate)
        except Exception:
            logger.exception("Parakeet failed; using the streaming transcript")
            return ""

    def _finish(self, streaming_text: str) -> STTDecodeResult:
        audio = bytes(self._pcm)
        self._pcm.clear()
        spec, self._spec = self._spec, None
        if self._pause is not None and self._noise is not None and self._pause.measured:
            self._noise.value = self._pause.noise
        if not streaming_text.strip():
            if spec is not None:
                spec.cancelled = True
            return STTDecodeResult(text="", is_final=True, end_of_turn=True)
        # a decode started at the pause is only used if nobody spoke after it (see _watch)
        raw = spec.future.result() if spec is not None else ""
        if not raw:  # no early result (none started, or it failed): decode the whole turn now
            raw = self._decode_audio(audio, self._sample_rate)
        if not raw:
            return STTDecodeResult(text=streaming_text, is_final=True, end_of_turn=True)

        slot = self._slot
        risk = self._risk
        if slot is None:
            slot, inferred = infer_context(raw)
            risk = risk or inferred
        risk = risk or Risk.LOW
        rep = repair_text(raw, self._matchers, slot, risk, self._cfg)
        if rep is None:
            return STTDecodeResult(text=raw, is_final=True, end_of_turn=True, raw_text=raw)
        return STTDecodeResult(
            text=rep.text,
            is_final=True,
            end_of_turn=True,
            raw_text=raw,
            repair=repair_to_dict(rep, slot, risk),
        )


class ParakeetHybridSTTEngine:
    """Kroko for streaming and turn ends, Parakeet with hotwords and entity repair for finals."""

    def __init__(
        self,
        models_dir: Path | str,
        entities: list[Entity],
        *,
        kroko_threads: int = 2,
        parakeet_threads: int = 2,
        hotword_score: float = 1.0,
        endpoint_silence: float = 1.2,
        pad_lead_s: float = 0.5,
        pad_tail_s: float = 1.0,
        early_decode_s: float | None = None,
        repair: bool = True,
        repair_cfg: RepairConfig | None = None,
        repair_only: frozenset[str] = frozenset(),
    ) -> None:
        models = Path(models_dir)
        self._kroko = SherpaSTTEngine(
            models,
            threads=kroko_threads,
            model="kroko",
            rule2_trailing_silence=endpoint_silence,
        )
        self._transcribe = ParakeetTranscriber(
            models / PARAKEET_DIR,
            hotword_names(entities, repair_only),
            threads=parakeet_threads,
            hotword_score=hotword_score,
        )
        self._cfg = repair_cfg
        self._pads = (pad_lead_s, pad_tail_s)
        self._early = early_decode_s
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parakeet-early")
        self._noise = NoiseFloor()
        self._matchers: dict[str, EntityMatcher] = {}
        if repair and entities:
            self._matchers = build_matchers(entities, _known_words())

    @property
    def name(self) -> str:
        return "kroko+parakeet"

    def create_stream(self, sample_rate: int = 16000) -> STTStream:
        return HybridSTTStream(
            self._kroko.create_stream(sample_rate),
            self._transcribe,
            self._matchers,
            self._cfg,
            pad_lead_s=self._pads[0],
            pad_tail_s=self._pads[1],
            early_s=self._early,
            executor=self._executor,
            noise=self._noise,
        )


def _known_words() -> frozenset[str]:
    """English dictionary used to keep ordinary words from being 'repaired' into names."""
    try:
        import cmudict
    except ImportError:
        logger.warning("cmudict not installed: repair has only a short built-in word list")
        return frozenset()
    return frozenset(cmudict.words())
