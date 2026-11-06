"""Y3: music is a second reference, summed with the reply's per 10 ms; the canceller removes it."""

from typing import Any

import numpy as np
from scipy.signal import fftconvolve

from doc_chat.aec import FRAME_BYTES, RATE, EchoCanceller


class FakeApm:
    def __init__(self) -> None:
        self.rendered: list[np.ndarray] = []

    def process_reverse_stream(self, frame: Any) -> None:
        self.rendered.append(np.frombuffer(bytes(frame.data), dtype=np.int16).copy())

    def process_stream(self, frame: Any) -> None:
        return None


def pcm(value: int) -> bytes:
    return np.full(160, value, dtype=np.int16).tobytes()


def make() -> tuple[EchoCanceller, FakeApm]:
    canceller = EchoCanceller()
    apm = FakeApm()
    canceller._apm = apm  # type: ignore[assignment]
    return canceller, apm


def feed(canceller: EchoCanceller, n: int = 1) -> None:
    for _ in range(n):
        canceller.process(bytes(FRAME_BYTES))


def test_music_and_a_reply_are_summed_in_the_same_frame() -> None:
    canceller, apm = make()
    canceller.reference(pcm(1000))
    canceller.reference(pcm(300), music=True)
    feed(canceller)
    assert int(apm.rendered[0][0]) == 1300


def test_music_alone_is_the_reference_and_does_not_wait_behind_a_reply() -> None:
    canceller, apm = make()
    canceller.reference(pcm(300), music=True)
    canceller.reference(pcm(300), music=True)
    feed(canceller, 2)
    assert [int(r[0]) for r in apm.rendered] == [300, 300]
    canceller.reference(pcm(1000))  # a reply starts while the music goes on
    canceller.reference(pcm(300), music=True)
    feed(canceller)
    assert int(apm.rendered[2][0]) == 1300  # not 1000 then 300


def test_each_source_has_its_own_gain() -> None:
    canceller, apm = make()
    canceller.gain, canceller.music_gain = 0.5, 0.25
    canceller.reference(pcm(1000))
    canceller.reference(pcm(400), music=True)
    feed(canceller)
    assert int(apm.rendered[0][0]) == 600
    canceller.reference(pcm(400), music=True)
    feed(canceller)
    assert int(apm.rendered[1][0]) == 100  # music alone at its gain


def test_clear_music_forgets_the_music_but_not_the_reply() -> None:
    canceller, apm = make()
    canceller.reference(pcm(1000))
    canceller.reference(pcm(300), music=True)
    canceller.clear_music()
    feed(canceller)
    assert int(apm.rendered[0][0]) == 1000
    canceller.reference(pcm(5), music=True)
    canceller.clear()  # the reply's side only
    feed(canceller)
    assert int(apm.rendered[1][0]) == 5


def _music_like(seconds: float, seed: int = 1) -> np.ndarray:
    """Broadband sound with a beat: steady enough for the canceller to learn."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    noise = fftconvolve(rng.normal(0, 1, len(t)), np.hanning(24), "same")
    tones = sum(np.sin(2 * np.pi * f * t) for f in (220, 330, 440, 660, 880, 1320))
    beat = 0.6 + 0.4 * np.sin(2 * np.pi * 2 * t) ** 2
    x = (noise * 0.7 + tones * 0.3) * beat
    return (x / np.max(np.abs(x)) * 9000).astype(np.int16).astype(np.float64)


def test_the_canceller_takes_the_music_out_of_the_microphone() -> None:
    """Music through the cancelled path: a room echo (delay 120 ms, a reflection) is cut by at
    least 15 dB once converged. A real room and speaker differ (docs/AEC-REMOTE-SPEAKER.md)."""
    ref = _music_like(8.0)
    h = np.zeros(int(0.3 * RATE))
    h[int(0.12 * RATE)], h[int(0.124 * RATE)] = 0.3, -0.1
    rng = np.random.default_rng(0)
    echo = fftconvolve(ref, h)[: len(ref)]
    mic = np.clip(echo + rng.normal(0, 30, len(echo)), -32768, 32767).astype(np.int16)
    canceller = EchoCanceller()
    pushed, out = 0, []
    for i in range(0, len(mic) - 1599, 1600):
        while pushed < min(i + 4000, len(ref)):  # the player stays about 0.25 s ahead
            canceller.reference(ref[pushed : pushed + 800].astype(np.int16).tobytes(), music=True)
            pushed += 800
        out.append(canceller.process(mic[i : i + 1600].tobytes()))
    cleaned = np.frombuffer(b"".join(out), dtype=np.int16).astype(np.float64)
    tail = slice(5 * RATE, 8 * RATE - 1600)

    def rms(x: np.ndarray) -> float:
        return float(np.sqrt(np.mean(x**2)))

    erle = 20 * np.log10(rms(mic[tail].astype(np.float64)) / max(rms(cleaned[tail]), 1e-9))
    assert erle >= 15.0, f"ERLE {erle:.1f} dB"
