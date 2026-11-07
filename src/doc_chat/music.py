"""Music on the laptop (Y3): decode a stream URL and send it to the Pi speaker's music channel.

The Pi's music MCP server only decides what to play; the audio is decoded here (ffmpeg), paced in
50 ms slices like `RemotePlayer`, sent over a second websocket to `scripts/pi_speaker.py`
(first message `{"type": "music"}`), and copied at 16 kHz into the echo canceller's music
reference. The music is brought to the voice's loudness (`AutoGain`, K-weighted) and is never
sent while any reply is playing (the hold); the Pi mixer drops it under a reply as well.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from math import gcd
from pathlib import Path
from typing import Any

import numpy as np

SLICE_S = 0.05
GAIN_STEP_DB = 0.25  # the auto gain is re-sent at most this often
LEAD_S = 0.25
SHORT_TERM_S = 3.0
AUTO_SLEW_DB_S = 1.0
AUTO_LIMITS_DB = (-40.0, 20.0)
# BS.1770 loudness of the TTS voice as it leaves the Pi mixer: Kitten chunks are brought to
# KITTEN_TARGET_RMS_DBFS (-21.3, active speech) by engines.match_level and the reply channel's gain
# is 1.0 (the mixer resets it per reply); measured on Kitten phrases, see the 2a ticket log.
TTS_REFERENCE_LUFS = -24.2
REF_RATE = 16000
FFMPEG = "ffmpeg"
SPOTIFY_FIFO = Path(
    os.environ.get("SPOTIFY_FIFO", str(Path.home() / ".cache" / "me3" / "librespot.pcm"))
)
# The Spotify desktop app plays into this private PipeWire sink (made by the stack); its monitor
# is recorded instead of librespot's pipe unless SPOTIFY_FIFO is a real FIFO (librespot's audio
# keys are refused for some accounts: librespot issue #1664).
SPOTIFY_SINK = os.environ.get("SPOTIFY_SINK", "me3_spotify")
SPOTIFY_APP = "spotify"  # application.name of the desktop app's stream
ROUTE_S = 4.0  # how long to look for the app's stream after a play event


MPRIS = [
    "dbus-send",
    "--session",
    "--type=method_call",
    "--dest=org.mpris.MediaPlayer2.spotify",
    "/org/mpris/MediaPlayer2",
]


def spotify_local(command: str, uri: str = "", run: Callable[..., Any] = subprocess.run) -> bool:
    """Tell the desktop app over MPRIS: open (play `uri`), pause, play (resume) or next."""
    method = {"open": "OpenUri", "pause": "Pause", "play": "Play", "next": "Next"}[command]
    argv = [*MPRIS, f"org.mpris.MediaPlayer2.Player.{method}"]
    if command == "open":
        argv.append(f"string:{uri}")
    try:
        return bool(run(argv, capture_output=True, timeout=3).returncode == 0)
    except (OSError, subprocess.SubprocessError):
        return False


def route_spotify(run: Callable[..., Any] = subprocess.run, sink: str = SPOTIFY_SINK) -> bool:
    """Move the Spotify app's playback stream into `sink`; False if no such stream yet."""
    out = run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True)
    moved = False
    for item in json.loads(out.stdout or "[]"):
        props = item.get("properties", {})
        if str(props.get("application.name", "")).lower() == SPOTIFY_APP:
            run(["pactl", "move-sink-input", str(item["index"]), sink], capture_output=True)
            moved = True
    return moved


def k_weighting_sos(rate: int) -> np.ndarray:
    """ITU-R BS.1770 K-weighting (high-shelf pre-filter, then the RLB high-pass) for `rate`,
    as second-order sections."""
    g, q, fc = 10 ** (3.999843853973347 / 20), 0.7071752369554196, 1681.9744509555319
    k = np.tan(np.pi * fc / rate)
    vh, vb = g, g**0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = np.array(
        [
            [
                (vh + vb * k / q + k * k) / a0,
                2 * (k * k - vh) / a0,
                (vh - vb * k / q + k * k) / a0,
                1.0,
                2 * (k * k - 1) / a0,
                (1 - k / q + k * k) / a0,
            ]
        ]
    )
    fh, qh = 38.13547087602444, 0.5003270373238773
    kh = np.tan(np.pi * fh / rate)
    ah = 1 + kh / qh + kh * kh
    high = np.array([[1.0, -2.0, 1.0, 1.0, 2 * (kh * kh - 1) / ah, (1 - kh / qh + kh * kh) / ah]])
    return np.vstack([shelf, high])


class Loudness:
    """K-weighted short-term loudness (3 s window) of a stream of mono 16-bit PCM slices."""

    def __init__(self, rate: int, window_s: float = SHORT_TERM_S) -> None:
        from collections import deque

        self._sos = k_weighting_sos(rate)
        self._zi = np.zeros((self._sos.shape[0], 2))
        self._ms: deque[tuple[float, int]] = deque()
        self._n = 0
        self._cap = int(rate * window_s)

    def add(self, pcm: bytes) -> None:
        from scipy.signal import sosfilt

        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float64) / 32768.0
        if not x.size:
            return
        y, self._zi = sosfilt(self._sos, x, zi=self._zi)
        self._ms.append((float(np.sum(y * y)), x.size))
        self._n += x.size
        while self._n - self._ms[0][1] >= self._cap:
            self._n -= self._ms.popleft()[1]

    @property
    def seconds_ready(self) -> bool:
        return self._n >= self._cap // 2

    @property
    def lufs(self) -> float:
        total = sum(e for e, _ in self._ms)
        return -0.691 + 10 * float(np.log10(total / max(self._n, 1) + 1e-12))


def loudness_of(x: np.ndarray, rate: int) -> float:
    """BS.1770 loudness (LUFS) of a float waveform in [-1, 1], with the standard's gating
    (-70 LUFS absolute, -10 LU relative) on 400 ms blocks at 75% overlap."""
    from scipy.signal import sosfilt

    y = sosfilt(k_weighting_sos(rate), np.asarray(x, dtype=np.float64))
    n, hop = int(0.4 * rate), int(0.1 * rate)
    if y.size < n:
        return -0.691 + 10 * float(np.log10(np.mean(y * y) + 1e-12))
    z = np.array([np.mean(y[i : i + n] ** 2) for i in range(0, y.size - n + 1, hop)])
    lk = -0.691 + 10 * np.log10(z + 1e-12)
    z = z[lk > -70]
    if not z.size:
        return -70.0
    keep = z[-0.691 + 10 * np.log10(z) > -0.691 + 10 * np.log10(z.mean()) - 10]
    return -0.691 + 10 * float(np.log10(keep.mean() if keep.size else z.mean()))


class AutoGain:
    """Brings the music's short-term loudness to `reference` LUFS, at most `slew_db_s` per second
    and within `limits`: music at the voice's level, whatever the track's mastering."""

    def __init__(
        self,
        rate: int,
        reference: float = TTS_REFERENCE_LUFS,
        slew_db_s: float = AUTO_SLEW_DB_S,
        limits: tuple[float, float] = AUTO_LIMITS_DB,
    ) -> None:
        self._meter = Loudness(rate)
        self._reference = reference
        self._slew = slew_db_s
        self._limits = limits
        self.db = 0.0

    def update(self, pcm: bytes, seconds: float) -> float:
        """Take one slice (`seconds` long) and return the gain in dB to apply from now."""
        self._meter.add(pcm)
        if self._meter.seconds_ready and (lufs := self._meter.lufs) > -69.0:
            want = float(np.clip(self._reference - lufs, *self._limits))
            step = self._slew * seconds
            self.db += float(np.clip(want - self.db, -step, step))
        return self.db


MUSIC_VAD_CONFIDENCE = 0.7
MUSIC_VAD_KOFN = (5, 10)  # .scratch/queued-fixes-2026-11-07/pause-vad.txt


class MusicVoice:
    """Pauses the music when the user speaks over it: its own Silero onset detector, fed every
    post-AEC mic block, running only while music is audible (during a reply it cannot tell the
    user from the assistant: there the barge-in decision pauses the music)."""

    def __init__(
        self,
        player: MusicPlayer,
        model_path: str,
        confidence: float = MUSIC_VAD_CONFIDENCE,
        kofn: tuple[int, int] = MUSIC_VAD_KOFN,
        vad: Any = None,
        gate_db: float | None = None,
    ) -> None:
        if vad is None:
            from doc_chat.bargein import SileroOnset

            vad = SileroOnset(model_path, confidence=confidence, kofn=kofn)
        self._vad = vad
        self._player = player
        self._running = False
        self._gate = None if gate_db is None else 10 ** (gate_db / 20.0)

    def feed(self, block: bytes) -> None:
        if not self._player.audible:
            if self._running:
                self._vad.reset()
                self._running = False
            return
        self._running = True
        if self._vad.feed(block):
            self._vad.reset()
            self._running = False
            if self._gate is not None:
                x = np.frombuffer(block[: len(block) // 2 * 2], dtype=np.int16) / 32768.0
                if x.size and float(np.sqrt(np.mean(x * x))) < self._gate:
                    return  # quieter than halfway between the user and the music's leak
            self._player.user_pause()


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


def _pipe_decoder(path: Path, rate: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            FFMPEG,
            "-nostdin",
            "-loglevel",
            "error",
            *(
                ["-f", "s16le", "-ar", "44100", "-ac", "2", "-i", str(path)]
                if path.is_fifo()
                else ["-f", "pulse", "-i", f"{SPOTIFY_SINK}.monitor"]
            ),
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-",
        ],
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
        tts_playing: Callable[[], bool] = lambda: False,
        decoder: Callable[[str, int], Any] = _decoder,
        pipe_decoder: Callable[[Path, int], Any] = _pipe_decoder,
    ) -> None:
        self._connect = connect  # returns a ws with send(); its hello gives the rate
        self._canceller = canceller
        self._tts = tts_playing
        self._decoder = decoder
        self._pipe_decoder = pipe_decoder
        self._proc: Any = None
        self._lock = threading.Lock()
        self._ws: Any = None
        self._rate = 0
        self._gen = 0
        self._paused = threading.Event()
        self._level_db = 0.0
        self._auto: AutoGain | None = None
        self._offset = 0.0
        self._applied = 0.0
        self._held = False  # a reply is playing: the music is not sent
        self.playing = False
        self.auto_paused = False  # paused by the user's voice, not by a command: the server is told
        self._spotify = False  # the current track plays in the desktop Spotify app

    @property
    def audible(self) -> bool:
        """Music is being sent: playing, not held for a reply, not paused."""
        return self.playing and not self._held and not self._paused.is_set()

    def user_pause(self) -> None:
        """Pause because the user spoke over the music or a reply. Sticky: nothing resumes it but
        a play or resume event (the user's own command). Safe from any thread."""
        if not self.playing:
            return
        self.auto_paused = True
        self._paused.set()
        self._flush()
        if self._spotify:
            threading.Thread(target=spotify_local, args=("pause",), daemon=True).start()

    # ---- events
    def handle(self, event: dict[str, Any]) -> None:
        action = event.get("action")
        if event.get("source") == "spotify":
            self._spotify = True
        if action in ("play", "resume", "stop"):
            self.auto_paused = False
        if action == "play":
            self.stop()
            self._level_db = float(event.get("db", self._level_db))
            self._spotify = event.get("source") == "spotify"
            if self._spotify:
                if event.get("uri"):
                    spotify_local("open", str(event["uri"]))
                self._start(SPOTIFY_FIFO, is_pipe=True)
                if not SPOTIFY_FIFO.is_fifo():
                    threading.Thread(target=self._route, daemon=True).start()
            else:
                self._start(str(event["url"]), is_pipe=False)
        elif action == "pause":
            self._paused.set()
            self._flush()
            if self._spotify:
                spotify_local("pause")
        elif action == "resume":
            self._paused.clear()
            if self._spotify and not self._held:
                spotify_local("play")
        elif action == "next" and self._spotify:
            spotify_local("next")
        elif action == "stop":
            if self._spotify:
                spotify_local("pause")
            self.stop()
        elif action == "volume":
            self._level_db = float(event["db"])
            self._send_gain(force=True)

    def _route(self) -> None:
        end = time.monotonic() + ROUTE_S
        with contextlib.suppress(OSError, ValueError, subprocess.SubprocessError):
            while time.monotonic() < end and not route_spotify():
                time.sleep(0.2)

    def _send(self, data: bytes | str) -> None:
        with self._lock:
            if self._ws is not None:
                self._ws.send(data)

    def _send_gain(self, force: bool = False) -> None:
        db = self._level_db + self._offset
        if force or abs(self._offset - self._applied) >= GAIN_STEP_DB:
            self._applied = self._offset
            with contextlib.suppress(Exception):
                self._send(json.dumps({"type": "music_gain", "db": db}))
            if self._canceller is not None:
                self._canceller.music_gain = 10 ** (db / 20)

    def _update_hold(self) -> bool:
        """Follow the replies: the music is flushed (and the Spotify app paused) when one starts
        and let go when it ends, unless the user paused it. True while held."""
        playing = bool(self._tts())
        if playing != self._held:
            self._held = playing
            if playing:
                self._flush()
                if self._spotify and not self._paused.is_set():
                    spotify_local("pause")
            elif self._spotify and not self._paused.is_set():
                spotify_local("play")
        return self._held

    def _flush(self) -> None:
        with contextlib.suppress(Exception):
            self._send(json.dumps({"type": "music_flush"}))
        if self._canceller is not None:
            self._canceller.clear_music()

    def _start(self, target: str | Path, is_pipe: bool = False) -> None:
        if self._ws is None:
            ws = self._connect()
            self._rate = int(json.loads(ws.recv(timeout=5))["rate"])
            ws.send(json.dumps({"type": "music"}))
            self._ws = ws
        self._paused.clear()
        self._offset = self._applied = 0.0
        self._auto = AutoGain(self._rate)
        self._held = False
        self._gen += 1
        self.playing = True
        self._send_gain(force=True)
        threading.Thread(target=self._run, args=(target, self._gen, is_pipe), daemon=True).start()

    def stop(self) -> None:
        self._gen += 1
        self.playing = False
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                if hasattr(proc, "stdout") and proc.stdout:
                    proc.stdout.close()
        if self._ws is not None:
            self._flush()

    def close(self) -> None:
        if self._spotify:
            spotify_local("pause")
        self.stop()
        with self._lock:
            ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                ws.close()

    # ---- the decode / pace loop
    def _run(self, target: str | Path, gen: int, is_pipe: bool = False) -> None:
        from scipy.signal import resample_poly

        rate = self._rate
        slice_bytes = int(rate * SLICE_S) * 2
        g = gcd(REF_RATE, rate)
        proc = (
            self._pipe_decoder(Path(target), rate) if is_pipe else self._decoder(str(target), rate)
        )
        with self._lock:
            self._proc = proc
        t0, sent = 0.0, 0
        try:
            while gen == self._gen:
                if self._update_hold() or self._paused.is_set():
                    time.sleep(0.05)
                    t0, sent = 0.0, 0
                    continue
                piece = proc.stdout.read(slice_bytes)
                if not piece:
                    break
                if self._auto is not None:
                    self._offset = self._auto.update(piece, len(piece) / 2 / rate)
                    self._send_gain()
                now = time.perf_counter()
                if sent == 0 or now > t0 + sent / rate:
                    t0, sent = now, 0
                while (wait := t0 + sent / rate - LEAD_S - time.perf_counter()) > 0:
                    if gen != self._gen:
                        return
                    if self._update_hold():
                        break
                    time.sleep(min(wait, 0.02))
                if gen != self._gen or self._paused.is_set() or self._held:
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
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            if gen == self._gen:
                self.playing = False
