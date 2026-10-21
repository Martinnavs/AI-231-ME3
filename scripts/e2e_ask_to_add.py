"""Try --ask-to-add without a microphone: each listen call is fed the next synthesised clip.

Needs a speech server (Parakeet) and a Gemini key. Start the server, then:

    uv run --group laptop python -W ignore -m lan_speech --stt-model parakeet \\
        --insecure-loopback --port 8767 --entities config/entities-docs.json
    FIRST="Tell me about the Order of the" SECOND="Phoenix." \\
        uv run --group laptop python -W ignore scripts/e2e_ask_to_add.py continue

Scenarios: continue (answers the prompt), silence (no answer), decline ("No, that's all."),
finished (a complete question, no prompt). Prints the turn and writes out/doc_chat/e2e-<name>.jsonl.
"""

import asyncio
import os
import sys
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, "src")
from doc_chat.__main__ import main
from doc_chat.speech import DocChatSpeechClient
from lan_speech.engines import SupertonicTTSEngine

tts = SupertonicTTSEngine(Path("models/supertonic-3"), total_steps=4, intra_op_threads=2)


def say(t):
    x = np.frombuffer(tts.synthesize_chunk(t), np.int16).astype(np.float32) / 32768
    g = gcd(16000, tts.sample_rate)
    return (resample_poly(x, 16000 // g, tts.sample_rate // g) * 32767).astype(np.int16)


scenario = sys.argv[1]
FIRST = os.environ.get("FIRST", "Tell me what Dumbledore said to")
SECOND = os.environ.get("SECOND", "Harry about the prophecy.")
SCRIPTS = {
    # (clip text or None for silence) per listen_turn call
    "continue": [FIRST, SECOND, "quit"],
    "silence": [FIRST, None, "quit"],
    "decline": [FIRST, "No, that's all.", "quit"],
    "finished": ["Who is Hermione Granger?", "quit"],
}[scenario]
calls = iter(SCRIPTS)
rng = np.random.default_rng(0)


def noise(n):
    return (rng.normal(0, 0.002, n) * 32767).astype(np.int16)


async def fake_mic(self, rate, block_ms):
    text = next(calls)
    audio = (
        noise(int(1.2 * rate))
        if text is None
        else np.concatenate([noise(int(1.2 * rate)), say(text), noise(int(0.5 * rate))])
    )
    step = rate * block_ms // 1000
    for i in range(0, len(audio), step):
        yield audio[i : i + step].tobytes()
        await asyncio.sleep(0.02)
    while True:  # the microphone keeps running, quiet
        yield noise(step).tobytes()
        await asyncio.sleep(0.1)


DocChatSpeechClient._mic_chunks = fake_mic
sys.exit(
    main(
        [
            "--mic",
            "--server",
            "ws://127.0.0.1:8767",
            "--docs",
            "corpus/harry-potter",
            "--llm",
            "gemini",
            "--stream",
            "--ask-to-add",
            "--log",
            f"out/doc_chat/e2e-{scenario}.jsonl",
            "--vad-hold",
            "0.6",
        ]
    )
)
