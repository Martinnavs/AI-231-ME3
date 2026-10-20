"""Tests for LAN speech protocol serialization and deserialization (P2)."""

from __future__ import annotations

import json

import pytest

from lan_speech.protocol import (
    CancelMessage,
    DoneMessage,
    ErrorMessage,
    FinalizeMessage,
    HealthzResponse,
    ProtocolError,
    ReadyMessage,
    SpeakMessage,
    StartMessage,
    TranscriptMessage,
    VoiceItem,
    pack_tts_audio,
    parse_message,
    unpack_tts_audio,
)


def test_protocol_roundtrip() -> None:
    # 1. StartMessage
    start = StartMessage(sample_rate=16000)
    raw = start.to_json()
    assert StartMessage.from_json(raw) == start
    assert parse_message(raw) == start

    # 2. FinalizeMessage
    fin = FinalizeMessage()
    raw = fin.to_json()
    assert FinalizeMessage.from_json(raw) == fin
    assert parse_message(raw) == fin

    # 3. TranscriptMessage (final)
    tr_final = TranscriptMessage(
        text="turn on the lights", is_final=True, end_of_turn=True, t_audio_end_s=1.25
    )
    raw = tr_final.to_json()
    assert TranscriptMessage.from_json(raw) == tr_final
    assert parse_message(raw) == tr_final

    # TranscriptMessage (interim, null t_audio_end_s)
    tr_interim = TranscriptMessage(
        text="turn on", is_final=False, end_of_turn=False, t_audio_end_s=None
    )
    raw = tr_interim.to_json()
    assert TranscriptMessage.from_json(raw) == tr_interim
    assert parse_message(raw) == tr_interim

    # 4. ErrorMessage
    err = ErrorMessage(status=400, reason="start required")
    raw = err.to_json()
    assert ErrorMessage.from_json(raw) == err
    assert parse_message(raw) == err

    # 5. ReadyMessage
    ready = ReadyMessage(sample_rate=44100)
    raw = ready.to_json()
    assert ReadyMessage.from_json(raw) == ready
    assert parse_message(raw) == ready

    # 6. SpeakMessage (with voice)
    speak = SpeakMessage(context=42, text="hello world", voice="F1")
    raw = speak.to_json()
    assert SpeakMessage.from_json(raw) == speak
    assert parse_message(raw) == speak

    # SpeakMessage (without voice)
    speak_no_voice = SpeakMessage(context=43, text="hello world", voice=None)
    raw = speak_no_voice.to_json()
    assert SpeakMessage.from_json(raw) == speak_no_voice
    assert parse_message(raw) == speak_no_voice

    # 7. CancelMessage
    cancel = CancelMessage(context=42)
    raw = cancel.to_json()
    assert CancelMessage.from_json(raw) == cancel
    assert parse_message(raw) == cancel

    # 8. DoneMessage
    done = DoneMessage(context=42)
    raw = done.to_json()
    assert DoneMessage.from_json(raw) == done
    assert parse_message(raw) == done

    # 9. VoiceItem
    voice = VoiceItem(id="F1", name="F1 Voice", language="en")
    d = voice.to_dict()
    assert VoiceItem.from_dict(d) == voice

    # 10. HealthzResponse
    healthz = HealthzResponse(ok=True, stt="sherpa-zipformer", tts="supertonic-3")
    d = healthz.to_dict()
    assert HealthzResponse.from_dict(d) == healthz

    # 11. TTS audio packing
    pcm = b"\x00\x01\x02\x03"
    packed = pack_tts_audio(99, pcm)
    ctx, out_pcm = unpack_tts_audio(packed)
    assert ctx == 99
    assert out_pcm == pcm


def test_protocol_unknown_type_rejected() -> None:
    with pytest.raises(ProtocolError):
        parse_message('{"type": "bogus_action"}')

    with pytest.raises(ProtocolError):
        parse_message('{"no_type": 123}')

    with pytest.raises(ProtocolError):
        unpack_tts_audio(b"\x00\x01")  # too short


def test_split_clauses() -> None:
    from lan_speech.protocol import split_clauses

    assert split_clauses("") == []
    assert split_clauses("   ") == []

    # Single clause <= 6 words
    assert split_clauses("Sure,") == ["Sure,"]
    assert split_clauses("Sure, the kitchen lights are on.") == [
        "Sure,",
        "the kitchen lights are on.",
    ]

    # Sentence with first chunk > 6 words
    res = split_clauses("One two three four five six seven eight nine.")
    assert res == ["One two three four five six", "seven eight nine."]

    # Punctuation splits
    res2 = split_clauses("First clause; second clause: third clause! Fourth clause? Fifth clause.")
    assert res2 == [
        "First clause;",
        "second clause:",
        "third clause!",
        "Fourth clause?",
        "Fifth clause.",
    ]


def test_split_speech_chunks_keeps_initials_and_abbreviations() -> None:
    from lan_speech.protocol import split_sentences, split_speech_chunks

    text = "J.K. Rowling wrote it. Dr. Smith agreed, e.g. in Paris."
    assert split_sentences(text) == ["J.K. Rowling wrote it.", "Dr. Smith agreed, e.g. in Paris."]
    chunks = [c for c, _ in split_speech_chunks(text)]
    assert chunks[0] == "J.K. Rowling wrote it."
    assert "Dr. Smith agreed," in chunks
    assert not any(c.endswith(("J.K.", "Dr.", "e.g.")) for c in chunks)


def test_split_speech_chunks_quote_enders_and_i_period() -> None:
    from lan_speech.protocol import split_sentences

    assert split_sentences('He said "stop." Then he left. So did I. Bye') == [
        'He said "stop."',
        "Then he left.",
        "So did I.",
        "Bye",
    ]


def test_pauses_are_shorter_for_commas_than_sentence_ends() -> None:
    from lan_speech.protocol import (
        COMMA_PAUSE_S,
        SENTENCE_PAUSE_S,
        split_speech_chunks,
    )

    chunks = split_speech_chunks("Well, yes; indeed: fine. Done!")
    pauses = {c: p for c, p in chunks}
    assert pauses["Well,"] == COMMA_PAUSE_S
    assert pauses["fine."] == SENTENCE_PAUSE_S
    assert COMMA_PAUSE_S < pauses["yes;"] < SENTENCE_PAUSE_S
    assert chunks[-1][1] == 0.0
    assert COMMA_PAUSE_S < SENTENCE_PAUSE_S


def test_start_hints_round_trip_and_are_omitted_when_unset() -> None:
    plain = json.loads(StartMessage().to_json())
    assert set(plain) == {"type", "sample_rate"}
    hinted = StartMessage(sample_rate=16000, slot="contact", risk="high")
    assert StartMessage.from_json(hinted.to_json()) == hinted
    assert parse_message(hinted.to_json()) == hinted


def test_transcript_repair_details_round_trip_and_are_omitted_when_unset() -> None:
    plain = json.loads(TranscriptMessage("hi", True, True).to_json())
    assert "raw_text" not in plain and "repair" not in plain
    msg = TranscriptMessage(
        "call Angelo",
        True,
        True,
        t_audio_end_s=1.5,
        raw_text="call angeilo",
        repair={"rung": "silent", "matches": [{"heard": "angeilo", "entity": "Angelo"}]},
    )
    assert TranscriptMessage.from_json(msg.to_json()) == msg
