"""The laptop music player (Y3): ducking and event handling, with a fake decoder and socket."""

from __future__ import annotations

import io
import json
import time
from typing import Any

import numpy as np

from doc_chat.aec import EchoCanceller
from doc_chat.music import Ducker, MusicPlayer

RATE = 16000


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def recv(self, timeout: float = 0) -> str:
        return json.dumps({"type": "hello", "rate": RATE})

    def send(self, data: Any) -> None:
        self.sent.append(data)

    def close(self) -> None:
        pass


class FakeProc:
    def __init__(self, seconds: float) -> None:
        pcm = (np.ones(int(RATE * seconds)) * 1000).astype(np.int16).tobytes()
        self.stdout = io.BytesIO(pcm)

    def kill(self) -> None:
        pass


def wait(cond: Any, timeout: float = 3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def messages(ws: FakeWs) -> list[dict[str, Any]]:
    return [json.loads(m) for m in ws.sent if isinstance(m, str)]


def test_ducker_drops_at_once_and_restores_after_half_a_second() -> None:
    d = Ducker()
    assert d.update(0.0, False, False) == 0.0
    assert d.update(1.0, True, False) == -15.0
    assert d.update(1.1, False, True) == -15.0
    assert d.update(2.0, False, False) == -15.0
    assert d.update(2.4, False, False) == -15.0
    assert d.update(2.5, False, False) == 0.0
    assert d.update(2.6, True, False) == -15.0


def test_ducker_restarts_the_wait_when_speech_returns() -> None:
    d = Ducker()
    d.update(0.0, True, False)
    d.update(1.0, False, False)
    d.update(1.3, True, False)
    assert d.update(1.4, False, False) == -15.0
    assert d.update(1.8, False, False) == -15.0
    assert d.update(1.9, False, False) == 0.0


def make(seconds: float = 0.4, **kw: Any) -> tuple[MusicPlayer, FakeWs]:
    ws = FakeWs()
    player = MusicPlayer(lambda: ws, decoder=lambda url, rate: FakeProc(seconds), **kw)
    return player, ws


def test_play_opens_the_music_role_sets_the_level_and_streams_audio() -> None:
    player, ws = make()
    player.handle({"event": "music", "action": "play", "url": "http://x/a", "db": -6.0})
    assert wait(lambda: not player.playing)
    kinds = [m["type"] for m in messages(ws)]
    assert kinds[0] == "music"
    gain = next(m for m in messages(ws) if m["type"] == "music_gain")
    assert gain["db"] == -6.0
    audio = sum(len(m) for m in ws.sent if isinstance(m, bytes))
    assert audio == int(RATE * 0.4) * 2


def test_stop_flushes_and_ends_the_stream_early() -> None:
    player, ws = make(seconds=30)
    player.handle({"action": "play", "url": "http://x/a", "db": -6.0})
    assert wait(lambda: any(isinstance(m, bytes) for m in ws.sent))
    player.handle({"action": "stop"})
    assert not player.playing
    assert messages(ws)[-1]["type"] == "music_flush"
    n = len(ws.sent)
    time.sleep(0.2)
    assert len(ws.sent) <= n + 1


def test_pause_flushes_and_resume_continues() -> None:
    player, ws = make(seconds=30)
    player.handle({"action": "play", "url": "http://x/a", "db": -6.0})
    assert wait(lambda: any(isinstance(m, bytes) for m in ws.sent))
    player.handle({"action": "pause"})
    time.sleep(0.2)
    n = len(ws.sent)
    time.sleep(0.3)
    assert len(ws.sent) == n
    player.handle({"action": "resume"})
    assert wait(lambda: len(ws.sent) > n)
    player.close()


def test_volume_sends_the_new_level() -> None:
    player, ws = make(seconds=30)
    player.handle({"action": "play", "url": "http://x/a", "db": -6.0})
    player.handle({"action": "volume", "db": -9.0})
    assert messages(ws)[-1] == {"type": "music_gain", "db": -9.0} or any(
        m.get("db") == -9.0 for m in messages(ws)
    )
    player.close()


def test_ducks_while_the_user_speaks_and_feeds_the_music_reference() -> None:
    canceller = EchoCanceller()
    speaking = {"v": True}
    player, ws = make(seconds=1.0, canceller=canceller, user_speaking=lambda: speaking["v"])
    player.handle({"action": "play", "url": "http://x/a", "db": -6.0})
    assert wait(lambda: any(m.get("db") == -21.0 for m in messages(ws)))
    assert wait(lambda: len(canceller._music_frames) > 0)
    player.close()
