"""CLI loop and timing for voice doc-chat (ticket 24)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from doc_chat.chain import (
    SentenceStream,
    build_answer_chain,
    build_chat_chain,
    build_sentence_streamer,
)
from doc_chat.corpus import load_and_split_corpus, tokenize
from doc_chat.speech import DocChatSpeechClient, SpokenTurn

_QUIT_WORDS = {"quit", "exit", "goodbye", "stop"}


def _p50_p95(vals: list[float]) -> tuple[float, float]:
    if not vals:
        return 0.0, 0.0
    arr = sorted(vals)
    n = len(arr)
    k50 = int(round(0.50 * (n - 1)))
    k95 = int(round(0.95 * (n - 1)))
    return arr[k50], arr[k95]


def save_wav(file_path: Path, pcm_data: bytes, sample_rate: int) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(file_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)


def _warn(error: str | None) -> None:
    if error:
        print(f"  [llm failed - {error}]", file=sys.stderr, flush=True)


def timing_line(rec: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in (
        ("stt_final_ms", "stt"),
        ("retrieval_ms", "retrieval"),
        ("llm_first_token_ms", "llm first token"),
        ("llm_ms", "llm"),
        ("tts_first_audio_ms", "tts first audio"),
    ):
        value = rec.get(key)
        if value:
            parts.append(f"{label} {value:.0f} ms")
    heard = rec.get("speech_end_to_first_audio_ms")
    if heard is not None:
        parts.append(f"answer starts {heard:.0f} ms after you stopped")
    return "  [" + " | ".join(parts) + "]"


def _failed_record(turn: int, question: str, exc: Exception) -> dict[str, Any]:
    return {
        "turn": turn,
        "question": question,
        "answer_words": 0,
        "stt_final_ms": None,
        "retrieval_ms": None,
        "tts_first_audio_ms": None,
        "tts_total_ms": None,
        "speech_end_to_first_audio_ms": None,
        "cancel_to_silence_ms": None,
        "source": None,
        "error": str(exc),
    }


@dataclass
class Session:
    args: argparse.Namespace
    client: DocChatSpeechClient
    log_file: IO[str]
    out_dir: Path | None
    chain: Any | None = None
    streamer: Callable[[str], SentenceStream] | None = None
    records: list[dict[str, Any]] = field(default_factory=list)

    def log(self, rec: dict[str, Any]) -> None:
        self.log_file.write(json.dumps(rec) + "\n")
        self.log_file.flush()
        self.records.append(rec)

    async def turn(
        self, idx: int, question: str, t_speech_end: float, stt_final_ms: float | None
    ) -> None:
        """Answer one question and speak it; records the timing of every stage."""
        from doc_chat.playback import Player

        print(f"you:  {question}", flush=True)
        player = Player(self.client.tts_sample_rate) if self.args.play else None
        on_chunk = player.push if player else None
        try:
            stream: SentenceStream | None = None
            if self.streamer is not None:
                stream = self.streamer(question)
                spoken = await self.client.speak_sentences(
                    stream,
                    voice=self.args.voice,
                    t_speech_end=t_speech_end,
                    on_chunk=on_chunk,
                    on_sentence=lambda s: print(f"bot:  {s}", flush=True),
                )
                _warn(stream.error)
                source, retrieval_ms = stream.source, stream.retrieval_ms
                llm_ms, first_token, llm_error = stream.llm_ms, stream.first_token_ms, stream.error
            else:
                assert self.chain is not None
                ans = await self.chain.ainvoke(question)
                _warn(ans.llm_error)
                print(f"bot:  {ans.text}", flush=True)
                pcm, first_ms, total_ms, to_first = await self.client.speak_text(
                    ans.text, voice=self.args.voice, t_speech_end=t_speech_end, on_chunk=on_chunk
                )
                spoken = SpokenTurn(pcm, first_ms, total_ms, to_first, [ans.text])
                source, retrieval_ms = ans.source, ans.retrieval_ms
                llm_ms, first_token, llm_error = ans.llm_ms, None, ans.llm_error
        finally:
            if player is not None:
                await asyncio.to_thread(player.close)

        if self.out_dir is not None:
            save_wav(self.out_dir / f"turn{idx:02d}.wav", spoken.pcm, self.client.tts_sample_rate)

        def rounded(value: float | None) -> float | None:
            return None if value is None else round(value, 2)

        rec = {
            "turn": idx,
            "question": question,
            "answer_words": sum(len(s.split()) for s in spoken.sentences),
            "stt_final_ms": rounded(stt_final_ms),
            "retrieval_ms": rounded(retrieval_ms),
            "llm_ms": rounded(llm_ms),
            "llm_first_token_ms": rounded(first_token),
            "llm_error": llm_error,
            "tts_first_audio_ms": round(spoken.first_audio_ms, 2),
            "tts_total_ms": round(spoken.total_ms, 2),
            "speech_end_to_first_audio_ms": round(spoken.speech_end_to_first_ms, 2),
            "cancel_to_silence_ms": None,
            "source": source,
        }
        self.log(rec)
        print(timing_line(rec), flush=True)

    async def run_text(self) -> None:
        try:
            await self.turn(1, self.args.text, time.perf_counter(), None)
        except Exception as exc:
            self.log(_failed_record(1, self.args.text, exc))

    async def run_wavs(self) -> None:
        for idx, wav_item in enumerate(self.args.wav, start=1):
            wav_file = Path(wav_item)
            try:
                transcript, stt_final_ms, t_speech_end = await self.client.send_stt_wav(wav_file)
                question = transcript.strip()
                if not question:
                    rec = _failed_record(idx, "", RuntimeError(""))
                    rec.pop("error")
                    rec["stt_final_ms"] = round(stt_final_ms, 2)
                    self.log(rec)
                    continue
                await self.turn(idx, question, t_speech_end, stt_final_ms)
            except Exception as exc:
                self.log(_failed_record(idx, str(wav_file), exc))

    async def run_mic(self) -> None:
        print("Listening... speak, then pause. Say 'quit' or press Ctrl-C to stop.", flush=True)
        idx = 0
        while True:
            idx += 1
            question, t_speech_end = await self.client.listen_mic(
                on_partial=lambda t: print(f"\r  hearing: {t[:70]:<70}", end="", flush=True)
            )
            print("\r" + " " * 90 + "\r", end="")
            if question.lower().strip(" .!?") in _QUIT_WORDS:
                break
            try:
                await self.turn(idx, question, t_speech_end, None)
            except Exception as exc:
                print(f"  [turn failed - {type(exc).__name__}]", file=sys.stderr, flush=True)
                self.log(_failed_record(idx, question, exc))


def _summary(records: list[dict[str, Any]]) -> None:
    valid = [r for r in records if r.get("error") is None and r.get("answer_words", 0) > 0]
    if not valid:
        return
    print("\n--- Doc-Chat Latency Summary (ms) ---")
    for name in (
        "stt_final_ms",
        "retrieval_ms",
        "llm_first_token_ms",
        "llm_ms",
        "tts_first_audio_ms",
        "tts_total_ms",
        "speech_end_to_first_audio_ms",
    ):
        vals = [r[name] for r in valid if r.get(name) is not None]
        if vals:
            p50, p95 = _p50_p95(vals)
            print(f"{name:<30} p50: {p50:>7.1f} ms    p95: {p95:>7.1f} ms")


async def async_main(args: argparse.Namespace) -> int:
    if args.mic or args.play:
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            print("install the laptop group for --mic/--play", file=sys.stderr)
            return 2

    system_prompt: str | None = None
    if args.system_prompt:
        try:
            system_prompt = Path(args.system_prompt).read_text(encoding="utf-8")
        except OSError:
            print("system prompt file not found", file=sys.stderr)
            return 2

    llm = None
    if args.llm == "qwen":
        from doc_chat.qwen import build_qwen_chat_model

        llm = build_qwen_chat_model(
            model=args.llm_model,
            timeout=args.llm_timeout,
            max_tokens=args.llm_max_tokens,
            temperature=args.llm_temperature,
        )

    retriever = None
    if not args.no_docs:
        chunks = load_and_split_corpus(args.docs)
        from langchain_community.retrievers import BM25Retriever

        retriever = BM25Retriever.from_documents(chunks, k=2, preprocess_func=tokenize)

    client = DocChatSpeechClient(server_url=args.server)
    await client.connect()

    log_path = Path(args.log) if args.log else Path("out/doc_chat/turns.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out) if args.out else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as log_file:
        session = Session(args=args, client=client, log_file=log_file, out_dir=out_dir)
        if args.stream:
            assert llm is not None
            session.streamer = build_sentence_streamer(
                retriever, llm, max_words=args.max_words, system_prompt=system_prompt
            )
        elif retriever is None:
            assert llm is not None
            session.chain = build_chat_chain(
                llm, max_words=args.max_words, system_prompt=system_prompt
            )
        else:
            session.chain = build_answer_chain(
                retriever, max_words=args.max_words, llm=llm, system_prompt=system_prompt
            )
        try:
            if args.text is not None:
                await session.run_text()
            elif args.wav:
                await session.run_wavs()
            elif args.mic:
                await session.run_mic()
        except (KeyboardInterrupt, asyncio.CancelledError):
            # Ctrl-C mid-TTS: send cancel, close sockets, flush jsonl; no traceback (Edge 50)
            await client.cancel_current()
        finally:
            await client.close()

    _summary(session.records)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m doc_chat")
    parser.add_argument(
        "--server",
        default="ws://127.0.0.1:8765",
        help="Speech server URL (default: ws://127.0.0.1:8765)",
    )
    parser.add_argument(
        "--docs",
        default="docs",
        help="Path to documents folder (default: docs)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--wav", nargs="+", help="WAV audio input file(s)")
    group.add_argument("--mic", action="store_true", help="Microphone input via sounddevice")
    group.add_argument("--text", help="Text question string (skips STT)")

    parser.add_argument("--play", action="store_true", help="Play audio via sounddevice")
    parser.add_argument("--out", help="Output directory for generated WAVs")
    parser.add_argument(
        "--log",
        default="out/doc_chat/turns.jsonl",
        help="Turn log output JSONL path (default: out/doc_chat/turns.jsonl)",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=40,
        help="Max words per answer (default: 40)",
    )
    parser.add_argument(
        "--voice",
        default="F1",
        help="TTS voice name (default: F1)",
    )
    parser.add_argument(
        "--llm",
        choices=["none", "qwen"],
        default="none",
        help="LLM backend choice (none or qwen; default: none)",
    )
    parser.add_argument(
        "--no-docs",
        action="store_true",
        help="Skip retrieval: send each question straight to Qwen (needs --llm qwen)",
    )
    qwen = parser.add_argument_group("Qwen options (with --llm qwen)")
    qwen.add_argument(
        "--stream",
        action="store_true",
        help="Speak each sentence as Qwen produces it instead of waiting for the whole reply",
    )
    qwen.add_argument("--llm-model", help="Model id (default: from ONIT_MODEL or opencode config)")
    qwen.add_argument("--llm-timeout", type=float, default=30.0, help="Seconds (default: 30)")
    qwen.add_argument("--llm-max-tokens", type=int, default=160, help="Reply cap (default: 160)")
    qwen.add_argument("--llm-temperature", type=float, default=0.2, help="Default: 0.2")
    qwen.add_argument(
        "--system-prompt",
        help="Text file replacing the built-in system prompt ({context} is where the retrieved "
        "passages go; they are appended if the placeholder is missing)",
    )

    args = parser.parse_args(argv)
    if (args.no_docs or args.stream) and args.llm != "qwen":
        print("--no-docs and --stream need --llm qwen", file=sys.stderr)
        return 2
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
