"""Player: stopping mid-answer must be quick and must happen on the thread that is writing."""

from __future__ import annotations

import sys
import threading
import time
import types
from typing import Any

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

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...

    def write(self, data: bytes) -> None:
        self.write_thread = threading.get_ident()
        self.writing = True
        time.sleep(len(data) / 2 / SR)
        self.written += len(data)
        self.writing = False

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
    assert stream[0].written == (SR // 5) * 2 and not stream[0].aborted


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
    assert not s.corrupted, "abort() reached the device from a thread other than the writer"
    assert s.aborted and s.abort_thread == s.write_thread
    assert s.written < SR * 2 * 2 * 2  # well under the first two seconds were played


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
