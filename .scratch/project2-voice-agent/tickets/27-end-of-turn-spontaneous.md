# 27: End-of-turn hold on spontaneous speech, laptop mic

**Repo:** /mnt/School/PhD/AI231/AI-231-ME3-agy
**Blocked by:** None
**Status:** ready-for-agent (needs the human to record)

## Why
The pause hold (0.64 s) is the largest fixed cost in the 2.4-2.6 s you-stopped-to-sound time. The rules in `scripts/eot_replay.py` were compared on read phrases on the Pi mic. They must be compared on spontaneous speech (hesitations, restarts, long questions) on the laptop mic before changing the hold.

## Files
- `scripts/eot_replay.py` (rules take a recording from `doc_chat --record`), `docs/SMART-TURN.md` / `docs/HANDOFF.md` (results), `config` value for the hold

## Action
Human records about 10 minutes of natural questions for the doc-chat corpus (two sessions). Replay all rules in `eot_replay.py`, report split-phrase rate and delay p50/p95 per rule with n stated. Select a hold; do not pick by the data it was tuned on (hold out one session). Smart Turn failed on this mic earlier, so it is a comparison row, not the plan.

## RECAP
### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | replay is deterministic on a committed 30 s fixture | `uv run pytest -q tests/scripts/test_eot_replay.py` |

## Acceptance Criteria
- [ ] Chosen rule cuts at most 1 in 20 phrases on the held-out session
- [ ] Hold change measured live on 10 turns

## Non-Goals
- Adding a model to the Pi

## Security-sensitive
no

## Execution Log
