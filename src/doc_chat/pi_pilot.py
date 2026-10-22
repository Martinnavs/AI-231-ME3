"""A live barge-in pilot for the Raspberry Pi: the main design's gate, run on a real room.

The assistant speaks (standard clips with random gaps) through the speaker while the microphone
is listened to. The person holds a key while talking; the key events are the ground truth for
*when they started*. The gate is the one in main's design (ticket 12): on the first Silero speech
window above 0.7 the assistant ducks 15 dB; once speech has held for 250 ms it fades out in
15 ms and stops; if the speech ends first it comes back. Everything is timestamped on one clock.

Pure parts (``Gate``, ``Assistant``, ``analyze``) have no hardware; ``Pilot`` takes the same
``stream_factory`` as ``pi_check.Duplex`` so it runs on the simulated sound card too.
"""

from __future__ import annotations

import fcntl
import json
import os
import queue
import select
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from . import pi_check as pc
from .bargein import SileroOnset

WINDOW = SileroOnset.WINDOW
WINDOW_S = WINDOW / pc.SR

KEY_CODES = {"LEFTCTRL": 29, "RIGHTCTRL": 97, "N": 49, "M": 50, "SPACE": 57, "CAPSLOCK": 58}
_EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux
_EVIOCSCLOCKID = 0x400445A0
_EVIOCGRAB = 0x40044590
_EV_KEY = 1


# ----------------------------------------------------------------------------- the gate


@dataclass
class GateConfig:
    confidence: float = 0.7  # Pipecat's Silero default
    gate_s: float = 0.25  # main's min_barge_in_ms
    duck_db: float = -15.0
    duck_ramp_s: float = 0.005
    fade_s: float = 0.015
    restore_ramp_s: float = 0.045  # 3 x the fade, as in the ticket
    rule: str = "strict"  # strict: gate_s of unbroken speech; kofn: k of the last n windows
    k: int = 7
    n: int = 10


class Gate:
    """Duck / restore / interrupt from a stream of Silero confidences (one per 32 ms window).

    Only acts while the assistant is speaking. 'Continuous' means any window below the
    confidence resets the count, as in Pipecat. With ``rule='kofn'`` the interrupt instead
    needs a speech window and at least ``k`` speech windows among the last ``n``; duck and
    restore are unchanged."""

    def __init__(self, cfg: GateConfig | None = None) -> None:
        self.cfg = cfg or GateConfig()
        self.heard = 0.0
        self.state = "idle"  # idle | ducked | interrupted
        self.recent: deque[bool] = deque(maxlen=self.cfg.n)

    def reset(self) -> None:
        self.heard = 0.0
        self.state = "idle"
        self.recent.clear()

    def step(self, conf: float, speaking: bool) -> str | None:
        if not speaking:
            self.reset()
            return None
        if self.state == "interrupted":
            return None
        hit = conf >= self.cfg.confidence
        self.recent.append(hit)
        if hit:
            self.heard += WINDOW_S
            event = None
            if self.state == "idle":
                self.state = "ducked"
                event = "duck"
            if self.cfg.rule == "kofn":
                done = sum(self.recent) >= self.cfg.k
            else:
                done = self.heard >= self.cfg.gate_s - 1e-9
            if done:
                self.state = "interrupted"
                event = "interrupt"
            return event
        self.heard = 0.0
        if self.state == "ducked":
            self.state = "idle"
            return "restore"
        return None


# ----------------------------------------------------------------------------- the assistant


class Assistant:
    """Plays clips with random gaps and obeys duck / restore / interrupt commands.

    ``render`` is called from the audio callback for each output block."""

    def __init__(
        self,
        clips: dict[str, Any],
        rate: int,
        cfg: GateConfig | None = None,
        seed: int = 0,
        lead_s: float = 3.0,
        gap_s: tuple[float, float] = (0.5, 1.5),
    ) -> None:
        self.rate = rate
        self.cfg = cfg or GateConfig()
        self.clips = {k: pc.resample(v, pc.SR, rate) for k, v in clips.items()}
        self.rng = np.random.default_rng(seed)
        self.gap_s = gap_s
        self.state = "gap"  # gap | speaking | waiting (after an interrupt)
        self.gap_left = int(lead_s * rate)
        self.name = ""
        self.clip: Any = None
        self.pos = 0
        self.gain = 1.0
        self.target = 1.0
        self.step = 0.0
        self.interrupting = False
        self.stopped_t = 0.0
        self.events: list[dict[str, Any]] = []
        self._order: list[str] = []
        self._cmds: list[tuple[str, float, float]] = []
        self._lock = threading.Lock()

    @property
    def speaking(self) -> bool:
        return self.state == "speaking"

    def command(self, kind: str, t_detect: float, t_cmd: float) -> None:
        with self._lock:
            self._cmds.append((kind, t_detect, t_cmd))

    def resume(self) -> None:
        self.state = "gap"
        self.gap_left = int(self.rng.uniform(*self.gap_s) * self.rate)
        self.gain = self.target = 1.0
        self.step = 0.0
        self.interrupting = False

    def _ramp(self, target: float, seconds: float) -> None:
        self.target = target
        self.step = (target - self.gain) / max(seconds * self.rate, 1.0)

    def _apply(self, kind: str, t_detect: float, t_cmd: float, t_applied: float) -> None:
        ok = self.state == "speaking" and not self.interrupting
        if kind == "duck" and ok:
            self._ramp(10 ** (self.cfg.duck_db / 20), self.cfg.duck_ramp_s)
        elif kind == "restore" and ok:
            self._ramp(1.0, self.cfg.restore_ramp_s)
        elif kind == "interrupt" and ok:
            self.interrupting = True
            self._ramp(0.0, self.cfg.fade_s)
        else:
            kind += "-ignored"
        self.events.append(
            {"kind": kind, "t_detect": t_detect, "t_cmd": t_cmd, "t_applied": t_applied}
        )

    def _start_clip(self, t: float) -> None:
        if not self._order:
            self._order = list(self.clips)
            self.rng.shuffle(self._order)
        self.name = self._order.pop()
        self.clip = self.clips[self.name]
        self.pos = 0
        self.state = "speaking"
        self.events.append({"kind": "clip_start", "t": t, "name": self.name})

    def render(self, frames: int, t_out: float) -> Any:
        out = np.zeros(frames, np.float32)
        with self._lock:
            cmds, self._cmds = self._cmds, []
        for kind, t_detect, t_cmd in cmds:
            self._apply(kind, t_detect, t_cmd, t_out)
        i = 0
        while i < frames and self.state != "waiting":
            if self.state == "gap":
                n = min(frames - i, self.gap_left)
                self.gap_left -= n
                i += n
                if self.gap_left <= 0:
                    self._start_clip(t_out + i / self.rate)
                continue
            n = min(frames - i, len(self.clip) - self.pos)
            if self.step == 0.0:
                g = np.full(n, self.gain, np.float32)
            else:
                g = self.gain + self.step * np.arange(1, n + 1, dtype=np.float32)
                g = np.minimum(g, self.target) if self.step > 0 else np.maximum(g, self.target)
                self.gain = float(g[-1])
                if self.gain == self.target:
                    self.step = 0.0
            out[i : i + n] = self.clip[self.pos : self.pos + n] * g
            self.pos += n
            if self.interrupting and self.gain <= 0.0:
                j = int(np.argmax(g <= 0.0))
                self.stopped_t = t_out + (i + j) / self.rate
                self.events.append({"kind": "stopped", "t": self.stopped_t, "name": self.name})
                self.state = "waiting"
            elif self.pos >= len(self.clip):
                self.events.append({"kind": "clip_end", "t": t_out + (i + n) / self.rate})
                self.state = "gap"
                self.gap_left = int(self.rng.uniform(*self.gap_s) * self.rate)
            i += n
        return out


# ----------------------------------------------------------------------------- the keyboard


@dataclass
class KeyEvent:
    t: float  # CLOCK_MONOTONIC seconds
    code: int
    value: int  # 1 down, 0 up


class KeySource(Protocol):
    def drain(self, now: float) -> list[KeyEvent]: ...
    def close(self) -> None: ...


class EvdevKeys:
    """Raw key events from /dev/input/event*, stamped by the kernel on CLOCK_MONOTONIC.
    The device is grabbed so the keys do not also type into whatever has focus."""

    def __init__(self, path: str, grab: bool = True) -> None:
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, _EVIOCSCLOCKID, struct.pack("i", time.CLOCK_MONOTONIC))
        if grab:
            fcntl.ioctl(self.fd, _EVIOCGRAB, 1)
        self._q: queue.SimpleQueue[KeyEvent] = queue.SimpleQueue()
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop:
            if not select.select([self.fd], [], [], 0.1)[0]:
                continue
            try:
                data = os.read(self.fd, _EVENT.size * 32)
            except BlockingIOError:
                continue
            for off in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
                sec, usec, typ, code, value = _EVENT.unpack_from(data, off)
                if typ == _EV_KEY and value in (0, 1):
                    self._q.put(KeyEvent(sec + usec / 1e6, code, value))

    def drain(self, now: float) -> list[KeyEvent]:
        out = []
        while not self._q.empty():
            out.append(self._q.get_nowait())
        return out

    def close(self) -> None:
        self._stop = True
        self._thread.join(timeout=1)
        os.close(self.fd)


class ScriptedKeys:
    """Key events at known times on the stream's own clock (for tests and the dry run)."""

    def __init__(self, events: list[KeyEvent]) -> None:
        self._events = sorted(events, key=lambda e: e.t)

    def drain(self, now: float) -> list[KeyEvent]:
        out = [e for e in self._events if e.t <= now]
        self._events = [e for e in self._events if e.t > now]
        return out

    def close(self) -> None:
        pass


# ----------------------------------------------------------------------------- the pilot


class Decimator:
    """Streaming low-pass and downsample to 16 kHz (exact for rates that are a multiple of it)."""

    def __init__(self, rate: int) -> None:
        from scipy.signal import firwin

        self.rate = rate
        self.q = rate // pc.SR if rate % pc.SR == 0 else 0
        self.taps = firwin(63, 7000 / (rate / 2)) if self.q > 1 else None
        self.zi = np.zeros(62) if self.q > 1 else None
        self.n = 0

    def feed(self, x: Any) -> Any:
        if self.q == 1:
            return x
        if not self.q:
            return pc.resample(x, self.rate, pc.SR)
        from scipy.signal import lfilter

        y, self.zi = lfilter(self.taps, 1.0, x, zi=self.zi)
        first = (-self.n) % self.q
        self.n += len(x)
        return y[first :: self.q].astype(np.float32)


class Pilot:
    def __init__(
        self,
        rate: int,
        in_device: Any,
        out_device: Any,
        out_channels: int,
        keys: KeySource,
        silero_path: str,
        assistant: Assistant | None = None,
        hold_key: int = KEY_CODES["RIGHTCTRL"],
        noise_key: int = KEY_CODES["N"],
        cfg: GateConfig | None = None,
        stream_factory: Callable[..., Any] | None = None,
        threaded: bool = True,
    ) -> None:
        if stream_factory is None:
            import sounddevice as sd

            stream_factory = sd.Stream
        self.rate, self.in_device, self.out_device = rate, in_device, out_device
        self.out_channels = out_channels
        self.keys, self.assistant = keys, assistant
        self.hold_key, self.noise_key = hold_key, noise_key
        self.cfg = cfg or GateConfig()
        self.gate = Gate(self.cfg)
        self._factory = stream_factory
        self.threaded = threaded
        self.det = SileroOnset(
            silero_path, confidence=self.cfg.confidence, min_s=1e9, keep_history=True
        )
        self.dec = Decimator(rate)
        self.events: list[dict[str, Any]] = []
        self.conf: list[
            tuple[float, float, bool]
        ] = []  # (window end t, confidence, assistant speaking)
        self.mic16: list[Any] = []
        self.underruns = 0
        self.hold_down = False
        self.t0: float | None = None
        self._off: float | None = None
        self._t_mic0: float | None = None
        self._frames = 0
        self._seen = 0
        self._k = 0
        self._done = threading.Event()
        self._total = 0
        self._work: queue.SimpleQueue[tuple[Any, float] | None] = queue.SimpleQueue()

    # -- audio callback
    def _times(self, time_info: Any) -> tuple[float, float]:
        if time_info is None:  # simulated card: the sample clock
            t = self._frames / self.rate
            return t, t
        off = time.monotonic() - time_info.currentTime
        self._off = off if self._off is None else min(self._off, off)
        return time_info.inputBufferAdcTime + self._off, time_info.outputBufferDacTime + self._off

    def _callback(
        self, indata: Any, outdata: Any, frames: int, time_info: Any, status: Any
    ) -> None:
        t_in, t_out = self._times(time_info)
        if self.t0 is None:
            self.t0 = t_in
        if status and getattr(status, "output_underflow", False):
            self.underruns += 1
        block = indata[:frames, 0].copy()
        now = t_in + frames / self.rate
        for ev in self.keys.drain(now):
            self.events.append({"kind": "key", "code": ev.code, "value": ev.value, "t": ev.t})
            if ev.code == self.hold_key:
                self.hold_down = ev.value == 1
        a = self.assistant
        if a is not None and a.state == "waiting":
            since = t_in - a.stopped_t
            if (not self.hold_down and since >= 1.0) or since >= 3.0:
                a.resume()
                self.gate.reset()
        if self.threaded:
            self._work.put((block, t_in))
        else:
            self._process(block, t_in, now)
        outdata[:, :] = 0
        if a is not None:
            outdata[:frames, :] = a.render(frames, t_out)[:, None]
        self._frames += frames
        if self._frames >= self._total:
            self._done.set()

    # -- the detector side
    def _process(self, block: Any, t_in: float, now: float) -> None:
        y = self.dec.feed(block)
        if self._t_mic0 is None:
            self._t_mic0 = t_in
        self.mic16.append(y)
        self.det.feed((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
        new = self.det.history[self._seen :] if self.det.history is not None else []
        self._seen += len(new)
        a = self.assistant
        for conf in new:
            self._k += 1
            t_end = self._t_mic0 + self._k * WINDOW_S
            speaking = a.speaking if a is not None else False
            self.conf.append((t_end, conf, speaking))
            event = self.gate.step(conf, speaking)
            if event and a is not None:
                a.command(event, t_end, now)

    def _worker(self) -> None:
        while (item := self._work.get()) is not None:
            block, t_in = item
            self._process(block, t_in, time.monotonic())

    def run(self, seconds: float) -> None:
        self._total = int(seconds * self.rate)
        worker = None
        if self.threaded:
            worker = threading.Thread(target=self._worker, daemon=True)
            worker.start()
        stream = self._factory(
            samplerate=self.rate,
            blocksize=self.rate // 50,
            device=(self.in_device, self.out_device),
            channels=(1, self.out_channels),
            dtype="float32",
            callback=self._callback,
        )
        try:
            with stream:
                self._done.wait(timeout=seconds + 15)
        finally:
            self.keys.close()
            if worker is not None:
                self._work.put(None)
                worker.join(timeout=10)

    # -- results
    def save(self, folder: Path, meta: dict[str, Any]) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        t0 = self.t0 or 0.0
        events = list(self.events) + (self.assistant.events if self.assistant else [])
        for ev in events:
            for k in ("t", "t_detect", "t_cmd", "t_applied"):
                if k in ev:
                    ev[k] = round(ev[k] - t0, 4)
        events.sort(key=lambda e: e.get("t", e.get("t_applied", 0.0)))
        mic = np.concatenate(self.mic16) if self.mic16 else np.zeros(0, np.float32)
        pc.write_wav(folder / "mic.wav", mic)
        (folder / "events.json").write_text(json.dumps(events, indent=1))
        t_conf = (self._t_mic0 if self._t_mic0 is not None else t0) - t0
        (folder / "conf.json").write_text(
            json.dumps(
                {
                    "t": [round(c[0] - t0, 4) for c in self.conf],
                    "conf": [round(c[1], 3) for c in self.conf],
                    "speaking": [c[2] for c in self.conf],
                }
            )
        )
        meta = {
            **meta,
            "mic_first_sample_t": round(t_conf, 4),
            "underruns": self.underruns,
            "gate": self.cfg.__dict__,
            "hold_key": self.hold_key,
            "noise_key": self.noise_key,
            "hardware": pc.hardware_info(),
        }
        (folder / "run.json").write_text(json.dumps(meta, indent=1))


# ----------------------------------------------------------------------------- analysis


def _holds(
    events: list[dict[str, Any]], code: int, debounce_s: float = 0.3
) -> list[tuple[float, float]]:
    """(down, up) pairs for one key. This keyboard chatters (runs of 10-30 ms release/press pairs
    while a key is held), so presses closer together than ``debounce_s`` are one hold."""
    raw, down = [], None
    for ev in events:
        if ev["kind"] == "key" and ev["code"] == code:
            if ev["value"] == 1 and down is None:
                down = ev["t"]
            elif ev["value"] == 0 and down is not None:
                raw.append((down, ev["t"]))
                down = None
    out: list[tuple[float, float]] = []
    for d, u in raw:
        if out and d - out[-1][1] < debounce_s:
            out[-1] = (out[-1][0], u)
        else:
            out.append((d, u))
    return out


def _presses(events: list[dict[str, Any]], code: int) -> list[float]:
    return [e["t"] for e in events if e["kind"] == "key" and e["code"] == code and e["value"] == 1]


def _load(folder: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    meta = json.loads((folder / "run.json").read_text())
    events = json.loads((folder / "events.json").read_text())
    conf = json.loads((folder / "conf.json").read_text())
    return meta, events, conf


def key_to_voice_offsets(folder: Path, threshold: float = 0.5) -> dict[str, Any]:
    """From a calibration run (no assistant): seconds from key-down to the first speech window."""
    meta, events, conf = _load(folder)
    t = np.array(conf["t"]) - WINDOW_S  # window starts
    c = np.array(conf["conf"])
    offsets = []
    for down, up in _holds(events, meta["hold_key"]):
        idx = np.nonzero((t >= down - 0.5) & (t <= up) & (c >= threshold))[0]
        if len(idx):
            offsets.append(float(t[idx[0]] - down))
    if not offsets:
        return {"n": 0, "holds": len(_holds(events, meta["hold_key"]))}
    a = np.array(offsets)
    return {
        "n": len(a),
        "holds": len(_holds(events, meta["hold_key"])),
        "median_s": round(float(np.median(a)), 3),
        "p10_s": round(float(np.percentile(a, 10)), 3),
        "p90_s": round(float(np.percentile(a, 90)), 3),
        "offsets_s": [round(x, 3) for x in offsets],
    }


def analyze(folder: Path, offset_s: float = 0.0) -> dict[str, Any]:
    """Detection rate and latency of the gate against the key-down times (plus ``offset_s`` for
    the key-to-voice delay), and the false ducks / interrupts per minute of assistant speech."""
    meta, events, conf = _load(folder)
    holds = _holds(events, meta["hold_key"])
    noise = _presses(events, meta["noise_key"])
    win_start = np.array(conf["t"]) - WINDOW_S
    win_conf = np.array(conf["conf"])

    def voice_onset(down: float, up: float) -> float | None:
        """First window at 0.5 confidence inside the hold: where the voice really starts. Only
        meaningful when the assistant does not trip the detector by itself (checked by the
        false-trigger count)."""
        idx = np.nonzero((win_start >= down - 0.5) & (win_start <= up) & (win_conf >= 0.5))[0]
        return float(win_start[idx[0]]) if len(idx) else None

    spans, start = [], None
    for ev in events:
        if ev["kind"] == "clip_start":
            start = ev["t"]
        elif ev["kind"] in ("clip_end", "stopped") and start is not None:
            spans.append((start, ev["t"]))
            start = None
    gate = {k: [e for e in events if e["kind"] == k] for k in ("duck", "restore", "interrupt")}

    def speaking_at(t: float) -> bool:
        return any(s <= t <= e for s, e in spans)

    def first(kind: str, lo: float, hi: float) -> dict[str, Any] | None:
        return next((e for e in gate[kind] if lo <= e["t_detect"] <= hi), None)

    over: list[dict[str, Any]] = []
    quiet = 0
    refined = 0
    for down, up in holds:
        heard = voice_onset(down, up)
        onset = heard if heard is not None else down + offset_s
        refined += heard is not None
        if not speaking_at(onset):
            quiet += 1
            continue
        duck, intr = first("duck", down - 0.3, up + 0.5), first("interrupt", down - 0.3, up + 0.5)
        over.append(
            {
                "onset_s": round(onset, 2),
                "onset_from_audio": heard is not None,
                "ducked": duck is not None,
                "interrupted": intr is not None,
                "duck_latency_s": round(duck["t_applied"] - onset, 3) if duck else None,
                "interrupt_latency_s": round(intr["t_applied"] - onset, 3) if intr else None,
                "interrupt_processing_s": round(intr["t_applied"] - intr["t_detect"], 3)
                if intr
                else None,
            }
        )
    windows = [(d - 0.3, u + 0.5) for d, u in holds] + [(t - 0.3, t + 1.5) for t in noise]
    false = {
        k: sum(1 for e in gate[k] if not any(lo <= e["t_detect"] <= hi for lo, hi in windows))
        for k in ("duck", "interrupt")
    }
    speech_min = sum(e - s for s, e in spans) / 60
    noise_out = []
    for t in noise:
        noise_out.append(
            {
                "t": round(t, 2),
                "assistant_speaking": speaking_at(t),
                "ducked": first("duck", t - 0.3, t + 1.5) is not None,
                "restored": first("restore", t - 0.3, t + 1.5) is not None,
                "interrupted": first("interrupt", t - 0.3, t + 1.5) is not None,
            }
        )

    def stat(key: str) -> dict[str, float] | None:
        v = np.array([o[key] for o in over if o[key] is not None])
        if not len(v):
            return None
        return {
            "median": round(float(np.median(v)), 3),
            "p90": round(float(np.percentile(v, 90)), 3),
            "max": round(float(v.max()), 3),
        }

    return {
        "label": meta.get("label"),
        "offset_s": offset_s,
        "holds": len(holds),
        "holds_over_assistant_speech": len(over),
        "holds_in_assistant_pauses": quiet,
        "onsets_from_audio": refined,
        "ducked": sum(o["ducked"] for o in over),
        "interrupted": sum(o["interrupted"] for o in over),
        "duck_latency_s": stat("duck_latency_s"),
        "interrupt_latency_s": stat("interrupt_latency_s"),
        "interrupt_processing_s": stat("interrupt_processing_s"),
        "false_per_assistant_minute": {
            k: round(v / speech_min, 2) if speech_min else None for k, v in false.items()
        },
        "false_counts": false,
        "assistant_speech_min": round(speech_min, 2),
        "noise_presses": noise_out,
        "underruns": meta.get("underruns"),
        "per_hold": over,
    }
