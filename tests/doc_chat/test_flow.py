"""The improved loop: clarifying questions, yes/no replies and history, through a real Session."""

from __future__ import annotations

import argparse
import io
import time
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from doc_chat.__main__ import Session, timing_line  # noqa: E402
from doc_chat.chain import Answer  # noqa: E402
from doc_chat.dialogue import ChatHistory  # noqa: E402
from doc_chat.speech import HeardTurn  # noqa: E402


class FakeClient:
    tts_sample_rate = 24000

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak_text(self, text: str, voice: str | None = None, **kw: Any) -> Any:
        self.spoken.append(text)
        return b"\x00\x00" * 10, 5.0, 9.0, 5.0


class FakeChain:
    def __init__(self) -> None:
        self.questions: list[str] = []

    async def ainvoke(self, question: str) -> Answer:
        self.questions.append(question)
        return Answer(text=f"Answer to {question}", source=None, retrieval_ms=0.0, llm_ms=1.0)


def heard(text: str, repair: dict[str, Any] | None = None, raw: str | None = None) -> HeardTurn:
    now = time.perf_counter()
    return HeardTurn(text, raw, repair, now - 0.5, now, now - 0.1)


CONFIRM = {
    "rung": "confirm",
    "question": "Did you mean Dumbledore?",
    "matches": [{"heard": "dumbledor", "entity": "Dumbledore", "rung": "confirm"}],
}


def session() -> tuple[Session, FakeClient, FakeChain]:
    client, chain = FakeClient(), FakeChain()
    args = argparse.Namespace(play=False, voice="F1", slot="doc?", risk=None)
    s = Session(
        args=args,
        client=client,  # type: ignore[arg-type]
        log_file=io.StringIO(),
        out_dir=None,
        chain=chain,
        history=ChatHistory(4),
    )
    return s, client, chain


async def test_a_plain_turn_is_answered_and_remembered() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Who is Harry?"))
    assert chain.questions == ["Who is Harry?"]
    assert client.spoken == ["Answer to Who is Harry?"]
    assert s.history.pairs() == [("Who is Harry?", "Answer to Who is Harry?")]


async def test_a_doubtful_name_is_asked_about_instead_of_guessed() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    assert client.spoken == ["Did you mean Dumbledore?"]
    assert chain.questions == []  # nothing was sent to the model yet
    assert s.pending is not None and s.pending.if_yes == "Is Dumbledore a wizard?"


async def test_yes_answers_the_corrected_question() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    await s.handle(2, heard("Yes."))
    assert chain.questions == ["Is Dumbledore a wizard?"]
    assert s.pending is None
    assert s.history.pairs()[0][0] == "Is Dumbledore a wizard?"


async def test_no_asks_to_say_it_again_and_calls_nothing() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    await s.handle(2, heard("No."))
    assert client.spoken[-1] == "Okay. Please say it again."
    assert chain.questions == [] and s.pending is None


async def test_a_rephrased_question_is_taken_as_a_new_one() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    await s.handle(2, heard("Tell me about the Triwizard Tournament"))
    assert chain.questions == ["Tell me about the Triwizard Tournament"]
    assert s.pending is None


async def test_a_question_without_a_candidate_is_asked_and_not_remembered() -> None:
    s, client, chain = session()
    repair = {"rung": "targeted", "question": "Which place was that?"}
    await s.handle(1, heard("Weather in", repair))
    assert client.spoken == ["Which place was that?"]
    assert s.pending is None and chain.questions == []


async def test_the_clarification_is_logged_with_the_rung() -> None:
    s, _, _ = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    rec = s.records[0]
    assert rec["clarification"] == "Did you mean Dumbledore?" and rec["repair_rung"] == "confirm"
    assert rec["answer_words"] == 0


async def test_the_timing_record_shows_who_waited_for_what() -> None:
    s, _, _ = session()
    await s.handle(1, heard("Who is Harry?"))
    rec = s.records[0]
    assert rec["stt_server_ms"] is not None and rec["pause_ms"] is not None
    assert rec["pause_ms"] > 300  # the client's pause before it asked the server to finish
    assert "pause" in timing_line(rec) and "server" in timing_line(rec)


async def test_what_the_recogniser_heard_is_shown_when_repair_changed_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    s, _, _ = session()
    await s.handle(1, heard("Who is Hermione?", None, raw="Who is hermoine?"))
    assert "recognised as: Who is hermoine?" in capsys.readouterr().out


# ---- ask-to-add: an unfinished sentence is followed up, then joined ----

from doc_chat.speech import NoSpeech  # noqa: E402


class FakeModel:
    """P(finished) by lookup: 'play something by' hangs, everything else is done."""

    def prob(self, text: str) -> float:
        return 0.05 if text.lower().rstrip(".").endswith(" by") else 0.9


class FollowUpClient(FakeClient):
    def __init__(self, replies: list[Any]) -> None:
        super().__init__()
        self.replies = replies
        self.joined: list[bytes] = []

    async def listen_turn(self, **kw: Any) -> HeardTurn:
        self.listen_kwargs = kw
        reply = self.replies.pop(0)
        if reply is NoSpeech:
            raise NoSpeech
        return reply  # type: ignore[no-any-return]

    async def hear_pcm(self, pcm: bytes, **kw: Any) -> HeardTurn:
        self.joined.append(pcm)
        return heard("Play something by Taylor Swift.")


def adding_session(replies: list[Any]) -> tuple[Session, FollowUpClient, FakeChain]:
    client, chain = FollowUpClient(replies), FakeChain()
    args = argparse.Namespace(
        play=False,
        voice="F1",
        slot="doc?",
        risk=None,
        add_threshold=0.3,
        add_wait=3.0,
        vad_hold=0.6,
    )
    s = Session(
        args=args,
        client=client,  # type: ignore[arg-type]
        log_file=io.StringIO(),
        out_dir=None,
        chain=chain,
        history=ChatHistory(4),
        turn_model=FakeModel(),  # type: ignore[arg-type]
        add_prompts=[("Got it. Anything to add?", b"\x01\x00" * 4)],
    )
    return s, client, chain


def with_audio(turn: HeardTurn, pcm: bytes) -> HeardTurn:
    from dataclasses import replace

    return replace(turn, pcm=pcm)


async def test_a_finished_turn_is_not_questioned() -> None:
    s, client, _ = adding_session([])
    first = heard("What is the capital of France?")
    assert await s.complete_turn(first) is first
    assert not hasattr(client, "listen_kwargs")  # it never listened for more


async def test_an_unfinished_turn_is_asked_about_and_the_audio_is_joined() -> None:
    first = with_audio(heard("Play something by"), b"\x02\x00" * 6)
    more = with_audio(heard("Taylor Swift."), b"\x03\x00" * 5)
    s, client, _ = adding_session([more])

    result = await s.complete_turn(first)

    assert result.text == "Play something by Taylor Swift."
    assert len(client.joined) == 1
    assert client.joined[0].startswith(b"\x02\x00" * 6) and client.joined[0].endswith(
        b"\x03\x00" * 5
    )
    assert len(client.joined[0]) > 11 * 2  # a stretch of silence sits between the parts
    assert client.listen_kwargs["first_speech_timeout"] == 3.0
    assert s.add_note == {"asked_to_add": True, "unfinished_score": 0.05, "added": "Taylor Swift."}


async def test_silence_keeps_the_turn_as_it_was() -> None:
    s, client, _ = adding_session([NoSpeech])
    first = heard("Play something by")
    assert await s.complete_turn(first) is first
    assert client.joined == []
    assert s.add_note is not None and s.add_note["added"] is None


async def test_a_no_keeps_the_turn_as_it_was() -> None:
    s, client, _ = adding_session([heard("No, that's all.")])
    first = heard("Play something by")
    assert await s.complete_turn(first) is first
    assert client.joined == []


async def test_a_doubtful_name_goes_down_the_clarification_path_instead() -> None:
    s, client, _ = adding_session([])
    first = heard("Is dumbledor a by", CONFIRM)
    assert await s.complete_turn(first) is first
    assert client.spoken == []


async def test_the_joined_turn_is_answered_and_the_note_is_logged() -> None:
    first = with_audio(heard("Play something by"), b"\x02\x00" * 6)
    more = with_audio(heard("Taylor Swift."), b"\x03\x00" * 5)
    s, _client, chain = adding_session([more])

    joined = await s.complete_turn(first)
    await s.handle(1, joined)

    assert chain.questions == ["Play something by Taylor Swift."]
    assert s.records[-1]["asked_to_add"] is True and s.records[-1]["added"] == "Taylor Swift."
    assert s.add_note is None  # consumed by the record


# ---- recording: scores logged without asking, per-turn metadata, session.json ----


async def test_the_score_is_logged_even_when_nothing_is_asked() -> None:
    s, client, _ = adding_session([])
    s.args.ask_to_add = False  # --log-turn-score only
    first = heard("Play something by")
    assert await s.complete_turn(first) is first
    assert s.add_note == {"unfinished_score": 0.05, "asked_to_add": False}
    assert not hasattr(client, "listen_kwargs")


async def test_a_turn_record_keeps_what_is_needed_to_look_back() -> None:
    s, _client, _ = adding_session([])
    turn = heard("Who is Hermione?", raw="who is hermoin")
    from dataclasses import replace

    turn = replace(turn, pcm=b"\x00\x00" * 32000, partial="who is")
    await s.handle(1, turn)
    rec = s.records[-1]
    assert rec["heard"] == "Who is Hermione?" and rec["raw_text"] == "who is hermoin"
    assert rec["partial"] == "who is" and rec["audio_s"] == 2.0
    assert rec["ts"] and "repair" in rec and "noise_floor" in rec


def test_session_info_records_code_and_settings(tmp_path: Any) -> None:
    from doc_chat.__main__ import write_session_info

    args = argparse.Namespace(
        note="quiet room, headset",
        llm="gemini",
        llm_model=None,
        server="ws://127.0.0.1:8765",
        add_threshold=0.3,
        text=None,
        wav=None,
    )
    write_session_info(tmp_path, args, None)
    import json

    info = json.loads((tmp_path / "session.json").read_text())
    assert info["note"] == "quiet room, headset" and info["llm"] == "gemini"
    assert info["settings"]["add_threshold"] == 0.3 and "note" not in info["settings"]
    assert info["started"] and info["python"]


# ---- the echo responder and random phrases (speech tests without a language model) ----


def test_random_phrases_repeat_for_the_same_seed_and_differ_between_seeds() -> None:
    from doc_chat.phrases import phrases, split_in_two

    assert phrases("a", 3) == phrases("a", 3)
    assert phrases("a", 3) != phrases("b", 3)
    first, second = split_in_two(phrases("a")[0] + " and more words here")
    assert first and second and (first + " " + second).split()[0] == first.split()[0]


def test_the_echo_model_repeats_the_question_then_reads_random_sentences() -> None:
    from langchain_core.messages import HumanMessage

    from doc_chat.echo import EchoChatModel

    model = EchoChatModel(first_token_ms=0, word_delay_ms=0, random_sentences=3)
    text = model.invoke([HumanMessage(content="hello there")]).content
    assert isinstance(text, str) and text.startswith("I heard you say: hello there.")
    assert text == model.invoke([HumanMessage(content="hello there")]).content  # repeatable
    assert sum(text.count(c) for c in ".?") >= 4
    streamed = "".join(c.content for c in model.stream([HumanMessage(content="hello there")]))  # type: ignore[misc]
    assert streamed.strip() == text.strip()


def test_llm_echo_is_accepted_without_documents(capsys: pytest.CaptureFixture[str]) -> None:
    from doc_chat.__main__ import main

    # --no-docs/--stream are fine with the echo model; the text path answers straight away
    assert main(["--text", "hi there", "--no-docs", "--llm", "echo", "--echo-delay-ms", "0"]) == 0
    assert "I heard you say: hi there" in capsys.readouterr().out
