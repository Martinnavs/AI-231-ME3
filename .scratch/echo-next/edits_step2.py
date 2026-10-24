"""Reference edits for step 2 (a)-(d): (old, new) pairs, each old block unique in its file.

Applied by `.scratch/echo-fix/apply_edits.py edits_step2.py <root>`. Tests are new files in
`skeleton/`. The tickets quote these blocks verbatim.
"""

# ---- ticket 01: "pause" is an order, not a stop word, in the pivot strip ----
T01_MAIN = [
    (
        '''_STOP_PREFIX = re.compile(
    r"^\\W*(?:(?:wait|stop|hold on|hang on|pause|okay|ok|please|sorry|actually|hey|um|uh|so)\\b\\W*)+",
    re.IGNORECASE,
)''',
        '''_STOP_PREFIX = re.compile(
    r"^\\W*(?:(?:wait|stop|hold on|hang on|okay|ok|please|sorry|actually|hey|um|uh|so)\\b\\W*)+",
    re.IGNORECASE,
)''',
    ),
    (
        '''    question would search the documents and prompt the model with "wait stop" as well. Only a
    prefix that contains a real stop word is removed, and only if a question remains."""''',
        '''    question would search the documents and prompt the model with "wait stop" as well. Only a
    prefix that contains a real stop word is removed, and only if a question remains. "Pause" is
    not one here: "Stop. Pause the music." is an order to pause the music."""''',
    ),
    (
        '''    has_stop = re.search(r"\\b(wait|stop|hold on|hang on|pause)\\b", prefix, re.IGNORECASE)
    punctuated = re.search(r"\\b(wait|stop|pause|on)\\b\\s*[,.!?;:-]", prefix, re.IGNORECASE)''',
        '''    has_stop = re.search(r"\\b(wait|stop|hold on|hang on)\\b", prefix, re.IGNORECASE)
    punctuated = re.search(r"\\b(wait|stop|on)\\b\\s*[,.!?;:-]", prefix, re.IGNORECASE)''',
    ),
]

# ---- ticket 02: drop the assistant's own words from the front of an interruption ----
T02_BARGEIN = [
    (
        '''    return match.size >= min_words and match.size / len(a) >= share


class SileroOnset:''',
        '''    return match.size >= min_words and match.size / len(a) >= share


_TOKEN = re.compile(r"[A-Za-z0-9']+")
_STOP_AT = {"stop", "wait", "pause"}
_LEADING_STOP = re.compile(r"^\\W*(?:(?:stop|wait|hold on|hang on)\\b\\W*)+", re.IGNORECASE)


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


class SileroOnset:''',
    ),
]

T02_MAIN = [
    (
        '''    PreRoll,
    SileroOnset,
    looks_like_echo,
)''',
        '''    PreRoll,
    SileroOnset,
    is_all_echo,
    looks_like_echo,
    strip_echo_prefix,
)''',
    ),
    (
        '''            after_interrupt = interruption.kind == "barge_in"
            if (
                interruption.kind == "barge_in"
                and getattr(self.args, "echo_text_guard", True)
                and looks_like_echo(heard.text, spoken)
            ):''',
        '''            after_interrupt = interruption.kind == "barge_in"
            strip = interruption.kind == "barge_in" and getattr(
                self.args, "strip_echo_prefix", False
            )
            if (
                interruption.kind == "barge_in"
                and getattr(self.args, "echo_text_guard", True)
                and (
                    looks_like_echo(heard.text, spoken)
                    or (strip and is_all_echo(heard.text, spoken))
                )
            ):''',
    ),
    (
        '''            if interruption.kind == "resumed":
                idx -= 1  # the cut-off turn and what followed are one turn''',
        '''            if strip and (cleaned := strip_echo_prefix(heard.text, spoken)) != heard.text:
                print(f"      (my own words dropped: {cleaned!r})", flush=True)
                heard = dc_replace(heard, text=cleaned)
            if interruption.kind == "resumed":
                idx -= 1  # the cut-off turn and what followed are one turn''',
    ),
    (
        '''    parser.add_argument(
        "--barge-echo-margin",''',
        '''    parser.add_argument(
        "--strip-echo-prefix",
        action="store_true",
        help="after a barge-in, drop the assistant's own words heard before your 'stop' and treat "
        "a transcript made only of its words (misheard ones too) as its echo "
        "(.scratch/echo-next/PLAN.md, E3)",
    )
    parser.add_argument(
        "--barge-echo-margin",''',
    ),
]

# ---- ticket 03: no reference lead by default ----
T03_AEC = [
    (
        '''Both streams are fed in 10 ms frames in lockstep, as the canceller expects. The first slice of a
reply starts `lead_ms` of reference ahead. 50 ms was the best of 0-300 ms on a real recording
(docs/AEC-REMOTE-SPEAKER.md): more makes the canceller worse as the path delay grows.
"""''',
        '''Both streams are fed in 10 ms frames in lockstep, as the canceller expects. The first slice of a
reply can start `lead_ms` of reference ahead; the default is none. AEC3 resets its render buffer
when the reference stays more than 32 ms ahead of the microphone for a second, and it finds
render-to-echo delays only up to about 0.5 s, so any lead uses up that range
(.scratch/echo-next/PLAN.md, E1: lead 0 left the least echo 1-2 s into a clip).
"""''',
    ),
    (
        '''    def __init__(self, lead_ms: int = 50) -> None:''',
        '''    def __init__(self, lead_ms: int = 0) -> None:''',
    ),
]

T03_MAIN = [
    (
        '''        "--aec-lead-ms",
        type=int,
        default=50,
        help="with --aec: ms of reference fed ahead of the microphone (default: 50; more is worse "
        "as the path delay grows)",''',
        '''        "--aec-lead-ms",
        type=int,
        default=0,
        help="with --aec: ms of reference fed ahead of the microphone (default: 0; any lead uses "
        "up the canceller's ~0.5 s delay range)",''',
    ),
]

# ---- ticket 04: a cautious bar for the first second of each reply ----
T04_BARGEIN = [
    (
        '''        echo: EchoModel | None = None,
        level_kofn: tuple[int, int] | None = None,
    ) -> None:
        self.mode = mode''',
        '''        echo: EchoModel | None = None,
        level_kofn: tuple[int, int] | None = None,
        warmup_blocks: int = 0,
    ) -> None:
        self.mode = mode
        # for the first warmup_blocks blocks of a reply the echo allowance uses the echo model's
        # prior (no canceller), not what it learned from cancelled audio: the canceller leaks
        # while it settles at the start of a reply (.scratch/echo-next/PLAN.md, E2)
        self.warmup_blocks = warmup_blocks
        self._played_blocks = 0''',
    ),
    (
        '''        self._above.clear()
        if self.silero is not None:
            self.silero.reset()''',
        '''        self._above.clear()
        self._played_blocks = 0
        if self.silero is not None:
            self.silero.reset()''',
    ),
    (
        '''        extra = self.echo.expected(played) if use_echo and self.echo and played > 0.01 else 0.0
        fired = self.playing.feed(level, dur, extra)
        bar = max(self.playing.threshold, extra)
        if self.level_kofn is not None:
            self._above.append(level > bar)
            fired = level > bar and sum(self._above) >= self.level_kofn[0]
        self.last_bar, self.last_level = bar, level''',
        '''        extra = self.echo.expected(played) if use_echo and self.echo and played > 0.01 else 0.0
        if self._played_blocks or played > 0:
            self._played_blocks += 1
        guarded = extra
        if self.echo is not None and 0 < self._played_blocks <= self.warmup_blocks:
            guarded = max(extra, self.echo.margin * self.echo.prior * played)
        fired = self.playing.feed(level, dur, guarded)
        bar = max(self.playing.threshold, extra)
        guarded_bar = max(self.playing.threshold, guarded)
        if self.level_kofn is not None:
            self._above.append(level > guarded_bar)
            fired = level > guarded_bar and sum(self._above) >= self.level_kofn[0]
        self.last_bar, self.last_level = guarded_bar, level''',
    ),
]

T04_MAIN = [
    (
        '''            level_kofn=_kofn(getattr(self.args, "level_kofn", None)),
        )

        trace = self._guard_trace''',
        '''            level_kofn=_kofn(getattr(self.args, "level_kofn", None)),
            warmup_blocks=round(float(getattr(self.args, "barge_warmup_s", 0.0)) * 10),
        )

        trace = self._guard_trace''',
    ),
    (
        '''    parser.add_argument(
        "--barge-echo-prior",''',
        '''    parser.add_argument(
        "--barge-warmup-s",
        type=float,
        default=0.0,
        help="for this many seconds at the start of each reply, expect the echo of the prior "
        "coupling (--barge-echo-prior) even if less was learned: the canceller leaks while it "
        "settles (try 1.0 with --aec; .scratch/echo-next/PLAN.md, E2)",
    )
    parser.add_argument(
        "--barge-echo-prior",''',
    ),
]

EDITS = {
    "src/doc_chat/__main__.py": T01_MAIN + T02_MAIN + T03_MAIN + T04_MAIN,
    "src/doc_chat/bargein.py": T02_BARGEIN + T04_BARGEIN,
    "src/doc_chat/aec.py": T03_AEC,
}
