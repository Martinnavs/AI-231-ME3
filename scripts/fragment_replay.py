# ruff: noqa: E501
"""Offline replay of logged sessions: how often a long sentence is cut by the end-of-turn hold,
and what longer holds would catch against what they cost in latency (ticket 04).

Run from the repo root:  uv run python scripts/fragment_replay.py [sessions-dir] > FRAGMENTS.md
No network, no paid calls. Rules are fixed in RULES below before any result is read.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOLD_NOW = 0.6  # --vad-hold; the detector decides at 100 ms block edges: 0.639 s observed
HOLD_OBSERVED_EXTRA = 0.04
PI_BUDGET_S = 0.05
PI_BASELINE_S = 1.50  # docs: the tuned v4 median on the Pi
SERVER_DEFAULT_S = 0.58  # stt_server_ms median when a session has none
ONSET_DEFAULT_S = 0.30  # guard onset (Silero 7/10) when it cannot be measured
SOON_S = 1.5  # a barge-in this soon after the answer started may be a cut sentence
DANGLING = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "so",
        "to",
        "of",
        "for",
        "with",
        "in",
        "on",
        "at",
        "by",
        "from",
        "about",
        "that",
        "which",
        "if",
        "because",
        "my",
        "your",
        "his",
        "her",
        "our",
        "their",
        "some",
        "any",
        "is",
        "are",
        "was",
        "were",
        "am",
        "can",
        "could",
        "would",
        "will",
        "should",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "not",
        "then",
        "also",
        "plus",
        "than",
        "like",
        "into",
        "onto",
        "over",
        "under",
        "between",
    ]
)
CONTINUATIONS = frozenset(
    [
        "and",
        "or",
        "but",
        "so",
        "because",
        "with",
        "for",
        "to",
        "that",
        "which",
        "on",
        "in",
        "at",
        "the",
        "a",
        "an",
        "also",
        "plus",
        "then",
        "preferably",
        "maybe",
        "like",
    ]
)


@dataclass(frozen=True)
class Rule:
    name: str
    hold_s: float  # the longer hold, applied where the rule fires
    long_speech_s: float | None = None  # R2: speech so far above this
    p_unfinished: float | None = None  # R3: P(finished) from the last partial below this
    dangling: bool = False  # R3: or the last word is a dangling function word


def _rules() -> list[Rule]:
    rules = [Rule("R0 today (0.6 s)", HOLD_NOW)]
    rules += [Rule(f"R1 fixed {h:.1f} s", h) for h in (0.8, 1.0, 1.2)]
    rules += [
        Rule(f"R2 >{t:g} s speech, hold {h:.1f}", h, long_speech_s=t)
        for h in (1.0, 1.2)
        for t in (2.0, 3.0, 4.0)
    ]
    rules += [
        Rule(f"R3 dangling or P<{p}, hold {h:.1f}", h, p_unfinished=p, dangling=True)
        for h in (1.0, 1.2)
        for p in (0.3, 0.5)
    ]
    rules += [
        Rule(f"R2+R3 >3 s or dangling or P<{p}, hold {h:.1f}", h, 3.0, p, True)
        for h in (1.0, 1.2)
        for p in (0.3, 0.5)
    ]
    return rules


RULES = _rules()


@dataclass
class Turn:
    session: str
    harness: str
    first_sound_s: float  # speech end to first audio
    speech_s: float | None
    text: str  # the last partial, else the final words
    p_finished: float | None = None


@dataclass
class Fragment:
    session: str
    harness: str
    kind: str  # "resumed" (caught by the guard and joined) or "uncaught" (probable)
    turn: int
    text: str
    gap_s: float
    speech_s: float
    p_finished: float | None = None
    follow: str = ""
    estimated: bool = False


@dataclass
class Session:
    name: str
    harness: str
    rows: list[dict[str, Any]]
    settings: dict[str, Any] = field(default_factory=dict)


def harness_of(settings: dict[str, Any]) -> str:
    url = str(settings.get("agent_url") or "")
    return "pi" if url and not re.search(r"//(127\.0\.0\.1|localhost)", url) else "laptop"


def load_sessions(root: Path) -> list[Session]:
    out = []
    for d in sorted(p for p in root.iterdir() if (p / "turns.jsonl").exists()):
        if d.name.startswith("e2e-"):
            continue  # scripted test runs
        try:
            settings = json.loads((d / "session.json").read_text()).get("settings", {})
        except (OSError, ValueError):
            settings = {}
        rows = [json.loads(line) for line in (d / "turns.jsonl").read_text().splitlines() if line]
        out.append(Session(d.name, harness_of(settings), rows, settings))
    return out


def last_word(text: str) -> str:
    words = re.findall(r"[a-z']+", text.lower())
    return words[-1] if words else ""


def unfinished(text: str, p_finished: float | None, rule: Rule) -> bool:
    if rule.dangling and last_word(text) in DANGLING:
        return True
    return (
        rule.p_unfinished is not None and p_finished is not None and p_finished < rule.p_unfinished
    )


def fires(rule: Rule, speech_s: float | None, text: str, p_finished: float | None) -> bool:
    if rule.long_speech_s is None and rule.p_unfinished is None:
        return rule is not RULES[0]  # R1 fires on every turn; R0 adds nothing
    long = rule.long_speech_s is not None and speech_s is not None and speech_s > rule.long_speech_s
    return long or unfinished(text, p_finished, rule)


def server_median(rows: list[dict[str, Any]]) -> float:
    xs = [r["stt_server_ms"] / 1000 for r in rows if r.get("stt_server_ms")]
    return statistics.median(xs) if xs else SERVER_DEFAULT_S


def pause_median(rows: list[dict[str, Any]]) -> float:
    xs = [r["pause_ms"] / 1000 for r in rows if r.get("pause_ms")]
    return statistics.median(xs) if xs else HOLD_NOW + HOLD_OBSERVED_EXTRA


def reconstruct_gap(row: dict[str, Any], pause_s: float, server_s: float, onset_s: float) -> float:
    """The silence between the fragment's last sound and the continuation's first sound.

    From the last sound the client waits `pause_s` (the hold) and the server takes `server_s` to
    answer `finalize`: that is the fragment's final (t_final). The row's `interrupt_after_final_ms`
    runs from t_final to the moment the guard decided the user was talking again, which is
    `onset_s` after the voice actually returned. Never below the hold: the turn only ended because
    the silence had lasted that long."""
    return max(pause_s + server_s + row["interrupt_after_final_ms"] / 1000.0 - onset_s, pause_s)


def caught(rule: Rule, gap_s: float) -> bool:
    """The longer hold outlasts the silence, so the fragment and its continuation are one turn."""
    return gap_s < rule.hold_s + HOLD_OBSERVED_EXTRA


def percentile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def project(rule: Rule, turns: list[Turn]) -> tuple[int, float, float]:
    """(turns delayed, median, p90) of speech end to first sound; the added hold is added 1:1 to
    the turns where the rule fires."""
    extra = rule.hold_s - HOLD_NOW
    values, delayed = [], 0
    for t in turns:
        hit = fires(rule, t.speech_s, t.text, t.p_finished)
        delayed += hit
        values.append(t.first_sound_s + (extra if hit else 0.0))
    return delayed, statistics.median(values), percentile(values, 0.9)


def fragment_caught(rule: Rule, f: Fragment) -> bool:
    return (
        fires(rule, f.speech_s, f.text, f.p_finished)
        and caught(rule, f.gap_s)
        and rule is not RULES[0]
    )


def turns_of(session: Session, p_of: Any) -> list[Turn]:
    rows = session.rows
    out = []
    for r in rows:
        e = r.get("speech_end_to_first_audio_ms")
        if not e or r.get("interrupted"):
            continue
        hold = (r.get("pause_ms") or (HOLD_NOW + HOLD_OBSERVED_EXTRA) * 1000) / 1000
        audio = r.get("audio_s")
        text = r.get("partial") or r.get("heard") or r.get("question") or ""
        out.append(
            Turn(
                session.name,
                session.harness,
                e / 1000.0,
                None if audio is None else max(audio - hold, 0.0),
                text,
                p_of(text),
            )
        )
    return out


def wav_marks(path: Path) -> tuple[float, float, float] | None:
    """(fragment speech seconds, trailing silence, continuation lead) of a joined turn wav: the
    client joins fragment + 0.2 s of exact zeros + continuation (`Session.follow_up`)."""
    import numpy as np

    try:
        with wave.open(str(path)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    except (OSError, wave.Error):
        return None
    zero = np.flatnonzero(x == 0)
    if zero.size < 3000:
        return None
    breaks = np.flatnonzero(np.diff(zero) > 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [zero.size - 1]))
    runs = [
        (zero[s], zero[e] + 1)
        for s, e in zip(starts, ends, strict=True)
        if zero[e] + 1 - zero[s] >= 3000
    ]
    if len(runs) != 1:
        return None
    a, b = runs[0]

    def env(y: Any) -> Any:
        n = len(y) // 800
        return 20 * np.log10(
            np.sqrt(np.mean((y[: n * 800].reshape(n, 800) / 32768.0) ** 2, axis=1)) + 1e-9
        )

    ef, ec = env(x[:a]), env(x[b:])
    sf, sc = np.flatnonzero(ef > -35), np.flatnonzero(ec > -35)
    if not sf.size or not sc.size:
        return None
    last = (sf[-1] + 1) * 0.05
    return last - sf[0] * 0.05, a / 16000 - last, sc[0] * 0.05


def fragments_of(session: Session, p_of: Any, audio_dir: Path | None = None) -> list[Fragment]:
    rows = session.rows
    pause_s = pause_median(rows)
    server_s = server_median(rows)
    found = []
    for i, r in enumerate(rows):
        kind = r.get("interrupted")
        nxt = rows[i + 1] if i + 1 < len(rows) else {}
        text = str(r.get("question") or "")
        if kind == "resumed":
            marks = wav_marks(audio_dir / f"turn{r['turn']:02d}.wav") if audio_dir else None
            pause = (
                marks[1] if marks and 0.5 <= marks[1] <= 1.0 else pause_s
            )  # else not a joined wav
            speech = marks[0] if marks else max(0.33 * len(text.split()), 0.4)
            found.append(
                Fragment(
                    session.name, session.harness, "resumed", r["turn"], text,
                    reconstruct_gap(r, pause, server_s, ONSET_DEFAULT_S), speech,
                    p_of(text), str(nxt.get("question") or ""), estimated=marks is None,
                )
            )  # fmt: skip
        elif (
            kind == "barge_in" and (r.get("interrupt_after_answer_start_ms") or 1e9) < SOON_S * 1000
        ):
            follow = str(nxt.get("question") or "")
            p = p_of(text)
            first = (re.findall(r"[a-z']+", follow.lower()) or [""])[0]
            looks_cut = (
                last_word(text) in DANGLING or first in CONTINUATIONS or (p is not None and p < 0.3)
            )
            if looks_cut and follow:
                found.append(
                    Fragment(
                        session.name, session.harness, "uncaught", r["turn"], text,
                        reconstruct_gap(r, pause_s, server_s, ONSET_DEFAULT_S),
                        max(0.33 * len(text.split()), 0.4), p, follow, estimated=True,
                    )
                )  # fmt: skip
    return found


def table(turns: list[Turn], frags: list[Fragment]) -> list[str]:
    lines = []
    n, nf = len(turns), len(frags)
    base = project(RULES[0], turns)
    for rule in RULES:
        delayed, med, p90 = project(rule, turns)
        got = sum(fragment_caught(rule, f) for f in frags)
        lines.append(
            f"| {rule.name} | {got}/{nf} | {delayed}/{n} | {med:.2f} ({med - base[1]:+.2f}) | {p90:.2f} ({p90 - base[2]:+.2f}) |"
        )
    return lines


def make_p_of() -> Any:
    try:
        from doc_chat.turnend import TextTurnModel

        model = TextTurnModel()
    except Exception as exc:  # no model: only the dangling-word test is available
        print(f"<!-- turnsense unavailable: {type(exc).__name__} -->", file=sys.stderr)
        return lambda text: None
    cache: dict[str, float] = {}

    def p_of(text: str) -> float | None:
        if not text.strip():
            return None
        if text not in cache:
            cache[text] = model.prob(text)
        return cache[text]

    return p_of


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("out/sessions")
    p_of = make_p_of()
    sessions = load_sessions(root)
    print(HEADER)
    for harness in ("laptop", "pi"):
        turns, frags = [], []
        for s in sessions:
            if s.harness != harness:
                continue
            turns += turns_of(s, p_of)
            frags += fragments_of(s, p_of, root / s.name / "audio")
        res = [f for f in frags if f.kind == "resumed"]
        unc = [f for f in frags if f.kind == "uncaught"]
        print(
            f"\n## {harness}: {len(turns)} answered turns, {len(res)} caught fragments (resumed), {len(unc)} probable uncaught\n"
        )
        print(
            f"R0 median {statistics.median(t.first_sound_s for t in turns):.2f} s, p90 {percentile([t.first_sound_s for t in turns], 0.9):.2f} s"
            if turns
            else "no turns"
        )
        print(
            "\n| rule | fragments caught | turns delayed | median s (delta) | p90 s (delta) |\n|---|---|---|---|---|"
        )
        print("\n".join(table(turns, frags)))
        if turns and harness == "pi":
            base = project(RULES[0], turns)[1]
            ok = [r.name for r in RULES[1:] if project(r, turns)[1] - base <= PI_BUDGET_S]
            print(
                f"\nWithin the +{PI_BUDGET_S:.2f} s Pi-median budget (logged Pi median {base:.2f} s; doc baseline {PI_BASELINE_S:.2f} s):\n"
            )
            print("\n".join(f"- {n}" for n in ok) if ok else "NONE")
        gaps = [f.gap_s for f in res]
        if gaps:
            print(
                f"\nReconstructed silence in caught fragments: min {min(gaps):.2f}, p25 {percentile(gaps, 0.25):.2f}, median {statistics.median(gaps):.2f}, p75 {percentile(gaps, 0.75):.2f}, max {max(gaps):.2f} s"
            )
        print(
            "\nFragments (gap = reconstructed mid-sentence silence; * = speech length estimated from words):\n"
        )
        print(
            "| kind | session | turn | gap s | speech s | P(finished) | fragment | continued with |\n|---|---|---|---|---|---|---|---|"
        )
        for f in frags:
            p = "-" if f.p_finished is None else f"{f.p_finished:.2f}"
            print(
                f"| {f.kind} | {f.session} | {f.turn} | {f.gap_s:.2f} | {f.speech_s:.1f}{'*' if f.estimated else ''} | {p} | {f.text[:60]} | {f.follow[:60]} |"
            )
    return 0


HEADER = """# Fragments against latency (ticket 04)

## Rules (fixed before the results were read)

- Fragment: a user sentence cut by the end-of-turn hold. Caught = `interrupted: "resumed"` rows (the guard heard the user continue before an answer started and the client joined the audio). Probable uncaught = a `barge_in` row less than 1.5 s after the answer started whose next turn starts with a continuation word, or whose text ends in a dangling function word, or with turnsense P(finished) < 0.3 (heuristic: the owner judges the list).
- Silence gap reconstruction: gap = hold (pause detector, from the joined wav's trailing silence when it has one, else the session median `pause_ms`) + server finalize time (session median `stt_server_ms`) + `interrupt_after_final_ms` (from t_final to the guard's decision) - 0.30 s guard onset (Silero 7/10 needs at least 7 windows of 32 ms plus block rounding), never below the hold. A large gap (over about 2 s) is mostly a correction or restatement after the answer was already under way, not a pause inside a sentence.
- A rule with hold H catches a fragment iff the rule fires on it and gap < H + 0.04 (the 0.64 s observed vs 0.6 s set). Rules fire on: R1 every turn; R2 a turn whose speech so far (`audio_s` minus the hold) is above T; R3 a turn whose last streaming partial ends in a dangling function word or has turnsense P(finished) below p (for fragments only the final text is logged, so R3 is optimistic for them); R2+R3 either.
- Latency: the added hold (H - 0.6) is added 1:1 to speech-end-to-first-sound of the turns where the rule fires; harness `pi` = sessions whose agent ran on the Pi (`agent_url` not loopback), `laptop` = the rest; scripted `e2e-*` sessions excluded.
- Budget: a rule is within budget if the projected Pi median rises by at most 0.05 s.
- Offline projection from one speaker's scripted sessions: not a live measurement. State repetitions: a fragment is counted once per resumed or probable row.
"""

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
