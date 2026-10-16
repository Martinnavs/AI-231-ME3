"""Integration tests for doc_chat session (P4, P5)."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import wave
from collections.abc import Generator
from pathlib import Path

import pytest

pytest.importorskip("websockets")
pytest.importorskip("langchain_core")
pytest.importorskip("langchain_community")

from doc_chat.__main__ import main as doc_chat_main  # noqa: E402
from doc_chat.speech import DocChatSpeechClient  # noqa: E402
from lan_speech.engines import FakeSTTEngine, FakeSTTStream, FakeTTSEngine  # noqa: E402
from lan_speech.server import LanSpeechServer  # noqa: E402


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def make_dummy_wav(file_path: Path, duration_s: float = 0.2) -> Path:
    n_samples = int(16000 * duration_s)
    with wave.open(str(file_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * n_samples)
    return file_path


@pytest.fixture
def running_fake_server() -> Generator[tuple[int, str], None, None]:
    port = find_free_port()
    token = "test-doc-chat-token-42"
    server = LanSpeechServer(
        stt_engine=FakeSTTEngine(
            stream_factory=lambda: FakeSTTStream(finalize_text="audio configuration")
        ),
        tts_engine=FakeTTSEngine(delay_s=0.01),
        host="127.0.0.1",
        port=port,
        token=token,
    )
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        ready.set()
        loop.run_forever()

    th = threading.Thread(target=run_loop, daemon=True)
    th.start()
    ready.wait(timeout=5.0)

    try:
        yield port, token
    finally:
        asyncio.run_coroutine_threadsafe(server.stop(), loop).result(timeout=5.0)
        loop.call_soon_threadsafe(loop.stop)
        th.join(timeout=5.0)


def test_session_end_to_end(
    running_fake_server: tuple[int, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, token = running_fake_server
    monkeypatch.setenv("LAN_SPEECH_TOKEN", token)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "audio.md").write_text(
        "# Audio Configuration\n\n"
        "Here is the audio configuration. "
        "You can turn on the lights using voice commands. "
        "The system records audio at sixteen kilohertz mono."
    )

    wav1 = make_dummy_wav(tmp_path / "turn1.wav", duration_s=0.2)
    wav2 = make_dummy_wav(tmp_path / "turn2.wav", duration_s=0.2)

    out_dir = tmp_path / "out_wavs"
    log_file = tmp_path / "turns.jsonl"

    argv = [
        "--server",
        f"ws://127.0.0.1:{port}",
        "--docs",
        str(docs_dir),
        "--wav",
        str(wav1),
        str(wav2),
        "--out",
        str(out_dir),
        "--log",
        str(log_file),
    ]

    ret = doc_chat_main(argv)
    assert ret == 0

    assert log_file.is_file()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    for line in lines:
        data = json.loads(line)
        assert data["turn"] in (1, 2)
        assert data["question"] in ("audio configuration", "turn on the lights")
        assert data["answer_words"] > 0
        assert data["stt_final_ms"] is not None and data["stt_final_ms"] >= 0
        assert data["retrieval_ms"] is not None and data["retrieval_ms"] >= 0
        assert data["tts_first_audio_ms"] is not None and data["tts_first_audio_ms"] >= 0
        assert data["tts_total_ms"] is not None and data["tts_total_ms"] >= 0
        assert (
            data["speech_end_to_first_audio_ms"] is not None
            and data["speech_end_to_first_audio_ms"] >= 0
        )
        assert data["source"] == "audio.md"

    # Verify output WAV files per turn
    assert (out_dir / "turn01.wav").is_file()
    assert (out_dir / "turn01.wav").stat().st_size > 0
    assert (out_dir / "turn02.wav").is_file()
    assert (out_dir / "turn02.wav").stat().st_size > 0


@pytest.mark.asyncio
async def test_cancel_on_new_turn(running_fake_server: tuple[int, str]) -> None:
    port, token = running_fake_server
    client = DocChatSpeechClient(server_url=f"ws://127.0.0.1:{port}", token=token)
    await client.connect()

    try:
        # Start speaking context 1
        _speak_task = asyncio.create_task(
            client.speak_text(
                "First long sentence that is currently being spoken.",
                voice="F1",
            )
        )
        await asyncio.sleep(0.01)

        # Barge-in: new turn arrives, cancel context 1
        cancel_to_silence_ms = await client.cancel_current()
        assert cancel_to_silence_ms >= 0
        assert _speak_task.done() or _speak_task.cancelled()

        # Now context 2 speaks cleanly
        pcm2, first2, tot2, sp2 = await client.speak_text(
            "Second turn after cancel.",
            voice="F1",
        )
        assert len(pcm2) > 0
        assert first2 >= 0
        assert tot2 >= 0
    finally:
        await client.close()
