"""Live engine integration test (P7).

Marked `live`: left for the human runner with real model files and VOICE_AGENT_LIVE=1.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from audio_engine import AudioClip
from audio_engine.wav import resample


def word_edits(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def normalize(text: str) -> list[str]:
    return re.sub(r"[^a-z' ]+", " ", text.lower()).split()


@pytest.mark.live
def test_live_engines_roundtrip() -> None:
    """Supertonic synthesizes 'turn on the lights', sherpa transcribes it, WER <= 0.2."""
    models_dir = Path("models")
    if not (models_dir / "supertonic-3").is_dir() or not (models_dir / "kroko-en").is_dir():
        pytest.skip("Model files missing in models/")

    from lan_speech.engines import SherpaSTTEngine, SupertonicTTSEngine

    target_text = "turn on the lights"

    # 1. Synthesize audio with Supertonic
    tts = SupertonicTTSEngine(
        model_dir=models_dir / "supertonic-3",
        total_steps=4,
        intra_op_threads=2,
        default_voice="F1",
    )
    pcm_bytes = tts.synthesize_chunk(target_text)
    assert len(pcm_bytes) > 0

    # 2. Resample from TTS sample rate (44100) to STT sample rate (16000)
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    clip = AudioClip(samples=samples, sample_rate=tts.sample_rate)
    resampled = resample(clip, target_sr=16000)
    pcm16k = (np.clip(resampled.samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()

    # 3. Transcribe with Sherpa
    stt = SherpaSTTEngine(models_dir=models_dir, threads=2, int8=True)
    stream = stt.create_stream(sample_rate=16000)

    # Feed in 100 ms chunks
    chunk_size = 3200  # 1600 samples * 2 bytes
    for i in range(0, len(pcm16k), chunk_size):
        stream.accept_waveform(16000, pcm16k[i : i + chunk_size])
        stream.decode()

    transcript = stream.finalize()
    ref_words = normalize(target_text)
    hyp_words = normalize(transcript)
    edits = word_edits(ref_words, hyp_words)
    wer = edits / max(len(ref_words), 1)

    assert wer <= 0.2, f"WER {wer:.2f} > 0.2 (ref='{target_text}', hyp='{transcript}')"
