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


def test_route_spotify_moves_only_the_spotify_stream() -> None:
    from doc_chat.music import route_spotify

    calls: list[list[str]] = []

    class Out:
        stdout = (
            '[{"index": 7, "properties": {"application.name": "Spotify"}},'
            ' {"index": 9, "properties": {"application.name": "Firefox"}}]'
        )

    def run(argv: list[str], **_: object) -> Out:
        calls.append(argv)
        return Out()

    assert route_spotify(run, sink="me3_spotify") is True
    assert calls[1] == ["pactl", "move-sink-input", "7", "me3_spotify"]
    assert len(calls) == 2


# ---- the audio must not vanish (2026-11-12): a restart, a new stream, a broken socket


def _pactl(sinks: list[dict[str, Any]], inputs: list[dict[str, Any]]) -> Any:
    def run(argv: list[str], **kw: Any) -> Any:
        body = sinks if argv[-1] == "sinks" else inputs
        return type("R", (), {"stdout": json.dumps(body), "returncode": 0})()

    return run


MY_SINK = [{"index": 329, "name": "me3_spotify"}, {"index": 58, "name": "speaker"}]


def test_spotify_in_sink_needs_the_app_playing_into_our_sink() -> None:
    from doc_chat.music import spotify_in_sink

    ours = [{"sink": 329, "properties": {"application.name": "Spotify"}}]
    assert spotify_in_sink(_pactl(MY_SINK, ours)) is True
    elsewhere = [{"sink": 58, "properties": {"application.name": "Spotify"}}]
    assert spotify_in_sink(_pactl(MY_SINK, elsewhere)) is False  # on the laptop speaker
    corked = [{"sink": 329, "corked": True, "properties": {"application.name": "Spotify"}}]
    assert spotify_in_sink(_pactl(MY_SINK, corked)) is False
    other = [{"sink": 329, "properties": {"application.name": "Firefox"}}]
    assert spotify_in_sink(_pactl(MY_SINK, other)) is False


def test_a_restarted_client_captures_the_music_that_keeps_playing(monkeypatch: Any) -> None:
    from doc_chat import music as music_module

    seen: list[Path] = []

    def fake_pipe(path: Path, rate: int) -> FakeProc:
        seen.append(path)
        return FakeProc(b"\x00" * 3200 * 40)

    player = MusicPlayer(FakeWs, pipe_decoder=fake_pipe)
    monkeypatch.setattr(music_module, "spotify_in_sink", lambda *a, **k: False)
    assert player.adopt_spotify() is False and not player.playing  # nothing plays in our sink
    monkeypatch.setattr(music_module, "spotify_in_sink", lambda *a, **k: True)
    assert player.adopt_spotify() is True
    assert wait(lambda: bool(seen)) and player.playing and player._spotify
    assert player.adopt_spotify() is False  # already capturing: not twice
    player.close()


def test_a_new_stream_on_the_default_output_is_moved_into_our_sink(monkeypatch: Any) -> None:
    from doc_chat import music as music_module

    moved: list[str] = []
    monkeypatch.setattr(music_module, "spotify_in_sink", lambda *a, **k: False)
    monkeypatch.setattr(
        music_module, "route_spotify", lambda *a, **k: moved.append("moved") or True
    )
    player = MusicPlayer(FakeWs)
    player._reroute()
    monkeypatch.setattr(music_module, "spotify_in_sink", lambda *a, **k: True)
    player._reroute()
    assert moved == ["moved"]  # only when the stream is not in our sink


def test_resume_and_next_look_for_the_apps_stream_again(monkeypatch: Any) -> None:
    from doc_chat import music as music_module

    monkeypatch.setattr(music_module, "spotify_local", lambda *a, **k: True)
    routed: list[int] = []
    player = MusicPlayer(FakeWs)
    monkeypatch.setattr(player, "_route", lambda: routed.append(1))
    player._spotify = True
    player.handle({"action": "resume"})
    player.handle({"action": "next"})
    assert wait(lambda: len(routed) == 2)


def test_a_broken_speaker_socket_is_reopened_and_the_music_goes_on() -> None:
    sockets: list[Any] = []

    class Ws(FakeWs):
        def __init__(self, fail_after: int | None) -> None:
            self.audio: list[bytes] = []
            self.fail_after = fail_after

        def send(self, data: Any) -> None:
            if isinstance(data, bytes):
                if self.fail_after is not None and len(self.audio) >= self.fail_after:
                    raise ConnectionError("the Pi speaker restarted")
                self.audio.append(data)

    def connect() -> Ws:
        ws = Ws(fail_after=2 if not sockets else None)
        sockets.append(ws)
        return ws

    player = MusicPlayer(connect, pipe_decoder=lambda path, rate: FakeProc(b"\x01\x00" * 16000 * 3))
    player._spotify = True
    player.handle({"action": "play", "source": "spotify", "db": 0.0})
    assert wait(lambda: len(sockets) == 2 and len(sockets[1].audio) > 3, 6.0)
    assert len(sockets[0].audio) == 2  # the first socket carried two slices, then broke
    player.close()
