"""Player: stopping mid-answer must be quick and must happen on the thread that is writing."""

from __future__ import annotations

import sys
import threading
import time
import types
from typing import Any

import numpy as np
import pytest

SR = 24000


class FakeStream:
    """Plays at real speed, and fails the way PortAudio/ALSA does if abort comes from another
    thread while a write is in progress."""

    def __init__(self, **kw: Any) -> None:
        self.lock = threading.Lock()
        self.writing = False
        self.write_thread: int | None = None
        self.abort_thread: int | None = None
        self.corrupted = False
        self.written = 0
        self.aborted = False
        self.stop_threads: list[int] = []
        self.chunks: list[bytes] = []

    def start(self) -> None: ...

    def stop(self) -> None:
        self.stop_threads.append(threading.get_ident())
        if self.writing and self.stop_threads[-1] != self.write_thread:
            self.corrupted = True  # the real device would hang here

    def close(self) -> None: ...

    def write(self, data: bytes) -> bool:
        self.write_thread = threading.get_ident()
        self.writing = True
        time.sleep(len(data) / 2 / SR)
        self.written += len(data)
        self.chunks.append(data)
        self.writing = False
        return False  # no underflow

    def abort(self) -> None:
        self.abort_thread = threading.get_ident()
        if self.writing and self.abort_thread != self.write_thread:
            self.corrupted = True  # the real device would hang here
        self.aborted = True


@pytest.fixture
def stream(monkeypatch: pytest.MonkeyPatch) -> FakeStream:
    made: list[FakeStream] = []

    def factory(**kw: Any) -> FakeStream:
        made.append(FakeStream(**kw))
        return made[0]

    mod = types.ModuleType("sounddevice")
    mod.RawOutputStream = factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    return made  # type: ignore[return-value]


def test_close_plays_everything_that_was_queued(stream: Any) -> None:
    from doc_chat.playback import Player

    p = Player(SR)
    p.push(b"\x00\x00" * (SR // 5))  # 0.2 s
    p.close()
    assert stream[0].written == (SR // 5) * 2 and not stream[0].corrupted


def test_abort_is_quick_and_never_touches_the_stream_from_another_thread(stream: Any) -> None:
    from doc_chat.playback import Player

    p = Player(SR)
    for _ in range(10):
        p.push(b"\x00\x00" * (SR * 2))  # 20 s of audio queued
    time.sleep(0.3)
    t0 = time.perf_counter()
    p.abort()
    p.close()
    elapsed = time.perf_counter() - t0
    s = stream[0]
    assert elapsed < 0.3, f"took {elapsed:.2f} s to go quiet"
    assert not s.corrupted, "the stream was stopped from a thread other than the writer"
    assert s.stop_threads and s.stop_threads[0] == s.write_thread
    assert s.written < SR * 2 * 2 * 2  # well under the first two seconds were played


def test_a_cut_off_ends_with_a_fade_not_a_step(stream: Any) -> None:
    from doc_chat.playback import FADE_S, Player

    p = Player(SR)
    tone = (np.sin(np.arange(SR * 2) * 0.2) * 0.8 * 32767).astype(np.int16).tobytes()
    p.push(tone)
    time.sleep(0.2)
    p.abort()
    p.close()
    chunks = stream[0].chunks
    ramp = np.frombuffer(chunks[-1], dtype=np.int16)
    before = np.frombuffer(chunks[-2], dtype=np.int16)
    assert len(ramp) == int(SR * FADE_S)
    assert abs(int(ramp[0]) - int(before[-1])) < 3000  # it continues from where the tone was
    assert abs(int(ramp[-1])) < 700 and np.abs(np.diff(ramp.astype(int))).max() < 4000  # to ~zero


def test_underruns_are_counted(stream: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from doc_chat.playback import Player

    p = Player(SR)
    flags = iter([False, True, True, False, True])
    monkeypatch.setattr(stream[0], "write", lambda data: next(flags, False))
    p.push(b"\x00\x00" * (SR // 20) * 5)  # five slices
    p.close()
    assert p.underruns == 3


def test_audio_pushed_after_an_abort_is_not_played(stream: Any) -> None:
    from doc_chat.playback import Player

    p = Player(SR)
    p.push(b"\x00\x00" * (SR // 2))
    time.sleep(0.1)
    p.abort()
    written = stream[0].written
    p.push(b"\x00\x00" * SR)  # the synthesiser has not noticed yet
    p.close()
    assert stream[0].written <= written + SR // 10 * 2  # at most the slice in flight


def test_the_player_reports_how_loud_it_is_playing(stream: Any) -> None:
    from doc_chat.playback import Player

    p = Player(SR)
    assert p.level_recent() == 0.0
    loud = (np.sin(np.arange(SR // 5) * 0.3) * 0.5 * 32767).astype(np.int16).tobytes()
    p.push(loud)
    time.sleep(0.1)
    assert 0.2 < p.level_recent() < 0.5  # RMS of a 0.5-amplitude sine is about 0.35
    p.close()
    time.sleep(0.4)
    assert p.level_recent() == 0.0  # silent again once nothing was written for a while


def test_close_waits_for_a_reply_longer_than_two_seconds(stream: Any) -> None:
    # the caller starts listening when close returns: it must not return with seconds still playing
    from doc_chat.playback import Player

    p = Player(SR)
    n = int(SR * 2.4)
    p.push(b"\x00\x00" * n)  # 2.4 s, played at real speed by the fake device
    t0 = time.perf_counter()
    p.close()
    assert time.perf_counter() - t0 >= 2.3
    assert stream[0].written == n * 2
