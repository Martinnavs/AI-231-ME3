"""The agent's tools: smart_home, plus the tools of its MCP servers, each call recorded.

MCP servers: docs (BM25 over the baby-onit corpus), the official time server, web_info
(weather and Wikipedia, in the root project) and the Pi's device server over the network. The
Pi's is optional: without a token, or when the Pi is unreachable, the agent starts without it.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import os
import re
import subprocess
import sys
import time
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
from voice_pipeline.chain_state import (
    SIDE_TOOLS,
    ChainState,
    is_acknowledgement,
    is_chain_tool,
    refines,
)
from voice_pipeline.device_say import device_sentence, single_question
from voice_pipeline.scratchpad import NOTES_SENTENCE, Scratchpad
from voice_pipeline.tools.smart_home import SCHEMA, make_handler

REPO_ROOT = Path(__file__).resolve().parents[3]
# On the laptop the MCP servers run through uv; on the Pi (no uv) $MCP_PYTHON is the venv's python.
MCP_PYTHON = os.environ.get("MCP_PYTHON")
ROOT_MODULE = (
    [MCP_PYTHON, "-m"] if MCP_PYTHON else ["uv", "run", "--group", "laptop", "python", "-m"]
)
DOCS_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.docs"]


def web_info_command(env: Mapping[str, str] | None = None) -> list[str]:
    """The weather/places server's command. The MCP child does not inherit the environment, so the
    venue goes on the command line."""
    env = os.environ if env is None else env
    cmd = [*ROOT_MODULE, "voice_agent.mcp_servers.web_info"]
    cmd += ["--home", env.get("VOICE_AGENT_HOME_PLACE") or "Cebu City"]
    return cmd + (
        ["--coords", env["VOICE_AGENT_HOME_COORDS"]] if env.get("VOICE_AGENT_HOME_COORDS") else []
    )


WEB_INFO_COMMAND = web_info_command()
TAVILY_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.tavily_search"]
# $DELEGATE_STATE_DIR (a second server for testing) keeps its jobs and notices apart from the
# running stack's; the MCP child does not inherit the environment, so it goes on the command line
DELEGATE_STATE_DIR: str | None = os.environ.get("DELEGATE_STATE_DIR") or None
DELEGATE_CONFIG: str | None = os.environ.get("DELEGATE_CONFIG") or None
MUSIC_RESOLVER_URL: str | None = os.environ.get("MUSIC_RESOLVER_URL") or None
APPS_HOST: str | None = os.environ.get("APPS_PUBLIC_HOST") or None


def delegate_workspace() -> Path:
    """The shared workspace the delegate mounts at /sandbox (from its config)."""
    import tomllib

    config = Path(DELEGATE_CONFIG) if DELEGATE_CONFIG else REPO_ROOT / "config/delegate.toml"
    paths = tomllib.loads(config.read_text())["paths"]
    return Path(paths.get("workspace", "~/agent_sandbox/workspace")).expanduser()


def delegate_command() -> list[str]:
    cmd = [*ROOT_MODULE, "voice_agent.mcp_servers.delegate"]
    if DELEGATE_STATE_DIR:
        cmd += ["--state-dir", DELEGATE_STATE_DIR]
    if DELEGATE_CONFIG:
        cmd += ["--config", DELEGATE_CONFIG]
    return cmd


def music_source() -> str:
    """ "spotify" or "youtube": $MUSIC_SOURCE, else "source" in config/music.json."""
    if env := os.environ.get("MUSIC_SOURCE"):
        return env
    try:
        return str(json.loads((REPO_ROOT / "config" / "music.json").read_text()).get("source"))
    except (OSError, ValueError):
        return "youtube"


def music_command() -> list[str] | None:
    """The music MCP server, or None when it cannot run. YouTube needs the laptop's resolver; the
    Spotify source does not (the Web API searches, the desktop app on the laptop plays), so
    HARNESS=laptop gets the music tool too (it had none, and "play X" was answered "Playing"
    by the smart_home stub with nothing playing, 2026-11-12)."""
    spotify = music_source() == "spotify"
    if not MUSIC_RESOLVER_URL and not spotify:
        return None
    command = [*ROOT_MODULE, "voice_agent.mcp_servers.music"]
    if MUSIC_RESOLVER_URL:
        command += ["--resolver-url", MUSIC_RESOLVER_URL]
    return command + (["--source", "spotify"] if spotify else [])


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
    "nearby",
    "nearest",
    "publish_pages",
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
QUEUED_SAY = "I'll add that after the first one finishes. Say separate to start it now."
DELEGATE_REPLACED = "Okay, I've switched to that. I'll tell you when it's done."
# "Sorry, I mean ..." / "No, I mean ...": the user restates what they just asked for. A restated
# request used to start a second and a third job with other wording (three in 20 s, 2026-11-12);
# now it replaces the job that was started in the last REPLACE_WINDOW_S.
REPAIR = re.compile(r"\bi\s+mean(?:t)?\b", re.IGNORECASE)
REPLACE_WINDOW_S = 30.0
_last_job: list[tuple[str, float]] = []  # [(job id, when it started)]
_cancel_job: list[Any] = []  # the delegate_cancel handler
# a delegate task that deletes or overwrites files needs the user's yes first (a misheard fragment
# once deleted every app in the workspace)
DESTRUCTIVE = re.compile(r"\b(delete|remove|erase|wipe|clear out|rm|empty|overwrite|reset)\b", re.I)
CONFIRMED = re.compile(
    r"\b(yes|yeah|yep|yup|sure|go ahead|do it|confirm(ed)?|please do|oo|sige)\b", re.I
)
ASK_DESTRUCTIVE = "That would delete or overwrite files in the shared folder. Should I go ahead?"
# Publishing to GitHub Pages (voice_agent.mcp_servers.pages): the first call is always a dry run
# whose summary is spoken with a question; only a call for the same app, within PUBLISH_YES_S and
# while the user's own words are a yes, publishes. Code enforces it: the model cannot skip it.
PUBLISH_READY = "Ready to publish"  # the pages server's dry-run prefix (pages.READY)
PUBLISH_ASK = " Should I go ahead and publish it?"
PUBLISH_YES_S = 90.0
_pending_publish: tuple[str, float] | None = None
PUBLISH_RULE = (
    "Publishing: to put an app that was already made on GitHub Pages ('publish it', 'put it "
    "online'), call publish_pages with its folder name (open_app with an empty name lists them). "
    "It asks the user to confirm; say what it returns. Never publish anything else."
)
DELEGATE_GRACE_S = 1.5
_pending_destructive: str | None = None


# Words a build request is made of whatever it is about: they do not show that a task continues
# the research in a chain's notes.
TASK_GENERIC = frozenset(
    {
        "create",
        "build",
        "make",
        "simple",
        "website",
        "webpage",
        "page",
        "pages",
        "self",
        "contained",
    }
    | {"file", "files", "html", "index", "using", "about", "with", "from", "that", "this", "folder"}
    | {"named", "user", "information", "include", "show", "shows", "display", "details", "sources"}
    | {"facts", "notes", "conversation", "unchecked", "apps", "project", "sandbox", "scratch"}
    | {"profile", "markdown", "version", "script", "code", "also", "only", "should", "would"}
    # question and function words, and the labels the notes file puts around every entry: the
    # question "what is the tallest mountain" must not make a build "what sourdough bread is"
    # look related (live, 2026-11-11)
    | {"what", "which", "when", "where", "whom", "does", "have", "has", "them", "their", "there"}
    | {"into", "your", "will", "than", "then", "each", "some", "more", "most", "just", "like"}
    | {"very", "many", "much", "well", "been", "were", "they", "these", "those", "over", "such"}
    | {"text", "data", "never", "instructions", "untrusted", "treat", "answer", "research"}
    | {"query", "recent", "wiki", "summary", "today", "fact", "short", "basic", "overview"}
)


def _distinct(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{4,}", text.lower())) - TASK_GENERIC


UPDATE_IN_PLACE = "Update the existing"
NOTHING_TO_DO = "Okay, nothing to do."
MANIFEST_SENTENCE = (
    "When done, also write MANIFEST.json beside the main file: "
    '{"items": [the names of the things it covers], "gaps": [what it lacks or could not confirm]}.'
)
MANIFEST_TASKS = re.compile(r"\b(?:page|website|table|markdown|csv|list|\.md|\.html)\b", re.I)
PLAYABLE_TASKS = re.compile(r"\b(?:game|playable|animation|simulation)\b", re.I)
MANIFEST_NAME = "MANIFEST.json"
MANIFEST_MAX_BYTES = 20_000


def main_file(files: list[Any]) -> str:
    """The deliverable among a job's files: not the manifest or the sources list (the manifest
    came first in one job's file list and "Update the existing .../MANIFEST.json" went to the
    delegate, 2026-11-12). The first page, else the first file that is not bookkeeping."""
    names = [str(f) for f in files]
    kept = [n for n in names if Path(n).name not in (MANIFEST_NAME, "SOURCES.json")]
    pages = [n for n in kept if n.lower().endswith((".html", ".htm", ".md", ".csv", ".txt"))]
    return (pages or kept or [""])[0]


def read_manifest(artifact: str, workspace: Path | None = None) -> dict[str, list[str]] | None:
    """The sub-agent's MANIFEST.json beside the deliverable ({"items": [...], "gaps": [...]}), or
    None: what the page holds and lacks, so the front agent can resolve "the other two". Only a
    plain file inside the workspace is read, and only a small one."""
    root = (delegate_workspace() if workspace is None else workspace).resolve()
    path = (root / artifact).parent / MANIFEST_NAME
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            return None
        if not path.is_file() or path.stat().st_size > MANIFEST_MAX_BYTES:
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        key: [str(x) for x in data.get(key, []) if isinstance(x, str | int | float)]
        for key in ("items", "gaps")
    }


def update_sentence(data: dict[str, Any]) -> str:
    """The patch half of a refinement task: where the deliverable is, what to keep, what it lacks.
    The model restated whole pages as new tasks and the page lost three of its five cities
    (2026-11-12); this goes on every refinement of a finished deliverable."""
    parts = [
        f"{UPDATE_IN_PLACE} {data['artifact_path']} in place: read it first and keep everything "
        "that is on it unless the user asked to remove it."
    ]
    if data.get("items"):
        parts.append(f"Keep ALL of these items: {', '.join(data['items'])}.")
    if data.get("gaps"):
        parts.append(f"It is known to lack: {'; '.join(data['gaps'])}. Fill these if you can.")
    return " ".join(parts)


def related(task: str, *texts: str) -> bool:
    """Whether `task` shares a distinctive word with `texts` (a chain's subject and facts, or the
    scratch notes): a build about something else must not carry them."""
    return bool(_distinct(task) & _distinct(" ".join(texts)))


def _job_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    return result if isinstance(result, dict) else None


# One lookup per question: within a turn a second web lookup gets the first one's text back.
LOOKUP_TOOLS = frozenset({"web_research", "web_answer", "wiki_summary"})
REPEAT_PREFIX = "(already looked up this turn) "
_turn_lookups: list[str] = []
_lookup_future: asyncio.Future[str] | None = None


def reset_turn_lookups() -> None:
    global _lookup_future
    _turn_lookups.clear()
    _lookup_future = None


async def replace_running_job(said: str) -> bool:
    """A restatement ("sorry, I mean ...") within REPLACE_WINDOW_S of a job's start cancels that
    job, so that the new request replaces it instead of running beside it. True if one was
    cancelled."""
    if not (_last_job and _cancel_job and REPAIR.search(said)):
        return False
    job, started = _last_job[0]
    if time.monotonic() - started > REPLACE_WINDOW_S:
        return False
    got: list[Any] = []

    async def done(result: Any, *args: Any, **kwargs: Any) -> None:
        got.append(result)

    params = SimpleNamespace(
        function_name="delegate_cancel",
        tool_call_id="replace",
        arguments={"job": job},
        result_callback=done,
    )
    with contextlib.suppress(Exception):
        await _cancel_job[0](params)
    row = _job_row(got[0]) if got else None
    _last_job.clear()
    return row is not None and row.get("status") == "cancelled"


def job_started(result: Any) -> bool:
    """Whether a `delegate` result says the job was started and runs in the background."""
    row = _job_row(result)
    return row is not None and row.get("status") == "started"


def job_queued(result: Any) -> bool:
    """Whether a `delegate` result says the request was queued as an addition to a running job."""
    row = _job_row(result)
    return row is not None and row.get("status") == "queued"


def started_job_id(result: Any) -> str:
    row = _job_row(result)
    return str(row.get("job") or "") if row and row.get("status") == "started" else ""


# The chain being worked on (research, then build); set by build_tools(chain=...). None: no chain.
CHAIN: ChainState | None = None
# Notes of this conversation's lookups for the background job; None: none are kept.
SCRATCHPAD: Scratchpad | None = None
CHAIN_UPDATE_DESCRIPTION = (
    "Record where a multi-step task stands so the next turn knows. phase is one of "
    "awaiting_name, researching, awaiting_fact_approval, building; subject is who or what "
    "it is about; facts is a few short verified facts, separated by ' | ' (while researching they "
    "add up); items is the list of things the task is about (cities, phones, events, files), "
    "separated by ' | ': they are ADDED to the list, never replaced, so give only new ones; remove "
    "drops items the user no longer wants. Returns ok."
)


IDLE_REFUSED = "ok, the task stays open until it finishes or you say cancel"


def chain_update_schema(chain: ChainState, record: list[dict[str, Any]]) -> FunctionSchema:
    async def handler(params: FunctionCallParams) -> None:
        args = params.arguments
        record.append({"tool": "chain_update", "args": dict(args)})
        fields: dict[str, Any] = {}
        if args.get("subject"):
            fields["subject"] = str(args["subject"])[:80]
        if args.get("facts"):
            fields["facts"] = re.split(r"\s*(?:\||\n)\s*", str(args["facts"]))
        for name in ("items", "remove"):
            if args.get(name):
                fields[name] = [x for x in re.split(r"\s*(?:\||\n)\s*", str(args[name])) if x]
        if args.get("phase") == "idle":  # only code ends a chain: expiry, cancel words, done
            await params.result_callback(IDLE_REFUSED)
            return
        try:
            chain.update(str(args.get("phase") or ""), **fields)
        except ValueError as exc:
            await params.result_callback(f"error: {exc}")
            return
        if SCRATCHPAD is not None and fields.get("facts"):
            SCRATCHPAD.add(
                "facts", str(fields.get("subject") or "chain"), "\n".join(fields["facts"])
            )
        await params.result_callback("ok")

    return FunctionSchema(
        name="chain_update",
        description=CHAIN_UPDATE_DESCRIPTION,
        properties={
            "phase": {
                "type": "string",
                "description": "awaiting_name, researching, awaiting_fact_approval, building",
            },
            "subject": {"type": "string", "description": "who or what the task is about"},
            "facts": {"type": "string", "description": "short facts separated by ' | '"},
            "items": {"type": "string", "description": "new things the task is about, ' | '"},
            "remove": {"type": "string", "description": "items to drop, ' | '"},
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
    "If the app does not exist the reply says so: offer to make it with `delegate`. A text file "
    "(a name ending .md, .txt or .csv) is not opened but read back to you, so you can read it "
    "aloud or summarize it as plain sentences, with no symbols and no file or web addresses; a "
    "long file comes in parts (call again with `offset`). Never delegate just to read a file."
)


READ_EXTENSIONS = frozenset({".md", ".txt", ".csv"})
READ_CHARS = 1500  # about a minute of speech; the rest is read on "continue"


def readable_files(root: Path) -> list[Path]:
    """Text deliverables in the workspace, newest first (not scratch notes, not links out)."""
    found: list[Path] = []
    try:
        for path in root.rglob("*"):
            rel = path.relative_to(root)
            if (
                path.suffix.lower() in READ_EXTENSIONS
                and len(rel.parts) <= 3
                and rel.parts[0] not in ("scratch", "__pycache__")
                and path.name not in ("SOURCES.json", "PROFILE.md", "NOTES.md")
                and path.is_file()
                and not path.is_symlink()
            ):
                found.append(path)
    except OSError:
        return []
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def read_deliverable(name: str, offset: int = 0, root: Path | None = None) -> str:
    """The text of the workspace file called `name` (a part of READ_CHARS from `offset`), or a
    sentence saying what to do instead. Only the workspace's text deliverables can be read."""
    base = delegate_workspace() if root is None else root
    files = readable_files(base)
    name = Path(name.strip()).name
    if not name:
        listed = ", ".join(p.name for p in files[:6])
        return f"Files I can read: {listed}." if listed else "I have no text files to read yet."
    match = next((p for p in files if p.name.lower() == name.lower()), None)
    if match is None:
        key = app_key(Path(name).stem)
        match = next((p for p in files if key and app_key(p.stem) == key), None)
    if match is None:
        return f"I can't find a file called {name}."
    try:
        text = match.read_text(errors="replace")
    except OSError:
        return f"I can't read {name} right now."
    start = max(offset, 0)
    part = text[start : start + READ_CHARS]
    more = start + READ_CHARS
    if more < len(text):
        part += f"\n[More follows: call read_file again with offset={more} when asked to continue.]"
    return part or "That file is empty."


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
    if Path(name).suffix.lower() in READ_EXTENSIONS:
        return "That is a file, not an app. I can read it to you if you like.", None
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
# Not shown to the model: music (the router plays it), convert_time (get_current_time takes any
# zone) and wiki_summary (web_answer covers it, and the router's wiki_summary calls already run as
# web_answer). Fewer tools to choose between; the router path still reaches all of them.
MODEL_HIDDEN = frozenset({MUSIC_TOOL, "convert_time", "wiki_summary"})
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


NO_MUSIC = "Music isn't connected right now."


def without_music(home: FunctionSchema) -> FunctionSchema:
    """smart_home when there is no music tool: a music request says so. The stub that stands in for
    the dashboard answered OK, and the agent told the owner it was playing something."""
    inner = home.handler
    assert inner is not None

    async def handler(params: FunctionCallParams) -> None:
        if str(params.arguments.get("intent", "")) in MUSIC_INTENTS:
            await params.result_callback(NO_MUSIC, properties=NO_SECOND_REQUEST)
            return
        await inner(params)

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


def publish_config() -> Path:
    return Path(os.environ.get("PUBLISH_CONFIG") or REPO_ROOT / "config" / "publish.toml")


def publish_configured(path: Path | None = None) -> bool:
    """Whether config/publish.toml names a repository (else the tool is not offered at all)."""
    import tomllib

    try:
        repo = tomllib.loads((path or publish_config()).read_text()).get("repo", "")
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return bool(str(repo).strip())


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
    if publish_configured():
        optional.append(stdio_server([*ROOT_MODULE, "voice_agent.mcp_servers.pages"]))
    if (music := music_command()) is not None:
        optional.append(stdio_server(music))
    return required, optional + ([pi] if pi is not None else [])


NOT_IN_DOCUMENTS = "The documents have nothing about the app or game you made."


def _about_own_work(user: str) -> bool:
    from voice_pipeline.call_policy import about_own_work

    return about_own_work(user)


WHO_QUESTION = re.compile(r"\s*who(?:'s|\s+(?:is|are|was|were))\b", re.IGNORECASE)
NOT_ONE_FACT = re.compile(
    r"\b(said|says?|think|thought|review\w*|opinions?|history|news|compare|why|how|best|top)\b",
    re.IGNORECASE,
)
_web_answer: list[Callable[[FunctionCallParams], Awaitable[None]]] = []


def one_fact_question(user: str) -> bool:
    """A "who is ..." question with nothing that asks for several sources: one fact, so the
    grounded web_answer, not a run of web_research searches (eight in a row for "who is the
    coordinator of the AI program", 2026-11-11: the front agent kept choosing web_research)."""
    return (
        bool(WHO_QUESTION.match(user)) and not NOT_ONE_FACT.search(user) and len(user.split()) <= 25
    )


def _recorded(schema: FunctionSchema, record: list[dict[str, Any]]) -> FunctionSchema:
    inner = schema.handler
    assert inner is not None

    async def handler(params: FunctionCallParams) -> None:
        if (
            schema.name == "web_research"
            and _web_answer
            and one_fact_question(question.current)
            and not (CHAIN is not None and CHAIN.active())
        ):
            params.arguments = {"query": question.current}  # the user's own full question
            params.function_name = "web_answer"  # the MCP wrapper runs the tool named by the call
            await _web_answer[0](params)
            return
        if schema.name == "search_docs" and _about_own_work(question.current):
            await params.result_callback(NOT_IN_DOCUMENTS)
            return
        raw_args = {k: v for k, v in params.arguments.items() if k != "say"}
        args = dict(raw_args)
        entry: dict[str, Any] = {
            "tool": schema.name,
            "args": args,
        }
        if "task" in raw_args:
            entry["raw_task"] = raw_args["task"]
        record.append(entry)
        if CHAIN is not None and CHAIN.active() and not is_chain_tool(schema.name):
            CHAIN.touch_unrelated(side=schema.name in SIDE_TOOLS)
        params.arguments = args  # the real tool never sees `say`
        if schema.name in LOOKUP_TOOLS:
            global _lookup_future
            if _turn_lookups:
                entry["cached"] = True
                await params.result_callback(REPEAT_PREFIX + _turn_lookups[0])
                return
            if _lookup_future is not None and not _lookup_future.done():
                entry["cached"] = True
                try:
                    cached_val = await asyncio.wait_for(
                        asyncio.shield(_lookup_future), timeout=10.0
                    )
                    await params.result_callback(REPEAT_PREFIX + cached_val)
                except Exception:
                    await params.result_callback(REPEAT_PREFIX + "no result")
                return

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            _lookup_future = loop.create_future()
            fut = _lookup_future
            first_callback = params.result_callback

            async def remember(result: Any, *, properties: Any = None) -> None:
                global _lookup_future
                if isinstance(result, str) and not FAILURE.search(result):
                    _turn_lookups.append(result)
                    if not fut.done():
                        fut.set_result(result)
                    if SCRATCHPAD is not None:
                        query = str(args.get("query") or args.get("topic") or "")
                        SCRATCHPAD.add(schema.name, query, result)
                else:
                    if not fut.done():
                        fut.set_result(str(result) if result is not None else "")
                await first_callback(result, properties=properties)

            params.result_callback = remember
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
            if is_acknowledgement(question.current or ""):
                record.pop()  # "got it" is no request (it once started a job): nothing runs
                await deliver_job(NOTHING_TO_DO, properties=NO_SECOND_REQUEST)
                return
            injected_parts: list[str] = []
            if (
                CHAIN is not None
                and CHAIN.active()
                and (CHAIN.data.get("facts") or CHAIN.data.get("subject"))
                and not related(
                    task,
                    str(CHAIN.data.get("subject", "")),
                    *CHAIN.data["facts"],
                    *CHAIN.data["items"],
                )
            ):
                CHAIN.update("idle")  # a build about something else: the research chain is over
            if CHAIN is not None and CHAIN.data.get("facts"):
                facts_str = " | ".join(CHAIN.data["facts"])
                if facts_str not in task:
                    injected_parts.append(f"Notes from the conversation (unchecked): {facts_str}.")
            if (
                SCRATCHPAD is not None
                and SCRATCHPAD.has_entries()
                and related(task, SCRATCHPAD.render())
            ):
                urls = sorted(SCRATCHPAD.urls())
                if urls and not any(u in task for u in urls):
                    injected_parts.append(f"Primary source URLs: {', '.join(urls[:5])}.")
                if NOTES_SENTENCE not in task:
                    injected_parts.append(NOTES_SENTENCE)
            if (
                CHAIN is not None
                and CHAIN.active()
                and CHAIN.data.get("artifact_path")
                and (
                    refines(question.current or "")
                    or refines(task[:200])
                    or "existing" in task.lower()
                )
                and UPDATE_IN_PLACE not in task
                and not args.get("separate")  # a task of its own is not an update of the page
            ):
                injected_parts.insert(0, update_sentence(CHAIN.data))
            if (
                MANIFEST_TASKS.search(task)
                and not PLAYABLE_TASKS.search(task)
                and "MANIFEST.json" not in task
            ):
                injected_parts.append(MANIFEST_SENTENCE)  # in the task, small models obey it
            if injected_parts:
                args["task"] = f"{task.rstrip()} " + " ".join(injected_parts)
            if re.search(
                r"\b(?:markdown|cheat\s*sheet|\.md|\.py|python\s+script|table)\b",
                task,
                re.IGNORECASE,
            ) and not re.search(r"\b(?:game|playable|interactive)\b", task, re.IGNORECASE):
                args["task"] = re.sub(
                    r"\s*Create a web version at apps/\S+", "", args.get("task", task)
                )
            global _pending_destructive
            if DESTRUCTIVE.search(task):
                if _pending_destructive is None or not CONFIRMED.search(question.current or ""):
                    _pending_destructive = task
                    await deliver_job(ASK_DESTRUCTIVE, properties=NO_SECOND_REQUEST)
                    return
                _pending_destructive = None
            # a turn that is cut off and resumed is cancelled during this wait, before a job starts
            await asyncio.sleep(DELEGATE_GRACE_S)
            replaced = await replace_running_job(question.current or "")

            async def job_callback(result: Any, *, properties: Any = None) -> None:
                entry["result"] = str(result)[:160]  # what the delegate server answered
                if job_started(result):
                    if CHAIN is not None:
                        CHAIN.update("building", job_id=started_job_id(result))
                    _last_job[:] = [(started_job_id(result), time.monotonic())]
                    await deliver_job(
                        DELEGATE_REPLACED if replaced else DELEGATE_STARTED,
                        properties=properties or NO_SECOND_REQUEST,
                    )
                elif job_queued(result):
                    # one fixed sentence (the model said "I've added that update ... it is currently
                    # being processed ..." in three sentences): the user can say "separate"
                    await deliver_job(QUEUED_SAY, properties=properties or NO_SECOND_REQUEST)
                else:
                    await deliver_job(result, properties=properties)

            params.result_callback = job_callback
        elif schema.name == "publish_pages":
            global _pending_publish
            app = str(args.get("app", "")).strip()
            pending = _pending_publish
            yes = (
                pending is not None
                and pending[0] == app
                and time.monotonic() - pending[1] <= PUBLISH_YES_S
                and CONFIRMED.search(question.current or "") is not None
            )
            args["dry_run"] = not yes  # the model's own value never counts
            deliver_publish = params.result_callback

            async def publish_callback(result: Any, *, properties: Any = None) -> None:
                global _pending_publish
                text = str(result)
                if yes:
                    _pending_publish = None
                elif text.startswith(PUBLISH_READY):
                    _pending_publish = (app, time.monotonic())
                    text += PUBLISH_ASK
                else:
                    _pending_publish = None
                await deliver_publish(text, properties=NO_SECOND_REQUEST)

            params.result_callback = publish_callback
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
        try:
            await inner(params)
        finally:
            if (
                schema.name in LOOKUP_TOOLS
                and _lookup_future is not None
                and not _lookup_future.done()
            ):
                _lookup_future.set_result("")

    if schema.name == "web_answer":
        _web_answer[:] = [handler]  # the newest build wins; web_research redirects to it
    if schema.name == "delegate_cancel":
        _cancel_job[:] = [inner]  # the raw handler: a replaced job is cancelled without a record
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
    scratchpad: Scratchpad | None = None,
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
        name = str(params.arguments.get("name") or "")
        if Path(name).suffix.lower() in READ_EXTENSIONS:  # a text file is read back, not opened
            try:
                offset = int(params.arguments.get("offset") or 0)
            except (TypeError, ValueError):
                offset = 0
            await params.result_callback(read_deliverable(name, offset))
            return
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
                    "offset": {
                        "type": "integer",
                        "description": "for a long text file: skip this many characters",
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
    global CHAIN, SCRATCHPAD
    CHAIN, SCRATCHPAD = chain, scratchpad
    if chain is not None:
        tools.append(chain_update_schema(chain, record))
    music = next((t for t in tools if t.name == MUSIC_TOOL), None)
    if music is not None:
        tools[0] = with_music(tools[0], music, record)
    elif live is None:  # no dashboard and no music tool: nothing could play
        tools[0] = without_music(tools[0])
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
