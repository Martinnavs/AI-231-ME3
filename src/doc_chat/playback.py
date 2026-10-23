"""Non-blocking speaker output, so synthesis of the next sentence overlaps playback."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import threading
import time
from collections import deque
from typing import Any

import numpy as np

SLICE_S = 0.05  # audio is handed to the device in pieces this long, so a stop takes effect fast
FADE_S = 0.008  # a cut-off ends with a fade this long instead of a step (which is heard as a click)


def apply_gain(x: np.ndarray, start: float, end: float) -> np.ndarray:
    """16-bit samples scaled by a gain that moves in a straight line from `start` to `end`."""
    ramp = np.linspace(start, end, len(x), dtype=np.float32)
    scaled: np.ndarray = np.clip(x.astype(np.float32) * ramp, -32768, 32767).astype(np.int16)
    return scaled


class Player:
    """Writes PCM to the speakers from a background thread; `close` waits for it to finish.

    `abort` goes silent within one slice (50 ms) plus the device's own buffer (about 35 ms), and
    ends with an 8 ms fade, not a step. It must not touch the audio stream itself:
    PortAudio's ALSA backend deadlocks, and prints a wall of "Expression ... failed" errors, if a
    stream is aborted from one thread while another is blocked writing to it. So `abort` only sets
    a flag; the writing thread notices it between slices and aborts the stream itself.
    """

    def __init__(self, sample_rate: int) -> None:
        import sounddevice as sd

        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._aborted = threading.Event()
        self._levels: deque[tuple[float, float]] = deque(maxlen=64)  # (time, RMS) per slice
        self._slice = max(int(sample_rate * SLICE_S), 1) * 2  # bytes of 16-bit mono
        self._rate = sample_rate
        self.underruns = 0  # slices the device had to wait for (heard as a stutter)
        self._pushed_s = 0.0  # seconds of audio queued so far: how long `close` may have to wait
        self._gain = 1.0  # 1.0 = as pushed; set_gain ducks it
        self._stream: Any = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16")
        self._stream.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        stopped = False
        last = 0  # the last sample handed to the device
        applied = 1.0  # the gain at the end of the previous slice

        def stop_device() -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            n = max(int(self._rate * FADE_S), 1)
            ramp = (last * np.linspace(1.0, 0.0, n)).astype(np.int16).tobytes()
            try:
                self._stream.write(ramp)  # from this thread, the one that writes
                self._stream.stop()  # lets the device play out its buffer (about 35 ms)
            except Exception:
                with contextlib.suppress(Exception):
                    self._stream.abort()

        while (pcm := self._queue.get()) is not None:
            for i in range(0, len(pcm), self._slice):
                if self._aborted.is_set():
                    break
                piece = pcm[i : i + self._slice]
                x = np.frombuffer(piece[: len(piece) // 2 * 2], dtype=np.int16)  # whole samples
                if self._gain != 1.0 or applied != 1.0:  # ducked, or just let back up
                    x = apply_gain(x, applied, self._gain)
                    applied = self._gain
                    piece = x.tobytes()
                level = (
                    float(np.sqrt(np.mean((x.astype(np.float32) / 32768.0) ** 2)))
                    if x.size
                    else 0.0
                )
                self._levels.append((time.perf_counter(), level))
                try:
                    if self._stream.write(piece):
                        self.underruns += 1
                except Exception:
                    self._aborted.set()
                    break
                last = int(x[-1]) if x.size else last
            if self._aborted.is_set():
                stop_device()
        if self._aborted.is_set():
            stop_device()

    def level_recent(self, window_s: float = 0.3) -> float:
        """The loudest slice (RMS, 0-1) handed to the speaker in the last ``window_s`` seconds."""
        now = time.perf_counter()
        return max((lv for t, lv in list(self._levels) if now - t <= window_s), default=0.0)

    def set_gain(self, db: float) -> None:
        """Turn the speaker down (negative dB) or back to normal (0), from the next slice on."""
        self._gain = 10 ** (db / 20)

    def push(self, pcm: bytes) -> None:
        self._pushed_s += len(pcm) / 2 / self._rate
        self._queue.put(pcm)

    def abort(self) -> None:
        """Go silent now: drop everything queued; the writing thread stops the device."""
        self._aborted.set()
        with contextlib.suppress(queue.Empty):
            while True:
                self._queue.get_nowait()

    def close(self) -> None:
        """Return when everything pushed has been played (or at once after `abort`).

        The caller treats "closed" as "the assistant has stopped talking" and starts listening, so
        returning while seconds of a long reply are still coming out of the speaker makes the
        assistant hear itself. The wait is bounded by the reply's own length, so a stuck device
        cannot hold it for long."""
        self._queue.put(None)
        self._thread.join(timeout=self._pushed_s + 2.0)
        with contextlib.suppress(Exception):
            if not self._aborted.is_set():  # a cut-off already stopped it, from the writing thread
                self._stream.stop()
            self._stream.close()


def mix_beep(
    pcm: bytes, offset: int, rate: int, at_s: float, length_s: float = 0.3, hz: float = 880.0
) -> bytes:
    """Mix a short tone into a chunk of a reply: the cue to talk over it, for measuring barge-in.

    ``offset`` is the number of samples of the reply before this chunk; the tone covers
    ``length_s`` from ``at_s`` into the reply, so it can fall in one chunk or be split across
    two. The reply under it is turned down so the tone is clearly the loudest thing."""
    start, stop = int(at_s * rate), int((at_s + length_s) * rate)
    n = len(pcm) // 2
    lo, hi = max(start - offset, 0), min(stop - offset, n)
    if lo >= hi:
        return pcm
    x = np.frombuffer(pcm[: n * 2], dtype=np.int16).astype(np.float32)
    t = (np.arange(lo, hi) + offset - start) / rate
    x[lo:hi] = np.clip(x[lo:hi] * 0.2 + 0.3 * 32767 * np.sin(2 * np.pi * hz * t), -32768, 32767)
    return x.astype(np.int16).tobytes() + pcm[n * 2 :]


# ---- a speaker on another machine (the Pi), reached over the LAN ----

REMOTE_LEAD_S = 0.25  # audio is kept at most this far ahead of the speaker, so an abort is quick


class RemoteSpeaker:
    """One connection to ``scripts/pi_speaker.py``; every reply gets its own `RemotePlayer`.

    Messages: the Pi sends ``{"type": "hello", "rate": N}`` (the rate its speaker opens at); we
    send ``{"type": "begin"}``, binary frames of 16-bit mono PCM at that rate, then ``{"type":
    "end"}`` (let it finish) or ``{"type": "abort"}`` (go silent now). The bearer token is the
    speech server's (``LAN_SPEECH_TOKEN``)."""

    def __init__(self, url: str, token: str | None = None) -> None:
        from websockets.sync.client import connect

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._cm: Any = connect(url, additional_headers=headers, open_timeout=5)
        self._ws: Any = self._cm.__enter__()  # kept open for the whole session
        self.rate = int(json.loads(self._ws.recv(timeout=5))["rate"])

    def send(self, data: bytes | str) -> None:
        self._ws.send(data)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._cm.__exit__(None, None, None)


class RemotePlayer:
    """The same interface as `Player`, with the sound coming out of another machine.

    Each pushed chunk (a sentence) is resampled to the remote speaker's rate, then sent in 50 ms
    slices paced at playing speed, never more than `REMOTE_LEAD_S` ahead. The remote end drops
    what it holds on ``abort``, so the stop is the lead plus its own device buffer."""

    def __init__(self, speaker: RemoteSpeaker, sample_rate: int, canceller: Any = None) -> None:
        from math import gcd

        self._speaker = speaker
        self._canceller = canceller  # gets what is sent, as 16 kHz audio, to cancel its echo
        self._gain = 1.0
        if canceller is not None:
            canceller.gain = 1.0
        self._rate = speaker.rate
        g = gcd(sample_rate, self._rate)
        self._up, self._down = self._rate // g, sample_rate // g
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._aborted = threading.Event()
        self._levels: deque[tuple[float, float]] = deque(maxlen=64)
        self.underruns = 0
        self._pushed_s = 0.0
        self._tts_rate = sample_rate
        self._speaker.send(json.dumps({"type": "begin"}))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from math import gcd

        from scipy.signal import resample_poly

        slice_n = max(int(self._rate * SLICE_S), 1)
        t0, sent = 0.0, 0  # playing started at t0; `sent` samples are scheduled since
        while (pcm := self._queue.get()) is not None:
            if self._aborted.is_set():
                continue
            x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float64)
            y = resample_poly(x, self._up, self._down) if self._up != self._down else x
            data = np.clip(y, -32768, 32767).astype(np.int16)
            ref16 = None
            if self._canceller is not None:
                g16 = gcd(16000, self._tts_rate)
                ref16 = resample_poly(x, 16000 // g16, self._tts_rate // g16)
            for i in range(0, len(data), slice_n):
                piece = data[i : i + slice_n]
                now = time.perf_counter()
                if sent == 0 or now > t0 + sent / self._rate:  # first slice, or we fell behind
                    if sent:
                        self.underruns += 1
                    t0, sent = now, 0
                while (wait := t0 + sent / self._rate - REMOTE_LEAD_S - time.perf_counter()) > 0:
                    if self._aborted.is_set():
                        break
                    time.sleep(min(wait, 0.02))
                if self._aborted.is_set():
                    break
                try:
                    self._speaker.send(piece.tobytes())
                except Exception:
                    self._aborted.set()
                    break
                if ref16 is not None:
                    a = round(i * 16000 / self._rate)
                    b = round((i + len(piece)) * 16000 / self._rate)
                    reference = np.clip(ref16[a:b], -32768, 32767).astype(np.int16)
                    self._canceller.reference(reference.tobytes())
                level = float(np.sqrt(np.mean((piece.astype(np.float32) / 32768.0) ** 2)))
                self._levels.append((time.perf_counter() + REMOTE_LEAD_S, level))
                sent += len(piece)
        if not self._aborted.is_set():
            with contextlib.suppress(Exception):
                self._speaker.send(json.dumps({"type": "end"}))

    def level_recent(self, window_s: float = 0.3) -> float:
        now = time.perf_counter()
        loudest = max((lv for t, lv in list(self._levels) if 0 <= now - t <= window_s), default=0.0)
        return loudest * self._gain

    def set_gain(self, db: float) -> None:
        """Turn the speaker down (negative dB) or back to normal (0), now: the Pi applies it to
        what it has not yet played, and the canceller's reference follows."""
        self._gain = 10 ** (db / 20)
        if self._canceller is not None:
            self._canceller.gain = self._gain
        with contextlib.suppress(Exception):
            self._speaker.send(json.dumps({"type": "gain", "db": db}))

    def push(self, pcm: bytes) -> None:
        self._pushed_s += len(pcm) / 2 / self._tts_rate
        self._queue.put(pcm)

    def abort(self) -> None:
        self._aborted.set()
        with contextlib.suppress(queue.Empty):
            while True:
                self._queue.get_nowait()
        with contextlib.suppress(Exception):
            self._speaker.send(json.dumps({"type": "abort"}))
        if self._canceller is not None:
            self._canceller.clear()

    def close(self) -> None:
        """Return when everything pushed has been sent *and played* (or at once after `abort`).

        Slices are sent `REMOTE_LEAD_S` ahead of playing, so after the last one is sent the Pi
        still has that much to play, plus its device buffer. See `Player.close`."""
        self._queue.put(None)
        self._thread.join(timeout=self._pushed_s + 2.0)
        self._aborted.wait(REMOTE_LEAD_S + 0.1)  # the tail still playing at the Pi (not if aborted)


_remote: dict[str, RemoteSpeaker] = {}


def make_player(
    sample_rate: int, remote_url: str | None = None, canceller: Any = None
) -> Player | RemotePlayer:
    """The local speaker, or (with ``remote_url``) the speaker of another machine."""
    if remote_url is None:
        return Player(sample_rate)
    if remote_url not in _remote:
        _remote[remote_url] = RemoteSpeaker(remote_url, os.environ.get("LAN_SPEECH_TOKEN"))
    return RemotePlayer(_remote[remote_url], sample_rate, canceller)
