"""CLI loop and timing for voice doc-chat (ticket 24)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import platform
import re
import subprocess
import sys
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import IO, Any

from doc_chat.bargein import (
    BargeInGuard,
    Ducker,
    EchoModel,
    PreRoll,
    SileroOnset,
    is_all_echo,
    looks_like_echo,
    strip_echo_prefix,
)
from doc_chat.chain import (
    SentenceStream,
    build_answer_chain,
    build_chat_chain,
    build_sentence_streamer,
)
from doc_chat.corpus import load_and_split_corpus, tokenize
from doc_chat.dialogue import ChatHistory, Clarification, clarification_for, parse_yes_no
from doc_chat.fillers import Fillers, TurnFillers, strip_fillers
from doc_chat.speech import (
    STITCH_GAP_S,
    DocChatSpeechClient,
    HeardTurn,
    MicHub,
    NoSpeech,
    SpokenTurn,
    with_lead_in,
)
from doc_chat.turnend import ADD_PROMPTS, TextTurnModel, declines_to_add

_QUIT_WORDS = {"quit", "exit", "goodbye"}
# said to make it stop talking, not to ask anything: it goes quiet and listens, nothing else
_STOP_WORDS = {
    "stop", "wait", "wait wait", "hold on", "hang on", "pause", "okay stop", "ok stop",
    "stop it", "stop stop", "shush", "enough", "never mind", "nevermind",
}  # fmt: skip
_STOP_CORE = {"stop", "wait", "hold", "hang", "pause", "shush", "enough", "nevermind", "mind"}
_STOP_FILLER = {"on", "it", "okay", "ok", "please", "now", "just", "never"}


_STOP_PREFIX = re.compile(
    r"^\W*(?:(?:wait|stop|hold on|hang on|okay|ok|please|sorry|actually|hey|um|uh|so)\b\W*)+",
    re.IGNORECASE,
)
_QUESTION_OPENERS = frozenset(
    ["tell", "what", "who", "when", "where", "why", "how", "explain", "describe", "give"]
)


def _strip_stop_prefix(text: str) -> str:
    """ "Wait, stop. Tell me about Suttree instead." -> "Tell me about Suttree instead.".

    A pivot: the user talked over the assistant with an order to stop and a new question. The
    order has done its work (the reply was cut off when the voice was heard); keeping it in the
    question would search the documents and prompt the model with "wait stop" as well. Only a
    prefix that contains a real stop word is removed, and only if a question remains. "Pause" is
    not one here: "Stop. Pause the music." is an order to pause the music."""
    m = _STOP_PREFIX.match(text)
    if not m:
        return text
    prefix = m.group(0)
    has_stop = re.search(r"\b(wait|stop|hold on|hang on)\b", prefix, re.IGNORECASE)
    punctuated = re.search(r"\b(wait|stop|on)\b\s*[,.!?;:-]", prefix, re.IGNORECASE)
    if not has_stop or not (punctuated or len(re.findall(r"[a-z']+", prefix.lower())) >= 2):
        return text  # "Wait times at the library?": that "wait" is part of the question
    rest = text[m.end() :].strip()
    if len(rest.split()) < 2:
        return text
    return rest[0].upper() + rest[1:]


def _only_stop_words(said: list[str]) -> bool:
    """Exact stop phrases, or up to four words that are all stop words or fillers with at least
    one real stop word among them."""
    if " ".join(said) in _STOP_WORDS:
        return True
    return (
        1 <= len(said) <= 4
        and all(w in _STOP_CORE or w in _STOP_FILLER for w in said)
        and any(w in _STOP_CORE for w in said)
    )


def _is_stop_phrase(said: list[str], after_interrupt: bool = False) -> bool:
    """An utterance that only tells the assistant to be quiet ("wait, stop", "hold on please").

    Three cases, so a question that merely contains "stop" is not one:
    * only stop words (`_only_stop_words`);
    * two words ending in stop/wait/pause ("quick stop": the first word was misheard);
    * a short utterance that *ends* in a stop phrase ("a young pilot wait stop": the assistant's own
      words came back in the same turn, then the user said stop). Only the strict check applies to
      the tail, so "why did the road stop" is a question;
    * right after the user talked over the assistant: up to eight words ending in stop/wait/pause
      ("the provided text is not stop": its own words came back and "wait" was lost)."""
    if _only_stop_words(said):
        return True
    if len(said) == 2 and said[-1] in ("stop", "wait", "pause"):
        return True
    if (
        after_interrupt
        and 2 <= len(said) <= 8
        and said[-1] in ("stop", "wait", "pause")
        and not _QUESTION_OPENERS.intersection(said)  # "tell me about the bus stop" is a question
    ):
        return True
    return 3 <= len(said) <= 8 and _only_stop_words(said[-2:])


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
    if rec.get("stt_final_ms") and rec.get("stt_server_ms") is not None:
        parts.append(
            f"stt {rec['stt_final_ms']:.0f} ms (pause {rec.get('pause_ms') or 0:.0f} + "
            f"server {rec['stt_server_ms']:.0f})"
        )
    for key, label in (
        ("stt_final_ms", "stt"),
        ("retrieval_ms", "retrieval"),
        ("llm_first_token_ms", "llm first token"),
        ("llm_ms", "llm"),
        ("tts_first_audio_ms", "tts first audio"),
    ):
        value = rec.get(key)
        if value and not (key == "stt_final_ms" and rec.get("stt_server_ms") is not None):
            parts.append(f"{label} {value:.0f} ms")
    heard = rec.get("speech_end_to_first_audio_ms")
    if heard is not None:
        parts.append(f"answer starts {heard:.0f} ms after you stopped")
    return "  [" + " | ".join(parts) + "]"


def _git(*cmd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-c", f"safe.directory={Path.cwd()}", *cmd],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def write_session_info(directory: Path, args: argparse.Namespace, llm: Any | None) -> None:
    """session.json: what this run was (code, settings, models), for comparing runs later."""
    info = {
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": args.note,
        "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_branch": _git("branch", "--show-current"),
        "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "llm": args.llm,
        "llm_model": getattr(llm, "model_name", None) or args.llm_model,
        "settings": {
            k: v
            for k, v in vars(args).items()
            if k not in {"note", "text", "wav"}  # nothing in the arguments is secret
        },
        "server": args.server,
        "server_note": "start the speech server with the flags you want recorded in --note",
    }
    (directory / "session.json").write_text(json.dumps(info, indent=1, default=str) + "\n")


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


@dataclass(frozen=True)
class Interruption:
    """The user started speaking while the assistant was busy.

    ``kind`` is "resumed" (the answer had not started: the user was cut off and went on) or
    "barge_in" (the assistant was speaking). ``blocks`` is what was heard around the start of it.
    """

    kind: str
    blocks: list[bytes]
    cancel_ms: float
    after_final_ms: float


RESUME_GAP_S = 0.2  # silence put between a cut-off turn and what the user said next
IDLE_RATIO = 3.0  # how far above the room's noise counts as speech while the assistant is quiet


@dataclass
class Session:
    args: argparse.Namespace
    client: DocChatSpeechClient
    log_file: IO[str]
    out_dir: Path | None
    chain: Any | None = None
    streamer: Callable[[str], SentenceStream] | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    history: ChatHistory = field(default_factory=lambda: ChatHistory(0))
    pending: Clarification | None = None
    turn_model: TextTurnModel | None = None  # set with --ask-to-add
    add_prompts: list[tuple[str, bytes]] = field(default_factory=list)  # (text, audio), recorded
    add_note: dict[str, Any] | None = None  # how the last turn was completed, for the log
    record_dir: Path | None = None  # --record: session.json, turns.jsonl, audio/, bot/
    _player: Any = None  # the Player of the answer being spoken, to cut it short
    _stream: SentenceStream | None = None  # the model call behind it
    _answer_started: float | None = None  # perf_counter of the first answer audio
    _replies: int = 0  # replies started this session (for --cue-every)
    _pivot_from: str | None = None  # what was said, when "wait, stop" was cut off the front
    _guard_trace: IO[str] | None = None  # --trace-guard: a JSON line per microphone block
    _echo_model: EchoModel | None = None  # speaker-to-microphone coupling, learned over the session
    _cue_s: float | None = None  # seconds into this reply a cue beep sounds, if it has one
    _spoken: list[str] = field(default_factory=list)  # sentences begun so far
    _prompt_count: int = 0
    fillers: Fillers | None = None  # --fillers: pre-recorded lines while a slow answer waits
    _turn_fillers: TurnFillers | None = None
    _filler_push: Callable[[bytes], None] | None = None  # into the answer's player
    _filler_started: float | None = None  # perf_counter of the first filler audio
    _filler_lines: list[str] = field(default_factory=list)
    _loop: asyncio.AbstractEventLoop | None = None

    def log(self, rec: dict[str, Any]) -> None:
        self.log_file.write(json.dumps(rec) + "\n")
        self.log_file.flush()
        self.records.append(rec)

    async def say(self, text: str) -> None:
        """Speak one short line of our own (a clarifying question), outside the LLM path."""
        from doc_chat.playback import make_player

        print(f"bot:  {text}", flush=True)
        player = (
            make_player(
                self.client.tts_sample_rate,
                getattr(self.args, "play_remote", None),
                self.client.canceller,
            )
            if self.args.play
            else None
        )
        self._player = player
        self._spoken.append(text)
        try:
            await self.client.speak_text(text, voice=self.args.voice, on_chunk=self._chunk(player))
        finally:
            if player is not None:
                await asyncio.to_thread(player.close)

    def _chunk(self, player: Any) -> Callable[[bytes], None]:
        """What to do with each piece of synthesised audio: note the first, then play it.

        With ``--cue-beep S`` every ``--cue-every``-th reply gets a beep mixed in S seconds from
        its start (see `mix_beep`), and `_cue_s` says so, for measuring barge-in."""
        from doc_chat.playback import mix_beep

        beep_s = float(getattr(self.args, "cue_beep", 0) or 0)
        every = int(getattr(self.args, "cue_every", 2) or 0)
        self._replies += 1
        cue = beep_s > 0 and every > 0 and self._replies % every == 0
        self._cue_s = beep_s if cue else None
        rate = self.client.tts_sample_rate
        pushed = [0]  # samples of this reply handed on so far

        def push(pcm: bytes) -> None:
            if self._answer_started is None:
                self._answer_started = time.perf_counter()
            if cue:
                pcm = mix_beep(pcm, pushed[0], rate, beep_s)
            pushed[0] += len(pcm) // 2
            if player is not None:
                player.push(pcm)

        return push

    async def play_pcm(self, text: str, pcm: bytes) -> None:
        """Play a line that was synthesised in advance (no waiting for the speech server)."""
        from doc_chat.playback import make_player

        print(f"bot:  {text}", flush=True)
        if not self.args.play:
            return
        player = make_player(
            self.client.tts_sample_rate,
            getattr(self.args, "play_remote", None),
            self.client.canceller,
        )
        try:
            player.push(pcm)
        finally:
            await asyncio.to_thread(player.close)

    def _play_filler(self, kind: str) -> None:
        """Queue one filler line on the answer's player (before any answer audio)."""
        picked = self.fillers.pick(kind) if self.fillers is not None else None
        if picked is None or self._filler_push is None or self._answer_started is not None:
            return
        text, pcm = picked
        if self._filler_started is None:
            self._filler_started = time.perf_counter()
        self._filler_lines.append(text)
        print(f"bot:  ({text})", flush=True)
        self._filler_push(pcm)

    async def _filler_timer(self, t_final: float) -> str:
        """Play this turn's timed fillers; returns "giveup" when no answer came in time."""
        turn = self._turn_fillers
        while turn is not None and (at := turn.next_at()) is not None:
            await asyncio.sleep(max(0.0, t_final + at - time.perf_counter()))
            if self._answer_started is not None:
                break
            kind = turn.due(time.perf_counter() - t_final)
            if kind == "giveup":
                return kind
            if kind is not None:
                self._play_filler(kind)
        await asyncio.Future()  # nothing more to play; cancelled when the turn ends
        return ""

    def on_agent_event(self, event: dict[str, Any]) -> None:
        """A message from the agent server (any thread): a tool call may play its filler."""
        if self._loop is None:
            return
        if event.get("event") == "tool":
            self._loop.call_soon_threadsafe(
                self._tool_filler, str(event.get("name", "")), bool(event.get("said"))
            )
        elif event.get("event") == "text" and self._turn_fillers is not None:
            self._loop.call_soon_threadsafe(self._turn_fillers.text_arrived)

    def _tool_filler(self, name: str, said: bool = False) -> None:
        turn = self._turn_fillers
        if turn is not None and self._answer_started is None:
            kind = turn.tool(name, said)
            if kind is not None:
                self._play_filler(kind)

    async def _give_up(self, idx: int, heard: HeardTurn, work: asyncio.Task[None]) -> None:
        """No answer in time: drop the turn and apologise (the user asks again)."""
        if self._stream is not None:
            self._stream.cancel()
        if self._player is not None:
            self._player.abort()
        await self.client.send_tts_cancel()
        work.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(work, timeout=2.0)
        print("  [gave up: no answer in time]", flush=True)
        picked = self.fillers.pick("giveup") if self.fillers is not None else None
        if picked is not None:
            await self.play_pcm(*picked)
        self.log(
            {
                "turn": idx,
                "question": heard.text,
                "answer_words": 0,
                "gave_up": True,
                "fillers": list(self._filler_lines) or None,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )

    async def complete_turn(self, heard: HeardTurn) -> HeardTurn:
        """If the words end like an unfinished sentence, ask whether there is more and join it.

        The speaker is asked ("Got it. Anything to add?"); what they say next is joined to the
        first part as audio and decoded once. A "no", or silence for --add-wait seconds, keeps
        the turn as it was. Turns the speech server wants to clarify (a doubtful name) are left to
        that path.
        """
        self.add_note = None
        model = self.turn_model
        if model is None or not heard.text.strip():
            return heard
        if clarification_for(heard.text, heard.repair) is not None:
            return heard
        p = model.prob(heard.text)
        note: dict[str, Any] = {"unfinished_score": round(p, 3), "asked_to_add": False}
        asking = getattr(self.args, "ask_to_add", True) and bool(self.add_prompts)
        if not asking or p >= self.args.add_threshold:
            self.add_note = note  # scored and logged only: --log-turn-score
            return heard
        text, pcm = self.add_prompts[self._prompt_count % len(self.add_prompts)]
        self._prompt_count += 1
        print(f"you:  {heard.text}   (sounds unfinished: {p:.2f})", flush=True)
        await self.play_pcm(text, pcm)
        note["asked_to_add"] = True
        try:
            more = await self.client.listen_turn(
                slot=self.args.slot,
                risk=self.args.risk,
                vad_hold=self.args.vad_hold,
                first_speech_timeout=self.args.add_wait,
                on_partial=lambda t: print(f"\r  hearing: {t[:70]:<70}", end="", flush=True),
            )
        except NoSpeech:
            print("  (nothing added)", flush=True)
            self.add_note = {**note, "added": None}
            return heard
        print("\r" + " " * 90 + "\r", end="")
        if declines_to_add(more.text):
            print(f"you:  {more.text}   (nothing to add)", flush=True)
            self.add_note = {**note, "added": None}
            return heard
        gap = b"\x00\x00" * int(16000 * STITCH_GAP_S)
        joined = await self.client.hear_pcm(
            heard.pcm + gap + more.pcm,
            slot=self.args.slot,
            risk=self.args.risk,
            t_speech_end=more.t_speech_end,
        )
        if not joined.text.strip():
            self.add_note = {**note, "added": None}
            return heard
        print(f"      (joined {heard.text!r} + {more.text!r})", flush=True)
        self.add_note = {**note, "added": more.text}
        return joined

    async def handle(self, idx: int, heard: HeardTurn) -> None:
        """What to do with a heard turn: answer it, or ask something first."""
        text = heard.text.strip()
        if self.fillers is not None and (clean := strip_fillers(text, self.fillers.lines())):
            if clean != text:
                print(f"      (a filler line dropped: {clean!r})", flush=True)
            text = clean
        if self.pending is not None:
            pending, self.pending = self.pending, None
            reply = parse_yes_no(text) if pending.kind == "confirm" else None
            if reply == "yes" and pending.if_yes:
                print(f"you:  {text}   -> {pending.if_yes}", flush=True)
                await self.turn(idx, pending.if_yes, heard.t_speech_end, heard.stt_ms, heard)
                return
            if reply == "no":
                await self.say("Okay. Please say it again.")
                self.log(self._clarification_record(idx, text, "declined", heard))
                return
            # anything else is a fresh question: they rephrased instead of answering
        else:
            clarification = clarification_for(text, heard.repair)
            if clarification is not None:
                print(f"you:  {text}", flush=True)
                self.pending = clarification if clarification.kind == "confirm" else None
                await self.say(clarification.question)
                self.log(self._clarification_record(idx, text, clarification.question, heard))
                return
        await self.turn(idx, text, heard.t_speech_end, heard.stt_ms, heard)

    @staticmethod
    def _clarification_record(
        idx: int, text: str, asked: str, heard: HeardTurn | None
    ) -> dict[str, Any]:
        return {
            "turn": idx,
            "question": text,
            "answer_words": 0,
            "clarification": asked,
            "repair_rung": (heard.repair or {}).get("rung") if heard else None,
            "stt_final_ms": round(heard.stt_ms, 2) if heard else None,
        }

    async def turn(
        self,
        idx: int,
        question: str,
        t_speech_end: float,
        stt_final_ms: float | None,
        heard: HeardTurn | None = None,
    ) -> None:
        """Answer one question and speak it; records the timing of every stage."""
        from doc_chat.playback import make_player

        print(f"you:  {question}", flush=True)
        if heard is not None and heard.raw_text and heard.raw_text != heard.text:
            print(f"      (recognised as: {heard.raw_text})", flush=True)
        player = (
            make_player(
                self.client.tts_sample_rate,
                getattr(self.args, "play_remote", None),
                self.client.canceller,
            )
            if self.args.play
            else None
        )
        self._player = player
        self._filler_push = player.push if player is not None else None
        on_chunk = self._chunk(player)

        def on_sentence(sentence: str) -> None:
            self._spoken.append(sentence)
            print(f"bot:  {sentence}", flush=True)

        try:
            stream: SentenceStream | None = None
            if self.streamer is not None:
                stream = self.streamer(question)
                self._stream = stream
                spoken = await self.client.speak_sentences(
                    stream,
                    voice=self.args.voice,
                    t_speech_end=t_speech_end,
                    on_chunk=on_chunk,
                    on_sentence=on_sentence,
                )
                _warn(stream.error)
                source, retrieval_ms = stream.source, stream.retrieval_ms
                llm_ms, first_token, llm_error = stream.llm_ms, stream.first_token_ms, stream.error
            else:
                assert self.chain is not None
                ans = await self.chain.ainvoke(question)
                _warn(ans.llm_error)
                on_sentence(ans.text)
                pcm, first_ms, total_ms, to_first = await self.client.speak_text(
                    ans.text, voice=self.args.voice, t_speech_end=t_speech_end, on_chunk=on_chunk
                )
                spoken = SpokenTurn(pcm, first_ms, total_ms, to_first, [ans.text])
                source, retrieval_ms = ans.source, ans.retrieval_ms
                llm_ms, first_token, llm_error = ans.llm_ms, None, ans.llm_error
        finally:
            if player is not None:
                await asyncio.to_thread(player.close)

        underruns = getattr(player, "underruns", None)
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
            "audio_underruns": underruns,
            "source": source,
            "cue_s": self._cue_s,
            "fillers": list(self._filler_lines) or None,
            "filler_after_final_ms": (
                None
                if self._filler_started is None or heard is None
                else round((self._filler_started - heard.t_final) * 1000.0, 1)
            ),
        }
        if heard is not None:
            rec["pause_ms"] = (
                None
                if heard.finalize_sent is None
                else rounded((heard.finalize_sent - heard.t_speech_end) * 1000.0)
            )
            rec["stt_server_ms"] = rounded(heard.server_ms)
            rec["repair_rung"] = (heard.repair or {}).get("rung")
            rec["heard"] = heard.text
            rec["pivot_from"] = self._pivot_from
            rec["raw_text"] = heard.raw_text
            rec["partial"] = heard.partial or None
            rec["audio_s"] = round(len(heard.pcm) / 2 / 16000, 2) if heard.pcm else None
            rec["repair"] = heard.repair
        rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        rec["llm"] = getattr(self.args, "llm", None)
        floor = getattr(self.client, "noise_floor", None)
        rec["noise_floor"] = None if floor is None else round(floor, 5)
        if self.add_note is not None:
            rec.update(self.add_note)
            self.add_note = None
        self.log(rec)
        self.history.add(question, " ".join(spoken.sentences))
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
                heard = await self.client.hear_wav(
                    wav_file, slot=self.args.slot, risk=self.args.risk
                )
                if not heard.text.strip():
                    rec = _failed_record(idx, "", RuntimeError(""))
                    rec.pop("error")
                    rec["stt_final_ms"] = round(heard.stt_ms, 2)
                    self.log(rec)
                    continue
                await self.handle(idx, heard)
            except Exception as exc:
                self.log(_failed_record(idx, str(wav_file), exc))

    async def run_mic(self) -> None:
        hold = self.args.vad_hold
        how = f"a pause of {hold:g} s ends your turn" if hold > 0 else "pause to end your turn"
        interrupts = " You can talk over it." if self.client.hub is not None else ""
        print(
            f"Listening... speak ({how}).{interrupts} Say 'quit' or press Ctrl-C to stop.",
            flush=True,
        )
        idx = 0
        heard: HeardTurn | None = None
        after_interrupt = False  # the turn being handled was said right after talking over a reply
        while True:
            idx += 1
            if heard is None:
                heard = await self.hear()
            said = re.sub(r"[^a-z' ]", " ", heard.text.lower()).split()
            was_interrupt, after_interrupt = after_interrupt, False
            if " ".join(said) in _QUIT_WORDS:
                break
            if _is_stop_phrase(said, was_interrupt):
                print(f"you:  {heard.text}   (it stopped; listening)", flush=True)
                self.log(
                    {
                        "turn": idx,
                        "question": heard.text,
                        "answer_words": 0,
                        "stop_word": True,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
                heard = None
                idx -= 1
                continue
            self._pivot_from = None
            stripped = _strip_stop_prefix(heard.text)
            if stripped != heard.text:  # "wait, stop, tell me about X instead": stop and ask
                print(f"      (pivot: {stripped!r})", flush=True)
                self._pivot_from = heard.text
                heard = dc_replace(heard, text=stripped)
            interruption: Interruption | None = None
            try:
                if self.pending is None:
                    heard = await self.complete_turn(heard)
                if self.record_dir is not None and heard.pcm:
                    save_wav(self.record_dir / "audio" / f"turn{idx:02d}.wav", heard.pcm, 16000)
                interruption = await self.respond(idx, heard)
            except Exception as exc:
                print(f"  [turn failed - {type(exc).__name__}]", file=sys.stderr, flush=True)
                self.log(_failed_record(idx, heard.text, exc))
            if interruption is None:
                heard = None
                continue
            spoken = " ".join(self._filler_lines + self._spoken)
            heard = await self.follow_up(heard, interruption)
            after_interrupt = interruption.kind == "barge_in"
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
            ):
                print(f"  [ignored: that was my own voice ({heard.text!r})]", flush=True)
                self.log(
                    {
                        "turn": idx,
                        "question": heard.text,
                        "answer_words": 0,
                        "ignored_echo": True,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
                heard = None
                idx -= 1
                continue
            if strip and (cleaned := strip_echo_prefix(heard.text, spoken)) != heard.text:
                print(f"      (my own words dropped: {cleaned!r})", flush=True)
                heard = dc_replace(heard, text=cleaned)
            if interruption.kind == "resumed":
                idx -= 1  # the cut-off turn and what followed are one turn

    async def hear(
        self, lead_in: list[bytes] | None = None, first_speech_timeout: float | None = None
    ) -> HeardTurn:
        """Listen for the next turn (optionally starting with audio that was already heard)."""
        audio = None
        if lead_in is not None and self.client.hub is not None:
            audio = with_lead_in(lead_in, self.client.hub.blocks())
        heard = await self.client.listen_turn(
            slot=self.args.slot,
            risk=self.args.risk,
            vad_hold=self.args.vad_hold,
            on_partial=lambda t: print(f"\r  hearing: {t[:70]:<70}", end="", flush=True),
            audio=audio,
            first_speech_timeout=first_speech_timeout,
        )
        print("\r" + " " * 90 + "\r", end="")
        return heard

    async def respond(self, idx: int, heard: HeardTurn) -> Interruption | None:
        """Answer the turn while listening; if the user starts talking, stop and say how."""
        hub = self.client.hub
        if hub is None or not getattr(self.args, "barge_in", True):
            await self.handle(idx, heard)
            return None
        self._answer_started = None
        self._spoken = []
        self._stream = None
        self._player = None
        self._filler_started = None
        self._filler_lines = []
        self._filler_push = None
        self._loop = asyncio.get_running_loop()
        self._turn_fillers = TurnFillers(self.fillers) if self.fillers is not None else None
        t_final = heard.t_final
        work = asyncio.create_task(self.handle(idx, heard))
        watch = asyncio.create_task(self._watch_for_voice(heard.tail))
        timer = asyncio.create_task(self._filler_timer(t_final))
        try:
            await asyncio.wait({work, watch, timer}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            work.cancel()
            watch.cancel()
            timer.cancel()
            raise
        if timer.done() and not work.done() and not watch.done():  # no answer in time
            watch.cancel()
            await self._give_up(idx, heard, work)
            return None
        timer.cancel()
        if not watch.done():  # the answer finished without interruption
            watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watch
            work.result()
            return None
        blocks = watch.result()
        t0 = time.perf_counter()
        started = self._answer_started
        if self._stream is not None:
            self._stream.cancel()
        if self._player is not None:
            self._player.abort()
        await self.client.send_tts_cancel()
        work.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(work, timeout=2.0)  # a stuck device must not freeze us
        cancel_ms = (time.perf_counter() - t0) * 1000.0
        kind = "barge_in" if started is not None else "resumed"
        spoken = " ".join(self._spoken)
        print(
            f"  [interrupted ({kind}); stopped {cancel_ms:.0f} ms after it heard you]", flush=True
        )
        if kind == "barge_in":
            self.history.add(heard.text, spoken or "(interrupted)")
        self.log(
            {
                "turn": idx,
                "question": heard.text,
                "answer_words": len(spoken.split()) if kind == "barge_in" else 0,
                "interrupted": kind,
                "cancel_to_silence_ms": round(cancel_ms, 1),
                "interrupt_after_final_ms": round((t0 - t_final) * 1000.0, 1),
                "interrupt_after_answer_start_ms": (
                    None if started is None else round((t0 - started) * 1000.0, 1)
                ),
                "cue_s": self._cue_s,
                "spoken": spoken or None,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        return Interruption(kind, blocks, cancel_ms, (t0 - t_final) * 1000.0)

    async def _watch_for_voice(self, tail: bytes = b"") -> list[bytes]:
        """Return the recent blocks once the user's voice is heard over the assistant.

        Quiet at first (it is still thinking: any speech counts), then stricter once the answer is
        playing, because the speakers leak into the microphone.
        """
        hub = self.client.hub
        assert hub is not None
        noise = self.client.noise_floor or 0.015
        ratio = float(getattr(self.args, "barge_ratio", 4.0))
        min_s = float(getattr(self.args, "barge_ms", 300)) / 1000.0
        roll = PreRoll(seconds=float(getattr(self.args, "preroll_s", 0.8)))
        step = 3200  # 100 ms of 16-bit samples
        blocks = [tail[i : i + step] for i in range(0, len(tail), step)]

        if (
            self._echo_model is None
        ):  # one for the session: what it learns carries to the next reply
            self._echo_model = EchoModel(
                prior=float(getattr(self.args, "barge_echo_prior", 0.5)),
                margin=float(getattr(self.args, "barge_echo_margin", 1.8)),
                window=int(getattr(self.args, "barge_echo_window", 15)),
                quantile=float(getattr(self.args, "barge_echo_quantile", 0.5)),
            )
        echo = self._echo_model
        silero = (
            SileroOnset(
                str(getattr(self.args, "vad_model", "assets/silero_vad.onnx")),
                confidence=float(getattr(self.args, "vad_confidence", 0.7)),
                min_s=min_s,
                kofn=_kofn(getattr(self.args, "silero_kofn", None)),
            )
            if getattr(self.args, "vad", "energy") in ("silero", "both")
            else None
        )
        vad_mode = getattr(self.args, "vad", "energy")

        guard = BargeInGuard(
            noise,
            ratio=ratio,
            idle_ratio=IDLE_RATIO,
            min_s=min_s,
            mode=vad_mode,
            silero=silero,
            echo=echo,
            level_kofn=_kofn(getattr(self.args, "level_kofn", None)),
            warmup_blocks=round(float(getattr(self.args, "barge_warmup_s", 0.0)) * 10),
            ducked_k=getattr(self.args, "level_kofn_ducked", None),
        )

        trace = self._guard_trace
        duck_db = float(getattr(self.args, "duck_db", 0.0))
        duck_k = int(getattr(self.args, "duck_k", 2))
        ducker = Ducker(duck_db) if duck_db < 0.0 else None

        def feed(block: bytes) -> bool:
            roll.push(block)
            level_of = getattr(self._player, "level_recent", None)
            played = float(level_of()) if level_of is not None else 0.0
            # a filler is the assistant speaking too: its echo must not count as the user
            answering = self._answer_started is not None or self._filler_started is not None
            guard.ducked = ducker is not None and ducker.ducked
            fired = guard.feed(block, played, answering=answering)
            set_gain = getattr(self._player, "set_gain", None)
            if ducker is not None and silero is not None and answering and set_gain is not None:
                voiced = guard.last_level > guard.last_bar and silero.recent_speech >= duck_k
                new_gain = ducker.update(voiced, fired, time.perf_counter())
                if new_gain is not None:
                    set_gain(new_gain)
            if trace is not None:  # one line per 100 ms block: what the guard saw and decided
                trace.write(
                    json.dumps(
                        {
                            "t": round(time.perf_counter(), 3),
                            "reply": self._replies,
                            "answering": answering,
                            "level": round(guard.last_level, 5),
                            "bar": round(guard.last_bar, 5),
                            "played": round(played, 5),
                            "coupling": round(echo.coupling, 3),
                            "noise": round(noise, 5),
                            "conf": round(silero.last_confidence, 3) if silero else None,
                            "model": guard.last_model,
                            "level_hit": guard.last_level_hit,
                            "fired": fired,
                            "ducked": ducker is not None and ducker.ducked,
                            "cue_s": self._cue_s,
                        }
                    )
                    + "\n"
                )
                trace.flush()
            return fired

        for block in blocks:  # speech that went on while the server was still answering
            if feed(block):
                return roll.blocks()
        while True:
            if feed(await hub.get()):
                return roll.blocks()

    async def follow_up(self, previous: HeardTurn, it: Interruption) -> HeardTurn:
        """Hear what the user says after an interruption (starting with the audio just heard)."""
        new = await self.hear(lead_in=it.blocks)
        if it.kind != "resumed" or not previous.pcm:
            return new
        gap = b"\x00\x00" * int(16000 * RESUME_GAP_S)
        joined = await self.client.hear_pcm(
            previous.pcm + gap + new.pcm,
            slot=self.args.slot,
            risk=self.args.risk,
            t_speech_end=new.t_speech_end,
        )
        if not joined.text.strip():
            return new
        print(f"      (joined {previous.text!r} + {new.text!r})", flush=True)
        return joined


def _kofn(spec: str | None) -> tuple[int, int] | None:
    """'7/10' -> (7, 10); None stays None."""
    if not spec:
        return None
    k, _, n = spec.partition("/")
    if not (k.isdigit() and n.isdigit()) or not 0 < int(k) <= int(n):
        raise SystemExit(f"expected K/N with 0 < K <= N, got {spec!r}")
    return int(k), int(n)


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
    agent_llm = None  # the agent client, for --fillers' tool events
    if args.llm == "qwen":
        from doc_chat.qwen import build_qwen_chat_model

        llm = build_qwen_chat_model(
            model=args.llm_model,
            timeout=args.llm_timeout,
            max_tokens=args.llm_max_tokens,
            temperature=args.llm_temperature,
        )

    elif args.llm == "echo":
        from doc_chat.echo import EchoChatModel

        args.max_words = max(args.max_words, 120)  # the reply is long on purpose

        echo_args: dict[str, Any] = {
            "first_token_ms": args.echo_delay_ms,
            "random_sentences": args.echo_random,
        }
        if args.echo_text:
            echo_args["passage"] = Path(args.echo_text).read_text(encoding="utf-8")
        llm = EchoChatModel(**echo_args)
    elif args.llm == "agent":
        from doc_chat.agent_client import AgentChatModel

        llm = AgentChatModel(url=args.agent_url, timeout=args.llm_timeout)
        agent_llm = llm
    elif args.llm == "gemini":
        from doc_chat.gemini import build_gemini_chat_model

        llm = build_gemini_chat_model(
            model=args.llm_model,
            timeout=args.llm_timeout,
            max_tokens=args.llm_max_tokens,
            temperature=args.llm_temperature,
        )

    retriever = None
    if not args.no_docs:
        chunks = load_and_split_corpus(
            args.docs,
            chunk_size=args.chunk_size,
            chunk_overlap=max(args.chunk_size // 10, 0),
        )
        from langchain_community.retrievers import BM25Retriever

        retriever = BM25Retriever.from_documents(chunks, k=args.top_k, preprocess_func=tokenize)

    client = DocChatSpeechClient(server_url=args.server)
    await client.connect()
    hub: MicHub | None = None
    if args.mic and args.barge_in:
        canceller = None
        if args.aec:
            from doc_chat.aec import EchoCanceller

            canceller = EchoCanceller(args.aec_lead_ms)
        hub = MicHub(canceller=canceller)
        hub.start()  # one microphone stream for the whole session: heard while it thinks and talks
        client.hub = hub
        client.canceller = canceller

    record_dir = Path(args.record) if args.record else None
    if record_dir is not None:  # one folder per run: everything needed to look back at it
        record_dir.mkdir(parents=True, exist_ok=True)
        (record_dir / "audio").mkdir(exist_ok=True)
        write_session_info(record_dir, args, llm)
    log_path = (
        record_dir / "turns.jsonl"
        if record_dir is not None
        else Path(args.log)
        if args.log
        else Path("out/doc_chat/turns.jsonl")
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out) if args.out else (record_dir / "bot" if record_dir else None)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as log_file:
        session = Session(
            args=args,
            client=client,
            log_file=log_file,
            out_dir=out_dir,
            history=ChatHistory(args.history if llm is not None else 0),
            record_dir=record_dir,
        )
        if getattr(args, "trace_guard", False) and record_dir is not None:
            session._guard_trace = (record_dir / "guard.jsonl").open("a", encoding="utf-8")
        if args.ask_to_add or args.log_turn_score:
            try:
                session.turn_model = TextTurnModel(args.turn_model)
            except Exception as exc:
                print(
                    f"--ask-to-add / --log-turn-score need the turn model in {args.turn_model} "
                    f"(python scripts/fetch_turn_model.py): {type(exc).__name__}",
                    file=sys.stderr,
                )
                await client.close()
                return 2
            if args.ask_to_add:
                for line in ADD_PROMPTS:  # recorded once, so the prompt starts without waiting
                    pcm, *_ = await client.speak_text(line, voice=args.voice)
                    session.add_prompts.append((line, pcm))
        if getattr(args, "fillers", False):
            fillers = Fillers.load()

            async def record(line: str) -> bytes:
                pcm, *_ = await client.speak_text(line, voice=args.voice)
                return bytes(pcm)

            print(f"recording {len(fillers.lines())} filler lines...", flush=True)
            await fillers.synthesise(record)  # once, so a filler starts without waiting
            session.fillers = fillers
            if agent_llm is not None:
                agent_llm.on_event = session.on_agent_event
        past = session.history.pairs
        if args.stream:
            assert llm is not None
            session.streamer = build_sentence_streamer(
                retriever, llm, max_words=args.max_words, system_prompt=system_prompt, history=past
            )
        elif retriever is None:
            assert llm is not None
            session.chain = build_chat_chain(
                llm, max_words=args.max_words, system_prompt=system_prompt, history=past
            )
        else:
            session.chain = build_answer_chain(
                retriever,
                max_words=args.max_words,
                llm=llm,
                system_prompt=system_prompt,
                history=past,
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
            if hub is not None:
                hub.stop()
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
    parser.add_argument(
        "--play-remote",
        metavar="WS_URL",
        help="play on another machine's speaker (scripts/pi_speaker.py), e.g. ws://pi:8780; "
        "uses the LAN_SPEECH_TOKEN; implies --play",
    )
    parser.add_argument(
        "--aec",
        action="store_true",
        help="cancel the remote speaker's echo in the microphone, using what is sent to it as the "
        "reference (needs --play-remote and --mic; docs/AEC-REMOTE-SPEAKER.md)",
    )
    parser.add_argument(
        "--aec-lead-ms",
        type=int,
        default=0,
        help="with --aec: ms of reference fed ahead of the microphone (default: 0; any lead uses "
        "up the canceller's ~0.5 s delay range)",
    )
    parser.add_argument(
        "--duck-db",
        type=float,
        default=0.0,
        help="turn the remote speaker down by this many dB (negative, e.g. -15) at the first "
        "sign of your voice over the answer, and back up if you do not interrupt (default: off)",
    )
    parser.add_argument(
        "--duck-k",
        type=int,
        default=2,
        help="with --duck-db: speech windows among the last 10 that count as the first sign",
    )
    parser.add_argument("--out", help="Output directory for generated WAVs")
    parser.add_argument(
        "--log",
        default="out/doc_chat/turns.jsonl",
        help="Turn log output JSONL path (default: out/doc_chat/turns.jsonl)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="characters per retrieved passage (default: 500; ~1000 suits a large corpus)",
    )
    parser.add_argument(
        "--top-k", type=int, default=2, help="how many passages the model is given (default: 2)"
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
        choices=["none", "qwen", "gemini", "echo", "agent"],
        default="none",
        help="LLM backend: none, qwen, gemini, echo (no model: repeats what it heard, then "
        "reads a fixed passage, for testing speech and interruptions), or agent (the Pipecat "
        "agent server in pipeline/, with tools; use with --no-docs). Default: none",
    )
    parser.add_argument(
        "--agent-url",
        default="ws://127.0.0.1:8781",
        help="with --llm agent: the agent server (default: ws://127.0.0.1:8781)",
    )
    parser.add_argument(
        "--no-docs",
        action="store_true",
        help="Skip retrieval: send each question straight to the model (needs --llm)",
    )
    parser.add_argument(
        "--vad-hold",
        type=float,
        default=0.6,
        help="seconds of quiet after you speak before this client ends the turn (default: 0.6); "
        "0 leaves it to the speech server, which is slower",
    )
    parser.add_argument(
        "--slot",
        default="doc?",
        help="kind of name to repair, sent to the speech server (default: doc?, optional names "
        "from --entities; harmless when the server has no such list)",
    )
    parser.add_argument("--risk", choices=["low", "high"], help="how costly a misheard name is")
    parser.add_argument(
        "--history",
        type=int,
        default=4,
        help="earlier question/answer pairs sent to the model with each new question (default: 4)",
    )
    parser.add_argument(
        "--barge-in",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep listening while it thinks and speaks: if you start talking it stops, and "
        "either joins what you say to your cut-off sentence or takes it as the next question "
        "(default: on with --mic; use a headset, speakers can trigger it)",
    )
    parser.add_argument(
        "--barge-ms",
        type=int,
        default=300,
        help="how long you must be speaking before it stops talking (default: 300)",
    )
    parser.add_argument(
        "--barge-ratio",
        type=float,
        default=4.0,
        help="how far above the room noise you must be while it is talking (default: 4; raise it "
        "if its own voice stops it)",
    )
    parser.add_argument(
        "--vad",
        choices=["energy", "silero", "both"],
        default="energy",
        help="what decides that you started talking over it: loudness above the room noise "
        "(energy, with the echo allowance), the Silero VAD model main's design uses (silero), or "
        "both (Silero says it is speech AND it is clearly above the echo)",
    )
    parser.add_argument("--vad-model", default="assets/silero_vad.onnx")
    parser.add_argument(
        "--vad-confidence", type=float, default=0.7, help="Silero speech confidence (default: 0.7)"
    )
    parser.add_argument(
        "--trace-guard",
        action="store_true",
        help="with --record: write guard.jsonl, one line per 100 ms block with what the barge-in "
        "guard saw (levels, the echo bar, each stage) and decided",
    )
    parser.add_argument(
        "--cue-beep",
        type=float,
        default=0.0,
        metavar="S",
        help="measuring barge-in: mix a beep into every --cue-every-th reply S seconds in (it "
        "comes out of the same speaker); you talk over it, and the log says when it sounded",
    )
    parser.add_argument(
        "--cue-every", type=int, default=2, metavar="N", help="with --cue-beep (default: 2)"
    )
    parser.add_argument(
        "--silero-kofn",
        metavar="K/N",
        help="Silero onset when K of the last N 32 ms windows are speech, instead of --barge-ms of "
        "unbroken speech (measured on the Pi: 7/10 with --vad-confidence 0.5)",
    )
    parser.add_argument(
        "--level-kofn",
        metavar="K/N",
        help="with --vad both: loudness counts when K of the last N 100 ms blocks are above the "
        "echo allowance, instead of a net 0.3 s (measured laptop mic + Pi speaker: 3/8)",
    )
    parser.add_argument(
        "--level-kofn-ducked",
        type=int,
        default=None,
        metavar="K",
        help="with --level-kofn and --duck-db: while the speaker is ducked, K blocks above the "
        "echo allowance are enough (try 2: a bare 'stop' gives only one or two loud blocks)",
    )
    parser.add_argument(
        "--fillers",
        action="store_true",
        help="say a short pre-recorded line when an answer is slow: a tool's line when it is "
        "called, a stall at 1.2 s, a notice at 2.5 s, an apology at 8 s (config/fillers.json)",
    )
    parser.add_argument(
        "--echo-text-guard",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="ignore an 'interruption' whose words are the assistant's own (its voice came back "
        "through the microphone)",
    )
    parser.add_argument(
        "--strip-echo-prefix",
        action="store_true",
        help="after a barge-in, drop the assistant's own words heard before your 'stop' and treat "
        "a transcript made only of its words (misheard ones too) as its echo "
        "(.scratch/echo-next/PLAN.md, E3)",
    )
    parser.add_argument(
        "--barge-echo-margin",
        type=float,
        default=1.8,
        help="while it speaks, you must be this many times louder than its own voice coming back "
        "through the microphone (learned as it speaks); 0 turns the echo allowance off",
    )
    parser.add_argument(
        "--barge-echo-quantile",
        type=float,
        default=0.5,
        help="which part of the recent echo to stay above: 0.5 follows the typical echo, 0.95 its "
        "peaks (use 0.95 with --barge-echo-window 60 and a speaker in the room)",
    )
    parser.add_argument(
        "--barge-echo-window",
        type=int,
        default=15,
        help="how many recent 100 ms blocks of echo the allowance is learned from (default: 15)",
    )
    parser.add_argument(
        "--preroll-s",
        type=float,
        default=0.8,
        help="seconds of microphone audio before a barge-in that go to the recogniser with it "
        "(default: 0.8; 1.2 put about a second of the assistant's own voice in front of yours: "
        ".scratch/echo-next/PLAN.md, E4)",
    )
    parser.add_argument(
        "--barge-warmup-s",
        type=float,
        default=0.0,
        help="for this many seconds at the start of each reply, expect the echo of the prior "
        "coupling (--barge-echo-prior) even if less was learned: the canceller leaks while it "
        "settles (try 1.0 with --aec; .scratch/echo-next/PLAN.md, E2)",
    )
    parser.add_argument(
        "--barge-echo-prior",
        type=float,
        default=0.5,
        help="share of its voice assumed to come back before it is measured (default: 0.5)",
    )
    parser.add_argument(
        "--record",
        metavar="DIR",
        help="keep this run in DIR: session.json (code version, settings), turns.jsonl (one line "
        "per turn), audio/ (what you said, one WAV per turn) and bot/ (what it said)",
    )
    parser.add_argument(
        "--note", default="", help="free text stored in session.json (server flags, room, headset)"
    )
    parser.add_argument(
        "--log-turn-score",
        action="store_true",
        help="score every turn with the turn-end model and log it, without asking anything "
        "(use it to pick --add-threshold; --ask-to-add does this too)",
    )
    parser.add_argument(
        "--ask-to-add",
        action="store_true",
        help='when what you said ends like an unfinished sentence ("play something by"), ask '
        '"anything to add?" and join your answer to the first part (needs --mic and the turn '
        "model: python scripts/fetch_turn_model.py)",
    )
    parser.add_argument(
        "--add-threshold",
        type=float,
        default=0.3,
        help="ask when the turn model's P(finished) is below this (default: 0.3)",
    )
    parser.add_argument(
        "--add-wait",
        type=float,
        default=3.0,
        help='seconds to wait for an answer to "anything to add?" (default: 3)',
    )
    parser.add_argument(
        "--turn-model", default="models/turn-detector", help="folder of the turn model"
    )
    parser.add_argument(
        "--echo-text", help="with --llm echo: a text file to read instead of the built-in passage"
    )
    parser.add_argument(
        "--echo-random",
        type=int,
        default=0,
        metavar="N",
        help="with --llm echo: read N random sentences (fixed by the question), not a passage",
    )
    parser.add_argument(
        "--echo-delay-ms",
        type=float,
        default=300.0,
        help="with --llm echo: delay before the first word, in place of the model (default: 300)",
    )
    qwen = parser.add_argument_group("LLM options (with --llm qwen or gemini)")
    qwen.add_argument(
        "--stream",
        action="store_true",
        help="Speak each sentence as the model produces it instead of waiting for the whole reply",
    )
    qwen.add_argument(
        "--llm-model",
        help="Model id (default: qwen from ONIT_MODEL or opencode, gemini from GEMINI_MODEL)",
    )
    qwen.add_argument("--llm-timeout", type=float, default=30.0, help="Seconds (default: 30)")
    qwen.add_argument("--llm-max-tokens", type=int, default=160, help="Reply cap (default: 160)")
    qwen.add_argument("--llm-temperature", type=float, default=0.2, help="Default: 0.2")
    qwen.add_argument(
        "--system-prompt",
        help="Text file replacing the built-in system prompt ({context} is where the retrieved "
        "passages go; they are appended if the placeholder is missing)",
    )

    args = parser.parse_args(argv)
    if args.play_remote:
        args.play = True
    if args.aec and not (args.play_remote and args.mic and args.barge_in):
        print("--aec needs --play-remote, --mic and --barge-in", file=sys.stderr)
        return 2
    if args.ask_to_add and not args.mic:
        print("--ask-to-add needs --mic", file=sys.stderr)
        return 2
    if (args.no_docs or args.stream) and args.llm not in ("qwen", "gemini", "echo", "agent"):
        print("--no-docs and --stream need --llm qwen or --llm gemini", file=sys.stderr)
        return 2
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
