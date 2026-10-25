# Handoff: laptop speech, the doc_chat test client, interruptions, the Pi, and the Pi-as-loudspeaker setup

**First written:** 2026-10-21; **updated:** 2026-10-24 (Philippine time). **Tip of the stack:** branch `agy/interrupts`, about 85 commits ahead of `main`; `git log origin/agy/interrupts -1` shows the last pushed commit, and some later commits may be local only (section 0). **Tests at the tip (2026-10-24):** 638 pass, 9 skipped, 2 xfailed, 1 fails (the known `test_flow` case, see section 0); the Pipecat project passes its own 24 (`make pipeline-test`); ruff and mypy clean on everything touched.
**Audience:** the owner picking this up on the Raspberry Pi, or any person or AI assistant continuing the work with no memory of how it got here. Everything needed is in the repository; this file is the map.

---

## 0. Status as of 2026-10-24 (read this first)

Sections 1-11 were written on 2026-10-21, before the Pi was tested. This section says what happened since; where it disagrees with a later section, this one wins (the later sections are updated below where it matters).

**Resume here (written at the end of 2026-10-25, Philippine time).** This block supersedes the 2026-10-24 one below where they disagree. Branch `agy/interrupts`; pushed up to `449418b`; **local only: `dc8944a` (R4), `402c215` (R6), `e442316` (filler log) and this wrap-up commit.** Push only when the owner asks.

* **Where we stand against the industry:** `docs/INDUSTRY-COMPARISON.md` (read it before researching anything). End of speech to first sound: **median 2.58 s, p90 3.03 s** over 27 ordinary turns of sessions 4-6, against "good 0.6-0.9 s, sluggish over 1.2 s". 38% of it is the end-of-turn hold plus recogniser (transcript ready 0.98 s), 49% the LLM's first response (1.27 s), 14% TTS (0.35 s). Interrupting works (cut-off to silence 1 ms). Realistic target for a tool-calling agent: 1.5-2 s.
* **Landed this session** (details and numbers in `docs/LATENCY.md`): the `say` flush and filler fixes (`0061e18`); fillers spaced 1.5 s (`gap_s`); **R5** one LLM request for sentence-result tools and `smart_home` commands that worked, with the veto in `TurnTap._result` and failure sentences still going to the model; the `say` **held 0.6 s and dropped when the answer arrives first**, canned lines follow the `say`, stall 1.2 -> 1.8 s; **R4** `HedgedAgent` (`pipeline/src/voice_pipeline/hedge.py`): two Gemini pipelines per turn, the first to answer wins, tool handlers gated so only the winner runs them (`--hedge SECONDS`, 0 = both at once, -1 = off); **R6** `scripts/demo_warmup.py` (network, Pi, speech, one request per tool; READY or the FAIL lines); `filler_log` in the turn record.
* **Spoken sessions 3-6** (`out/sessions/fillers-3..6`): one LLM request per tool turn; no canned filler on any ordinary turn; slow-turn test (agent server frozen with `kill -STOP`, resumed with `kill -CONT`) gave stall, 1.5 s gap, notice, apology at 8 s, then recovery, twice. Hedge winners were pipeline 0: 9, 1: 12 (session 5) and 0: 3, 1: 7 (session 6). Gemini accepts the next user turn with or without a model reply after a tool result (the research claim that it rejects it was wrong for this model).
* **Open, ranked:** (1) the speech recogniser mishears the demo's names ("Baglio", "June" for Dune, "Ann Carson"); the speech server's entity list (`config/entities-docs.json`) is built for the Harry Potter documents, so add the demo names and test on clips first (changes what the server hears: ask the owner); (2) end-of-turn hold 0.64 s and early decode are the next speed levers (`docs/INDUSTRY-COMPARISON.md`); (3) ticket 25 (smaller first TTS chunk); (4) home forecast background refresh (second half of R5, not done); (5) everything in section 7 below that is not about the fillers.
* **Servers at wrap-up:** the Pi speaker and `pi_device_mcp` stop themselves about 09:30 on 2026-10-25; the speech server (127.0.0.1:8770) and the hedged agent server (127.0.0.1:8781) were started 07:33 / 08:2x with a 2 h timeout; the speech token is in `~/.config/lan_speech_token` on both sides (fresh this session). To start again follow the "To start a spoken session" block below and run `scripts/demo_warmup.py` before talking.
* **Known test failure:** `tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents` needs a running speech server and fails without one (it did before this session too).

* **Echo: solved for the scripted interruptions.** `--aec --duck-db -15 --strip-echo-prefix --barge-warmup-s 1.0`, with pre-roll 0.8 s and canceller lead 0 as the defaults, gave 0/30 early cut-offs, 0/12 echo transcripts and 0 wrong actions over two live sessions. A bare one-word "Stop." is still missed (Silero doesn't hold 7 of 10 windows on it); **the owner decided it is not required**: a stop word plus more words is the interruption. The "Pi's first reply after idle arrives 0.55 s late" below was misattributed: it was a cold `scipy.signal` import (~0.8 s), now loaded at connect. Details: `docs/LIVE-AGENT.md` (the 2026-10-24 sections) and `.scratch/echo-next/PLAN.md`.
* **Real MCP tools** in the agent: the official time server, our `web_info` (Open-Meteo weather, Wikipedia; keyless) and a Pi device server (`scripts/pi_device_mcp.py`: volume and status over HTTP with the session token). TIME and WEATHER left `smart_home` (17 intents). Dry run 88/92 and a spoken check 8/8 routed right. `docs/MCP-SERVERS.md` ("The agent's real tools").
* **Latency is the open problem**, and the owner's main worry for the demo. End of speech to first sound took 3.5-10.9 s in the spoken check, because the LLM's first response spikes (0.7-7.7 s) and every answer is a tool turn with two LLM requests. The safeguards are **landed** (agy tickets 01-03, commits `deaa6d5`, `891f756`): a **fast path** (common commands skip the LLM), the tools' **`say` sentence**, and **doc_chat `--fillers`** (stall at 1.2 s after the transcript, the tool's line, notice at 2.5 s, give-up at 8 s). One spoken session (`out/sessions/fillers-1`): give-up and fast path work; **two bugs to fix next**: the `say` sentence is held back by the sentence splitter until the tool result arrives (so it does not help yet), and filler audio leaks into the next transcript ("Almost there. Got it."). Both fixes are written up in `docs/LATENCY.md`. The `say` argument cost 3-4 exact on the dry run (82/87 against 85-88); a prompt fix recovered part of it.
* **LLM spike probe, final** (44 pairs): one request p50 1.33 s, p90 2.86 s, 8/88 over 4 s; the faster of two simultaneous requests p50 1.18 s, p90 1.94 s; in 7 of 11 slow pairs the twin was fast. **This supports R4 (send two, take the first); the owner decides.** `docs/LATENCY.md`.
* **Nothing is running on the laptop** (stopped at wrap-up). The Pi's `pi_speaker` and `pi_device_mcp` were started 20:08 with a 2 h timeout and stop themselves; the Pi speaker was set back to 80%.
* **To start a spoken session** (nothing persists): the speech token that was used lived in an earlier assistant session's scratch folder and may be gone, so **make a fresh one on both sides** (`docs/PI-BRIDGE.md`: a mode-600 file `~/.config/lan_speech_token` on the Pi and on the laptop). Then in this order: the Pi speaker and `pi_device_mcp` (section 9; ask the owner first; the Pi is reached over its IPv6 link-local address, the IPv4 one has no known_hosts entry), the speech server, `make agent-server` (restart it after any `pipeline/` change), then `doc_chat` with the section 9 command plus `--fillers`. The owner runs `doc_chat` themselves (it needs the mic). Background jobs die after 2 h.
* **Model:** `gemini-3.1-flash-lite` stays. Jev Router on OpenRouter was 3x slower to the first token, picked the right tool 14/16 against 16/16, and cost 10x as much.

**State at the end of 2026-10-23** (kept for the record).

* **The repository** is committed and pushed; nothing of value is only on the laptop except recordings under `out/` (git-ignored). Gates at the tip: `ruff`, `ruff format --check`, `mypy` clean; `uv run --group laptop pytest -q` 597 passed, 9 skipped, 2 xfailed, 1 failed (known); `make pipeline-check pipeline-test` clean, 16 passed.
* **Nothing is running.** The speech server, the agent server and the Pi speaker were all stopped. Start them in this order (commands in section 9): fresh speech token on both sides, the Pi speaker, the speech server, `make agent-server`, then `doc_chat`. The Pi speaker stops itself after 2 hours; the agent server must be running whenever someone speaks, or every answer fails with "Sorry, I couldn't reach the language model".
* **What was done after the Pi work**, each with its own document: the pending tickets were reassessed for the new direction and four added (`.scratch/project2-voice-agent/DESIGN.md`, D5-D7, order at the end); an LLM choice was researched and measured (`docs/SMART-HOME-TOOL.md`: native Gemini 3.1 flash-lite with a router prompt, 50/50 on the dry run); a `smart_home` tool and a separate Pipecat project `pipeline/` (Pipecat pins `onnxruntime~=1.24`, which the speech models must not take); the agent, with a docs MCP server, in the live loop (`docs/LIVE-AGENT.md`); echo cancellation across the two devices and duck-then-stop (`docs/AEC-REMOTE-SPEAKER.md`, `LIVE-AGENT.md`, `--aec`, `--duck-db`).
* **Best configuration so far** for the live loop: `--aec --duck-db -15` with the guard flags below. The duck is the part that stops the bot's own echo from interrupting it (0 of 14 and 0 of 16 early cut-offs against 7 of 20 with the canceller alone); the canceller is the part that keeps its words out of the transcript (0 of 7 interruption transcripts with its words in them, against about 6 of 8, counted by eye) but costs some opening words (2 of 6 garbled in each run that had it). One session per condition, 8 scripted interruptions each: direction, not rates.
* **Known weak spots, in order of how much they hurt:** the Pi's first reply after idle arrives 0.55 s late; home commands take 3.3 s from your last word to the first sound (two LLM requests); opening words are misrecognised (one dropped "Turn off" turned the lights on); the ducked speaker dips on some quiet replies. Section 6 and 7 have the full lists and the order of work.
* **Who builds code:** agy writes code from level-3 tickets (full code in each ticket, reference code verified by the assistant in a scratch copy first), in a bubblewrap fence that only the owner launches (`.scratch/*/agy_work/fence.sh`, section 8). Everything agy produced so far came back byte-identical to the tickets.

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
| Pending tickets reassessed for the new direction (local speech, laptop mic + Pi loudspeaker, Pipecat kept) | 11 tickets amended in place, 4 added (25 laddered TTS, 26 first words, 27 end of turn on spontaneous speech, 28 doc_chat as a tool); order at the end of the design doc | `.scratch/project2-voice-agent/DESIGN.md` (D5-D7), `tickets/` |
| Fast LLM choice: Gemini models, Groq and cheap hosted models researched (agy, search-only) and measured from here | Google AI Studio has no region setting (Vertex would); Groq serves this network from Dallas | `.scratch/speech-hosting/AGY-GEMINI-FAST.md`, `AGY-CHEAP-LLM.md`, `docs/SMART-HOME-TOOL.md` |
| One `smart_home(intent, slot)` tool for the 19 ME2 commands in a text-only Pipecat pipeline (separate uv project `pipeline/`; code written by agy from level-3 tickets inside a bubblewrap fence) and a 47-prompt dry run over three models and four system prompts | **Native Gemini 3.1 flash-lite + router prompt: 50 of 50 exact, no false calls, no leaks; first spoken reply 0.52 s p50 (plain) / 1.31 s (tool turn) from LLM input.** Groq 8B 26-40 of 48 with tool syntax spoken aloud; Groq 70B 49 of 50 but slower | `docs/SMART-HOME-TOOL.md`, `pipeline/`, `.scratch/smart-home-tool/` |
| The agent in the live loop (`doc_chat --llm agent`: Pipecat + native Gemini, `smart_home` with open slots and a phonebook, docs MCP server), built by agy in two waves, then spoken sessions (quiet, interruptions) | Tool routing right on every quiet turn; pivots acted on 6/6; stop to first audio 3.3 s p50 on home commands (two LLM requests); beep to stop 0.41 s p50. **Echo in the transcripts breaks plain "Stop." and made it act on its own voice once**; one dropped-first-words wrong action | `docs/LIVE-AGENT.md` |
| Cross-device echo cancellation (WebRTC canceller, reference = the audio sent to the Pi), measured offline | The two sound cards drift 137 ppm; with the reference fed ahead the recogniser heard the full echoed reply on the raw mic in 6/6 echo-only clips and 0-7 words after; your speech kept (5/6 vs 4/6). Not yet in the live loop | `docs/AEC-REMOTE-SPEAKER.md`, `scripts/echo_path_probe.py`, `scripts/aec_offline.py` |
| Echo canceller across the two devices (`doc_chat --aec`) and duck-then-stop (`--duck-db -15`) built by agy (level-3 tickets, byte-identical) and tried live with the speaker in front of the laptop, same 8 scripted interruptions | Echo-caused failures gone (no transcript began with the bot's words, 0 spurious `STOP`, no action on its own voice, a bare "Stop!" heard clean) but 2 of 8 interruptions were garbled at the first words, 1 missed (slow model), false ducks on 3 of 7 quiet replies, 1 false stop; 5 of 8 right. The A/B then showed the duck is what stops the bot's own echo from interrupting it (7 of 20 early cut-offs with `--aec` alone, 0 with the duck, with or without cancelling) and the canceller is what cleans the transcript but costs opening words; text guards and a guard-only canceller are next. One session per condition | `docs/LIVE-AGENT.md` (session 3), `.scratch/echo-fix/` |
| Main's ticket 12 (barge-in gate) amended to the 7-of-10 rule | On local branch `docs/ticket-12-kofn-gate` (`ce0851a`, off main, not pushed). Ticket 12 itself is **not built**: it needs the agent pipeline (tickets 05, 10, 11), which the owner parked to focus on interrupts and STT | `.scratch/project2-voice-agent/tickets/12-...md` |

**Tests.** The Pipecat project has its own suite: `make pipeline-check pipeline-test` (ruff, mypy strict, 10 tests). `uv run --group laptop pytest -q` gives about 570 passed, 9 skipped, 2 xfailed and **1 failed** (counts move as tests are added): `tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents`. It connects to a speech server on `127.0.0.1:8765` and fails the same way on the commit before this work; start the server (section 9) and it passes. `ruff check` over the whole `scripts/` folder still reports E501 in `scripts/pi_synth_report.py` (an older file). **Do not run `ruff format` over `scripts/`**: it reformats that file and you will commit an unrelated change; format only the files you edit.

**Doc-chat flags the guard needs (2026-10-23):** `--vad both --vad-confidence 0.5 --silero-kofn 7/10 --level-kofn 3/8 --barge-echo-quantile 0.95 --barge-echo-window 60 --barge-echo-margin 1.2 --barge-echo-prior 0.8`. Without the last four the assistant interrupts itself on every reply with the Pi speaker in the room. Measurement flags: `--cue-beep 2.5 --cue-every 2` (a beep on every second reply to talk over), `--trace-guard` with `--record DIR` (a line per mic block in `DIR/guard.jsonl`), and `scripts/cue_report.py DIR` to score the session.

**State of the physical setup right now** (all of it disappears; rebuild with `docs/PI-BRIDGE.md` and `docs/PI-REMOTE-SPEAKER.md`):

* The Pi (`rpi-anavarez`, repo at `~/Documents/AI-231-ME3`) is reached by an Ethernet cable to the laptop (link-local `169.254.x.x`) and by SSH with a key (`~/.ssh/id_ed25519_pi` on the laptop; Pi address `fe80::18ac:b795:c102:5eb1%enp0s20f0u2c2`, or `169.254.230.219`). No sudo on the Pi.
* The Pi's repository (still at the old commit `4a9b3ef`) has **uncommitted copies** of `scripts/pi_bridge.py`, `scripts/pi_record.py`, `scripts/pi_speaker.py` and `src/doc_chat/bargein.py`; on 2026-10-23 `src/doc_chat/playback.py` and `scripts/pi_speaker.py` were replaced by the current ones (the duck needs both; originals in `~/pi_backup_20261023` on the Pi). Running `git checkout -- . && git pull` there would need the Pi to reach GitHub; copying the two files with `scp` worked. The Pi runs no other new code.
* `websockets` was installed into the Pi's venv with `uv pip install` (it is not in the `pi` dependency group). The speech token is in a mode-600 file on the Pi (`~/.config/lan_speech_token`); it was generated for the session and is not in the repository.
* The laptop firewall rule that lets the Pi reach the speech server is runtime-only (`firewall-cmd --zone=trusted --change-interface=<cable>`); it is gone after a reboot. The laptop's own speaker output was **muted**; nothing here changes that.
* Pi sink volume was left at 80%. The speech token for the session was kept in a scratch file of the assistant's session and in `~/.config/lan_speech_token` on the Pi; it is not in the repository, so **make a new one** (`docs/PI-BRIDGE.md`) and put it on both sides.
* The "speaker in front of the laptop" placement used for the last four sessions was not measured (distance and angle were not recorded); record them next time.

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
| `src/doc_chat/aec.py`, `agent_client.py`; `bargein.py`/`playback.py`/`speech.py` additions | `EchoCanceller` (WebRTC via LiveKit's `AudioProcessingModule`, reference = what is sent to the Pi, 50 ms lead, `--aec`); `AgentChatModel` (`--llm agent`, streams from the agent server); `Ducker`, `SileroOnset.recent_speech`, `Player/RemotePlayer.set_gain` (`--duck-db`), `MicHub(canceller=)` |
| `pipeline/` | a separate uv project (own lock and venv) for Pipecat 1.13: `voice_pipeline.agent` (one pipeline, native Gemini), `agent_tools`, `tools/smart_home.py` (19 ME2 intents, open slots, JSON phonebook), `agent_server.py` (ws://127.0.0.1:8781), `dryrun.py` (72 prompts); `README.md` there and `docs/SMART-HOME-TOOL.md` |
| `src/voice_agent/mcp_servers/docs.py` | the docs MCP server (fastmcp 2.14.7): `search_docs` over the McCarthy corpus |
| `src/voice_agent/mcp_servers/web_info.py` | the weather + Wikipedia MCP server (fastmcp, stdio, keyless): `weather(place, day)`, `wiki_summary(topic)`; `docs/MCP-SERVERS.md` |
| `scripts/pi_device_mcp.py` | the Pi's device MCP server (runs on the Pi, HTTP on 8790, session token): `get_volume`, `set_volume`, `device_status` |
| `pipeline/src/voice_pipeline/agent_tools.py`, `latency_probe.py` | the agent's tools (`standard_servers`: docs, time, web_info, and the Pi's when reachable; `--pi-url`); the LLM spike probe (`docs/LATENCY.md`) |
| `.scratch/latency/` | the latency safeguards, not yet landed: plan, reviewed phrases, reference code (`skeleton/`, `overlay/`), tickets 01-03, the agy fence |

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
| Echo cancellation measurements | `echo_path_probe.py` (plays noise and a tone through the Pi, analyses the path: delay drift, coherence, nonlinearity), `aec_offline.py` (runs the canceller on a `remote_echo_check.py` recording and reads the transcripts; `--with livekit`) |

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
| `out/remote_echo/probe-80` | the echo-path probe (137 ppm drift) |
| `out/sessions/agent-quiet-1`, `agent-interrupt-2`, `agent-interrupt-aec-1`, `agent-interrupt-ab-aec`, `agent-interrupt-ab-duck` | the agent's spoken sessions (quiet, interruptions without and with the canceller, the two A/B arms); `-idle-bug`, `-buggy`, `-0-aborted` suffixes are discarded runs (a Pipecat idle timeout, an uneven script, a stray first turn) |
| `pipeline/out/` | dry-run rows and the agent server's per-turn logs |
| `.scratch/*/backup-*`, `.scratch/*/skeleton`, `.scratch/*/agy_work/{work,orig}` | scratch copies and backups from the agy runs (git-ignored or untracked); safe to delete |

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

Added late on 2026-10-23 (agent, echo canceller, duck, agy runs):

- **zsh does not split a variable holding a command or a file list.** `G="git -c ..."; $G status` and `cp $FILES` fail ("command not found", "cannot stat 'a b c'"). It bit four times, once after a `cp` loop had already half-run. Write the command out or use a bash array in a script.
- **A background job started from the assistant is stopped at its time limit (2 h).** The agent server went away in the middle of a test and the first answer failed. Check `ss -ltn | grep 8781` and a typed request before every spoken session.
- **Pipecat's pipeline worker idles out after about 5 minutes** and cancels itself while the server keeps accepting requests (every request then times out after 20 s). `idle_timeout_secs=None` in `Agent`.
- **Gemini's OpenAI-compatible endpoint cannot be used with tools from Pipecat** (an empty phantom call, then a dropped `thought_signature`). Use the native `GoogleLLMService`; details in `docs/SMART-HOME-TOOL.md`. Groq `llama-3.1-8b` is fast but speaks its tool calls as text (26-40 of 48 right).
- **The first offline echo-cancellation result ("5 dB") was a harness fault, not the canceller**: the two sound cards drift 137 ppm, and a fixed-delay reference has no coherence with the microphone. Test a measurement tool on a synthetic case with a known answer first (`echo_path_probe.py` has three).
- **A canceller test that hands over all the reference at once loses most of it** (the buffer keeps 3 s). Feed it as the player does, about 0.25 s ahead.
- **A reference lead of 200 ms looked natural and was wrong**: on a real recording 0-100 ms held for path delays of 0-300 ms and 200 ms or more fell apart as the delay grew. The default is 50 ms.
- **Total-power reduction in dB is not a measure when the echo is near the room floor** (the canceller cannot go below the floor, and its suppression also quietens the floor). Count detector fires and read the recogniser's transcripts instead.
- **A spoken test script with mixed instructions gives an unusable session** (plain stops and questions run unevenly; unscripted apologies to the bot get answered). Give one rule per session ("at the beep say X; do not answer the assistant").
- **agy:** commands the owner types with `!` must be typed in bash mode, or they reach the assistant as chat text; files under `/tmp` of the assistant's sandbox are invisible to the owner's shell (keep the work under `.scratch/`); on this network agy's start-up check fails now and then (IPv6 resets) and `fence.sh` retries it; a network drop can end a run mid-ticket (relaunch: copying the same code twice gives the same files); a ticket generator must count prompts and tests (a miscount of 47 vs 48 and 7 vs 8 was caught only by dry-running every Proof command against the ticket text).
- **A scorer regex missed spoken tool syntax** (`function=smart_home>`, `<LIGHT_OFF></LIGHT_OFF>`); widened, with tests.

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
| Pipecat stays for the live pipeline; `doc_chat` becomes a tool inside it (ticket 28) | **settled (owner, 2026-10-23)** | `.scratch/project2-voice-agent/DESIGN.md` D6 |
| Pipecat code lives in a separate uv project `pipeline/` | **settled** | pipecat pins onnxruntime~=1.24 / soundfile~=0.13; the root keeps 1.31 / 0.14 for the speech models (`docs/SMART-HOME-TOOL.md`) |
| Fast LLM: Gemini `gemini-3.1-flash-lite` via Pipecat's native `GoogleLLMService`, system prompt `system_v2_router.txt` | **settled as the baseline (owner)** | 50/50 on the dry run and the lowest latency; Gemini's OpenAI-compatible endpoint breaks tool round trips from Pipecat (thought signatures) |
| One plain `smart_home` function for home control (not MCP; the ME2 UI may become an MCP server later) | **settled (owner)** | `docs/SMART-HOME-TOOL.md` |
| A parallel "ornamental" fast model (Groq) beside the working model | **not adopted for now** | native Gemini already starts plain replies at 0.52 s; on tool turns an acknowledgement or a templated result at tool-call time closes the gap more cheaply |
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
11. Main's agent pipeline exists only in a light form: `pipeline/` has the agent core (native Gemini, `smart_home`, the docs MCP server) as a text-only Pipecat pipeline behind a WebSocket, and the live audio path is still `doc_chat`. Tickets 05, 10, 11, 12 and 28 are partly realised (`.scratch/project2-voice-agent/DESIGN.md`, progress note at the end); the real Pipecat audio pipeline with the guard as a processor is not built.
12. ~~**The Pi's first reply after idle arrives about 0.55 s late** (the device wakes).~~ *Resolved 2026-10-24: the cause was a cold `scipy.signal` import on the laptop (~0.8 s), now loaded at connect; the Pi needs no keep-awake for idles up to 20 s.* It adds to latency and mis-times the canceller for that reply. Idea: keep the Pi's sink awake (inaudible silence) in `pi_speaker.py`.
13. **The canceller costs opening words** (2 of 6 garbled in each run that had it) and the combined run's beep to stop was 1.34 s p50 (0.36-0.41 s in the other runs; includes the owner's reaction time). Cause not separated beyond the A/B. Try the canceller for the guard only with the recogniser on the raw audio and a text guard.
14. **False ducks on quiet replies** (3 of 7 in the combined run): audible dips for 0.6 s.
15. **Home commands cost two LLM requests** (the call at about 1 s, the confirmation at about 2 s): 3.3 s p50 from your last word to the first sound against 2.3-2.6 s before tools. An acknowledgement or a templated result at the tool call would recover most of it.
16. **Misrecognised first words now cause wrong actions** (a dropped "Turn off" turned the lights on). Ticket 26.
17. **The three servers (speech, agent, Pi speaker) are started by hand and nothing persists.** There is no health check; a stopped agent server shows up as "couldn't reach the language model".
18. **API limits were not verified.** The Gemini key's quota (the agy report says a free tier is 20 requests a day, unconfirmed), OpenRouter credit, and whether a billed key is in use are unknown; watch for HTTP 429.
19. Only one speaker, one room and one placement for every agent session; counts are 8 interruptions per condition.

---

## 7. Next actions, in order (updated 2026-10-25; items 2-4 of the 2026-10-24 list are done: filler fixes, the repeat spoken session, R4/R5/R6)

1. **(Owner)** Decide or ask the professor: is "laptop microphone, Pi loudspeaker" acceptable (section 6, item 1)? Everything below branches on it. Choose how to land the branches (`docs/BRANCHES.md`; about 85 commits ahead of `main`).
2. **(Assistant; direct or a fourth agy ticket, the owner's choice)** The four fixes from the fillers session (`docs/LATENCY.md`): the `say` as its own event spoken directly (and the tool event always sent), strip filler phrases anywhere in the transcript, the stall waits if answer text has already arrived. Tests first.
3. **(Both)** Repeat the same spoken script (fast command, weather, Wikipedia, a Pi tool, a talk-over, the paused-agent give-up) with `--fillers` and the echo flags; then decide whether to keep `say`.
4. **(Owner, then assistant)** Decide R4 (the probe supports it). Then R6, the demo warm-up (touch every server and tool, network check, keep-alive), and R5 (one LLM request for tools whose answer is already a sentence, after a Gemini history test; home forecast refreshed every 10 minutes). Plan: `.scratch/latency/PLAN.md`.
5. **(Assistant)** Delete or keep `--level-kofn-ducked` (it did not help); a minimal system prompt whose routing lives in each tool's description (the owner asked for "minimal yet scalable to all tools").
6. **(Both)** Ticket 26 (first words of a question), 27 (end of turn on spontaneous speech, record on the laptop mic), 25 (laddered TTS, about 0.15-0.2 s).
7. **(Both)** Repeat the spoken sessions with a second speaker, another room, and a recorded speaker distance; try 90% volume now that the canceller exists.
8. **(Assistant)** Tickets 22 (lift `doc_chat/speech.py` into the `lan` backend of `audio_engine`) and 16 (Pi speaker deploy and preflight) are unblocked; then the real Pipecat pipeline (ticket 11) with the guard and the duck as processors, then 10, 28, 18, 19 (laptop-side playback), 20, 15 (shrunk), 17. Pipecat code stays in `pipeline/` (it cannot import the root packages without taking their dependency pins).
9. Parked, in order of value: coughs and knocks recorded for real; server-side resume for early send; provisional transcripts for speculative LLM start; a Groq comparison on answer quality (it needs a larger model than the 8B).

---

## 8. Working agreements (follow these; they came from the owner)

- **No model on the Pi except the Silero VAD.** The Pi test tool runs nothing else.
- **Never read or print secrets.** Do not open `~/.secrets/*`, `.env`, or any `*key*`/`*token*` file; code reads keys at run time and prints only OK/failure. The Gemini key lives in `~/.secrets/google-ai-studio/` (`api-key.txt` or `api_key.txt`); Qwen's in `~/.secrets/onit/`.
- **Do not touch the owner's untracked files:** `docs/VOICE-AGENT-EVALUATION-METRICS.md`, `kek.txt`, `resources/building-voice-agents/`. Add files to git **by name**, never `git add -A` or `git add docs`.
- **agy:** research prompts are search-only (`agy --sandbox --mode plan`, no shell, no file reads). **Code-writing runs** use `--dangerously-skip-permissions` only inside the fence in `.scratch/*/agy_work/fence.sh` (bubblewrap: everything read-only except one work copy and agy's own state; `~/.secrets` and `~/.ssh` hidden; empty environment; `gemini-3.8-flash-high`; 60 minute limit; a retry for the start-up check) and **only the owner launches them** (`!` in bash mode, or a terminal). The method is `coding-with-simple-models` at level 3: tickets that contain the full code, function lists, line caps and a forbidden-additions list; the assistant writes and tests the reference code in a scratch copy first, dry-runs every Proof command on the code extracted from the ticket text, and reviews each wave byte-for-byte before copying it into the repository (originals backed up).
- **Never kill processes that are not yours.** The owner's own speech server may use port 8765; tests use 8766-8767.
- **Gate every commit** on `ruff check`, `ruff format --check`, `mypy` (packages in `pyproject.toml`) and `pytest`.
- **Commit trailer:** `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. In this repository use `git -c safe.directory=$PWD ...` (the checkout is on a mounted drive).
- **Report faithfully:** say what failed, what was not measured, and how many repetitions a number rests on (most interruption numbers rest on 2).
- **The Pi: SSH key login only, no password in the repository or the session.** Copy keys with `ssh-copy-id` in a normal terminal (the prompt cannot appear inside an assistant session). No sudo on the Pi, so nothing is installed system-wide; `uv pip install` into its venv is fine.
- **Speech-server tokens are generated per session, kept in mode-600 files and never printed or committed.** Off loopback the server refuses to start without one.
- **Do not change the owner's laptop settings** (mute, volume, firewall) without asking; the firewall rule used here is runtime-only and the owner added it.
- **Format only the files you edit.** `ruff format scripts/` reformats `scripts/pi_synth_report.py` (an older file) and puts an unrelated change in your commit.
- **State what a number rests on** and say when a setting was chosen after seeing the data (the 3-of-3 at 70% is not held out; the 6-of-6 at 80% is).
- **Commit and push when the owner says so; they usually ask after each milestone.** Several commits by topic, files added by name, a key scan first, and the numbers in the message state what they rest on.
- **Before a spoken session** check the servers (speech, agent, Pi speaker), tell the owner exactly what to say and when, and keep the script to one rule per session.
- **Ask before** anything loud (the probe plays noise at 80%), anything that changes a dependency, or anything on the Pi.
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

# the agent in the live loop (docs/LIVE-AGENT.md); start in this order, nothing persists
# 1. a fresh token in a mode-600 file on both sides (docs/PI-BRIDGE.md); the Pi speaker (stops itself after 2 h)
ssh -i ~/.ssh/id_ed25519_pi rpi-anavarez@169.254.230.219 'cd ~/Documents/AI-231-ME3 && LD_LIBRARY_PATH=$HOME/.local/portaudio/usr/lib/aarch64-linux-gnu LAN_SPEECH_TOKEN=$(cat ~/.config/lan_speech_token) timeout 7200 .venv/bin/python -u scripts/pi_speaker.py --device CD002 --rate 48000'
# 2. the speech server on the cable address (see "Pi bridge" above); 3. the agent server (reads ~/.secrets/google-ai-studio/)
make agent-server                                    # ws://127.0.0.1:8781; one row per turn in pipeline/out/agent_server/
#    Pi device tools (docs/MCP-SERVERS.md): on the Pi, LAN_SPEECH_TOKEN=$(cat ~/.config/lan_speech_token) .venv/bin/python -u scripts/pi_device_mcp.py --port 8790
#    the agent server needs the same token: LAN_SPEECH_TOKEN=... make agent-server
# 4. the live loop; best configuration (2026-10-24) is --aec --duck-db -15 --strip-echo-prefix --barge-warmup-s 1.0;
#    add --cue-beep 2.5 --cue-every 1 for the interruption script; --fillers once the latency tickets land
LAN_SPEECH_TOKEN=... uv run --group laptop python -W ignore -u -m doc_chat --mic --llm agent --no-docs --stream \
    --server ws://<laptop cable ip>:8770 --play-remote ws://<pi>:8780 --vad both --vad-confidence 0.5 --silero-kofn 7/10 --level-kofn 3/8 \
    --barge-echo-quantile 0.95 --barge-echo-window 60 --barge-echo-margin 1.2 --barge-echo-prior 0.8 --trace-guard \
    --history 4 --max-words 90 --aec --duck-db -15 --strip-echo-prefix --barge-warmup-s 1.0 --record out/sessions/<name>
uv run --group laptop python scripts/cue_report.py out/sessions/<name>          # score a cue-beep session
uv run --group laptop python scripts/demo_warmup.py   # R6: network, Pi, speech and one request per tool; READY or the FAIL lines; --keepalive 60 to stay warm
make agent-dryrun                                    # 87 prompts, native Gemini (costs cents); LIMIT=5 for a short one
cd pipeline && LAN_SPEECH_TOKEN=... uv run python -m voice_pipeline.latency_probe --minutes 45 --every 60   # LLM spike probe (well under a cent)
make pipeline-check pipeline-test                    # the Pipecat project's own gates
# echo cancellation measurements (stay silent while the probe plays noise at the Pi's volume)
LAN_SPEECH_TOKEN=... uv run --group laptop python scripts/echo_path_probe.py --url ws://<pi>:8780 --out out/remote_echo/probe-80
uv run --group laptop --with livekit python scripts/aec_offline.py out/remote_echo/fresh80 --delays 300 --ppm 137 [--stt]
# put new code on the Pi (it is not a git pull): the duck needs both files
scp -i ~/.ssh/id_ed25519_pi src/doc_chat/playback.py rpi-anavarez@169.254.230.219:Documents/AI-231-ME3/src/doc_chat/
scp -i ~/.ssh/id_ed25519_pi scripts/pi_speaker.py scripts/pi_device_mcp.py rpi-anavarez@169.254.230.219:Documents/AI-231-ME3/scripts/

# the gate
uv run --group laptop ruff check src tests scripts && uv run --group laptop ruff format --check src tests scripts \
  && uv run --group laptop mypy && uv run --group laptop pytest -q
```

---

## 10. Resuming with an AI assistant

Paste this into a new session in the repository root:

> Read `docs/HANDOFF.md` fully (start with section 0, "Resume here"), then `docs/LIVE-AGENT.md`, `docs/AEC-REMOTE-SPEAKER.md`, `docs/SMART-HOME-TOOL.md`, `docs/PI-REMOTE-SPEAKER.md`, `docs/PI-BRIDGE.md` and `docs/PI-RESULTS.md`. Follow the working agreements in section 8. Check section 0 for what is running or uncommitted on the Pi, then do section 7 from the first item that is not blocked on me. Report what each result shows and what it does not (say how many repetitions it rests on), and propose the next change before making it. Do not touch my untracked files and do not push without asking.

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
- **Duck / duck-then-stop:** turn the speaker down (here 15 dB) at the first sign of a voice over the answer, stop it if the guard then fires, restore it if no barge-in follows (`--duck-db`).
- **Reference (for echo cancellation):** the audio sent to the speaker; the canceller subtracts what it predicts the room returns. **Lead:** how far ahead of the microphone the reference is fed (50 ms here). **ppm drift:** the two sound cards' clocks differ (137 ppm = 8 ms per minute), so the echo drifts against the reference.
- **Coherence:** 0 to 1, how much of one signal is a linear copy of another; the ceiling on what a linear canceller can remove.
- **MCP server:** a tool provider speaking the Model Context Protocol; here `src/voice_agent/mcp_servers/` (home, speech, code, docs). **fastmcp:** the library used for the docs server.
- **Pipecat:** the real-time voice framework kept for the pipeline (`pipeline/`, a separate uv project because it pins older `onnxruntime`/`soundfile`).
- **Level-3 ticket / the fence:** a ticket that contains the full code for a cheaper model to copy exactly; the bubblewrap wall around agy's code-writing runs (section 8).
- **Cue-beep session:** a spoken test where a beep 2.5 s into each reply tells the owner when to talk over it, so every interruption has a known time (`scripts/cue_report.py`).
