# Guardrails for every latency ticket

These tickets make slow answers feel fast: a rule-based fast path for well-known commands in the agent server (`pipeline/`, Pipecat project; run its commands from `pipeline/`), the tools' `say` sentence and tool event, and timed filler lines in the doc_chat client. The code is already written in each ticket: transcribe it, do not extend it. Smaller and exactly as written beats more robust.

Every ticket ships a code skeleton, and the skeleton is the implementation: transcribe it, and adapt only what is needed to make it run. Log each adaptation as a minor deviation in the ticket. If a skeleton looks wrong, stop and log a material deviation; do not redesign it.

## Hard limits

- **Line caps** are given per file in each ticket and count physical lines. Over cap: stop and log a material deviation. Never compress lines to fit; report instead.
- **Function inventory** is given per file. The finished file contains exactly those top-level functions and classes. Adding, renaming, or splitting one is a material deviation.
- **Verbatim:** every code block in a ticket is copied byte-for-byte, blank lines included.
- **Tests:** exactly the listed tests, nothing more.

## Forbidden — do not add

1. Type/shape checks (`isinstance`, schema validation) on values our own code returns, our own constants, or our own config. 
2. Re-validation of arguments the caller controls.
3. `try`/`except` blocks beyond the skeleton's.
4. New constants, classes, dataclasses, enums, registries, or allowlists.
5. Count pre-checks or recounts.
6. Normalization the skeleton doesn't do (`.upper()`, `.strip()`, numeric coercion of slots).
7. Docstrings longer than the skeleton's, comments that narrate the code, new logging or prints.
8. Edits outside the ticket's file table, "while I'm here" cleanups, formatting churn.
9. Tests for behaviour the skeleton doesn't have.
10. Reading, printing or logging API keys or anything under `~/.secrets`.
11. Network calls of any kind (no LLM calls, no `uv add`, no `uv sync` without `--offline`, no `pip install`). Never start the speech server, the agent server or a live doc_chat session.
12. Launching subagents or background tasks.

## Allowed without asking

- Fixing a real bug in a skeleton (one-line minor deviation).
- Running the ticket's Proof commands, from the repository root.

## Gate commands

Each ticket lists its own Proof and gate commands; run them from the repository root. Never run `ruff format` on files outside your ticket's file table.

## When blocked

Missing dependency, failing command, or a contract mismatch with another ticket's landed output: report the exact error in the ticket's Execution Log and stop. Do not install packages, change environment, or work around it. Git is read-only for you: status/diff/log only, no commits.

## Known failure

`tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents` needs a speech server on 127.0.0.1:8765 and fails without one (`SystemExit: 2`) before any of these tickets. Ticket 03's Proof deselects it; do not try to fix it.
