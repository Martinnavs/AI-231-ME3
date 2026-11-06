# 04: Teach the router negation, Filipino commands, "play <target>", commands with no referent, and job requests

Run on the cluster. Repo `AI-231-ME3`, branch `agy/interrupts` (pull first), working dir `finetune/`.
Report the bottom line first, say plainly what is the owner's decision, never overstate.

## In one paragraph
The live router is `kd-web3-s0` (ticket 03; passed the laptop's held-out retest 60/62). A laptop probe of 99 hard phrasings
found it acts wrongly on a few families of wording: it **carries out negated commands**, does not understand **Filipino
imperatives**, reads **every "play ..." as music** (games and apps included), **guesses a target** for commands that refer to
nothing, and sends **job questions to a web lookup**. The laptop now has call-policy rules (2026-11-06) that send these turns to
the hosted model, so they are no longer acted on wrongly; but each costs 0.5-1 s more than a router answer. This retrain is
to let the router answer them itself (or escalate them on its own). Same recipe as ticket 03, new adapter.

**Revision 2 (2026-11-06, before any training):** tasks 1-6 are unchanged; tasks 7-8 are new (a confirmation after the
hosted model's offer, and fixed ack/statement replies, from ticket 03's own note); old tasks 7-9 are now 9-11, and the
acceptance checks are extended. Read the whole ticket before generating data, and generate all seeds in one go.

## What the laptop saw (patterns; the wordings are laptop test cases)
Describe each pattern in your data with your own phrasings; never copy wording from the laptop's test material (see the
"Do not look at" section).
- A negated home command ("do not <action> ...") produced the action itself.
- Filipino / Taglish imperatives (the polite `paki-` prefix, `-in mo`, `-an mo` forms of on, off, open, louder, quieter, call,
  wake) produced `none|Sorry, I didn't catch that.`; a Filipino question about the time produced the weather tool, one about
  the weather produced a lookup.
- "Dim the lights" produced LIGHT_OFF; "go back" (a song) produced NEXT; "resume / continue the music" produced STOP.
- "Play <a game, an app, the thing you made, it>" produced PLAY_MUSIC (15 of 24 game wordings).
- "Play <an artist / a genre / a song>" produced bare PLAY_MUSIC: the router line protocol has no music slot, so the artist
  is lost. The hosted model now fills an optional music slot, so these should escalate.
- A command with no referent and no history that resolves it ("set it to <N>", "do that again") produced a guessed action
  (TEMPERATURE N; LIGHT_ON).
- "Is the <game / app / script> done?" produced `web_answer`; "run a <listing / command> in the sandbox" sometimes produced a
  status check instead of a new job (the hosted model's delegate tools; the router should escalate both).

## What is NOT in this ticket (do not do)
- Pipeline code (`pipeline/`): done on the laptop (call-policy rules W1-W5 and the V1 "play" rule). They stay; this training
  makes the router agree with them, it does not replace them.
- A music slot in the router's line protocol: PLAY_MUSIC stays slot-free in `spec/`; targets with a named artist escalate.
- A new home intent (no PREVIOUS, no RESUME): use the existing ones as below.
- Misspelled place names: the laptop's speech server repairs them before the router sees the text.
- Hyper-parameters, other sizes, more seeds, `--include-eval`, documents or corpus names.

## Tasks
1. **Negation seed** `s_negated` (weight about 4): negated home commands in English and Filipino ("don't / do not / no need to
   / never / huwag / wag + an action", "stop <verb>ing ..." where it is not a request to stop the music) -> `none|` with a
   short reply of at most 6 words ("Okay, I won't.", "Alright, leaving them."). Contrasts, unchanged targets: the plain
   imperatives keep their calls; "stop", "stop the music", "stop playing" keep STOP; "never mind" keeps the ack reply.
2. **Filipino and Taglish command seed** `s_filipino` (weight about 6): Tagalog and Taglish forms of the existing home commands
   and questions with **exactly the target the English form has**: lights on/off (`buksan`, `patayin`, `paki-on`, `paki-off`,
   `ilaw`), volume up/down (`lakasan`, `hinaan`), call/message (`tawagan`, `i-text`, with phonebook and not-in-phonebook
   names), alarm (`gisingin mo ako ...`, with the time in the slot form of `HOME_SLOT_FORMS`), timers, the time (`oras`),
   the weather (`panahon`, with and without a place), music (`patugtugin`, `ituloy`). Short forms of 3 words count too.
   Contrasts: a Filipino sentence that is not a command (a statement, a greeting) keeps the statement / ack targets.
3. **Near-miss seed** `s_near_miss` (weight about 3): "dim / lower the lights" with a level -> `BRIGHTNESS|<N> percent`,
   without a level -> `none|<a short question about the level>`; "go back / previous song / play the last one again" ->
   `none|<at most 8 words saying it can only skip forward>`, never NEXT; "resume / continue / unpause the music" ->
   PLAY_MUSIC, never STOP or PAUSE. Contrasts: "lights off", "next song", "pause the music" unchanged.
4. **Play-target seed** `s_play_target` (weight about 4): "play / can I play / let me play / open / launch <a game or app
   name, the game you made, the clone, it>" -> `escalate|tools`; "play <an artist, a band, a genre, a song title, a named
   playlist>" -> `escalate|tools`. Contrast, unchanged: bare music ("play music", "play some music", "play something",
   "put some music on", "can you play a song") -> PLAY_MUSIC. Use your own game, app and artist names (Filipino and
   international).
5. **No-referent seed** `s_no_referent` (weight about 3): "set it to <N>", "do that again", "same as before", "make it
   higher", "turn that one off" with **no history** -> `none|<a short question asking what it refers to>`. Contrast: the same
   words after a history that resolves them keep the existing follow-up targets (`s_follow_up`), and "set it to <N> degrees"
   keeps TEMPERATURE.
6. **Job-request seed** additions to `s_delegate_request` (weight +2): "is the <game / app / script / build> done / ready / finished",
   "did the <job> finish", "how is the <thing you are making> going" -> `escalate|tools` (never `web_answer`); "run / do / give
   me a directory listing / list the files / run <a command> in the sandbox" -> `escalate|tools`. Contrast, unchanged:
   current-fact questions using "done", "finished", "ready" about the world keep `web_answer`.
7. **Confirmation-after-offer seed** `s_confirm_offer` (weight about 3): a history whose last assistant turn is an offer or a
   question from the hosted model (an `escalate` turn in the history, followed by an assistant reply ending in "?" or an offer
   such as "Want me to open it?", "Would you like me to make one?", "Shall I start it?") and then a short confirmation or
   refusal ("yes", "sure, go ahead", "okay do it", "open it in a tab", "no thanks", "oo", "sige") -> `escalate|other`.
   The router cannot act on an offer it did not make. Contrasts, unchanged: the same words after the **router's own** question
   ("To how many degrees?" then "twenty-four") keep the follow-up target; the same words with no offer before them keep the
   ack target.
8. **Fixed ack and statement replies** (ticket 03's note, "Fix in the next data round"): `s_ack_after_anything` and
   `s_statement` draw their reply from one fixed phrase per kind ("Okay." for an ack, "Alright." for a statement), not from a
   random list, so strict exact match can reach 100%. Keep the at-most-6-words rule.
9. **Tests** in `tests/test_data.py`: `s_confirm_offer` targets are all `escalate|other` and its contrasts keep their targets;
   ack and statement replies are the one fixed phrase each; `s_negated` targets are never a home call and replies are at most 6 words; every
   `s_filipino` example's target equals the target of its paired English form; `s_near_miss` never produces NEXT for a
   backward request or STOP/PAUSE for a resume; `s_play_target` targets are all `escalate|tools` and the bare-music contrasts
   all PLAY_MUSIC; `s_no_referent` targets are `none|` questions ending in "?"; no seed contains corpus names (the list kept
   in the test from ticket 03).
10. **Data and training, the ticket-03 recipe** (`finetune/README.md`, "Cluster session 2026-11-04"), run names `web4`:
   ```bash
   python3 data/generate.py --out data/generated --n 30000 --seed 0
   python3 data/generate.py --out data/generated-s1 --n 40000 --seed 1
   python3 scripts/merge_distill_data.py data/generated data/generated-s1 data/distill
   GPU=<n> bash train/run.sh start h1b-lora-web4-s0 --model ibm-granite/granite-4.0-h-1b --data data/generated --out runs/h1b-lora-web4-s0 --seed 0 --batch 4 --accum 16
   CUDA_VISIBLE_DEVICES=<n> .venv-kernels/bin/python scripts/cache_teacher_logits.py --teacher ibm-granite/granite-4.0-h-1b --teacher-adapter runs/h1b-lora-web4-s0/final --data data/distill --out data/distill/teacher_top128.pt
   CUDA_VISIBLE_DEVICES=<n> .venv-kernels/bin/python train/train.py --model ibm-granite/granite-4.0-h-350m --data data/distill --out runs/kd-350m-web4-s0 --seed 0 --batch 8 --accum 8 --teacher-cache data/distill/teacher_top128.pt
   ```
   Never GPU 6. Check the **teacher** on the new seeds before distilling; if the 1B teacher also fails a family, say so.
11. **Push** the student adapter as `martinnavs/granite-4.0-h-350m-router-kd-web4-s0-unverified` (private).

## Acceptance criteria (cluster side)
- [ ] `pytest` in `finetune/` green, including the new tests.
- [ ] Dev split exact reported **per new seed** (negated, Filipino, near miss, play target, no referent, job requests,
      confirm offer) and ack/statement strict exact (was 13/36 and 9/44), and
      overall, next to kd-web3-s0 (full dev 98.7% with ack/statement wording free; old kinds 98.4%); old kinds must not drop
      by more than 1 point.
- [ ] A held-out check you write yourself, through the **student** with generation, with names and phrasings not in the
      banks: 60 cases (12 negated, 15 Filipino incl. 3-word forms, 8 near miss, 10 play target incl. bare-music contrasts,
      8 no referent with and without a resolving history, 7 job requests) plus 10 confirm-offer cases (6 after an offer,
      4 contrasts after the router's own question); report the wrong ones one by one.
- [ ] Ticket-03 held-out (70 cases) and its 40 multi-turn repeats re-run: no regression.
- [ ] Data counts per seed, before and after; report commands, results table, spend ($0 expected), anything not done.

Laptop-side acceptance (not yours, for context): the held-out retest set >= 60/62, OnIt >= 75/77, pack >= 78/92, the fresh
sets of ticket 03 unchanged, and the laptop's hard-phrasing probe with fewer turns needing the call-policy escalation and
0 wrong actions.

## Do not look at / copy from
`.scratch/` other than this ticket (held-out test sets, probe phrasings and spoken-test results), `out/sessions/`,
`docs/stress/`, and do not copy wording from `pipeline/config/eval/agent_prompts.jsonl` or `eval/onit_eval.jsonl` into the
banks. Use your own names and phrasings; the laptop tests with different ones.

## Hard rules
- Never read or print API keys or anything under `~/.secrets`.
- This recipe needs no paid API. If anything would call Gemini, ESTIMATE the cost, tell the owner, wait for "go".
- Stop servers by PID (never `pkill -f`); GPU 6 is off limits.
- Git: `git add` by explicit file name, commit by topic, trailers `Co-Authored-By: ...` and `Claude-Session: ...`;
  push only when the owner asks, never force, never push main. Format only the files you edit.

## Execution Log
(append dated entries below; never rewrite earlier ones)
