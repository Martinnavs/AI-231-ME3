"""Preloaded fast-path phrases play from memory: no server call, handed on in 100 ms slices."""

import asyncio
import time

from doc_chat.agent_client import FAST_PHRASES, load_fast_phrases
from doc_chat.speech import DocChatSpeechClient


def _client() -> DocChatSpeechClient:
    client = DocChatSpeechClient(
        "ws://127.0.0.1:1"
    )  # never connected: a cached phrase needs no socket
    client.tts_sample_rate = 1000  # 100 ms = 100 samples = 200 bytes
    return client


def test_a_cached_phrase_is_played_without_the_server_in_100_ms_slices() -> None:
    client = _client()
    pcm = bytes(range(256)) * 3  # 768 bytes
    client.audio_cache[("The lights are on.", "F1")] = pcm
    slices: list[bytes] = []
    t_speech_end = time.perf_counter() - 1.0
    out, first_ms, total_ms, to_first = asyncio.run(
        client.speak_text(
            "The lights are on.", voice="F1", t_speech_end=t_speech_end, on_chunk=slices.append
        )
    )
    assert out == pcm and b"".join(slices) == pcm
    assert [len(s) for s in slices] == [200, 200, 200, 168]
    assert first_ms < 50 and 990 < to_first < 1100  # measured from the end of speech


def test_a_phrase_that_is_not_cached_still_needs_the_server() -> None:
    client = _client()
    client.audio_cache[("The lights are on.", "F1")] = b"\x00\x00"
    try:
        asyncio.run(client.speak_text("Something else.", voice="F1"))
    except RuntimeError as exc:
        assert "not connected" in str(exc)
    else:
        raise AssertionError("an uncached phrase must go to the server")
    try:  # a cached phrase for another voice is a different phrase
        asyncio.run(client.speak_text("The lights are on.", voice="M1"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("the cache is per voice")


def test_the_phrase_file_loads() -> None:
    phrases = load_fast_phrases()
    assert FAST_PHRASES.exists() and len(phrases) == 8 and "Paused." in phrases
