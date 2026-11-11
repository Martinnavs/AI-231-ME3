# Documentation index

> **This is the production tree.** Research notes, history, experiments (scripts, tests, recorded results) live on the development branch `agy/interrupts`; a file named in a doc below that is not here is there, or is a local file (`config/pi.env`, `config/profile.md`) or runtime output (`out/`).

Start with **`HANDOFF.md`**. Everything else is linked from there; this is the full list.

## The handoff set (read first)

| File | What it is |
|---|---|
| `HANDOFF.md` | the map: state, findings, decisions, open questions, next actions, working agreements |
| `ARCHITECTURE.md` | the voice agent as deployed on 2026-11-05: topology and ports, lifecycle (`make pi-detect`, `up`, `talk`, `down`), one turn end to end, every component with its settings, state on disk, measured numbers against the industry, security, config table, differences from older docs |
| `PI-TEST-PLAN.md` | runbook for the Raspberry Pi echo test (what to run, how to read it, what each outcome means) |
| `PI-REMOTE-SPEAKER.md` | the laptop microphone with the Pi as loudspeaker: how the Pi speaker echo defeats Silero, the echo guard that fixes it, and how well it worked (6 of 6 interruptions, 0 of 6 false) |
| `CORPUS-BABY-ONIT.md` | the document search corpus (baby-onit, Explained): what is in it, how it is chunked and searched, where it is wired, what was checked |
| `LIVE-AGENT.md` | the agent (smart_home + docs MCP server) in the live voice loop: what was built, the stress dry run, the two spoken sessions (latency, tool routing, interruptions) and what broke (echo in the transcripts) |
| `AEC-REMOTE-SPEAKER.md` | echo cancellation across two devices (laptop mic, Pi speaker): the 137 ppm clock drift, offline results on real recordings, what is not shown |
| `SMART-HOME-TOOL.md` | the one `smart_home` tool for the 19 ME2 commands, the separate Pipecat project `pipeline/`, and the LLM choice: native Gemini 3.1 flash-lite + router prompt 50/50, first reply 0.52 s (plain) / 1.31 s (tool) |
| `MCP-SERVERS.md` | the MCP servers; the last section is the live agent's real tools (time, weather + Wikipedia, the Pi's device server), their security and how to start them |
| `PUBLISH-PAGES.md` | publishing a finished app to GitHub Pages after a spoken yes: owner setup, what is checked, what is not covered |
| `PROFILE.md` | the owner profile (`config/profile.md`): what it is, setup, how it is installed, the optional doctor check, the Pi, the live check |
| `LATENCY.md` | where an answer's time goes, the LLM spike probe, the research on fillers, and the safeguards (fast path, the tools' `say` sentence, doc_chat fillers with a give-up at 8 s): landed 2026-10-24/25 (the spoken sessions, R4, R5, R6 and what each changed are in its later sections) |
| `ONIT-PARITY.md` | the professor's added criterion (the voice agent must do all that OnIt can do): OnIt and baby-onit inventoried, the minimum MCP servers and tools to replicate the fundamentals, voice-specific issues, open questions (analysis only, 2026-10-27) |
| `ROUTER-WEB-AND-DOCS-RACE.md` | the router's web lookups (cluster tickets 01/02, kd-web2 candidate), the documents-vs-web race in the harness, measurements and a latency estimate (2026-11-03) |
| `PROGRESS-2026-11-11.md` | **latest:** the harness review and fixes, owner profile file, Cebu venue and barangay, nearby/nearest places (Photon, Google), endpoint silence 0.7, GitHub Pages publish (ticket 04); evidence, how to run, open items in order |
| `../finetune/README.md` | start here on the cluster: the router fine-tuning project (contract, evaluation sets, baselines, tasks, rules); code in `finetune/` |

## Speech and the test client

| File | What it is |
|---|---|
| `LAPTOP-SPEECH.md` | the laptop speech server: benchmark results (Kokoro, Supertonic, Kroko), the WebSocket protocol |
| `STT-HOSTING.md` | STT accuracy and hosting findings: Parakeet, hotwords, name repair, padding, live sessions, latency, early decode |
| `DOC-CHAT.md` | the voice test client: every option, the improved loop, barge-in, Gemini, ask-to-add |
| `IRL-RECIPE.md` | a real-voice test with recording and a report |

## Turn end and interruptions

| File | What it is |
|---|---|
| `BARGE-IN.md` | the original barge-in design and the measurements on the laptop (echo with and without cancellation) |
| `INTERRUPTS.md` | the model-free interruption bench, the detector ablation, what it shows |

## Project 2 design (from `main`)

| File | What it is |
|---|---|
| `../.scratch/project2-voice-agent/DESIGN.md` | the Pi voice agent design, decisions, the professor's requirements |
| `../.scratch/project2-voice-agent/tickets/` | tickets 01-28 (12 = barge-in gate; 21-24 = laptop speech track; 25-28 added 2026-10-23; order after the reassessment at the end of `DESIGN.md`) |
| `../.scratch/live-agent/`, `../.scratch/echo-fix/` | plans, level-3 tickets, the agy briefs and replies, the reviews, edit lists and ticket generators for the agent and for the echo canceller and duck |
| `../.scratch/smart-home-tool/` | plan, design, level-3 tickets, agy fence and results for the `smart_home` tool |
| `../PROJECT2-DESIGN-REVIEW.md`, `../FULL-DUPLEX-RESEARCH.md` | the reviews behind the design |
| `AUDIO-ENGINE.md`, `EVENT-LOG.md`, `MCP-SERVERS.md`, `BENCHMARK.md` | contracts of the main project |

## Research notes from the agy assistant (`.scratch/speech-hosting/`)

`AGY-RESEARCH.md` (hosting), `AGY-STT-IMPROVE.md`, `AGY-PARAKEET-IMPROVE.md`, `AGY-PARETO-REPAIR.md` (STT), `AGY-NET-*.md` (poor-network practice), `AGY-SPEC-DECODE.md` (early-decode accuracy), `AGY-ECHO.md` (echo), `AGY-GEMINI-FAST.md` (Gemini models for a fast agent), `AGY-CHEAP-LLM.md` (cheap hosted LLMs from the Philippines; relies on 2024-era data), `AGY-PI-NEXT-v1.md` / `AGY-PI-NEXT.md` (one-word interrupt and AEC, **unreliable**: see `RESEARCH-HANDOFF-PI.md`), `PROMPT-PI-NEXT.md` (the brief), `VERIFICATION.md`. They are web-search summaries; claims are tagged by the assistant and partly unreliable (see `ECHO-RESEARCH.md` "How far to trust it").

## Results coming back from the Pi

`docs/pi-results/` (created by `scripts/pi_pack_results.sh`): one folder per run with `run.md` and `run.json`, plus your `NOTES.md`.

## Not in the repository (the owner's, local)

`docs/VOICE-AGENT-EVALUATION-METRICS.md`, `kek.txt`, `resources/building-voice-agents/` are the owner's untracked files; leave them alone.
