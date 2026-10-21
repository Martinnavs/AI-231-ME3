"""Try talking over the assistant without a microphone: a scripted voice cuts in.

Needs a speech server (Parakeet) and a Gemini key (see scripts/e2e_ask_to_add.py for the server
command). Scenarios:

  cutoff   you pause mid-sentence for longer than the hold, then carry on while it is thinking:
           the cut-off turn is cancelled and the two parts are joined
  barge    you start a new question while it is answering: it stops and takes the new question

    uv run --group laptop python -W ignore scripts/e2e_barge_in.py cutoff
    uv run --group laptop python -W ignore scripts/e2e_barge_in.py barge

The run is recorded in out/sessions/e2e-<scenario>/ (read it with scripts/session_report.py).
"""

import asyncio
import sys
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, "src")
import time
import types

import doc_chat.speech as speech
from doc_chat.__main__ import Session, main
from lan_speech.engines import SupertonicTTSEngine

SR = 16000


def _silent_speakers() -> types.ModuleType:
    """A speaker that plays at real speed and makes no sound, so there is time to cut in."""
    mod = types.ModuleType("sounddevice")

    class RawOutputStream:
        def __init__(self, samplerate: int, **kw: object) -> None:
            self.rate = samplerate
            self.aborted = False

        def start(self) -> None: ...

        def stop(self) -> None: ...

        def close(self) -> None: ...

        def abort(self) -> None:
            self.aborted = True

        def write(self, data: bytes) -> None:
            end = time.perf_counter() + len(data) / 2 / self.rate
            while time.perf_counter() < end and not self.aborted:
                time.sleep(0.005)
            if self.aborted:
                raise RuntimeError("aborted")

    mod.RawOutputStream = RawOutputStream  # type: ignore[attr-defined]
    return mod


sys.modules["sounddevice"] = _silent_speakers()
scenario = sys.argv[1]
tts = SupertonicTTSEngine(Path("models/supertonic-3"), total_steps=4, intra_op_threads=2)
rng = np.random.default_rng(0)


def say(text: str) -> np.ndarray:
    x = np.frombuffer(tts.synthesize_chunk(text), np.int16).astype(np.float32) / 32768
    g = gcd(SR, tts.sample_rate)
    return (resample_poly(x, SR // g, tts.sample_rate // g) * 32767).astype(np.int16)


def quiet(seconds: float) -> np.ndarray:
    return (rng.normal(0, 0.002, int(seconds * SR)) * 32767).astype(np.int16)


state: dict[str, asyncio.Event] = {}

if scenario == "cutoff":
    FIRST = ("Tell me what Harry thinks about", "his friend Ron.")
else:
    FIRST = ("Who is Hermione Granger?", "Actually, who is Ron Weasley?")


async def feeder(hub: speech.MicHub) -> None:
    async def until(event: asyncio.Event) -> None:
        while not event.is_set():  # a real microphone never stops: keep sending room noise
            await play(quiet(0.1))

    async def play(audio: np.ndarray) -> None:
        for i in range(0, len(audio), SR // 10):
            hub._queue.put_nowait(audio[i : i + SR // 10].tobytes())
            await asyncio.sleep(0.1)

    await play(np.concatenate([quiet(1.4), say(FIRST[0])]))
    if scenario == "cutoff":
        await play(np.concatenate([quiet(0.85), say(FIRST[1]), quiet(0.2)]))
    else:
        await until(state["answer_started"])
        await play(np.concatenate([quiet(0.3), say(FIRST[1])]))
    await until(state["finished"])  # the last answer is done
    await play(np.concatenate([quiet(0.5), say("quit")]))
    while True:  # the microphone keeps running
        await play(quiet(1.0))


def patched_start(self: speech.MicHub) -> None:
    state["answer_started"] = asyncio.Event()
    state["finished"] = asyncio.Event()
    self._task = asyncio.get_running_loop().create_task(feeder(self))


speech.MicHub.start = patched_start  # type: ignore[method-assign]
speech.MicHub.stop = lambda self: self._task.cancel()  # type: ignore[method-assign]

original_chunk = Session._chunk
original_turn = Session.turn


def chunk(self: Session, player: object):  # type: ignore[no-untyped-def]
    inner = original_chunk(self, player)

    def push(pcm: bytes) -> None:
        inner(pcm)
        state["answer_started"].set()

    return push


async def turn(self: Session, *a, **k):  # type: ignore[no-untyped-def]
    await original_turn(self, *a, **k)
    if not (scenario == "barge" and len(self.records) < 1):
        state["finished"].set()


Session._chunk = chunk  # type: ignore[method-assign]
Session.turn = turn  # type: ignore[method-assign]

sys.exit(
    main(
        [
            "--mic",
            "--play",
            "--server",
            "ws://127.0.0.1:8767",
            "--docs",
            "corpus/harry-potter",
            "--llm",
            "gemini",
            "--stream",
            "--record",
            f"out/sessions/e2e-{scenario}",
            "--vad-hold",
            "0.6",
        ]
    )
)
