# Recipe: a real-voice test of the voice MVP

What this checks, in about 20 minutes with your own voice: how quickly the assistant answers,
how well it hears names, and how often it wrongly asks "anything to add?". Every run is saved
in one folder so two runs can be compared and the weak spot found.

## 0. Once, before you start

```bash
cd /mnt/School/PhD/AI231/AI-231-ME3-agy
uv sync --group laptop
uv run python scripts/fetch_laptop_models.py        # speech models (Kroko, Parakeet, Supertonic)
uv run python scripts/fetch_turn_model.py           # text turn-end model, 176 MB
scripts/power_mode.sh max                           # plug in the charger first
uv run --group laptop python -W ignore -m doc_chat.gemini   # prints "gemini check ok (... ms)"
```

If the Gemini check fails, nothing else in this recipe can run (the key file is
`~/.secrets/google-ai-studio/api-key.txt` or `api_key.txt`; its contents are never printed).
Use a headset if you can: the microphone is closed while the assistant speaks, but room echo at
the end of its answer can still be picked up. Sit where you will sit at the demo.

## 1. Terminal 1: the speech server

```bash
uv run --group laptop python -W ignore -m lan_speech --stt-model parakeet --insecure-loopback \
    --entities config/entities-docs.json
```

Wait for `LanSpeechServer listening on 127.0.0.1:8765`. (If something else already uses 8765, add
`--port 8766` here and `--server ws://127.0.0.1:8766` below.) Write down any extra server flag you
use (`--early-decode-ms 200`, `--hotword-score 0.5`) in the `--note` of step 3.

## 2. Check the room (30 seconds, optional but useful)

```bash
uv run --group laptop python scripts/try_stt.py --levels
```

Stay quiet for a second, then say a sentence. The noise figure should be well under your speech
level (a gap of 20 dB or more is fine). Ctrl-C to leave. A loud fan or a speaker playing music will
show up here and in the results later.

## 3. Terminal 2: the assistant, recording

```bash
RUN=out/sessions/$(date +%m%d-%H%M)
uv run --group laptop python -W ignore -m doc_chat --mic --play --docs corpus/harry-potter \
    --llm gemini --stream --log-turn-score --record $RUN \
    --note "laptop mic, quiet room, headset, server defaults"
```

It prints `Listening...`. Speak; a pause of 0.6 s ends your turn. Say "quit" to stop. Everything
is saved in `$RUN/`.

You can talk over it: the microphone stays open while it thinks and while it speaks. If you start
talking after it has been cut off it joins your two parts; if you talk while it is answering it
stops and takes what you say as the next question. (`--ask-to-add` is off here: in the first
real run it asked 11 times and you never added anything, because people restart the sentence.
`--log-turn-score` still records what it would have asked. Use a headset: speakers can make it
interrupt itself; if that happens raise `--barge-ratio`.)

## 4. What to say (about 25 turns, in this order)

Say each line once, naturally, then wait for the answer to finish. If you cough or stumble, carry on
and remember the turn number. After each block look at the screen: the line
`you:` is what it heard, `(recognised as: ...)` appears when a name was repaired.

| Block | Say | What you are checking |
|---|---|---|
| A. Warm-up (5) | "Who is Hermione Granger?" / "What house is Harry in?" / "Who teaches potions?" / "What is Quidditch?" / "Where do the students sleep?" | plain questions: the baseline for speed and accuracy |
| B. Names (6) | "Is Dumbledore a wizard?" / "Tell me about Severus Snape." / "Who is Hagrid?" / "What does Voldemort want?" / "Who is Hermione's cat?" / "Tell me about Gryffindor." Say two of them sloppily or fast. | hotword/name repair: does it hear and, when unsure, ask "Did you mean ...?" |
| C. Follow-ups (4) | "Who is Ron Weasley?" then "And his sister?" then "What house is she in?" then "Thanks." | history: does the second question make sense without repeating the subject |
| D. Trailing off (6) | Say the first part, stay silent about a second, then (a) finish it after the prompt: "Tell me about the Order of the ..." -> "Phoenix." (b) "What did Dumbledore say to ..." -> "Harry about the prophecy?" (c) "Who is the headmaster of ..." -> "Hogwarts?" Then (d) trail off and say nothing; (e) trail off and answer the prompt with "No, that's all."; (f) trail off, say "um" and carry on. | the ask-to-add path: does it ask, does the joined sentence come out right, does it leave you alone when you say no |
| E. Normal speed (3) | Three complete questions without any pause inside them, at your usual speaking speed. | false prompts: it should NOT ask anything here |
| G. Interruptions (6) | (a) say half a sentence, pause about one second, then finish it ("Tell me what Harry thinks about ..." -> "... his friend Ron"). (b) Ask something that gets a long answer, and while it speaks say "stop" (c) Ask a long-answer question, and while it speaks ask a different question. (d) Cough or tap the desk while it speaks. (e) Say "uh" mid-sentence and go on. (f) Speak over it very early, right as it starts. | cut-off join (a, e), barge-in (b, c, f), and that a cough does not stop it (d) |
| F. Hard conditions (3) | One question while a fan or music plays; one spoken softly; one a little farther from the microphone. | how fast the quality falls |

Finish with "quit".

## 5. Look at the results

```bash
uv run --group laptop python scripts/session_report.py $RUN --init-truth    # writes $RUN/truth.tsv
# open $RUN/truth.tsv and correct each line to what you ACTUALLY said (one line per turn)
uv run --group laptop python scripts/session_report.py $RUN --write         # prints and saves report.md
```

The report shows: the latency of each stage and which one dominates, how the names were handled,
the word error rate against your corrected `truth.tsv`, how the turn-end model scored each turn
(and what each threshold would have asked), and a short "where to tune" list. Keep `$RUN/` (it has
`session.json`, `turns.jsonl`, `audio/`, `bot/`, `report.md`) and send me the report; the audio
lets me replay any turn that went wrong.

## 6. What is recorded

| File | Content |
|---|---|
| `session.json` | when, code commit (and whether there were uncommitted changes), the model, every setting, your `--note` |
| `turns.jsonl` | one line per turn (an interruption has its own line with `interrupted` = `resumed` or `barge_in`, `cancel_to_silence_ms`, `interrupt_after_final_ms`, `interrupt_after_answer_start_ms`, `spoken`): `heard` (final text), `raw_text` (before name repair), `partial` (the streaming text just before the end), `repair` (rung and matches), `audio_s`, `noise_floor`, `pause_ms` (your pause), `stt_server_ms`, `llm_first_token_ms`, `llm_ms`, `tts_first_audio_ms`, `speech_end_to_first_audio_ms`, `unfinished_score`, `asked_to_add`, `added`, `ts` |
| `audio/turnNN.wav` | what you said in turn NN (joined audio if you were asked to add) |
| `bot/turnNN.wav` | what it said |
| `truth.tsv` | your correction of each turn (you write it) |
| `report.md` | the summary |

## 7. Where to tune (symptom -> try)

| You see | Try | Where |
|---|---|---|
| Answer starts more than ~2 s after you stop | look at "biggest stage" in the report; the language model's first word is about 0.9 s from this machine, the rest is the pause (0.6 s) and the server (0.4-0.6 s) | `--llm-model`, `--vad-hold`, `--early-decode-ms 200` on the server |
| It cuts you off mid-sentence | longer pause, and make sure the room noise is low | `--vad-hold 0.8` |
| Waiting for the pause feels slow | shorter pause (more cut-offs; ask-to-add catches some) | `--vad-hold 0.4` |
| Wrong names | add the missing names; raise the name bonus a little (above ~1.5 over-boosts) | `config/entities-docs.json` or `scripts/make_entities.py`, server `--hotword-score` |
| It asks "anything to add?" when you were done (E block) | lower the threshold | `--add-threshold 0.2` |
| It does not ask when you trailed off (D block) | raise the threshold, or the recogniser completed your phrase on its own (see `heard` vs `partial`) | `--add-threshold 0.5` |
| It stops talking when you did not speak (its own voice, a cough) | `--barge-ratio 6` or `--barge-ms 450` | doc_chat flags |
| It keeps talking when you cut in, or reacts late | `--barge-ms 200` (lower = quicker, more false stops) | doc_chat flags |
| It cannot be interrupted at all, or you want the old behaviour | `--no-barge-in` | doc_chat flags |
| You went on after a pause and it answered the first half | the pause is longer than the hold: `--vad-hold 0.9` | doc_chat flags |
| The joined sentence is wrong after you add | listen to `audio/turnNN.wav`; the gap between the parts is 0.4 s | `STITCH_GAP_S` in `src/doc_chat/speech.py` |
| Server time above 500 ms | charger, `scripts/power_mode.sh max`, close other CPU-heavy programs | |

## 7b. If something goes wrong

| You see | Meaning |
|---|---|
| A wall of `Expression 'res' failed in 'src/hostapi/alsa/pa_linux_alsa.c'` / `PaAlsaStream_EndProcessing` lines, and it stays silent or freezes for many seconds after you cut in | the speaker was stopped from the wrong thread (fixed in the player: it now stops within about 15 ms). A report from before that fix shows `cancel_to_silence_ms` in the thousands. |
| A few `ALSA lib ... Unknown PCM` / `jack server is not running` lines at start | harmless noise from PortAudio probing devices |
| `ALSA ... underrun occurred` while it speaks | the CPU was busy; try `scripts/power_mode.sh max` |
| `Invalid number of channels` / no input | wrong default device: list with `python -m sounddevice`, set the default input in your sound settings |

If the error is something else, copy the first 10 lines and the `turns.jsonl` of that run.

## 8. Known limits (so they are not mistaken for bugs)

- One speaker, one room; accents other than yours are untested.
- The turn-end text model misses a quarter to a half of unfinished sentences, and the
  recogniser sometimes completes a hanging phrase itself ("said to" -> "said to me."); then
  nothing is asked.
- Starting the answer early (deciding at a 200 ms pause) is not built; the fixed 0.6 s pause is
  always in the latency.
- Barge-in needs 0.3 s of speech before it stops (so a cough does not), and with speakers its own
  voice can trip it; the bar is higher while it speaks (4x the room noise) than while it thinks (3x).
- After a barge-in the cut-off answer is remembered only as far as it was spoken.
- Cloud model latency depends on the network you are on (phone hotspot included); note it in
  `--note` when you change networks.

See `docs/SMART-TURN.md` for the experiments behind the turn-end part and `docs/DOC-CHAT.md` for
every option.
