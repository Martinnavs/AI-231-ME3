# Context management for refinement dialogues: research and a lean plan (2026-11-12)

What this is: five search-only agy runs (axes: working memory, reference resolution, editing a deliverable the front agent cannot see, voice-specific problems and testing, prior-art architectures), the owner's session of 2026-11-12 08:39 as the case, and our code. Raw reports and the briefs are in `.scratch/context-research/` (git-ignored). Agy claims were spot-checked against the primary pages (Pipecat Flows `ContextStrategy`, Anthropic's "effective context engineering" page, Manus's context-engineering post: all say what the reports quote); the rest is cited as agy's and weaker.

## The case (what failed)
Five cities were researched and a page built. Then "the other two cities" and "their order" could not be resolved (the front agent never sees the page; the weather tool had failed for two cities and the page said "not available"); the agent re-searched instead of acting for three turns, drew Cebu from an unrelated weather question ten minutes earlier, and the final update rebuilt the page with a new three-city set (Manila, Caloocan, Taguig lost). A split request and a cut sentence were acted on as complete.

## What all five runs agreed on (the lean pattern)
| # | Idea | Evidence | Our version | Lines |
|---|---|---|---|---|
| 1 | The set of things being worked on is a first-class object with merge, not replace, semantics | LangGraph reducers, OpenAI Agents SDK local context vs history, Letta core memory, SOM-DST (carry over unless changed) | `chain.json` gets `items` (a list; `chain_update(items=...)` unions, `replace` is explicit), shown as "Working set: ..." in the [ACTIVE TASK] line | 30 |
| 2 | The front agent must know what the deliverable holds | Claude Code read-before-edit, SWE-agent, Aider repo map (about 1,000 tokens), Anthropic notes | the sub-agent ends each job with a `manifest.json` (items, columns, gaps); the delegate server stores it on the job row; the active-task line says "Page apps/x: items A, B, C; missing: Caloocan weather" | 40 |
| 3 | A refinement of an existing deliverable is routed to the delegate as a patch, not researched | task-owner and handoff patterns (OpenAI handoffs), agy's act-first policy, tau-bench style over-searching | one prompt rule plus the delegate boundary adds "Keep ALL of: <working set>. Operation: ..." to any update task | 15 |
| 4 | Check that an update kept what it had | agy axis 3 "entity preservation guard", our own `lint_pages` | after a job, items of the working set absent from the page text are added to the job summary ("the page lost Manila") | 15 |
| 5 | A failed tool must say so and retry cheaply | agy axes 1, 3, 5; Manus "keep errors in context" | `weather`/`nearby` geocoder: retry without "City"/"Municipality", then with the country; an error sentence instead of "not available" | 15 |
| 6 | "No, I mean ..." corrects the last action | agy axis 2 (third-turn repair) | prompt rule: after an action, "no, I mean X" redoes that action with X and makes no new lookup | 3 |
| 7 | Incomplete turns are held or joined | agy axes 1-5; our `complete_turn` exists (`--ask-to-add`, a text turn model) but the live session ran without it | a rule-based "ends on a conjunction or article" check as the default when no turn model is loaded; join with the next speech (the join in `follow_up` did not happen on 2026-11-12: unexplained) | 25 |
| 8 | Recite the task state at the end of the context | Manus `todo.md`, agy axis 5 | the [ACTIVE TASK] block already exists; move it to just before the user's message | 5 |
| 9 | Test it with replayed multi-turn scenarios | agy axis 4 (Wilson bounds), our `chain_eval` | scenario `refine_keeps_items` (build five items, "add X", "the other two", check items kept and no lookup on refinement turns) | 30 |

Not recommended for us (agy suggested; costs more than they give here): a conversational query-rewriting model call per turn, a vector or archival memory, per-turn history truncation by playback timestamps (needs TTS word timestamps), an optimistic pre-announcement with cancellable delegation (a larger behaviour change).

## Order (gain per risk, with the demo today)
1 + 3 + 5 + 9 first (the failure above, about 100 lines, all deterministic or prompt), then 2 and 4, then 7 and 6 and 8.

## Evidence strength and caveats
Convergent across five independent runs and three primary pages; the line counts are agy's and ours, not measured. The "small model follows a prompt rule" parts (3, 6, 8) need the replayed scenario to count: a live run costs about $0.03 per trial. Nothing here has been built yet.
