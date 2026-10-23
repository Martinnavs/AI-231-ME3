# Documentation index

Start with **`HANDOFF.md`**. Everything else is linked from there; this is the full list.

## The handoff set (read first)

| File | What it is |
|---|---|
| `HANDOFF.md` | the map: state, findings, decisions, open questions, next actions, working agreements |
| `PI-TEST-PLAN.md` | runbook for the Raspberry Pi echo test (what to run, how to read it, what each outcome means) |
| `PI-RESULTS.md` | what the Pi echo test showed (split mic/speaker, with and without software AEC), and what it did not |
| `PI-BRIDGE.md` | the Pi microphone and speaker with the laptop's STT and TTS over a cable: how to run it, the traps hit, and the measured latency (2.0 s to sound) |
| `PI-REMOTE-SPEAKER.md` | the laptop microphone with the Pi as loudspeaker: how the Pi speaker echo defeats Silero, the echo guard that fixes it, and how well it worked (6 of 6 interruptions, 0 of 6 false) |
| `CORPUS-MCCARTHY.md` | the 33-file Cormac McCarthy corpus for the larger doc-chat test: how agy built it, the errors found and fixed, what was not checked |
| `RESEARCH-HANDOFF-PI.md` | **for the Pi agent**: research status after the Pi test, closed leads, verified sources, next experiments in order |
| `SILERO-SPEECH-COMMANDS.md` | laptop test: Silero does hear a clean one-word "stop" (so the Pi's low confidence is not the model); the 250 ms hold costs short words |
| `BRANCHES.md` | the three branches, how they relate, how to land them, how to resolve conflicts |
| `ECHO-RESEARCH.md` | how assistants and frameworks handle echo and barge-in; how far to trust the report |
| `SMART-HOME-TOOL.md` | the one `smart_home` tool for the 19 ME2 commands, the separate Pipecat project `pipeline/`, and the LLM choice: native Gemini 3.1 flash-lite + router prompt 50/50, first reply 0.52 s (plain) / 1.31 s (tool) |

## Speech and the test client

| File | What it is |
|---|---|
| `LAPTOP-SPEECH.md` | the laptop speech server: benchmark results (Kokoro, Supertonic, Kroko), the WebSocket protocol |
| `STT-HOSTING.md` | STT accuracy and hosting findings: Parakeet, hotwords, name repair, padding, live sessions, latency, early decode |
| `STT-COMPARE.md` | comparing STT models on your own voice (the Kroko decision) |
| `DOC-CHAT.md` | the voice test client: every option, the improved loop, barge-in, Gemini, ask-to-add |
| `IRL-RECIPE.md` | a real-voice test with recording and a report |

## Turn end and interruptions

| File | What it is |
|---|---|
| `SMART-TURN.md` | experiments: Smart Turn v3 (audio), Turnsense (text), the confirm-and-stitch simulation; why none is adopted |
| `BARGE-IN.md` | the original barge-in design and the measurements on the laptop (echo with and without cancellation) |
| `INTERRUPTS.md` | the model-free interruption bench, the detector ablation, what it shows |

## Project 2 design (from `main`)

| File | What it is |
|---|---|
| `../.scratch/project2-voice-agent/DESIGN.md` | the Pi voice agent design, decisions, the professor's requirements |
| `../.scratch/project2-voice-agent/tickets/` | tickets 01-28 (12 = barge-in gate; 21-24 = laptop speech track; 25-28 added 2026-10-23; order after the reassessment at the end of `DESIGN.md`) |
| `../.scratch/smart-home-tool/` | plan, design, level-3 tickets, agy fence and results for the `smart_home` tool |
| `../PROJECT2-DESIGN-REVIEW.md`, `../FULL-DUPLEX-RESEARCH.md` | the reviews behind the design |
| `AUDIO-ENGINE.md`, `EVENT-LOG.md`, `MCP-SERVERS.md`, `BENCHMARK.md` | contracts of the main project |

## Research notes from the agy assistant (`.scratch/speech-hosting/`)

`AGY-RESEARCH.md` (hosting), `AGY-STT-IMPROVE.md`, `AGY-PARAKEET-IMPROVE.md`, `AGY-PARETO-REPAIR.md` (STT), `AGY-NET-*.md` (poor-network practice), `AGY-SPEC-DECODE.md` (early-decode accuracy), `AGY-ECHO.md` (echo), `AGY-GEMINI-FAST.md` (Gemini models for a fast agent), `AGY-CHEAP-LLM.md` (cheap hosted LLMs from the Philippines; relies on 2024-era data), `AGY-PI-NEXT-v1.md` / `AGY-PI-NEXT.md` (one-word interrupt and AEC, **unreliable**: see `RESEARCH-HANDOFF-PI.md`), `PROMPT-PI-NEXT.md` (the brief), `VERIFICATION.md`. They are web-search summaries; claims are tagged by the assistant and partly unreliable (see `ECHO-RESEARCH.md` "How far to trust it").

## Results coming back from the Pi

`docs/pi-results/` (created by `scripts/pi_pack_results.sh`): one folder per run with `run.md` and `run.json`, plus your `NOTES.md`.

## Not in the repository (the owner's, local)

`docs/VOICE-AGENT-EVALUATION-METRICS.md`, `kek.txt`, `resources/building-voice-agents/` are the owner's untracked files; leave them alone.
