"""The agent's tools: smart_home, plus the tools of its MCP servers, each call recorded.

MCP servers: docs (a sample: BM25 over the McCarthy corpus), the official time server, web_info
(weather and Wikipedia, in the root project) and the Pi's device server over the network. The
Pi's is optional: without a token, or when the Pi is unreachable, the agent starts without it.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp import StdioServerParameters
from mcp.client.session_group import StreamableHttpParameters
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.mcp_service import MCPClient

from voice_pipeline import question
from voice_pipeline.chain_state import ChainState, is_chain_tool
from voice_pipeline.device_say import device_sentence, single_question
from voice_pipeline.tools.smart_home import SCHEMA, make_handler

REPO_ROOT = Path(__file__).resolve().parents[3]
# On the laptop the MCP servers run through uv; on the Pi (no uv) $MCP_PYTHON is the venv's python.
MCP_PYTHON = os.environ.get("MCP_PYTHON")
ROOT_MODULE = (
    [MCP_PYTHON, "-m"] if MCP_PYTHON else ["uv", "run", "--group", "laptop", "python", "-m"]
)
DOCS_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.docs"]
WEB_INFO_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.web_info"]
TAVILY_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.tavily_search"]
# $DELEGATE_STATE_DIR (a second server for testing) keeps its jobs and notices apart from the
# running stack's; the MCP child does not inherit the environment, so it goes on the command line
DELEGATE_STATE_DIR: str | None = os.environ.get("DELEGATE_STATE_DIR") or None
DELEGATE_CONFIG: str | None = os.environ.get("DELEGATE_CONFIG") or None
MUSIC_RESOLVER_URL: str | None = os.environ.get("MUSIC_RESOLVER_URL") or None
APPS_HOST: str | None = os.environ.get("APPS_PUBLIC_HOST") or None


def delegate_command() -> list[str]:
    cmd = [*ROOT_MODULE, "voice_agent.mcp_servers.delegate"]
    if DELEGATE_STATE_DIR:
        cmd += ["--state-dir", DELEGATE_STATE_DIR]
    if DELEGATE_CONFIG:
        cmd += ["--config", DELEGATE_CONFIG]
    return cmd


def music_command() -> list[str] | None:
    """The music MCP server (Pi side), or None without a resolver address."""
    if not MUSIC_RESOLVER_URL:
        return None
    return [*ROOT_MODULE, "voice_agent.mcp_servers.music", "--resolver-url", MUSIC_RESOLVER_URL]


def configure(
    *,
    apps_root: str | None = None,
    apps_host: str | None = None,
    delegate_state_dir: str | None = None,
    delegate_config: str | None = None,
) -> None:
    """Endpoints given as flags (agent_server --apps-root ...) override the environment."""
    global APPS_ROOT, APPS_HOST, DELEGATE_STATE_DIR, DELEGATE_CONFIG
    if apps_root:
        APPS_ROOT = Path(apps_root).expanduser()
    if apps_host:
        APPS_HOST = apps_host
    if delegate_state_dir:
        DELEGATE_STATE_DIR = delegate_state_dir
    if delegate_config:
        DELEGATE_CONFIG = delegate_config


DELEGATE_COMMAND = delegate_command()
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
# a delegate task that deletes or overwrites files needs the user's yes first (a misheard fragment
# once deleted every app in the workspace)
DESTRUCTIVE = re.compile(r"\b(delete|remove|erase|wipe|clear out|rm|empty|overwrite|reset)\b", re.I)
CONFIRMED = re.compile(
    r"\b(yes|yeah|yep|yup|sure|go ahead|do it|confirm(ed)?|please do|oo|sige)\b", re.I
)
ASK_DESTRUCTIVE = "That would delete or overwrite files in the shared folder. Should I go ahead?"
DELEGATE_GRACE_S = 1.5
_pending_destructive: str | None = None


def _job_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    return result if isinstance(result, dict) else None


def job_started(result: Any) -> bool:
    """Whether a `delegate` result says the job was started and runs in the background."""
    row = _job_row(result)
    return row is not None and row.get("status") == "started"


def started_job_id(result: Any) -> str:
    row = _job_row(result)
    return str(row.get("job") or "") if row and row.get("status") == "started" else ""


# The chain being worked on (research, then build); set by build_tools(chain=...). None: no chain.
CHAIN: ChainState | None = None
CHAIN_UPDATE_DESCRIPTION = (
    "Record where a multi-step task stands so the next turn knows. phase is one of "
    "awaiting_name, researching, awaiting_fact_approval, building, idle; subject is who or what "
    "it is about; facts is a few short verified facts, separated by ' | ' (while researching they "
    "add up). Returns ok."
)


def chain_update_schema(chain: ChainState, record: list[dict[str, Any]]) -> FunctionSchema:
    async def handler(params: FunctionCallParams) -> None:
        args = params.arguments
        record.append({"tool": "chain_update", "args": dict(args)})
        fields: dict[str, Any] = {}
        if args.get("subject"):
            fields["subject"] = str(args["subject"])[:80]
        if args.get("facts"):
            fields["facts"] = re.split(r"\s*(?:\||\n)\s*", str(args["facts"]))
        try:
            chain.update(str(args.get("phase") or ""), **fields)
        except ValueError as exc:
            await params.result_callback(f"error: {exc}")
            return
        await params.result_callback("ok")

    return FunctionSchema(
        name="chain_update",
        description=CHAIN_UPDATE_DESCRIPTION,
        properties={
            "phase": {
                "type": "string",
                "description": "awaiting_name, researching, awaiting_fact_approval, building, idle",
            },
            "subject": {"type": "string", "description": "who or what the task is about"},
            "facts": {"type": "string", "description": "short facts separated by ' | '"},
        },
        required=["phase"],
        handler=handler,
    )


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
OPEN_VERB = re.compile(r"\b(open|play|launch|start|run|show)\b", re.IGNORECASE)
# "turn it on" / "turn on" is a switch, never a request to open a page
TURN_SWITCH = re.compile(r"\bturn\s+(?:\w+\s+)?(?:on|off|up|down)\b", re.IGNORECASE)
CONSENT_WORDS = frozenset({"go", "do"})


def asks_to_open(asked: str) -> bool:
    """An open verb in the user's words, or a short confirmation made only of confirmation words
    ("yes", "yes please", "go ahead", "do it"). "please" or "yes" inside a longer sentence is not
    consent."""
    from voice_pipeline.call_policy import CONFIRM_ANCHORS, CONFIRM_MAX_WORDS, CONFIRM_WORDS

    if OPEN_VERB.search(TURN_SWITCH.sub(" ", asked)):
        return True
    words = re.findall(r"[a-z']+", asked.lower())
    return (
        0 < len(words) <= CONFIRM_MAX_WORDS
        and all(w in CONFIRM_WORDS for w in words)
        and any(w in CONFIRM_ANCHORS or w in CONSENT_WORDS for w in words)
        and "no" not in words
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


APP_NOISE = frozenset({"clone", "game", "app", "the", "a", "an", "version", "my", "web", "of"})


GENERIC_APP_WORDS = frozenset({"it", "that", "this", "one", "that one", "something", "anything"})


def app_key(name: str) -> str:
    """A name for comparing: lower case, words split on spaces, - and _, noise words dropped,
    plural s removed ("Angry Birds clone" and angry_birds both become "angry bird")."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in APP_NOISE]
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def closest_app(name: str, have: list[str]) -> str | None:
    """The existing app `name` means, or None: same key, else the best spelling match >= 0.8."""
    if name in have:
        return name
    key = app_key(name)
    if not key:
        return None
    for app in have:
        if app_key(app) == key:
            return app
    scored = sorted(
        ((difflib.SequenceMatcher(None, key, app_key(app)).ratio(), app) for app in have),
        reverse=True,
    )
    return scored[0][1] if scored and scored[0][0] >= 0.8 else None


# One async callable per turn, installed by agent_server.handle: sends an event to the client on the
# turn's websocket (the way notices are pushed). Tools running on the Pi use it for things that
# must happen on the laptop (open a page in its browser, play music).
EventSink = Callable[[dict[str, Any]], Awaitable[None]]
EVENT_SINK: EventSink | None = None


def set_event_sink(sink: EventSink | None) -> None:
    global EVENT_SINK
    EVENT_SINK = sink


async def emit_event(event: dict[str, Any]) -> bool:
    """Send `event` to the client of the turn in progress; False when there is none."""
    if EVENT_SINK is None:
        return False
    try:
        await EVENT_SINK(event)
    except Exception:
        return False
    return True


def open_app(
    name: str,
    asked: str,
    root: Path | None = None,
    port: int | None = None,
    host: str | None = None,
) -> tuple[str, str | None]:
    """(the sentence to say, the URL to open or None). Nothing is opened unless `asked` is a
    request to open or play it and `name` is an existing app. With `host` (HARNESS=pi, the apps
    server is on the Pi) nothing is started here: the URL is for the client's browser."""
    base, port = (APPS_ROOT if root is None else root), (APPS_PORT if port is None else port)
    have = app_names(base)
    name = name.strip()
    if not name:
        return (
            "I have " + ", ".join(n.replace("_", " ") for n in have) + ". Which one do you want?"
            if have
            else "I don't have any apps yet. Want me to make one?",
            None,
        )
    spoken = name.replace("_", " ").replace("-", " ")
    if not re.fullmatch(r"[A-Za-z0-9 _-]{1,60}", name):
        return (
            "I can't open that. App names use letters, digits, spaces, dashes and underscores.",
            None,
        )
    found = closest_app(name, have)
    if found is None and (not app_key(name) or name.lower() in GENERIC_APP_WORDS):
        # "the game", "the clone", "it": nothing was named, so no offer to make an app by that name
        if not have:
            return "I don't have any apps yet. Want me to make one?", None
        listed = ", ".join(n.replace("_", " ") for n in have)
        return f"I have {listed}. Which one do you want?", None
    if found is None:
        return (
            f"I don't have an app called {spoken}."
            + (" I have " + ", ".join(n.replace("_", " ") for n in have) + "." if have else "")
            + " Want me to make it?",
            None,
        )
    name = found
    if not asks_to_open(asked):
        return "I open apps when you ask me to. Say open it and I will.", None
    url = f"http://{host or '127.0.0.1'}:{port}/{name}/"
    if host:
        return f"Opening {name.replace('_', ' ')} in your browser.", url
    subprocess.Popen(  # noqa: S603 - fixed command, URL built from a checked name
        [OPEN_COMMAND, url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return f"Opening {name.replace('_', ' ')} in your browser.", url


# The Pi's music MCP server has one tool; the model does not see it (it keeps using smart_home),
# the home commands PLAY_MUSIC, PAUSE, STOP, NEXT, VOLUME_UP and VOLUME_DOWN reach it.
MUSIC_TOOL = "music"
MODEL_HIDDEN = frozenset({MUSIC_TOOL})
MUSIC_INTENTS = {
    "PLAY_MUSIC": "play",
    "PAUSE": "pause",
    "STOP": "stop",
    "NEXT": "next",
    "VOLUME_UP": "volume_up",
    "VOLUME_DOWN": "volume_down",
}


async def call_music(music: FunctionSchema, action: str, query: str) -> dict[str, Any] | None:
    """The music tool's answer ({ok, sentence, event?, inactive?}), or None if it did not answer."""
    got: list[Any] = []

    async def done(result: Any, *args: Any, **kwargs: Any) -> None:
        got.append(result)

    params = SimpleNamespace(
        function_name=MUSIC_TOOL,
        tool_call_id="music",
        arguments={"action": action, "query": query},
        result_callback=done,
    )
    assert music.handler is not None
    await music.handler(params)  # type: ignore[arg-type]
    if not got:
        return None
    result = got[0]
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    return result if isinstance(result, dict) else None


def with_music(
    home: FunctionSchema, music: FunctionSchema, record: list[dict[str, Any]]
) -> FunctionSchema:
    """smart_home whose music intents go to the music tool while music is active (PLAY_MUSIC
    always); with nothing playing PAUSE, STOP, NEXT and the volume keep the old behaviour. The
    tool's event (play this stream) goes to the client; its sentence is the whole reply."""
    inner = home.handler
    assert inner is not None

    async def handler(params: FunctionCallParams) -> None:
        intent = str(params.arguments.get("intent", ""))
        action = MUSIC_INTENTS.get(intent)
        if action is None:
            await inner(params)
            return
        slot = params.arguments.get("slot")
        answer = await call_music(music, action, str(slot or "") if intent == "PLAY_MUSIC" else "")
        if answer is None or answer.get("inactive"):
            await inner(params)
            return
        record.append({"intent": intent, "slot": slot})
        sentence = str(answer.get("sentence") or "Done.")
        event = answer.get("event")
        if isinstance(event, dict) and not await emit_event(event):
            sentence = "I can't play audio right now."
        await params.result_callback(sentence, properties=NO_SECOND_REQUEST)

    return FunctionSchema(
        name=home.name,
        description=home.description,
        properties=home.properties,
        required=home.required,
        handler=handler,
    )


def stdio_server(command: list[str], env: dict[str, str] | None = None) -> MCPClient:
    """MCP client for a server started over stdio from the repo root. With MCP_PYTHON (the Pi:
    nothing is installed) the child gets the repo's src on its path."""
    if MCP_PYTHON:
        env = {"PYTHONPATH": str(REPO_ROOT / "src"), **(env or {})}
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


def local_device_server() -> MCPClient:
    """The device tools as a stdio child (HARNESS=pi: the agent runs on the Pi itself)."""
    # wpctl needs the user PipeWire socket; a stdio child gets a bare env
    audio = {
        k: os.environ[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if k in os.environ
    }
    return stdio_server(
        [MCP_PYTHON or sys.executable, str(REPO_ROOT / "scripts/pi_device_mcp.py"), "--stdio"],
        audio or None,
    )


def standard_servers(pi_url: str | None = PI_URL) -> tuple[list[MCPClient], list[MCPClient]]:
    """(required, optional) servers: docs, time, web_info, tavily_search; delegate and the Pi's
    (if reachable) are optional."""
    pi = local_device_server() if pi_url == "local" else pi_device_server(pi_url)
    required = [
        docs_server(),
        stdio_server(TIME_COMMAND),
        stdio_server(WEB_INFO_COMMAND),
        stdio_server(TAVILY_COMMAND),
    ]
    optional = [stdio_server(delegate_command())]
    if (music := music_command()) is not None:
        optional.append(stdio_server(music))
    return required, optional + ([pi] if pi is not None else [])


NOT_IN_DOCUMENTS = "The documents have nothing about the app or game you made."


def _about_own_work(user: str) -> bool:
    from voice_pipeline.call_policy import about_own_work

    return about_own_work(user)


def _recorded(schema: FunctionSchema, record: list[dict[str, Any]]) -> FunctionSchema:
    inner = schema.handler
    assert inner is not None

    async def handler(params: FunctionCallParams) -> None:
        if schema.name == "search_docs" and _about_own_work(question.current):
            await params.result_callback(NOT_IN_DOCUMENTS)
            return
        args = {k: v for k, v in params.arguments.items() if k != "say"}
        record.append({"tool": schema.name, "args": args})
        if CHAIN is not None and CHAIN.active() and not is_chain_tool(schema.name):
            CHAIN.touch_unrelated()
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
            task = str(args.get("task", ""))
            global _pending_destructive
            if DESTRUCTIVE.search(task):
                if _pending_destructive is None or not CONFIRMED.search(question.current or ""):
                    _pending_destructive = task
                    await deliver_job(ASK_DESTRUCTIVE, properties=NO_SECOND_REQUEST)
                    return
                _pending_destructive = None
            # a turn that is cut off and resumed is cancelled during this wait, before a job starts
            await asyncio.sleep(DELEGATE_GRACE_S)

            async def job_callback(result: Any, *, properties: Any = None) -> None:
                if job_started(result):
                    if CHAIN is not None:
                        CHAIN.update("building", job_id=started_job_id(result))
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
    chain: ChainState | None = None,
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
        said, url = open_app(
            str(params.arguments.get("name") or ""), question.current, host=APPS_HOST
        )
        if (
            url is not None
            and APPS_HOST
            and not await emit_event({"event": "open_url", "url": url})
        ):
            said = "I can't open a page from here right now."
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
        tools += [s if s.name == MUSIC_TOOL else _recorded(s, record) for s in schemas]
    global CHAIN
    CHAIN = chain
    if chain is not None:
        tools.append(chain_update_schema(chain, record))
    music = next((t for t in tools if t.name == MUSIC_TOOL), None)
    if music is not None:
        tools[0] = with_music(tools[0], music, record)
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
