# 03: Teach the router that a new request after an earlier one is independent (history regression)

Run on the cluster. Repo `AI-231-ME3`, branch `agy/interrupts` (pull first), working dir `finetune/`.
Report the bottom line first, say plainly what is the owner's decision, never overstate.

## In one paragraph
The ticket-02 adapter (`martinnavs/granite-4.0-h-350m-router-kd-web2-s0-unverified`) is as good as the live router on
single turns (OnIt 76/77, pack 79/92) but failed in a spoken session on a multi-turn case: the user and the router had
exchanged "I'm feeling cold" / a question back / "thirty-two degrees" (the router set the thermostat), then the user said
"Call <a name that is not in the phonebook>." and the router answered `TEMPERATURE|32 degrees` again: **a repeat of the
previous action, which is a wrong home action.** Replayed offline: kd-web2 repeats it when that history is present and is
right without it; the live kd-s0 is right with and without. Cause in the data: every multi-turn example is a **follow-up
that continues the previous turn** (`s_follow_up`, `s_ask_follow_up`, `s_doc_follow_up`: "and tomorrow", "make it
twenty minutes", "text them instead"). Nothing teaches that a new, unrelated request after an earlier action is
independent, so the student learned "with a history, vary the last call". Fix it with data, same recipe, new adapter.

## Related misroutes seen in the same session (all history-related, fix in the same retrain)
The wordings quoted below are **laptop test cases**: describe the pattern in your data with your own phrasings, never
copy these exact lines into the banks.
- After a web answer was cut off, the user said "It's okay." and the router repeated the earlier lookup
  (`web_answer|<the previous question>`) instead of a pleasantry.
- "Have you called <name> yet?" (a question about the past) became `CALL|<name>`.
- A stop word garbled by the recogniser ("wind storm") was answered by repeating it (`none|Wind storm.`).
The harness now rejects exact parrots, but the router should not produce them.

## What is NOT in this ticket (do not do)
- Pipeline code (`pipeline/`): done on the laptop (device_status, parrot check).
- Documents questions, McCarthy or any corpus names: handled in the harness, never in training.
- `web_answer`, `wiki_summary`, arithmetic, timers, hyper-parameters, other sizes, more seeds, `--include-eval`.

## Tasks
1. **Topic-switch seed** `s_topic_switch` (weight about 8, comparable to `s_follow_up`'s 14 split sensibly): a history of
   1-3 earlier exchanges, then an **independent** final request. Draw the history from the existing makers' (user,
   lines) pairs: a home action (no slot and slot), a temperature/timer/alarm exchange, an ask-then-answer exchange
   (`s_ask` + the answer, ending in a call), weather, time, a lookup (`web_answer`/`wiki_summary` lines), a chat reply, an
   escalation. Draw the final turn from an existing single-turn maker (`s_noslot`, `s_slot`, `s_contact`,
   `s_contact_missing`, `s_time`, `s_weather`, `s_web_answer`, `s_settled`, `s_arithmetic`, `s_defer`) with **exactly the
   target it has without history**. Make sure a large share has a history whose last call is a slot action (TEMPERATURE,
   TIMER, ALARM, BRIGHTNESS, VOLUME, SET_VOLUME-like intents) and a final turn of a different intent, and a share where the
   final turn is a contact that is **not in the phonebook** (`none|<name> is not in the phonebook.`) or one that is.
   The history must use the same phonebook as the final turn.
2. **Acknowledgement seed** `s_ack_after_anything`: a pleasantry or dismissal after a history of any kind ("okay",
   "alright", "fine", "never mind", "forget it", "no that's fine", "all good", "don't worry about it") -> `none|` with a short
   reply of at most 6 words ("Okay.", "No problem.", "Alright."). Histories include lookups (`web_answer`), home actions,
   escalations. Contrast: the same words as the answer to a question the router just asked ("Okay, 20 minutes") keep
   their existing follow-up target.
3. **Past-tense and yes/no questions about actions** `s_action_question` -> `escalate|other` (no call): "have you called
   <name> yet", "did you set the timer", "did the lights turn on", "is the alarm set", with and without history. Contrast:
   the imperative forms ("call <name>", "set a timer for ten minutes") keep their calls.
4. **Garbled fragments** `s_garble` -> `none|Sorry, I didn't catch that.` (this exact phrase): 1-3 word fragments with no
   recognisable intent, e.g. homophone garbles of short phrases and nonsense word pairs, in the style of the existing noise ("bucket on mu").
   Keep it small (weight about 2) and add contrasts so real short commands stay commands ("lights on", "lights off",
   "volume up", "next song", "stop the music" if those intents exist). Do NOT garble real intents in the training targets.
5. **Tests** in `tests/test_data.py`: in `s_topic_switch` the final turn's target equals the target the same maker gives
   without history; the final target is never equal to the last history line when the final user text differs in intent;
   `s_ack_after_anything` replies are at most 6 words; `s_garble` targets are exactly the fixed phrase; no seed contains
   corpus names (assert against a short list of the sample corpus's author, novels and character names kept in the test).
6. **Data and training, the same recipe as ticket 02** (`finetune/README.md`, "Cluster session 2026-11-03"):
   ```bash
   python3 data/generate.py --out data/generated --n 30000 --seed 0
   python3 data/generate.py --out data/generated-s1 --n 40000 --seed 1
   python3 scripts/merge_distill_data.py data/generated data/generated-s1 data/distill
   GPU=<n> bash train/run.sh start h1b-lora-web3-s0 --model ibm-granite/granite-4.0-h-1b --data data/generated --out runs/h1b-lora-web3-s0 --seed 0 --batch 4 --accum 16
   GPU=<n> bash train/run.sh start kd-350m-web3-s0 --model ibm-granite/granite-4.0-h-350m --data data/distill --out runs/kd-350m-web3-s0 --seed 0 --batch 8 --accum 8 --teacher ibm-granite/granite-4.0-h-1b --teacher-adapter runs/h1b-lora-web3-s0/final
   ```
   Never GPU 6. The teacher finishes before the student starts. Check the **teacher** on the new seeds before distilling:
   if the 1B teacher also repeats the previous action on topic-switch examples, say so (it changes the fix).
7. **Push** the student adapter as `martinnavs/granite-4.0-h-350m-router-kd-web3-s0-unverified` (private).

## Acceptance criteria (cluster side)
- [ ] `pytest` in `finetune/` green, including the new tests.
- [ ] Dev split exact reported **per new seed** (topic switch, ack, action question, garble) and overall, each next to the
      ticket-02 numbers (96.3 % on the 300-example subset); overall must not drop by more than 1 point.
- [ ] A held-out check you write yourself: 40 fresh multi-turn cases (history of a home action, final turn an unrelated
      request) generated with different names/phrasings from the training banks, run through the **student** with
      generation; report how many repeat or vary the previous call (target: 0).
- [ ] Data counts per seed, before and after; report (commands, results table, spend $0 expected, anything not done).

Laptop-side acceptance (not yours, for context): OnIt >= 75/77, pack >= 78/92, the earlier fresh sets unchanged
(20/20 changing facts to `web_answer`, 10/10 arithmetic escalated, 3/3 timers), and 30 fresh multi-turn cases with 0
repeated actions.

## Do not look at / copy from
`.scratch/` (all of it: held-out test sets and the spoken-test results), `out/sessions/`, `docs/stress/`, and do not copy
wording from `pipeline/config/eval/agent_prompts.jsonl` or `eval/onit_eval.jsonl` into the banks. Use your own
names and phrasings; the laptop tests with different ones.

## Hard rules
- Never read or print API keys or anything under `~/.secrets`.
- This recipe needs no paid API. If anything would call Gemini, ESTIMATE the cost, tell the owner, wait for "go".
- Stop servers by PID (never `pkill -f`); GPU 6 is off limits.
- Git: `git add` by explicit file name, commit by topic, trailers `Co-Authored-By: ...` and `Claude-Session: ...`;
  push only when the owner asks, never force, never push main. Format only the files you edit.

## Execution Log
(append dated entries below; never rewrite earlier ones)
