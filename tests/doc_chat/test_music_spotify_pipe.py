import io
import json
import threading
import time
from pathlib import Path
from typing import Any

from doc_chat.music import SPOTIFY_FIFO, MusicPlayer

RATE = 16000


class FakeWs:
    def recv(self, timeout: float = 0) -> str:
        return json.dumps({"type": "hello", "rate": RATE})

    def send(self, data: Any) -> None:
        pass

    def close(self) -> None:
        pass


class FakeProc:
    def __init__(self, data: bytes = b"\x00" * 3200) -> None:
        self.stdout = io.BytesIO(data)

    def kill(self) -> None:
        pass


def wait(cond: Any, timeout: float = 3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_spotify_play_uses_pipe_decoder_with_fifo_path() -> None:
    seen_paths: list[Path] = []
    seen_rates: list[int] = []

    def fake_pipe(path: Path, rate: int) -> FakeProc:
        seen_paths.append(path)
        seen_rates.append(rate)
        return FakeProc()

    player = MusicPlayer(FakeWs, pipe_decoder=fake_pipe)
    player.handle({"action": "play", "source": "spotify", "title": "Song", "db": -6.0})
    assert wait(lambda: bool(seen_paths))
    player.stop()
    assert seen_paths == [SPOTIFY_FIFO]
    assert seen_rates == [RATE]


def test_url_play_still_uses_url_decoder() -> None:
    seen_urls: list[str] = []
    seen_rates: list[int] = []

    def fake_url(url: str, rate: int) -> FakeProc:
        seen_urls.append(url)
        seen_rates.append(rate)
        return FakeProc()

    player = MusicPlayer(FakeWs, decoder=fake_url)
    player.handle(
        {"action": "play", "url": "http://example.com/stream", "title": "Song", "db": -6.0}
    )
    assert wait(lambda: bool(seen_urls))
    player.stop()
    assert seen_urls == ["http://example.com/stream"]
    assert seen_rates == [RATE]


def test_stop_ends_pipe_playback() -> None:
    class BlockingStdout:
        def __init__(self) -> None:
            self.closed = False
            self.event = threading.Event()

        def read(self, n: int = -1) -> bytes:
            if self.closed:
                return b""
            self.event.wait()
            return b""

        def close(self) -> None:
            self.closed = True
            self.event.set()

    class BlockingProc:
        def __init__(self) -> None:
            self.stdout = BlockingStdout()

        def kill(self) -> None:
            self.stdout.close()

    player = MusicPlayer(FakeWs, pipe_decoder=lambda p, r: BlockingProc())
    player.handle({"action": "play", "source": "spotify", "title": "Song", "db": -6.0})
    assert wait(lambda: player.playing)
    t0 = time.time()
    player.stop()
    elapsed = time.time() - t0
    assert elapsed < 1.0
    assert not player.playing
