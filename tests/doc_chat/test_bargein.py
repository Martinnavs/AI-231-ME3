"""Talking over the assistant: the detector, and what the session does when it happens."""

from __future__ import annotations

import argparse
import asyncio
import io
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

pytest.importorskip("langchain_core")

from doc_chat.__main__ import Interruption, Session  # noqa: E402
from doc_chat.bargein import OnsetDetector, PreRoll  # noqa: E402
from doc_chat.dialogue import ChatHistory  # noqa: E402
from doc_chat.speech import HeardTurn  # noqa: E402

SR = 16000


def block(rms: float) -> bytes:
    """100 ms of a 220 Hz tone with the given RMS level (0-1)."""
    t = np.arange(SR // 10) / SR
    return (rms * np.sqrt(2) * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16).tobytes()


# ---- the detector ----


def test_quiet_never_triggers() -> None:
    d = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    assert not any(d.feed(0.004, 0.1) for _ in range(100))


def test_speech_triggers_after_the_minimum_time() -> None:
    d = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    fired = [d.feed(0.2, 0.1) for _ in range(5)]
    assert fired == [False, False, True, True, True]


def test_a_click_or_cough_does_not_add_up() -> None:
    d = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    for _ in range(20):
        assert not d.feed(0.5, 0.1)  # a loud block counts 0.1 s...
        d.feed(0.001, 0.1)  # ...and quiet ones take it back
        d.feed(0.001, 0.1)


def test_a_dip_inside_a_word_does_not_reset_it() -> None:
    d = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    d.feed(0.2, 0.1)
    d.feed(0.2, 0.1)
    d.feed(0.001, 0.1)  # a quiet moment
    assert d.feed(0.2, 0.1) is False
    assert d.feed(0.2, 0.1) is True


def test_the_threshold_never_drops_below_the_floor() -> None:
    assert OnsetDetector(noise=0.0001, ratio=4.0, floor=0.02).threshold == 0.02
    assert OnsetDetector(noise=0.02, ratio=4.0, floor=0.02).threshold == pytest.approx(0.08)


def test_preroll_keeps_only_the_latest_blocks() -> None:
    roll = PreRoll(seconds=0.3)
    for i in range(5):
        roll.push(bytes([i]))
    assert roll.blocks() == [bytes([2]), bytes([3]), bytes([4])]


# ---- the session ----


class FakeHub:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def put(self, *levels: float) -> None:
        for lv in levels:
            self.queue.put_nowait(block(lv))

    async def get(self) -> bytes:
        return await self.queue.get()

    def drain(self) -> int:
        return 0

    async def blocks(self) -> Any:
        while True:
            yield await self.get()


class FakeStream:
    cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakePlayer:
    aborted = False

    def abort(self) -> None:
        self.aborted = True


class FakeClient:
    tts_sample_rate = 24000
    noise_floor: float | None = 0.01

    def __init__(self) -> None:
        self.hub = FakeHub()
        self.tts_cancels = 0
        self.heard: list[HeardTurn] = []
        self.listen_audio: list[list[bytes]] = []
        self.joined: list[bytes] = []
        self.join_text = "joined sentence"

    async def send_tts_cancel(self) -> None:
        self.tts_cancels += 1

    async def listen_turn(self, **kw: Any) -> HeardTurn:
        audio = kw.get("audio")
        got: list[bytes] = []
        if audio is not None:
            async for blk in audio:  # take the lead-in, which is what the test looks at
                got.append(blk)
                if len(got) >= 3:
                    break
        self.listen_audio.append(got)
        return self.heard.pop(0)

    async def hear_pcm(self, pcm: bytes, **kw: Any) -> HeardTurn:
        self.joined.append(pcm)
        return turn(self.join_text)


def turn(text: str, pcm: bytes = b"", tail: bytes = b"") -> HeardTurn:
    return HeardTurn(text, None, None, 1.0, 2.0, 1.5, pcm, "", tail)


def session(**args: Any) -> tuple[Session, FakeClient]:
    client = FakeClient()
    ns = argparse.Namespace(
        play=False,
        voice="F1",
        slot="doc?",
        risk=None,
        vad_hold=0.6,
        barge_in=True,
        barge_ms=300,
        barge_ratio=4.0,
        **args,
    )
    s = Session(
        args=ns,
        client=client,  # type: ignore[arg-type]
        log_file=io.StringIO(),
        out_dir=None,
        history=ChatHistory(4),
    )
    return s, client


async def test_an_answer_nobody_interrupts_just_completes() -> None:
    s, _client = session()
    done: list[int] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        await asyncio.sleep(0.05)
        done.append(idx)

    s.handle = handle  # type: ignore[method-assign]
    assert await s.respond(1, turn("who is harry")) is None
    assert done == [1]


async def test_talking_before_the_answer_starts_means_you_were_cut_off() -> None:
    s, client = session()
    cancelled: list[bool] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        try:
            await asyncio.sleep(10)  # the model is still thinking
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    s.handle = handle  # type: ignore[method-assign]
    client.hub.put(0.001, 0.2, 0.2, 0.2, 0.2)
    result = await asyncio.wait_for(s.respond(1, turn("break from the document")), timeout=5)

    assert isinstance(result, Interruption) and result.kind == "resumed"
    assert cancelled == [True] and client.tts_cancels == 1
    assert s.history.pairs() == []  # nothing was answered, nothing is remembered
    rec = s.records[-1]
    assert rec["interrupted"] == "resumed" and rec["answer_words"] == 0
    assert rec["interrupt_after_answer_start_ms"] is None
    assert len(result.blocks) >= 3  # the start of what was said is kept


async def test_talking_over_the_answer_stops_it_and_remembers_what_was_said() -> None:
    s, client = session()
    stream, player = FakeStream(), FakePlayer()

    async def handle(idx: int, heard: HeardTurn) -> None:
        s._answer_started = 1.0
        s._spoken = ["Hermione is a witch.", "She is clever."]
        s._stream, s._player = stream, player  # type: ignore[assignment]
        await asyncio.sleep(10)

    s.handle = handle  # type: ignore[method-assign]

    async def talk_later() -> None:
        await asyncio.sleep(0.1)
        client.hub.put(0.3, 0.3, 0.3, 0.3)

    asyncio.create_task(talk_later())
    result = await asyncio.wait_for(s.respond(1, turn("who is hermione")), timeout=5)

    assert result is not None and result.kind == "barge_in"
    assert stream.cancelled and player.aborted and client.tts_cancels == 1
    assert s.history.pairs() == [("who is hermione", "Hermione is a witch. She is clever.")]
    rec = s.records[-1]
    assert (
        rec["interrupted"] == "barge_in" and rec["spoken"] == "Hermione is a witch. She is clever."
    )
    assert rec["cancel_to_silence_ms"] < 500


async def test_its_own_voice_leaking_into_the_microphone_does_not_stop_it() -> None:
    """While it speaks the bar is higher than while it thinks."""
    s, client = session()
    done: list[int] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        s._answer_started = 1.0
        await asyncio.sleep(0.3)
        done.append(idx)

    s.handle = handle  # type: ignore[method-assign]
    # noise 0.01: speech while thinking needs > 0.03; while speaking > 0.04 (4 x noise)
    client.hub.put(*([0.035] * 8))
    assert await s.respond(1, turn("who is hermione")) is None
    assert done == [1]


async def test_the_same_level_does_interrupt_while_it_is_only_thinking() -> None:
    s, client = session()

    async def handle(idx: int, heard: HeardTurn) -> None:
        await asyncio.sleep(10)

    s.handle = handle  # type: ignore[method-assign]
    client.hub.put(*([0.035] * 8))
    result = await asyncio.wait_for(s.respond(1, turn("who is hermione")), timeout=5)
    assert result is not None and result.kind == "resumed"


async def test_speech_that_went_on_while_the_server_answered_counts_at_once() -> None:
    """Audio held back after `finalize` (the tail) is heard first, before anything new."""
    s, client = session()

    async def handle(idx: int, heard: HeardTurn) -> None:
        await asyncio.sleep(10)

    s.handle = handle  # type: ignore[method-assign]
    tail = block(0.2) * 4  # 0.4 s of speech after the turn was ended
    result = await asyncio.wait_for(s.respond(1, turn("break from the document", tail=tail)), 5)
    assert result is not None and result.kind == "resumed"
    assert client.hub.queue.empty()  # nothing had to come from the microphone


async def test_without_a_hub_or_with_barge_in_off_nothing_listens() -> None:
    s, client = session()
    s.args.barge_in = False
    ran: list[int] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        ran.append(idx)

    s.handle = handle  # type: ignore[method-assign]
    client.hub.put(0.5, 0.5, 0.5, 0.5)
    assert await s.respond(1, turn("x")) is None and ran == [1]


async def test_a_cut_off_sentence_is_joined_to_what_you_said_next() -> None:
    s, client = session()
    previous = turn("Break from the document.", pcm=b"\x01\x00" * 100)
    client.heard.append(turn("what is tissue paper used for", pcm=b"\x02\x00" * 50))
    lead = [block(0.2)] * 3

    result = await s.follow_up(previous, Interruption("resumed", lead, 12.0, 700.0))

    assert result.text == "joined sentence"
    assert client.listen_audio[0] == lead  # listening began with what had been heard
    joined = client.joined[0]
    assert joined.startswith(b"\x01\x00" * 100) and joined.endswith(b"\x02\x00" * 50)
    assert len(joined) > 300 + 0  # a short silence sits between the parts


async def test_talking_over_the_answer_is_a_new_turn_not_a_join() -> None:
    s, client = session()
    previous = turn("Who is Hermione?", pcm=b"\x01\x00" * 100)
    new = turn("Actually, who is Ron?", pcm=b"\x02\x00" * 50)
    client.heard.append(new)

    result = await s.follow_up(previous, Interruption("barge_in", [block(0.3)] * 3, 12.0, 900.0))

    assert result is new and client.joined == []


async def test_the_mic_loop_resumes_a_cut_off_turn_under_the_same_number() -> None:
    s, client = session()
    s.args.ask_to_add = False
    seen: list[tuple[int, str]] = []
    calls = {"n": 0}

    async def handle(idx: int, heard: HeardTurn) -> None:
        calls["n"] += 1
        seen.append((idx, heard.text))
        if calls["n"] == 1:
            client.hub.put(0.2, 0.2, 0.2, 0.2)
            await asyncio.sleep(10)  # interrupted

    s.handle = handle  # type: ignore[method-assign]
    first = replace(turn("Break from the document.", pcm=b"\x01\x00" * 10))
    client.heard.extend([first, turn("what is tissue paper", pcm=b"\x02\x00" * 10), turn("quit")])
    # listen order: the first turn, the continuation (follow-up), then the next turn: "quit"
    await asyncio.wait_for(s.run_mic(), timeout=10)

    assert seen == [(1, "Break from the document."), (1, "joined sentence")]


# ---- echo awareness: the assistant's own voice coming back through the microphone ----

from doc_chat.bargein import EchoModel, looks_like_echo  # noqa: E402


def test_the_echo_model_assumes_a_cautious_coupling_until_it_has_measured() -> None:
    m = EchoModel(prior=0.5, margin=2.0, min_samples=4)
    assert m.coupling == 0.5 and m.expected(0.1) == pytest.approx(0.1)
    for _ in range(4):
        m.learn(mic_level=0.002, played_level=0.1)  # a headset: almost nothing comes back
    assert m.coupling == pytest.approx(0.02)
    assert m.expected(0.1) == pytest.approx(0.004)


def test_quiet_playback_teaches_nothing() -> None:
    m = EchoModel(min_samples=1)
    m.learn(0.5, 0.005)  # the speaker is nearly silent: the ratio would be meaningless
    assert m.coupling == m.prior


def test_one_loud_block_does_not_move_the_median() -> None:
    m = EchoModel(min_samples=3, window=9)
    for _ in range(8):
        m.learn(0.025, 0.1)
    m.learn(0.5, 0.1)  # a cough
    assert m.coupling == pytest.approx(0.25)


def test_the_extra_bar_stops_an_echo_from_counting_as_speech() -> None:
    d = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    assert not any(d.feed(0.03, 0.1, extra=0.05) for _ in range(20))  # below the echo allowance
    d2 = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    assert any(d2.feed(0.03, 0.1) for _ in range(5))  # the same level without the allowance


def test_its_own_words_are_recognised_as_an_echo() -> None:
    spoken = "I heard you say hello. Our guide found the bright umbrella by the river every winter."
    assert looks_like_echo("guide found the bright umbrella by the river", spoken)
    assert looks_like_echo("Our guide found the bright umbrella.", spoken)


def test_what_the_user_says_is_not_taken_for_an_echo() -> None:
    spoken = "I heard you say hello. Our guide found the bright umbrella by the river every winter."
    assert not looks_like_echo("Stop.", spoken)  # too short to tell
    assert not looks_like_echo("What is the weather today?", spoken)
    assert not looks_like_echo(
        "Can you repeat the river part please", spoken
    )  # only a bit overlaps


async def test_the_watcher_ignores_an_echo_that_the_plain_threshold_would_have_accepted() -> None:
    s, client = session()

    class Speaker:
        def level_recent(self) -> float:
            return 0.1  # it is playing loudly

    async def handle(idx: int, heard: HeardTurn) -> None:
        s._answer_started = 1.0
        s._player = Speaker()  # type: ignore[assignment]
        await asyncio.sleep(0.6)

    s.handle = handle  # type: ignore[method-assign]
    # the microphone hears 0.05 (well above 4x the noise) = a quarter-ish of what is played
    client.hub.put(*([0.05] * 12))
    assert await s.respond(1, turn("who is hermione")) is None


async def test_the_same_level_does_interrupt_when_it_is_far_above_the_learned_echo() -> None:
    s, client = session()

    class Speaker(FakePlayer):
        def level_recent(self) -> float:
            return 0.1

    async def handle(idx: int, heard: HeardTurn) -> None:
        s._answer_started = 1.0
        s._player = Speaker()  # type: ignore[assignment]
        await asyncio.sleep(10)

    s.handle = handle  # type: ignore[method-assign]
    # 0.6 s of faint echo (coupling 0.02) teaches the estimate, then a real voice
    client.hub.put(*([0.002] * 6), *([0.2] * 6))
    result = await asyncio.wait_for(s.respond(1, turn("who is hermione")), timeout=5)
    assert result is not None and result.kind == "barge_in"


# ---- "stop" is said to make it stop talking; it does not quit the session ----


async def test_stop_and_wait_only_silence_it_and_do_not_quit_or_start_an_answer() -> None:
    s, client = session()
    s.args.ask_to_add = False
    answered: list[str] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        answered.append(heard.text)

    s.handle = handle  # type: ignore[method-assign]
    client.heard.extend([turn("Stop."), turn("Wait, wait."), turn("Who is Ron?"), turn("Quit.")])
    await asyncio.wait_for(s.run_mic(), timeout=10)

    assert answered == ["Who is Ron?"]  # "stop" and "wait" neither ended the run nor were answered
    assert [r.get("stop_word") for r in s.records[:2]] == [True, True]


async def test_the_stop_attempts_answered_in_the_last_session_now_only_silence() -> None:
    # the three transcripts (cue-test-1) that were answered as questions and made it talk again
    s, client = session()
    s.args.ask_to_add = False
    answered: list[str] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        answered.append(heard.text)

    s.handle = handle  # type: ignore[method-assign]
    client.heard.extend(
        [
            turn("A young pilot. Wait, stop!"),  # its own words came back, then the user said stop
            turn("Quick, stop!"),  # "wait" heard as "quick"
            turn("Mr. Ballard. Wait, stop."),
            turn("Who is Ron?"),
            turn("Quit."),
        ]
    )
    await asyncio.wait_for(s.run_mic(), timeout=10)
    assert answered == ["Who is Ron?"]
    assert [r.get("stop_word") for r in s.records[:3]] == [True, True, True]


# ---- the Silero VAD, as main's design would run it ----

from pathlib import Path  # noqa: E402

SILERO = Path("assets/silero_vad.onnx")
needs_silero = pytest.mark.skipif(
    not SILERO.exists(), reason="Pipecat's silero_vad.onnx not present"
)


@needs_silero
def test_silero_ignores_silence_and_noise_bursts() -> None:
    from doc_chat.bargein import SileroOnset

    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 0.12, SR) * 32767).astype(np.int16).tobytes()  # 1 s, loud
    quiet = (rng.normal(0, 0.003, SR) * 32767).astype(np.int16).tobytes()
    v = SileroOnset(str(SILERO))
    for audio in (quiet, noise, quiet):
        assert not any(v.feed(audio[i : i + 3200]) for i in range(0, len(audio), 3200))


@needs_silero
def test_silero_needs_the_speech_to_last_the_minimum_time() -> None:
    from doc_chat.bargein import SileroOnset

    # a synthetic vowel-like buzz is not speech to a neural model: this checks the plumbing only
    v = SileroOnset(str(SILERO), min_s=0.25)
    v.confidence = -1.0  # treat every window as speech
    hits = [v.feed(block(0.1)) for _ in range(5)]
    assert hits[:2] == [False, True] or hits[0] is False  # 100 ms blocks: 3 windows = 96 ms each
    assert hits[-1] is True


@needs_silero
def test_silero_reset_matches_a_fresh_instance() -> None:
    from doc_chat.bargein import SileroOnset

    rng = np.random.default_rng(1)
    audio = (rng.normal(0, 0.05, SR) * 32767).astype(np.int16).tobytes()
    fresh = SileroOnset(str(SILERO), keep_history=True)
    for i in range(0, len(audio), 3200):
        fresh.feed(audio[i : i + 3200])
    reused = SileroOnset(str(SILERO), keep_history=True)
    for i in range(0, len(audio), 3200):  # leave state behind, then wipe it
        reused.feed(audio[i : i + 3200])
    reused.reset()
    assert reused.history == [] and reused.last_confidence == 0.0
    for i in range(0, len(audio), 3200):
        reused.feed(audio[i : i + 3200])
    assert reused.history == fresh.history


# ---- BargeInGuard: the user versus the assistant's own voice coming back ----


def _blocks(level: float, n: int) -> list[bytes]:
    """n 100 ms blocks of a noise-like signal at the given RMS (0-1)."""
    rng = np.random.default_rng(7)
    return [(rng.normal(0, level, SR // 10) * 32767).astype(np.int16).tobytes() for _ in range(n)]


def _run(guard: object, blocks: list[bytes], played: float, answering: bool = True) -> list[bool]:
    from doc_chat.bargein import BargeInGuard

    assert isinstance(guard, BargeInGuard)
    return [guard.feed(b, played, answering) for b in blocks]


def _guard(coupling_prior: float = 0.3) -> object:
    from doc_chat.bargein import BargeInGuard, EchoModel

    return BargeInGuard(
        0.01, ratio=4.0, min_s=0.3, mode="energy", echo=EchoModel(prior=coupling_prior, margin=1.8)
    )


def test_guard_ignores_echo_at_the_expected_coupling() -> None:
    # played 0.3 loud, the speaker leaks 30% of it back: the microphone hears 0.09
    assert not any(_run(_guard(), _blocks(0.09, 30), played=0.3))


def test_guard_hears_a_user_clearly_above_the_echo() -> None:
    g = _guard()
    _run(g, _blocks(0.09, 10), played=0.3)  # the echo alone, so the coupling is learned
    fired = _run(g, _blocks(0.35, 8), played=0.3)  # the user, about 12 dB above it
    assert any(fired)


def test_guard_cannot_separate_a_user_no_louder_than_the_echo() -> None:
    # the known limit of a level guard: speech at the echo's level is indistinguishable from it
    g = _guard()
    _run(g, _blocks(0.09, 10), played=0.3)
    assert not any(_run(g, _blocks(0.10, 10), played=0.3))


def test_guard_idle_phase_needs_no_echo_allowance() -> None:
    # the assistant is not speaking yet: ordinary speech above the room is enough
    assert any(_run(_guard(), _blocks(0.1, 8), played=0.0, answering=False))


def test_guard_default_prior_is_cautious_about_a_leaky_speaker() -> None:
    from doc_chat.bargein import BargeInGuard, EchoModel

    # a speaker leaking 47% of its level back (loud, near the mic): the default prior (0.5) holds
    g = BargeInGuard(0.01, mode="energy", echo=EchoModel())
    assert not any(_run(g, _blocks(0.14, 30), played=0.3))


def test_guard_with_an_optimistic_prior_never_learns_a_leaky_speaker() -> None:
    # blocks above the bar are taken for the user and not learned from, so a too-low prior keeps
    # firing on the echo: why the prior is cautious (docs/BARGE-IN.md)
    g = _guard(coupling_prior=0.05)
    assert sum(_run(g, _blocks(0.15, 40), played=0.3)) > 20


@needs_silero
def test_guard_one_loud_block_of_echo_does_not_fire_with_the_model() -> None:
    from doc_chat.bargein import BargeInGuard, EchoModel, SileroOnset

    silero = SileroOnset(str(SILERO), min_s=0.3)
    silero.confidence = -1.0  # the model calls every window speech, as it does for the TTS voice
    g = BargeInGuard(0.01, mode="both", silero=silero, echo=EchoModel())
    quiet, loud = _blocks(0.05, 12), _blocks(0.5, 1)
    fired = _run(g, quiet[:6] + loud + quiet[6:], played=0.3)  # one burst among echo-level blocks
    assert not any(fired)


@needs_silero
def test_guard_sustained_speech_above_the_echo_fires_with_the_model() -> None:
    from doc_chat.bargein import BargeInGuard, EchoModel, SileroOnset

    silero = SileroOnset(str(SILERO), min_s=0.3)
    silero.confidence = -1.0
    g = BargeInGuard(0.01, mode="both", silero=silero, echo=EchoModel())
    _run(g, _blocks(0.05, 8), played=0.3)  # the echo alone
    assert any(_run(g, _blocks(0.5, 8), played=0.3))  # the user, well above it, for 0.8 s


def test_kofn_flag_parses_and_rejects_nonsense() -> None:
    import pytest as _pytest

    from doc_chat.__main__ import _kofn

    assert _kofn("7/10") == (7, 10) and _kofn(None) is None and _kofn("") is None
    for bad in ("7", "0/10", "11/10", "a/b", "7/"):
        with _pytest.raises(SystemExit):
            _kofn(bad)


@needs_silero
def test_silero_kofn_rides_over_a_dip_that_resets_the_strict_rule() -> None:
    from doc_chat.bargein import SileroOnset

    def run(v: SileroOnset) -> bool:
        pattern = [1.0] * 6 + [0.0] + [1.0] * 4  # speech windows with one dip inside
        hit = False
        for c in pattern:
            v.confidence = 0.5
            v._confidence = lambda w, c=c: c  # type: ignore[method-assign]
            hit = v.feed(np.zeros(SileroOnset.WINDOW * 2, dtype=np.int16).tobytes()[:1024]) or hit
        return hit

    assert run(SileroOnset(str(SILERO), min_s=0.25, kofn=(7, 10)))
    assert not run(SileroOnset(str(SILERO), min_s=0.25))  # unbroken 0.25 s needs 8 windows


def test_right_after_talking_over_it_a_line_ending_in_stop_is_the_order() -> None:
    from doc_chat.__main__ import _is_stop_phrase

    # live: "wait" was lost and its own words came back with the order
    for text in (
        "steve knopf public stop",
        "the provided text is not stop",
        "oh child of god stop",
    ):
        assert _is_stop_phrase(text.split(), after_interrupt=True), text
        assert not _is_stop_phrase(text.split()), text  # but not as a question out of the blue
    question = "tell me about the bus stop sign"
    assert not _is_stop_phrase(question.split(), after_interrupt=True)


def test_stop_phrases_silence_the_assistant_but_questions_do_not() -> None:
    from doc_chat.__main__ import _is_stop_phrase

    for yes in (
        "a young pilot wait stop",  # its own words came back, then the user said stop
        "mr ballard wait stop",
        "stop",
        "wait stop",
        "stop wait",
        "hold on please",
        "wait wait",
        "ok stop it",
        "enough",
    ):
        assert _is_stop_phrase(yes.split()), yes
    for no in (
        "why did the stop sign",
        "tell me about the road",
        "on it",
        "please",
        "wait for me to finish my long question",
        "why did the road stop",
        "",
    ):
        assert not _is_stop_phrase(no.split()), no


def test_echo_model_quantile_follows_the_peaks_not_the_middle() -> None:
    from doc_chat.bargein import EchoModel

    typical = EchoModel(window=60)
    peaky = EchoModel(window=60, quantile=0.95)
    for m in (typical, peaky):
        for r in [0.3] * 54 + [0.8] * 6:  # a steady echo with an occasional loud syllable
            m.learn(r * 0.2, 0.2)
    assert abs(typical.coupling - 0.3) < 1e-6
    assert peaky.coupling > 0.7
    assert peaky.expected(0.2) > typical.expected(0.2) * 2


def test_guard_with_a_peak_following_bar_ignores_a_spiky_echo() -> None:
    from doc_chat.bargein import BargeInGuard, EchoModel

    def run(echo: EchoModel) -> bool:
        g = BargeInGuard(0.01, mode="energy", echo=echo, level_kofn=(3, 8))
        spiky = [0.16 if i % 8 in (0, 3, 6) else 0.05 for i in range(80)]  # 3 in 8 at ratio 0.8
        learn = [(lv, 0.2) for lv in spiky[:40]]
        for lv, pl in learn:  # learning phase: the echo alone, below the bar
            echo.learn(lv, pl)
        blocks = [b for lv in spiky[40:] for b in _blocks(lv, 1)]
        return any(_run(g, blocks, played=0.2))

    assert run(EchoModel(prior=0.8, margin=1.0, window=60))  # follows the median: the peaks fire
    assert not run(EchoModel(prior=0.8, margin=1.2, window=60, quantile=0.95))


def test_a_pivot_loses_its_wait_stop_and_keeps_the_question() -> None:
    from doc_chat.__main__ import _strip_stop_prefix

    assert _strip_stop_prefix("Wait, stop. Tell me about Suttree instead.") == (
        "Tell me about Suttree instead."
    )
    assert _strip_stop_prefix("Hold on, wait stop, what typewriter did he use?") == (
        "What typewriter did he use?"
    )
    # nothing to remove, or nothing left to ask: unchanged
    for text in ("Tell me about the road.", "No Country for Old Men?", "Wait, stop.", "Stop"):
        assert _strip_stop_prefix(text) == text
    # "wait" that is part of the question stays
    assert _strip_stop_prefix("Wait times at the library?") == "Wait times at the library?"


async def test_a_pivot_stops_and_answers_the_new_question_without_the_order() -> None:
    s, client = session()
    s.args.ask_to_add = False
    answered: list[str] = []
    pivots: list[str | None] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        answered.append(heard.text)
        pivots.append(s._pivot_from)  # what was said before "wait, stop" was cut off the front

    s.handle = handle  # type: ignore[method-assign]
    client.heard.extend(
        [turn("Wait, stop. Tell me about Outer Dark instead."), turn("Stop."), turn("Quit.")]
    )
    await asyncio.wait_for(s.run_mic(), timeout=10)
    assert answered == ["Tell me about Outer Dark instead."]
    assert pivots == ["Wait, stop. Tell me about Outer Dark instead."]


def test_a_question_that_ends_in_stop_is_not_swallowed_after_an_interruption() -> None:
    from doc_chat.__main__ import _is_stop_phrase

    assert not _is_stop_phrase(["tell", "me", "about", "the", "bus", "stop"], after_interrupt=True)
    assert _is_stop_phrase(["the", "provided", "text", "is", "not", "stop"], after_interrupt=True)
