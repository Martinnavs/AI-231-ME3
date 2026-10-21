# Documentation index

Start with **`HANDOFF.md`**. Everything else is linked from there; this is the full list.

## The handoff set (read first)

| File | What it is |
|---|---|
| `HANDOFF.md` | the map: state, findings, decisions, open questions, next actions, working agreements |
| `PI-TEST-PLAN.md` | runbook for the Raspberry Pi echo test (what to run, how to read it, what each outcome means) |
| `BRANCHES.md` | the three branches, how they relate, how to land them, how to resolve conflicts |
| `ECHO-RESEARCH.md` | how assistants and frameworks handle echo and barge-in; how far to trust the report |

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
| `../.scratch/project2-voice-agent/tickets/` | tickets 01-24 (12 = barge-in gate; 21-24 = laptop speech track) |
| `../PROJECT2-DESIGN-REVIEW.md`, `../FULL-DUPLEX-RESEARCH.md` | the reviews behind the design |
| `AUDIO-ENGINE.md`, `EVENT-LOG.md`, `MCP-SERVERS.md`, `BENCHMARK.md` | contracts of the main project |

## Research notes from the agy assistant (`.scratch/speech-hosting/`)

`AGY-RESEARCH.md` (hosting), `AGY-STT-IMPROVE.md`, `AGY-PARAKEET-IMPROVE.md`, `AGY-PARETO-REPAIR.md` (STT), `AGY-NET-*.md` (poor-network practice), `AGY-SPEC-DECODE.md` (early-decode accuracy), `AGY-ECHO.md` (echo), `VERIFICATION.md`. They are web-search summaries; claims are tagged by the assistant and partly unreliable (see `ECHO-RESEARCH.md` "How far to trust it").

## Results coming back from the Pi

`docs/pi-results/` (created by `scripts/pi_pack_results.sh`): one folder per run with `run.md` and `run.json`, plus your `NOTES.md`.

## Not in the repository (the owner's, local)

`docs/VOICE-AGENT-EVALUATION-METRICS.md`, `kek.txt`, `resources/building-voice-agents/` are the owner's untracked files; leave them alone.
