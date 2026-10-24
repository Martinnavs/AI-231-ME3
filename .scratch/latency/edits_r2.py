"""Reference edits for R2 (fillers, the tool `say` sentence): (old, new) pairs, each old unique.

Root project: `src/doc_chat/__main__.py`, `src/doc_chat/agent_client.py` (new files
`src/doc_chat/fillers.py`, `config/fillers.json`, tests in skeleton/). Pipeline: `agent.py`,
`agent_tools.py`, `config/eval/system_agent.txt`, and `agent_server.py` as R3 left it.
"""

MAIN = [
    (
        "from doc_chat.dialogue import ChatHistory, Clarification, clarification_for, parse_yes_no\n",
        "from doc_chat.dialogue import ChatHistory, Clarification, clarification_for, parse_yes_no\n"
        "from doc_chat.fillers import Fillers, TurnFillers\n",
    ),
    (
        "    _prompt_count: int = 0\n",
        '''    _prompt_count: int = 0
    fillers: Fillers | None = None  # --fillers: pre-recorded lines while a slow answer waits
    _turn_fillers: TurnFillers | None = None
    _filler_push: Callable[[bytes], None] | None = None  # into the answer's player
    _filler_started: float | None = None  # perf_counter of the first filler audio
    _filler_lines: list[str] = field(default_factory=list)
    _loop: asyncio.AbstractEventLoop | None = None
''',
    ),
    (
        "    async def complete_turn(self, heard: HeardTurn) -> HeardTurn:\n",
        '''    def _play_filler(self, kind: str) -> None:
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
        if self._loop is not None and event.get("event") == "tool":
            self._loop.call_soon_threadsafe(self._tool_filler, str(event.get("name", "")))

    def _tool_filler(self, name: str) -> None:
        turn = self._turn_fillers
        if turn is not None and self._answer_started is None:
            kind = turn.tool(name)
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
''',
    ),
    (
        '''        self._player = player
        on_chunk = self._chunk(player)
''',
        '''        self._player = player
        self._filler_push = player.push if player is not None else None
        on_chunk = self._chunk(player)
''',
    ),
    (
        '''            "cue_s": self._cue_s,
        }
        if heard is not None:
            rec["pause_ms"] = (''',
        '''            "cue_s": self._cue_s,
            "fillers": list(self._filler_lines) or None,
            "filler_after_final_ms": (
                None
                if self._filler_started is None or heard is None
                else round((self._filler_started - heard.t_final) * 1000.0, 1)
            ),
        }
        if heard is not None:
            rec["pause_ms"] = (''',
    ),
    (
        "            spoken = \" \".join(self._spoken)\n            heard = await self.follow_up(heard, interruption)",
        "            spoken = \" \".join(self._filler_lines + self._spoken)\n            heard = await self.follow_up(heard, interruption)",
    ),
    (
        '''        self._answer_started = None
        self._spoken = []
        self._stream = None
        self._player = None
        t_final = heard.t_final
        work = asyncio.create_task(self.handle(idx, heard))
        watch = asyncio.create_task(self._watch_for_voice(heard.tail))
        try:
            await asyncio.wait({work, watch}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            work.cancel()
            watch.cancel()
            raise
        if not watch.done():  # the answer finished without interruption''',
        '''        self._answer_started = None
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
        if not watch.done():  # the answer finished without interruption''',
    ),
    (
        "            answering = self._answer_started is not None\n",
        "            # a filler is the assistant speaking too: its echo must not count as the user\n"
        "            answering = self._answer_started is not None or self._filler_started is not None\n",
    ),
    (
        "    llm = None\n    if args.llm == \"qwen\":\n",
        "    llm = None\n    agent_llm = None  # the agent client, for --fillers' tool events\n"
        "    if args.llm == \"qwen\":\n",
    ),
    (
        '''        llm = AgentChatModel(url=args.agent_url, timeout=args.llm_timeout)
''',
        '''        llm = AgentChatModel(url=args.agent_url, timeout=args.llm_timeout)
        agent_llm = llm
''',
    ),
    (
        '''            if args.ask_to_add:
                for line in ADD_PROMPTS:  # recorded once, so the prompt starts without waiting
                    pcm, *_ = await client.speak_text(line, voice=args.voice)
                    session.add_prompts.append((line, pcm))
''',
        '''            if args.ask_to_add:
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
''',
    ),
    (
        "    parser.add_argument(\n        \"--echo-text-guard\",",
        '''    parser.add_argument(
        "--fillers",
        action="store_true",
        help="say a short pre-recorded line when an answer is slow: a tool's line when it is "
        "called, a stall at 1.2 s, a notice at 2.5 s, an apology at 8 s (config/fillers.json)",
    )
    parser.add_argument(
        "--echo-text-guard",''',
    ),
]

AGENT_CLIENT = [
    (
        "  server -> {\"text\": str} ... then {\"done\": true}, or {\"error\": str}\n",
        "  server -> {\"text\": str} ... then {\"done\": true}, or {\"error\": str}; and\n"
        "            {\"event\": \"tool\", \"name\": str} when a tool is called (for a filler)\n",
    ),
    (
        "    url: str = DEFAULT_URL\n    timeout: float = 30.0\n",
        "    url: str = DEFAULT_URL\n    timeout: float = 30.0\n"
        "    on_event: Any = None  # called with every {\"event\": ...} message (worker thread)\n",
    ),
    (
        '''                elif event.get("done"):
                    return''',
        '''                elif "event" in event:
                    if self.on_event is not None:
                        self.on_event(event)
                elif event.get("done"):
                    return''',
    ),
]

AGENT = [
    (
        '''    def begin(self, on_text: OnText | None) -> None:
        self.done.clear()
        self.on_text = on_text''',
        '''    def begin(self, on_text: OnText | None, on_tool: OnText | None = None) -> None:
        self.done.clear()
        self.on_text = on_text
        self.on_tool = on_tool
        self.announced = False  # the first tool call of a turn is announced once''',
    ),
    (
        '''        elif isinstance(frame, FunctionCallsStartedFrame):
            self.calls_in_response = True
            if self.first_call is None:
                self.first_call = now''',
        '''        elif isinstance(frame, FunctionCallsStartedFrame):
            self.calls_in_response = True
            if self.first_call is None:
                self.first_call = now
            if not self.announced:
                self.announced = True
                await self._announce(frame)''',
    ),
    (
        '''class Agent:''',
        '''    async def _announce(self, frame: FunctionCallsStartedFrame) -> None:
        """Say the model's `say` sentence for the call, or tell the client which tool runs."""
        calls = list(frame.function_calls)
        say = next((str(c.arguments.get("say") or "").strip() for c in calls), "")
        if say and self.on_text is not None:
            self.text.append(say + " ")
            if self.first_text is None:
                self.first_text = time.perf_counter() - self.started
            await self.on_text(say + " ")
        elif calls and self.on_tool is not None:
            await self.on_tool(calls[0].function_name)


class Agent:''',
    ),
    (
        '''    async def ask(
        self, messages: list[Any], *, reset: bool, on_text: OnText | None = None
    ) -> dict[str, Any]:
        """One turn. reset=True replaces the conversation with `messages`, else appends them."""
        self.tap.begin(on_text)''',
        '''    async def ask(
        self,
        messages: list[Any],
        *,
        reset: bool,
        on_text: OnText | None = None,
        on_tool: OnText | None = None,
    ) -> dict[str, Any]:
        """One turn. reset=True replaces the conversation with `messages`, else appends them.
        `on_tool` gets the tool's name when a call starts and the model gave no `say`."""
        self.tap.begin(on_text, on_tool)''',
    ),
]

AGENT_TOOLS = [
    (
        '''PI_URL = "http://169.254.230.219:8790/mcp"
''',
        '''PI_URL = "http://169.254.230.219:8790/mcp"
SAY = {
    "type": "string",
    "description": "One short sentence said to the user the moment this call starts, saying "
    "what you are doing (like 'Checking the weather.'); never the result.",
}
''',
    ),
    (
        '''    async def handler(params: FunctionCallParams) -> None:
        record.append({"tool": schema.name, "args": dict(params.arguments)})
        await inner(params)

    return FunctionSchema(
        name=schema.name,
        description=schema.description,
        properties=schema.properties,''',
        '''    async def handler(params: FunctionCallParams) -> None:
        args = {k: v for k, v in params.arguments.items() if k != "say"}
        record.append({"tool": schema.name, "args": args})
        params.arguments = args  # the real tool never sees `say`
        await inner(params)

    return FunctionSchema(
        name=schema.name,
        description=schema.description,
        properties={**schema.properties, "say": SAY},''',
    ),
    (
        '''    home = FunctionSchema(
        name=SCHEMA.name,
        description=SCHEMA.description,
        properties=SCHEMA.properties,''',
        '''    home = FunctionSchema(
        name=SCHEMA.name,
        description=SCHEMA.description,
        properties={**SCHEMA.properties, "say": SAY},''',
    ),
]

AGENT_SERVER = [
    (
        '''        async def on_text(text: str) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps({"text": text}))
''',
        '''        async def on_text(text: str) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps({"text": text}))

        async def on_tool(name: str) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps({"event": "tool", "name": name}))
''',
    ),
    (
        "                turn = asyncio.create_task(self.agent.ask(messages, reset=True, on_text=on_text))",
        "                turn = asyncio.create_task(\n"
        "                    self.agent.ask(messages, reset=True, on_text=on_text, on_tool=on_tool)\n"
        "                )",
    ),
]

SYSTEM_PROMPT = [
    (
        "A turn can need more than one tool,",
        "When you call a tool, also fill its `say` argument with one short sentence that tells the "
        "user what you are doing (\"Checking the weather.\", \"Turning on the lights.\"); it is "
        "spoken the moment the call starts. Never put the result in it.\n\nA turn can need more "
        "than one tool,",
    ),
]

TEST_AGENT_SERVER = [
    (
        "    async def ask(self, messages: list[dict[str, Any]], *, reset: bool, on_text: Any) -> dict:",
        "    async def ask(\n"
        "        self, messages: list[dict[str, Any]], *, reset: bool, on_text: Any, on_tool: Any = None\n"
        "    ) -> dict:",
    ),
]

EDITS = {
    "src/doc_chat/__main__.py": MAIN,
    "src/doc_chat/agent_client.py": AGENT_CLIENT,
    "pipeline/src/voice_pipeline/agent.py": AGENT,
    "pipeline/src/voice_pipeline/agent_tools.py": AGENT_TOOLS,
    "pipeline/src/voice_pipeline/agent_server.py": AGENT_SERVER,
    "pipeline/config/eval/system_agent.txt": SYSTEM_PROMPT,
    "pipeline/tests/test_agent_server.py": TEST_AGENT_SERVER,
}
