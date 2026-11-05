"""The agent's tools: smart_home, plus the tools of its MCP servers, each call recorded.

MCP servers: docs (a sample: BM25 over the McCarthy corpus), the official time server, web_info
(weather and Wikipedia, in the root project) and the Pi's device server over the network. The
Pi's is optional: without a token, or when the Pi is unreachable, the agent starts without it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters
from mcp.client.session_group import StreamableHttpParameters
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.mcp_service import MCPClient

from voice_pipeline import question
from voice_pipeline.device_say import device_sentence, single_question
from voice_pipeline.tools.smart_home import SCHEMA, make_handler

REPO_ROOT = Path(__file__).resolve().parents[3]
ROOT_MODULE = ["uv", "run", "--group", "laptop", "python", "-m"]
DOCS_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.docs"]
WEB_INFO_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.web_info"]
TAVILY_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.tavily_search"]
# $DELEGATE_STATE_DIR (a second server for testing) keeps its jobs and notices apart from the
# running stack's; the MCP child does not inherit the environment, so it goes on the command line
DELEGATE_COMMAND = [
    *ROOT_MODULE,
    "voice_agent.mcp_servers.delegate",
    *(
        ["--state-dir", os.environ["DELEGATE_STATE_DIR"]]
        if os.environ.get("DELEGATE_STATE_DIR")
        else []
    ),
]
TIME_COMMAND = [sys.executable, "-m", "mcp_server_time", "--local-timezone", "Asia/Manila"]
PI_URL_FALLBACK = "http://169.254.230.219:8790/mcp"


def pi_url_from_env(env: Mapping[str, str] | None = None) -> str:
    """The Pi's device-tools URL: $PI_DEVICE_URL (scripts/stack/up.sh sets it from config/pi.env),
    else the address this was first written with."""
    return (os.environ if env is None else env).get("PI_DEVICE_URL") or PI_URL_FALLBACK


PI_URL = pi_url_from_env()
# Tools whose result is already a sentence to say: no second LLM request (R5; TurnTap decides).
SPOKEN_RESULT_TOOLS = {
    "weather",
    "wiki_summary",
    "web_answer",
    "get_volume",
    "set_volume",
    "open_app",
}
# a failure sentence is not an answer: the model gets it and recovers (another topic, its own
# knowledge), as before R5
FAILURE = re.compile(r"couldn't find|did not answer|not connected|can't say|error", re.IGNORECASE)
# failures that may clear on their own (a network blip), unlike "couldn't find": worth one retry
TRANSIENT = re.compile(r"did not answer|not connected|can't say|error", re.IGNORECASE)
NO_SECOND_REQUEST = FunctionCallResultProperties(run_llm=False)
# What a started delegate job is answered with: no second model request, so no invented duration
# and no raw JSON (R2). TurnTap puts "Started. " in front when the model's own `say` is missing.
DELEGATE_STARTED = "I'll tell you when it's done."


def job_started(result: Any) -> bool:
    """Whether a `delegate` result says the job was started and runs in the background."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return False
    return isinstance(result, dict) and result.get("status") == "started"


SAY = {
    "type": "string",
    "description": "One short sentence said to the user the moment this call starts, saying "
    "what you are doing (like 'Checking the weather.'); never the result.",
}


# With the model's own Google Search the lookup tools are redundant, and a model given both would
# call the slower one: the server keeps them (the router runs web_answer), the model does not
# see them
BUILTIN_SEARCH_REPLACES = frozenset({"web_answer", "wiki_summary"})


def with_builtin_search(tools: ToolsSchema) -> ToolsSchema:
    """`tools` for a model that searches the web itself: no web_answer or wiki_summary, and
    Gemini's built-in `google_search` next to the function tools (the request then also needs
    tool_config.include_server_side_tool_invocations, see agent.build_llm)."""
    kept = [t for t in tools.standard_tools if t.name not in BUILTIN_SEARCH_REPLACES]
    return ToolsSchema(
        standard_tools=kept, custom_tools={AdapterType.GEMINI: [{"google_search": {}}]}
    )


# Offered to the model only once a delegated job exists (--hide-idle-delegate, off by default):
# before that they only cost schema tokens on every request.
IDLE_DELEGATE_TOOLS = frozenset({"delegate_status", "delegate_cancel"})


def without_tools(tools: ToolsSchema, names: frozenset[str]) -> ToolsSchema:
    """`tools` minus the function tools called `names`."""
    kept = [t for t in tools.standard_tools if t.name not in names]
    return ToolsSchema(standard_tools=kept, custom_tools=tools.custom_tools)


def has_delegate_jobs(jobs_file: Path) -> bool:
    """Whether the delegate server's job table lists any job (an unreadable table counts as yes)."""
    try:
        return bool(json.loads(jobs_file.read_text())["jobs"])
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError):
        return True


# Web apps the delegate writes (apps/<name>/index.html), served by voice_agent.apps_server on
# loopback; open_app is the only way the agent starts a browser, and only for these URLs (T2, T4).
APPS_ROOT = Path(os.environ.get("APPS_ROOT") or "~/agent_sandbox/workspace/apps").expanduser()
APPS_PORT = int(os.environ.get("PORT_APPS") or 8795)
APP_NAME = re.compile(r"[a-z0-9_-]{1,40}")
# the user must ask, or agree to an offer ("yes", "open it"); the model alone never opens a page
OPEN_REQUEST = re.compile(
    r"\b(open|play|launch|start|run|show|yes|yeah|yep|sure|ok|okay|please|go ahead|do it)\b",
    re.IGNORECASE,
)
OPEN_COMMAND = os.environ.get("OPEN_APP_COMMAND") or "xdg-open"
OPEN_APP_DESCRIPTION = (
    "Open a web app (a game or demo the background assistant made) in the user's browser. Use it "
    "only when the user asks to open or play one, or says yes to your offer to open it. `name` is "
    "the app's folder name (letters, digits, - and _); leave it empty to hear which apps exist. "
    "If the app does not exist the reply says so: offer to make it with `delegate`."
)


def app_names(root: Path | None = None) -> list[str]:
    """The apps that have an index.html."""
    base = APPS_ROOT if root is None else root
    try:
        return sorted(
            d.name
            for d in base.iterdir()
            if APP_NAME.fullmatch(d.name) and not d.is_symlink() and (d / "index.html").is_file()
        )
    except OSError:
        return []


def open_app(
    name: str, asked: str, root: Path | None = None, port: int | None = None
) -> tuple[str, str | None]:
    """(the sentence to say, the URL opened or None). Nothing is opened unless `asked` is a
    request to open or play it and `name` is an existing app."""
    base, port = (APPS_ROOT if root is None else root), (APPS_PORT if port is None else port)
    have = app_names(base)
    name = name.strip().lower().replace(" ", "_")
    if not name:
        return (
            "I have " + ", ".join(n.replace("_", " ") for n in have) + "."
            if have
            else "I don't have any apps yet. Want me to make one?",
            None,
        )
    if not APP_NAME.fullmatch(name):
        return "I can't open that. App names use letters, digits, dashes and underscores.", None
    if name not in have:
        return (
            f"I don't have an app called {name.replace('_', ' ')}. Want me to make it?",
            None,
        )
    if not OPEN_REQUEST.search(asked):
        return "I open apps when you ask me to. Say open it and I will.", None
    url = f"http://127.0.0.1:{port}/{name}/"
    subprocess.Popen(  # noqa: S603 - fixed command, URL built from a checked name
        [OPEN_COMMAND, url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return f"Opening {name.replace('_', ' ')} in your browser.", url


def stdio_server(command: list[str], env: dict[str, str] | None = None) -> MCPClient:
    """MCP client for a server started over stdio from the repo root."""
    params = StdioServerParameters(
        command=command[0], args=command[1:], cwd=str(REPO_ROOT), env=env
    )
    return MCPClient(server_params=params)


def docs_server(command: list[str] = DOCS_COMMAND, env: dict[str, str] | None = None) -> MCPClient:
    """MCP client for the docs server (root project)."""
    return stdio_server(command, env)


def pi_device_server(url: str | None = PI_URL, token: str | None = None) -> MCPClient | None:
    """MCP client for the Pi's device server (token: LAN_SPEECH_TOKEN), or None without one."""
    token = os.environ.get("LAN_SPEECH_TOKEN") if token is None else token
    if not url or not token:
        return None
    params = StreamableHttpParameters(url=url, headers={"Authorization": f"Bearer {token}"})
    return MCPClient(server_params=params)


def standard_servers(pi_url: str | None = PI_URL) -> tuple[list[MCPClient], list[MCPClient]]:
    """(required, optional) servers: docs, time, web_info, tavily_search; delegate and the Pi's
    (if reachable) are optional."""
    pi = pi_device_server(pi_url)
    required = [
        docs_server(),
        stdio_server(TIME_COMMAND),
        stdio_server(WEB_INFO_COMMAND),
        stdio_server(TAVILY_COMMAND),
    ]
    optional = [stdio_server(DELEGATE_COMMAND)]
    return required, optional + ([pi] if pi is not None else [])


def _recorded(schema: FunctionSchema, record: list[dict[str, Any]]) -> FunctionSchema:
    inner = schema.handler
    assert inner is not None

    async def handler(params: FunctionCallParams) -> None:
        args = {k: v for k, v in params.arguments.items() if k != "say"}
        record.append({"tool": schema.name, "args": args})
        params.arguments = args  # the real tool never sees `say`
        if schema.name == "device_status" and params.tool_call_id != "router":
            # the router path words the status itself (agent_server.run_routed)
            deliver_status = params.result_callback

            async def status_callback(result: Any, *, properties: Any = None) -> None:
                said = (
                    device_sentence(result, question.current)
                    if single_question(question.current)
                    else None
                )
                if said is None:  # nothing usable, or more than a status was asked: the model
                    await deliver_status(result, properties=properties)
                else:
                    await deliver_status(said, properties=properties or NO_SECOND_REQUEST)

            params.result_callback = status_callback
        elif schema.name == "delegate":
            deliver_job = params.result_callback

            async def job_callback(result: Any, *, properties: Any = None) -> None:
                if job_started(result):
                    await deliver_job(DELEGATE_STARTED, properties=properties or NO_SECOND_REQUEST)
                else:
                    await deliver_job(result, properties=properties)

            params.result_callback = job_callback
        elif schema.name in SPOKEN_RESULT_TOOLS:
            deliver = params.result_callback

            async def result_callback(result: Any, *, properties: Any = None) -> None:
                failed = isinstance(result, str) and FAILURE.search(result) is not None
                # a failure, or a question that wants a decision made from the result ("should I
                # bring an umbrella"), goes back to the model instead of being spoken as it is
                ask_again = failed or question.needs_reasoning(question.current)
                await deliver(
                    result, properties=properties or (None if ask_again else NO_SECOND_REQUEST)
                )

            params.result_callback = result_callback
        await inner(params)

    return FunctionSchema(
        name=schema.name,
        description=schema.description,
        properties={**schema.properties, "say": SAY},
        required=schema.required,
        handler=handler,
    )


async def build_tools(
    record: list[dict[str, Any]],
    *,
    live: str | None = None,
    servers: Sequence[MCPClient] = (),
    optional: Sequence[MCPClient] = (),
) -> ToolsSchema:
    """smart_home (posting to the ME2 dashboard only when `live` is set) and the MCP tools.

    A server in `optional` that cannot be reached is left out with a warning; a server in
    `servers` that cannot be reached is an error."""
    home = FunctionSchema(
        name=SCHEMA.name,
        description=SCHEMA.description,
        properties={**SCHEMA.properties, "say": SAY},
        required=SCHEMA.required,
        handler=make_handler(base_url=live, record=record),
    )

    async def open_handler(params: FunctionCallParams) -> None:
        said, _url = open_app(str(params.arguments.get("name") or ""), question.current)
        await params.result_callback(said)

    tools: list[FunctionSchema] = [
        home,
        _recorded(
            FunctionSchema(
                name="open_app",
                description=OPEN_APP_DESCRIPTION,
                properties={
                    "name": {
                        "type": "string",
                        "description": "the app's folder name, e.g. flappy_bird",
                    },
                    "say": SAY,
                },
                required=["name"],
                handler=open_handler,
            ),
            record,
        ),
    ]
    for server in servers:
        tools += [_recorded(s, record) for s in (await server.tools()).standard_tools]
    for server in optional:
        try:
            schemas = (await server.tools()).standard_tools
        except Exception as exc:  # the Pi is off or unreachable: start without its tools
            print(f"MCP server left out ({type(exc).__name__}: {exc})", file=sys.stderr)
            continue
        tools += [_recorded(s, record) for s in schemas]
    return ToolsSchema(standard_tools=tools)


def runnable(
    prompts: list[dict[str, Any]], names: set[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """(prompts whose expected tools are all available, ids of the ones that are not)."""
    kept: list[dict[str, Any]] = []
    skipped: list[str] = []
    for prompt in prompts:
        needed = {e["tool"] for turn in prompt["expect"] for e in turn if "tool" in e}
        if needed <= names:
            kept.append(prompt)
        else:
            skipped.append(str(prompt["id"]))
    return kept, skipped
