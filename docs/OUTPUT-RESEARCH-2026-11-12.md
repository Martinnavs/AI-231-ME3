# Topic and output tracking: research and a lean plan (2026-11-12)

Five search-only agy runs (topic and intent tracking, output tracking, spoken output design, lean architecture and hooks, evaluation), the owner's sessions of 09:55 and 10:26 as the case, and our code. Raw reports and briefs: `.scratch/output-research/` (git-ignored). The reports converge strongly (the same six ideas lead all five); their line counts and thresholds (Jaccard 0.55, 600 ms, 25 s) are their guesses, not measurements. `conversation.item.truncate` (OpenAI Realtime) and Pipecat's context-aggregator placement are real mechanisms; the exact API quotes were not re-fetched (the OpenAI page is script-rendered).

## What happens today, in numbers (`scripts/output_audit.py`, new, free)
Over the four agent-server logs of 2026-11-12 (105 turns): **6** replies with two first-person status sentences that restate each other, **3** premature claims ("I have updated ..." in a turn that only started a job), **9** delegate calls within 25 s of another, **4** one- or two-word turns that started a lookup or job ("Mm-hmm." -> web_answer, "I'm sorry." -> delegate; "Title page" -> delegate is a legitimate refinement). The same script is the regression check: run it on a fixed set of logs before and after each change.

## What the research says, against what we already have
| Idea (agy) | We have | Verdict |
|---|---|---|
| Truncate the history to what was heard after a barge-in | the client adds the sentences begun so far (`history.add(question, spoken)`) | **have** at sentence level; word-level needs TTS word timestamps: skip |
| Defer the "say" sentence and drop it if the tool answers fast | `SAY_DELAY_S` 0.6 s; slow tools (weather, web_answer, search_docs, delegate) speak it at once, measured 1.3 s or more | **have**; the visible repeat is only that the say names the subject the answer names again: natural, not changed |
| Two-slot topic register (task vs side topic) | chain + side-question neutrality + finished task stays open (section 19) | **have** |
| Cancel and replace on a repair ("sorry, I mean ...") | merge of an overlapping job, same deliverable, follow-up queue; but a restated request with other wording and another folder starts a second and third job | **new**, high value |
| Drop perfect-tense claims while a job has only started | nothing | **new**, small |
| Restatement filter between sentences of one reply (say, then "OK, I am ...") | payload and source filters only | **new**, medium |
| Backchannel and fragment gate before any tool | `is_acknowledgement` guards delegate only | **extend** to every tool |
| Notice dedupe against what was just said | notices wait for a quiet moment (0.8 s) | **new**, small |
| Idempotency key per tool call, 20-25 s window | partly (jobs) | folded into the repair rule |

## Plan (each under about 40 lines; deterministic or one prompt line)
1. **Restated request replaces the running job** (problems 2, 6): a repair marker in the user's words ("sorry, I mean", "no, I mean", "I meant") plus a job started in the last 30 s cancels it; the new job starts and one fixed sentence is spoken; two delegate calls with the same normalised task inside 25 s are one.
2. **Claim guard** (3): in the turn that started a job, perfect-tense completion sentences ("I have updated/added/created ...") are dropped from the spoken stream, plus one prompt line (progressive tense until a result exists).
3. **Restatement filter** (2): within one reply, a first-person status sentence that overlaps the one before it (the audit's rule) is dropped; the job-start sentence is built from code, not by the model.
4. **Backchannel gate** (7): "mm-hmm", "uh-huh", "I'm sorry", "okay" and similar closed-class turns start no tool and no model call (unless the last assistant turn ended in an offer).
5. **Notice dedupe** (9): a finished-job notice that repeats most of what was said in the last minute is shortened to "Done.".
Not recommended: embedding or NLI similarity, an LLM judge for redundancy, per-word truncation, a second always-on model.

## Caveats
The audit counts are on 105 turns from four sessions, not a benchmark; the thresholds must be tuned on logs, not taken from the reports. Cutting sentences risks cutting content: the filters only touch first-person status sentences. Nothing in the plan has been built; the audit script and its tests are.
