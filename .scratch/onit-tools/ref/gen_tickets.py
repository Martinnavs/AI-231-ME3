"""Generate the level-3 tickets from the reference overlay, so ticket code == reference code byte for byte."""
import ast, math, os, subprocess, sys
from pathlib import Path

R = Path("/mnt/School/PhD/AI231/AI-231-ME3-agy")
HERE = R / ".scratch/onit-tools"
O = Path("/tmp/claude-1000/-mnt-School-PhD-AI231-AI-231-ME3-agy/e3e00ee2-dc78-4726-921d-62420406350d/scratchpad/overlay")
sys.path.insert(0, str(HERE / "ref"))
from edits import EDITS

TICKETS = HERE / "tickets"
TICKETS.mkdir(exist_ok=True)
DATE = "2026-10-29"


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


def new_file_block(rel: str) -> str:
    return f"#### `{rel}` (new, verbatim)\n\n```python\n{(HERE / 'ref/skeleton' / rel).read_text()}```\n"


def edit_blocks(rel: str) -> str:
    edits = [e for e in EDITS if e[0] == rel]
    out = [f"#### `{rel}` (edit list, apply in order; each OLD block occurs exactly once at the moment you apply it)\n"]
    for i, (_, old, new) in enumerate(edits, 1):
        out.append(f"**Edit {i} of {len(edits)}.** Replace OLD with NEW.\n\nOLD:\n\n```text\n{old}```\n\nNEW:\n\n```text\n{new}```\n")
    return "\n".join(out)


def base(path_rel: str) -> int:
    return lines(R / path_rel)


def capnew(rel: str, slack: float) -> tuple[int, int]:
    n = lines(HERE / "ref/skeleton" / rel)
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
    inv = "\n".join(f"- `{rel}`: top-level " + inventory(O / rel if (O / rel).exists() else HERE / 'ref/skeleton' / rel) for rel in inv_files)
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


F01 = ["src/voice_agent/mcp_servers/shell_policy.py", "src/voice_agent/mcp_servers/code.py", "src/voice_agent/sandbox/runner.py",
       "tests/mcp_servers/test_shell_policy.py", "tests/mcp_servers/test_shell_tools.py", "tests/sandbox/test_host_readonly.py"]
SRC01 = " ".join(F01[:3])
TEST01 = "tests/mcp_servers/test_shell_policy.py tests/mcp_servers/test_shell_tools.py tests/sandbox/test_host_readonly.py"
P01 = [
    (f"uv run --offline --group laptop ruff check {' '.join(F01)}", "All checks passed!"),
    (f"uv run --offline --group laptop ruff format --check {' '.join(F01)}", "6 files already formatted"),
    (f"uv run --offline --group laptop mypy {SRC01}", "Success: no issues found in 3 source files"),
    (f"uv run --offline --group laptop pytest -q {TEST01}", run_proof(f"/mnt/School/PhD/AI231/AI-231-ME3-agy/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir={O} " + " ".join(str(O / t) for t in TEST01.split()))),
    ("uv run --offline --group laptop pytest -q tests/mcp_servers/test_code_server.py tests/sandbox", run_proof(f"/mnt/School/PhD/AI231/AI-231-ME3-agy/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir={O} {O}/tests/mcp_servers/test_code_server.py {O}/tests/sandbox")),
]
F04 = ["src/voice_agent/agent/subagent.py", "tests/agent/test_subagent.py"]
P04 = [
    (f"uv run --offline --group laptop ruff check {' '.join(F04)}", "All checks passed!"),
    (f"uv run --offline --group laptop ruff format --check {' '.join(F04)}", "2 files already formatted"),
    ("uv run --offline --group laptop mypy src/voice_agent/agent/subagent.py", "Success: no issues found in 1 source file"),
    ("uv run --offline --group laptop pytest -q tests/agent", run_proof(f"/mnt/School/PhD/AI231/AI-231-ME3-agy/.venv/bin/python -m pytest -q -p no:cacheprovider --rootdir={O} {O}/tests/agent")),
]

build(
    "01", "01-shell-tools", "shell tools in the sandboxed code server (bash, edit_file, grep)", "none",
    "The delegate model needs a shell, an exact-text editor and a capped grep inside a sandbox that limits everything it can reach. This ticket adds a read-only view of the host to the existing bwrap runner, a command policy, and three tools on the existing `code` MCP server.",
    [("src/voice_agent/mcp_servers/shell_policy.py", 1.1), ("tests/mcp_servers/test_shell_policy.py", 1.25),
     ("tests/mcp_servers/test_shell_tools.py", 1.25), ("tests/sandbox/test_host_readonly.py", 1.25)],
    ["src/voice_agent/sandbox/runner.py", "src/voice_agent/mcp_servers/code.py"],
    ["src/voice_agent/mcp_servers/shell_policy.py", "tests/mcp_servers/test_shell_policy.py", "tests/mcp_servers/test_shell_tools.py", "tests/sandbox/test_host_readonly.py"],
    """### E — Edges

| Edge | Behaviour |
|---|---|
| `bash` with a command `check_command` refuses | raises `ValueError(<one sentence>)`; nothing runs |
| `bash` while bwrap or systemd-run is missing | `ValueError("sandbox unavailable: ...")` (from `SandboxUnavailable`) |
| `bash` timeout above 120 or below 1 | clamped to 1..120 |
| `bash` writes outside `/sandbox`, reads `/home`, opens a socket | fails inside the sandbox (read-only host dirs, `/home` not mounted, `--unshare-all` without `--share-net`) |
| `edit_file` with `old` absent, repeated, or empty | `ValueError("old text must occur exactly once in <path>; it occurs N times")` |
| `edit_file` / `grep` path outside the root or a symlink | `ValueError("path outside sandbox root")` |
| `grep` no match | returns `"no matches"` |
| `grep` more than 50 lines, lines over 300 characters, output over 20 KiB | cut to those caps |

### A — Assumptions and how they were checked

- bwrap, systemd-run and prlimit work on this laptop: `preflight().problems == []` and a real `run_in_sandbox` of `python3 -c "print(1+1)"` returned `2`.
- `pytest` is not installed in the system Python (`/usr/bin/python3 -c "import pytest"` fails), so the sandboxed agent cannot run pytest; its tests use `unittest`.
- The runner's existing default (`host_readonly=False`) is unchanged: the existing sandbox and code-server tests still pass with the edits.

### R — Rules

Copy every code block byte for byte. The sandbox is the real fence; `check_command` is only the first one, so do not add patterns to it.""",
    P01,
    """flowchart LR
    A["agent (later ticket)"] -- "bash / edit_file / grep" --> C["code server: tools (changed)"]
    C -- "check_command (new)" --> S["shell_policy.py (new)"]
    C -- "SandboxPolicy host_readonly (new)" --> R["runner.build_command (changed)"]
    R -- "bwrap argv" --> B[("bwrap: host ro, /sandbox rw, no net")]""",
    "no change to `run_python`, `publish_site`, `_paths.py`, the speech or media servers, or any other existing test. No `serve` tool (a later ticket). No network access for the agent.",
    "yes — it lets a model run commands. Keep to the code as written; do not weaken or extend the policy, and do not change any bwrap flag.",
)

build(
    "04", "04-subagent-runner", "subagent runner: a model loops over MCP tools until it answers, with hard limits", "01 (only so the tool names exist at run time; no code dependency)",
    "A background model gets a task, calls the MCP tools in a loop (OpenAI-compatible chat completions), and returns a short summary. Hard limits (steps, seconds, tokens, dollars) and a spend ledger stop a runaway run. It is used for the model experiment and later for `delegate(task)`.",
    [("src/voice_agent/agent/subagent.py", 1.1), ("tests/agent/test_subagent.py", 1.25)],
    [],
    ["src/voice_agent/agent/subagent.py", "tests/agent/test_subagent.py"],
    """### E — Edges

| Edge | Behaviour |
|---|---|
| model answers without tool calls | `status="done"`, `summary` is its text |
| tool-call arguments are not a JSON object | `json_errors += 1`; the model receives `error: the arguments were not a JSON object` |
| the same tool call twice in a row | second is not run; `repeats += 1`; the model is told |
| tool result over 12,000 characters | cut with `…[truncated]`; errors are prefixed `error: ` |
| steps, seconds, tokens or dollars over a limit | status `step_limit`, `time_limit`, `token_limit`, `cost_limit` |
| ledger pool at 90% of its cap | status `budget`, no request sent |
| HTTP 429/5xx | one retry after 2 s; any other HTTP error ends the run with `status="error"`; the API key never appears in `summary` |
| billed `usage.cost` present | used as the cost; otherwise tokens times the model's per-million prices, with cached tokens at the cached price |

### A — Assumptions and how they were checked

- Tools come from `ToolRegistry` (`voice_agent.agent.mcp_clients`): `.tools` is a list of `ToolSpec`, `await .call(name, args)` returns a `ToolOutcome`. Read in `src/voice_agent/agent/mcp_clients.py`. The tests use a fake with the same two members.
- OpenAI-compatible tool calling worked on all ten endpoints in `.scratch/onit-tools/probe/results.jsonl`, including echoing the raw assistant message back (needed for Gemini thought signatures).

### R — Rules

Copy every code block byte for byte. No real HTTP in tests (they use `httpx.MockTransport`). Never read or print keys.""",
    P04,
    """flowchart LR
    T["task + model + api key"] --> L["run_task loop (new)"]
    L -- "chat/completions" --> M{{"model endpoint"}}
    L -- "tool call" --> G["ToolRegistry.call (existing)"]
    L -- "usd per step" --> D[("Ledger: spend.json (new)")]
    L --> Z["RunResult: status, summary, tokens, usd"]""",
    "no change to `mcp_clients.py`, the live agent, the pipeline, or any config. No CLI, no model price table, no key loading (the caller passes the key).",
    "no for the code itself; it handles an API key passed in by the caller. It must never print or log the key.",
)
