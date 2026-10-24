"""Echo cancellation across two devices: WebRTC's canceller with the audio we send as reference.

The assistant's voice comes out of another machine's speaker (the Pi) and returns through the
laptop microphone. The laptop knows exactly what it sent, so that stream is the reference.
`RemotePlayer` hands each slice it sends to `reference`; `MicHub` passes every microphone block
through `process`. Measured offline in docs/AEC-REMOTE-SPEAKER.md: the two sound cards drift about
137 ppm, which the canceller tracks by itself.

Both streams are fed in 10 ms frames in lockstep, as the canceller expects. The first slice of a
reply can start `lead_ms` of reference ahead; the default is none. AEC3 resets its render buffer
when the reference stays more than 32 ms ahead of the microphone for a second, and it finds
render-to-echo delays only up to about 0.5 s, so any lead uses up that range
(.scratch/echo-next/PLAN.md, E1: lead 0 left the least echo 1-2 s into a clip).
"""

from __future__ import annotations

import threading
from collections import deque

import numpy as np
from livekit import rtc

RATE = 16000
FRAME = 160  # 10 ms, what the canceller takes
FRAME_BYTES = FRAME * 2
SILENCE = bytes(FRAME_BYTES)


class EchoCanceller:
    """Remove the reference (what was sent to the speaker) from the microphone."""

    def __init__(self, lead_ms: int = 0) -> None:
        self._apm = rtc.AudioProcessingModule(echo_cancellation=True)
        self._apm.set_stream_delay_ms(lead_ms)
        self._lead = lead_ms // 10
        self._frames: deque[bytes] = deque(maxlen=300)
        self._pending = b""
        self._active = False
        self._lock = threading.Lock()
        self.gain = 1.0  # what the speaker is doing to the reference now (1.0 = nothing)

    def reference(self, pcm: bytes) -> None:
        """16-bit 16 kHz mono audio that is being sent to the speaker (any length, any thread)."""
        with self._lock:
            self._pending += pcm
            whole = len(self._pending) // FRAME_BYTES * FRAME_BYTES
            for i in range(0, whole, FRAME_BYTES):
                self._frames.append(self._pending[i : i + FRAME_BYTES])
            self._pending = self._pending[whole:]

    def clear(self) -> None:
        """Forget reference not yet used (the speaker was told to go silent)."""
        with self._lock:
            self._frames.clear()
            self._pending = b""

    def _render(self, frame: bytes) -> None:
        if self.gain != 1.0:
            x = np.frombuffer(frame, dtype=np.int16).astype(np.float32) * self.gain
            frame = np.clip(x, -32768, 32767).astype(np.int16).tobytes()
        self._apm.process_reverse_stream(rtc.AudioFrame(frame, RATE, 1, FRAME))

    def _feed_render(self) -> None:
        with self._lock:
            if self._frames and not self._active:
                self._active = True
                head = [self._frames.popleft() for _ in range(min(self._lead, len(self._frames)))]
            else:
                head = []
            frame = self._frames.popleft() if self._frames else None
            if frame is None:
                self._active = False
        for h in head:
            self._render(h)
        self._render(frame if frame is not None else SILENCE)

    def process(self, block: bytes) -> bytes:
        """The microphone block with the echo taken out (same length; a partial frame passes)."""
        out = bytearray()
        for i in range(0, len(block) - FRAME_BYTES + 1, FRAME_BYTES):
            self._feed_render()
            frame = rtc.AudioFrame(block[i : i + FRAME_BYTES], RATE, 1, FRAME)
            self._apm.process_stream(frame)
            out += bytes(frame.data)
        return bytes(out) + block[len(out) :]
