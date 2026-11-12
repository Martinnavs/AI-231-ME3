"""Measure the room once: noise floor, the user's speech level, optionally the music's leak.

Run on the laptop with the client's microphone settings:
    python -m doc_chat.calibrate [--music] [--full --play-remote ws://PI:8780]
Writes out/calibration.json (keys `when`, `noise_db`, `user_db`, optional `music_db`; dBFS).

--full measures the room as it will be used: the quiet room, your voice, Spotify played through
the Pi speaker (piped there exactly as the client does) for the music's leak at the microphone and
the noise floor with music on, your voice over that music, and a background voice (a talk video
or podcast) for the loudness gate. It adds `noise_music_db`, `user_music_db`, `bg_voice_db` and
`gate_db` (the --voice-gate-db to use, when the background voice is far enough below you).
`load_calibration()` returns it, or None when the file is missing or unreadable, and every
consumer then behaves exactly as without calibration.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

PATH = Path("out/calibration.json")
RATE = 16000
BLOCK_S = 0.1
PROMPTS = (
    "What is the weather like in Davao today?",
    "Set a timer for ten minutes, please.",
    "Can you play something relaxing?",
)
# --baseline: the phrases every microphone comparison uses (docs/HEADSET-MIC.md), recorded to a WAV
BASELINE_PROMPTS = (
    "What's the weather tomorrow?",
    "Dad, can you pass the salt?",
    "Play some jazz.",
    "Hey Jamie, dinner's ready.",
    "Set a timer for ten minutes.",
    "I forgot to water the herbs.",
)
BASELINE_WAV = Path("out/calibration-baseline.wav")
OPTIONAL_KEYS = ("music_db", "noise_music_db", "user_music_db", "bg_voice_db", "gate_db")
MIN_SEPARATION_DB = 12.0  # the user must be this far above the background voice to gate on level
GATE_MIN_DB, GATE_MAX_DB = 6.0, 14.0
MIN_SNR_DB = 15.0  # the user's voice should be this far above the noise for reliable recognition
MIN_MUSIC_GAP_DB = 10.0
SILENCE_DB = -80.0  # a recording quieter than this has nothing in it


def dbfs(level: float) -> float:
    """A linear RMS level (1.0 = full scale) in dBFS."""
    return 20.0 * math.log10(max(level, 1e-6))


def percentile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def block_levels(pcm: bytes, rate: int = RATE, block_s: float = BLOCK_S) -> list[float]:
    """RMS of each block of 16-bit mono PCM, as a fraction of full scale."""
    import numpy as np

    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float64) / 32768.0
    n = int(rate * block_s)
    return [float(np.sqrt(np.mean(x[i : i + n] ** 2))) for i in range(0, len(x) - n + 1, n)]


def noise_db(levels: list[float]) -> float:
    """The 10th percentile of the block levels, as the pause detector computes the room."""
    return dbfs(percentile(levels, 0.10))


def speech_db(levels: list[float], noise: float) -> float:
    """RMS level of the blocks at least 10 dB above the noise floor (the speech), in dBFS."""
    floor = 10 ** ((noise + 10.0) / 20.0)
    active = [lv for lv in levels if lv >= floor]
    if not active:
        raise ValueError("no speech above the noise floor")
    return dbfs(math.sqrt(sum(lv * lv for lv in active) / len(active)))


def music_db(levels: list[float]) -> float:
    """The music's level at the mic: the median block level."""
    return dbfs(percentile(levels, 0.5))


def turn_level_db(pcm: bytes) -> float | None:
    """A recording's speech level the way the loudness gate measures a turn (dBFS), or None."""
    from doc_chat.voice_gate import active_levels

    return active_levels(pcm)[1]


def recommend_gate_db(user: float, background: float) -> float | None:
    """The --voice-gate-db for this room: half the distance between you and the background voice,
    kept between 6 and 14 dB; None when the background voice is under 12 dB below you, where no
    loudness gate can tell it from you."""
    gap = user - background
    if gap < MIN_SEPARATION_DB:
        return None
    return round(min(max(gap / 2.0, GATE_MIN_DB), GATE_MAX_DB), 1)


def advice(result: dict[str, Any]) -> list[str]:
    """What the measurements say about this setup, one line each; empty when all is well."""
    out: list[str] = []
    user = result["user_db"]
    if "music_db" in result and user - result["music_db"] < MIN_MUSIC_GAP_DB:
        out.append(
            f"The music reaches the microphone at {result['music_db']:.0f} dBFS, within "
            f"{MIN_MUSIC_GAP_DB:.0f} dB of your voice ({user:.0f}): lower it or move the speaker."
        )
    if "noise_music_db" in result:
        over = result.get("user_music_db")
        if over is None:
            out.append("Over the music your voice was not 10 dB above it: expect misheard words.")
        elif over - result["noise_music_db"] < MIN_SNR_DB:
            out.append(
                f"Over the music your voice is only {over - result['noise_music_db']:.0f} dB above "
                f"the noise (under {MIN_SNR_DB:.0f}): expect misheard words."
            )
    if "bg_voice_db" in result:
        if "gate_db" in result:
            out.append(
                f"Loudness gate: use --voice-gate-db auto ({result['gate_db']:.0f} dB; the "
                f"background voice is {user - result['bg_voice_db']:.0f} dB below you)."
            )
        else:
            out.append(
                f"The background voice is only {user - result['bg_voice_db']:.0f} dB below you: "
                f"the loudness gate cannot tell it from you. Lower it, move it away, or move "
                f"the microphone closer to you."
            )
    return out


def load_calibration(path: Path = PATH) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
        out: dict[str, Any] = {"when": str(data["when"])}
        for key in ("noise_db", "user_db", *OPTIONAL_KEYS):
            if key in data:
                out[key] = float(data[key])
        if "noise_db" not in out or "user_db" not in out:
            return None
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return None


def describe(cal: dict[str, Any] | None) -> str:
    if cal is None:
        return "no room calibration (out/calibration.json): defaults"
    music = f", music {cal['music_db']:.0f}" if "music_db" in cal else ""
    gate = f", gate {cal['gate_db']:.0f} dB" if "gate_db" in cal else ""
    return (
        f"room calibration from {cal['when']}: noise {cal['noise_db']:.0f}, "
        f"speech {cal['user_db']:.0f}{music} dBFS{gate}"
    )


def music_gate_db(cal: dict[str, Any] | None) -> float | None:
    """Speech must be louder than this to pause the music: halfway between the user's level and
    the music's leak. None without a calibration that has both."""
    if cal is None or "music_db" not in cal:
        return None
    return float((cal["user_db"] + cal["music_db"]) / 2.0)


def _record(seconds: float, device: int | str | None = None) -> bytes:
    import sounddevice as sd

    pcm = sd.rec(
        int(seconds * RATE),
        samplerate=RATE,
        channels=1,
        dtype="int16",
        blocking=True,
        device=device,
    )
    return bytes(pcm.tobytes())


class SpotifyBridge:
    """Pipes the Spotify app's sound to the Pi speaker while calibrating, as the client does
    (doc_chat.music.MusicPlayer): the app's stream goes into our null sink, the sink is captured and
    sent to the speaker. Without a Pi speaker URL nothing is piped and the music is measured as
    it plays."""

    def __init__(self, url: str | None, token: str | None = None) -> None:
        self.url = url
        self.token = token
        self.player: Any = None

    def start(self) -> None:
        if not self.url:
            return
        from doc_chat.music import MusicPlayer

        url, token = self.url, self.token

        def connect() -> Any:
            from websockets.sync.client import connect as ws_connect

            headers = {"Authorization": f"Bearer {token}"} if token else {}
            return ws_connect(url, additional_headers=headers, open_timeout=5).__enter__()

        self.player = MusicPlayer(connect)

    def wait_playing(self, seconds: float = 20.0) -> bool:
        """Route the app's stream into the sink and start piping; True once it plays."""
        from doc_chat.music import route_spotify

        if self.player is None:
            return False
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                route_spotify()
                if self.player.playing or self.player.adopt_spotify():
                    return True
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        return bool(self.player.playing)

    def stop(self) -> None:
        if self.player is not None:
            self.player.close()  # also pauses the app
            self.player = None


def measure_music(
    record: Callable[[float], bytes],
    ask: Callable[[str], Any],
    say: Callable[[str], Any],
    bridge: SpotifyBridge,
    prompts: tuple[str, ...],
    noise: float,
) -> dict[str, float]:
    """The music's leak, the noise floor with music on, and your voice over the music."""
    ask("Start a song in Spotify at the volume you would use, then press Enter... ")
    bridge.start()
    if bridge.url:
        if bridge.wait_playing():
            say("  Spotify is playing through the Pi speaker.")
        else:
            say("  Spotify's sound was not found in the Pi path: measuring what the room hears.")
    ask("Stay quiet for 8 seconds, press Enter... ")
    levels = block_levels(record(8.0))
    out = {"music_db": round(music_db(levels), 1), "noise_music_db": round(noise_db(levels), 1)}
    say(
        f"  music leak {out['music_db']:.1f} dBFS, "
        f"noise floor with music {out['noise_music_db']:.1f}"
    )
    spoken: list[float] = []
    for line in prompts:
        ask(f'Press Enter, then say over the music: "{line}" ')
        spoken += block_levels(record(3.5))
    try:
        out["user_music_db"] = round(speech_db(spoken, out["noise_music_db"]), 1)
        say(f"  your voice over the music {out['user_music_db']:.1f} dBFS")
    except ValueError:
        say("  your voice was never 10 dB above the music")
    bridge.stop()
    return out


def measure_background(
    record: Callable[[float], bytes], ask: Callable[[str], Any], say: Callable[[str], Any]
) -> float | None:
    """A background voice at the volume it would have, measured the way the gate measures a turn."""
    ask(
        "Play a talk video or podcast at the volume it would have (no music), "
        "stay quiet, press Enter... "
    )
    level = turn_level_db(record(8.0))
    if level is None or level < SILENCE_DB:
        say("  no background voice heard")
        return None
    say(f"  background voice {level:.1f} dBFS")
    return round(level, 1)


def _input_name(device: int | str | None) -> str:
    import sounddevice as sd

    return str(sd.query_devices(device, kind="input")["name"])


def _save_wav(path: Path, pcm: bytes) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def main(argv: list[str] | None = None) -> int:
    from doc_chat.speech import mic_device

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--music", action="store_true", help="also measure the music leak (8 s)")
    ap.add_argument(
        "--full",
        action="store_true",
        help="also measure the setup as used: Spotify through the Pi speaker (the music's leak, "
        "the noise with music, your voice over it) and a background voice (for the loudness gate)",
    )
    ap.add_argument(
        "--play-remote",
        default=None,
        help="the Pi speaker's websocket (ws://PI:8780) to pipe Spotify to; the token is read "
        "from LAN_SPEECH_TOKEN",
    )
    ap.add_argument("--out", default=str(PATH))
    ap.add_argument(
        "--mic-device",
        default=None,
        help="input device, a sounddevice index or part of its name (default: the system default)",
    )
    ap.add_argument(
        "--baseline",
        action="store_true",
        help="say the six baseline phrases instead of the three prompts and keep the audio "
        f"({BASELINE_WAV})",
    )
    ap.add_argument("--baseline-wav", default=str(BASELINE_WAV))
    args = ap.parse_args(argv)
    device = mic_device(args.mic_device)
    print(f"Microphone: {_input_name(device)}", flush=True)
    print("Quiet, please: measuring the room for 3 s...", flush=True)
    quiet = _record(3.0, device)
    noise = noise_db(block_levels(quiet))
    print(f"  noise floor {noise:.1f} dBFS")
    speech: list[float] = []
    kept = bytearray(quiet)
    for line in BASELINE_PROMPTS if args.baseline else PROMPTS:
        input(f'Press Enter, then say: "{line}" ')
        pcm = _record(3.5, device)
        kept += pcm
        speech += block_levels(pcm)
    user = speech_db(speech, noise)
    print(f"  your speech {user:.1f} dBFS")
    result: dict[str, Any] = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "noise_db": round(noise, 1),
        "user_db": round(user, 1),
    }
    if args.music:
        input("Start a song at your usual volume, stay quiet, press Enter... ")
        result["music_db"] = round(music_db(block_levels(_record(8.0, device))), 1)
        print(f"  music leak {result['music_db']:.1f} dBFS")
    if args.full:
        prompts = BASELINE_PROMPTS[:3] if args.baseline else PROMPTS
        bridge = SpotifyBridge(args.play_remote, os.environ.get("LAN_SPEECH_TOKEN"))
        try:
            result |= measure_music(
                lambda s: _record(s, device), input, print, bridge, prompts, noise
            )
        finally:
            bridge.stop()
        background = measure_background(lambda s: _record(s, device), input, print)
        if background is not None:
            result["bg_voice_db"] = background
            gate = recommend_gate_db(user, background)
            if gate is not None:
                result["gate_db"] = gate
        for line in advice(result):
            print(f"  ! {line}")
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {path}")
    if args.baseline:
        _save_wav(Path(args.baseline_wav), bytes(kept))
        print(f"wrote {args.baseline_wav} (3 s quiet, then the six phrases at 3.5 s each)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
