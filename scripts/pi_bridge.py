"""Pi-side speech bridge: the Pi's microphone and speaker, the laptop's STT and TTS.

Run on the Pi. Audio never leaves the Pi except over the LAN to `lan_speech` on the laptop; the
only model on the Pi is the Silero VAD (barge-in). Each turn: listen (the server decides when the
turn ends), say what was heard back through the Pi speaker, and stop if the user talks over it.

    LAN_SPEECH_TOKEN=... uv run python scripts/pi_bridge.py --server ws://<laptop>:8765 \
        --in-device UACDemo --out-device CD002   # both USB devices open at 48 kHz

Needs the base dependencies + `--group pi` + `websockets` (see docs/PI-BRIDGE.md). No language
model: the reply is "You said: <transcript>" so the whole loop is STT -> TTS only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doc_chat.bargein import PreRoll, SileroOnset  # noqa: E402
from doc_chat.playback import Player  # noqa: E402
from doc_chat.speech import DocChatSpeechClient, MicHub, NoSpeech, with_lead_in  # noqa: E402

QUIT_WORDS = {"quit", "exit", "goodbye"}
T0 = time.monotonic()
STATE = {"phase": "start"}


def log(msg: str) -> None:
    print(f"[{time.monotonic() - T0:7.1f}s] {msg}", flush=True)


async def heartbeat(hub: MicHub, player: Player, every: float = 2.0) -> None:
    """A status line every few seconds, so a stall shows which phase it is in."""
    peak = 0.0
    while True:
        for _ in range(int(every / 0.1)):
            await asyncio.sleep(0.1)
            peak = max(peak, float(getattr(hub, "level", 0.0)))
        db = 20 * math.log10(peak) if peak > 0 else -99.0
        log(f"  .. {STATE['phase']} mic_peak={db:.0f}dBFS backlog={hub._queue.qsize()}")
        peak = 0.0


def record_latency(
    path: str | None, heard: Any, marks: dict[str, float], player: Player, hit: bool
) -> None:
    """One JSON line per turn: where the time between 'user stopped' and 'sound' went (ms)."""
    if not path or "first_chunk" not in marks or heard.finalize_sent is None:
        return

    def ms(a: float, b: float) -> float:
        return round((b - a) * 1000.0, 1)

    out_ms = float(getattr(player._stream, "latency", 0.0)) * 1000.0
    row = {
        "text": heard.text,
        "words": len(heard.text.split()),
        "pause_hold": ms(heard.t_speech_end, heard.finalize_sent),
        "server_final": ms(heard.finalize_sent, heard.t_final),
        "dispatch": ms(heard.t_final, marks["call"]),
        "tts_first_chunk": ms(marks["call"], marks["first_chunk"]),
        "to_first_chunk": ms(heard.t_speech_end, marks["first_chunk"]),
        "device_out_latency": round(out_ms, 1),
        "to_sound_est": round(ms(heard.t_speech_end, marks["first_chunk"]) + out_ms, 1),
        "interrupted": hit,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


def dump_tasks() -> None:
    """On SIGUSR1: print where every asyncio task is waiting."""
    for task in asyncio.all_tasks():
        log(f"TASK {task.get_name()} done={task.done()}")
        for frame in task.get_stack(limit=4):
            name = frame.f_code.co_filename.rsplit("/", 1)[-1]
            log(f"    {name}:{frame.f_lineno} {frame.f_code.co_name}")


class ResamplingPlayer(Player):
    """Player for a device that cannot open at the TTS rate (the Pi's USB speaker: 48 kHz only).

    Each pushed chunk is resampled with a polyphase filter; chunks are whole sentences or large
    pieces, so the small edge effect at chunk joins is not audible."""

    def __init__(self, tts_rate: int, device_rate: int) -> None:
        super().__init__(device_rate)
        from math import gcd

        g = gcd(tts_rate, device_rate)
        self._up, self._down = device_rate // g, tts_rate // g

    def push(self, pcm: bytes) -> None:
        import numpy as np
        from scipy.signal import resample_poly

        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float64)
        y = resample_poly(x, self._up, self._down)
        super().push(np.clip(y, -32768, 32767).astype(np.int16).tobytes())


class DecimatingMicHub(MicHub):
    """MicHub for a device that only opens at a multiple of 16 kHz (the Pi's USB mic: 48 kHz).

    Blocks are low-passed with a filter that keeps its state across blocks, then every
    ``factor``-th sample is kept, so the rest of the client sees ordinary 16 kHz blocks.
    """

    def __init__(self, device_rate: int = 48000, block_ms: int = 100) -> None:
        super().__init__(rate=16000, block_ms=block_ms)
        if device_rate % 16000:
            raise ValueError("device rate must be a multiple of 16 kHz")
        self.device_rate = device_rate
        self.level = 0.0  # RMS (0-1) of the newest block, for the status line

    def start(self) -> None:
        import numpy as np
        import sounddevice as sd
        from scipy.signal import butter, sosfilt, sosfilt_zi

        factor = self.device_rate // 16000
        sos = butter(8, 7000, btype="low", fs=self.device_rate, output="sos")
        zi = sosfilt_zi(sos) * 0.0
        loop = asyncio.get_running_loop()
        block = self.device_rate * self.block_ms // 1000

        def on_audio(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            nonlocal zi
            x = np.frombuffer(bytes(indata), dtype=np.int16).astype(np.float64)
            y, zi = sosfilt(sos, x, zi=zi)
            out = np.clip(y[::factor], -32768, 32767).astype(np.int16).tobytes()
            self.level = float(np.sqrt(np.mean((y[::factor] / 32768.0) ** 2)))
            loop.call_soon_threadsafe(self._queue.put_nowait, out)

        self._stream = sd.RawInputStream(
            samplerate=self.device_rate,
            channels=1,
            dtype="int16",
            blocksize=block,
            callback=on_audio,
        )
        self._stream.start()


def _device(arg: str | None) -> str | int | None:
    return None if not arg else (int(arg) if arg.isdigit() else arg)


async def _watch(hub: MicHub, silero: SileroOnset, roll: PreRoll, stop: asyncio.Event) -> bool:
    """True once the user's voice is heard (blocks kept in `roll`); False if `stop` is set first."""
    while not stop.is_set():
        try:
            block = await asyncio.wait_for(hub.get(), timeout=0.2)
        except TimeoutError:
            continue
        roll.push(block)
        if silero.feed(block):
            return True
    return False


async def speak(
    client: DocChatSpeechClient,
    player: Player,
    hub: MicHub,
    make_silero: Callable[[], SileroOnset],
    roll: PreRoll,
    text: str,
    marks: dict[str, float] | None = None,
) -> bool:
    """Say `text`; True if the user interrupted (output is already silent then).

    ``marks`` receives perf_counter times: ``call`` (speak sent) and ``first_chunk``."""
    marks = marks if marks is not None else {}
    rate = client.tts_sample_rate
    stop = asyncio.Event()
    hub.drain()
    watcher = asyncio.create_task(_watch(hub, make_silero(), roll, stop))
    t0 = marks["call"] = time.perf_counter()

    def on_chunk(pcm: bytes) -> None:
        marks.setdefault("first_chunk", time.perf_counter())
        player.push(pcm)

    speaker = asyncio.create_task(client.speak_text(text, on_chunk=on_chunk))
    interrupted = False
    try:
        await asyncio.wait({watcher, speaker}, return_when=asyncio.FIRST_COMPLETED)
        if watcher.done() and watcher.result():
            interrupted = True
        else:
            pcm, first_ms, total_ms, _ = await speaker
            secs = len(pcm) / 2 / rate
            log(f"      tts first {first_ms:.0f} ms, done {total_ms:.0f} ms, {secs:.1f} s of audio")
            end = t0 + first_ms / 1000 + len(pcm) / 2 / rate  # when the speaker will be silent
            while time.perf_counter() < end and not interrupted:
                done, _ = await asyncio.wait({watcher}, timeout=0.05)
                interrupted = bool(done and watcher.result())
    finally:
        stop.set()
        if interrupted:
            player.abort()
            await client.cancel_current()
        for task in (speaker, watcher):
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    return interrupted


async def run(args: argparse.Namespace) -> int:
    import sounddevice as sd

    sd.default.device = (_device(args.in_device), _device(args.out_device))
    client = DocChatSpeechClient(server_url=args.server)
    await client.connect()
    hub = DecimatingMicHub(args.in_rate) if args.in_rate != 16000 else MicHub()
    hub.start()
    client.hub = hub
    player: Player = (
        Player(client.tts_sample_rate)
        if args.out_rate == client.tts_sample_rate
        else ResamplingPlayer(client.tts_sample_rate, args.out_rate)
    )

    silero = SileroOnset(args.vad_model, confidence=args.confidence, min_s=args.min_s)

    def make_silero() -> SileroOnset:  # one loaded model; recurrent state cleared per reply
        silero.reset()
        return silero

    roll = PreRoll()
    asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, dump_tasks)
    beat = asyncio.create_task(heartbeat(hub, player))
    log(f"connected to {args.server}; speak (say 'goodbye' to quit)")
    lead: list[bytes] | None = None
    try:
        while True:
            audio = with_lead_in(lead, hub.blocks()) if lead else None
            lead = None
            STATE["phase"] = "listening"
            try:
                heard = await client.listen_turn(
                    vad_hold=args.pause,
                    audio=audio,
                    first_speech_timeout=args.idle,
                    on_partial=lambda t: log(f"  partial: {t!r}"),
                )
            except NoSpeech:
                continue
            log(f"heard: {heard.text!r}  (stt {heard.stt_ms:.0f} ms)")
            if heard.text.strip().lower().strip(".,!? ") in QUIT_WORDS:
                await speak(client, player, hub, make_silero, roll, "Goodbye.")
                return 0
            STATE["phase"] = "speaking"
            marks: dict[str, float] = {}
            hit = await speak(
                client, player, hub, make_silero, roll, f"You said: {heard.text}", marks
            )
            record_latency(args.latency_log, heard, marks, player, hit)
            if hit:
                log("      interrupted: listening")
                lead = roll.blocks()
    finally:
        beat.cancel()
        player.close()
        hub.stop()
        await client.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--server", required=True, help="ws://<laptop>:<port> of lan_speech")
    ap.add_argument(
        "--in-device", default="UACDemo", help="microphone: sounddevice name substring or index"
    )
    ap.add_argument("--in-rate", type=int, default=48000, help="mic rate (multiple of 16000)")
    ap.add_argument(
        "--out-device", default="CD002", help="speaker: sounddevice name substring or index"
    )
    ap.add_argument("--out-rate", type=int, default=48000, help="speaker rate the device opens at")
    ap.add_argument("--latency-log", help="append one JSON line of stage timings per turn")
    ap.add_argument("--vad-model", default="assets/silero_vad.onnx")
    ap.add_argument("--confidence", type=float, default=0.5)
    ap.add_argument("--min-s", type=float, default=0.25)
    ap.add_argument("--pause", type=float, default=0.6, help="client-side pause ending a turn (s)")
    ap.add_argument("--idle", type=float, default=None, help="re-arm after this many silent s")
    args = ap.parse_args(argv)
    if not os.environ.get("LAN_SPEECH_TOKEN") and "127.0.0.1" not in args.server:
        print("LAN_SPEECH_TOKEN is not set", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
