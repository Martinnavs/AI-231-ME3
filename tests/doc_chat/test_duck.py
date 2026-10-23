import asyncio
import importlib.util
import json
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from doc_chat.bargein import Ducker, SileroOnset
from doc_chat.playback import Player, RemotePlayer, RemoteSpeaker, apply_gain

spec = importlib.util.spec_from_file_location(
    "pi_speaker", Path(__file__).resolve().parents[2] / "scripts" / "pi_speaker.py"
)
assert spec and spec.loader
pi_speaker = importlib.util.module_from_spec(spec)
sys.modules["pi_speaker"] = pi_speaker
spec.loader.exec_module(pi_speaker)

PORT = 8792


def test_apply_gain_moves_in_a_straight_line() -> None:
    x = np.full(1000, 10000, dtype=np.int16)
    y = apply_gain(x, 1.0, 0.1)
    assert y[0] == 10000 and abs(int(y[-1]) - 1000) <= 1
    assert np.all(np.diff(y.astype(np.int32)) <= 0)


def test_ducker_ducks_once_and_restores_after_the_voice_stops() -> None:
    d = Ducker(-15.0, restore_s=0.6)
    assert d.update(voiced=False, fired=False, now=0.0) is None
    assert d.update(voiced=True, fired=False, now=1.0) == -15.0 and d.ducked
    assert d.update(voiced=True, fired=False, now=1.1) is None  # already ducked
    assert d.update(voiced=False, fired=False, now=1.3) is None  # too soon to restore
    assert d.update(voiced=False, fired=False, now=1.7) == 0.0 and not d.ducked


def test_ducker_stays_down_when_the_guard_fires() -> None:
    d = Ducker(-15.0)
    d.update(voiced=True, fired=False, now=1.0)
    assert d.update(voiced=False, fired=True, now=1.1) is None and not d.ducked


def test_silero_counts_recent_speech_windows() -> None:
    v = SileroOnset("assets/silero_vad.onnx", confidence=0.5, kofn=(7, 10))
    v._recent.extend([True, False, True])
    assert v.recent_speech == 2


def test_player_scales_what_reaches_the_device(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks: list[bytes] = []

    class Stream:
        def __init__(self, **kw: Any) -> None: ...
        def start(self) -> None: ...
        def stop(self) -> None: ...
        def close(self) -> None: ...
        def abort(self) -> None: ...

        def write(self, data: bytes) -> bool:
            chunks.append(data)
            return False

    mod = types.ModuleType("sounddevice")
    mod.RawOutputStream = Stream  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    player = Player(16000)
    player.set_gain(-20.0)
    player.push(np.full(1600, 10000, dtype=np.int16).tobytes())
    player.close()
    heard = np.frombuffer(b"".join(chunks), dtype=np.int16)
    assert 900 < np.abs(heard[800:]).mean() < 1100  # a tenth of 10000, after the ramp


class FakeSpeaker:
    rate = 48000

    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, data: bytes | str) -> None:
        self.sent.append(data)


class FakeCanceller:
    gain = 1.0

    def reference(self, pcm: bytes) -> None: ...

    def clear(self) -> None: ...


def test_remote_player_set_gain_tells_the_pi_and_the_canceller() -> None:
    speaker, canceller = FakeSpeaker(), FakeCanceller()
    player = RemotePlayer(speaker, 16000, canceller)  # type: ignore[arg-type]
    player._levels.append((time.perf_counter(), 0.5))
    player.set_gain(-15.0)
    messages = [json.loads(m) for m in speaker.sent if isinstance(m, str)]
    assert {"type": "gain", "db": -15.0} in messages
    assert abs(canceller.gain - 10 ** (-15 / 20)) < 1e-9
    assert abs(player.level_recent() - 0.5 * 10 ** (-15 / 20)) < 1e-6
    player.set_gain(0.0)
    assert canceller.gain == 1.0
    player.abort()


class FakePiPlayer:
    instances: list["FakePiPlayer"] = []

    def __init__(self, rate: int) -> None:
        self.bytes = 0
        self.gains: list[float] = []
        FakePiPlayer.instances.append(self)

    def push(self, pcm: bytes) -> None:
        self.bytes += len(pcm)

    def set_gain(self, db: float) -> None:
        self.gains.append(db)

    def abort(self) -> None: ...

    def close(self) -> None: ...


def test_the_pi_applies_a_gain_message_to_the_reply_in_progress() -> None:
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def run() -> None:
        evt = asyncio.Event()
        task = asyncio.create_task(
            pi_speaker.serve_speaker("127.0.0.1", PORT, "secret", 48000, FakePiPlayer, evt)
        )
        await evt.wait()
        ready.set()
        await task

    threading.Thread(target=lambda: loop.run_until_complete(run()), daemon=True).start()
    assert ready.wait(5)
    speaker = RemoteSpeaker(f"ws://127.0.0.1:{PORT}", "secret")
    player = RemotePlayer(speaker, 16000)
    player.push(np.zeros(16000, dtype=np.int16).tobytes())
    end = time.time() + 5
    while not (FakePiPlayer.instances and FakePiPlayer.instances[0].bytes) and time.time() < end:
        time.sleep(0.02)
    player.set_gain(-15.0)
    while not FakePiPlayer.instances[0].gains and time.time() < end:
        time.sleep(0.02)
    assert FakePiPlayer.instances[0].gains == [-15.0]
    player.abort()
    speaker.close()
