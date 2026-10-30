# Guardrails for every onit-tools ticket

These tickets add shell tools to a sandboxed MCP server and a subagent runner (a model loop over MCP tools with hard limits). The code is already written in each ticket: transcribe it, do not extend it. Smaller and exactly as written beats more robust.

Every ticket ships the full code of each new file and an edit list for each changed file. That code is the implementation: transcribe it, and adapt only what is needed to make it run. Log each adaptation as a minor deviation in the ticket. If the code looks wrong, stop and log a material deviation; do not redesign it.

## Hard limits

- **Line caps** are given per file in each ticket and count physical lines. Over cap: stop and log a material deviation. Never compress lines to fit; report instead.
- **Function inventory** is given per file. The finished file contains exactly those top-level functions and classes (edited files: the existing ones plus the listed new ones). Adding, renaming, or splitting one is a material deviation.
- **Verbatim:** every code block in a ticket is copied byte-for-byte, blank lines included. Edit lists are applied literally: each OLD block replaced by its NEW block, in order.
- **Tests:** exactly the listed tests, nothing more.

## Forbidden — do not add

1. Type/shape checks (`isinstance`, schema validation) on values our own code returns, our own constants, or our own config.
2. Re-validation of arguments the caller controls.
3. `try`/`except` blocks beyond the ones in the code.
4. New constants, classes, dataclasses, enums, registries, allowlists or blocklist entries.
5. Count pre-checks or recounts.
6. Normalization the code doesn't do.
7. Docstrings or comments beyond the code's own, new logging or prints.
8. Edits outside the ticket's file table, "while I'm here" cleanups, formatting churn. Never run `ruff format` on files outside the ticket's file table.
9. Tests for behaviour the code doesn't have.
10. Reading, printing or logging API keys or anything under `~/.secrets`.
11. Network calls of any kind (no model API calls, no `uv add`, no `uv sync` without `--offline`, no `pip install`). Never start the agent server, the speech server or any MCP server outside the tests.
12. Launching subagents or background tasks.
13. Any change to a bwrap flag, a sandbox limit, or an entry of the command policy. These are security decisions the owner has made.

## Allowed without asking

- Fixing a real bug in the code (one-line minor deviation).
- Running the ticket's Proof commands, from the repository root.

## When blocked

Missing dependency, failing command, or a contract mismatch: report the exact error in the ticket's Execution Log and stop. Do not install packages, change the environment, or work around it. Git is read-only for you: status/diff/log only, no commits.
