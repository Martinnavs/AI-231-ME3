"""RemotePlayer -> pi_speaker over a real websocket; a fake stands in for the sound card."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from doc_chat.playback import RemotePlayer, RemoteSpeaker

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pi_speaker.py"
spec = importlib.util.spec_from_file_location("pi_speaker", SCRIPT)
assert spec and spec.loader
pi_speaker = importlib.util.module_from_spec(spec)
sys.modules["pi_speaker"] = pi_speaker
spec.loader.exec_module(pi_speaker)

PORT = 8791
DEVICE_RATE = 48000


class FakePlayer:
    """What the Pi would play: records pushes and whether it was aborted or closed."""

    instances: list[FakePlayer] = []

    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.bytes = 0
        self.aborted = False
        self.closed = False
        FakePlayer.instances.append(self)

    def push(self, pcm: bytes) -> None:
        self.bytes += len(pcm)

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture(scope="module")
def server() -> Any:
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def run() -> None:
        evt = asyncio.Event()
        task = asyncio.create_task(
            pi_speaker.serve_speaker("127.0.0.1", PORT, "secret", DEVICE_RATE, FakePlayer, evt)
        )
        await evt.wait()
        ready.set()
        await task

    thread = threading.Thread(target=lambda: loop.run_until_complete(run()), daemon=True)
    thread.start()
    assert ready.wait(5)
    yield


@pytest.fixture()
def pi(server: Any) -> Any:
    FakePlayer.instances.clear()
    yield


def tone(seconds: float, rate: int = 44100) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    return (0.3 * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16).tobytes()


def wait_for(cond: Any, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_audio_is_resampled_paced_and_finished(pi: Any) -> None:
    speaker = RemoteSpeaker(f"ws://127.0.0.1:{PORT}", "secret")
    assert speaker.rate == DEVICE_RATE
    player = RemotePlayer(speaker, 44100)
    t0 = time.perf_counter()
    player.push(tone(1.0))
    player.close()
    assert wait_for(lambda: FakePlayer.instances and FakePlayer.instances[0].closed)
    got = FakePlayer.instances[0]
    assert abs(got.bytes / 2 - DEVICE_RATE) < 200  # one second of audio, at the Pi's rate
    assert not got.aborted
    assert time.perf_counter() - t0 > 0.6  # sent at playing speed (1 s minus the 0.25 s lead)
    speaker.close()


def test_abort_stops_sending_and_drops_what_the_pi_holds(pi: Any) -> None:
    speaker = RemoteSpeaker(f"ws://127.0.0.1:{PORT}", "secret")
    player = RemotePlayer(speaker, 44100)
    player.push(tone(5.0))
    assert wait_for(lambda: FakePlayer.instances and FakePlayer.instances[0].bytes > 0)
    player.abort()
    assert wait_for(lambda: FakePlayer.instances[0].aborted)
    sent = FakePlayer.instances[0].bytes
    time.sleep(0.4)
    assert FakePlayer.instances[0].bytes == sent  # nothing more arrives after the abort
    assert sent / 2 < DEVICE_RATE * 1.0  # far less than the 5 s pushed: it is paced
    speaker.close()


def test_a_second_reply_gets_a_fresh_player(pi: Any) -> None:
    speaker = RemoteSpeaker(f"ws://127.0.0.1:{PORT}", "secret")
    for _ in range(2):
        p = RemotePlayer(speaker, 44100)
        p.push(tone(0.2))
        p.close()
    assert wait_for(lambda: len(FakePlayer.instances) == 2 and FakePlayer.instances[1].closed)
    speaker.close()


def test_wrong_token_is_refused(pi: Any) -> None:
    with pytest.raises(Exception):  # noqa: B017,PT011 - websockets raises InvalidStatus
        RemoteSpeaker(f"ws://127.0.0.1:{PORT}", "wrong")


def test_remote_close_waits_until_the_pi_has_played_a_long_reply(pi: Any) -> None:
    speaker = RemoteSpeaker(f"ws://127.0.0.1:{PORT}", "secret")
    player = RemotePlayer(speaker, 44100)
    player.push(tone(3.0))
    t0 = time.perf_counter()
    player.close()
    # slices go out 0.25 s ahead of playing, and close adds that lead back: about 3 s in all
    assert time.perf_counter() - t0 >= 2.9
    assert wait_for(lambda: FakePlayer.instances and FakePlayer.instances[0].closed)
    assert abs(FakePlayer.instances[0].bytes / 2 - 3 * DEVICE_RATE) < 400
    speaker.close()


def test_mix_beep_covers_the_right_samples_even_across_two_chunks() -> None:
    from doc_chat.playback import mix_beep

    rate = 16000
    quiet = (np.ones(rate * 2, dtype=np.int16) * 100).tobytes()  # 2 s of a faint constant
    whole = np.frombuffer(mix_beep(quiet, 0, rate, 0.5), dtype=np.int16)
    loud = np.nonzero(np.abs(whole) > 1000)[0]
    assert loud.min() >= int(0.5 * rate) and loud.max() < int(0.8 * rate) + 1
    assert abs(len(loud) - int(0.3 * rate)) < rate // 100 * 4  # a sine crosses zero: nearly all
    # the same reply split in two chunks gives the same samples
    cut = int(0.65 * rate) * 2
    a = mix_beep(quiet[:cut], 0, rate, 0.5)
    b = mix_beep(quiet[cut:], cut // 2, rate, 0.5)
    assert np.array_equal(np.frombuffer(a + b, dtype=np.int16), whole)
    # a chunk that does not reach the beep is returned untouched
    assert mix_beep(quiet[:1000], 0, rate, 0.5) == quiet[:1000]
