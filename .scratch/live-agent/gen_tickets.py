"""Generate the level-3 tickets from the verified reference code in skeleton/.

    python3 gen_tickets.py <repo path the tickets should name> <output dir>
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SK = HERE / "skeleton"
REPO, OUT = sys.argv[1], Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)


def code(rel: str, lang: str = "python") -> str:
    return f"```{lang}\n{(SK / rel).read_text()}```\n"


def n(rel: str) -> int:
    return len((SK / rel).read_text().splitlines())


def cap(rel: str, test: bool = False) -> str:
    lines = n(rel)
    return f"**{int(lines * (1.25 if test else 1.1)) + 1}** (skeleton {lines})"


APPROVAL = (
    "| 2026-10-23 | design | fastmcp 2.14.7 docs MCP server; documents via a tool; ME2 UI kept, "
    "not a priority; phonebook for CALL/MESSAGE, loose reminders, open slots; dry run native "
    "Gemini only; agy builds at level 3 | owner |"
)


def ticket(
    num: str,
    title: str,
    decision: str,
    blocked: str,
    objective: str,
    files: list[tuple[str, str, str]],
    nongoals: str,
    security: str,
    flow: str,
    seq: str,
    edges: list[tuple[str, str]],
    contracts: str,
    inventory: str,
    assumptions: list[tuple[str, str]],
    proof_intro: str,
    proofs: list[tuple[str, str]],
) -> str:
    table = "\n".join(f"| `{p}` | {a} | {c} |" for p, a, c in files)
    edge_rows = "\n".join(f"| {e} | {b} |" for e, b in edges)
    a_rows = "\n".join(f"| {a} | {b} |" for a, b in assumptions)
    p_rows = "\n".join(f"| {i} | {t} | `{c}` | |" for i, (t, c) in enumerate(proofs, 1))
    return f"""# Feature Spec: {num} — {title}

**Status:** approved
**Date:** 2026-10-23
**Blocked by:** {blocked}

{decision} Read `../GUARDRAILS.md` first; it binds this ticket.

## Context

- **Objective:** {objective}
- **Role:** strict systems architect; you are transcribing an approved implementation.
- **User goal:** a live voice test where the agent acts on the home and answers from the documents, with tools as the single mechanism.
- **Source:** `{REPO}/.scratch/live-agent/PLAN.md`
- **Repo:** `{REPO}`

| File | Action | Line cap |
|---|---|---|
{table}

**Non-goals / touch nothing else:** {nongoals} No other file in the repo.

**Security-sensitive:** {security}

## Diagrams

### Data flow

```mermaid
{flow}
```

### Sequence / component

```mermaid
{seq}
```

## RECAP

### E — Edges

| Edge | Behavior |
|---|---|
{edge_rows}

Nothing else.

### C — Contracts

{contracts}
Final inventory: {inventory}

### A — Assumptions

| Assumption | Verified when / how |
|---|---|
{a_rows}

### P — Proof (acceptance criteria)

{proof_intro}

| # | Test / acceptance criterion | Command | Result |
|---|---|---|---|
{p_rows}

## Deviations

| Date | Deviation | Minor: rationale / Material: design-gate re-run |
|---|---|---|

## Approvals

| Date | Gate | What was approved / decided | By |
|---|---|---|---|
{APPROVAL}

## Execution Log
"""


PIPE_GATES = (
    "cd pipeline && uv run --offline ruff check . && uv run --offline ruff format --check . "
    "&& uv run --offline mypy"
)
VERIFIED = "Established: ran green in the ticket-writer's overlay of this repo, 2026-10-23"

# ---------------------------------------------------------------- 01
T01 = ticket(
    "01",
    "smart_home: open slots and a JSON phonebook",
    "smart_home's slots become open (any time, duration, colour, temperature, brightness, "
    "reminder text); CALL and MESSAGE now take a slot that must be a contact in "
    "`pipeline/config/phonebook.json`, whose names are listed in the tool description.",
    "None (can start immediately)",
    "rewrite one module, add one data file, extend its tests. Small.",
    [
        ("pipeline/config/phonebook.json", "new, verbatim", f"**{n('pipeline/config/phonebook.json')}** (exactly)"),
        ("pipeline/src/voice_pipeline/tools/smart_home.py", "rewrite, verbatim", cap("pipeline/src/voice_pipeline/tools/smart_home.py")),
        ("pipeline/tests/test_smart_home.py", "rewrite, verbatim", cap("pipeline/tests/test_smart_home.py", True)),
    ],
    "no change to `dryrun.py`, `latency.py` or the config/eval files (tickets 05); no slot normalisation.",
    "no — local data only; no secrets.",
    'flowchart LR\n    PB["phonebook.json (new)"] -- "contacts" --> MS["make_schema() (new)"]\n    MS -- "SCHEMA with names" --> L["LLM"]\n    L -- "smart_home(intent, slot)" --> H["make_handler handler (changed)"]\n    H -- "contact check, record" --> R["record / ME2 (--live)"]',
    "sequenceDiagram\n    participant L as LLM\n    participant H as handler\n    L->>H: CALL slot=anna\n    H->>H: record; anna in phonebook (case-insensitive)?\n    H-->>L: ok recorded, or 'not in the phonebook (Anna, Ben, ...)'",
    [
        ("unknown intent", "`{ok: False, message: 'unknown intent X'}`; recorded; no HTTP"),
        ("slotted intent (now including CALL, MESSAGE) without slot", "`{ok: False, message: '<INTENT> needs a value: <hint>'}`; recorded"),
        ("CALL/MESSAGE slot not in the phonebook (case-insensitive)", "`{ok: False, message: '<slot> is not in the phonebook (<names>)'}`; recorded; no HTTP"),
        ("`base_url` None", "`{ok: True, message: 'recorded'}`"),
        ("dashboard unreachable / non-200", "unchanged from before"),
    ],
    f"`pipeline/config/phonebook.json`:\n\n{code('pipeline/config/phonebook.json', 'json')}\n`pipeline/src/voice_pipeline/tools/smart_home.py` (whole file, verbatim):\n\n{code('pipeline/src/voice_pipeline/tools/smart_home.py')}",
    "smart_home.py: constants `PHONEBOOK`, `INTENTS`, `SLOT_FOR_INTENT`, `SLOT_HINTS`, `SCHEMA`; functions `load_phonebook`, `make_schema`, `to_command`, `make_handler` (inner `handler`), `_post`.",
    [
        ("`parents[3]` of smart_home.py is `pipeline/`", VERIFIED),
        ("FunctionSchema(name, description, properties, required) in pipecat 1.13.0", "Established: used by the landed ticket of 2026-10-23"),
        ("Unknowns", "None"),
        ("Dependencies/license", "none new"),
    ],
    f"Tests in `pipeline/tests/test_smart_home.py` (whole file, verbatim):\n\n{code('pipeline/tests/test_smart_home.py')}\nNo other fixtures.",
    [
        ("9 tests pass", "cd pipeline && uv run --offline pytest -q tests/test_smart_home.py"),
        ("pipeline gates green", PIPE_GATES),
        ("line caps respected", "wc -l on the files in the table"),
    ],
)

# ---------------------------------------------------------------- 02
ROOT_FILES_02 = "src/voice_agent/mcp_servers/docs.py tests/mcp_servers/test_docs_server.py"
T02 = ticket(
    "02",
    "docs MCP server (fastmcp): search_docs over the doc-chat corpus",
    "A new MCP server in the root project, built with fastmcp 2.14.7 (already in the laptop "
    "dependency group): one tool, `search_docs(query, top_k=3)`, BM25 over a corpus folder "
    "using doc_chat's own loader. This is the precedent for tools as MCP servers.",
    "None (can start immediately)",
    "one server module and its tests. Small.",
    [
        ("src/voice_agent/mcp_servers/docs.py", "new, verbatim", cap("src/voice_agent/mcp_servers/docs.py")),
        ("tests/mcp_servers/test_docs_server.py", "new, verbatim", cap("tests/mcp_servers/test_docs_server.py", True)),
    ],
    "the existing MCP servers (`home.py`, `speech.py`, `code.py`) and `doc_chat` stay unchanged; `pyproject.toml`/`uv.lock` are already updated, do not touch them.",
    "no — reads only the corpus folder given on the command line.",
    'flowchart LR\n    C["corpus/*.md"] -- "load_and_split_corpus (existing)" --> B["BM25Retriever"]\n    A["MCP client (agent)"] -- "search_docs(query, top_k)" --> S["docs FastMCP server (new)"]\n    S -- "query" --> B\n    B -- "chunks" --> S\n    S -- "[{source, text}]" --> A',
    "sequenceDiagram\n    participant A as MCP client\n    participant S as docs server\n    A->>S: list_tools\n    S-->>A: [search_docs]\n    A->>S: call_tool search_docs {query, top_k}\n    S-->>A: top_k passages (k clamped 1..8)",
    [
        ("top_k below 1 or above 8", "clamped to 1..8"),
        ("source metadata path", "only the file name is returned"),
    ],
    f"`src/voice_agent/mcp_servers/docs.py` (whole file, verbatim):\n\n{code('src/voice_agent/mcp_servers/docs.py')}",
    "docs.py: constants `DEFAULT_CORPUS`, `DEFAULT_TOPIC`, `CHUNK_SIZE`; functions `build_server` (inner tool `search_docs`), `main`.",
    [
        ("fastmcp 2.14.7 in the root laptop group; `FastMCP.tool(description=...)`, `run(show_banner=...)`", "Established: installed 2026-10-23 (ticket 00), signatures read"),
        ("`doc_chat.corpus.load_and_split_corpus`, `tokenize` exist", "Established: src/doc_chat/corpus.py"),
        ("Unknowns", "None"),
        ("Dependencies/license", "fastmcp (Apache-2.0), already added"),
    ],
    f"Tests in `tests/mcp_servers/test_docs_server.py` (whole file, verbatim):\n\n{code('tests/mcp_servers/test_docs_server.py')}\nRun from the repo root.",
    [
        ("3 tests pass", "uv run --offline --group laptop pytest -q tests/mcp_servers/test_docs_server.py"),
        ("lint and format clean on the two files", f"uv run --offline --group laptop ruff check {ROOT_FILES_02} && uv run --offline --group laptop ruff format --check {ROOT_FILES_02}"),
        ("root mypy strict clean", "uv run --offline --group laptop mypy"),
        ("line caps respected", "wc -l on the files in the table"),
    ],
)

# ---------------------------------------------------------------- 03
MAIN = "src/doc_chat/__main__.py"
ORIG_MAIN = len((HERE.parents[1] / MAIN).read_text().splitlines())
EDITS = '''Edit list for `src/doc_chat/__main__.py` (three replacements; each old block occurs exactly once; change nothing else):

1. In `main()`'s `--llm` argument, replace

```python
        choices=["none", "qwen", "gemini", "echo"],
        default="none",
        help="LLM backend: none, qwen, gemini, or echo (no model: repeats what it heard, then "
        "reads a fixed passage, for testing speech and interruptions). Default: none",
    )
```

with

```python
        choices=["none", "qwen", "gemini", "echo", "agent"],
        default="none",
        help="LLM backend: none, qwen, gemini, echo (no model: repeats what it heard, then "
        "reads a fixed passage, for testing speech and interruptions), or agent (the Pipecat "
        "agent server in pipeline/, with tools; use with --no-docs). Default: none",
    )
    parser.add_argument(
        "--agent-url",
        default="ws://127.0.0.1:8781",
        help="with --llm agent: the agent server (default: ws://127.0.0.1:8781)",
    )
```

2. In the LLM selection, replace

```python
    elif args.llm == "gemini":
        from doc_chat.gemini import build_gemini_chat_model
```

with

```python
    elif args.llm == "agent":
        from doc_chat.agent_client import AgentChatModel

        llm = AgentChatModel(url=args.agent_url, timeout=args.llm_timeout)
    elif args.llm == "gemini":
        from doc_chat.gemini import build_gemini_chat_model
```

3. Replace

```python
    if (args.no_docs or args.stream) and args.llm not in ("qwen", "gemini", "echo"):
```

with

```python
    if (args.no_docs or args.stream) and args.llm not in ("qwen", "gemini", "echo", "agent"):
```

(the error message on the next line stays as it is: an existing test asserts it).
'''
ROOT_FILES_03 = "src/doc_chat/agent_client.py tests/doc_chat/test_agent_client.py src/doc_chat/__main__.py"
T03 = ticket(
    "03",
    "doc_chat --llm agent: a chat model that streams from the agent server",
    "doc_chat gets a new LLM backend, `--llm agent`: a LangChain chat model (like the existing "
    "`EchoChatModel`) that sends the conversation to the agent server over a WebSocket and "
    "streams the reply. Closing the socket early (an interruption) cancels the server's turn.",
    "None (can start immediately; the server is ticket 06, tested here against a fake)",
    "one new module, its tests, three small edits in the CLI. Small.",
    [
        ("src/doc_chat/agent_client.py", "new, verbatim", cap("src/doc_chat/agent_client.py")),
        ("tests/doc_chat/test_agent_client.py", "new, verbatim", cap("tests/doc_chat/test_agent_client.py", True)),
        (MAIN, "edit (list below)", f"**{n(MAIN)}** (now {ORIG_MAIN}; exactly {n(MAIN)} after the edits)"),
    ],
    "`chain.py`, `SentenceStream`, retrieval and every other doc_chat behaviour unchanged; the existing error message for `--no-docs` stays word for word.",
    "no — talks only to 127.0.0.1 by default; no secrets.",
    'flowchart LR\n    SS["SentenceStream (existing)"] -- "llm.stream(messages)" --> C["AgentChatModel (new)"]\n    C -- "{messages}" --> W["agent server ws://127.0.0.1:8781"]\n    W -- "{text} ... {done}" --> C\n    C -- "AIMessageChunk" --> SS',
    "sequenceDiagram\n    participant SS as SentenceStream\n    participant C as AgentChatModel\n    participant W as agent server\n    SS->>C: stream(messages)\n    C->>W: connect, send {messages}\n    W-->>C: {text} x n\n    W-->>C: {done}\n    Note over SS,C: interruption: SentenceStream stops iterating, the socket closes, the server cancels",
    [
        ("server sends {error}", "`RuntimeError('agent server error: ...')` (SentenceStream turns it into the fallback reply)"),
        ("no event within `timeout`", "the websockets timeout error propagates"),
        ("message types other than system/human/ai", "dropped from the request"),
    ],
    f"`src/doc_chat/agent_client.py` (whole file, verbatim):\n\n{code('src/doc_chat/agent_client.py')}\n{EDITS}",
    "agent_client.py: constants `DEFAULT_URL`, `_ROLES`; function `to_wire`; class `AgentChatModel` (methods `_llm_type`, `_generate`, `_stream`). `__main__.py`: no new functions.",
    [
        ("websockets 17 sync client is installed in the root environment", "Established: imported 2026-10-23"),
        ("LangChain's `stream()` appends an empty final chunk", "Established: the test filters empty chunks"),
        ("`tests/doc_chat/test_gemini.py` asserts the old `--no-docs` error text", "Established: read 2026-10-23; edit 3 keeps it"),
        ("Unknowns", "None"),
        ("Dependencies/license", "none new"),
    ],
    f"Tests in `tests/doc_chat/test_agent_client.py` (whole file, verbatim):\n\n{code('tests/doc_chat/test_agent_client.py')}\nRun from the repo root.",
    [
        ("the 3 new tests and the existing gemini CLI test pass", "uv run --offline --group laptop pytest -q tests/doc_chat/test_agent_client.py tests/doc_chat/test_gemini.py"),
        ("lint and format clean on the three files", f"uv run --offline --group laptop ruff check {ROOT_FILES_03} && uv run --offline --group laptop ruff format --check {ROOT_FILES_03}"),
        ("root mypy strict clean", "uv run --offline --group laptop mypy"),
        ("the CLI lists the backend", "uv run --offline --group laptop python -m doc_chat --help | grep -c -- '--agent-url'"),
        ("line caps respected", "wc -l on the files in the table"),
    ],
)

# ---------------------------------------------------------------- 04
T04 = ticket(
    "04",
    "agent core: one Pipecat pipeline around native Gemini, and its tools",
    "Two new modules in pipeline/: `agent_tools.py` (smart_home plus every tool of the docs MCP "
    "server, each call recorded) and `agent.py` (one long-lived pipeline; `ask` runs a turn and "
    "times it, streaming text to a callback; `cancel` interrupts it). Before any code, confirm "
    "tickets 01 and 02 landed: `voice_pipeline.tools.smart_home` exports `SCHEMA` and "
    "`make_handler(*, base_url, record, contacts=None)`, and `src/voice_agent/mcp_servers/docs.py` "
    "exists. A mismatch means stop.",
    "01, 02",
    "two new modules. Medium-small.",
    [
        ("pipeline/src/voice_pipeline/agent_tools.py", "new, verbatim", cap("pipeline/src/voice_pipeline/agent_tools.py")),
        ("pipeline/src/voice_pipeline/agent.py", "new, verbatim", cap("pipeline/src/voice_pipeline/agent.py")),
    ],
    "no change to smart_home.py (ticket 01), docs.py (ticket 02), dryrun.py (ticket 05) or any server (ticket 06).",
    "yes, only in that `read_key` reads the Gemini key from `~/.secrets/google-ai-studio/` at run time; it is never printed. You do not run that path.",
    'flowchart LR\n    M["docs MCP server (root, ticket 02)"] -- "tools over stdio" --> BT["build_tools (new)"]\n    SH["smart_home (ticket 01)"] -- "SCHEMA, make_handler" --> BT\n    BT -- "ToolsSchema, recorded handlers" --> AG["Agent (new)"]\n    AG -- "messages" --> G["GoogleLLMService (native Gemini)"]\n    G -- "frames" --> TT["TurnTap (new)"]\n    TT -- "text, timings" --> AG',
    "sequenceDiagram\n    participant C as caller\n    participant A as Agent\n    participant G as Gemini\n    participant T as TurnTap\n    C->>A: ask(messages, reset, on_text)\n    A->>G: LLMMessagesUpdateFrame / AppendFrame (run_llm)\n    G->>T: ResponseStart, FunctionCallsStarted, ResponseEnd\n    G->>T: ResponseStart, Text..., ResponseEnd -> done\n    A-->>C: {status, text, first_call_s, first_text_s, end_s}\n    C->>A: cancel() (optional, mid-turn)\n    A->>G: InterruptionFrame",
    [
        ("no end of turn within 20 s", "`status: 'error'`"),
        ("cancel mid-turn", "text forwarding stops, the turn returns at once; a late end frame from it cannot end the next turn (`responding` flag)"),
        ("reset=False", "messages are appended to the conversation instead of replacing it"),
        ("an MCP tool is called", "`{'tool': name, 'args': {...}}` appended to the record before the MCP call"),
    ],
    f"`pipeline/src/voice_pipeline/agent_tools.py` (whole file, verbatim):\n\n{code('pipeline/src/voice_pipeline/agent_tools.py')}\n`pipeline/src/voice_pipeline/agent.py` (whole file, verbatim):\n\n{code('pipeline/src/voice_pipeline/agent.py')}",
    "agent_tools.py: constants `REPO_ROOT`, `DOCS_COMMAND`; functions `docs_server`, `_recorded` (inner `handler`), `build_tools`. agent.py: constants `MODEL`, `TURN_TIMEOUT_S`, `OnText`; functions `read_key`, `build_llm`; classes `TurnTap` (`__init__`, `begin`, `process_frame`), `Agent` (`__init__`, `start`, `stop`, `ask`, `cancel`).",
    [
        ("`MCPClient.tools()` returns a ToolsSchema whose schemas carry handlers; `ToolsSchema.standard_tools`; `FunctionSchema.handler`", "Established: pipecat 1.13.0 source read 2026-10-23"),
        ("A late `LLMFullResponseEndFrame` from a cancelled turn arrives after the next `begin`", "Established: seen in a live probe 2026-10-23; the `responding` flag fixes it (probe re-run green)"),
        ("`GoogleLLMService` handles Gemini 3 thought signatures and tool round trips", "Established: 50/50 dry run, 2026-10-23"),
        ("Unknowns", "None"),
        ("Dependencies/license", "pipecat-ai[google,mcp,openai]==1.13.0, already in pipeline/pyproject.toml"),
    ],
    "No unit tests in this ticket (tickets 05 and 06 test the code that uses it; the ticket-writer ran a live probe and the full stress run on this exact code). Run from the repo root.",
    [
        ("the modules import", "cd pipeline && uv run --offline python -c \"from voice_pipeline.agent import Agent, TurnTap, build_llm; from voice_pipeline.agent_tools import build_tools, docs_server; print('ok')\""),
        ("pipeline gates green", PIPE_GATES),
        ("line caps respected", "wc -l on the files in the table"),
    ],
)

# ---------------------------------------------------------------- 05
T05 = ticket(
    "05",
    "stress dry run on the agent (native Gemini, smart_home + docs)",
    "`dryrun.py` is rewritten to drive the ticket-04 `Agent` (one pipeline and one docs server for "
    "the whole run, native Gemini only) over a new 72-prompt set that also routes to "
    "`search_docs`; `latency.py` is deleted (its timings are now in every dry-run row). Before any "
    "code, confirm ticket 04 landed: `voice_pipeline.agent` exports `Agent` and `build_llm(system)`, "
    "and `voice_pipeline.agent_tools` exports `build_tools(record, *, live, docs)` and "
    "`docs_server()`. A mismatch means stop.",
    "04",
    "one module rewrite, two data files, one test rewrite, two deletions. Small.",
    [
        ("pipeline/src/voice_pipeline/dryrun.py", "rewrite, verbatim", cap("pipeline/src/voice_pipeline/dryrun.py")),
        ("pipeline/tests/test_dryrun.py", "rewrite, verbatim", cap("pipeline/tests/test_dryrun.py", True)),
        ("pipeline/config/eval/system_agent.txt", "new, verbatim", f"**{n('pipeline/config/eval/system_agent.txt')}** (exactly)"),
        ("pipeline/config/eval/agent_prompts.jsonl", f"new, `cp {REPO}/.scratch/live-agent/skeleton/pipeline/config/eval/agent_prompts.jsonl`", f"**{n('pipeline/config/eval/agent_prompts.jsonl')}** (exactly)"),
        ("pipeline/src/voice_pipeline/latency.py", "delete", "0"),
        ("pipeline/config/eval/smart_home_prompts.jsonl", "delete (superseded by agent_prompts.jsonl)", "0"),
    ],
    "keep `system_v0..v3_*.txt`; no change to agent.py/agent_tools.py; never run the dry run (paid API).",
    "yes, only through ticket 04's `read_key`; never printed.",
    'flowchart LR\n    P["agent_prompts.jsonl (72)"] -- "turns" --> D["dryrun.run_prompt (new)"]\n    D -- "ask(reset on first turn)" --> A["Agent (ticket 04)"]\n    A -- "record: smart_home + search_docs calls" --> D\n    D -- "score_turn, leaked" --> O["out/agent_dryrun/<id>/rows.jsonl + summary.txt"]',
    "sequenceDiagram\n    participant D as dryrun\n    participant A as Agent\n    D->>A: start; ask('Say OK.') warm-up\n    loop each prompt\n        D->>A: ask(turn 1, reset=True), ask(turn 2, reset=False)...\n        D->>D: score; re-run the prompt once if any turn is not ok\n    end\n    D->>A: stop",
    [
        ("turn with no text and no call", "`status: 'empty'`"),
        ("any non-ok turn in a prompt", "the prompt is re-run once; the second result is kept"),
        ("several search_docs calls in one turn", "counted once"),
        ("CREATE_REMINDER slot", "matches if either slot contains the other (case-insensitive)"),
        ("expected no call and the model calls", "`false_call` true"),
    ],
    f"`pipeline/src/voice_pipeline/dryrun.py` (whole file, verbatim):\n\n{code('pipeline/src/voice_pipeline/dryrun.py')}\n`pipeline/config/eval/system_agent.txt` (verbatim):\n\n{code('pipeline/config/eval/system_agent.txt', 'text')}\n`pipeline/config/eval/agent_prompts.jsonl`: copy the file named in the table byte for byte; do not edit.",
    "dryrun.py: constants `ROOT`, `FREE_TEXT`, `LEAK`; functions `_key`, `_matches`, `score_turn`, `leaked`, `run_prompt`, `_p50`, `summarize`, `run`, `main`.",
    [
        ("Ticket 04's exports as listed above", "Must verify: Step 0"),
        ("The stress run on this exact code: 75/77 exact (after the expectation fixes), 0 leaks, 0 bad turns", VERIFIED),
        ("Unknowns", "None"),
        ("Dependencies/license", "none new"),
    ],
    f"Tests in `pipeline/tests/test_dryrun.py` (whole file, verbatim):\n\n{code('pipeline/tests/test_dryrun.py')}\nRun from the repo root.",
    [
        ("the 5 tests pass, and ticket 01's still pass", "cd pipeline && uv run --offline pytest -q tests/test_dryrun.py tests/test_smart_home.py"),
        ("pipeline gates green", PIPE_GATES),
        ("CLI help works offline", "cd pipeline && uv run --offline python -m voice_pipeline.dryrun --help"),
        ("data files exact; deletions done", f"cd pipeline && wc -l config/eval/system_agent.txt config/eval/agent_prompts.jsonl && cmp config/eval/agent_prompts.jsonl {REPO}/.scratch/live-agent/skeleton/pipeline/config/eval/agent_prompts.jsonl && test ! -e src/voice_pipeline/latency.py && test ! -e config/eval/smart_home_prompts.jsonl"),
        ("line caps respected", "wc -l on the files in the table"),
    ],
)

# ---------------------------------------------------------------- 06
T06 = ticket(
    "06",
    "agent server for doc_chat, and the Makefile targets",
    "`agent_server.py` puts the ticket-04 `Agent` behind a local WebSocket (the protocol of "
    "ticket 03's client): it drops client system messages, serves one turn at a time, cancels a "
    "turn when the client hangs up, and logs every turn. The root Makefile's `smart-home-dryrun` "
    "target becomes `agent-dryrun` and `agent-server`. Before any code, confirm ticket 04 landed "
    "(same exports as ticket 05's check). A mismatch means stop.",
    "04",
    "one server module, its tests, one Makefile block. Small.",
    [
        ("pipeline/src/voice_pipeline/agent_server.py", "new, verbatim", cap("pipeline/src/voice_pipeline/agent_server.py")),
        ("pipeline/tests/test_agent_server.py", "new, verbatim", cap("pipeline/tests/test_agent_server.py", True)),
        ("Makefile", "rewrite, verbatim", f"**{n('Makefile')}** (exactly)"),
    ],
    "no change to agent.py, dryrun.py or doc_chat; never start the server (paid API).",
    "yes, only through ticket 04's `read_key`; binds 127.0.0.1 only.",
    'flowchart LR\n    C["doc_chat AgentChatModel (ticket 03)"] -- "{messages}" --> S["AgentServer.handle (new)"]\n    S -- "ask(non-system messages, reset)" --> A["Agent (ticket 04)"]\n    A -- "text pieces" --> S\n    S -- "{text} ... {done}" --> C\n    S -- "turn row" --> L["out/agent_server/<time>.jsonl"]',
    "sequenceDiagram\n    participant C as client\n    participant S as AgentServer\n    participant A as Agent\n    C->>S: {messages}\n    S->>A: ask(..., on_text=send)\n    A-->>S: text pieces\n    S-->>C: {text}...\n    alt client hangs up first\n        S->>A: cancel()\n    else turn finishes\n        S-->>C: {done} or {error: timeout}\n    end\n    S->>S: append log row",
    [
        ("client system messages", "dropped (the server owns the prompt)"),
        ("a second client while a turn runs", "waits for the lock"),
        ("client disconnects before done", "`agent.cancel()`; row `cancelled: true`"),
        ("sending to a closed socket", "suppressed"),
        ("turn status not ok", "`{error: 'timeout'}`"),
    ],
    f"`pipeline/src/voice_pipeline/agent_server.py` (whole file, verbatim):\n\n{code('pipeline/src/voice_pipeline/agent_server.py')}\n`Makefile` (whole file, verbatim):\n\n{code('Makefile', 'makefile')}",
    "agent_server.py: constant `ROOT`; class `AgentServer` (`__init__`, `handle` with inner `on_text`); functions `run`, `main`.",
    [
        ("websockets 17.3 asyncio server/client in the pipeline environment", "Established: imported 2026-10-23"),
        ("Ticket 04's exports", "Must verify: Step 0"),
        ("Unknowns", "None"),
        ("Dependencies/license", "none new"),
    ],
    f"Tests in `pipeline/tests/test_agent_server.py` (whole file, verbatim):\n\n{code('pipeline/tests/test_agent_server.py')}\nRun from the repo root.",
    [
        ("all pipeline tests pass (16)", "cd pipeline && uv run --offline pytest -q"),
        ("pipeline gates green", PIPE_GATES),
        ("make targets resolve", "make -n agent-dryrun agent-server pipeline-test"),
        ("line caps respected", "wc -l on the files in the table"),
    ],
)

for name, text in [
    ("01-smart-home-open-slots.md", T01),
    ("02-docs-mcp-server.md", T02),
    ("03-doc-chat-agent-client.md", T03),
    ("04-agent-core.md", T04),
    ("05-stress-dry-run.md", T05),
    ("06-agent-server.md", T06),
]:
    (OUT / name).write_text(text)
    print("wrote", OUT / name, len(text.splitlines()), "lines")
