"""Generate the level-3 tickets from the reference overlay, so ticket code == reference code byte for byte."""
import ast, math, os, subprocess, sys
from pathlib import Path

R = Path("/mnt/School/PhD/AI231/AI-231-ME3-agy")
HERE = R / ".scratch/onit-tools"
O = Path("/tmp/claude-1000/-mnt-School-PhD-AI231-AI-231-ME3-agy/e3e00ee2-dc78-4726-921d-62420406350d/scratchpad/overlay2")
sys.path.insert(0, str(HERE / "ref2"))
from edits import EDITS

TICKETS = HERE / "tickets"
TICKETS.mkdir(exist_ok=True)
DATE = "2026-10-30"


def lines(p: Path) -> int:
    return len(p.read_text().splitlines())


def inventory(p: Path) -> str:
    tree = ast.parse(p.read_text())
    names = [f"`{n.name}`" for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)]
    return ", ".join(names)


def run_proof(cmd: str) -> str:
    env = dict(os.environ, PYTHONPATH=str(O / "src"), MYPYPATH=str(O / "src"))
    out = subprocess.run(cmd, shell=True, cwd=R, env=env, capture_output=True, text=True).stdout.strip().splitlines()
    import re
    return re.sub(r" in [0-9.]+s$", "", out[-1]) if out else "(no output)"


LANG = {".py": "python", ".toml": "toml", ".jsonl": "json"}


def new_file_block(rel: str) -> str:
    lang = LANG[Path(rel).suffix]
    return f"#### `{rel}` (new, verbatim)\n\n```{lang}\n{(HERE / 'ref2/skeleton' / rel).read_text()}```\n"


def edit_blocks(rel: str) -> str:
    edits = [e for e in EDITS if e[0] == rel]
    out = [f"#### `{rel}` (edit list, apply in order; each OLD block occurs exactly once at the moment you apply it)\n"]
    for i, (_, old, new) in enumerate(edits, 1):
        out.append(f"**Edit {i} of {len(edits)}.** Replace OLD with NEW.\n\nOLD:\n\n```text\n{old}```\n\nNEW:\n\n```text\n{new}```\n")
    return "\n".join(out)


def base(path_rel: str) -> int:
    return lines(R / path_rel)


def capnew(rel: str, slack: float) -> tuple[int, int]:
    n = lines(HERE / "ref2/skeleton" / rel)
    return n, math.ceil(n * slack)


def build(num: str, slug: str, title: str, blocked: str, objective: str, new: list[tuple[str, float]], edited: list[str],
          inv_files: list[str], recap: str, proofs: list[tuple[str, str]], diagram: str, nongoals: str, sec: str) -> None:
    rows = []
    for rel, slack in new:
        n, cap = capnew(rel, slack)
        rows.append(f"| `{rel}` | new, verbatim | **{cap}** (skeleton {n}) |")
    for rel in edited:
        after = lines(O / rel)
        rows.append(f"| `{rel}` | edit (list below) | **{after + 3}** (now {base(rel)}; exactly {after} after the edits) |")
    code = "\n".join(new_file_block(rel) for rel, _ in new)
    edits = "\n".join(edit_blocks(rel) for rel in edited)
    inv = "\n".join(f"- `{rel}`: top-level " + inventory(O / rel if (O / rel).exists() else HERE / 'ref2/skeleton' / rel) for rel in inv_files)
    proof_txt = "\n".join(f"{i}. `{cmd}`\n   Expected last line (ignore the timing): `{exp}`" for i, (cmd, exp) in enumerate(proofs, 1))
    text = f"""# Feature Spec: {num} — {title}

**Status:** ready
**Date:** {DATE}
**Blocked by:** {blocked}

{objective}

## Context

- **Objective:** {title}. Code is written and tested in a scratch copy; you transcribe it.
- **Role:** strict systems architect; you are transcribing an approved implementation.
- **Source:** `/mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/onit-tools/PLAN.md` and `EXPERIMENT.md`.
- **Repo:** the working directory you were given (a copy of `/mnt/School/PhD/AI231/AI-231-ME3-agy`).

| File | Action | Line cap |
|---|---|---|
{chr(10).join(rows)}

**Non-goals / touch nothing else:** {nongoals}

**Security-sensitive:** {sec}

## Diagrams

```mermaid
{diagram}
```

## RECAP

{recap}

## C — Contracts (the code; copy it)

{code}
{edits}

### Function inventory

{inv}

## P — Proof

Run from the repository root of your working copy. Run exactly these, in this order.

{proof_txt}

## Execution Log

(append dated entries below; never rewrite earlier ones)
"""
    (TICKETS / f"{slug}.md").write_text(text)
    print("wrote", slug, len(text.splitlines()), "lines")


PY = "/mnt/School/PhD/AI231/AI-231-ME3-agy/.venv/bin/python"
PPY = "/mnt/School/PhD/AI231/AI-231-ME3-agy/pipeline/.venv/bin/python"
F05A = ["src/voice_agent/agent/jobs.py", "src/voice_agent/mcp_servers/delegate.py",
        "tests/agent/test_jobs.py", "tests/mcp_servers/test_delegate_server.py"]
P05A = [
    (f"uv run --offline --group laptop ruff check {' '.join(F05A)}", "All checks passed!"),
    (f"uv run --offline --group laptop ruff format --check {' '.join(F05A)}", "4 files already formatted"),
    ("uv run --offline --group laptop mypy src/voice_agent/agent/jobs.py src/voice_agent/mcp_servers/delegate.py",
     "Success: no issues found in 2 source files"),
    ("uv run --offline --group laptop pytest -q tests/agent tests/mcp_servers/test_delegate_server.py",
     run_proof(f"{PY} -m pytest -q -p no:cacheprovider --rootdir={O} {O}/tests/agent {O}/tests/mcp_servers/test_delegate_server.py")),
]
PIPE = ["pipeline/src/voice_pipeline/notices.py", "pipeline/src/voice_pipeline/agent_tools.py", "pipeline/src/voice_pipeline/agent_server.py",
        "pipeline/tests/test_notices.py", "pipeline/tests/test_agent_server_notices.py", "pipeline/tests/test_agent_tools.py"]
PIPE_REL = " ".join(p.removeprefix("pipeline/") for p in PIPE)
OV = f"cd {O}/pipeline && MYPYPATH={O}/pipeline/src PYTHONPATH={O}/pipeline/src"
P05B = [
    (f"cd pipeline && uv run --offline ruff check {PIPE_REL}", "All checks passed!"),
    (f"cd pipeline && uv run --offline ruff format --check {PIPE_REL}", "6 files already formatted"),
    ("cd pipeline && uv run --offline mypy", run_proof(f"{OV} {PPY} -m mypy")),
    ("cd pipeline && uv run --offline pytest -q", run_proof(f"{OV} {PPY} -m pytest -q -p no:cacheprovider --rootdir={O}/pipeline tests")),
]

build(
    "05a", "05a-delegate-server", "delegate MCP server: a stronger model works on a hard task in a sandbox, in the background",
    "01 and 04 are already in the repo (run_task, the code server with bash, edit_file and grep)",
    "The voice agent hands big tasks (a website, hard math with code, an audit) to a stronger model. This ticket adds the worker as an MCP server in the root project: three tools (`delegate`, `delegate_status`, `delegate_cancel`), a job table on disk, a config file choosing the model (haiku-5.5 on OpenRouter, with the class Qwen server as fallback), and a notice file that the pipeline reads in ticket 05b.",
    [("src/voice_agent/agent/jobs.py", 1.1), ("src/voice_agent/mcp_servers/delegate.py", 1.1), ("config/delegate.toml", 1.0),
     ("tests/fixtures/delegate_notices.jsonl", 1.0), ("tests/agent/test_jobs.py", 1.25), ("tests/mcp_servers/test_delegate_server.py", 1.25)],
    [],
    ["src/voice_agent/agent/jobs.py", "src/voice_agent/mcp_servers/delegate.py", "tests/agent/test_jobs.py", "tests/mcp_servers/test_delegate_server.py"],
    """### E — Edges

| Edge | Behaviour |
|---|---|
| the task finishes within `wait_s` (default 6 s, at most 20 s) | `delegate` returns `{job, status, summary}`; no notice is written |
| the task is still running at `wait_s` | `delegate` returns `{job, status: "started", message}`; when it ends a line `{"job", "text"}` is appended to `notices.jsonl` |
| the default backend ends with `error` or `budget` | the fallback runs the task again in a new folder `<job>-<fallback>`; any other non-done status is final |
| a job ends other than `done` | job status `error`; the summary is the reason sentence from `REASONS` (or "something went wrong"); the notice reads "I could not finish jobN: <reason>." |
| `delegate_cancel` on a running job | its task is cancelled, status `cancelled`, no notice; with nothing running it answers "There is no running task to stop." |
| the server restarts while a job runs | on opening the table the job becomes `lost`; no notice |
| keys | read from files at run time (`key_file`, or `~/.secrets/onit/` for the class server); never in a result, a log line or the job table |

### A — Assumptions and how they were checked

- `pipeline/` is a separate project that cannot import `voice_agent` (its own pinned dependencies), so the worker must be an MCP server the pipeline starts, like `tavily_search`. Checked: no `voice_agent` import anywhere under `pipeline/src`.
- `McpClientManager`, `registry.subset`, `run_task` and `Ledger` behave as `bench/run_bench.py` uses them; that code ran 100+ real model runs through the same calls (haiku-5.5 39/39 on the limit set).
- The class server's `/models` answer has `data[0].id`; `doc_chat.qwen` provides `_read_secret_file` and `normalize_base_url` (read).
- bwrap works on this laptop (`preflight().problems == []`); the one test that needs it is skipped otherwise.
- The golden file `tests/fixtures/delegate_notices.jsonl` is the notice format; ticket 05b's tests read the same file.

### R — Rules

Copy every code block byte for byte. No real HTTP in tests (`httpx.MockTransport`) and no model calls. Never read or print `~/.secrets`. Do not start the server outside the tests.""",
    P05A,
    """flowchart LR
    F["flash-lite (later: pipeline, 05b)"] -- "delegate(task)" --> D["delegate server: Delegator (new)"]
    D -- "create / finish" --> J[("jobs.json (new)")]
    D -- "run_task (existing)" --> R["code server in /sandbox folder per job (existing)"]
    D -- "ended while nobody waited" --> N[("notices.jsonl: golden format (new)")]""",
    "no change to `subagent.py`, the `code` server, the sandbox runner, `mcp_clients.py`, `config/agent.toml`, or anything under `pipeline/` (ticket 05b). No `serve` tool, no web tools, no client change.",
    "yes — it holds API keys and starts a model that can run commands in a sandbox. Do not log or return a key, and do not change any limit, sandbox flag or policy entry.",
)

build(
    "05b", "05b-pipeline-notices", "the agent starts the delegate server and speaks the notices of finished jobs",
    "05a (its golden notice file `tests/fixtures/delegate_notices.jsonl` must exist: the tests here read it)",
    "The live agent gains the delegate tools and says when a delegated job ended. It starts the delegate MCP server as an optional server, reads the notice file of ticket 05a, and after the answer to the next turn appends one spoken sentence per finished job. No client change: the sentence is sent as a flagged `text` message like the model's own `say` lines. The system prompt gains one case describing when to delegate.",
    [("pipeline/src/voice_pipeline/notices.py", 1.1), ("pipeline/tests/test_notices.py", 1.25), ("pipeline/tests/test_agent_server_notices.py", 1.25)],
    ["pipeline/src/voice_pipeline/agent_tools.py", "pipeline/src/voice_pipeline/agent_server.py", "pipeline/config/eval/system_agent.txt", "pipeline/tests/test_agent_tools.py"],
    ["pipeline/src/voice_pipeline/notices.py", "pipeline/tests/test_notices.py", "pipeline/tests/test_agent_server_notices.py"],
    """### E — Edges

| Edge | Behaviour |
|---|---|
| a job ended since the last turn | after the turn's text and before `{"done": true}` the server sends `{"text": "Update on your earlier request: <text>", "flush": true}` once per notice |
| no notices, a cancelled turn, or a turn that is not `ok` | nothing is sent |
| the notice file does not exist, or ends in a half-written line | nothing is read until a complete line is there |
| the server starts with an old notice file | the queue starts at its end: old notices are not repeated |
| the delegate server cannot start | `standard_servers` lists it as optional, so the agent starts without the delegate tools (a warning is printed) |
| `--notices ''` | no notices are read |

### A — Assumptions and how they were checked

- The protocol is one request per connection, so the server cannot speak between turns; the notice is appended to the next turn's reply. Speaking while idle needs a client change, left to a later ticket.
- `on_say` sends `{"text": ..., "flush": true}`, which the client speaks at once (read in `agent_server.handle` and `src/doc_chat/agent_client.py`).
- The ruff import-order rule (I001) cannot be checked in the scratch copy; the Proof runs it inside the real project.
- Pipeline baseline before the edits: ruff clean, mypy 11 source files, 113 tests passing.

### R — Rules

Copy every code block and apply every edit byte for byte. Do not start the agent server or any MCP server outside the tests. Do not touch `doc_chat` or anything under `src/`.""",
    P05B,
    """flowchart LR
    S["delegate server (05a)"] -- "notices.jsonl: golden format" --> Q["NoticeQueue (new)"]
    Q -- "pop() at each turn end" --> H["AgentServer.handle (changed)"]
    H -- "text + flush: Update on your earlier request" --> C["client speaks it (unchanged)"]
    T["standard_servers (changed)"] -- "starts" --> S""",
    "no change to `doc_chat`, `src/`, the router, the hedge, the fast path or `dryrun.py`; no new MCP server code; no client change; no new tests beyond the listed ones.",
    "no — it reads a text file and starts an optional server. Do not run the agent server.",
)
