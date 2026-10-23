import wave
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

from doc_chat.aec import FRAME_BYTES, RATE, EchoCanceller
from doc_chat.playback import RemotePlayer
from doc_chat.speech import MicHub

CLIPS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "pi_echo"


def _clip(name: str) -> np.ndarray:
    with wave.open(str(CLIPS / name), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)


def _db(x: np.ndarray) -> float:
    return 20 * float(np.log10(max(float(np.sqrt(np.mean((x / 32768.0) ** 2))), 1e-6)))


def _run(delay_s: float, gain: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Three replies back to back and their echo in the microphone (a short room, delayed)."""
    ref = np.concatenate([_clip(f"reply-0{i}.wav") for i in (1, 2, 3)])
    h = np.zeros(int(0.3 * RATE))
    h[int(delay_s * RATE)], h[int((delay_s + 0.004) * RATE)] = 0.4, -0.12
    rng = np.random.default_rng(0)
    echo = np.concatenate([fftconvolve(ref, h)[: len(ref)], np.zeros(RATE)])
    mic = np.clip(echo + rng.normal(0, 40, len(echo)), -32768, 32767).astype(np.int16)
    canceller = EchoCanceller()
    canceller.gain = gain
    sent = ref * gain
    pushed, out = 0, []
    for i in range(0, len(mic) - 1599, 1600):
        while pushed < min(i + 4000, len(sent)):  # the player stays about 0.25 s ahead
            canceller.reference(sent[pushed : pushed + 800].astype(np.int16).tobytes())
            pushed += 800
        out.append(canceller.process(mic[i : i + 1600].tobytes()))
    out_bytes = b"".join(out)
    return mic.astype(np.float64), np.frombuffer(out_bytes, dtype=np.int16).astype(np.float64)


def test_echo_is_much_quieter_after_a_few_seconds() -> None:
    mic, out = _run(delay_s=0.1)
    last = slice(-6 * RATE, -RATE)  # the third reply, after the canceller has settled
    assert _db(mic[last]) - _db(out[last]) >= 10.0


def test_without_reference_the_microphone_passes_through() -> None:
    canceller = EchoCanceller()
    block = (np.random.default_rng(1).normal(0, 3000, 1600)).astype(np.int16).tobytes()
    out = np.frombuffer(canceller.process(block), dtype=np.int16).astype(np.float64)
    assert len(out) == 1600
    assert abs(_db(out) - _db(np.frombuffer(block, dtype=np.int16).astype(np.float64))) < 3.0


def test_partial_frame_and_length_are_kept() -> None:
    canceller = EchoCanceller()
    block = bytes(FRAME_BYTES * 3 + 50)
    assert len(canceller.process(block)) == len(block)


def test_clear_drops_unused_reference() -> None:
    canceller = EchoCanceller()
    canceller.reference(bytes(FRAME_BYTES * 5 + 7))
    assert len(canceller._frames) == 5
    canceller.clear()
    assert len(canceller._frames) == 0 and canceller._pending == b""


class FakeSpeaker:
    rate = 48000

    def send(self, data: bytes | str) -> None:
        pass


class RecordingCanceller:
    def __init__(self) -> None:
        self.reference_bytes = 0
        self.cleared = False

    def reference(self, pcm: bytes) -> None:
        self.reference_bytes += len(pcm)

    def clear(self) -> None:
        self.cleared = True

    def process(self, block: bytes) -> bytes:
        return block[::-1]


def test_remote_player_hands_the_canceller_16k_audio() -> None:
    canceller = RecordingCanceller()
    player = RemotePlayer(FakeSpeaker(), 22050, canceller)  # type: ignore[arg-type]
    player.push(bytes(2 * 22050 // 5))  # 0.2 s of audio at the speech server's rate
    player.close()
    assert abs(canceller.reference_bytes - 2 * RATE // 5) <= 40  # 0.2 s at 16 kHz, give or take
    player.abort()
    assert canceller.cleared


async def test_mic_hub_passes_blocks_through_the_canceller() -> None:
    hub = MicHub(canceller=RecordingCanceller())
    hub._take(b"\x01\x02\x03\x04")
    assert await hub.get() == b"\x04\x03\x02\x01"
