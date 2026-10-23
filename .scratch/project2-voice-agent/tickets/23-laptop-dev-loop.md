# 23: Laptop dev loop — the full interactive pipeline on the laptop with local STT/TTS and a scripted LLM

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 21, 22, 10 and 11 (the entry point and `ScriptedLLMClient`). Note: 10 and 11 currently wait on D1 only for model IDs and the cloud adapter; the scripted LLM needs no vendor. If the human splits D1 (agent core with a scripted client first), this ticket can start as soon as 11 lands.
**Status:** reduced; superseded in part

## Why
Most of the early work is making the agent feel interactive (turn-taking, fillers, barge-in). This ticket makes that work testable on the laptop with no paid keys: real mic, real speaker, real local STT/TTS, scripted replies. It is also the baseline the Pi results get compared to.

## Amendment 2026-10-23 (direction change: local speech, laptop mic + Pi loudspeaker, `doc_chat` findings)
Largely superseded: `python -m doc_chat --play-remote ... ` with a model-free or Gemini reply already runs the laptop loop with real mic, local STT/TTS and the Pi speaker, and `docs/PI-REMOTE-SPEAKER.md` holds the results table. What remains, once 11 exists: the `--fake-llm` flag, `config/laptop.toml`, and `scripts/run_laptop.sh`. The results-table item is done; do not redo it.

## Files
- `config/laptop.toml` (profile: `stt_backend="lan"`, `tts_backend="lan"`, loopback URL; mic/speaker devices left null)
- `scripts/run_laptop.sh` (starts `python -m lan_speech` with a token generated for the session, then `python -m voice_agent --config config/laptop.toml --fake-llm`; stops both on exit)
- `src/voice_agent/__main__.py`, `src/voice_agent/pipeline/app.py` (add only a `--fake-llm` flag: real STT/TTS from config, `ScriptedLLMClient` for the agent; `--fake` behavior unchanged)
- `docs/LAPTOP-SPEECH.md` (append "Running the loop" and a results table)
- `tests/pipeline/test_laptop_profile.py`

## Action
Add the `--fake-llm` mode and the laptop profile; the loop runs through the same `main()` and pipeline as the Pi. A default script gives 3 canned replies, including one that triggers a filler (stall) and one long reply to test barge-in. Run the file-mode benchmark (`--input-wav`, real `lan` engines, scripted LLM) on the ticket 13 turn set and record `ENDPOINT` → first answer audio and first-sound latency from the stage log, using the ticket 14 analysis where it applies. Document how to run it, and what differs from the Pi: no hardware AEC, laptop speaker/mic acoustics, CPU headroom.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| `lan_speech` server not running | the app exits 2 with a message naming `scripts/run_laptop.sh`; no hang |
| token unset | `run_laptop.sh` generates one for the session; the app never prints it |
| `--fake` and `--fake-llm` both given | exit 2 (mutually exclusive) |
| laptop speakers feed the mic (no AEC) | documented limitation; the VAD barge-in threshold in the profile is set one step higher; no code change to ticket 12 logic |
| server dies mid-session | `SpeechBackendError` path from ticket 11: apology clip or log; no crash |

### C — Contracts
No new public API. CLI: `python -m voice_agent --config PATH --fake-llm` (new flag only); config profile file only.

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `ScriptedLLMClient` exists with a script file format (ticket 10/11) | read `src/voice_agent/agent/`; use its real loader |
| A2 | The pipeline selects STT/TTS from `agent.stt_backend` / `tts_backend` via the registry | read `pipeline/app.py::build_deps` |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_laptop_profile_loads` — `config/laptop.toml` loads via `load_config` and selects `lan` | `uv run pytest -q tests/pipeline/test_laptop_profile.py` |
| P2 | `test_fake_llm_file_mode` — `main(["--config", laptop.toml, "--fake-llm", "--input-wav", F, "--output-wav", O])` against the in-process ticket-21 fake-engine server → non-empty output wav and a `TURN_DONE` per turn in the stage log | same |
| P3 | `test_fake_and_fake_llm_exclusive` | same |
| P4 | Results table in `docs/LAPTOP-SPEECH.md`: ENDPOINT → first answer audio and first sound (p50/p95) on the turn set, real engines | human-run: `scripts/run_laptop.sh --input-wav ...`; the agent writes the command, the human records numbers |

## Unknowns
- Real latency numbers depend on the laptop and on ticket 21's GO result.

## Dependencies/license
None new.

## Acceptance Criteria
- [ ] `--fake-llm` works through `main()`; `--fake` unchanged
- [ ] P1–P3 green; gate suite green; results procedure documented
- [ ] No token printed or logged

## Non-Goals
- No change to barge-in or filler logic; no AEC; no cloud calls; not deployed to the Pi

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`, `docs/LAPTOP-SPEECH.md`, `docs/AUDIO-ENGINE.md`

## Execution Log
