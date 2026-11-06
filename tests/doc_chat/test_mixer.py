"""Y3: the Pi speaker's mixer: replies and music through one stream."""

import threading
import time
from typing import Any

import numpy as np

from doc_chat.mixer import IDLE_CLOSE_S, MUSIC_BUFFER_S, Mixer

RATE = 8000  # a small rate keeps the tests fast; a slice is 50 ms = 400 samples


def const(value: int, ms: int = 50) -> bytes:
    return np.full(RATE * ms // 1000, value, dtype=np.int16).tobytes()


def samples(data: bytes | None) -> np.ndarray:
    assert data is not None
    return np.frombuffer(data, dtype=np.int16)


class Sink:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.opened = 0
        self.closed = 0

    def factory(self, rate: int) -> tuple[Any, Any]:
        self.opened += 1
        return self.written.append, lambda: setattr(self, "closed", self.closed + 1)


def test_a_reply_and_music_are_added() -> None:
    mixer = Mixer(RATE, Sink().factory)
    mixer.tts_channel().push(const(1000))
    mixer.music_push(const(300))
    assert set(samples(mixer.step())) == {1300}


def test_music_alone_and_a_reply_alone() -> None:
    mixer = Mixer(RATE, Sink().factory)
    mixer.music_push(const(300))
    assert set(samples(mixer.step())) == {300}
    mixer.tts_channel().push(const(700))
    assert set(samples(mixer.step())) == {700}
    assert mixer.step() is None  # nothing left: nothing to write


def test_music_ducks_to_its_gain_without_a_step() -> None:
    mixer = Mixer(RATE, Sink().factory)
    mixer.music_push(const(8000, 200))
    mixer.step()
    mixer.music_set_gain(-15)
    out = samples(mixer.step())
    assert out[0] > 7000 and abs(int(out[-1]) - int(8000 * 10 ** (-15 / 20))) <= 2  # a ramp
    assert set(samples(mixer.step())) == {int(8000 * 10 ** (-15 / 20))}


def test_the_reply_gain_ducks_only_the_reply() -> None:
    mixer = Mixer(RATE, Sink().factory)
    channel = mixer.tts_channel()
    channel.push(const(1000, 150))
    mixer.music_push(const(400, 150))
    channel.set_gain(-6.0)
    mixer.step()
    assert abs(int(samples(mixer.step())[-1]) - (int(1000 * 10 ** (-6 / 20)) + 400)) <= 2


def test_abort_drops_the_reply_with_a_short_fade_and_the_music_goes_on() -> None:
    mixer = Mixer(RATE, Sink().factory)
    channel = mixer.tts_channel()
    channel.push(const(1000, 500))
    mixer.music_push(const(300, 500))
    mixer.step()
    channel.abort()
    out = samples(mixer.step())
    fade = int(RATE * 0.008)
    assert (
        out[0] == 1300 and out[fade // 2] < 1100 and set(out[fade + 2 :]) == {300}
    )  # a ramp, no click
    assert set(samples(mixer.step())) == {300}


def test_close_returns_when_the_reply_has_been_played() -> None:
    mixer = Mixer(RATE, Sink().factory)
    channel = mixer.tts_channel()
    channel.push(const(1000, 150))
    done = threading.Event()
    threading.Thread(target=lambda: (channel.close(), done.set()), daemon=True).start()
    time.sleep(0.1)
    assert not done.is_set()
    for _ in range(3):
        mixer.step()
    assert done.wait(1.0)


def test_close_after_abort_returns_at_once() -> None:
    mixer = Mixer(RATE, Sink().factory)
    channel = mixer.tts_channel()
    channel.push(const(1000, 1000))
    channel.abort()
    t0 = time.monotonic()
    channel.close()
    assert time.monotonic() - t0 < 0.3


def test_a_stale_reply_cannot_push_into_the_next_one() -> None:
    mixer = Mixer(RATE, Sink().factory)
    old = mixer.tts_channel()
    new = mixer.tts_channel()
    old.push(const(5))
    new.push(const(7))
    assert set(samples(mixer.step())) == {7}


def test_the_music_buffer_keeps_about_a_second_and_drops_the_oldest() -> None:
    mixer = Mixer(RATE, Sink().factory)
    mixer.music_push(const(1, 1000))
    mixer.music_push(const(2, 1000))
    assert len(mixer._music) <= int(RATE * MUSIC_BUFFER_S) * 2
    assert samples(mixer.step())[0] == 2  # the first second is gone


def test_flush_silences_the_music() -> None:
    mixer = Mixer(RATE, Sink().factory)
    mixer.music_push(const(300, 500))
    mixer.music_flush()
    assert mixer.step() is None


def test_a_short_music_tail_is_padded_with_silence() -> None:
    mixer = Mixer(RATE, Sink().factory)
    mixer.music_push(const(300, 30))
    out = samples(mixer.step())
    assert len(out) == RATE // 20 and out[0] == 300 and out[-1] == 0


def test_the_sum_is_clipped_not_wrapped() -> None:
    mixer = Mixer(RATE, Sink().factory)
    mixer.tts_channel().push(const(30000))
    mixer.music_push(const(30000))
    got = set(samples(mixer.step()))  # soft-limited near full scale, never wrapped negative
    assert len(got) == 1 and 32700 <= got.pop() <= 32767


def test_the_output_opens_with_the_first_audio_and_closes_after_a_quiet_second() -> None:
    sink = Sink()
    mixer = Mixer(RATE, sink.factory)
    assert not mixer.run_once(0.0) and sink.opened == 0  # nothing to play: the card stays free
    mixer.music_push(const(300, 100))
    assert mixer.run_once(1.0) and mixer.run_once(1.05) and sink.opened == 1
    assert not mixer.run_once(1.1)
    assert not mixer.run_once(1.1 + IDLE_CLOSE_S / 2) and sink.closed == 0
    assert not mixer.run_once(1.1 + IDLE_CLOSE_S + 0.01) and sink.closed == 1
    mixer.music_push(const(300, 50))
    assert mixer.run_once(5.0) and sink.opened == 2


def test_the_thread_writes_in_the_background_and_stops() -> None:
    sink = Sink()
    mixer = Mixer(RATE, sink.factory)
    mixer.start()
    mixer.music_push(const(300, 100))
    deadline = time.monotonic() + 2
    while len(sink.written) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    mixer.stop()
    assert len(sink.written) >= 2 and sink.closed == 1
