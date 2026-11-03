# The router's web lookups and the documents-vs-web race (2026-11-03)

**Bottom line.** The local router now sends changing-fact questions straight to `web_answer`
(skipping Gemini's ~0.7 s decision), and a harness race answers questions about the loaded
documents from the documents. The candidate router `martinnavs/granite-4.0-h-350m-router-kd-web2-s0-unverified`
scores as well as the live kd-s0 on the old tests (OnIt 76/77, pack 79/92) and passed a 12-turn typed run.
**Not yet the live router** (the owner decides); no spoken session yet. Estimated overall median end of speech
to first sound unchanged at about 1.5 s; web answers about 3.4 s (grounded Gemini is the tail).

## Why
`--builtin-search` (Gemini's own search in the main request) failed its dry run: the model searched changing
facts 0-1 times in 4-6 (`.scratch/search-research/COMBO.md`). The other way to skip the decision step is the
local router, but kd-s0 sent 0/10 changing-fact questions to a lookup: it was trained to escalate web
questions (`finetune/data/banks.py`, deferral banks). Web was deferred because it was multi-step; with
`web_answer` (one call, a finished spoken answer) it is not.

## The router (cluster tickets, `.scratch/router-web-answer/tickets/`)
| Model | OnIt | Pack | Changing facts -> web_answer | Arithmetic not a home action | Router p50 |
|---|---|---|---|---|---|
| kd-s0 (live) | 76/77 | 79/92 | 0/10 | yes | 0.13 s |
| kd-web (ticket 01) | 76/77 | 77/92 | 20/20 | no: "12 times 9" -> `TIMER` | 0.18 s |
| **kd-web2 (ticket 02)** | 76/77 | 79/92 | 20/20 | 10/10 | 0.13 s |

Owner decisions: settled facts going to `wiki_summary`/`web_answer` are fine (about 1 s); the McCarthy corpus is
only a sample of "the user's documents", so its names are never put in the training data; documents questions are
handled in the harness. Known kd-web2 regression: pack b05 "Call Gary." (not a contact) -> `CALL|Gary`; the
smart-home tool refuses non-contacts, so no call is placed. Held-out test sets live in `.scratch/web-prefilter/`
and `.scratch/doc-routing-research/` and are deliberately not committed.

## The harness (`pipeline/src/voice_pipeline/call_policy.py`, `doc_answer.py`, commit 47597f3)
- `call_policy` holds every rule about the router's calls; new: `web_answer` accepted, TIMER/ALARM values must
  be a duration or time, a router chat reply to a question goes to Gemini while documents are loaded.
- **Race:** documents loaded + router lookup -> the web lookup and a documents answer start together: the
  `search_docs` passages and one gemini-3.1-flash-lite request that answers with a verbatim quote or says NONE.
  The documents answer is spoken only if its quote is in the passages and its numbers and names are too;
  otherwise the web result is (2.5 s cap on the documents side). Each turn logs `race` = {docs, doc_s, winner,
  rejected}.
- Rejected alternatives, measured: agy's upper-bound-normalised BM25 gate (19/36 and 13/36 false gates at its
  0.30 threshold); a name gate (missed name-less questions, false hits on common words); "documents loaded ->
  no router lookups" (loses the router path on ~80 % of question turns).

## Measurements (all 2026-11-03)
| Test | Result |
|---|---|
| Replay, 3 corpora incl. an invented unseen one, 165 calls, 2 runs | documents questions answered from the documents 10-11/12 per corpus; others answered from the documents 0-1/47-50 (the one: Moby Dick -> Melville, true); doc call median 0.77-0.82 s |
| Adversarial (false premises, near misses, absent facts), 16 | 15 NONE, 1 right answer; no wrong answer, so the checks had nothing to stop |
| Live typed, kd-web, 10 turns | documents won 2/3 (one rejected by a check, the web answered); web turns unchanged |
| Live typed, kd-web2, 12 turns | all 7 pre-registered rules passed: home 0.0-0.17 s, sums 0.93 s (escalated), web 2.06-2.45 s, documents wins 1.08-1.09 s, weather 0.67 s (server side) |
Spend: about $0.21 in all (replays $0.054 incl. one unapproved repeat from a script import, adversarial $0.003,
live runs about $0.15).

## Estimate against the benchmarks (not measured; sums of separately measured medians)
| Turn | Estimate, end of speech to first sound |
|---|---|
| home, chat (router) | ~1.25 s |
| sums (router -> Gemini) | ~2.0 s |
| documents win | ~2.3 s |
| web, router path | ~3.4 s (the "looking it up" line at ~2.05 s) |
Overall median about 1.5 s (inside real phone-call medians 1.30-1.74 s); p90 about 3.4-3.7 s (phone p95
1.77-2.26 s). Web answers are about 0.5 s later than in the 10-29 stress test because grounded Gemini (33/40
right) replaced Tavily (~0.7 s slower per search). Next: make grounded Gemini faster.
