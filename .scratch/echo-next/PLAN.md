# Echo next steps: plan for an assistant working without the owner

**Written:** 2026-10-24 (PHT) by the assistant (Opus) for a follow-up assistant (Sonnet) that continues while the owner is away. **Branch:** `agy/interrupts`. **Status table: section 6** (keep it current; it is the first thing the owner reads when back).

## 0. Read first, in this order

1. This file.
2. `.scratch/echo-verify/SUMMARY.md`: what was checked in the Gemini echo response (`docs/GEMINI-ARGON-ECHO-RESPONSE.md`) and against which sources. Do not re-research those claims.
3. `docs/ECHO-AGENT-BRIEF.md` section 1 (the problem, the measurements, the "done" criteria in its section 2) and `docs/LIVE-AGENT.md` (the A/B).
4. `docs/HANDOFF.md` section 0 and section 8 (working agreements), section 9 (commands).
5. Code: `src/doc_chat/aec.py`, `src/doc_chat/playback.py` (`RemotePlayer`), `src/doc_chat/bargein.py` (`EchoModel`, `BargeInGuard`, `PreRoll`, `looks_like_echo`), `src/doc_chat/__main__.py` (barge-in loop near line 730-830; stop and pivot rules), `scripts/pi_speaker.py`.

## 1. Owner's decisions for this period (2026-10-24)

- **Code:** you may change product code directly. Every behaviour change goes behind a flag whose default keeps today's behaviour, unless this plan says the default should change and the offline evidence supports it (then say so in the commit and section 6). Gate before each commit: `uv run --group laptop ruff check src tests scripts && uv run --group laptop ruff format --check src tests scripts && uv run --group laptop mypy && uv run --group laptop pytest -q` (1 known failure: `tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents`, needs a server on 8765). Format only the files you edit (never `ruff format scripts/`). Commit locally by topic, add files by name, **never push**. Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; use `git -c safe.directory=$PWD`.
- **Pi and anything loud: ask the owner first, every time.** That covers starting the Pi speaker, `scp` to the Pi, any playback at the Pi's volume, the probe. If the owner cannot be reached, skip it and leave it in section 5 (queued). Never change the laptop's mute, volume or firewall.
- **Nothing needing a human voice** (cue-beep interruption sessions) runs until the owner is back.
- Do not touch the owner's untracked files: `docs/VOICE-AGENT-EVALUATION-METRICS.md`, `kek.txt`, `resources/building-voice-agents/`, `docs/GEMINI-ARGON-ECHO-RESPONSE.md`, `docs/ECHO-AGENT-BRIEF.md` (the last two may be committed only if the owner asks). Uncommitted edits to `docs/HANDOFF.md` and `docs/LIVE-AGENT.md` are the owner's A/B count corrections: leave them as they are (they may be committed together with other doc changes only if the owner asks).
- Never read or print secrets (`~/.secrets/*`, `.env`, tokens). Never kill processes you did not start (the owner's speech server may be on 8765; use 8766-8767 for yours).
- Reporting style: bottom line first, then evidence, then caveats; always say how many clips/replies/sessions a number rests on, and when a setting was chosen after seeing the same data it is evaluated on.

## 2. The problem in five lines

Laptop mic + Pi loudspeaker in front of it at 80%. The assistant's echo (1) interrupts it early (7 of 20 replies with the canceller alone; the duck stopped this), (2) gets into the transcript through the 1.2 s pre-roll, putting echo words in front of "Stop" so the stop rules miss it and spurious `STOP` commands run, (3) with the canceller, the opening words of interruptions are sometimes garbled (2 of 6 per run). Best config so far: `--aec --duck-db -15`. One speaker, one room, one session per condition.

## 3. Step 1: offline experiments on existing recordings (no Pi, no sound)

Recordings (git-ignored, on this laptop): `out/remote_echo/fresh80` (12 clips at 80%: 6 echo-only, 6 with the owner saying "Wait, stop, I have a question." from a beep 2.5 s in; `mic.wav`, `result.json`, `trace.npz`), `out/sessions/agent-interrupt-ab-aec` (canceller only; audio is AFTER the canceller), `agent-interrupt-ab-duck` (duck only; raw mic), `agent-interrupt-aec-1` (canceller + duck), `agent-interrupt-2` (no canceller, speaker behind). Each session: `turns.jsonl`, `guard.jsonl` (one row per 100 ms block), `audio/turnNN.wav` (what the recogniser got, starting with the 1.2 s pre-roll for interruption turns), `bot/`.

### E1. The reference lead and AEC3's excess-render reset

- **Question:** does feeding the reference 50 ms ahead (`aec.py`, `lead_ms=50`) make AEC3 reset about a second into each reply (source: `render_delay_buffer.cc` `DetectExcessRenderBlocks`, `max_allowed_excess_render_blocks = 8` blocks = 32 ms, checked every 250 blocks = 1 s), and is a lead of 0-30 ms better?
- **Method:** `scripts/aec_lead_check.py` (new) runs the real `doc_chat.aec.EchoCanceller` the way it runs live: reference slices of 50 ms pushed at their send time (0.25 s ahead of the speaker, as `RemotePlayer` does), 100 ms mic blocks through `process`, clip by clip on `fresh80`, raw clip audio as the reference (no drift correction, like live). A simulated path delay (send-to-echo minus the 0.25 s cushion) of 100, 200 and 650 ms (650 = after idle). Leads 0, 30, 50, 100.
- **Measures:** echo-only clips where Silero (0.5, 7 of 10) still fires (of 6), and when; residual level by time since clip start (0-1 s, 1-2 s, 2 s to end) in dB above the room floor; talk clips where Silero fires on the owner after the beep (of 6).
- **Counts as support for changing the default lead to 0 (or 30):** at path 100-200 ms, no more echo-only fires than lead 50 and residual in the 1-2 s window not worse, talk-over detection unchanged. A bump in residual around 1 s at lead 50 but not at 0 would be the reset's signature (no direct way to see the reset through the LiveKit binding).
- **Also read off:** what happens at path 650 ms for every lead (AEC3's delay search covers roughly 0-512 ms by my reading of the source); this is the case for keeping the Pi awake.

### E2. A protected bar at the start of each reply (the guard's echo model learns from cancelled audio)

- **Question:** the guard learns its coupling from post-canceller mic levels, so its bar sits near 4x room noise (0.025-0.044 at the 7 early fires in `agent-interrupt-ab-aec`). Does a floor on the bar during the first T seconds of a reply remove the early fires without losing the cued interruptions?
- **Method:** `scripts/guard_trace_replay.py` (new) replays the level stage from `guard.jsonl` (level, played, noise per block) with the live settings (quantile 0.95, window 60, margin 1.2, prior 0.8, level 3 of 8), AND-ed with the recorded Silero stage (`model` field). First check the baseline replay reproduces the recorded fires; then variants: bar >= f x played for the first T s of each reply (f in 0.3, 0.4, 0.5; T in 1.0, 1.5 s), and a coupling floor for the whole reply.
- **Measures:** in `ab-aec`: early fires (before the cue at 2.5 s) of the 7; cued fires kept (fired within 1.5 s after the cue) and the added delay. Check the cued fires in the other three sessions too (duck sessions: only "still fires", the duck changed the audio).
- **Caveat to state:** after an early fire the live reply ended, so the replay cannot show what the rest of those replies would have done.

### E3. Echo-prefix stripping in text (no audio)

- **Question:** if leading words of the interruption transcript that match the bot's recent words are stripped before the stop/pivot rules, how many of the logged interruption transcripts become right?
- **Method:** `scripts/echo_prefix_eval.py` (new): for every interruption turn in the four sessions, take the heard text and the interrupted reply's `spoken` text from `turns.jsonl`, apply a candidate rule, compare with the scripted line (the 8 lines are in `docs/ECHO-AGENT-BRIEF.md` section 6, same order every session). Candidate rule: strip the longest leading run of heard words that appears in order in the last ~40 spoken words (allow one mismatched word, require at least 2 matched), then the existing leading-stop strip.
- **Measures:** per session, transcripts that end as the scripted command (or a bare stop for lines 1 and 5) before and after; any case where the rule removes a real user word. **The rule is designed on these same transcripts:** say so; it needs the next live session to be validated.

### E4. A shorter pre-roll (audio, needs a speech server)

- **Question:** with less pre-roll (0.6, 0.45, 0.3 s, and onset minus 0.2 s from the guard trace) instead of 1.2 s, does the recogniser hear fewer echo words while keeping the first word?
- **Method:** trim the first part of each interruption `audio/turnNN.wav` and re-transcribe. Start your own speech server on loopback (`uv run --group laptop python -W ignore -m lan_speech --stt-model parakeet --insecure-loopback --port 8767 --entities config/entities-docs.json`; check `--help` for the exact flags first; stop it afterwards). Raw sessions (`ab-duck`, `agent-interrupt-2`) answer "raw audio, trimmed"; `ab-aec` and `aec-1` answer "cancelled audio, trimmed". Re-transcribe the untrimmed files too as the baseline (the live transcripts were streamed; the offline result may differ).
- **Measures:** per session and pre-roll: transcripts with echo words in them, transcripts whose first scripted word survives, transcripts exactly right after E3's rule.

## 4. Step 2: code changes (behind flags; after step 1 says which are worth it)

Each change: its own flag, unit tests, the gate, one local commit, and a line in section 6.

1. **Reference lead** (`aec.py`): if E1 supports it, change the default `--aec-lead-ms` to the winner (the flag exists already). Keep `set_stream_delay_ms` (the value persists and seeds AEC3 after every reset).
2. **Pi keep-awake** (`scripts/pi_speaker.py`): a `--keep-awake` option that keeps the ALSA output open between replies and writes silence (or +/-1 LSB dither if the card mutes on zeros) so the sound card never idles. Needs care with abort (drop queued audio, keep writing silence) and with the 2 h self-stop. Unit-testable with a fake output. Deploying it is a Pi action: **ask first**.
3. **Protected bar** (`bargein.py` + flag in `__main__.py`): the E2 winner, e.g. `--barge-warmup-s T --barge-warmup-floor f`.
4. **Echo-prefix stripping** (`bargein.py` helper + use in the stop/pivot path in `__main__.py`): the E3 rule behind `--strip-echo-prefix`. Unit tests from the logged transcripts (put them in `tests/fixtures/`), including cases where it must not strip.
5. **Pre-roll length** (`PreRoll(seconds=...)` in `__main__.py`): `--preroll-s` (default 1.2), and optionally onset-anchored trimming if E4 shows it matters.
6. **Split tap** (cancelled audio to the guard, raw audio to the pre-roll and recogniser): only if E4 shows raw trimmed audio keeps first words better than cancelled audio. `MicHub` currently delivers only the cancelled block; this is a larger change: plan it before writing it.
7. **Four-track recording** for the next live session: with `--record` and `--aec`, also write the raw mic, the reference as fed to the canceller (same 10 ms ticks) and the cancelled output, so the live render-to-echo delay and any reference gaps can be measured afterwards.

Not to do (checked and dropped, see the summary): neural cancellers, speaker-embedding rejection of the bot's voice, the H3 duck-mismatch fix, buying hardware (every hardware canceller needs the mic and the speaker on one device, which means a different topology: owner's decision).

## 5. Step 3: queued live tests (owner present, or explicit permission each time)

- Echo-only, nobody speaking (ask first): `echo_path_probe.py` and `remote_echo_check.py` with and without `--keep-awake`, measuring the first-reply delay after 10-30 s idle and the delay spread across replies.
- With the owner speaking: one cue-beep session with the best config plus the new flags and the four-track recording; score with `scripts/cue_report.py`, count against the brief's criteria (0 self-interruptions in 30 quiet replies, 90% first words, stop within ~0.5 s, no echo reaching the LLM, no audible dips).

## 6. Status (update as you go)

| Item | State | Result / where |
|---|---|---|
| E1 lead check | **done** (Opus, 2026-10-24) | `scripts/aec_lead_check.py`, 12 clips of `fresh80` (6 echo-only), one recording. **The canceller stops working when path + lead exceeds about 500 ms** (lead 0: works at path 500, fails at 550; lead 50: fails at path 500): Silero then fires on 6/6 echo-only clips and the residual stays about +6 dB over the room for the whole clip. This matches AEC3's ~512 ms delay search read from the source. So the ~650 ms after the Pi idles is out of reach at any lead: **keeping the Pi awake (step 2.2) is required, not optional**, or the reference must be delayed to match. At path 100-200 ms, lead 0 left the least residual 1-2 s after the echo starts (-4.9 / -3.7 dB against +1.9 / -3.0 dB at lead 50), consistent with the excess-render reset at lead 50; Silero fires on echo-only clips did not separate the leads (2-4 of 6 everywhere: noise at n=6). Talk-over detection 6/6 everywhere. **Recommendation: lead 0** (the default change is still to be made; it frees 50 ms of the ~500 ms budget). |
| E2 protected bar replay | **done** (Opus) | `scripts/guard_trace_replay.py`. The replay reproduces the live decisions (1481/1482, 1184/1184, 821/821, 969/973 blocks). In `ab-aec` (canceller only) a bar of at least f x played for the first 1.0-1.5 s of each reply removes the early fires: f=0.5 leaves 2 of 7, **f >= 0.6 leaves 0 of 7**; every cued interruption that fired live still fires, in all four sessions. A warm-up of 2.0 s or more starts losing cued hits; a whole-reply coupling floor (0.35) also leaves 2 and loses hits. The two hardest fires had mic level 0.52-0.64 x played, so 0.6 is a thin margin: **recommend "for the first 1.0 s of each reply use the prior coupling (0.8), i.e. bar >= 1.2 x 0.8 x played"** (f = 0.96, tested: 0 of 7, hits kept). Rests on 7 early fires in one session, chosen on that same session; interruptions in a reply's first second (none in these sessions) will need a louder voice during the warm-up. The duck already prevented these fires live; this is the canceller-only fix, and it costs no volume dip. |
| E3 echo-prefix text eval | **done** (Opus) | `scripts/echo_prefix_eval.py --fix-pause`. Over the 4 A/B-era sessions: fixes every echo-prefix failure in the logs ("First book in the Border Trilogy. Stop! What year..." -> "What year was it published?"; "2005 border. Stop. Set a timer..." -> "Set a timer for ten minutes."; "Joseph McCartney Chief Stop, when did he die?" -> "When did he die?"; "Album published in twenty five Stop, Play Some Music." -> "Play Some Music."; the echoed "Set a timer for a minute." that set a second timer -> STOP; "Novel published in 2007." answered as a question -> STOP). Over all 18 sessions (123 interruption transcripts): 46 change: 25 silenced before and after, 12 echo prefix removed, 9 sent to the LLM before and treated as a stop after (all 9 read as the bot's own words); none read as a user request lost. **Thresholds chosen on these same transcripts.** **Bug found in today's code:** `_strip_stop_prefix` treats "pause" as a stop word, so "Stop. Pause the music." becomes "The music." (and "Wait, pause the video please" -> "The video please"); `--fix-pause` in the script shows the fix (drop "pause" from `_STOP_PREFIX`). |
| E4 pre-roll re-transcription | **script written, not run** | `scripts/preroll_eval.py` (trims 1.2 s pre-roll to 0.8/0.6/0.45/0.3 s on each saved interruption turn and re-transcribes; marks transcripts with the reply's words in front). The run was stopped by the owner before it produced output; nothing is known yet. Needs a speech server (section 7). Not done: the onset-anchored variant (trim to the guard's first speech window minus 0.2 s), which needs the turn-to-guard-trace mapping. |
| Step 2 (a)-(d) | **done, integrated** (2026-10-24) | Written by agy from the level-3 tickets in two launches (the first dropped its network connection after ticket 03's files; the reviewer ran 03's Proof and logged it). Every file byte-identical to the reference, no deviations. In the repository: the "pause" fix and lead 0 are on by default; `--strip-echo-prefix` and `--barge-warmup-s` are flags, off by default (use `--strip-echo-prefix --barge-warmup-s 1.0` with `--aec` in the next live session). Gate: 616 passed, only the known `test_flow` failure. Next: E4, then (e) Pi keep-awake (ask before touching the Pi), then the live session in section 5. |
| agy config | 33 `read_url` domain rules added to `~/.gemini/antigravity-cli/settings.json` for the fact-check; backup `settings.json.bak-20261024`; restore only if the owner asks | |

## 7. Continuing on another machine (e.g. the owner's HPC cluster)

Step 1 needs no microphone, speaker or Pi; it runs anywhere with the repository, `uv`, and the recordings.

- **Code:** the four scripts (`scripts/aec_lead_check.py`, `echo_prefix_eval.py`, `guard_trace_replay.py`, `preroll_eval.py`) and this folder are uncommitted on the laptop as of this writing; they reach the cluster only once committed and pushed (owner's call) or copied.
- **Recordings (git-ignored, about 31 MB, laptop only):** copy these folders, keeping their paths under the repository root:
  `out/sessions/agent-interrupt-2`, `out/sessions/agent-interrupt-ab-duck`, `out/sessions/agent-interrupt-ab-aec`, `out/sessions/agent-interrupt-aec-1`, `out/remote_echo/fresh80`. E3 over all sessions also used the rest of `out/sessions/` (optional).
- **Environment:** `uv sync --group laptop`; E1 adds `--with livekit` (`uv run --group laptop --with livekit python scripts/aec_lead_check.py out/remote_echo/fresh80`). `assets/silero_vad.onnx` and `tests/fixtures/pi_echo/` are in git.
- **E4's speech server:** `uv run python scripts/fetch_laptop_models.py` (Parakeet v2 int8 and Supertonic into `models/`), then on the compute node `uv run --group laptop python -W ignore -m lan_speech --stt-model parakeet --insecure-loopback --port 8767 --entities config/entities-mccarthy.json` and, in another shell on the same node, `env -u LAN_SPEECH_TOKEN uv run --group laptop python scripts/preroll_eval.py out/sessions/agent-interrupt-2 out/sessions/agent-interrupt-ab-duck out/sessions/agent-interrupt-ab-aec out/sessions/agent-interrupt-aec-1 --server ws://127.0.0.1:8767`. CPU is enough (the server runs Parakeet through sherpa-onnx); stop the server afterwards.
- **Re-running E1-E3** should reproduce the numbers in section 6 exactly (deterministic; E1 uses the real WebRTC canceller from the `livekit` wheel, so a different `livekit` version may shift E1 slightly: the laptop had livekit-rtc 1.1.21).
- **Step 2 code** can be written and unit-tested on the cluster; anything live (section 5) waits for the laptop, the Pi and the owner.

## 8. After agy's wave: review and integrate (docs/AGY-CODING.md steps 7-8)

1. `agy_work/wave1.done` exists; read `agy_work/wave1.md` (its reply) and `wave1.err`.
2. `diff -rq --exclude .venv --exclude __pycache__ --exclude .mypy_cache --exclude .ruff_cache --exclude .pytest_cache agy_work/orig agy_work/work`: only the files in the four tickets' tables (and the tickets themselves) may differ.
3. Compare every changed file with the reference: `bash .scratch/echo-next/build_overlay.sh <scratch dir> --no-gate`, then `cmp` each of `src/doc_chat/{__main__,bargein,aec}.py` and the four new test files against `agy_work/work/`. Identical = the inventory and caps hold by construction.
4. Re-run the Proof commands yourself in `agy_work/work`; append a review entry to each ticket's Execution Log.
5. Integrate: back up the three source files, copy the accepted files into the repo (a bash array, not a zsh string), `cmp`, run the full gate in the repo, commit by file name (one commit per ticket or one for the wave), no push.
