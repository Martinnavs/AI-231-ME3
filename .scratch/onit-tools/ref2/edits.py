# (file, old, new): each old block must occur exactly once in the file as it is when applied.
AT = "pipeline/src/voice_pipeline/agent_tools.py"
AS = "pipeline/src/voice_pipeline/agent_server.py"
EDITS = [
    (
        AT,
        'TAVILY_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.tavily_search"]\n',
        'TAVILY_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.tavily_search"]\n'
        'DELEGATE_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.delegate"]\n',
    ),
    (
        AT,
        '    """(required, optional) servers: docs, time, web_info, tavily_search; the Pi\'s if reachable."""\n',
        '    """(required, optional) servers: docs, time, web_info, tavily_search; delegate and the Pi\'s\n'
        '    (if reachable) are optional."""\n',
    ),
    (
        AT,
        "    return required, [pi] if pi is not None else []\n",
        "    optional = [stdio_server(DELEGATE_COMMAND)]\n"
        "    return required, optional + ([pi] if pi is not None else [])\n",
    ),
    (
        AS,
        "from voice_pipeline.hedge import HedgedAgent, HedgeGate, gated_tools\n",
        "from voice_pipeline.hedge import HedgedAgent, HedgeGate, gated_tools\n"
        "from voice_pipeline.notices import NoticeQueue\n",
    ),
    (
        AS,
        "        router: LocalRouter | None = None,\n    ) -> None:\n        self.router = router\n",
        "        router: LocalRouter | None = None,\n        notices: NoticeQueue | None = None,\n"
        "    ) -> None:\n        self.router = router\n        self.notices = notices\n",
    ),
    (
        AS,
        '            if not cancelled:\n                reply = {"done": True} if result["status"] == "ok" else {"error": "timeout"}\n',
        "            said: list[str] = []\n"
        '            if not cancelled and result["status"] == "ok" and self.notices is not None:\n'
        "                said = self.notices.pop()\n"
        "                for text in said:\n"
        '                    await on_say(f"Update on your earlier request: {text}")\n'
        '            if not cancelled:\n                reply = {"done": True} if result["status"] == "ok" else {"error": "timeout"}\n',
    ),
    (
        AS,
        '                "cancelled": cancelled,\n                **result,\n',
        '                "cancelled": cancelled,\n                "notices": said,\n                **result,\n',
    ),
    (
        AS,
        "    router_hold: float = 0.0,\n) -> int:\n    record:",
        "    router_hold: float = 0.0,\n    notices: Path | None = None,\n) -> int:\n    record:",
    ),
    (
        AS,
        "    server = AgentServer(agent, record, log, tools if fast or router else None, router)\n",
        "    queue = NoticeQueue(notices) if notices else None\n"
        "    server = AgentServer(agent, record, log, tools if fast or router else None, router, queue)\n",
    ),
    (
        AS,
        "    args = parser.parse_args(argv)\n    try:\n        return asyncio.run(\n",
        "    parser.add_argument(\n"
        '        "--notices",\n'
        "        default=str(Path.home() / \".local/state/voice_agent/delegate/notices.jsonl\"),\n"
        '        help="where the delegate server writes the spoken notices of jobs that ended; \'\' for none",\n'
        "    )\n"
        "    args = parser.parse_args(argv)\n    try:\n        return asyncio.run(\n",
    ),
    (
        AS,
        "                args.router_hold,\n            )\n        )\n",
        "                args.router_hold,\n                Path(args.notices) if args.notices else None,\n            )\n        )\n",
    ),
    (
        "pipeline/config/eval/system_agent.txt",
        "Case F, anything else. Answer briefly without tools.\n",
        "Case F, a big task: building a website or app, solving a hard multi-step math problem with code, "
        "auditing this machine, or analysing files or data. Call delegate with `task` written as a complete "
        "request with every detail it needs (it cannot ask you questions); it works in the background in a "
        "sandbox. If the result comes back, say it. If it only says the task started, tell the user it will "
        "take a few minutes and carry on; the result is announced later. delegate_status says how a task is "
        "going and delegate_cancel stops it. Never use delegate for simple questions, home commands, the "
        "weather or the time.\n\nCase G, anything else. Answer briefly without tools.\n",
    ),
    (
        "pipeline/tests/test_agent_tools.py",
        '    assert len(required) == 4 and optional == []  # no token: no Pi\n'
        '    monkeypatch.setenv("LAN_SPEECH_TOKEN", "secret")\n'
        "    assert len(standard_servers()[1]) == 1\n"
        "    assert len(standard_servers(None)[1]) == 0\n",
        '    assert len(required) == 4 and len(optional) == 1  # no token: the delegate server, no Pi\n'
        '    monkeypatch.setenv("LAN_SPEECH_TOKEN", "secret")\n'
        "    assert len(standard_servers()[1]) == 2\n"
        "    assert len(standard_servers(None)[1]) == 1\n",
    ),
]
