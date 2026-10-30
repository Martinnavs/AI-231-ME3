# Postmortem: the Gemini prepaid credit ran out during prompt tuning (2026-10-30)

**Status:** written 2026-10-30. Blameless in tone, but the decisions below were the assistant's, and it says so.

## Summary

Prompt-tuning dry runs against `gemini-3.1-flash-lite` used up the key's prepaid credit. The API now answers every request with `402 Payment Required: Your prepayment credits are depleted`. That key is also the live voice agent's front-line model, so the agent's Gemini turns fail until the owner adds credit; the local router still answers the turns it handles itself (not re-tested). The dry runs were never budgeted, counted or announced, because they were treated as free tests.

## Impact

- **Live agent:** Gemini-backed turns fail from about 15:43 on 2026-10-30 until credit is added. The November demo is not at risk if credit is restored in time; nothing else in the repo is damaged.
- **Money:** about 5,074 turns and 9,469 requests in one day. At 2.5k-3.5k input tokens a request and the $0.25 per million list price, **$6-9** (an estimate: no usage was recorded, caching discounts would lower it). The actual figure is on the AI Studio billing page.
- **Lost work:** round 2 of the tuning is incomplete (repetition 3 of the delegate and recheck packs is empty for both conditions).

## Timeline (laptop clock, 2026-10-30)

| Time | Event | Requests (about) |
|---|---|---|
| 12:04-12:13 | First dry runs: baseline against the delegate build, delegate pack | 755 |
| 12:17-12:27 | Recheck of one prompt, three conditions, twelve runs | 542 |
| 12:36-13:29 | **Round 1, three conditions x three repetitions x three packs**, interleaved | 3,031 |
| 13:33-13:38 | Found the harness flaw (late tool calls counted against the next prompt); probes of the fix | 223 |
| 13:45-14:26 | **Round 1 repeated** with the fixed harness, three processes at once | 3,023 |
| 15:05-15:50 | **Round 2**, two processes at once | 1,891 |
| 15:43:16 | First `402` in a log (`TP3_r3`, repetition 3) | |
| 15:51 | Assistant noticed empty runs, probed, read the 402 and reported it | 4 |

## Root causes

1. **No budget, no ledger for dry runs.** The delegate benchmark had a hard cap and a ledger (OpenRouter $0.23 of $3, Gemini bench $0.12 of $1.50, both honoured). The dry runs used the same Gemini key and had neither. The mental model was "tests are free"; the key is shared with production.
2. **No cost visibility.** The dry run records timing and calls, not tokens or dollars, so the spend could only be estimated afterwards.
3. **The experiment was sized by statistics, not by cost.** Three conditions x three repetitions x four packs was chosen for statistical comfort; the request count was never computed. Each full condition-repetition is about 360 requests.
4. **About a third of the requests were wasted by an unvalidated harness.** A turn ended at the first speakable result, so a late call landed in the next prompt's row. It was found only after the first full round (3,031 requests, 32% of the total) had been run and analysed; that round was discarded. A self-check that rows line up would have cost a few requests.
5. **Parallelism.** Two or three processes at once raised the burn rate and meant the spend could not be watched as it grew.
6. **Silent failure, no early stop.** An exhausted credit shows up as `empty` turns and a message-less "Unknown error". The dry run marked the turns and carried on, and the wait loops watched only for completion, so nobody looked at the content of the results until the analysis.
7. **Consent without a price.** The owner agreed to round 2 ("sure") without being told it would cost roughly 1,900 requests. The assistant had the information to say so and did not.

## What went well

- The pre-registered rule was written before any variant ran, and was applied as written (P3 was not adopted although it looked better).
- The harness flaw was reported and its data discarded, not blended in.
- After the failure only clean paired runs were used; the report states how many repetitions that left.
- The 402 was reported to the owner the moment it was understood, with the cost estimate and the assistant's part in it; Gemini use stopped.
- The ledgered spend (OpenRouter, Gemini bench) stayed far inside its caps, which shows the control works where it exists.
- The experiment also found a real bug (two delegate servers on one state folder crashed each other), now fixed with a test.

## Near misses in the same session (no harm, caught)

| What | How it was caught |
|---|---|
| A secret scan that did not run (zsh passed the file list as one name; the output still said "clean") | Noticed the warning, re-ran in bash on exactly the committed files |
| `pkill -f` matched the assistant's own shell (twice) | Exit code 144; processes found and stopped by PID |
| A check that swapped in the old file and then "restored" a backup taken after the swap, leaving the fix out of the working tree | `grep getpid` returned 0; fix re-applied and the stash list confirmed empty |

## Action items

| # | Action | Owner |
|---|---|---|
| 1 | Add credit to the Gemini key (or confirm the live agent's fallback behaviour meanwhile) | owner |
| 2 | Use a separate Google project and key with its own spending cap for experiments, so tests cannot drain the live agent's credit | owner (assistant can write the steps) |
| 3 | `dryrun`: print an estimate of requests and dollars before starting; refuse more than 500 requests without `--yes-spend`; count requests as it goes; stop after 5 empty turns in a row | assistant |
| 4 | Pre-flight probe (2 requests) at the start of every batch, and a request ledger like `bench/spend.json` for Gemini dry runs | assistant |
| 5 | Validate a harness on a small pack before the full run (rows line up, no late calls, calls match the prompt) | assistant |
| 6 | Put a budget line (requests and dollars) in the pre-registration of any experiment, and say it when asking for a go | assistant |
| 7 | Run conditions one after the other, or behind one throttle, not as parallel processes | assistant |
| 8 | Finish round 2 (the missing repetitions) only after items 1-3 | both |

## Not known

- The exact spend and the time of depletion (only the first logged 402, 15:43:16, is known; it is an upper bound on when the balance reached zero).
- Whether the live agent was used between 15:43 and now.
- Whether the local router alone gives acceptable behaviour for the live agent while Gemini is down.

## Related, not part of the incident

The agent has no per-turn cap on repeated `web_answer` calls (one prompt sometimes makes 6-7), and a second tool call in a later round is lost after the client is told "done" (about 1% of turns). Both are recorded in `.scratch/onit-tools/EXPERIMENT.md`.

## Follow-up (same evening)

Credit was added and the first test after the incident ran under the new controls:

| Action item | Status |
|---|---|
| 1 Add credit | done by the owner |
| 3 Estimate first, cap, stop after empty turns | done in `dryrun` (`--max-usd`, shared ledger, stop at 90%, abort after 5 empty prompts, refuses a run whose high estimate does not fit) with tests |
| 4 Pre-flight probe and a request ledger | done: a 2-turn probe through the same ledger measured real tokens before the runs |
| 5 Validate a harness on a small pack | the settle fix has a test; a probe now checks the credit and the token counts |
| 6 Budget line in the pre-registration | done for round 3 ($0.25 in `PLAN.md`) |
| 7 Sequential runs | done: P1 then P3, one process at a time |
| 2 Separate Google project and key for experiments | open (owner) |

Result of the first controlled test: round 3 (72 turns) cost $0.146 against a $0.25 cap (ledger, an upper bound). The guard's first estimate was low (68 requests predicted, 82-95 used, because a turn takes 2.5 requests, not 1.9); the constant was corrected from the measurement. Failed requests are billed to the ledger at the worst-case rate even though a rejected request costs nothing, so a failed probe slightly overstates spend; that is the safe direction.
