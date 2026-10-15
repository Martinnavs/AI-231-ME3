# Overnight queue (set 2026-10-15, user afk until 2026-10-16)

Orchestrator: the Claude session. Workers: agy (`handoff/dispatch.sh`) and Claude `developer` subagents (isolated worktrees).

## Order (each step starts when its Blocked-by tickets are merged to main)
| Ticket | Worker | After |
|---|---|---|
| 02 stage log | agy | running |
| 13 truth set | claude dev | running |
| 06 speech MCP (security) | claude dev | now |
| 14 bench analysis | agy | 02, 13 merged (03 done) |
| 07 home MCP | agy | 06 merged |
| 08 sandbox + code MCP (security) | claude dev | 06 merged |
| 09 MCP client manager | claude dev | 06, 07, 08 merged |
| security-auditor over 06 and 08 | claude security-auditor | after each merges; findings logged, not auto-fixed |

STOP LINE (needs the user): 05 and 10 (D1 unconfirmed; D3 Tavily edit to 10 pending), 11/12/15/16/19/20/18/17 (downstream of 05/10), any paid or authenticated API call, hardware checkpoints.

## Rules
- Merge to main only after re-running `make check` and the full `uv run pytest -q` in the worker's worktree myself, and confirming only the ticket's Files changed. Red gate or a worker BLOCKED/PARTIAL: one retry with a fix-request in the ticket log, then park the ticket (leave branch, do not merge) and carry on with unblocked tickets.
- Never: push, force, delete branches with unmerged work, spend on paid APIs, change contracts (a contract change = park).
- Secrets never printed. Fixes found by security-auditor are logged here, not applied unreviewed.

## Log
<<<<<<< HEAD
- 13 merged (worker gate re-run clean, 45 passed). Note for review: speech_bounds hangover_ms is a no-op; build_truth_set gained voice kw.
=======
>>>>>>> worktree-agent-a90e39355eff87083
- 06 merged (60 passed). Follow-ups: when 05 lands, confirm speech.py picks up the real SpeechBackendError from audio_engine._ws (currently local fallback). security-auditor over 06 pending.
