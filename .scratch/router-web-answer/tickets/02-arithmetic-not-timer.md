# 02: Retrain the web router so arithmetic never becomes a home action

Run on the cluster. Repo `AI-231-ME3`, branch `agy/interrupts` (pull first), working dir `finetune/`.
Status: RELEASED by the owner (2026-11-03). Report the bottom line first, say plainly what is the owner's decision,
never overstate.

## In one paragraph
Ticket 01 gave the router a `web_answer` call (adapter `martinnavs/granite-4.0-h-350m-router-kd-web-s0-unverified`).
It works: every changing-fact question in two held-out sets went to `web_answer`. One new error must be fixed by
training: **"What is 12 times 9?" -> `TIMER|12 times 9`**, a wrong home action (kd-s0 escalated it). Add
arithmetic examples, retrain with the same recipe, push a new adapter. That is the whole ticket.

## What is NOT in this ticket (decided, do not do)
- **Documents questions** (the 7 pack items about the sample McCarthy corpus that went to the web): solved on the
  laptop by a gate that checks the loaded documents' names, not by training. Do NOT add McCarthy (or any corpus)
  names, titles or characters to the data; the pack's McCarthy items stay a test of an unseen corpus.
- **Settled facts** going to `wiki_summary` / `web_answer`: accepted (the fast path). Do not change.
- Pipeline code (`pipeline/`), other model sizes, more seeds, `--include-eval`, hyper-parameters.

## Tasks
1. **Arithmetic seed** in `data/banks.py` + `data/generate.py`: spoken arithmetic -> `escalate|reasoning`.
   Cover sums, differences, products, quotients, percentages, squares/roots, with digits and number words
   ("what's 7 times 8", "eighteen plus twenty four", "what is 15 percent of 240", "divide 90 by 6",
   "how much is 3 squared"). Weight it like `s_wiki`.
2. **Contrasting timers**: numbers that ARE timers stay `TIMER|<duration>` ("set a timer for twelve minutes",
   "timer for 8 minutes", "remind me in 30 minutes" if that intent exists). Make sure every TIMER target in the
   data has a duration unit (minutes/seconds/hours); add a test that asserts it.
3. **Tests** in `tests/test_data.py`: no arithmetic example has a home intent target; every TIMER target has a
   duration unit.
4. **Data and training, the same recipe as ticket 01** (`finetune/README.md`, "Cluster session 2026-11-03"):
   ```bash
   python3 data/generate.py --out data/generated --n 30000 --seed 0
   python3 data/generate.py --out data/generated-s1 --n 40000 --seed 1
   python3 scripts/merge_distill_data.py data/generated data/generated-s1 data/distill
   GPU=<n> bash train/run.sh start h1b-lora-web2-s0 --model ibm-granite/granite-4.0-h-1b --data data/generated --out runs/h1b-lora-web2-s0 --seed 0 --batch 4 --accum 16
   GPU=<n> bash train/run.sh start kd-350m-web2-s0 --model ibm-granite/granite-4.0-h-350m --data data/distill --out runs/kd-350m-web2-s0 --seed 0 --batch 8 --accum 8 --teacher ibm-granite/granite-4.0-h-1b --teacher-adapter runs/h1b-lora-web2-s0/final
   ```
   Never GPU 6. The teacher must finish before the student starts.
5. **Push** the student adapter as `martinnavs/granite-4.0-h-350m-router-kd-web2-s0-unverified` (private).
   The laptop converts it to GGUF and runs the OnIt set, the pack and its held-out sets.

## Acceptance criteria (cluster side)
- [ ] `pytest` in `finetune/` green, including the new tests.
- [ ] Dev split: overall exact, `web_answer` recall and precision (ticket 01: 39/39, 39/49), arithmetic seed exact,
      and timer exact, each reported next to the kd-web-s0 numbers.
- [ ] Data counts per seed, before and after.
- [ ] Report: commands run, results table, spend ($0 expected), anything you could not do.

Laptop-side acceptance (not yours, for context): OnIt >= 75/77, pack >= 78/92, no arithmetic turn routed to a home
intent, changing facts still go to `web_answer`.

## Do not look at / copy from
`.scratch/web-prefilter/`, `.scratch/doc-routing-research/` (held-out test sets and their results), and do not
copy wording from `pipeline/config/eval/agent_prompts.jsonl` or `eval/onit_eval.jsonl` into the banks.

## Hard rules
- Never read or print API keys or anything under `~/.secrets`.
- This recipe needs no paid API. If anything would call Gemini, ESTIMATE the cost, tell the owner, wait for "go".
- Stop servers by PID (never `pkill -f`).
- Git: `git add` by explicit file name, commit by topic, trailers `Co-Authored-By: ...` and `Claude-Session: ...`;
  push only when the owner asks, never force, never push main.
- Format only the files you edit.

## Execution Log
(append dated entries below; never rewrite earlier ones)

### 2026-11-03 (cluster)
Bottom line: retrained; on the generator dev split arithmetic is 80/80 with 0 routed to a home action (previous model 10/80, 7 to a home action). Not run: GGUF, OnIt set, pack (laptop).
- Data: `ARITH_TEMPLATES` + `s_arithmetic` (weight 5), timer durations/phrasings, tests (every TIMER target has a unit). Beyond the ticket: 11 Pi device_status wordings, `s_pi` weight 4->6 (fixes the "what's the pi temperature" false positive).
- Counts seed 0 before->after: train 11356->11777, dev 1154->1209, test 1116->1191; distill train 21578->23016.
- Commands: as in the ticket; teacher `h1b-lora-web2-s0` (GPU 4, ~58 min), student `kd-350m-web2-s0` (GPU 4, ~2.1 h).
- Dev (1209): exact 97.5% vs 91.6%; web_answer tp/fp/fn 31/0/0 vs 31/8/0; arithmetic 80/80 vs 10/80; timer 83/83 both; pi 27/34 vs 18/34 (misses: "is the pi running okay" -> escalate|tools).
- Adapter: martinnavs/granite-4.0-h-350m-router-kd-web2-s0-unverified (private). Spend $0. Nothing pushed.
