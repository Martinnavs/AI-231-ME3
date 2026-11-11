"""Agent server: the agent behind a local WebSocket, for `doc_chat --llm agent`.

    LAN_SPEECH_TOKEN=... uv run python -m voice_pipeline.agent_server [--port 8781]
        [--live URL] [--pi-url URL]

Protocol (the client is src/doc_chat/agent_client.py in the root project), one request per
connection: {"messages": [...]} in; {"text": ...} pieces, then {"done": true} or {"error": ...}
out. The server owns the system prompt, so client system messages are dropped. Requests are
served one at a time; a client that disconnects before "done" cancels its turn. Every turn is
logged to out/agent_server/<start time>.jsonl with its question, tool calls and timings.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request as HttpRequest
from websockets.http11 import Response as HttpResponse

from voice_pipeline import agent_tools, call_policy, fast_path, question, selfinfo, venue
from voice_pipeline.agent import (
    KEEPALIVE_S,
    SAY_DELAY_SLOW,
    SAY_DELAY_TODAY,
    Agent,
    build_llm,
    close_llm_sessions,
)
from voice_pipeline.agent_tools import (
    FAILURE,
    IDLE_DELEGATE_TOOLS,
    MODEL_HIDDEN,
    PI_URL,
    TRANSIENT,
    build_tools,
    configure,
    has_delegate_jobs,
    standard_servers,
    with_builtin_search,
    without_tools,
)
from voice_pipeline.chain_state import (
    ChainState,
    is_answer,
    is_chain_tool,
    is_followup,
    wants_cancel,
)
from voice_pipeline.device_say import device_sentence
from voice_pipeline.doc_answer import (
    DOC_ANSWER_TIMEOUT_S,
    DocAnswer,
    Request,
    gemini_request,
    passages_of,
)
from voice_pipeline.doc_answer import answer as doc_answer
from voice_pipeline.hedge import HedgedAgent, HedgeGate, gated_tools
from voice_pipeline.notices import NoticeQueue
from voice_pipeline.payload_guard import PayloadFilter
from voice_pipeline.router import (
    HISTORY_PAIRS,
    NO_SLOT_SAY,
    SLOT_SAY,
    TIMEOUT_S,
    LocalRouter,
    Routed,
    spec,
)
from voice_pipeline.scratchpad import PROFILE_RULE, Scratchpad, install_profile
from voice_pipeline.tools.smart_home import load_phonebook

ROOT = Path(__file__).resolve().parents[2]
_logger = logging.getLogger(__name__)
# A web lookup the router runs can take 0.8-2.9 s (grounded Gemini: 1.3 s with no search, 2.1 s with
# one). If it is still running this long after it started, the client is told a tool call is under
# way and plays that tool's pre-recorded line ("Let me look that up."), as for a model tool call.
# Simulated on 65 measured lookups: first sound 1.8 s -> 1.2 s and answer start 2.8 s -> 2.2 s for
# the questions that need a search; quick ones (median 0.8 s) finish before it would fire.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
LOOKUP_ACK_S = 1.0
ACKED_LOOKUPS = frozenset({"web_answer"})
# Spoken at once when the router sends a web lookup (routed turns have no model `say`); listed in
# config/fast_phrases.json so the client plays it from memory.
OFFLINE_SAY = "I can't reach the internet right now, but I can still do home and device things."
LOOKUP_FAIL_SAY = "I couldn't get that from the internet right now."
OFFLINE_LOOKUP_S = 1.0  # offline, a lookup gets this long (a cached forecast answers in ~0.1 s)
LOOKUP_GIVE_S = 4.0  # a routed lookup that failed after this long is not handed to Gemini again
LOOKUP_SAY = "Let me look that up."
MUSIC_SAY = (
    "Finding some music."  # a routed PLAY_MUSIC still running after MUSIC_SAY_S (a music search)
)
MUSIC_SAY_S = 0.7
NOTICE_POLL_S = 0.25  # how often the notices file is read while a client listens for them


def token_guard(token: str) -> Callable[[ServerConnection, HttpRequest], HttpResponse | None]:
    """A `process_request` hook: the WebSocket handshake needs `Authorization: Bearer <token>`.
    Anything else gets HTTP 401 before a turn can start (the delegate sandbox shares the laptop's
    loopback, so the port alone is not a boundary). The token is never logged."""
    expected = token.encode()

    def check(connection: ServerConnection, request: HttpRequest) -> HttpResponse | None:
        header = request.headers.get("Authorization") or ""
        scheme, _, given = header.partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), expected):
            return None
        _logger.warning(
            "rejected a connection without a valid token from %s", connection.remote_address
        )
        return connection.respond(401, "Unauthorized\n")

    return check


class OnlineProbe:
    """Whether Gemini's API is reachable: a TCP connect every `every` s; offline after two
    failures in a row, online again after one success. A turn that needs Gemini while offline is
    answered at once instead of waiting out the LLM timeout (15 s with the retry)."""

    def __init__(self, host: str = "generativelanguage.googleapis.com", every: float = 5.0) -> None:
        self.host, self.every, self.online, self._fails = host, every, True, 0

    async def check(self) -> bool:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(self.host, 443), 2.0)
        except (TimeoutError, OSError):
            return False
        writer.close()
        return True

    async def run(self) -> None:
        while True:
            ok = await self.check()
            self._fails = 0 if ok else self._fails + 1
            self.online = ok or self._fails < 2
            await asyncio.sleep(self.every)


class AgentServer:
    """Serves `agent` turns over WebSocket and logs each one."""

    def __init__(
        self,
        agent: Any,
        record: list[dict[str, Any]],
        log: Path,
        tools: Any = None,
        router: LocalRouter | None = None,
        notices: NoticeQueue | None = None,
        doc_request: Request | None = None,
        jobs_file: Path | None = None,
        chain: ChainState | None = None,
        case_f_block: str = "",
    ) -> None:
        self.chain = chain  # set: a running research/build task steers the turns that follow it
        self.case_f_block = case_f_block  # P2: the Case F chain protocol, out of the base prompt
        self.jobs_file = jobs_file  # set: delegate_status/cancel are offered only once a job exists
        self.doc_request = doc_request
        self.router = router
        self.notices = notices
        # notices read from the file, not yet played to the end (or cut)
        self.unacked: dict[int, dict[str, str]] = {}
        self.notice_ids = 0
        self.test_delay: tuple[str, float] | None = None  # (word, seconds): --test-delay-word
        self.probe: OnlineProbe | None = None  # set by run(); None: always online (tests)
        self.router_lines: dict[str, str] = {}  # reply we spoke -> the router lines behind it
        self.agent = agent
        self.record = record
        self.log = log
        self.lock = asyncio.Lock()
        # with the agent's tools, common home commands skip the LLM (fast_path.py)
        self.tools = {t.name: t for t in tools.standard_tools} if tools is not None else {}

    async def fast(self, found: fast_path.Match, on_text: Any) -> dict[str, Any]:
        """Run the tool the LLM would have called, then say the fixed confirmation."""
        started = time.perf_counter()
        if found.tool == "clock":  # the clock answers; no tool runs
            self.record.append({"tool": "clock", "args": dict(found.args)})
            text = fast_path.local_answer(found, datetime.now(ZoneInfo(fast_path.TIMEZONE)))
            await on_text(text)
            elapsed = time.perf_counter() - started
            return {"status": "ok", "text": text, "first_call_s": 0.0, "first_text_s": elapsed,
                    "end_s": elapsed, "fast_path": True}  # fmt: skip
        results: list[Any] = []

        async def done(result: Any, *args: Any, **kwargs: Any) -> None:
            results.append(result)

        params = SimpleNamespace(
            function_name=found.tool,
            tool_call_id="fast-path",
            arguments=dict(found.args),
            result_callback=done,
        )
        await self.tools[found.tool].handler(params)
        text = fast_path.spoken_result(found, results[0] if results else {"ok": False})
        await on_text(text)
        elapsed = time.perf_counter() - started
        return {"status": "ok", "text": text, "first_call_s": 0.0, "first_text_s": elapsed,
                "end_s": elapsed, "fast_path": True}  # fmt: skip

    def history(self, messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """The last earlier (user, router lines) pair(s); a reply the router did not write
        (Gemini's) stands in as a plain `none|` line."""
        pairs: list[tuple[str, str]] = []
        said: str | None = None
        for m in messages[:-1]:
            if m.get("role") == "user":
                said = str(m.get("content", ""))
            elif m.get("role") == "assistant" and said is not None:
                reply = str(m.get("content", ""))
                pairs.append(
                    (said, self.router_lines.get(reply) or "none|" + " ".join(reply.split())[:100])
                )
                said = None
        return pairs[-HISTORY_PAIRS:]

    def steer(self, messages: list[dict[str, Any]], *, case_f: bool) -> list[dict[str, Any]]:
        """The messages for Gemini: the chain's state, and when `case_f` the Case F chain
        protocol, appended to the last user message (P2). The protocol is not in the base
        prompt: it goes with a turn while a chain is active, when the router escalates a
        turn Gemini cannot see coming, and when there is no router to filter."""
        if not messages:
            return messages
        last = messages[-1]
        if last.get("role") != "user" or not isinstance(last.get("content"), str):
            return messages
        suffix = ""
        if self.chain is not None and self.chain.active():
            suffix += "\n" + self.chain.block()
        if case_f and self.case_f_block:
            suffix += "\n" + self.case_f_block
        if not suffix:
            return messages
        return [*messages[:-1], {**last, "content": last["content"] + suffix}]

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Run one of the agent's tools directly and return its result."""
        results: list[Any] = []

        async def done(result: Any, *rest: Any, **kwargs: Any) -> None:
            results.append(result)

        params = SimpleNamespace(
            function_name=name, tool_call_id="router", arguments=args, result_callback=done
        )
        await self.tools[name].handler(params)
        return results[0] if results else {"ok": False}

    async def acked_call(
        self, tool: str, args: dict[str, Any], on_tool: Any, home: bool, said: bool = False
    ) -> Any:
        """`call_tool`, telling the client after LOOKUP_ACK_S that a slow lookup is under way
        (`said`: a sentence for it was already spoken, so the client plays no filler line)."""
        if not home and self.offline():
            # offline: a cached forecast still answers at once; anything else fails fast
            try:
                return await asyncio.wait_for(self.call_tool(tool, args), OFFLINE_LOOKUP_S)
            except TimeoutError:
                return "The service did not answer."
        if home or on_tool is None or tool not in ACKED_LOOKUPS:
            return await self.call_tool(tool, args)

        async def announce() -> None:
            await asyncio.sleep(LOOKUP_ACK_S)
            await on_tool(tool, said)

        ack = asyncio.create_task(announce())
        try:
            return await self.call_tool(tool, args)
        finally:
            ack.cancel()

    async def said_if_slow(self, tool: str, args: dict[str, Any], on_say: Any) -> Any:
        """`call_tool`; if it is still running after MUSIC_SAY_S, MUSIC_SAY is spoken first."""
        call = asyncio.ensure_future(self.call_tool(tool, args))
        done, _ = await asyncio.wait({call}, timeout=MUSIC_SAY_S)
        if not done:
            await on_say(MUSIC_SAY + " ")
        return await call

    async def lookup(self, tool: str, args: dict[str, Any]) -> Any:
        """The web lookup, its blip retried once; None when it failed."""
        result = await self.call_tool(tool, args)
        if TRANSIENT.search(str(result)):  # lookups are read-only
            result = await self.call_tool(tool, args)
        return None if FAILURE.search(str(result)) else result

    async def doc_race(self, user: str) -> tuple[str | None, dict[str, Any]]:
        """Passages of the documents and one Gemini request, checked (doc_answer.py). Never raises:
        (the answer or None, the `race` fields)."""
        started = time.perf_counter()
        got = DocAnswer("timeout")

        async def ask() -> DocAnswer:
            try:
                found = passages_of(await self.call_tool("search_docs", {"query": user}))
            except Exception:
                return DocAnswer("error")
            assert self.doc_request is not None
            return await doc_answer(self.doc_request, found, user)

        try:
            got = await asyncio.wait_for(ask(), DOC_ANSWER_TIMEOUT_S)
        except TimeoutError:
            pass
        except Exception:
            got = DocAnswer("error")
        fields: dict[str, Any] = {
            "docs": got.status,
            "doc_s": round(time.perf_counter() - started, 3),
        }
        if got.rejected:
            fields["rejected"] = got.rejected  # kept so a failed check can be diagnosed later
        return got.text, fields

    async def raced_lookup(
        self,
        tool: str,
        args: dict[str, Any],
        user: str,
        on_tool: Any,
        race: dict[str, Any],
        said: bool = False,
    ) -> str | None:
        """The web lookup and the documents' answer at the same time: the document answer if it
        passed its checks, else the web result; None if neither is usable. Nothing is spoken here;
        if neither has decided LOOKUP_ACK_S after the start the client is told a lookup is under
        way. `race` is filled in."""

        async def announce() -> None:
            await asyncio.sleep(LOOKUP_ACK_S)
            await on_tool(tool, said)

        web = asyncio.create_task(self.lookup(tool, args))
        ack = (
            asyncio.create_task(announce())
            if on_tool is not None and tool in ACKED_LOOKUPS
            else None
        )
        try:
            text, fields = await self.doc_race(user)
            race.update(fields)
            if text is not None:
                race["winner"] = "docs"
                return text
            try:
                result = await web
            except Exception:
                result = None
            race["winner"] = "web" if result is not None else None
            return None if result is None else str(result)
        finally:
            for task in (ack, web):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    async def run_routed(
        self,
        routed: Routed,
        started: float,
        on_text: Any,
        claim: Callable[[], bool] = lambda: True,
        on_tool: Any = None,
        on_say: Any = None,
    ) -> dict[str, Any] | None:
        """Run the router's calls with the agent's own tools and say the result. `claim` is asked
        before the first home command and before speaking: False means Gemini already answered
        (nothing is run or said). None: Gemini should take the turn, because the claim failed or a
        lookup failed before anything changed (it retries with another topic, as it does without
        the router)."""
        spoken: list[str] = []
        claimed = False
        announced = False

        def take() -> bool:
            nonlocal claimed
            claimed = claimed or claim()
            return claimed

        steps = call_policy.plan(routed.calls, routed.user, set(self.tools))
        for step in steps:
            call = step.call
            name = call.name
            if name == "none":
                spoken.append(call.args[0].strip())
            elif name == "get_current_time":
                zone = spec.to_tool_args(call).get("timezone", fast_path.TIMEZONE)
                now = datetime.now(ZoneInfo(zone))
                what = fast_path.clock_question(routed.user)
                text = fast_path.local_answer(fast_path.Match("clock", {"what": what}, None), now)
                spoken.append(
                    text
                    if zone == fast_path.TIMEZONE
                    else f"{text[:-1]} in {zone.split('/')[-1].replace('_', ' ')}."
                )
            else:
                home, tool, args = step.home, step.tool, step.args
                if (
                    on_say is not None
                    and not home
                    and not announced
                    and tool in call_policy.DOCUMENT_LOOKUPS
                ):
                    announced = True
                    await on_say(LOOKUP_SAY + " ")
                if (
                    not home
                    and len(steps) == 1
                    and tool in call_policy.DOCUMENT_LOOKUPS
                    and self.doc_request is not None
                    and "search_docs" in self.tools
                ):
                    routed.race = {}
                    asked = args.get("query") or args.get("topic") or routed.user
                    said = await self.raced_lookup(
                        tool, args, asked, on_tool, routed.race, announced
                    )
                    if said is None:
                        return None
                    spoken.append(said)
                    continue
                if home and not take():
                    return None
                if home and name == "PLAY_MUSIC" and on_say is not None and not announced:
                    announced = True
                    result = await self.said_if_slow(tool, args, on_say)
                else:
                    result = await self.acked_call(tool, args, on_tool, home, announced)
                if (
                    not home
                    and TRANSIENT.search(str(result))
                    and not claimed
                    and not self.offline()
                ):
                    result = await self.call_tool(
                        tool, args
                    )  # lookups are read-only: retry a blip once
                if not home and FAILURE.search(str(result)) and not claimed:
                    return None
                if home and isinstance(result, str):  # the music tool's own sentence
                    spoken.append(result)
                    continue
                if home:
                    ok = isinstance(result, dict) and result.get("ok") is not False
                    if ok:
                        spoken.append(
                            NO_SLOT_SAY.get(name)
                            or SLOT_SAY[name].format(call.args[0] if call.args else "")
                        )
                    else:
                        spoken.append(
                            fast_path.spoken_result(fast_path.Match(tool, args, None), result)
                        )
                        break
                elif tool == "device_status":
                    said = device_sentence(result, routed.user)
                    if said is None:
                        return None
                    spoken.append(said)
                else:
                    spoken.append(str(result))
        if not take():
            return None
        text = " ".join(spoken)
        await on_text(text)
        self.router_lines[text] = routed.lines
        while len(self.router_lines) > 64:
            self.router_lines.pop(next(iter(self.router_lines)))
        elapsed = time.perf_counter() - started
        return {
            "status": "ok",
            "text": text,
            "first_call_s": routed.seconds,
            "first_text_s": elapsed,
            "end_s": elapsed,
            "routed": True,
            "router_s": routed.seconds,
            "router_lines": routed.lines,
            **({"race": routed.race} if routed.race else {}),
        }

    def offline(self) -> bool:
        return self.probe is not None and not self.probe.online

    async def spoken(self, text: str, on_text: Any, **info: Any) -> dict[str, Any]:
        await on_text(text)
        return {"status": "ok", "text": text, **info}

    async def gemini(
        self, messages: list[dict[str, Any]], on_text: Any, on_tool: Any, on_say: Any
    ) -> dict[str, Any]:
        if self.offline():
            return await self.spoken(OFFLINE_SAY, on_text, offline=True)
        result: dict[str, Any] = await self.agent.ask(
            messages, reset=True, on_text=on_text, on_tool=on_tool, on_say=on_say
        )
        return result

    async def routed_turn(
        self, messages: list[dict[str, Any]], on_text: Any, on_tool: Any, on_say: Any
    ) -> dict[str, Any]:
        """The local router and Gemini answer the turn at the same time when the agent is hedged
        (the first to answer wins, hedge.py); otherwise the router goes first and Gemini only
        takes what it cannot run."""
        assert self.router is not None
        router = self.router
        started = time.perf_counter()
        user = str(messages[-1].get("content", ""))
        info: dict[str, Any] = {}
        hedged = isinstance(self.agent, HedgedAgent)  # Gemini may win: say nothing before the claim

        async def local(claim: Callable[[], bool]) -> dict[str, Any] | None:
            routed = await router.route(self.history(messages), user, set(self.tools))
            info.update(router_s=routed.seconds, router_lines=routed.lines)
            if self.offline() and re.match(r"\s*(web_answer|wiki_summary)\|", routed.lines):
                info["router_reason"] = "offline lookup"  # no cache: fail at once
                return None
            if routed.reason is None:
                done = await self.run_routed(
                    routed, started, on_text, claim, on_tool, None if hedged else on_say
                )
                if done is not None:
                    return done
                routed.reason = "lookup failed or Gemini first"
                if routed.race:
                    info["race"] = routed.race
            info["router_reason"] = routed.reason
            return None

        if isinstance(self.agent, HedgedAgent):
            # the requests start before the router decides, so the base prompt only; the Case F
            # protocol joins the turns from the moment a chain is active (the handle above)
            result = await self.agent.ask(
                messages, reset=True, on_text=on_text, on_tool=on_tool, on_say=on_say, local=local
            )
            return result if result.get("routed") else {**result, **info}
        done = await local(lambda: True)
        if done is not None:
            return done
        looked_up = re.match(
            r"\s*(web_answer|wiki_summary|weather)\|", str(info.get("router_lines", ""))
        )
        if looked_up and (self.offline() or time.perf_counter() - started > LOOKUP_GIVE_S):
            # the link is slow or down: Gemini would only repeat the same slow trip
            return await self.spoken(LOOKUP_FAIL_SAY, on_text, **info)
        # the router could not answer: the escalated turn gets the full Case F protocol
        result = await self.gemini(self.steer(messages, case_f=True), on_text, on_tool, on_say)
        return {**result, **info}

    def collect_notices(self) -> None:
        """Move the delegate server's new notices into `unacked` (each gets an id)."""
        for item in self.notices.pop_items() if self.notices is not None else []:
            self.notice_ids += 1
            self.unacked[self.notice_ids] = item
            self.note_chain_job(item.get("job", ""))

    def note_chain_job(self, job_id: str) -> None:
        """The chain's job ended: remember what it made. TODO(ticket 04): move to
        awaiting_publish_confirm when the user asked to publish."""
        chain = self.chain
        if chain is None or not job_id or chain.data["job_id"] != job_id or not chain.active():
            return
        try:
            jobs = json.loads(chain.path.with_name("jobs.json").read_text())["jobs"]
            row = next(j for j in jobs if j["id"] == job_id)
        except (OSError, ValueError, KeyError, StopIteration):
            return
        files = row.get("files") or []
        if row.get("status") == "done" and files:
            chain.update(artifact_path=str(files[0]))
        if row.get("status") == "done":
            chain.mark_done()

    async def drop_chain(self, on_text: Any) -> dict[str, Any]:
        """The user said never mind: end the chain, and stop its job if it still runs."""
        chain = self.chain
        assert chain is not None
        job = str(chain.data["job_id"])
        chain.update("idle")
        if job and "delegate_cancel" in self.tools:
            with contextlib.suppress(Exception):
                await self.call_tool("delegate_cancel", {"job": job})
        return await self.spoken("Okay, dropped.", on_text)

    async def notice_channel(self, ws: ServerConnection) -> None:
        """A push channel for finished jobs (R1): every notice goes out the moment it appears in
        the file, and again on a new connection until the client acks it as played to the end.
        The client decides when the user is idle; this side only keeps the notices."""
        sent: set[int] = set()

        async def acks() -> None:
            async for raw in ws:
                with contextlib.suppress(ValueError, AttributeError, TypeError):
                    self.unacked.pop(int(json.loads(raw)["ack"]), None)

        reader = asyncio.create_task(acks())
        try:
            while not reader.done():
                self.collect_notices()
                for nid, item in list(self.unacked.items()):
                    if nid not in sent:
                        sent.add(nid)
                        note = {"id": nid, "text": item["text"], "job": item.get("job", "")}
                        await ws.send(json.dumps({"notice": note}))
                await asyncio.sleep(NOTICE_POLL_S)
        except ConnectionClosed:
            pass
        finally:
            reader.cancel()

    async def delayed(self, work: Any, user: str) -> dict[str, Any]:
        """`work`, after the test-only pause when the user's words contain --test-delay-word (to
        hear the client's wait and give-up lines on demand); the pause is cancelled like a turn."""
        if self.test_delay is not None and self.test_delay[0] in user.lower():
            await asyncio.sleep(self.test_delay[1])
        result: dict[str, Any] = await work
        return result

    async def sync_music_paused(self) -> None:
        """The client paused the music itself (the user spoke over it): the music tool's state
        follows, silently, so a bare "play" resumes it. A no-op when nothing is playing."""
        if "music" not in self.tools:
            return
        with contextlib.suppress(Exception):
            await self.call_tool("music", {"action": "pause", "query": ""})

    async def handle(self, ws: ServerConnection) -> None:
        request = json.loads(await ws.recv())
        if request.get("notices"):
            await self.notice_channel(ws)
            return
        messages = [m for m in request["messages"] if m.get("role") != "system"]
        if request.get("music") == "paused":
            await self.sync_music_paused()

        payloads = PayloadFilter()

        async def send_event(event: dict[str, Any]) -> None:
            await ws.send(json.dumps(event))

        async def on_text(text: str) -> None:
            if text := payloads.feed(text):
                with contextlib.suppress(ConnectionClosed):
                    await ws.send(json.dumps({"text": text}))

        async def on_say(text: str) -> None:
            # its own flagged message: the client speaks it now instead of holding it back
            if text := payloads.feed(text):
                with contextlib.suppress(ConnectionClosed):
                    await ws.send(json.dumps({"text": text, "flush": True}))

        async def on_tool(name: str, said: bool) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps({"event": "tool", "name": name, "said": said}))

        async with self.lock:
            agent_tools.set_event_sink(send_event)
            if self.jobs_file is not None:
                self.agent.hide(
                    frozenset() if has_delegate_jobs(self.jobs_file) else IDLE_DELEGATE_TOOLS
                )
            before = len(self.record)
            last = messages[-1] if messages else {}
            question.current = str(last.get("content", "")) if last.get("role") == "user" else ""
            found = (
                fast_path.match(str(last.get("content", "")))
                if last.get("role") == "user"
                else None
            )
            agent_tools.reset_turn_lookups()
            chain = self.chain
            if (
                found is not None
                and found.args.get("what") in ("place", "barangay")
                and chain is not None
                and chain.active()
            ):
                found = None  # "where are we" during a task is about the task: Gemini sees it
            said = question.current
            if (
                chain is not None
                and chain.active()
                and chain.data["done"]
                and not wants_cancel(said)
                and not is_followup(said)
            ):
                chain.update("idle")  # the finished task is over; its facts go with it
            work: Any
            if chain is not None and chain.active() and wants_cancel(said):
                work = self.drop_chain(on_text)
            elif found is not None and (found.tool == "clock" or found.tool in self.tools):
                work = self.fast(found, on_text)
            elif chain is not None and chain.active() and last.get("role") == "user":
                # a research/build task is under way: Gemini sees its state and protocol
                work = self.gemini(self.steer(messages, case_f=True), on_text, on_tool, on_say)
            elif (
                self.router is not None
                and last.get("role") == "user"
                and not call_policy.answers_an_offer(messages, self.router_lines)
            ):
                work = self.routed_turn(messages, on_text, on_tool, on_say)
            else:
                # no router: nothing filters the turn, so the Case F protocol goes with it
                work = self.gemini(
                    self.steer(messages, case_f=self.router is None), on_text, on_tool, on_say
                )
            turn = asyncio.create_task(self.delayed(work, question.current))
            closed = asyncio.create_task(ws.wait_closed())
            await asyncio.wait({turn, closed}, return_when=asyncio.FIRST_COMPLETED)
            cancelled = not turn.done()
            if cancelled:
                await self.agent.cancel()
            result = await turn
            agent_tools.set_event_sink(None)
            closed.cancel()
            for held in payloads.finish():
                await on_text(held)
            if not cancelled:
                reply = {"done": True} if result["status"] == "ok" else {"error": "timeout"}
                try:
                    await ws.send(json.dumps(reply))
                except ConnectionClosed:
                    cancelled = True
            row = {
                "time": time.strftime("%H:%M:%S"),
                "question": messages[-1]["content"] if messages else "",
                "calls": self.record[before:],
                "cancelled": cancelled,
                **({"dropped_payload": payloads.dropped} if payloads.dropped else {}),
                **result,
            }
            with self.log.open("a") as sink:
                sink.write(json.dumps(row) + "\n")
            if chain is not None and not cancelled and chain.active():
                # lights, music, the clock: turns the router or the fast path handle make no chain
                # call, so they never counted; five of them left a research chain alive until a
                # website request picked its notes up (2026-11-11)
                used = [str(c.get("tool", "")) for c in self.record[before:]]
                if not any(is_chain_tool(t) for t in used) and not is_answer(said):
                    chain.touch_unrelated()


async def run(
    port: int,
    live: str | None,
    system_file: Path,
    pi_url: str | None,
    fast: bool = True,
    hedge: float | None = 0.0,
    keepalive: float = KEEPALIVE_S,
    ipv4_only: bool = False,
    router_url: str | None = None,
    router_timeout: float = TIMEOUT_S,
    router_hold: float = 0.0,
    notices: Path | None = None,
    builtin_search: bool = False,
    token: str | None = None,
    hide_idle_delegate: Path | None = None,
    say_delays: dict[str, float] | None = None,
    test_delay: tuple[str, float] | None = None,
    host: str = "127.0.0.1",
) -> int:
    record: list[dict[str, Any]] = []
    state_dir = (
        notices.parent
        if notices
        else Path(agent_tools.DELEGATE_STATE_DIR or "~/.local/state/voice_agent/delegate")
    ).expanduser()
    chain = ChainState(state_dir)  # an expired state is reset to idle here
    scratch: Scratchpad | None = None
    with contextlib.suppress(OSError, KeyError, ValueError):
        pad = Scratchpad(agent_tools.delegate_workspace() / "scratch" / "NOTES.md")
        chain.on_change = lambda event: pad.reset() if event == "end" else None
        scratch = pad
    servers, optional = standard_servers(pi_url)
    tools = await build_tools(
        record, live=live, servers=servers, optional=optional, chain=chain, scratchpad=scratch
    )
    system = system_file.read_text()
    selfinfo.load(agent_tools.REPO_ROOT / "config" / "profile.md")  # answers about the owner, local
    with contextlib.suppress(OSError, KeyError, ValueError):
        profile = agent_tools.REPO_ROOT / "config" / "profile.md"
        if install_profile(profile, agent_tools.delegate_workspace() / "scratch") and (
            system_file.name == "system_agent.txt"
        ):
            system += "\n\n" + PROFILE_RULE  # only when the owner filled one in
    with contextlib.suppress(Exception):  # once, about 1 s; offline the answer names the city only
        await asyncio.wait_for(venue.resolve(), venue.TIMEOUT_S + 1)
    if (line := venue.prompt_line()) and system_file.name == "system_agent.txt":
        system += "\n\n" + line
    if agent_tools.publish_configured() and system_file.name == "system_agent.txt":
        system += "\n\n" + agent_tools.PUBLISH_RULE  # the tool exists only when a repo is set
    case_f_block = ""  # P2: the Case F chain protocol, kept out of the base prompt
    if not builtin_search and system_file.name == "system_agent.txt":
        with contextlib.suppress(OSError):
            case_f_block = system_file.with_name("system_agent_case_f.txt").read_text()
    seen_by_model = with_builtin_search if builtin_search else (lambda t: t)

    def llm_tools(t: Any) -> Any:  # what the model sees: no music tool (smart_home reaches it)
        return without_tools(seen_by_model(t), MODEL_HIDDEN)

    if builtin_search:  # the model has to know the date to know what can have changed
        system = system.replace(
            "{today}", f"{datetime.now(ZoneInfo(fast_path.TIMEZONE)):%A, %B %-d, %Y}"
        )

    def llm() -> Any:
        return build_llm(
            system, keepalive_s=keepalive, ipv4_only=ipv4_only, builtin_search=builtin_search
        )

    agent: Agent | HedgedAgent
    if hedge is None:
        agent = Agent(llm(), llm_tools(tools), say_delays=say_delays)
        await agent.start()
        await agent.ask([{"role": "user", "content": "Say OK."}], reset=True)  # warm-up
    else:  # R4: two requests per turn, the first to answer wins (hedge.py)
        gate = HedgeGate()
        agent = HedgedAgent(
            [
                Agent(llm(), llm_tools(gated_tools(tools, gate, i)), say_delays=say_delays)
                for i in range(2)
            ],
            gate,
            hedge,
            hold_s=router_hold,
        )
        await agent.start()
        await agent.warm_up()
    log = ROOT / "out/agent_server" / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    router = None
    if router_url:
        book = {c["name"]: c["relation"] for c in load_phonebook()}
        router = LocalRouter(router_url, book, router_timeout, api_key=token)
        await router.warm_up()
    queue = NoticeQueue(notices) if notices else None
    doc_request = None
    if router and any(t.name == "search_docs" for t in tools.standard_tools):
        doc_request = await gemini_request(keepalive, ipv4_only)
    server = AgentServer(
        agent,
        record,
        log,
        tools if fast or router else None,
        router,
        queue,
        doc_request,
        hide_idle_delegate,
        chain,
        case_f_block=case_f_block,
    )
    server.test_delay = test_delay
    server.probe = OnlineProbe()
    probe_task = asyncio.create_task(server.probe.run())
    try:
        guard = token_guard(token) if token else None
        async with serve(server.handle, host, port, process_request=guard):
            note = "" if token else ", NO TOKEN (--no-token)"
            print(f"agent server on ws://{host}:{port}{note}, log {log}", flush=True)
            await asyncio.Future()
    finally:
        probe_task.cancel()
        await close_llm_sessions()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--host",
        default=os.environ.get("AGENT_HOST", "127.0.0.1"),
        help="address to listen on ($AGENT_HOST): 127.0.0.1 (the laptop stack) or the Pi's cable "
        "address (HARNESS=pi); anything but loopback needs the session token",
    )
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument(
        "--apps-root",
        default=None,
        help="the web apps folder (default ~/agent_sandbox/workspace/apps; $APPS_ROOT)",
    )
    parser.add_argument(
        "--apps-host",
        default=os.environ.get("APPS_PUBLIC_HOST"),
        help="open_app sends the client http://<this host>:8795/<name>/ instead of opening a "
        "browser here (HARNESS=pi: the Pi's cable address; $APPS_PUBLIC_HOST)",
    )
    parser.add_argument("--delegate-state-dir", default=None, help="jobs.json, notices.jsonl")
    parser.add_argument("--delegate-config", default=None, help="a delegate.toml (paths, limits)")
    parser.add_argument(
        "--no-token",
        action="store_true",
        help="serve without LAN_SPEECH_TOKEN (tests only: any local process, the delegate "
        "sandbox included, could then drive the agent's tools)",
    )
    parser.add_argument("--live", default=None, help="ME2 dashboard URL; default records only")
    parser.add_argument("--system", type=Path, default=None, help="the system prompt file")
    parser.add_argument(
        "--builtin-search",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="prototype: the model searches the web itself (Gemini's Google Search in the same "
        "request as the function tools, in place of web_answer); uses system_agent_search.txt. "
        "With --hedge both requests would search: use --hedge -1",
    )
    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    parser.add_argument(
        "--fast-path",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="answer common home commands without the LLM (fast_path.py)",
    )
    parser.add_argument(
        "--hedge",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="R4: send every LLM request twice and keep the first to answer; SECONDS > 0 sends "
        "the second only if the first has said nothing by then; -1 turns it off",
    )
    parser.add_argument(
        "--keepalive",
        type=float,
        default=KEEPALIVE_S,
        metavar="SECONDS",
        help="keep idle connections to Gemini this long (0 = the SDK default of 15 s, which lets "
        "them go cold between turns)",
    )
    parser.add_argument(
        "--ipv4",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="connect to Gemini over IPv4 only (the IPv6 path to Google resets connections on this "
        "network; docs/OPTIMIZATION-RESEARCH.md)",
    )
    parser.add_argument(
        "--router-url",
        default=os.environ.get("ROUTER_URL") or None,
        help="llama-server of the fine-tuned router (router.py): it answers simple turns itself "
        "and hands the rest to Gemini; off by default",
    )
    parser.add_argument("--router-timeout", type=float, default=TIMEOUT_S, metavar="SECONDS")
    parser.add_argument(
        "--router-hold",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="0: a plain race, whichever of the router and Gemini answers first wins (Gemini when "
        "the network is good, the router when it is slow or down); SECONDS > 0 keeps Gemini from "
        "claiming the turn that long so the router's complete answer is not beaten by Gemini's "
        "short filler sentence (about +0.3 s on turns the router defers)",
    )
    parser.add_argument(
        "--say-delays",
        choices=["default", "today", "slow"],
        default="default",
        help="how long the model's `say` is held per tool: today = weather only, slow = also "
        "web_answer, search_docs and delegate",
    )
    parser.add_argument(
        "--hide-idle-delegate",
        action="store_true",
        help="offer delegate_status and delegate_cancel only once the delegate server's job table "
        "(jobs.json next to --notices) lists a job: fewer schema tokens per request",
    )
    parser.add_argument(
        "--notices",
        default=str(Path.home() / ".local/state/voice_agent/delegate/notices.jsonl"),
        help="where the delegate server writes the spoken notices of jobs that ended; '' for none",
    )
    parser.add_argument(
        "--test-delay-word",
        default="",
        help="TEST ONLY: a turn whose words contain this waits --test-delay-s before it is "
        "answered, so the client's wait lines and give-up line can be heard on demand",
    )
    parser.add_argument("--test-delay-s", type=float, default=12.0)
    args = parser.parse_args(argv)
    token = None if args.no_token else os.environ.get("LAN_SPEECH_TOKEN")
    if not token and not args.no_token:
        parser.error("LAN_SPEECH_TOKEN is not set (pass --no-token only for tests)")
    if args.no_token and args.host not in LOOPBACK:
        parser.error("--no-token is only for loopback (--host 127.0.0.1)")
    configure(
        apps_root=args.apps_root,
        apps_host=args.apps_host,
        delegate_state_dir=args.delegate_state_dir,
        delegate_config=args.delegate_config,
    )
    try:
        return asyncio.run(
            run(
                args.port,
                args.live,
                args.system
                or ROOT
                / (
                    "config/eval/system_agent_search.txt"
                    if args.builtin_search
                    else "config/eval/system_agent.txt"
                ),
                args.pi_url or None,
                args.fast_path,
                None if args.hedge < 0 else args.hedge,
                args.keepalive,
                args.ipv4,
                args.router_url,
                args.router_timeout,
                args.router_hold,
                Path(args.notices) if args.notices else None,
                args.builtin_search,
                token,
                Path(args.notices).with_name("jobs.json")
                if args.hide_idle_delegate and args.notices
                else None,
                {"today": SAY_DELAY_TODAY, "slow": SAY_DELAY_SLOW}.get(args.say_delays),
                (args.test_delay_word.lower(), args.test_delay_s) if args.test_delay_word else None,
                args.host,
            )
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
