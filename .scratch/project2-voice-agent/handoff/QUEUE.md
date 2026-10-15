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
- 13 merged (worker gate re-run clean, 45 passed). Note for review: speech_bounds hangover_ms is a no-op; build_truth_set gained voice kw.
- 06 merged (60 passed). Follow-ups: when 05 lands, confirm speech.py picks up the real SpeechBackendError from audio_engine._ws (currently local fallback). security-auditor over 06 pending.
- 02 merged (agy; full suite green). 14, 07, 08 dispatched.
- security-auditor on 06: no High; 2 Med (R2-1 TOCTOU, R2-2 decompression size). Fix-request logged in ticket 06; developer dispatched (attempt 1). PARKED for the human: config default `media_root` lives inside `sandbox_root` (config.py, ticket 01 contract), which makes R2-1 exploitable once 08's sandbox can write there; also R5-8 (disk cap), R5-9 (mcp floor). Ticket 05 got a new requirement for R4-7.
- 08 merged (119 passed, 5 skipped). The 5 bwrap isolation tests are pi-marked and UNRUN: sandbox confinement is unverified until the Pi checkpoint. security-auditor over 08 pending.
- 06 security fixes merged (attempt 1 of 2 sufficed; new tests failed pre-fix 10/17, pass post-fix). Re-audit of the fix itself not yet run: do it with the tech-lead review.
- security-auditor on 08: 2 High (R1: publish_site/write_file/read_file races), 4 Med, 9 Low. Fix-request logged in ticket 08 (attempt 1); ticket 16 got the static-server hardening requirement. PARKED for human: media_root inside sandbox_root (code masks it in bwrap but the config default still overlaps); all bwrap isolation claims unverified until the Pi.
- 07 merged (agy; 137 passed, 6 skipped; P4 real-ME2-app test skipped per ticket since fastapi isn't a dep: unverified against the real dashboard). Resolved docs/MCP-SERVERS.md conflict by keeping both sections. 09 waits for the 08 security fix; 14 still running.
- 14 merged (agy; per-kind filler_rate missing, logged as R3 note in ticket 14). 08 security fixes merged (attempt 1 sufficed; 21 new tests failed pre-fix; 2 of them have no regression value). Follow-ups: docs/MCP-SERVERS.md not updated for the 08 hardening (outside the fix-request's files); oom_killed falls back to a forgeable exit code if user@UID.service/memory.events is unreadable; resolve_inside now refuses a symlinked root (also affects speech media_root); L2/L4/L8/L9 not done; re-audit of 06 and 08 fixes not yet run.
- 09 merged (177 passed, 6 skipped). run_python never exercised (no bwrap). Deviation: one asyncio task per server instead of a shared AsyncExitStack. QUEUE COMPLETE: next tickets (05, 10) need D1; everything after depends on them. Waiting on the human.
- 2026-10-16 review fixes: D, B, C, H merged (main 76d060d, 195 passed/6 skipped). A (sandbox/code, R1 blocker) running. Re-audit of B: 0 High, 1 Med (M1: root symlink check covers only last component; exploitable only if media_root moves deeper than sandbox_root/media), 5 Low (L1 EACCES leaks abs path, L2 OSError in load_config leaves raw exc/URL in stderr log, L3 redaction misses url-encoded/truncated key, L4 __context__ keeps raw text, L5 voice content unvalidated). Dispatched B2 for M1, L1, L2, L3, L5; L4 deferred (no leak on FastMCP 1.30). Still waiting on human: H1-H6, D1.
