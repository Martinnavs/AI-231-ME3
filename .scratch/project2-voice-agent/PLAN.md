# PLAN 1: Project 2 voice agent (disposable; the durable picture is DESIGN.md)

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Test pass:** required — production behavior, security (sandbox, path confinement) and public interfaces (MCP tools, CLI, log schema) are all new.
**Documentation pass:** required — `docs/PI-SETUP.md` and `docs/BENCHMARK.md` are operational runbooks; `document-writer` polishes the `docs/` reference files after review.
**Tech-lead review:** required. **Security audit:** tickets 06 and 08.

| # | Ticket | Blocked by | Security |
|---|---|---|---|
| 01 | Scaffold, config loader, gate suite | — | no |
| 02 | Stage event log (JSONL) | 01 | no |
| 03 | Pi health sampler | 01 | no |
| 04 | `audio_engine` contract + fakes | 01 | no |
| 05 | Deepgram STT / Cartesia TTS adapters | 04, D1 | no |
| 06 | Speech MCP server | 04 | **yes** |
| 07 | Home MCP server: lights, thermostat, phone (ME2 dashboard) | 06 | no |
| 08 | Sandbox runner + code MCP server | 06 | **yes** |
| 09 | MCP client manager | 06, 07, 08 | no |
| 10 | Agent core (fast agent, delegate, subagent) | 09, D1 | no |
| 11 | Live Pipecat pipeline + entry point | 02, 05, 10 | no |
| 12 | Barge-in gate, cancel/flush, speculative start, fillers | 11 | no |
| 13 | Benchmark truth set | 04 | no |
| 14 | Benchmark analysis + report | 13, 02, 03 | no |
| 15 | Rig capture + `bench.run` | 14, 11 | no |
| 16 | Pi preflight, systemd units, setup runbook | 12 | no |
| 19 | Media MCP server: local music, real playback, ducking | 16 | no |
| 20 | Assistant MCP server: time, weather, timers, alarms, reminders | 19 | no |
| 18 | Task-success suite + fast-model comparison (Anthropic / Groq / DGX, D4) | 19, 20 | no |
| 17 | Test-engineer pass (audit + E2E) | 01–16, 18–20 | no |

Execution order: 01–16, then 19, 20, 18, 17.

Amended 2026-10-15 after `FULL-DUPLEX-RESEARCH.md`: duck-then-fade barge-in (01, 02, 12, 14), explicit voice prompt rules and provider-neutral LLM errors (10), one ONNX thread per model (11), Pi 4 hardware rules and an ALSA period check (16), and ticket 18.

Amended again 2026-10-15 after the professor's ME3 briefing: no model on the Pi except VAD (smart-turn dropped; turn end from cloud STT, 01/02/04/05/11); pause trials and takeover rate (13/14); home server narrowed to lights/thermostat/phone (07); `publish_site` for the complex task (08, 16); hidden tools (09); warmer, correction-aware prompt (10); new tickets 19 (media) and 20 (assistant); ticket 18 rewritten as the task-success suite with a provider comparison including the class DGX.

Shared contract docs (written by their producer ticket, read by consumers): `docs/EVENT-LOG.md` (02), `docs/AUDIO-ENGINE.md` (04), `docs/MCP-SERVERS.md` (06), `docs/BENCHMARK.md` (13).

Test Boundaries: each developer ticket owns its unit tests (its RECAP Proof table). Ticket 17 owns cross-ticket E2E, which must call `voice_agent.__main__.main`, `bench.*.main` and spawned MCP servers — never a hand-wired call graph.
