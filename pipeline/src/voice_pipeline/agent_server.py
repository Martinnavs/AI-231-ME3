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

from voice_pipeline import call_policy, fast_path, question
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
    PI_URL,
    TRANSIENT,
    build_tools,
    has_delegate_jobs,
    standard_servers,
    with_builtin_search,
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
from voice_pipeline.router import (
    HISTORY_PAIRS,
    NO_SLOT_SAY,
    SLOT_SAY,
    TIMEOUT_S,
    LocalRouter,
    Routed,
    spec,
)
from voice_pipeline.tools.smart_home import load_phonebook

ROOT = Path(__file__).resolve().parents[2]
_logger = logging.getLogger(__name__)
# A web lookup the router runs can take 0.8-2.9 s (grounded Gemini: 1.3 s with no search, 2.1 s with
# one). If it is still running this long after it started, the client is told a tool call is under
# way and plays that tool's pre-recorded line ("Let me look that up."), as for a model tool call.
# Simulated on 65 measured lookups: first sound 1.8 s -> 1.2 s and answer start 2.8 s -> 2.2 s for
# the questions that need a search; quick ones (median 0.8 s) finish before it would fire.
LOOKUP_ACK_S = 1.0
ACKED_LOOKUPS = frozenset({"web_answer"})
# Spoken at once when the router sends a web lookup (routed turns have no model `say`); listed in
# config/fast_phrases.json so the client plays it from memory.
LOOKUP_SAY = "Let me look that up."


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
    ) -> None:
        self.jobs_file = jobs_file  # set: delegate_status/cancel are offered only once a job exists
        self.doc_request = doc_request
        self.router = router
        self.notices = notices
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
                result = await self.acked_call(tool, args, on_tool, home, announced)
                if not home and TRANSIENT.search(str(result)) and not claimed:
                    result = await self.call_tool(
                        tool, args
                    )  # lookups are read-only: retry a blip once
                if not home and FAILURE.search(str(result)) and not claimed:
                    return None
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
            result = await self.agent.ask(
                messages, reset=True, on_text=on_text, on_tool=on_tool, on_say=on_say, local=local
            )
            return result if result.get("routed") else {**result, **info}
        done = await local(lambda: True)
        if done is not None:
            return done
        result = await self.agent.ask(
            messages, reset=True, on_text=on_text, on_tool=on_tool, on_say=on_say
        )
        return {**result, **info}

    async def handle(self, ws: ServerConnection) -> None:
        request = json.loads(await ws.recv())
        messages = [m for m in request["messages"] if m.get("role") != "system"]

        async def on_text(text: str) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps({"text": text}))

        async def on_say(text: str) -> None:
            # its own flagged message: the client speaks it now instead of holding it back
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps({"text": text, "flush": True}))

        async def on_tool(name: str, said: bool) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps({"event": "tool", "name": name, "said": said}))

        async with self.lock:
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
            if found is not None and (found.tool == "clock" or found.tool in self.tools):
                turn = asyncio.create_task(self.fast(found, on_text))
            elif self.router is not None and last.get("role") == "user":
                turn = asyncio.create_task(self.routed_turn(messages, on_text, on_tool, on_say))
            else:
                turn = asyncio.create_task(
                    self.agent.ask(
                        messages, reset=True, on_text=on_text, on_tool=on_tool, on_say=on_say
                    )
                )
            closed = asyncio.create_task(ws.wait_closed())
            await asyncio.wait({turn, closed}, return_when=asyncio.FIRST_COMPLETED)
            cancelled = not turn.done()
            if cancelled:
                await self.agent.cancel()
            result = await turn
            closed.cancel()
            said: list[str] = []
            if not cancelled and result["status"] == "ok" and self.notices is not None:
                said = self.notices.pop()
                for text in said:
                    await on_say(f" Update on your earlier request: {text}")
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
                "notices": said,
                **result,
            }
            with self.log.open("a") as sink:
                sink.write(json.dumps(row) + "\n")


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
) -> int:
    record: list[dict[str, Any]] = []
    servers, optional = standard_servers(pi_url)
    tools = await build_tools(record, live=live, servers=servers, optional=optional)
    system = system_file.read_text()
    llm_tools = with_builtin_search if builtin_search else (lambda t: t)  # what the model sees
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
    )
    try:
        guard = token_guard(token) if token else None
        async with serve(server.handle, "127.0.0.1", port, process_request=guard):
            note = "" if token else ", NO TOKEN (--no-token)"
            print(f"agent server on ws://127.0.0.1:{port}{note}, log {log}", flush=True)
            await asyncio.Future()
    finally:
        await close_llm_sessions()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8781)
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
        default=None,
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
    args = parser.parse_args(argv)
    token = None if args.no_token else os.environ.get("LAN_SPEECH_TOKEN")
    if not token and not args.no_token:
        parser.error("LAN_SPEECH_TOKEN is not set (pass --no-token only for tests)")
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
            )
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
