# Handoff: laptop speech, the doc_chat test client, interruptions, and the Pi test

**Written:** 2026-10-21 (Philippine time). **Tip of the stack:** branch `agy/interrupts`. **Tests at the tip:** 529 pass, 9 skipped, 2 xfailed; ruff and mypy clean.
**Audience:** the owner picking this up on the Raspberry Pi, or any person or AI assistant continuing the work with no memory of how it got here. Everything needed is in the repository; this file is the map.

---

## 1. Start here

**The project.** ME3 Project 2: a full-duplex, interruptible English voice agent on a Raspberry Pi 4/5. The professor's rules (`.scratch/project2-voice-agent/DESIGN.md`): **no model runs on the Pi except the Silero VAD**; speech-to-text, the language model and text-to-speech are cloud services or the class server; graded on correctness, latency/responsiveness and naturalness; due about 2026-11-07 (tentative).

**What this stack of branches is.** The laptop-side work that answers four questions before the Pi pipeline is built:

1. How accurate can STT be (and where should it run)? &rarr; Parakeet-TDT + hotwords + a name-repair step on the laptop; findings in `docs/STT-HOSTING.md`.
2. How should the end of a turn be decided? &rarr; a client-side pause plus the server's `finalize`; the audio and text turn-end models we tried are *not* worth adopting as they are (`docs/SMART-TURN.md`).
3. How should interruption (barge-in) behave? &rarr; built and measured in the `doc_chat` test client (`docs/DOC-CHAT.md`, `docs/INTERRUPTS.md`).
4. Can the assistant's own voice be told apart from the user's? &rarr; **not in software.** This needs echo cancellation; the Pi test checks it on the real hardware (`docs/PI-TEST-PLAN.md`).

**Bottom line, six points.**

1. STT: Parakeet v2 with hotwords cut word error on our hard set from 8.3% to 4.7% (entities found 72% &rarr; 88%); 4.5% on a 25-turn live session. The server adds about 0.46 s (short commands) to 0.83 s (5-8 s sentences) after the user pauses.
2. End of turn: a fixed client-side pause (0.6 s) is the baseline; **Smart Turn v3 calls request-style pauses "finished" 99% of the time** (it cannot tell "play something by..." from a finished request), and a text model (Turnsense) is good on Parakeet's words but poor on the streaming words available early. Neither is adopted.
3. "Anything to add?" (`--ask-to-add`) fired 11 times in the owner's real run and **nobody ever added anything** (people restart the sentence). It is off by default and stays off.
4. With no echo, interruption works with any detector: it goes quiet 150-340 ms after the user's voice starts, with the right words understood.
5. **With the assistant's own voice leaking into the microphone, every software detector fails**, and Silero is the worst because the assistant's voice is speech. This is a known industry problem solved by echo cancellation at the endpoint (hardware, WebRTC), headsets in development, ducking and guard times (`docs/ECHO-RESEARCH.md`).
6. On the laptop, PipeWire's software echo cancellation brought false triggers from 91-92% of blocks to 0% (`docs/BARGE-IN.md`). **The Pi with its USB speakerphone has not been tested.** That is the next step.

**The next step, and who does it.** *You*, on the Pi: follow `docs/PI-TEST-PLAN.md` (about 20 minutes for the first condition), commit the small result files with `scripts/pi_pack_results.sh`, and push. Then see section 7.

---

## 2. Where things are

### Branches (details and landing options: `docs/BRANCHES.md`)

| Branch | Contents |
|---|---|
| `main` (local) | the original project plus tickets 21-24 notes; 3 commits not on `origin/main` |
| `agy/laptop-speech` | laptop speech server, STT work, `doc_chat` test client, STT scripts and docs |
| `agy/smart-turn-experiment` | + barge-in, Gemini, `--record`, report, end-of-turn experiments |
| `agy/interrupts` | + model-free echo responder, interruption bench and ablation, Silero option, **the Pi test kit** |

The three `agy/*` branches are a linear stack; today the merge into `main` is clean.

### Code

| Path | What it is |
|---|---|
| `src/lan_speech/` | the speech server (WebSocket `/stt`, `/tts`): Kroko streaming + Parakeet re-decode with hotwords, the name-repair ladder (`transcript_repair.py`), early decode, Supertonic 3 TTS. Protocol: `docs/LAPTOP-SPEECH.md` |
| `src/doc_chat/` | the voice test client: `__main__.py` (loop, flags, session), `speech.py` (sockets, `listen_turn`, mic hub), `bargein.py` (detectors), `playback.py` (speaker), `turnend.py` (text turn-end model), `dialogue.py` (history, yes/no), `gemini.py`/`qwen.py`/`echo.py` (language models; `echo` is model-free), `phrases.py` (random seeded sentences), `pi_check.py` (the Pi checks) |
| `config/entities*.json` | name lists for the repair step (`entities.json` 318 names; `entities-docs.json` 125 names from the Harry Potter corpus) |
| `assets/silero_vad.onnx` | the exact Silero model tested (Pipecat's copy), see `assets/README.md` |
| `tests/fixtures/pi_echo/` | six standard reply clips + the sentences read in the double-talk test |

### Scripts (all `uv run --group laptop python scripts/<name>.py` unless noted)

| Group | Scripts |
|---|---|
| Pi test kit | `pi_echo_check.py` (on the Pi: `devices`, `selftest`, `doubletalk`, `compare`), `pi_double_talk_score.py` (laptop), `pi_pack_results.sh`, `make_pi_clips.py` |
| Interruption experiments | `interrupt_bench.py` (`--ablation`), `e2e_barge_in.py`, `e2e_ask_to_add.py` |
| Real-voice test | `session_report.py` (reads a `--record` folder), recipe `docs/IRL-RECIPE.md` |
| End-of-turn experiments | `smart_turn_eval.py`, `smart_turn_dangling.py`, `text_turn_eval.py`, `text_turn_kroko.py`, `confirm_stitch_sim.py` (`uv run --with transformers ...`) |
| STT experiments | `stt_compare.py`, `stt_hotwords_eval.py`, `stt_repair_eval.py`, `stt_latency_bench.py`, `stt_finalize_latency.py`, `stt_early_eval.py`, `stt_provisional_eval.py`, `stt_padding_eval.py`, `score_tries.py`, `analyze_tries.py`, `try_stt.py`, `net_probe.py`, `phone_bench.py` |
| Housekeeping | `fetch_laptop_models.py`, `fetch_turn_model.py`, `make_entities.py`, `make_synth_clips.py`, `power_mode.sh`, `aec.sh`, `echo_check.py` |

### Documents (full index: `docs/INDEX.md`)

Read in this order: this file &rarr; `docs/PI-TEST-PLAN.md` &rarr; `docs/INTERRUPTS.md` &rarr; `docs/ECHO-RESEARCH.md` &rarr; `docs/BARGE-IN.md` &rarr; `docs/DOC-CHAT.md`. Research reports from the agy assistant are in `.scratch/speech-hosting/AGY-*.md`.

### Local-only data (git-ignored; exists on the laptop, not in the repository)

| Where | What |
|---|---|
| `models/` | speech models (fetch with `scripts/fetch_laptop_models.py`, `scripts/fetch_turn_model.py`) |
| `bench/mic_hard`, `bench/mic_heldout`, `out/tries/` | the owner's recorded voice and transcripts (privacy: do not publish) |
| `out/sessions/1021-0900`, `1021-0928` | the owner's two real doc_chat runs (40 turns and a short one) with audio |
| `out/interrupt_bench`, `out/*.txt` | bench outputs |

---

## 3. How the pieces fit

```mermaid
flowchart LR
    subgraph Laptop
      SRV[lan_speech server<br/>Kroko stream + Parakeet + repair + Supertonic TTS]
      CLI[doc_chat client<br/>mic hub, detectors, barge-in]
      LLM[(Gemini / Qwen / echo)]
      CLI -- audio, finalize --> SRV
      SRV -- transcript, repair --> CLI
      CLI -- sentences --> SRV
      SRV -- TTS audio --> CLI
      CLI <-- question / reply --> LLM
    end
    subgraph Pi
      KIT[pi_echo_check.py<br/>plays clips, records mic,<br/>Silero + loudness detectors]
      SD[USB speakerphone<br/>hardware echo cancel]
      KIT <-- one duplex stream --> SD
    end
    KIT -. results: run.json / run.md .-> NOTES[docs/pi-results]
```

- **Relation to main's Project 2.** Main plans a Pipecat pipeline on the Pi with cloud STT/TTS (tickets 10-12). As far as the tree shows, that pipeline (tickets 10, 11, 12, 16) is **not implemented** on `main` at `6ecb0f7`; check each ticket's Status line. Our laptop server and `doc_chat` are the "host STT/TTS ourselves" alternative (tickets 21-24) and a place to study the behaviours (end of turn, barge-in, echo) that the Pi pipeline will need. The findings transfer; the code does not (it is not Pipecat).
- **The Pi test kit is standalone.** It needs only numpy, scipy, onnxruntime, sounddevice and the vendored Silero file; no server, no network.

---

## 4. What we know, and how well

| Topic | Result | Evidence | Limits |
|---|---|---|---|
| STT accuracy | Parakeet v2 + hotwords: hard set 8.3% &rarr; 4.7% WER; held-out 11.6% &rarr; 9.3%; live 25 turns 4.5% | `docs/STT-HOSTING.md` | one speaker (the owner); hotword score above ~1.0 over-boosts |
| STT latency | server finalize p50 722 ms (460 ms for commands under 3 s); with early decode 375 ms median | `docs/STT-HOSTING.md` | early decode costs accuracy on short commands (4.9% &rarr; 7.4%), off by default |
| Name repair | silent / implicit / confirm / targeted / reprompt ladder with guards; "Hermoin" &rarr; Hermione | `src/lan_speech/transcript_repair.py`, tests | closed lists only |
| Audio turn-end model | Smart Turn v3.2 (8.7 MB): ends 98-100% of finished turns but also 93-100% of unfinished request-style pauses; 77-102 ms per check on one laptop thread (Pi not measured) | `docs/SMART-TURN.md` | one speaker; chatty speech it handles well |
| Text turn-end model | Turnsense (Apache-2.0): on Parakeet's words finishes 98% / wrongly ends 13% of unfinished; on Kroko's early words 69% / 34% | `docs/SMART-TURN.md` | the words available early are Kroko's |
| "Anything to add?" | simulation: recovers 60-74% of trailing-off pauses at 2-11% unneeded prompts; **real run: 11 asks, 0 additions** | `docs/SMART-TURN.md`, `docs/DOC-CHAT.md` | off by default |
| Language-model latency | Gemini flash-lite first token ~0.9 s (0.74-1.27 s) quiet-network; in the real run p50 1.25 s, p95 6.2 s; whole answer starts p50 3.06 s, p95 9.7 s after the user stops | `docs/DOC-CHAT.md`, `out/sessions/1021-0900` | the model and network dominate; not a speech problem |
| Cut-offs | in the real run 7 of 40 turns were ended while the user was still speaking (pause 0.6 s) | `out/sessions/1021-0900` | motivated the join-on-resume logic |
| Barge-in, no echo | stops 150-340 ms after the voice begins; 0% word error; "stop" no longer quits the app | `docs/INTERRUPTS.md` | synthetic voice, 2 repetitions |
| Barge-in, with echo | **fails**: Silero from -18 dB of leak, loudness from -12 dB, combinations too; loops of 30+ self-interruptions in a trial | `docs/INTERRUPTS.md` (ablation table) | simulated flat-gain leak, no room echo |
| Laptop echo cancellation | PipeWire WebRTC: false triggers 91-92% &rarr; 0% of blocks; first play leaks ~11 dB; room floor -12..-17 dB | `docs/BARGE-IN.md` | recognition accuracy under it **untested** |
| Silero cost | 0.12 ms per 32 ms window on the laptop (simulation); stop with `latency='low'` 45-55 ms | `docs/BARGE-IN.md`, `pi_check` | **Pi value unknown** |
| Industry practice | echo handled at the endpoint; headset in development; 200 ms speech minimum; ducking; truncate history | `docs/ECHO-RESEARCH.md` | agy report, partly unverified |

### Mistakes found and fixed during this work (so they are not repeated)

- **Speaker stopped from the wrong thread** deadlocked PortAudio's ALSA backend (a wall of `PaAlsaStream_EndProcessing failed`, a 21 s hang). The player thread now stops the device itself (`src/doc_chat/playback.py`, test `tests/doc_chat/test_playback.py`).
- **Audio kept streaming after `finalize`** created a stale end-of-turn transcript that appeared as a phantom turn. The client now holds audio while waiting for the final (`listen_turn`).
- **"stop" was a quit word.** Now `quit/exit/goodbye` quit; "stop/wait/hold on" only silence the assistant.
- **Parakeet puts a full stop on truncated audio**, which makes an unfinished sentence read as finished to a text model; strip punctuation before the check (`turnend.plain_text`).
- **Word timestamps drift** (80 ms steps), so cuts "after a word" must be validated by decoding the prefix (`confirm_stitch_sim.py`).
- **The Silero file on GitHub `master` is not the file Pipecat ships** (same size, different hash). We vendored the tested one.
- **A bench result line glued to progress output** (carriage returns without a newline) was lost until the parser searched for the marker anywhere in the line.

---

## 5. Decisions

| Decision | Status | Why |
|---|---|---|
| Parakeet v2 + hotwords as the accuracy engine on the laptop | settled | best measured; v3 and others worse |
| Client-side end of turn: adaptive pause 0.6 s then `finalize` | settled | Kroko's own endpoint took 1.6-2.3 s |
| Do not adopt Smart Turn v3 or Turnsense as the end-of-turn rule | settled (for now) | measured above |
| `--ask-to-add` off by default | settled | 0 additions in 11 asks |
| Keep recognition open while the assistant speaks; barge-in joins a cut-off turn or takes a new question | settled for `doc_chat` | `docs/DOC-CHAT.md` |
| Echo needs echo cancellation (hardware or software), not a smarter detector | settled by the ablation | `docs/INTERRUPTS.md` |
| Use Silero (150-250 ms) as the detector once echo is handled | **pending the Pi result** | it rejects non-speech noise (a 0.3 s noise burst stopped every loudness detector and no Silero one) but is oversensitive to faint echo |
| Delete `EchoModel` and its flags (`--barge-echo-margin/-prior`) | **pending the Pi result** | marginal gain, still fails at -12 dB; keep `looks_like_echo` (cheap, cuts loops 5-8x) |
| Duck-then-stop instead of a hard stop | **proposed, not built in doc_chat** | main's ticket 12 specifies it; makes a false trigger cheap |
| Land the branches (A: stacked PRs, B: fast-forward, C: product only) | **owner's call** | `docs/BRANCHES.md` |

---

## 6. Open questions and risks, ranked

1. **Does the speakerphone's hardware cancellation keep the Silero VAD quiet on the Pi, at normal and loud volume, including the first play?** (the Pi test). Everything below depends on it.
2. **Does echo cancellation hurt recognition?** The laptop's module lowered the room floor 12-17 dB; accuracy under it was never measured. The double-talk scorer measures it.
3. Silero's per-window cost on the Pi is unmeasured (the laptop number is 0.12 ms; budget 32 ms).
4. The start-of-playback grace period: the research says 250-300 ms, our notes say 3 s. Measure.
5. Which speakerphone is it, and does it have a mode switch? (the hardware sheet in the Pi plan.)
6. Only one speaker (the owner), one accent, one room. Other speakers are untested.
7. Real coughs and knocks: the benchmark used white noise.
8. The early-send half of the turn-end policy (decide at a 200 ms pause) needs the speech server to let a turn be resumed; not built.
9. Main's Pi pipeline (tickets 10-12) is unbuilt; the barge-in gate there is a rewrite in Pipecat terms.

---

## 7. Next actions, in order

1. **(You, Pi)** Run `docs/PI-TEST-PLAN.md` conditions A-D (E only if A fails). `scripts/pi_pack_results.sh`, write `docs/pi-results/NOTES.md`, commit, push. Copy the double-talk folders to the laptop and run `scripts/pi_double_talk_score.py`.
2. **(Assistant)** Read `docs/pi-results/`, fill the decision table in `docs/PI-TEST-PLAN.md` section 5, and write the outcome into section 4 here.
3. **(Assistant)** If A passes: delete `EchoModel` + flags; make `--vad silero` (150 ms, with a level gate if the plan says so) the default; add duck-then-stop to `doc_chat`; update `docs/INTERRUPTS.md`. If A fails: follow the other rows of the decision guide.
4. **(Owner)** Choose how to land the branches (`docs/BRANCHES.md`).
5. **(Both)** Port the winning gate into the Pi pipeline when tickets 10-12 are built.
6. Parked, in order of value: other speakers; coughs/knocks recorded for real; server-side resume for early send; provisional transcripts for speculative LLM start (ticket 12 / design review section 3).

---

## 8. Working agreements (follow these; they came from the owner)

- **No model on the Pi except the Silero VAD.** The Pi test tool runs nothing else.
- **Never read or print secrets.** Do not open `~/.secrets/*`, `.env`, or any `*key*`/`*token*` file; code reads keys at run time and prints only OK/failure. The Gemini key lives in `~/.secrets/google-ai-studio/` (`api-key.txt` or `api_key.txt`); Qwen's in `~/.secrets/onit/`.
- **Do not touch the owner's untracked files:** `docs/VOICE-AGENT-EVALUATION-METRICS.md`, `kek.txt`, `resources/building-voice-agents/`. Add files to git **by name**, never `git add -A` or `git add docs`.
- **Never use `--dangerously-skip-permissions` for the agy assistant** (the owner never approved it). Research prompts to agy are search-only (no shell, no file reads).
- **Never kill processes that are not yours.** The owner's own speech server may use port 8765; tests use 8766-8767.
- **Gate every commit** on `ruff check`, `ruff format --check`, `mypy` (packages in `pyproject.toml`) and `pytest`.
- **Commit trailer:** `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. In this repository use `git -c safe.directory=$PWD ...` (the checkout is on a mounted drive).
- **Report faithfully:** say what failed, what was not measured, and how many repetitions a number rests on (most interruption numbers rest on 2).
- Timezone: Philippine Standard Time (UTC+8). Run `scripts/power_mode.sh max` (on AC) before latency measurements and `balanced` after.

---

## 9. Cheat sheet

```bash
# laptop: the speech server (Parakeet + repair, names from the Harry Potter docs)
uv run python scripts/fetch_laptop_models.py
uv run --group laptop python -W ignore -m lan_speech --stt-model parakeet --insecure-loopback --entities config/entities-docs.json

# laptop: a real-voice session, recorded, with Gemini, you can talk over it (headset!)
uv run --group laptop python -W ignore -m doc_chat --mic --play --docs corpus/harry-potter --llm gemini --stream --log-turn-score --record out/sessions/$(date +%m%d-%H%M)
uv run --group laptop python scripts/session_report.py out/sessions/<folder>

# laptop: model-free interruption tests
uv run --group laptop python scripts/interrupt_bench.py --ablation --reps 2          # about 60 min, needs the server on 8767
uv run --group laptop python scripts/interrupt_bench.py --only barge echo noise --reps 2

# Pi: the test (see docs/PI-TEST-PLAN.md)
uv sync --group pi --no-group dev
uv run python scripts/pi_echo_check.py devices
uv run python scripts/pi_echo_check.py selftest   --label "A speakerphone vol60" --device "<name>"
uv run python scripts/pi_echo_check.py doubletalk --label "A speakerphone vol60" --device "<name>"
scripts/pi_pack_results.sh

# the gate
uv run --group laptop ruff check src tests scripts && uv run --group laptop ruff format --check src tests scripts \
  && uv run --group laptop mypy && uv run --group laptop pytest -q
```

---

## 10. Resuming with an AI assistant

Paste this into a new session in the repository root:

> Read `docs/HANDOFF.md` fully, then `docs/PI-TEST-PLAN.md` and everything in `docs/pi-results/` (the Pi results I just pushed). Follow the working agreements in section 8. Do section 7 step 2: interpret the results with the decision table, report what they show and what they do not (state repetitions), and propose the next change before making it. Do not touch my untracked files and do not push without asking.

---

## 11. Glossary

- **Barge-in:** the user interrupts while the assistant is speaking.
- **Echo / leak:** the assistant's voice coming back through the microphone, in dB relative to the speaker signal (-40 headset, -18 to -6 open speakers).
- **AEC:** acoustic echo cancellation; needs the playback signal as a reference.
- **VAD:** voice-activity detector. **Silero** is the neural one; "energy" is a loudness threshold.
- **Onset:** the moment a detector decides speech has started (after a minimum speech time).
- **Self-trigger / false trigger:** a detector firing on the assistant's own voice.
- **Finalize:** the client's message asking the server to end the turn now and return the final transcript.
- **Hotwords:** a bias list that makes the recogniser favour known names. **Repair ladder:** the post-step that fixes near-miss names (silently, by echo, or by asking).
- **Double talk:** the user and the assistant speaking at once.
- **Ablation:** the same test with parts of the system switched on and off (`docs/INTERRUPTS.md`).
