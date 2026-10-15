# Project 2: design review and what we can reuse from ME2

Review date: 2026-10-15. Inputs:
- the Gemini design chat (<https://share.gemini.google/J15syUXXOJKr>);
- a follow-up proposal answering this review's open decisions;
- the ME2 docs and code (<https://github.com/Martinnavs/AI-222-Machine-Exercises>, folder `ME2/`).

## Background

**Project 2** is a full-duplex, interruptible voice agent on a Raspberry Pi (4 or 5). The brief asks for:
- an agent harness with MCP support on the Pi;
- MCP servers for state-of-the-art ASR and TTS;
- local VAD;
- an agent that does real-world tasks;
- a standardized latency benchmark.

Commercial model APIs are allowed, but the MCP servers must run on the Pi. Our target is about 500 ms turn-taking, at low token cost.

**The proposed design** (from the Gemini chat):
- A fast orchestrator classifies each request and routes it to one of two subagents: low-intelligence (email, IoT) or medium-intelligence (research, writing code).
- Each subagent uses a light harness connected to the MCP servers and is sandboxed to one folder on the Pi.
- Local Silero VAD, cloud or tiny-local ASR, and streaming TTS.
- The harness is Pipecat, pruned to its core: pipeline, VAD, interruption handling and audio transport, with our own processors for the LLM call and MCP tools.

## ME2 in brief

ME2 is our previous machine exercise: an offline voice assistant for smart-home commands on a Raspberry Pi 4. It's in the `ME2/` folder of <https://github.com/Martinnavs/AI-222-Machine-Exercises>, and every `ME2/...` path below is in that repo.

**What it does.**
- The user says a wake word ("sesame"), then one of 19 fixed commands such as "turn off the lights" or "set a timer for 10 seconds".
- A small on-device speech model (14.3 MB, one CPU core) recognizes the command.
- A web dashboard (lights, music, thermostat, timer, alarm, reminders, phone) carries it out.
- No cloud, no LLM, no general speech-to-text.

**Its models are not reused in Project 2.** They only recognize ME2's fixed command set, and Project 2 is open conversation with cloud ASR and LLMs. What carries over is the engineering around them:
- running speech pipelines on a Pi;
- measuring latency honestly;
- benchmark tooling;
- deployment;
- a few interfaces.

Section 4 lists what can be salvaged.

**Numbers worth knowing.**

| Measure | Result |
|---|---|
| Latency on the Pi 4, end of speech to decision, median / p95 | 0.48 / 0.92 s |
| Pi 4 load | about one full CPU core, 600 MB of memory |
| Accuracy through a real speaker and USB mic, against clean audio replay | about 75% vs 89.6% |

**What it taught us about the Pi 4.**
- **Speed:** heavy per-frame work in Python was too slow until it was compiled (numba). Even then, the slowest 5% of steps nearly used up the real-time budget. Assume the Pi 4 has little spare CPU and measure early.
- **Running it:**
  - Use one thread per model; more threads made a small model slower.
  - Keep the Pi cooled; heat and a weak power supply throttle it (`vcgencmd get_throttled` should read `0x0`).
  - Run one benchmark at a time.
- **Audio:** sound played through a speaker and picked up by a cheap mic loses quality, mostly because of the channel's frequency response, not volume. Test with the real mic early.
- **Measuring:** latency replayed from a file is not live latency. ME2 reported replay timing plus measured compute and never measured true live latency through the audio hardware. Project 2 must not repeat that.

## Summary

The overall direction holds up: cloud models, local VAD, pruned Pipecat, MCP tools on the Pi, and fast/slow agents. Four things needed fixing:
1. **The ME2 baseline was misquoted.** ME2 does not answer in 300 ms on a Pi.
2. **There was no echo cancellation**, so the agent would interrupt itself.
3. **The proposed benchmark started the clock too late** and hid the largest delay.
4. **A cloud turn under 500 ms median needs tricks.** Plain streaming won't get there.

The follow-up proposal settles most open decisions (section 6), with corrections noted where it overstates or will break on our Pi.

## 1. The ME2 latency baseline

ME2's latency runs from the end of speech to the **decision**, not to sound coming out of the speaker.

| Where measured | Median / p95 | Source |
|---|---|---|
| Pi 4, estimated live (replay timing + compute) | **0.48 / 0.92 s** | `ME2/docs/BENCHMARKS.md` |
| Server replay, compute excluded | 0.38 / 0.65 s | `ME2/docs/SOAK-TEST.md` |
| Server replay, smaller stride | 0.26–0.30 s | `ME2/docs/SOAK-TEST.md` (a Pi can't keep up at that stride) |

The "300 ms" in the Gemini chat comes from the last row. Treat ME2 as a reference point, not as a component we already have: Project 2's latency is a different pipeline (cloud STT, LLM and TTS) measured to a different end point (first audio out).

## 2. Problems in the Gemini design, most important first

1. **T0 is set at VAD `speech_end`.**
   - That event fires only after the silence wait (300–500 ms in Gemini's own advice), so the benchmark would hide the largest delay.
   - T0 must be when the user actually stopped speaking, taken from a truth file. ME2's soak test already does this, with times from forced alignment.
2. **No acoustic echo cancellation (AEC).**
   - With a speaker and mic on the same Pi, the mic hears the bot's own voice, VAD fires, and the bot interrupts itself.
   - Fix: see decision 2 in section 6.
   - Also require a minimum amount of speech before accepting a barge-in, so coughs and clicks don't stop the bot.
3. **WebRTC is in the wrong place.**
   - The mic is local to the harness, so Pipecat's local audio transport is enough.
   - The cloud hops are just streaming connections to the STT, LLM and TTS APIs.
   - The advice to use aiortc or LiveKit, and the "transport 20–60 ms" line in the budget, don't apply.
4. **The endpointing advice contradicts itself.**
   - One turn says a 200 ms silence wait is too aggressive; the next says 300 ms is risky.
   - Better: a short silence wait (~200 ms) plus a turn-completion check. Pipecat's open smart-turn model is one candidate (it runs on CPU, but we haven't seen a Pi 4 timing).
5. **The RAM-disk "pointer" trick for an ASR MCP server undoes streaming ASR.**
   - It forces a batch transcription after the user stops talking.
   - The claim that JSON over MCP would "cripple" latency is overstated: 16 kHz audio is about 43 KB/s in base64, which local stdio handles easily.
   - The real mismatch is that MCP is built for request/response tools, not two-way audio streams. See decision 4 in section 6.
6. **A router-only orchestrator adds a full LLM round trip before the first audio.**
   - Instead, let the user-facing model start speaking right away and hand slow work to a subagent as an async tool call (`delegate(task)`).
   - Mercury's advantage is tokens per second. A voice agent doesn't need that, because TTS only reads at speaking speed; what matters is time to first token (network plus prompt processing).
   - We couldn't tell what "Jev" refers to, so check it before relying on it.
7. **A "native folder" with a subprocess is not a sandbox.** See section 7.
8. **Smaller errors.**
   - Gemini 1.5 Flash is outdated; check the current model lineup.
   - "Kill the TTS stream with a POSIX signal" means nothing for a cloud TTS connection; you cancel it and flush the buffer.
   - Our Pi runs Debian 13, not Arch.

## 3. Latency budget for a cloud turn

These are estimates, not measurements. Measure each stage on the Pi in week 1.

| Stage | Estimate |
|---|---|
| End-of-turn decision (silence wait + turn check) | 200–300 ms |
| Streaming STT final result after that | 50–150 ms |
| LLM time to first token (network + prompt) | 200–400 ms |
| TTS first audio | 100–250 ms |
| ALSA output buffer | 20–50 ms |
| **Total, end of speech to first audio** | **about 0.6–1.1 s** |

Ways to cut it:
- **Start the LLM early.**
  - Send the interim transcript once it looks like a complete clause and there has been about 150–200 ms of silence; cancel if the user keeps talking.
  - *Cost:* a cancelled request is still billed for its input tokens. Log the cancellation rate and tune the trigger against it.
- **Open connections at startup.** Keep STT and TTS WebSockets and the LLM's HTTP connection open, so no TLS handshake is paid per turn.
- **Network.** Use Ethernet rather than Wi-Fi, and the nearest API region.
- **Short prompts.** Keep the system prompt small and only the last few turns of history.
- **Pre-recorded acknowledgements**, with care:
  - A filler such as "checking now" lasts about 0.5–1 s and can't be cut off mid-word gracefully, so the real answer starts *after* it. That lowers how long the user waits for any sound, but it can delay the real content.
  - Use fillers only for turns handed to the slow subagent, not every turn.
  - In the benchmark, report both "first audio" and "first answer audio"; otherwise the filler games the metric.

The follow-up proposal's timeline (LLM first tokens at 350 ms, real answer at 450–500 ms) assumes about 200 ms for time to first token including the network, and that the answer "seamlessly replaces" the filler. Both are optimistic; treat 500 ms as the target for *first audio*, and measure first *answer* audio separately.

## 4. What can be salvaged from ME2

| ME2 piece | Use in Project 2 | Caveat |
|---|---|---|
| Soak-test method: `ME2/scripts/soak_run.py`, `ME2/docs/SOAK-TEST.md` (one long recording + a truth file of force-aligned end-of-speech times, scored by time range, p50/p95) | The input side of our latency benchmark: user turns with a known true end of speech (T0). The forced-alignment and scoring-by-time-range ideas carry over directly. | ME2's own recording is short smart-home commands; record or synthesize conversational turns for Project 2. ME2 measured replay timing only; ours must be live (section 8). |
| Pi health monitoring from the class benchmark run, `ME2/docs/VCM-BENCHMARK-RESULTS.md` (temperature, clock, throttling, CPU, RSS sampled every second) | Record Pi health alongside every benchmark run. | — |
| Pi setup notes, `ME2/docs/RASPBERRY-PI.md` | Lean venv instead of `uv sync` (the x86 CUDA wheels fail on ARM), 64-bit OS, `arecord` at 16 kHz S16_LE mono, stable mic names (`plughw:CARD=<name>,DEV=0`), one thread per model, cooling, one run at a time, the `pkill -f` pitfall. | ME2's CPU load came from its own decoder, which we don't reuse, so it doesn't predict Project 2's load. |
| `AudioSource` contract, `ME2/docs/STREAMING-CONTRACT.md` §1 | Back-pressure rules for any audio we handle outside Pipecat (benchmark replay, MCP file tools): a live mic skips and counts dropped audio; file replay runs in lockstep for reproducible tests. | Pipecat has its own transport inside the live loop. |
| JSONL event log on stdout (`ME2/docs/STREAMING-CONTRACT.md` §5) and the forwarder `ME2/src/app/forward.py` | The pattern for the harness's timestamped stage log (`t_endpoint`, `t_stt_final`, ...): one JSON object per line, consumed by separate scripts. | — |
| TTS backend interface, `ME2/docs/adding-a-tts-backend.md` (a `Synthesizer` ABC returning float32 numpy, no torch required, heavy imports inside the class) | The shape for the shared ASR/TTS engine behind both the MCP server and the Pipecat processors (decision 4). | It is batch only (text in, whole clip out); add a streaming method for the live loop. The only backend, CosyVoice2, is too heavy for a Pi. |
| Smart-home dashboard, `ME2/src/app/` (FastAPI, per-device endpoints: `/api/light/set_color`, `/api/thermostat`, `/api/timer/start`, `/api/state`, ...) | Optional: a ready-made, visible "IoT" target that an MCP server can drive through its per-device endpoints, with no ME2 model involved. | It has no authentication; keep it on a trusted network. |
| Headless deploy, `ME2/deploy/` (systemd user services with linger, phone-hotspot demo in `ME2/docs/UI-DEMO.md`) | Run the agent headless on the Pi for demos. | A cold boot through the services was never verified. |

**Not reused:** the wake-word model, the command decoder, the 19-command grammar, and the training data.

## 5. Open questions

1. **What counts as "MCP servers for ASR and TTS".** Confirm with the instructor before building decision 4.
2. **Which AEC device.** Buy or borrow one and test it in week 1 (decision 2).

## 6. Team decisions (from the follow-up proposal, with corrections)

**Decision 1: local fast path. Dropped.** Project 2 doesn't use ME2's command model. Note also that the follow-up's "sub-300 ms local turns" claim was wrong: ME2 measured 0.48 s median on a Pi 4.

**Decision 2: echo cancellation. Agree: hardware AEC.** Use a USB speakerphone or a mic array with an onboard DSP (for example the ReSpeaker USB Mic Array v2.0, or a conference speakerphone). Corrections:
- **Hardware AEC only works if the bot's audio plays through the same device**, because the DSP needs the playback signal as its reference. A speakerphone does this naturally. With the ReSpeaker, play through its own audio output, not the Pi's jack or HDMI.
- **It reduces echo; it doesn't "completely eliminate" it.** Keep the minimum-speech rule for barge-in and measure the false-interruption rate.
- **The CPU and delay figures quoted for software AEC (15–25% of a core, 20–40 ms) are unsourced.** Measure them if software AEC becomes the fallback.

**Decision 3: Pi 4 or Pi 5. Lean Pi 5 if we can get one, but decide by measurement.**
- The follow-up argues from ME2's Pi 4 load (real-time factor 0.96). That load came from ME2's own decoder, which Project 2 doesn't run, so it doesn't apply.
- Project 2's Pi load is audio I/O, Silero VAD, a turn detector, the Pipecat event loop, network streams, MCP servers and sandboxed scripts. It may well fit a Pi 4.
- Profile it on a Pi 4 in week 1. A Pi 5 (Raspberry Pi quotes 2–3x the CPU speed) is the safe choice if we add local TTS or let the coding agent run heavy scripts.

**Decision 4: MCP for ASR and TTS. Agree: one engine, two interfaces.**
- **Engine:** one module (for example `audio_engine/`) holds the STT and TTS drivers.
- **Live loop:** Pipecat imports it directly and streams PCM to and from the cloud services over WebSockets.
- **MCP:** a stdio server on the Pi imports the same module and exposes `transcribe_audio_file(path)`, `synthesize_speech_to_file(text, path)` and `get_tts_voices()`, so subagents can use speech as tools.
- This is the design in section 2, item 5. It still depends on the instructor accepting it (section 5).

## 7. Sandboxing the coding subagent

The follow-up's approach is right: `systemd-run` for resource limits plus `bwrap` for filesystem isolation. Its command will likely fail on our Pi, though.
- **The binds:** Debian 13 on arm64 has a merged `/usr` (so `/bin` and `/lib` are symlinks) and usually no `/lib64`. Binding a missing `/lib64` makes `bwrap` exit with an error.
- **The limits:** `MemoryMax` needs the memory cgroup controller. Raspberry Pi kernels have shipped with it disabled; check that `cat /sys/fs/cgroup/user.slice/cgroup.controllers` lists `memory`. If not, add `cgroup_enable=memory` to `/boot/firmware/cmdline.txt` and reboot.

A starting point, **untested on the Pi**:

```bash
systemd-run --user --scope -p MemoryMax=512M -p CPUQuota=100% \
  timeout 60 \
  bwrap \
    --ro-bind /usr /usr \
    --symlink usr/bin /bin --symlink usr/lib /lib \
    --ro-bind /etc/alternatives /etc/alternatives \
    --proc /proc --dev /dev --tmpfs /tmp \
    --bind "$HOME/agent_sandbox" /sandbox --chdir /sandbox \
    --unshare-all --die-with-parent --new-session \
    python3 script.py
```

- `CPUQuota=100%` caps the script at one core, leaving three for audio and the harness. `MemoryMax=512M` kills it before the system-wide OOM killer can hit the harness. `timeout 60` stops infinite loops.
- `--unshare-all` also removes network access. Add `--share-net` for a research subagent that needs the internet, but keep network off for code execution.
- Python packages the scripts need must be installed under `/usr`, or bind a venv read-only into the sandbox.

## 8. The latency benchmark

Gemini's T0–T4 stages are a fine skeleton. The follow-up's rig is the right shape; the main fix is the clock.

**Rig.** A benchmark host with a multi-channel audio interface:
- **Out 1:** plays the user turns through a speaker into the Pi's mic, through the air, so AEC and the real room are part of the test.
- **In 1:** records the Pi's speaker output (a mic next to it, or the line out).
- **In 2 (loopback):** records Out 1 as it is actually played. **Without this, T0 is off by the host's unknown playback delay.** With it, T0 and T4 sit on one timeline in one recording.

**Measurements.**
- **T0** = the true end of user speech: the truth file's offset, located on the loopback channel.
- **T4** = bot audio onset on In 1: the first point where the energy envelope rises a fixed margin (for example 12 dB) above the noise floor. Calibrate the margin once, and check that echo of the user's own voice can't trigger it.
- **Report p50 and p95** over many trials at different times of day, because cloud latency varies.
- **Report both "first audio" and "first answer audio"** if fillers are used.
- **Barge-in:**
  - time from the user starting to speak during a bot turn until the bot's audio stops (on In 1);
  - the false-interruption rate on turns with coughs and noise but no speech.
- **Stage log:** the harness writes JSONL with `t_endpoint`, `t_stt_final`, `t_llm_first_token`, `t_tts_first_chunk` and `t_barge_in_mute`, so we know which stage to fix.
- **Pi health** during every run: temperature, clock, throttling, CPU, memory.
