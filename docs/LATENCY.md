# Latency: making slow answers sound deliberate (2026-10-24)

**Status:** measured and designed; the code is written and tested as reference, ticketed for agy (`.scratch/latency/tickets/01-03`), **not yet in `src/` or `pipeline/`**. Plan and decisions: `.scratch/latency/PLAN.md`; research: `.scratch/latency-research/SUMMARY.md`.

**The problem (owner, 2026-10-24):** the LLM's response time spikes, and the demo will see the same. The goal is that a slow turn sounds deliberate and a stuck one ends with a sentence. Lowering the average is only part of it.

## Where the time goes

From the MCP spoken check (`out/sessions/mcp-spoken-1`, one session, 8 questions): **end of speech to first sound took 3.5-10.9 s.** Per turn:

| Stage | Time |
|---|---|
| End-of-turn wait + recogniser (transcript ready) | ~1.0-1.3 s |
| **LLM first response** | **0.7-7.7 s** (7.7 s for "Turn on the lights", before any tool) |
| Tool + **second LLM request** | 1.2-5 s |
| TTS first audio | ~0.4-0.7 s |

Every answer is now a tool turn, so every answer pays for two LLM requests. The model stays `gemini-3.1-flash-lite`. Jev Router through OpenRouter was 2.94 s against 0.98 s first token, picked the right tool 14/16 against 16/16, and cost 10x as much (`.scratch/latency-research/jev_check.py`).

## Measurements (R1)

- **Open-Meteo keeps an idle connection only 3-5 s** (`.scratch/latency/keepalive_probe.py`): 264-303 ms on the open connection after 1-3 s idle, 1.10-1.83 s after 5-60 s idle. Keeping the client open cannot help across turns minutes apart. The plan is to refresh the home forecast in the background every 10 minutes instead (R5).
- **LLM spike probe, final** (`pipeline/src/voice_pipeline/latency_probe.py`, 44 pairs = 88 requests, 16:27-19:21 with a laptop suspend in between; summary `pipeline/out/latency_probe/20261024-162724.txt`): one request to the first sign of an answer: **p50 1.33 s, p90 2.86 s, max 12.55 s; 15/88 over 2 s, 8/88 over 4 s**. Best of the two (what sending two requests and taking the first would give): **p50 1.18 s, p90 1.94 s, max 8.52 s**. Of the 11 pairs with a request over 2 s, the other request was fast in 7 (a second request would have rescued it) and slow too in 4 (the provider at that moment). Caveats: one cold pair right after the suspend (10.1 s / 4.1 s); the Pi was unreachable after the suspend, so later `pi` prompts met a failing tool (max 12.55 s). **This supports R4** (two requests, take the first): p90 2.86 -> 1.94 s for twice the requests (about 1-2 US cents more per hundred turns at the ~$0.00008 a request measured in the Jev check); it is the owner's call.

## What the research says (`.scratch/latency-research/SUMMARY.md`, run D)

- **Timing:**
  - Verbal fillers work best early, roughly 200-500 ms after the user stops (Lala et al., Interspeech 2019).
  - The experience degrades beyond about 4 s (Maslych et al., arXiv:2507.22352).
  - Products give up at about 8 s with an apology (Alexa's limit).
- **Form:**
  - Use speech, not a chime: a chime confirms reception but doesn't make the wait feel shorter.
  - Rotate the lines, never repeat one back to back, and only speak when the answer is actually late.
- **The tool's own sentence:** Vapi's `bot_say` idea. The model writes a context-aware first sentence inside the tool call itself, so no extra request is needed.

## The safeguards (owner's decisions, 2026-10-24)

| # | What | Where | State |
|---|---|---|---|
| R3 | **Fast path:** the 11 command kinds in the table below match exact phrasings on the last user message and run the real tool directly, then speak a fixed confirmation (or the tool's own error) without calling the LLM. Anything else goes to the LLM as before. The turn stays in the history, so follow-ups work. | `pipeline/src/voice_pipeline/fast_path.py`, `agent_server.py` (`--fast-path`, on by default) | ticket 01 |
| R2a | **`say` argument on every tool:** a short sentence the model writes in the call ("Checking the weather in Baguio."). It is spoken when the call starts and removed before the tool runs. If it's missing, the agent server sends `{"event": "tool", "name": ...}` instead. | `agent.py` (`TurnTap._announce`), `agent_tools.py`, `agent_server.py`, `system_agent.txt` | ticket 02 |
| R2b | **Fillers in doc_chat** (`--fillers`). Times count from when the transcript is ready: **stall at 1.2 s** (fast answers get nothing), the **tool's line** if a tool event comes first, an honest **notice at 2.5 s**, and **give-up at 8 s** (cancel, apologise, drop the turn, log `gave_up`). At most one stall-or-tool line per turn, and none once the answer has started. Lines are pre-synthesised at session start. They play through the answer's own audio path, so the echo guard counts them as the bot speaking. They stay out of the chat history. | `src/doc_chat/fillers.py`, `config/fillers.json`, `__main__.py`, `agent_client.py` | ticket 03 |
| R5 | One LLM request instead of two for tools whose answer is already a sayable sentence (`run_llm=False`), gated on a Gemini history test; home forecast refreshed in the background | `agent_tools.py`, `web_info.py` | later |
| R6 | Demo-day warm-up: touch every server and tool, check the network, keep-alive while waiting | `scripts/demo_warmup.py` | later |
| R4 | Two requests at once, take the first | `agent.py` / `agent_server.py` | R1 supports it (p90 2.86 -> 1.94 s); owner to decide |

**Why the stall is at 1.2 s, not 500 ms:** the transcript is ready about 1.0-1.3 s after the user stops, so 500 ms "after the end of speech" has always already passed when the agent starts. The owner chose 1.2 s after the transcript.

**Phrases** (reviewed by the owner, `.scratch/latency/fillers-draft.md`):
- 7 stall lines ("Mm, one moment.").
- 7 lines for each tool group: smart_home, time, weather, wiki/docs, volume set, volume/status check, and a default set.
- 7 notice lines ("Sorry, this is taking a little longer than usual.").
- 5 give-up lines ("Sorry, that's taking too long. Could you ask me again?").

None of them claims a result.

**Fast-path commands:**
- Lights on and off.
- Play, pause, stop and next for music.
- Volume up and down.
- A timer with a number and unit.
- An alarm at a clock time (always written "H:MM AM/PM").
- The speaker volume with a number (`set_volume`).

A leading "please", "hey" or "okay" is ignored, but "stop" and "wait" are not, so "stop the music" still matches. On the dry-run prompts, 11 of 82 single-turn prompts take the fast path, all with the expected tool and arguments.

## How it was checked

The reference code was tested in a scratch copy (`.scratch/latency/overlay/`, `skeleton/`):
- `pipeline/`: ruff and mypy clean, 61 passed.
- Root: 253 passed in `tests/doc_chat` (the known `test_flow` case deselected; it needs a speech server).

The tickets are generated from real file states by `.scratch/latency/gen_tickets.py`. They were applied from their text alone by `apply_ticket.py`, every Proof was green, and the result matched the reference byte for byte.

**Not yet done:**
- A paid dry run to check that routing still holds and that the `say` sentences are good.
- A spoken session with `--fillers` (does a filler collide with a fast answer? does the guard stay quiet over filler audio?).
- The owner's call on R4.
