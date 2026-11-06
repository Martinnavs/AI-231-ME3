"""Music on the laptop (Y3): decode a stream URL and send it to the Pi speaker's music channel.

The Pi's music MCP server only decides what to play; the audio is decoded here (ffmpeg), paced in
50 ms slices like `RemotePlayer`, sent over a second websocket to `scripts/pi_speaker.py`
(first message `{"type": "music"}`), and copied at 16 kHz into the echo canceller's music
reference. `Ducker` turns the music down while the user speaks or a reply plays.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
from collections.abc import Callable
from math import gcd
from typing import Any

import numpy as np

SLICE_S = 0.05
LEAD_S = 0.25
DUCK_DB = -15.0
RESTORE_S = 0.5
REF_RATE = 16000
FFMPEG = "ffmpeg"


class Ducker:
    """-15 dB while the user is speaking or any reply is playing; back 0.5 s after both end."""

    def __init__(self, duck_db: float = DUCK_DB, restore_s: float = RESTORE_S) -> None:
        self.duck_db = duck_db
        self.restore_s = restore_s
        self._quiet_since: float | None = None
        self.ducked = False

    def update(self, now: float, user_speaking: bool, tts_playing: bool) -> float:
        """The offset in dB to apply to the music level now."""
        if user_speaking or tts_playing:
            self.ducked, self._quiet_since = True, None
        elif self.ducked:
            self._quiet_since = self._quiet_since if self._quiet_since is not None else now
            if now - self._quiet_since >= self.restore_s:
                self.ducked, self._quiet_since = False, None
        return self.duck_db if self.ducked else 0.0


def _decoder(url: str, rate: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed argv; the URL comes from our own resolver
        [
            FFMPEG,
            "-nostdin",
            "-loglevel",
            "error",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            url,
            "-vn",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-",
        ],  # fmt: skip
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


class MusicPlayer:
    """Plays at most one track. `handle(event)` takes the music events of the agent server."""

    def __init__(
        self,
        connect: Callable[[], Any],
        canceller: Any = None,
        user_speaking: Callable[[], bool] = lambda: False,
        tts_playing: Callable[[], bool] = lambda: False,
        decoder: Callable[[str, int], Any] = _decoder,
    ) -> None:
        self._connect = connect  # returns a ws with send(); its hello gives the rate
        self._canceller = canceller
        self._user = user_speaking
        self._tts = tts_playing
        self._decoder = decoder
        self._lock = threading.Lock()
        self._ws: Any = None
        self._rate = 0
        self._gen = 0
        self._paused = threading.Event()
        self._level_db = -6.0
        self._duck = Ducker()
        self._offset = 0.0
        self._applied = 0.0
        self.playing = False

    # ---- events
    def handle(self, event: dict[str, Any]) -> None:
        action = event.get("action")
        if action == "play":
            self.stop()
            self._level_db = float(event.get("db", self._level_db))
            self._start(str(event["url"]))
        elif action == "pause":
            self._paused.set()
            self._flush()
        elif action == "resume":
            self._paused.clear()
        elif action == "stop":
            self.stop()
        elif action == "volume":
            self._level_db = float(event["db"])
            self._send_gain(force=True)

    def _send(self, data: bytes | str) -> None:
        with self._lock:
            if self._ws is not None:
                self._ws.send(data)

    def _send_gain(self, force: bool = False) -> None:
        db = self._level_db + self._offset
        if force or self._offset != self._applied:
            self._applied = self._offset
            with contextlib.suppress(Exception):
                self._send(json.dumps({"type": "music_gain", "db": db}))
            if self._canceller is not None:
                self._canceller.music_gain = 10 ** (db / 20)

    def _flush(self) -> None:
        with contextlib.suppress(Exception):
            self._send(json.dumps({"type": "music_flush"}))
        if self._canceller is not None:
            self._canceller.clear_music()

    def _start(self, url: str) -> None:
        if self._ws is None:
            ws = self._connect()
            self._rate = int(json.loads(ws.recv(timeout=5))["rate"])
            ws.send(json.dumps({"type": "music"}))
            self._ws = ws
        self._paused.clear()
        self._offset = self._applied = 0.0
        self._duck = Ducker()
        self._gen += 1
        self.playing = True
        self._send_gain(force=True)
        threading.Thread(target=self._run, args=(url, self._gen), daemon=True).start()

    def stop(self) -> None:
        self._gen += 1
        self.playing = False
        if self._ws is not None:
            self._flush()

    def close(self) -> None:
        self.stop()
        with self._lock:
            ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                ws.close()

    # ---- the decode / pace loop
    def _run(self, url: str, gen: int) -> None:
        from scipy.signal import resample_poly

        rate = self._rate
        slice_bytes = int(rate * SLICE_S) * 2
        g = gcd(REF_RATE, rate)
        proc = self._decoder(url, rate)
        t0, sent = 0.0, 0
        try:
            while gen == self._gen:
                if self._paused.is_set():
                    time.sleep(0.05)
                    t0, sent = 0.0, 0
                    continue
                piece = proc.stdout.read(slice_bytes)
                if not piece:
                    break
                self._offset = self._duck.update(time.monotonic(), self._user(), self._tts())
                self._send_gain()
                now = time.perf_counter()
                if sent == 0 or now > t0 + sent / rate:
                    t0, sent = now, 0
                while (wait := t0 + sent / rate - LEAD_S - time.perf_counter()) > 0:
                    if gen != self._gen:
                        return
                    time.sleep(min(wait, 0.02))
                if gen != self._gen or self._paused.is_set():
                    continue
                try:
                    self._send(piece)
                except Exception:
                    break
                if self._canceller is not None:
                    x = np.frombuffer(piece[: len(piece) // 2 * 2], dtype=np.int16)
                    ref = resample_poly(x.astype(np.float64), REF_RATE // g, rate // g)
                    self._canceller.reference(
                        np.clip(ref, -32768, 32767).astype(np.int16).tobytes(), music=True
                    )
                sent += len(piece) // 2
        finally:
            with contextlib.suppress(Exception):
                proc.kill()
            if gen == self._gen:
                self.playing = False
