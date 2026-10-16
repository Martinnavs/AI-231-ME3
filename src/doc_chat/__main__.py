"""CLI loop and timing for voice doc-chat (ticket 24)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from doc_chat.chain import Answer, build_answer_chain, build_chat_chain
from doc_chat.corpus import load_and_split_corpus, tokenize
from doc_chat.speech import DocChatSpeechClient


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


def _warn_llm(ans: Answer) -> None:
    if ans.llm_error:
        print(f"  [llm failed - {ans.llm_error}]", file=sys.stderr, flush=True)


async def async_main(args: argparse.Namespace) -> int:
    if args.mic or args.play:
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            print("install the laptop group for --mic/--play", file=sys.stderr)
            return 2

    # 1. LLM setup if requested
    llm = None
    if args.llm == "qwen":
        from doc_chat.qwen import build_qwen_chat_model

        llm = build_qwen_chat_model()

    # 2. Answer chain
    if args.no_docs:
        assert llm is not None
        chain = build_chat_chain(llm, max_words=args.max_words)
    else:
        chunks = load_and_split_corpus(args.docs)
        from langchain_community.retrievers import BM25Retriever

        retriever = BM25Retriever.from_documents(chunks, k=2, preprocess_func=tokenize)
        chain = build_answer_chain(retriever, max_words=args.max_words, llm=llm)

    # 3. Speech client
    client = DocChatSpeechClient(server_url=args.server)
    await client.connect()

    log_path = Path(args.log) if args.log else Path("out/doc_chat/turns.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")

    out_dir = Path(args.out) if args.out else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    turn_records: list[dict[str, Any]] = []

    try:
        if args.text is not None:
            # Single text question
            turn_idx = 1
            question = args.text
            t_speech_end = time.perf_counter()
            stt_final_ms = None

            try:
                ans: Answer = await chain.ainvoke(question)
                _warn_llm(ans)
                answer_words = len(ans.text.split()) if ans.text else 0
                retrieval_ms = ans.retrieval_ms
                source = ans.source

                pcm, first_audio, total_ms, speech_to_first = await client.speak_text(
                    ans.text,
                    voice=args.voice,
                    t_speech_end=t_speech_end,
                )

                if out_dir is not None:
                    save_wav(
                        out_dir / f"turn{turn_idx:02d}.wav",
                        pcm,
                        client.tts_sample_rate,
                    )

                if args.play:
                    import numpy as np
                    import sounddevice as sd

                    samples = np.frombuffer(pcm, dtype=np.int16)
                    sd.play(samples, samplerate=client.tts_sample_rate)
                    sd.wait()

                rec: dict[str, Any] = {
                    "turn": turn_idx,
                    "question": question,
                    "answer_words": answer_words,
                    "stt_final_ms": stt_final_ms,
                    "retrieval_ms": round(retrieval_ms, 2),
                    "llm_ms": None if ans.llm_ms is None else round(ans.llm_ms, 2),
                    "llm_error": ans.llm_error,
                    "tts_first_audio_ms": round(first_audio, 2),
                    "tts_total_ms": round(total_ms, 2),
                    "speech_end_to_first_audio_ms": round(speech_to_first, 2),
                    "cancel_to_silence_ms": None,
                    "source": source,
                }
                log_file.write(json.dumps(rec) + "\n")
                log_file.flush()
                turn_records.append(rec)
            except Exception as exc:
                rec = {
                    "turn": turn_idx,
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
                log_file.write(json.dumps(rec) + "\n")
                log_file.flush()
                turn_records.append(rec)

        elif args.wav:
            for turn_idx, wav_item in enumerate(args.wav, start=1):
                wav_file = Path(wav_item)
                try:
                    transcript, stt_final_ms, t_speech_end = await client.send_stt_wav(wav_file)
                    question = transcript.strip()

                    if not question:
                        rec = {
                            "turn": turn_idx,
                            "question": "",
                            "answer_words": 0,
                            "stt_final_ms": round(stt_final_ms, 2),
                            "retrieval_ms": None,
                            "tts_first_audio_ms": None,
                            "tts_total_ms": None,
                            "speech_end_to_first_audio_ms": None,
                            "cancel_to_silence_ms": None,
                            "source": None,
                        }
                        log_file.write(json.dumps(rec) + "\n")
                        log_file.flush()
                        turn_records.append(rec)
                        continue

                    ans = await chain.ainvoke(question)

                    _warn_llm(ans)
                    answer_words = len(ans.text.split()) if ans.text else 0
                    retrieval_ms = ans.retrieval_ms
                    source = ans.source

                    pcm, first_audio, total_ms, speech_to_first = await client.speak_text(
                        ans.text,
                        voice=args.voice,
                        t_speech_end=t_speech_end,
                    )

                    if out_dir is not None:
                        save_wav(
                            out_dir / f"turn{turn_idx:02d}.wav",
                            pcm,
                            client.tts_sample_rate,
                        )

                    if args.play:
                        import numpy as np
                        import sounddevice as sd

                        samples = np.frombuffer(pcm, dtype=np.int16)
                        sd.play(samples, samplerate=client.tts_sample_rate)
                        sd.wait()

                    rec = {
                        "turn": turn_idx,
                        "question": question,
                        "answer_words": answer_words,
                        "stt_final_ms": round(stt_final_ms, 2),
                        "retrieval_ms": round(retrieval_ms, 2),
                        "llm_ms": None if ans.llm_ms is None else round(ans.llm_ms, 2),
                        "llm_error": ans.llm_error,
                        "tts_first_audio_ms": round(first_audio, 2),
                        "tts_total_ms": round(total_ms, 2),
                        "speech_end_to_first_audio_ms": round(speech_to_first, 2),
                        "cancel_to_silence_ms": None,
                        "source": source,
                    }
                    log_file.write(json.dumps(rec) + "\n")
                    log_file.flush()
                    turn_records.append(rec)
                except Exception as exc:
                    rec = {
                        "turn": turn_idx,
                        "question": str(wav_file),
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
                    log_file.write(json.dumps(rec) + "\n")
                    log_file.flush()
                    turn_records.append(rec)

        elif args.mic:
            turn_idx = 0
            print("Listening... speak, then pause. Say 'quit' or press Ctrl-C to stop.", flush=True)
            while True:
                turn_idx += 1
                question, t_speech_end = await client.listen_mic(
                    on_partial=lambda t: print(f"\r  hearing: {t[:70]:<70}", end="", flush=True)
                )
                print(f"\ryou:  {question}{' ' * 40}", flush=True)
                if question.lower().strip(" .!?") in {"quit", "exit", "goodbye", "stop"}:
                    break
                ans = await chain.ainvoke(question)
                _warn_llm(ans)
                print(f"bot:  {ans.text}", flush=True)
                out_stream = None
                on_chunk = None
                if args.play:
                    import sounddevice as sd

                    out_stream = sd.RawOutputStream(
                        samplerate=client.tts_sample_rate, channels=1, dtype="int16"
                    )
                    out_stream.start()
                    on_chunk = out_stream.write
                try:
                    pcm, first_audio, total_ms, speech_to_first = await client.speak_text(
                        ans.text, voice=args.voice, t_speech_end=t_speech_end, on_chunk=on_chunk
                    )
                finally:
                    if out_stream is not None:
                        out_stream.stop()
                        out_stream.close()
                if out_dir is not None:
                    save_wav(out_dir / f"turn{turn_idx:02d}.wav", pcm, client.tts_sample_rate)
                rec = {
                    "turn": turn_idx,
                    "question": question,
                    "answer_words": len(ans.text.split()),
                    "stt_final_ms": None,
                    "retrieval_ms": round(ans.retrieval_ms, 2),
                    "llm_ms": None if ans.llm_ms is None else round(ans.llm_ms, 2),
                    "llm_error": ans.llm_error,
                    "tts_first_audio_ms": round(first_audio, 2),
                    "tts_total_ms": round(total_ms, 2),
                    "speech_end_to_first_audio_ms": round(speech_to_first, 2),
                    "cancel_to_silence_ms": None,
                    "source": ans.source,
                }
                log_file.write(json.dumps(rec) + "\n")
                log_file.flush()
                turn_records.append(rec)

    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ctrl-C mid-TTS: send cancel, close sockets, flush jsonl; no traceback (Edge 50)
        await client.cancel_current()
    finally:
        log_file.close()
        await client.close()

    # Print summary statistics
    valid = [r for r in turn_records if r.get("error") is None and r.get("answer_words", 0) > 0]
    if valid:
        stt_vals = [r["stt_final_ms"] for r in valid if r.get("stt_final_ms") is not None]
        ret_vals = [r["retrieval_ms"] for r in valid if r.get("retrieval_ms") is not None]
        tts_first_vals = [
            r["tts_first_audio_ms"] for r in valid if r.get("tts_first_audio_ms") is not None
        ]
        tts_tot_vals = [r["tts_total_ms"] for r in valid if r.get("tts_total_ms") is not None]
        sp_first_vals = [
            r["speech_end_to_first_audio_ms"]
            for r in valid
            if r.get("speech_end_to_first_audio_ms") is not None
        ]

        print("\n--- Doc-Chat Latency Summary (ms) ---")
        for name, vals in [
            ("stt_final_ms", stt_vals),
            ("retrieval_ms", ret_vals),
            ("llm_ms", [r["llm_ms"] for r in valid if r.get("llm_ms") is not None]),
            ("tts_first_audio_ms", tts_first_vals),
            ("tts_total_ms", tts_tot_vals),
            ("speech_end_to_first_audio_ms", sp_first_vals),
        ]:
            p50, p95 = _p50_p95(vals)
            print(f"{name:<30} p50: {p50:>7.1f} ms    p95: {p95:>7.1f} ms")

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

    args = parser.parse_args(argv)
    if args.no_docs and args.llm != "qwen":
        print("--no-docs needs --llm qwen", file=sys.stderr)
        return 2
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
