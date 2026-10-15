# Runner protocol: sequential Sonnet execution of the RECAP tickets

Every ticket points here under **Shared Context**. Read this once per ticket, before the ticket itself.

## Dispatch (for whoever runs the loop)

1. Take the lowest-numbered ticket whose `Status:` is `ready-for-agent` and whose `Blocked by` tickets are all `done`.
2. Dispatch one `developer` agent (model: sonnet) with exactly this prompt:
   > Read `.scratch/project2-voice-agent/RUNNER.md`, then execute ticket `.scratch/project2-voice-agent/tickets/<NN>-<slug>.md`. Repo root: `/mnt/jfs_hpc/home/anthony.martin.navarez/ME3`.
3. When it returns, check the ticket's last Execution Log entry:
   - `DONE` → set `Status: done`, go to step 1.
   - `BLOCKED` or `PARTIAL` → stop the loop and bring it to a human. Do not retry automatically more than once; a second failure always escalates.
4. After 16 the order is 19, 20, 18, then 17 (the Blocked-by lines enforce it). Ticket 17 goes to `test-engineer`, not `developer`. After 17, run `tech-lead` review over the whole diff (and `security-auditor` over tickets 06 and 08, which are security-sensitive).

## Rules for the developer agent

**Order inside a ticket.** Read the ticket fully → verify every Assumption the moment you first rely on it → implement the Contracts exactly → write the Proof tests → run the gate suite → append to the Execution Log → reply tersely.

**RECAP is a contract.**
- **Edges:** each row needs code that produces that behavior and, where the Proof table names it, a test.
- **Contracts:** signatures, types and schemas are binding. Changing one is a *material deviation*: stop, mark the ticket `BLOCKED`, explain what and why.
- **Assumptions:** verify each one before relying on it, and record how (command + result) in the Execution Log. A failed assumption is a material deviation.
- **Proof:** every row must exist and pass. An unrun or failing Proof row means the ticket is not done.
- **Minor deviations** (naming of private helpers, internal structure, an extra guard) are fine: one line each under "Deviations" in your log entry.

**Gate suite** (all must pass before reporting DONE):

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q <the test files this ticket owns>
```

Run the full `uv run pytest -q` once at the end, to catch breakage of earlier tickets. Tests marked `live` or `pi` are skipped by default and you must never enable them.

**Free, no pause:** reading anything; writing inside the repo; running tests, linters, mypy; `git add`/`git commit` on the working branch (one commit per ticket, message `<NN>: <ticket title>`); `uv add` of exactly the packages listed in the ticket's *Dependencies/license*; WebFetch of vendor or library documentation.

**Stop and report BLOCKED instead:** any package not listed in *Dependencies/license*; any call to a paid or authenticated API (Deepgram, Cartesia, Anthropic, Groq, the class DGX server, Open-Meteo), including "just one quick check"; writing outside the repo; `sudo`, system packages, shell rc, systemd units outside `deploy/`; `git push`, reset, clean; an architecture choice the ticket does not settle.

**Secrets.** Never print, log, commit or paste an API key. Keys come from the environment (`.env` loaded by `voice_agent.config`, which is git-ignored). Tests use fakes.

**Comments.** Default to none. Only for a hidden constraint or a surprising invariant.

**Library APIs drift.** Pipecat, the MCP SDK and the vendor APIs change often. Before writing against one, check the installed version (`uv pip show <pkg>`) and its real import paths (`uv run python -c "import ..."`), or WebFetch the docs. Don't code from memory. For any Anthropic SDK code, invoke the `claude-api` skill first and follow its Python docs.

## Execution Log entry format (append to the ticket, never rewrite)

```markdown
### <YYYY-MM-DD> developer: DONE | BLOCKED | PARTIAL
- Assumptions verified: A1 `<command>` → <result>; A2 ...
- Proof: P1 ✅ P2 ✅ ... (command: `uv run pytest -q tests/...` → N passed)
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (N passed, M skipped)
- Files: <paths>
- Deviations: none | <one line each>
- Commit: <short sha>
- Notes for reviewer: <only what isn't obvious from the diff>
```

Reply to the orchestrator with the status line, the Proof checkmarks and "details in ticket NN". Nothing more.
