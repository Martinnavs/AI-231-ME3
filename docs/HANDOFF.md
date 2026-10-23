# Handoff: laptop speech, the doc_chat test client, interruptions, the Pi, and the Pi-as-loudspeaker setup

**First written:** 2026-10-21; **updated:** 2026-10-23 (Philippine time). **Tip of the stack:** branch `agy/interrupts`; `origin/agy/interrupts` is at `ec221a7` (pushed); later local commits (the McCarthy corpus, the playback and stop-phrase fixes, the guard calibration) are not pushed, see `git log origin/agy/interrupts..`. **Tests at the tip:** 562 pass, 9 skipped, 2 xfailed, 1 fails (see section 0); ruff and mypy clean on everything touched.
**Audience:** the owner picking this up on the Raspberry Pi, or any person or AI assistant continuing the work with no memory of how it got here. Everything needed is in the repository; this file is the map.

---

## 0. Status as of 2026-10-23 (read this first)

Sections 1-11 were written on 2026-10-21, before the Pi was tested. This section says what happened since; where it disagrees with a later section, this one wins (the later sections are updated below where it matters).

**Where it stands against the 2026-10-21 state (before the Pi test).** One speaker, one room throughout; most counts are small.

| | 2026-10-21 (written before the Pi test) | 2026-10-23 |
|---|---|---|
| Hardware | the Pi was untested; a speakerphone was assumed | separate USB mic (weak: 5-15 dB over its noise) and speaker; the chosen setup is laptop mic + Pi loudspeaker, with the Pi running no model |
| Barge-in with the assistant's voice leaking into the mic | every software detector failed (simulated leak); Silero worst | Silero fires on the Pi speaker's echo at 40-90% volume; the echo guard (Silero AND level above the echo's *peaks*) stops it: 0 of 27 quiet replies interrupted by echo, 19 of 19 cued interruptions stopped (beep to stop p50 0.65 s) |
| Stopping | 150-340 ms after the voice begins (no echo, synthetic voice, 2 repetitions) | abort within 6 ms (p50) of the decision; a decision needs about 0.22 s of speech (7 of 10 windows) |
| Whole path on the Pi | not built | works end to end: Pi speaker via `pi_speaker.py`, laptop mic, STT, retrieval, Gemini, TTS, stop and pivot |
| Speech to text | Parakeet + hotwords, 4.5% WER on 25 live turns; finalize p50 0.72 s (0.46 s for commands under 3 s) | finalize p50 0.32 s, p95 0.55 s on 1-3 s utterances; 30 of 41 scripted utterances exactly right, errors mostly the first words |
| Reply latency | Gemini first token about 0.9 s; real run: answer starts p50 3.06 s, p95 9.7 s after you stop | first token p50 1.03 s; you stop -> first answer audio p50 2.57 s, p95 2.86 s (plus about 0.1-0.3 s to the Pi speaker); one 8.6 s cold start |
| Real-time factors | Supertonic RTF 0.147 on a long paragraph (battery) | TTS RTF p50 0.19 (p95 0.48); whole streaming pipeline RTF p50 0.23; guard RTF about 0.016; 0 underruns |
| Cut-offs | 7 of 40 turns ended while the user was still speaking (0.6 s hold) | none seen in the final sessions on the laptop mic (on the Pi's own mic a 1.2 s hold was needed) |
| Biggest open items | the Pi test itself | the laptop-mic + Pi-speaker brief question; first-words recognition; a second speaker and room; real echo cancellation if the volume must go above about 80% |

**What was done** (details in the linked documents):

| Step | Result | Where |
|---|---|---|
| Pi echo test (split USB mic and speaker; no speakerphone existed) | Volume decides self-trigger: Silero fires on its own voice at 90% without AEC, clean at 60-75%. The USB mic is weak (speech 5-15 dB over its noise) | `docs/PI-RESULTS.md` |
| Live barge-in pilot on the Pi | Recommended gate: Silero confidence 0.5, "7 of the last 10 windows", duck -15 dB, 75% volume | `docs/PI-RESULTS.md` |
| Pi bridge: Pi mic and speaker, laptop STT and TTS over a cable | Works end to end. Stop-speaking to first sound: 1.99 s (1.2 s pause hold + 0.44 s server + 0.32 s TTS). The weak mic fragments long phrases at the 0.6 s hold, so 1.2 s is used | `docs/PI-BRIDGE.md`, `scripts/pi_bridge.py` |
| Laptop mic with the Pi as loudspeaker (the owner's proposed production setup) | The Pi speaker's echo fires Silero on every clip at 40-90% volume. An echo guard (Silero stage AND level-above-expected-echo stage) detected 6 of 6 interruptions with 0 of 6 echo-only clips firing at 80% (held-out; one speaker, one room) | `docs/PI-REMOTE-SPEAKER.md` |
| End-of-turn rules replayed on a Pi recording of 14 read phrases | A Silero-based rule with a 0.5 s hold cut 0 of 14 phrases and fired 0.48 s after the phrase ended; the loudness rule at 1.2 s also cut 0 of 14 but took 1.04 s. Read speech only; the spontaneous recording was silent, so this is not settled | `scripts/eot_replay.py` |
| Live `doc_chat` conversation with the guard, Pi speaker at 80%, model-free random-sentence replies over the McCarthy corpus | After fixing three bugs and re-calibrating the echo bar from traces: 0 of 14 quiet replies interrupted by echo (6 + 8), 7 of 7 cued interruptions stopped (median 0.76 s from the beep), stop to first answer audio 1.69 s p50, 11 of 18 questions recognised exactly (errors mostly the first words). One speaker, one room, small counts | `docs/PI-REMOTE-SPEAKER.md` ("In the real doc_chat loop") |
| Live `doc_chat` with Gemini answering from the McCarthy corpus, cue-beep, same guard | 7 of 7 cued interruptions stopped (median 0.64 s from the beep), 0 echo false triggers, stop to first answer audio 2.26 s p50. Exposed two retrieval bugs (a five-word question was searched together with the previous one; title stubs outranked content), both fixed | `docs/PI-REMOTE-SPEAKER.md` ("With a real language model") |
| Pivoting: interrupt with "wait, stop, tell me about X instead" (Gemini, cue beep) | 5 of 5 stopped (median 0.42 s from the beep) and 5 of 5 answered the new question on the right topic; 6 of 6 quiet replies clean | `docs/PI-REMOTE-SPEAKER.md` ("Pivoting") |
| Cormac McCarthy corpus (33 files, 25,600 words, researched by four search-only agy runs, hand-checked) for a larger doc-chat test | 562 retrieval chunks, retrieval 0.7 ms; not a citable source (errors found and fixed are listed) | `docs/CORPUS-MCCARTHY.md`, `corpus/cormac-mccarthy/` |
| Main's ticket 12 (barge-in gate) amended to the 7-of-10 rule | On local branch `docs/ticket-12-kofn-gate` (`ce0851a`, off main, not pushed). Ticket 12 itself is **not built**: it needs the agent pipeline (tickets 05, 10, 11), which the owner parked to focus on interrupts and STT | `.scratch/project2-voice-agent/tickets/12-...md` |

**Tests.** `uv run --group laptop pytest -q` gives about 570 passed, 9 skipped, 2 xfailed and **1 failed** (counts move as tests are added): `tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents`. It connects to a speech server on `127.0.0.1:8765` and fails the same way on the commit before this work; start the server (section 9) and it passes. `ruff check` over the whole `scripts/` folder still reports E501 in `scripts/pi_synth_report.py` (an older file). **Do not run `ruff format` over `scripts/`**: it reformats that file and you will commit an unrelated change; format only the files you edit.

**Doc-chat flags the guard needs (2026-10-23):** `--vad both --vad-confidence 0.5 --silero-kofn 7/10 --level-kofn 3/8 --barge-echo-quantile 0.95 --barge-echo-window 60 --barge-echo-margin 1.2 --barge-echo-prior 0.8`. Without the last four the assistant interrupts itself on every reply with the Pi speaker in the room. Measurement flags: `--cue-beep 2.5 --cue-every 2` (a beep on every second reply to talk over), `--trace-guard` with `--record DIR` (a line per mic block in `DIR/guard.jsonl`), and `scripts/cue_report.py DIR` to score the session.

**State of the physical setup right now** (all of it disappears; rebuild with `docs/PI-BRIDGE.md` and `docs/PI-REMOTE-SPEAKER.md`):

* The Pi (`rpi-anavarez`, repo at `~/Documents/AI-231-ME3`) is reached by an Ethernet cable to the laptop (link-local `169.254.x.x`) and by SSH with a key (`~/.ssh/id_ed25519_pi` on the laptop; Pi address `fe80::18ac:b795:c102:5eb1%enp0s20f0u2c2`, or `169.254.230.219`). No sudo on the Pi.
* The Pi's repository working tree has **uncommitted copies** of `scripts/pi_bridge.py`, `scripts/pi_record.py`, `scripts/pi_speaker.py` and an older `src/doc_chat/bargein.py`. After the branch is pushed, run `git checkout -- . && git pull` there.
* `websockets` was installed into the Pi's venv with `uv pip install` (it is not in the `pi` dependency group). The speech token is in a mode-600 file on the Pi (`~/.config/lan_speech_token`); it was generated for the session and is not in the repository.
* The laptop firewall rule that lets the Pi reach the speech server is runtime-only (`firewall-cmd --zone=trusted --change-interface=<cable>`); it is gone after a reboot. The laptop's own speaker output was **muted**; nothing here changes that.
* Pi sink volume was left at 80%.

**Local-only data from this work** (git-ignored): `out/remote_echo/{pi90,pi80,pi60,pi40b,guard60,talk70far,fresh80}` (recordings and per-block traces; **`pi40` and `talk60` are invalid**: someone spoke or the cue was silent), `out/pi_bridge/eot-session.wav` (14 read phrases on the Pi mic; `eot-spontaneous.wav` is silence and `eot-laptop-session.wav` is an aborted partial file: ignore both). Pi-side latency logs were in `out/pi_bridge/` on the Pi.

**Open question for the owner or the professor:** does a design where the laptop owns the microphone and the Pi is only the loudspeaker satisfy the project's requirements (the brief is a voice agent on a Raspberry Pi)? Nothing in the repository settles it. The Pi-microphone path is kept working (`scripts/pi_bridge.py`) in case the answer is no.

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

**The next step, and who does it.** The Pi test in this paragraph's original form is done (section 0). The next steps are in section 7.

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
| `src/doc_chat/bargein.py` (new parts) | `BargeInGuard` (Silero stage + level-above-expected-echo stage, optional k-of-n rules), `SileroOnset(kofn=...)` and `.reset()` |
| `src/doc_chat/playback.py` (new parts) | `RemoteSpeaker` / `RemotePlayer` / `make_player`: play on another machine's speaker; `doc_chat --play-remote ws://<pi>:8780` |

### Scripts (all `uv run --group laptop python scripts/<name>.py` unless noted)

| Group | Scripts |
|---|---|
| Pi test kit | `pi_echo_check.py` (on the Pi: `devices`, `selftest`, `doubletalk`, `compare`), `pi_double_talk_score.py` (laptop), `pi_pack_results.sh`, `make_pi_clips.py` |
| Interruption experiments | `interrupt_bench.py` (`--ablation`), `e2e_barge_in.py`, `e2e_ask_to_add.py` |
| Real-voice test | `session_report.py` (reads a `--record` folder), recipe `docs/IRL-RECIPE.md` |
| End-of-turn experiments | `smart_turn_eval.py`, `smart_turn_dangling.py`, `text_turn_eval.py`, `text_turn_kroko.py`, `confirm_stitch_sim.py` (`uv run --with transformers ...`) |
| STT experiments | `stt_compare.py`, `stt_hotwords_eval.py`, `stt_repair_eval.py`, `stt_latency_bench.py`, `stt_finalize_latency.py`, `stt_early_eval.py`, `stt_provisional_eval.py`, `stt_padding_eval.py`, `score_tries.py`, `analyze_tries.py`, `try_stt.py`, `net_probe.py`, `phone_bench.py` |
| Housekeeping | `fetch_laptop_models.py`, `fetch_turn_model.py`, `make_entities.py`, `make_synth_clips.py`, `power_mode.sh`, `aec.sh`, `echo_check.py` |
| Pi bridge and Pi as loudspeaker | `pi_bridge.py` (runs on the Pi: Pi mic and speaker, laptop models), `pi_speaker.py` (runs on the Pi: plays what the laptop sends), `pi_record.py` (record a mic to WAV, Pi or laptop), `remote_echo_check.py` (laptop: Pi speaker echo and the guard), `guard_replay.py` (replay the guard on a recording), `eot_replay.py` (replay end-of-turn rules on a recording) |

### Documents (full index: `docs/INDEX.md`)

Read in this order: this file &rarr; `docs/PI-RESULTS.md` &rarr; `docs/PI-REMOTE-SPEAKER.md` &rarr; `docs/PI-BRIDGE.md` &rarr; `docs/INTERRUPTS.md` &rarr; `docs/ECHO-RESEARCH.md` &rarr; `docs/BARGE-IN.md` &rarr; `docs/DOC-CHAT.md` (`docs/PI-TEST-PLAN.md` is the runbook the Pi test followed). Research reports from the agy assistant are in `.scratch/speech-hosting/AGY-*.md`.

### Local-only data (git-ignored; exists on the laptop, not in the repository)

| Where | What |
|---|---|
| `models/` | speech models (fetch with `scripts/fetch_laptop_models.py`, `scripts/fetch_turn_model.py`) |
| `bench/mic_hard`, `bench/mic_heldout`, `out/tries/` | the owner's recorded voice and transcripts (privacy: do not publish) |
| `out/sessions/1021-0900`, `1021-0928` | the owner's two real doc_chat runs (40 turns and a short one) with audio |
| `out/interrupt_bench`, `out/*.txt` | bench outputs |
| `out/remote_echo/`, `out/pi_bridge/`, `out/pi/` | the 2026-10-22/23 Pi and Pi-speaker recordings and traces (see section 0 for which are invalid) |

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

- **Current topology (2026-10-23).** The diagram above is the original plan (the Pi test kit standalone). The setup now under test is the laptop owning the microphone and the speech models with the Pi as loudspeaker (`docs/PI-REMOTE-SPEAKER.md` has its diagram); the Pi-microphone bridge is `docs/PI-BRIDGE.md`.
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
| Pi USB microphone | speech only 5-15 dB over its own noise; Silero loses short words; sentences at 5-10 cm interrupt about 80-94% live (7 of 10 rule), "Stop" alone about 39% | `docs/PI-RESULTS.md` | one speaker, one room, 31-36 barge-ins |
| Pi bridge latency | stop-speaking to first sound 1.99 s p50 (p95 2.15 s): hold 1.2 s, server 0.44 s, TTS first audio 0.32 s, bridge overhead 2 ms after caching Silero | `docs/PI-BRIDGE.md` | 10 turns, one speaker, sound time estimated not measured acoustically |
| Pi speaker heard by the laptop mic | echo 12-26 dB over the room at 80-90% volume, still 0 to 2 dB at 40%; Silero fired on 6 of 6 clips at every volume | `docs/PI-REMOTE-SPEAKER.md` | one room, one run per volume |
| Echo guard (Silero stage AND level above expected echo) | 6 of 6 interruptions, 0 of 6 echo-only clips fired at 80% (Silero alone 12 of 12); 3 of 3 at 70% further away | `docs/PI-REMOTE-SPEAKER.md` | held-out for the 80% run only; one speaker; the cue was predictable |
| End-of-turn rule on the Pi mic | on 14 read phrases Silero with a 0.5 s hold cut 0 of 14 (0.48 s delay); the loudness rule at 0.6 s cut 2 of 14 | `scripts/eot_replay.py` | read speech; spontaneous speech not recorded |

### Mistakes found and fixed during this work (so they are not repeated)

Added 2026-10-23:

- **The Pi's ALSA `default` is HDMI**, not the USB speaker (pipewire-alsa is only unpacked under `~/.local`): audio "played" with no error and no sound. The bridge plays to `CD002` at 48 kHz. A silent stream looks the same as a working one in the logs; confirm by ear.
- **The Pi's USB devices open only at 48 kHz**; the TTS is 44.1 kHz and the server expects 16 kHz audio. Resample (`ResamplingPlayer`, `DecimatingMicHub` in `scripts/pi_bridge.py`).
- **Building a Silero session per reply cost about 200 ms on the Pi**; load once and call `SileroOnset.reset()`.
- **The combined guard fired on one loud syllable of the assistant's own voice** because it checked only the current block; it now needs sustained evidence.
- **My offline replay scored beeps against the wrong origin** (relative to the first push, used relative to each clip's push), which turned a 3-of-3 result into "1 of 3". Always cross-check a replay against the live run; `tests/doc_chat/test_guard_replay.py` covers it.
- **A muted laptop speaker made a beep silent**, so a talk-over test captured no speech. Cues now come out of the Pi, with the audio under test.
- **A firewall (`firewalld`, zone `public`) answered "No route to host"** while ping worked: the cable interface needed to be in a trusted zone.
- **`pkill -f "<name>"` over SSH or in a shell whose own command line contains `<name>` kills that shell.** Use `pgrep -af` first and `kill <pid>`.
- **zsh does not word-split `$VAR`**: put commands in functions, scripts or an ssh config alias, not in a variable.
- **Run commands that take over 2 minutes in the background**; the tool times out and moves them anyway.
- **The assistant heard itself because `Player.close()` returned after 2 s** while a long reply was still playing (synthesis is much faster than playback); it now waits for the reply's length. A silent-looking console is not evidence that playback finished.
- **The echo bar was built on the median echo, but the echo is spiky** (ratio p50 0.34, p99 0.79); and an echo model that restarts on every reply never learns. A harness run in a noisier room hid both. Calibrate on traces from the real loop (`--trace-guard`), not only on the harness.
- **A free-running spoken test has no ground truth.** Use the cue beep and `scripts/cue_report.py`.
- **Retrieval glued the previous question onto any question of five words or fewer**, so "Tell me about The Counselor" found the book asked about before it; and BM25 ranked 16-character title stubs above the paragraphs. Fixed (follow-up detection by connective or pronoun; fragments under 60 characters dropped). Use `--chunk-size 1000 --top-k 3 --system-prompt <file>` with a large corpus and Gemini.
- **"Wait, stop" was answered as a question** because the stop-word list was an exact-match set; later, 3 of 7 live stop attempts were still answered (its own words attached before "wait, stop", or "wait" heard as "quick"). Now handled: only-stop-words, two words ending in stop/wait/pause, or a short utterance ending in a stop phrase; "why did the road stop" is still a question. A bare two-word "bus stop" would count as a stop command.

Earlier (2026-10-21):

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
| Client-side end of turn: a pause, then `finalize` | **reopened** | 0.6 s was settled on the laptop mic; on the Pi's weak USB mic it cuts long phrases into fragments, so the bridge uses 1.2 s (costs 0.6 s). A Silero-based rule at 0.5 s looked safe on read speech (`scripts/eot_replay.py`); needs spontaneous recordings before adopting |
| Do not adopt Smart Turn v3 or Turnsense as the end-of-turn rule | settled (for now) | measured above |
| `--ask-to-add` off by default | settled | 0 additions in 11 asks |
| Keep recognition open while the assistant speaks; barge-in joins a cut-off turn or takes a new question | settled for `doc_chat` | `docs/DOC-CHAT.md` |
| Echo needs cancellation or an echo-aware decision, not a smarter detector alone | settled, refined | Silero fired on the assistant's own voice in every clip at 40-90% volume on both microphones; the echo guard (Silero AND level above the echo expected from what is being played) fixed it at 80% on the laptop mic |
| Silero as the barge-in detector | **settled for the gate** | confidence 0.5 and "7 of the last 10 windows" (`docs/PI-RESULTS.md`); the strict "0.25 s unbroken at 0.7" rule interrupted 58% at 0.67 s against 67% at 0.35 s |
| Keep `EchoModel` (the earlier plan was to delete it) | **changed** | `BargeInGuard` uses it for the speaker-to-microphone coupling; `looks_like_echo` stays too |
| Duck-then-stop instead of a hard stop | built in the Pi pilot (`src/doc_chat/pi_pilot.py`) only | not yet in `doc_chat`; main's ticket 12 specifies it |
| Production audio topology: laptop microphone, Pi as loudspeaker | **owner's proposal, implemented and tested; confirm with the professor** | removes the weak Pi-microphone problem; costs an echo path the guard has to handle; whether it meets the brief is unknown |
| Land the branches (A: stacked PRs, B: fast-forward, C: product only) | **owner's call** | `docs/BRANCHES.md` (written before the 2026-10-22/23 commits; those are on top of `agy/interrupts`) |

---

## 6. Open questions and risks, ranked

1. **Does the brief allow the laptop to own the microphone with the Pi only as loudspeaker?** If not, the Pi microphone path (weak mic: 5-15 dB over its noise, 5-10 cm operating distance) must be fixed with hardware or placement instead.
2. **End of turn on whichever microphone ships.** Record spontaneous requests (with the hesitations real users have) on both the laptop mic and the Pi mic, then replay the rules with `scripts/eot_replay.py`; the current numbers come from 14 read phrases on the Pi mic only.
3. **The echo guard now works in the real `doc_chat` loop (see section 0) but with small counts**, a predictable cue (a beep at 2.5 s) and model-free replies; real, unexpected interruptions will be slower to detect, and it has not been run with a real language model reading the corpus.
4. **A level guard has a ceiling.** At 90% volume the echo is 18-32 dB over the room, as loud as speech; not tried with the guard. Real echo cancellation (using the sent audio as the reference) would lift it, and was not built.
5. **Room noise rose from about -44 to -35 dBFS** during the 2026-10-23 runs and the cause is unknown; it moves every threshold that is relative to the room.
6. **The first one or two words of a question are often misrecognised** (4 of 18 live questions: "What prize did The Road win?" as "Price did the road win"; also 3 of 10 in a bridge run). The recorded audio shows the words reach the server (a loud onset after silence), so it is a recognition weakness on short opening words, not clipping; not yet addressed (hotwords, a lead-in, or the early-decode setting are the places to look).
7. Word error under echo cancellation, and the acoustic time until the speaker is silent after an interruption, were never measured.
8. Only one speaker (the owner), one accent, one room, 9 interruptions in the guard runs. Other speakers are untested.
9. Real coughs and knocks: the benchmark used white noise.
10. The early-send half of the turn-end policy (decide at a 200 ms pause) needs the speech server to let a turn be resumed; not built.
11. Main's agent pipeline (tickets 05, 10, 11) is unbuilt, so ticket 12's gate has nothing to attach to yet.

---

## 7. Next actions, in order

1. **(Owner)** Decide or ask the professor: is "laptop microphone, Pi loudspeaker" acceptable (section 6, item 1)? Everything below branches on it.
2. **(Both)** Repeat the Gemini live test (plain stops and pivots) with a second speaker and in a different room; the retrieval and stop-phrase fixes were exercised only in the pivot session.
3. **(Both)** Record spontaneous requests with `scripts/pi_record.py` (3 s of silence between requests; on the laptop `--in-device default --in-rate 16000`), then compare end-of-turn rules with `scripts/eot_replay.py`. Pick a hold and decide whether the Silero-based rule replaces the loudness rule in `listen_turn`.
4. **(Assistant)** Add duck-then-stop to `doc_chat` (the pilot has it; ticket 12 specifies it), then try the guard at 90% volume and with the speaker moved; decide whether real echo cancellation is needed.
5. **(Owner)** Push `agy/interrupts` (two commits ahead of origin) and `docs/ticket-12-kofn-gate`; choose how to land the branches (`docs/BRANCHES.md`); then `git pull` on the Pi (section 0).
6. **(Both)** Port the winning gate into the Pi pipeline when tickets 05, 10, 11 and 12 are built.
7. Parked, in order of value: other speakers; coughs and knocks recorded for real; the dropped first words; server-side resume for early send; provisional transcripts for speculative LLM start.

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
- **The Pi: SSH key login only, no password in the repository or the session.** Copy keys with `ssh-copy-id` in a normal terminal (the prompt cannot appear inside an assistant session). No sudo on the Pi, so nothing is installed system-wide; `uv pip install` into its venv is fine.
- **Speech-server tokens are generated per session, kept in mode-600 files and never printed or committed.** Off loopback the server refuses to start without one.
- **Do not change the owner's laptop settings** (mute, volume, firewall) without asking; the firewall rule used here is runtime-only and the owner added it.
- **Format only the files you edit.** `ruff format scripts/` reformats `scripts/pi_synth_report.py` (an older file) and puts an unrelated change in your commit.
- **State what a number rests on** and say when a setting was chosen after seeing the data (the 3-of-3 at 70% is not held out; the 6-of-6 at 80% is).
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

# Pi bridge (see docs/PI-BRIDGE.md): server on the laptop bound to the cable address, then on the Pi
LAN_SPEECH_TOKEN=... uv run --group laptop python -W ignore -m lan_speech --host <cable ip> --port 8770 --stt-model parakeet --entities config/entities-docs.json
LAN_SPEECH_TOKEN=... .venv/bin/python -u scripts/pi_bridge.py --server ws://<laptop cable ip>:8770 --pause 1.2   # Pi: Pi mic and speaker

# Pi as loudspeaker (see docs/PI-REMOTE-SPEAKER.md)
LAN_SPEECH_TOKEN=... .venv/bin/python -u scripts/pi_speaker.py --device CD002 --rate 48000                        # Pi
LAN_SPEECH_TOKEN=... uv run --group laptop python -W ignore -m doc_chat --mic --docs corpus/harry-potter --llm gemini --stream \
    --play-remote ws://<pi>:8780 --vad both --vad-confidence 0.5 --silero-kofn 7/10 --level-kofn 3/8                # laptop
LAN_SPEECH_TOKEN=... uv run --group laptop python scripts/remote_echo_check.py --url ws://<pi>:8780 --label "pi 80%" \
    --out out/remote_echo/x --talk-over 6 --repeat 2                                                                # echo and guard test (stay quiet / talk on the beep)
uv run --group laptop python scripts/guard_replay.py out/remote_echo/fresh80 [--sweep]                              # replay the guard offline
uv run --group laptop python scripts/pi_record.py --out out/pi_bridge/x.wav --seconds 240 --in-device default --in-rate 16000   # record a mic
uv run --group laptop python scripts/eot_replay.py out/pi_bridge/eot-session.wav                                    # end-of-turn rules on a recording

# the gate
uv run --group laptop ruff check src tests scripts && uv run --group laptop ruff format --check src tests scripts \
  && uv run --group laptop mypy && uv run --group laptop pytest -q
```

---

## 10. Resuming with an AI assistant

Paste this into a new session in the repository root:

> Read `docs/HANDOFF.md` fully (start with section 0), then `docs/PI-REMOTE-SPEAKER.md`, `docs/PI-BRIDGE.md` and `docs/PI-RESULTS.md`. Follow the working agreements in section 8. Check section 0 for what is running or uncommitted on the Pi, then do section 7 from the first item that is not blocked on me. Report what each result shows and what it does not (say how many repetitions it rests on), and propose the next change before making it. Do not touch my untracked files and do not push without asking.

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
- **k-of-n rule:** speech when at least k of the last n windows (or blocks) are speech, instead of an unbroken run; speech has quiet windows inside it (the Pi pilot's "7 of 10").
- **Echo guard (`BargeInGuard`):** says "the user" only when Silero hears speech AND the microphone is clearly louder than the echo expected from what is being played.
- **Bridge:** `scripts/pi_bridge.py`, the Pi's microphone and speaker joined to the laptop's speech models over the LAN.
- **Ablation:** the same test with parts of the system switched on and off (`docs/INTERRUPTS.md`).
