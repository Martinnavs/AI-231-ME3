"""Reference edits for ticket 03 (agent wiring): (old, new) pairs, each old block unique.

Applied by `.scratch/echo-fix/apply_edits.py edits_03.py <root>`. Whole-file replacements
(agent_tools.py, system_agent.txt) and the new test file are in skeleton/pipeline/.
"""

SMART_HOME = [
    (
        '"""One LLM tool for the 19 ME2 home commands, sent to the ME2 dashboard\'s POST /api/command.',
        '"""One LLM tool for 17 ME2 home commands, sent to the ME2 dashboard\'s POST /api/command.\n\n'
        "Time and weather are not home commands any more: the agent answers them with the real\n"
        "time and weather MCP tools (owner, 2026-10-24).",
    ),
    (
        '''    "TEMPERATURE",
    "WEATHER",
    "TIME",
    "TIMER",''',
        '''    "TEMPERATURE",
    "TIMER",''',
    ),
    (
        '"timer, alarm, reminders, phone call, message, time, weather. One call per command. "',
        '"timer, alarm, reminders, phone call, message. One call per command. "',
    ),
]

AGENT_SERVER = [
    (
        "    uv run python -m voice_pipeline.agent_server [--port 8781] [--live URL]",
        "    LAN_SPEECH_TOKEN=... uv run python -m voice_pipeline.agent_server [--port 8781]\n"
        "        [--live URL] [--pi-url URL]",
    ),
    (
        "from voice_pipeline.agent_tools import build_tools, docs_server",
        "from voice_pipeline.agent_tools import PI_URL, build_tools, standard_servers",
    ),
    (
        '''async def run(port: int, live: str | None, system_file: Path) -> int:
    record: list[dict[str, Any]] = []
    tools = await build_tools(record, live=live, docs=docs_server())''',
        '''async def run(port: int, live: str | None, system_file: Path, pi_url: str | None) -> int:
    record: list[dict[str, Any]] = []
    servers, optional = standard_servers(pi_url)
    tools = await build_tools(record, live=live, servers=servers, optional=optional)''',
    ),
    (
        '''    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.port, args.live, args.system))''',
        '''    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.port, args.live, args.system, args.pi_url or None))''',
    ),
]

DRYRUN = [
    (
        '"""Stress dry run of the agent (native Gemini, smart_home + docs MCP) over a fixed prompt set.',
        '"""Stress dry run of the agent (native Gemini, smart_home + MCP tools) over a fixed prompt set.',
    ),
    (
        "    uv run python -m voice_pipeline.dryrun [--limit N] [--live URL] [--run-id ID]",
        "    LAN_SPEECH_TOKEN=... uv run python -m voice_pipeline.dryrun [--limit N] [--live URL]\n"
        "        [--run-id ID] [--pi-url URL]\n"
        "\n"
        "Prompts that expect a tool the agent does not have (the Pi's, when it is off) are skipped\n"
        "and listed.",
    ),
    (
        "from voice_pipeline.agent_tools import build_tools, docs_server",
        "from voice_pipeline.agent_tools import PI_URL, build_tools, runnable, standard_servers",
    ),
    (
        '''async def run(limit: int | None, live: str | None, run_id: str, system_file: Path) -> int:
    lines = (ROOT / "config/eval/agent_prompts.jsonl").read_text().splitlines()
    prompts = [json.loads(line) for line in lines if line.strip()][:limit]
    out = ROOT / "out/agent_dryrun" / run_id
    out.mkdir(parents=True, exist_ok=True)
    record: list[dict[str, Any]] = []
    agent = Agent(
        build_llm(system_file.read_text()), await build_tools(record, live=live, docs=docs_server())
    )''',
        '''async def run(
    limit: int | None, live: str | None, run_id: str, system_file: Path, pi_url: str | None
) -> int:
    lines = (ROOT / "config/eval/agent_prompts.jsonl").read_text().splitlines()
    prompts = [json.loads(line) for line in lines if line.strip()][:limit]
    out = ROOT / "out/agent_dryrun" / run_id
    out.mkdir(parents=True, exist_ok=True)
    record: list[dict[str, Any]] = []
    servers, optional = standard_servers(pi_url)
    tools = await build_tools(record, live=live, servers=servers, optional=optional)
    prompts, skipped = runnable(prompts, {t.name for t in tools.standard_tools})
    if skipped:
        print(f"skipped (tool not available): {', '.join(skipped)}")
    agent = Agent(build_llm(system_file.read_text()), tools)''',
    ),
    (
        '''    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.limit, args.live, args.run_id, args.system))''',
        '''    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.limit, args.live, args.run_id, args.system, args.pi_url or None))''',
    ),
]

NEW_PROMPTS = "\n".join(
    [
        '{"id": "t01", "kind": "time", "turns": ["What time is it in London?"], "expect": [[{"tool": "get_current_time"}]]}',
        '{"id": "t02", "kind": "time", "turns": ["What\'s today\'s date?"], "expect": [[{"tool": "get_current_time"}]]}',
        '{"id": "t03", "kind": "time", "turns": ["If it\'s 3 PM here, what time is it in New York?"], "expect": [[{"tool": "convert_time"}]]}',
        '{"id": "w01", "kind": "weather", "turns": ["Will it rain tomorrow?"], "expect": [[{"tool": "weather"}]]}',
        '{"id": "w02", "kind": "weather", "turns": ["What\'s the weather like in Baguio?"], "expect": [[{"tool": "weather"}]]}',
        '{"id": "w03", "kind": "weather", "turns": ["How hot will it get today?"], "expect": [[{"tool": "weather"}]]}',
        '{"id": "k01", "kind": "knowledge", "turns": ["Who is Lionel Messi?"], "expect": [[{"tool": "wiki_summary"}]]}',
        '{"id": "k02", "kind": "knowledge", "turns": ["Tell me about the Eiffel Tower."], "expect": [[{"tool": "wiki_summary"}]]}',
        '{"id": "k03", "kind": "knowledge", "turns": ["What is a Raspberry Pi?"], "expect": [[{"tool": "wiki_summary"}]]}',
        '{"id": "v01", "kind": "device", "turns": ["Set the speaker volume to 40 percent."], "expect": [[{"tool": "set_volume"}]]}',
        '{"id": "v02", "kind": "device", "turns": ["How loud is the speaker right now?"], "expect": [[{"tool": "get_volume"}]]}',
        '{"id": "v03", "kind": "device", "turns": ["Put the speaker at 20 percent."], "expect": [[{"tool": "set_volume"}]]}',
        '{"id": "s01", "kind": "device", "turns": ["How hot is the Pi running?"], "expect": [[{"tool": "device_status"}]]}',
        '{"id": "s02", "kind": "device", "turns": ["Is the speaker connected?"], "expect": [[{"tool": "device_status"}]]}',
        '{"id": "m01", "kind": "mixed", "turns": ["Turn on the lights and tell me the weather."], "expect": [[{"intent": "LIGHT_ON", "slot": null}, {"tool": "weather"}]]}',
    ]
)

PROMPTS = [
    (
        '{"id": "c12", "kind": "canonical", "turns": ["What\'s the weather?"], "expect": [[{"intent": "WEATHER", "slot": null}]]}',
        '{"id": "c12", "kind": "canonical", "turns": ["What\'s the weather?"], "expect": [[{"tool": "weather"}]]}',
    ),
    (
        '{"id": "c13", "kind": "canonical", "turns": ["What time is it?"], "expect": [[{"intent": "TIME", "slot": null}]]}',
        '{"id": "c13", "kind": "canonical", "turns": ["What time is it?"], "expect": [[{"tool": "get_current_time"}]]}',
    ),
    (
        '{"id": "p05", "kind": "paraphrase", "turns": ["Do I need an umbrella today?"], "expect": [[{"intent": "WEATHER", "slot": null}]]}',
        '{"id": "p05", "kind": "paraphrase", "turns": ["Do I need an umbrella today?"], "expect": [[{"tool": "weather"}]]}',
    ),
    (
        '{"id": "p06", "kind": "paraphrase", "turns": ["Got the time?"], "expect": [[{"intent": "TIME", "slot": null}]]}',
        '{"id": "p06", "kind": "paraphrase", "turns": ["Got the time?"], "expect": [[{"tool": "get_current_time"}]]}',
    ),
]

EDITS = {
    "pipeline/src/voice_pipeline/tools/smart_home.py": SMART_HOME,
    "pipeline/src/voice_pipeline/agent_server.py": AGENT_SERVER,
    "pipeline/src/voice_pipeline/dryrun.py": DRYRUN,
    "pipeline/config/eval/agent_prompts.jsonl": PROMPTS,
}
APPEND = {"pipeline/config/eval/agent_prompts.jsonl": NEW_PROMPTS}
