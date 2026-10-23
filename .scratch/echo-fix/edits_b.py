"""Edit lists for ticket B (duck, then stop), applied after ticket A."""

EDITS: dict[str, list[tuple[str, str]]] = {
    "src/doc_chat/playback.py": [
        (
            """class Player:
    \"\"\"Writes PCM to the speakers""",
            """def apply_gain(x: np.ndarray, start: float, end: float) -> np.ndarray:
    \"\"\"16-bit samples scaled by a gain that moves in a straight line from `start` to `end`.\"\"\"
    ramp = np.linspace(start, end, len(x), dtype=np.float32)
    scaled: np.ndarray = np.clip(x.astype(np.float32) * ramp, -32768, 32767).astype(np.int16)
    return scaled


class Player:
    \"\"\"Writes PCM to the speakers""",
        ),
        (
            """        self.underruns = 0  # slices the device had to wait for (heard as a stutter)
        self._pushed_s = 0.0  # seconds of audio queued so far: how long `close` may have to wait
        self._stream: Any = sd.RawOutputStream""",
            """        self.underruns = 0  # slices the device had to wait for (heard as a stutter)
        self._pushed_s = 0.0  # seconds of audio queued so far: how long `close` may have to wait
        self._gain = 1.0  # 1.0 = as pushed; set_gain ducks it
        self._stream: Any = sd.RawOutputStream""",
        ),
        (
            """        stopped = False
        last = 0  # the last sample handed to the device
""",
            """        stopped = False
        last = 0  # the last sample handed to the device
        applied = 1.0  # the gain at the end of the previous slice
""",
        ),
        (
            """                x = np.frombuffer(piece[: len(piece) // 2 * 2], dtype=np.int16)  # whole samples
                level = (""",
            """                x = np.frombuffer(piece[: len(piece) // 2 * 2], dtype=np.int16)  # whole samples
                if self._gain != 1.0 or applied != 1.0:  # ducked, or just let back up
                    x = apply_gain(x, applied, self._gain)
                    applied = self._gain
                    piece = x.tobytes()
                level = (""",
        ),
        (
            """    def push(self, pcm: bytes) -> None:
        self._pushed_s += len(pcm) / 2 / self._rate
        self._queue.put(pcm)
""",
            """    def set_gain(self, db: float) -> None:
        \"\"\"Turn the speaker down (negative dB) or back to normal (0), from the next slice on.\"\"\"
        self._gain = 10 ** (db / 20)

    def push(self, pcm: bytes) -> None:
        self._pushed_s += len(pcm) / 2 / self._rate
        self._queue.put(pcm)
""",
        ),
        (
            """        self._canceller = canceller  # gets what is sent, as 16 kHz audio, to cancel its echo
""",
            """        self._canceller = canceller  # gets what is sent, as 16 kHz audio, to cancel its echo
        self._gain = 1.0
        if canceller is not None:
            canceller.gain = 1.0
""",
        ),
        (
            """        return max((lv for t, lv in list(self._levels) if 0 <= now - t <= window_s), default=0.0)
""",
            """        loudest = max((lv for t, lv in list(self._levels) if 0 <= now - t <= window_s), default=0.0)
        return loudest * self._gain

    def set_gain(self, db: float) -> None:
        \"\"\"Turn the speaker down (negative dB) or back to normal (0), now: the Pi applies it to
        what it has not yet played, and the canceller's reference follows.\"\"\"
        self._gain = 10 ** (db / 20)
        if self._canceller is not None:
            self._canceller.gain = self._gain
        with contextlib.suppress(Exception):
            self._speaker.send(json.dumps({"type": "gain", "db": db}))
""",
        ),
    ],
    "scripts/pi_speaker.py": [
        (
            """                kind = json.loads(msg).get("type")
""",
            """                data = json.loads(msg)
                kind = data.get("type")
""",
        ),
        (
            """                elif kind == "end" and player is not None:
""",
            """                elif kind == "gain" and player is not None:
                    player.set_gain(float(data["db"]))
                elif kind == "end" and player is not None:
""",
        ),
    ],
    "src/doc_chat/bargein.py": [
        (
            """    def _confidence(self, window: object) -> float:
""",
            """    @property
    def recent_speech(self) -> int:
        \"\"\"Speech windows among the last n (needs kofn): a rise is the first sign of a voice.\"\"\"
        return sum(self._recent)

    def _confidence(self, window: object) -> float:
""",
        ),
        (
            """class EchoModel:
""",
            """class Ducker:
    \"\"\"Turn the speaker down at the first sign of a voice, and back up if no barge-in follows.\"\"\"

    def __init__(self, db: float, restore_s: float = 0.6) -> None:
        self.db = db
        self.restore_s = restore_s
        self._since: float | None = None

    @property
    def ducked(self) -> bool:
        return self._since is not None

    def update(self, voiced: bool, fired: bool, now: float) -> float | None:
        \"\"\"The gain in dB to set (negative to duck, 0.0 to restore), or None to leave it.\"\"\"
        if self._since is None:
            if voiced and not fired:
                self._since = now
                return self.db
        elif fired:
            self._since = None
        elif not voiced and now - self._since >= self.restore_s:
            self._since = None
            return 0.0
        return None


class EchoModel:
""",
        ),
    ],
    "src/doc_chat/__main__.py": [
        (
            """from doc_chat.bargein import BargeInGuard, EchoModel, PreRoll, SileroOnset, looks_like_echo
""",
            """from doc_chat.bargein import (
    BargeInGuard,
    Ducker,
    EchoModel,
    PreRoll,
    SileroOnset,
    looks_like_echo,
)
""",
        ),
        (
            """        "as the path delay grows)",
    )
""",
            """        "as the path delay grows)",
    )
    parser.add_argument(
        "--duck-db",
        type=float,
        default=0.0,
        help="turn the remote speaker down by this many dB (negative, e.g. -15) at the first "
        "sign of your voice over the answer, and back up if you do not interrupt (default: off)",
    )
    parser.add_argument(
        "--duck-k",
        type=int,
        default=2,
        help="with --duck-db: speech windows among the last 10 that count as the first sign",
    )
""",
        ),
        (
            """        trace = self._guard_trace

        def feed(block: bytes) -> bool:
""",
            """        trace = self._guard_trace
        duck_db = float(getattr(self.args, "duck_db", 0.0))
        duck_k = int(getattr(self.args, "duck_k", 2))
        ducker = Ducker(duck_db) if duck_db < 0.0 else None

        def feed(block: bytes) -> bool:
""",
        ),
        (
            """            fired = guard.feed(block, played, answering=answering)
""",
            """            fired = guard.feed(block, played, answering=answering)
            set_gain = getattr(self._player, "set_gain", None)
            if ducker is not None and silero is not None and answering and set_gain is not None:
                voiced = guard.last_level > guard.last_bar and silero.recent_speech >= duck_k
                new_gain = ducker.update(voiced, fired, time.perf_counter())
                if new_gain is not None:
                    set_gain(new_gain)
""",
        ),
        (
            """                            "fired": fired,
""",
            """                            "fired": fired,
                            "ducked": ducker is not None and ducker.ducked,
""",
        ),
    ],
}
