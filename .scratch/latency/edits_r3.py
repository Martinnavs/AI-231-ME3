"""Reference edits for R3 (fast path in the agent server): (old, new) pairs, each old unique.

New files (fast_path.py and its tests) are in skeleton/pipeline/.
"""

AGENT_SERVER = [
    (
        "import time\nfrom pathlib import Path\nfrom typing import Any\n",
        "import time\nfrom pathlib import Path\nfrom types import SimpleNamespace\nfrom typing import Any\n",
    ),
    (
        "from voice_pipeline.agent import Agent, build_llm\n",
        "from voice_pipeline import fast_path\n"
        "from voice_pipeline.agent import Agent, build_llm\n",
    ),
    (
        '''    def __init__(self, agent: Any, record: list[dict[str, Any]], log: Path) -> None:
        self.agent = agent
        self.record = record
        self.log = log
        self.lock = asyncio.Lock()''',
        '''    def __init__(
        self, agent: Any, record: list[dict[str, Any]], log: Path, tools: Any = None
    ) -> None:
        self.agent = agent
        self.record = record
        self.log = log
        self.lock = asyncio.Lock()
        # with the agent's tools, common home commands skip the LLM (fast_path.py)
        self.tools = {t.name: t for t in tools.standard_tools} if tools is not None else {}

    async def fast(self, found: fast_path.Match, on_text: Any) -> dict[str, Any]:
        """Run the tool the LLM would have called, then say the fixed confirmation."""
        started = time.perf_counter()
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
                "end_s": elapsed, "fast_path": True}  # fmt: skip''',
    ),
    (
        '''            before = len(self.record)
            turn = asyncio.create_task(self.agent.ask(messages, reset=True, on_text=on_text))''',
        '''            before = len(self.record)
            last = messages[-1] if messages else {}
            found = fast_path.match(str(last.get("content", ""))) if last.get("role") == "user" else None
            if found is not None and found.tool in self.tools:
                turn = asyncio.create_task(self.fast(found, on_text))
            else:
                turn = asyncio.create_task(self.agent.ask(messages, reset=True, on_text=on_text))''',
    ),
    (
        "    server = AgentServer(agent, record, log)\n",
        "    server = AgentServer(agent, record, log, tools if fast else None)\n",
    ),
    (
        "async def run(port: int, live: str | None, system_file: Path, pi_url: str | None) -> int:",
        "async def run(\n    port: int, live: str | None, system_file: Path, pi_url: str | None, fast: bool = True\n) -> int:",
    ),
    (
        '''    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.port, args.live, args.system, args.pi_url or None))''',
        '''    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    parser.add_argument(
        "--fast-path",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="answer common home commands without the LLM (fast_path.py)",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(
            run(args.port, args.live, args.system, args.pi_url or None, args.fast_path)
        )''',
    ),
]

TEST_AGENT_SERVER_APPEND = '''

class FakeTool:
    """A tool handler like the agent's: records the call and returns a result."""

    def __init__(self, name: str, record: list[dict[str, Any]], result: Any) -> None:
        self.name = name
        self.record = record
        self.result = result

    async def handler(self, params: Any) -> None:
        self.record.append({"tool": self.name, "args": dict(params.arguments)})
        await params.result_callback(self.result)


async def _serve_fast(
    agent: FakeAgent, record: list[dict[str, Any]], log: Path, result: Any
) -> tuple[Any, str]:
    tools = type("Tools", (), {})()
    tools.standard_tools = [FakeTool("smart_home", record, result)]
    server = AgentServer(agent, record, log, tools)
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    port = next(iter(ws_server.sockets)).getsockname()[1]
    return ws_server, f"ws://127.0.0.1:{port}"


async def test_a_common_command_skips_the_llm(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = FakeAgent(record)
    ws_server, url = await _serve_fast(agent, record, tmp_path / "log.jsonl", {"ok": True})
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": "Set a timer for ten minutes."}]}))
        events = [json.loads(await ws.recv()) for _ in range(2)]
    ws_server.close()
    assert events == [{"text": "Timer set for 10 minutes."}, {"done": True}]
    assert agent.asked == []  # the LLM was not called
    row = json.loads((tmp_path / "log.jsonl").read_text())
    assert row["fast_path"] is True
    assert row["calls"] == [{"tool": "smart_home", "args": {"intent": "TIMER", "slot": "10 minutes"}}]


async def test_a_failed_command_says_why(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    failed = {"ok": False, "message": "home dashboard unreachable"}
    ws_server, url = await _serve_fast(FakeAgent(record), record, tmp_path / "log.jsonl", failed)
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": "Lights off."}]}))
        first = json.loads(await ws.recv())
    ws_server.close()
    assert first == {"text": "Sorry, that didn't work: home dashboard unreachable."}


async def test_anything_else_still_goes_to_the_llm(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = FakeAgent(record)
    ws_server, url = await _serve_fast(agent, record, tmp_path / "log.jsonl", {"ok": True})
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": "Turn on the lights and play music."}]}))
        await ws.recv()
    ws_server.close()
    assert agent.asked == [[{"role": "user", "content": "Turn on the lights and play music."}]]
'''

EDITS = {"pipeline/src/voice_pipeline/agent_server.py": AGENT_SERVER}
APPEND = {"pipeline/tests/test_agent_server.py": TEST_AGENT_SERVER_APPEND}
