# Spoken test v3: runbook (2026-11-04)

What it tests: the router with `web_answer` (candidate kd-web2; set `ROUTER_MODEL=router-350m-kd-s0` for the old one),
the call policy, the documents-vs-web race, grounded Gemini, Kitten voice, and the Pi (loudspeaker, device tools,
echo gate, barge-in). Laptop mic, Pi loudspeaker, no hedging (`--hedge -1`, as in the typed runs).
Script: `docs/stress/spoken-test-v3.md` (39 turns, ~20 min). Nothing here is committed.

## Order (each step is one command, from the repo root)
1. `.scratch/spoken-v3/status.sh` : cable, Pi ping, ports (read-only).
2. `.scratch/spoken-v3/up.sh` : router, speech server (loads Parakeet, ~30-60 s), agent server. Laptop only. If the Pi tools were not up yet, run `agent_restart.sh` after step 3 (the agent leaves them out otherwise).
3. **Owner's go for the Pi.** `.scratch/spoken-v3/pi_up.sh` : compares the session token (hashes only), starts the Pi
   speaker and device tools (both stop by themselves after 2 h).
4. `.scratch/spoken-v3/status.sh pi` : all five services up, Pi temperature.
5. The live loop, as ONE line from the owner's own shell (it needs the microphone): `! bash .scratch/spoken-v3/run.sh`
   Say the lines of the script in order. Ctrl-C ends the session.
6. `.scratch/spoken-v3/down.sh pi` : stops everything by PID, laptop and Pi.

Note: up.sh and agent_restart.sh no longer pass `--notices ''`; the agent server reads the default `~/.local/state/voice_agent/delegate/notices.jsonl`, so a finished delegate job is announced on the next turn's reply.

## Logs (where each thing ends up)
| What | Where |
|---|---|
| per-turn audio, transcripts, timings, echo-guard trace | `out/sessions/spoken-v3-<MMDD-HHMM>/` (path in `.scratch/spoken-v3/last_session`) |
| per-turn agent row: router lines, policy reason, `race` {docs, doc_s, winner, rejected}, calls, first-text/end | `pipeline/out/agent_server/<timestamp>.jsonl` |
| router, speech server, agent server stdout | `.scratch/spoken-v3/logs/{router,speech,agent}.log` |
| Pi speaker and device tools | on the Pi: `/tmp/pi_speaker.log`, `/tmp/pi_device.log` (copy them with scp after the run) |
| Google searches (ledger) | `~/.local/state/voice_agent/web_answer_ledger.jsonl` (new rows = searches billed) |

## After the run
`STRESS_SCRIPT=docs/stress/spoken-test-v3.jsonl python3 scripts/score_stress.py out/sessions/<session> pipeline/out/agent_server/<log>.jsonl`
and `uv run --group laptop python scripts/session_report.py out/sessions/<session>`.

## Cost
About 9 web lookups (up to 9 Google searches, $0.014 each) + 6 document calls + about 15 Gemini turns: roughly $0.15, at most $0.20.
