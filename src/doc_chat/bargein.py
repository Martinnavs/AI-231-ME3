"""Hearing the user start to speak while the assistant is thinking or talking.

`OnsetDetector` says when sound has been loud enough for long enough to be speech (a click or a
cough is not). `PreRoll` keeps the last second or so of microphone blocks, so the start of the
interruption is not lost while the detector makes up its mind.
"""

from __future__ import annotations

import difflib
import re
import statistics
from collections import deque

SAMPLE_RATE = 16000


class OnsetDetector:
    """True once the level has been above ``max(noise * ratio, floor)`` for ``min_s`` of speech.

    The count leaks while the level is low (a word's quiet moments do not reset it), and rises
    only while it is high, so a short burst never adds up to an onset.
    """

    def __init__(
        self, noise: float, ratio: float = 4.0, floor: float = 0.02, min_s: float = 0.3
    ) -> None:
        self.noise = noise
        self.ratio = ratio
        self.floor = floor
        self.min_s = min_s
        self._heard_s = 0.0

    @property
    def threshold(self) -> float:
        return max(self.noise * self.ratio, self.floor)

    def feed(self, level: float, dur_s: float, extra: float = 0.0) -> bool:
        """``extra`` raises the bar for this block (the expected echo of the assistant's voice)."""
        if level > max(self.threshold, extra):
            self._heard_s += dur_s
        else:
            self._heard_s = max(self._heard_s - dur_s, 0.0)
        return self._heard_s >= self.min_s

    def reset(self) -> None:
        self._heard_s = 0.0


class PreRoll:
    """The most recent ``seconds`` of 16-bit microphone blocks."""

    def __init__(self, seconds: float = 1.2, block_s: float = 0.1) -> None:
        self._blocks: deque[bytes] = deque(maxlen=max(round(seconds / block_s), 1))

    def push(self, block: bytes) -> None:
        self._blocks.append(block)

    def blocks(self) -> list[bytes]:
        return list(self._blocks)


class Ducker:
    """Turn the speaker down at the first sign of a voice, and back up if no barge-in follows."""

    def __init__(self, db: float, restore_s: float = 0.6) -> None:
        self.db = db
        self.restore_s = restore_s
        self._since: float | None = None

    @property
    def ducked(self) -> bool:
        return self._since is not None

    def update(self, voiced: bool, fired: bool, now: float) -> float | None:
        """The gain in dB to set (negative to duck, 0.0 to restore), or None to leave it."""
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
    """How much of the assistant's own voice comes back through the microphone.

    While it speaks, the loudness of what the microphone hears is compared with the loudness of
    what is being played. The typical ratio (the coupling: 0.02 with a headset, 0.2-0.5 with
    laptop speakers) times the loudness now is the echo to expect; the user is speaking if the
    microphone is clearly above that. Blocks that may be the user (above the bar) are not used to
    learn, so the user's own voice does not raise the estimate. Until enough blocks have been
    seen, ``prior`` is assumed, which is cautious.
    """

    def __init__(
        self,
        prior: float = 0.5,
        margin: float = 1.8,
        window: int = 15,
        min_samples: int = 4,
        quantile: float = 0.5,
    ) -> None:
        self.prior = prior
        self.margin = margin
        self.min_samples = min_samples
        self.quantile = quantile
        self._ratios: deque[float] = deque(maxlen=window)

    @property
    def coupling(self) -> float:
        """The speaker-to-microphone ratio to expect: the ``quantile`` of the recent ratios.

        The median (the default) follows the typical echo; the echo is spiky, though (on the Pi
        speaker's echo the median ratio was 0.34 but one block in twenty was above 0.7), so a
        bar built on the median is cleared by ordinary echo peaks. Use a high quantile (0.95)
        with a longer window when the speaker is in the room."""
        if len(self._ratios) < self.min_samples:
            return self.prior
        if self.quantile == 0.5:
            return statistics.median(self._ratios)
        ordered = sorted(self._ratios)
        pos = self.quantile * (len(ordered) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)

    def expected(self, played_level: float) -> float:
        """The microphone level above which a block is more than the echo."""
        return self.margin * self.coupling * played_level

    def learn(self, mic_level: float, played_level: float) -> None:
        if played_level > 0.01:
            self._ratios.append(mic_level / played_level)


class BargeInGuard:
    """Is this block of microphone audio the user talking over the assistant, or its own voice?

    Three situations, as in the live client:

    * ``answering`` False (the assistant is still thinking): any clear speech counts (loudness
      ``idle_ratio`` times the room, and Silero if it is in use).
    * ``answering`` True (its voice is coming out of a speaker): the block must also be louder than
      the echo to expect, ``EchoModel.expected(played)``, where ``played`` is the loudness of what
      was just sent to the speaker. That is what the laptop knows exactly and a detector alone does
      not: with the Pi's speaker 40-90% loud, Silero fired on the assistant's own voice in every
      clip (docs/PI-BRIDGE.md), so it only counts as the user when it is clearly above the echo.
    * ``mode='silero'``: the model alone decides (the earlier behaviour, no echo allowance).

    Blocks at or below the bar while the assistant plays teach the echo model the speaker-to-mic
    coupling. Call ``feed`` once per 100 ms block.
    """

    def __init__(
        self,
        noise: float,
        *,
        ratio: float = 4.0,
        idle_ratio: float = 3.0,
        min_s: float = 0.3,
        mode: str = "energy",
        silero: SileroOnset | None = None,
        echo: EchoModel | None = None,
        level_kofn: tuple[int, int] | None = None,
        warmup_blocks: int = 0,
        ducked_k: int | None = None,
        min_bar: float = 0.0,
    ) -> None:
        self.mode = mode
        # an absolute floor for the bar (block RMS, 0-1) while the assistant plays: the room-based
        # floor (ratio x noise) drops in a quiet room, but the echo does not (2026-10-29: a -40 dBFS
        # room put the floor at 0.040, on top of the echo's 95th percentile of 0.033-0.042)
        self.min_bar = min_bar
        # with level_kofn: while the speaker is ducked (set `ducked` before `feed`), ducked_k
        # blocks above the bar are enough: the echo is 15 dB down, and a bare "stop" gives only
        # one or two loud blocks (.scratch/echo-next/PLAN.md, live session 1)
        self.ducked_k = ducked_k
        self.ducked = False
        # for the first warmup_blocks blocks of a reply the echo allowance uses the echo model's
        # prior (no canceller), not what it learned from cancelled audio: the canceller leaks
        # while it settles at the start of a reply (.scratch/echo-next/PLAN.md, E2)
        self.warmup_blocks = warmup_blocks
        self._played_blocks = 0
        # level_kofn=(k, n): speech once k of the last n blocks were above the bar (speech has
        # gaps between words; a net-time counter never builds up on a 30-40% duty cycle)
        self.level_kofn = level_kofn
        self._above: deque[bool] = deque(maxlen=level_kofn[1] if level_kofn else 1)
        self.silero = silero
        self.echo = echo
        self.idle = OnsetDetector(noise, ratio=idle_ratio, min_s=min_s)
        self.playing = OnsetDetector(noise, ratio=ratio, min_s=min_s)
        self.last_bar = 0.0  # for diagnostics: the level a block had to beat, and the block's level
        self.last_level = 0.0
        self.last_model = False  # ... and what each stage said about the newest block
        self.last_level_hit = False

    def reset(self) -> None:
        """A new reply starts: forget the last one (counts, window memory, the model's state).
        What was learned about the speaker's echo is kept."""
        self.idle.reset()
        self.playing.reset()
        self._above.clear()
        self._played_blocks = 0
        if self.silero is not None:
            self.silero.reset()

    def model_stage(self, block: bytes) -> bool:
        """The Silero half: has the model heard speech long enough? (True when it is not in use.)

        Owns only the model's state, so it can run on its own thread beside `level_stage`."""
        return True if self.silero is None else self.silero.feed(block)

    def level_stage(self, block: bytes, played: float = 0.0, answering: bool = True) -> bool:
        """The loudness half: speech clearly above the room and, while the assistant plays, above
        the echo to expect from ``played``. Owns the energy detectors and the echo model."""
        import numpy as np

        if self.silero is not None and self.mode == "silero":
            return True  # the model alone decides; loudness and echo are not looked at
        x = np.frombuffer(block[: len(block) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        level = float(np.sqrt(np.mean(x**2))) if x.size else 0.0
        dur = len(block) / 2 / SAMPLE_RATE
        if not answering:
            fired_idle = self.idle.feed(level, dur)
            if self.silero is not None:
                return level > self.idle.threshold
            return fired_idle
        use_echo = self.echo is not None and self.echo.margin > 0
        extra = self.echo.expected(played) if use_echo and self.echo and played > 0.01 else 0.0
        if self._played_blocks or played > 0:
            self._played_blocks += 1
        guarded = extra
        if self.echo is not None and 0 < self._played_blocks <= self.warmup_blocks:
            guarded = max(extra, self.echo.margin * self.echo.prior * played)
        fired = self.playing.feed(level, dur, guarded)
        bar = max(self.playing.threshold, extra, self.min_bar)
        guarded_bar = max(self.playing.threshold, guarded, self.min_bar)
        fired = fired and level > self.min_bar
        if self.level_kofn is not None:
            self._above.append(level > guarded_bar)
            k = self.ducked_k if self.ducked and self.ducked_k else self.level_kofn[0]
            fired = level > guarded_bar and sum(self._above) >= k
        self.last_bar, self.last_level = guarded_bar, level
        if self.echo is not None and played > 0.01 and level <= bar:
            self.echo.learn(
                level, played
            )  # not the user: this is what the speaker sounds like here
        # with the model also in use, `fired` (above the bar for min_s, not just this block) must
        # agree with its onset: one loud syllable of the assistant's own voice must not count
        return fired

    def feed(self, block: bytes, played: float = 0.0, answering: bool = True) -> bool:
        """Both stages, in turn; each is fed every block so its state follows the audio."""
        model = self.model_stage(block)
        level = self.level_stage(block, played, answering)
        self.last_model, self.last_level_hit = model, level
        return model and level


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def looks_like_echo(heard: str, spoken: str, min_words: int = 3, share: float = 0.8) -> bool:
    """The 'user' said (nearly) the assistant's own words: its voice came back through the mic.

    True if a run of at least ``min_words`` words of what was heard appears, in order, in what
    the assistant was saying, and that run is most of what was heard.
    """
    a, b = _words(heard), _words(spoken)
    if len(a) < min_words or not b:
        return False
    match = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b)
    )
    return match.size >= min_words and match.size / len(a) >= share


_TOKEN = re.compile(r"[A-Za-z0-9']+")
_STOP_AT = {"stop", "wait", "pause"}
_LEADING_STOP = re.compile(r"^\W*(?:(?:stop|wait|hold on|hang on)\b\W*)+", re.IGNORECASE)


def _similar(a: str, b: str) -> bool:
    """The same word, or a misheard one ("mccartney" for "mccarthy"): 4+ letters, 75% alike."""
    if a == b:
        return True
    return min(len(a), len(b)) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def _reply_words(spoken: str) -> list[str]:
    return [w.lower().strip("'") for w in _TOKEN.findall(spoken.replace("-", " "))]


def is_all_echo(heard: str, spoken: str, min_run: int = 3, share: float = 0.6) -> bool:
    """The whole transcript is a run of the assistant's words, misheard words allowed.

    `looks_like_echo` needs exact words and 80%; the recogniser mishears the echo ("Novel
    published in 2007." for "novel published in 2006"), so this allows near words and 60%: an
    in-order run of at least ``min_run`` near words that is at least ``share`` of what was heard.
    """
    a = [w.lower().strip("'") for w in _TOKEN.findall(heard)]
    b = _reply_words(spoken)
    if len(a) < min_run or not b:
        return False
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if _similar(a[i - 1], b[j - 1]):
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best >= min_run and best / len(a) >= share


def strip_echo_prefix(heard: str, spoken: str, max_prefix: int = 8) -> str:
    """'2005 border. Stop. Set a timer.' -> 'Set a timer.' when '2005 border' is the assistant's.

    After a barge-in the recogniser gets the pre-roll, so the assistant's last words often come
    before the user's "stop". If at most ``max_prefix`` words come before the first stop word
    ("stop", "wait", "pause", "hold on", "hang on") and one of them (3+ letters) is near a word
    of the reply, they are dropped, and so are the stop words when at least two words remain.
    Otherwise the text comes back unchanged.
    """
    words = list(_TOKEN.finditer(heard))
    k = next(
        (
            i
            for i, m in enumerate(words)
            if m.group().lower() in _STOP_AT
            or (
                m.group().lower() in ("hold", "hang")
                and i + 1 < len(words)
                and words[i + 1].group().lower() == "on"
            )
        ),
        0,
    )
    if k == 0 or k > max_prefix:
        return heard
    reply = set(_reply_words(spoken))
    content = [m.group().lower() for m in words[:k] if len(m.group()) >= 3]
    if not any(_similar(w, r) for w in content for r in reply):
        return heard
    rest = heard[words[k].start() :]
    after = _LEADING_STOP.sub("", rest)
    return after[0].upper() + after[1:] if len(after.split()) >= 2 else rest


class SileroOnset:
    """Speech onset from the Silero VAD model, the way Pipecat's SileroVADAnalyzer runs it.

    This is the voice detector main's design uses (the only model allowed on the Pi): 32 ms
    windows of 512 samples with 64 samples of context, speech when the model's confidence is at
    least ``confidence`` (Pipecat default 0.7), and an onset once that has held without a break for
    ``min_s`` (Pipecat start_secs 0.2; main's config min_barge_in_ms 250). It does not look at
    loudness, and it cannot tell the assistant's voice from the user's.
    """

    WINDOW = 512
    CONTEXT = 64

    def __init__(
        self,
        model_path: str,
        confidence: float = 0.7,
        min_s: float = 0.25,
        keep_history: bool = False,
        kofn: tuple[int, int] | None = None,
    ) -> None:
        import numpy as np
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"], sess_options=opts
        )
        self._np = np
        self.confidence = confidence
        self.min_s = min_s
        # With kofn=(k, n) an onset is a speech window when at least k of the last n windows were
        # speech, instead of min_s of unbroken speech: speech has quiet windows inside it, and
        # one dip must not restart the count (the Pi pilot, docs/PI-RESULTS.md: 7 of 10).
        self.kofn = kofn
        self._recent: deque[bool] = deque(maxlen=kofn[1] if kofn else 1)
        self._state = np.zeros((2, 1, 128), dtype="float32")
        self._context = np.zeros((1, self.CONTEXT), dtype="float32")
        self._pending = np.zeros(0, dtype="float32")
        self._speech_s = 0.0
        self.last_confidence = 0.0
        self.history: list[float] | None = [] if keep_history else None  # one value per window

    def reset(self) -> None:
        """Forget what was heard, as a new instance would, without reloading the model (slow on
        a Pi: building the session costs about 200 ms)."""
        np = self._np
        self._state = np.zeros((2, 1, 128), dtype="float32")
        self._context = np.zeros((1, self.CONTEXT), dtype="float32")
        self._pending = np.zeros(0, dtype="float32")
        self._speech_s = 0.0
        self._recent.clear()
        self.last_confidence = 0.0
        if self.history is not None:
            self.history.clear()

    @property
    def recent_speech(self) -> int:
        """Speech windows among the last n (needs kofn): a rise is the first sign of a voice."""
        return sum(self._recent)

    def _confidence(self, window: object) -> float:
        np = self._np
        x = np.concatenate((self._context, np.asarray(window)[None, :]), axis=1)
        out, self._state = self._sess.run(
            None, {"input": x, "state": self._state, "sr": np.array(16000, dtype="int64")}
        )
        self._context = x[..., -self.CONTEXT :]
        return float(out[0][0])

    def feed(self, block: bytes) -> bool:
        """Take one block of 16-bit 16 kHz audio; True once speech has been heard long enough."""
        np = self._np
        x = np.frombuffer(block[: len(block) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate((self._pending, x))
        while len(self._pending) >= self.WINDOW:
            window, self._pending = self._pending[: self.WINDOW], self._pending[self.WINDOW :]
            self.last_confidence = self._confidence(window)
            if self.history is not None:
                self.history.append(self.last_confidence)
            hit = self.last_confidence >= self.confidence
            if self.kofn is not None:
                self._recent.append(hit)
                if hit and sum(self._recent) >= self.kofn[0]:
                    return True
                continue
            if hit:
                self._speech_s += self.WINDOW / 16000
            else:
                self._speech_s = 0.0  # Pipecat: any non-speech window while starting resets it
            if self._speech_s >= self.min_s:
                return True
        return False
