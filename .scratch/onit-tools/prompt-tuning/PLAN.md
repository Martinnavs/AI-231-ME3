# Prompt and tool-definition tuning for routing (pre-registered 2026-10-30, before any variant run)

Problems seen in the delegate dry runs (rows in `pipeline/out/agent_dryrun/`): `wiki_summary` and `web_answer` overlap (the model calls both); `web_answer`'s own example ("Who wrote Blood Meridian?") steers a Cormac McCarthy question away from `search_docs`, and one prompt made 11 `web_answer` calls after `search_docs`; "I'm cold." sets the thermostat (the owner's open bug); "Text Ben." asks first instead of calling `MESSAGE` since the delegate prompt change; duplicate calls (`PLAY_MUSIC` twice); the `say` text is repeated on all 11 tools.

Conditions (same model gemini-3.1-flash-lite, same tools, `--pi-url ''`, throwaway HOME so a delegate call cannot spend):
- **P0** the committed state.
- **P1** `system_v1.txt`: prompt only (Case B/C boundary, "once", no wiki_summary, feelings are not commands, a message to a phonebook contact is complete, each call once).
- **P2** P1 plus `tools_v2.json` via `dryrun --tool-text`: new descriptions for search_docs, web_answer (new example), wiki_summary (fallback only), and a shorter `say` text.

Packs per run: the 87-prompt pack (`agent_prompts.jsonl`), the delegate pack (21 prompts), the recheck pack (the 7 unstable prompts x6). Three repetitions per condition per pack, interleaved with the condition order rotated each repetition.

Metrics (computed by `analyze.py`, fixed now): (1) exact after treating `wiki_summary` as `web_answer` (the pack's knowledge expectations predate `web_answer`), repeated calls to one tool counted individually; (2) extra calls = calls beyond the expected number, summed; (3) false calls (a call when none was expected); (4) delegate pack: delegations made when expected, `delegate*` calls on negatives; (5) latency: first text and first tool call, p50 and p90, tool turns and no-tool turns, per condition over all runs.

Adopt a variant only if all hold against P0: alias-exact on the 87-pack not lower by more than 2 (of the pooled mean per run); extra calls lower; false calls not higher; delegate recall 100% and no delegate call on a negative; first-text p50 not more than 5% above P0 for tool turns and for no-tool turns (and not outside the spread seen between the P0 runs). If P1 and P2 both pass, take the one with fewer extra calls; ties go to the smaller change (P1).

# Round 2 (pre-registered 2026-10-30, after round 1's data, before any P3 run)

Round 1 adopted P1 (now the committed prompt). Two weaknesses remain under every round-1 condition: knowledge questions call `web_answer` and then `wiki_summary` every time (only the tool-description change in P2 fixed it), and simple arithmetic is sent to `web_answer`. P2 also broke "Set an alarm." and fixed "Text Ben.", both traced to the shorter `say` text, so round 2 keeps the original `say` text.

- **P1** = the committed default (system prompt `system_v1.txt`, no tool-text override).
- **P3** = `system_v3.txt` (P1 plus "including sums and everyday reasoning you can do in your head" in the last case) with `tools_v3.json` (new descriptions for search_docs, web_answer with a non-McCarthy example, wiki_summary as fallback only; no `say` change).
- Packs, settled harness, three repetitions, P1 and P3 running at the same time: the 87-pack (m), the delegate pack (d), the recheck pack (r), and a fresh held-out pack `agent_prompts_heldout2.jsonl` (g, 25 prompts written before any P3 run).
- Metrics: as in round 1 (alias-exact, extra calls, false calls, delegate recall and strays, first-text and first-call latency).
- **Adopt P3 over P1 only if all hold:** alias-exact not lower than P1 by more than 1 per run on each of m, r and g; extra calls lower on g and not higher on m and r; false calls not higher pooled; delegate recall 12/12 and no `delegate` call on a negative; first-text p50 not more than 5% above P1 for tool and no-tool turns; and no regression on the guard prompts a02 ("Set an alarm.", missing time) and b02 ("Text Ben."): exact count over all runs not lower than P1 by more than 2 of 21 (a02) and 21 (b02). Otherwise keep P1 and report.
