# 01: Teach the 350M router a `web_answer` call, then retrain and export it

Run on the cluster. Repo: `AI-231-ME3`, branch `agy/interrupts` (or a branch cut from it), working dir `finetune/`.
Owner: a PhD student. Report the bottom line first, say plainly what is their decision, never overstate.

## Why
The local router (`granite-4.0-h-350m` + LoRA `martinnavs/granite-4.0-h-350m-router-kd-s0`, Q4_K_M, llama-server on
the laptop) never sends a question to the web. Tested 2026-11-03 on 10 new changing-fact questions (UK PM, Bitcoin
price, F1 2026 champion, latest iPhone, ...): 0/10 picked a lookup, 9 gave `escalate|tools`, 1 a wrong
`convert_time`. Cause: the training data labels web questions `escalate|tools` (`data/banks.py` DEFER["tools"],
DEFER_COMPOSE + WEB_QUERIES) and `wiki_summary` only covers 26 encyclopedia topics.

Web was deferred originally because it was multi-step (search, then summarise). That no longer holds: the agent's
`web_answer` tool (grounded Gemini, Tavily and Wikipedia as fallbacks) takes the whole question and returns a
finished one-or-two-sentence answer that the router path speaks as is. The router only has to recognise "this
needs current information" and pass the question through. Goal: those turns skip the hosted model's ~0.7 s
decision step.

Mistakes are asymmetric: a missed web question falls back to Gemini (today's behaviour, no worse); a false
`web_answer` costs ~2 s and one paid search ($0.014) but still answers. Home commands, weather and time must not
drift into it.

## Confirmed design (owner, 2026-11-03)
1. A NEW router call `web_answer|<query>` (one free-text argument: the user's question as a complete question).
   Keep `wiki_summary|<topic>` for settled encyclopedia topics, unchanged.
2. Relabel the deferral examples that are single current-fact questions from `escalate|tools` to `web_answer`.
   Keep `escalate|tools` for: multi-step or compare/plan requests ("find cheap flights to Cebu and pick the best"),
   "open this page" (`fetch_content`), how-to / explain-at-length requests, anything about the user's files or
   documents, and the McCarthy corpus (`search_docs` is deferred as before).

## Tasks
1. `spec/tools.py`: add `Tool("web_answer", ..., (Arg("query"),), "info", example="web_answer|Who is the current
   Senate President of the Philippines?")`, local (the router emits it). The pipeline loads this file directly
   for its grammar (`pipeline/src/voice_pipeline/router.py:69`), so keep the GBNF rule-name rules (a test checks).
   Leave `web_search` as an OnIt tool the router never emits. Update `data/agent_tools_snapshot.json` with the
   agent's real `web_answer` schema: name `web_answer`, one required string `query`, description:
   "Look up a factual question on the web and get a short answer to say: people, places, films, facts, current
   events, how things work. Not for Cormac McCarthy (use search_docs). One call per question; write `query` as a
   complete question, for example 'Who painted the Mona Lisa?'. Say the answer as it is."
2. `data/banks.py` + `data/generate.py`: a new seed `s_web_answer` with varied changing-fact questions: office
   holders, sports results and champions, prices and exchange rates "now/today", latest/newest/next product or
   software versions and events, records, rankings, "did X happen yet", election results. Mix direct ("who is
   the current ...") and indirect wording ("has anyone broken the marathon record this year", "how much is a
   dollar in pesos today"). Target = `web_answer|<the question>`. Move single current-fact items out of the
   deferral banks (e.g. "any news about typhoons today", "the price of rice today", "the latest python
   release") into this seed; keep the multi-step ones deferred. Add hard negatives so the boundary is learned:
   settled facts with time words ("who was the first president of the Philippines", "what year did WW2 end"),
   weather with "today/now" (stays `weather`), time ("what time is it now", stays `get_current_time`),
   currency/time conversion that is a clock question (stays `convert_time`), home commands with "now".
   Give the new seed a sampling weight comparable to `s_wiki`. Add generator tests in `tests/test_data.py`.
3. `eval/onit_eval.py`: the OnIt set's `web` items (w01-w10) are scored "must escalate" by `defer_expect`
   (`DEFER_KINDS`). Change it so a single current-fact `web` item accepts `escalate` OR `web_answer`
   (w04 "current version of FastAPI", w02 "news about the peso today" style), and `fetch_content`/how-to items
   still need `escalate`. Report how many `web` items went to `web_answer` as its own number.
4. Data: regenerate with the SAME recipe that produced `kd-s0` (find its command/log on the cluster: `runs/`,
   shell history; if it used `--teacher gemini`, see the spend rule below). Same seed 0, same `--n`, same
   train.py hyper-parameters as kd-s0, base `ibm-granite/granite-4.0-h-350m`. Do NOT use `--include-eval`.
   If the kd-s0 recipe cannot be found, stop and ask the owner; do not guess a different recipe.
5. Train `350m-kd-web-s0` with `GPU=<n> bash train/run.sh start ...` (never GPU 6), export: merge, GGUF f16,
   Q4_K_M (steps in `finetune/README.md` "Convert and evaluate").
6. Evaluate Q4_K_M with llama-server (the eval runner sends the grammar): `python eval/onit_eval.py router-ft
   --url ...` on the OnIt set and `--set pack`, same settings as the kd-s0 rows (8 threads, greedy, one run).
7. Push the adapter to the Hub as `martinnavs/granite-4.0-h-350m-router-kd-web-s0` (private) only if the
   acceptance criteria below pass; otherwise report and stop.

## Acceptance criteria
- [ ] `pytest` in `finetune/` green (spec, data, train utils), including the new tests.
- [ ] OnIt set: no regression beyond 1 item against kd-s0 (76/77) on everything except the re-scored `web` items.
- [ ] 87-prompt pack: no regression beyond 1 item against kd-s0 (79/92); list every item whose result changed.
- [ ] No pack item that is home, weather, time, chat, McCarthy or a settled `wiki_summary` item now emits
      `web_answer` (list them if any).
- [ ] Generator dev split: `web_answer` precision and recall reported separately (expect optimistic numbers;
      the laptop has an unseen held-out set that is the real test).
- [ ] Latency on the cluster CPU reported next to kd-s0's (0.11 / 0.15 s p50 / p95).
- [ ] Report: commands run, data counts per seed before/after, any Gemini spend, results table.

## Non-goals (do not do)
- Pipeline wiring (`run_routed`, `INFO_TOOLS`, `reason_unusable` in `pipeline/`): done on the laptop afterwards.
- Changing `wiki_summary`, the home intents, or the hyper-parameters.
- Training H-1B or other sizes, more seeds, or `--include-eval`.
- Writing your own changing-fact eval set from the laptop's held-out questions: do not look in
  `.scratch/web-prefilter/` (that set must stay unseen by training).

## Hard rules
- Never read or print API keys or anything under `~/.secrets`; code reads them at run time.
- Gemini spend (`--teacher gemini` or anything else paid): ESTIMATE the cost first, tell the owner the number,
  wait for their "go". Run sequentially, no retries beyond the harness's own. The live agent's credit ran out
  once from unbudgeted runs (`docs/POSTMORTEM-2026-10-30-gemini-credit.md`).
- GPU 6 is off limits. Do not leave servers running; stop them by PID (never `pkill -f`).
- Git: `git add` by explicit file name, commit by topic, trailer lines `Co-Authored-By: ...` and
  `Claude-Session: <url>`; push only when the owner asks, never force, never push main.
- Format only the files you edit (`ruff format <file>`).

## Execution Log
(append dated entries below; never rewrite earlier ones)
