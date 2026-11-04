# 03 v2: Teach the router that a new request after an earlier one is independent (history regression), plus Pi, delegate and statement routing

Run on the cluster. Repo `AI-231-ME3`, branch `agy/interrupts` (pull first), working dir `finetune/`.
Report the bottom line first, say plainly what is the owner's decision, never overstate.

**Revision 2 (2026-11-04, before any training of v1):** tasks 1-4 are unchanged; tasks 5-7 (Pi contrast, delegate requests,
statements) are new, from a second spoken test; tasks 8-10 are the old 5-7 renumbered; the acceptance checks are extended.
Do not train until you have read the whole ticket, and generate the data with all seven seeds in one go.

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

## Added in revision 2: what the second spoken test showed (same history cause, plus three coverage gaps)
As above, the quoted wordings are **laptop test cases**: describe the pattern with your own phrasings and never copy these lines.
- A bare "set a timer" produced a timer for a number the user never said (copied from an alarm in the history).
- "What's the Pi temperature?" (heard as "pie") produced `get_volume`: a Pi turn after volume turns drags the tool.
- "Set the speaker to <N> percent", three times in a row after a status question, produced `device_status` instead of
  `set_volume`; the harness read out "the speaker is connected" and never set the volume.
- "Run ping and give me the latency" (and a garbled fragment of it) produced `device_status`: the router has no network
  tool and should escalate.
- "Check the status of the <game/build> I asked you to make" produced `web_answer` (the real game, then an unrelated
  celebrity). Requests to run something in a sandbox, to build an app, to check a background job and to fetch its
  results belong to the hosted model's delegate tools (`escalate`).
- A comment with no question ("it's so recent") after a lookup produced `web_answer`, and the raw comment was searched.

## What is NOT in this ticket (do not do)
- Pipeline code (`pipeline/`): done on the laptop (the call-policy guards: device_status gating, which question a lookup
  gets, question-form-only lookups, numeric slots grounded in the user's words). They do not replace this training.
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
5. **Pi contrast seed** `s_pi_contrast` (weight about 6, next to `s_pi`): the Pi has three tools and they must not bleed into
   each other. "set / change / make / put the speaker (volume) to N percent" (digits and number words, "the speaker",
   "the volume", "the Pi's volume") -> `set_volume|N`; asking the level ("how loud is the speaker", "what volume is it at")
   -> `get_volume`; Pi health (temperature, memory, uptime, "is the speaker connected / plugged in", "how is the Pi
   doing") -> `device_status`, including the homophones and mishearings of "Pi" ("pie", "raspberry pie") in every kind of Pi turn;
   network, ping, latency, connection speed, "check the internet" -> `escalate|tools` (the router has no
   network tool). Generate every kind both without history and after a history made of Pi turns (a volume exchange, a
   status question) and of home actions, so one Pi turn never drags the next Pi turn to the same tool.
6. **Delegate-request seed** `s_delegate_request` (weight about 4): "run <a command> in the sandbox / in the background and
   tell me <what>", "create / write / build a <game | app | script | website> (in the background)", "check the status of my
   <job | build | task | the game | the script>", "is it done yet", "are the results ready", "what did the background job
   find", "cancel that job". Targets use the existing reasons: building something -> `escalate|code`, a multi-step job ->
   `escalate|workflow`, running / status / results / cancel -> `escalate|tools`. Include a version where the user names no
   job ("is it done yet") after a history that contains such a request. Contrast, unchanged: real-world current-fact
   questions that happen to use the words "status", "results" or "done" ("what is the status of the Philippine peso",
   "are the election results in") keep `web_answer`.
7. **Statement seed** `s_statement` (weight about 3): reactions and comments that are neither a question nor a command ("that's
   recent", "interesting", "I see", "wow, that's a lot", "so that's the one") after a history of any kind (lookups, home
   actions, escalations) -> a short `none|` of at most 6 words ("Right.", "Glad that helps.", "Okay."); never a lookup, a
   home call, a repeated call or an escalate. Contrast: noun-phrase and wh- queries without a question mark ("tallest
   mountain in the world", "who is the current president of the philippines") stay lookups, and the answer to a question the
   router just asked keeps its follow-up target.
8. **Tests** in `tests/test_data.py`: in `s_topic_switch` the final turn's target equals the target the same maker gives
   without history; the final target is never equal to the last history line when the final user text differs in intent;
   `s_ack_after_anything` and `s_statement` replies are at most 6 words and never a call or a lookup; `s_garble` targets are
   exactly the fixed phrase; in `s_pi_contrast` every `device_status` example is free of volume / percent / ping / latency
   words and every "set ... percent" example has a `set_volume` target with that number; `s_delegate_request` targets are all
   `escalate`; no seed contains corpus names (assert against a short list of the sample corpus's author, novels and
   character names kept in the test).
9. **Data and training, the same recipe as ticket 02** (`finetune/README.md`, "Cluster session 2026-11-03"):
   ```bash
   python3 data/generate.py --out data/generated --n 30000 --seed 0
   python3 data/generate.py --out data/generated-s1 --n 40000 --seed 1
   python3 scripts/merge_distill_data.py data/generated data/generated-s1 data/distill
   GPU=<n> bash train/run.sh start h1b-lora-web3-s0 --model ibm-granite/granite-4.0-h-1b --data data/generated --out runs/h1b-lora-web3-s0 --seed 0 --batch 4 --accum 16
   GPU=<n> bash train/run.sh start kd-350m-web3-s0 --model ibm-granite/granite-4.0-h-350m --data data/distill --out runs/kd-350m-web3-s0 --seed 0 --batch 8 --accum 8 --teacher ibm-granite/granite-4.0-h-1b --teacher-adapter runs/h1b-lora-web3-s0/final
   ```
   Never GPU 6. The teacher finishes before the student starts. Check the **teacher** on the new seeds before distilling:
   if the 1B teacher also repeats the previous action on topic-switch examples, say so (it changes the fix).
10. **Push** the student adapter as `martinnavs/granite-4.0-h-350m-router-kd-web3-s0-unverified` (private).

## Acceptance criteria (cluster side)
- [ ] `pytest` in `finetune/` green, including the new tests.
- [ ] Dev split exact reported **per new seed** (topic switch, ack, action question, garble, Pi contrast, delegate request,
      statement) and overall, each next to the ticket-02 numbers (96.3 % on the 300-example subset); overall must not drop
      by more than 1 point.
- [ ] A held-out check you write yourself, run through the **student** with generation, with different names/phrasings from
      the training banks: 40 multi-turn cases (history of a home action, final turn an unrelated request; target: 0 repeat
      or vary the previous call) and 30 more split across the new seeds (12 Pi contrast incl. "set ... percent" after a
      status question, 10 delegate requests, 8 statements; report the wrong ones one by one).
- [ ] Data counts per seed, before and after; report (commands, results table, spend $0 expected, anything not done).

Laptop-side acceptance (not yours, for context): OnIt >= 75/77, pack >= 78/92, the earlier fresh sets unchanged
(20/20 changing facts to `web_answer`, 10/10 arithmetic escalated, 3/3 timers), 30 fresh multi-turn cases with 0
repeated actions, and fresh sets for the new seeds (12 Pi, 10 delegate requests, 10 statements) with none routed to a
wrong tool.

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
