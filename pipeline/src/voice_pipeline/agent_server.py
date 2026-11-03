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
import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from voice_pipeline import fast_path, question
from voice_pipeline.agent import KEEPALIVE_S, Agent, build_llm, close_llm_sessions
from voice_pipeline.agent_tools import (
    FAILURE,
    PI_URL,
    TRANSIENT,
    build_tools,
    standard_servers,
    with_builtin_search,
)
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
# A web lookup the router runs can take 0.8-2.9 s (grounded Gemini: 1.3 s with no search, 2.1 s with
# one). If it is still running this long after it started, the client is told a tool call is under
# way and plays that tool's pre-recorded line ("Let me look that up."), as for a model tool call.
# Simulated on 65 measured lookups: first sound 1.8 s -> 1.2 s and answer start 2.8 s -> 2.2 s for
# the questions that need a search; quick ones (median 0.8 s) finish before it would fire.
LOOKUP_ACK_S = 1.0
ACKED_LOOKUPS = frozenset({"web_answer"})


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
    ) -> None:
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

    async def acked_call(self, tool: str, args: dict[str, Any], on_tool: Any, home: bool) -> Any:
        """`call_tool`, telling the client after LOOKUP_ACK_S that a slow lookup is under way."""
        if home or on_tool is None or tool not in ACKED_LOOKUPS:
            return await self.call_tool(tool, args)

        async def announce() -> None:
            await asyncio.sleep(LOOKUP_ACK_S)
            await on_tool(tool, False)

        ack = asyncio.create_task(announce())
        try:
            return await self.call_tool(tool, args)
        finally:
            ack.cancel()

    async def run_routed(
        self,
        routed: Routed,
        started: float,
        on_text: Any,
        claim: Callable[[], bool] = lambda: True,
        on_tool: Any = None,
    ) -> dict[str, Any] | None:
        """Run the router's calls with the agent's own tools and say the result. `claim` is asked
        before the first home command and before speaking: False means Gemini already answered
        (nothing is run or said). None: Gemini should take the turn, because the claim failed or a
        lookup failed before anything changed (it retries with another topic, as it does without
        the router)."""
        spoken: list[str] = []
        claimed = False

        def take() -> bool:
            nonlocal claimed
            claimed = claimed or claim()
            return claimed

        for call in routed.calls:
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
                home = name in spec.HOME_INTENTS
                tool = "smart_home" if home else name
                args = (
                    {"intent": name, **({"slot": call.args[0]} if call.args else {})}
                    if home
                    else spec.to_tool_args(call)
                )
                if tool == "wiki_summary" and "web_answer" in self.tools:
                    # the router was trained on wiki_summary; the web answer replaces it (Wikipedia
                    # is its fallback), so the trained name keeps working without retraining
                    asked = routed.user.strip()  # the whole question, unless this turn did more
                    tool, args = (
                        "web_answer",
                        {"query": asked if len(routed.calls) == 1 and asked else args["topic"]},
                    )
                if home and not take():
                    return None
                result = await self.acked_call(tool, args, on_tool, home)
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

        async def local(claim: Callable[[], bool]) -> dict[str, Any] | None:
            routed = await router.route(self.history(messages), user, set(self.tools))
            info.update(router_s=routed.seconds, router_lines=routed.lines)
            if routed.reason is None:
                done = await self.run_routed(routed, started, on_text, claim, on_tool)
                if done is not None:
                    return done
                routed.reason = "lookup failed or Gemini first"
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
                    await on_say(f"Update on your earlier request: {text}")
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
        agent = Agent(llm(), llm_tools(tools))
        await agent.start()
        await agent.ask([{"role": "user", "content": "Say OK."}], reset=True)  # warm-up
    else:  # R4: two requests per turn, the first to answer wins (hedge.py)
        gate = HedgeGate()
        agent = HedgedAgent(
            [Agent(llm(), llm_tools(gated_tools(tools, gate, i))) for i in range(2)],
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
        router = LocalRouter(router_url, book, router_timeout)
        await router.warm_up()
    queue = NoticeQueue(notices) if notices else None
    server = AgentServer(agent, record, log, tools if fast or router else None, router, queue)
    try:
        async with serve(server.handle, "127.0.0.1", port):
            print(f"agent server on ws://127.0.0.1:{port}, log {log}", flush=True)
            await asyncio.Future()
    finally:
        await close_llm_sessions()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8781)
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
        "--notices",
        default=str(Path.home() / ".local/state/voice_agent/delegate/notices.jsonl"),
        help="where the delegate server writes the spoken notices of jobs that ended; '' for none",
    )
    args = parser.parse_args(argv)
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
            )
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
