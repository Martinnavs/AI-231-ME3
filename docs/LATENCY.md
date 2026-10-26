# Latency: making slow answers sound deliberate (2026-10-24)

**Status:** R2 and R3 are **landed** (agy tickets `.scratch/latency/tickets/01-03`, commits `deaa6d5`, `891f756`) and were tried once in a spoken session; **two bugs found, not yet fixed** (below). Plan and decisions: `.scratch/latency/PLAN.md`; research: `.scratch/latency-research/SUMMARY.md`.

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
- **Re-run 2026-10-26 with a 300 s keep-alive** (30 pairs, `pipeline/out/latency_probe/20261026-131405.txt`): one request p50 0.98 s, p90 1.31 s, 3/60 over 2 s; best of two p50 0.90 s, p90 1.09 s, max 1.20 s. The next bullet was measured with cold connections (one request a minute) and overstates the provider's spikes; see `docs/OPTIMIZATION-RESEARCH.md`.
- **LLM spike probe, final, cold connections** (`pipeline/src/voice_pipeline/latency_probe.py`, 44 pairs = 88 requests, 16:27-19:21 with a laptop suspend in between; summary `pipeline/out/latency_probe/20261024-162724.txt`): one request to the first sign of an answer: **p50 1.33 s, p90 2.86 s, max 12.55 s; 15/88 over 2 s, 8/88 over 4 s**. Best of the two (what sending two requests and taking the first would give): **p50 1.18 s, p90 1.94 s, max 8.52 s**. Of the 11 pairs with a request over 2 s, the other request was fast in 7 (a second request would have rescued it) and slow too in 4 (the provider at that moment). Caveats: one cold pair right after the suspend (10.1 s / 4.1 s); the Pi was unreachable after the suspend, so later `pi` prompts met a failing tool (max 12.55 s). **This supports R4** (two requests, take the first): p90 2.86 -> 1.94 s for twice the requests (about 1-2 US cents more per hundred turns at the ~$0.00008 a request measured in the Jev check); it is the owner's call.

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
| R2b | **Fillers in doc_chat** (`--fillers`). Times count from when the transcript is ready: **stall at 1.8 s** (fast answers get nothing; raised from 1.2 s on 2026-10-25 because the typical first LLM call is 1.0-1.4 s, so 1.2 s fired just before ordinary answers), the **tool's line** if a tool event comes first, an honest **notice at 2.5 s**, and **give-up at 8 s** (cancel, apologise, drop the turn, log `gave_up`). At most one stall-or-tool line per turn, and none once the answer has started. Lines are pre-synthesised at session start. They play through the answer's own audio path, so the echo guard counts them as the bot speaking. They stay out of the chat history. | `src/doc_chat/fillers.py`, `config/fillers.json`, `__main__.py`, `agent_client.py` | ticket 03 |
| R5 | One LLM request instead of two: `weather`, `wiki_summary`, `get_volume`, `set_volume` speak their own result sentence; a `smart_home` command that worked ends on the model's `say`. `TurnTap._result` vetoes (the model still answers) when the response has several calls, when `smart_home` had no `say`, or when a tool failed. The Gemini history test passed (next user turn accepted with or without a model reply after the tool result; 1 trial each). Home forecast refresh in the background: not done | `agent.py`, `agent_tools.py`, `tools/smart_home.py` | landed 2026-10-25; dry run `r5-full` 85/87 exact, spoken time of tool turns 1.57 s (was 2.05 s), weather 0.85 s (was 2.09 s); one run each |
| R6 | Demo-day warm-up: `scripts/demo_warmup.py` checks the network (TLS to Gemini, Open-Meteo, Wikipedia), the Pi ports, warms the speech server (one TTS line, one STT clip) and sends one read-only request per tool through the agent server (7 requests, about 12 s in all); prints PASS/SLOW/FAIL per line and READY; `--keepalive S` sends a tiny request every S seconds | `scripts/demo_warmup.py` | landed 2026-10-25; one run: all PASS, Open-Meteo connect SLOW at 944 ms |
| R4 | Two requests at once, take the first: `HedgedAgent` (`hedge.py`) runs two full `Agent` pipelines per turn; the first to produce a tool call or text wins, the other is cancelled and silent, and every tool handler is gated so only the winner runs it (two requests must not both set the volume). `--hedge SECONDS` on the agent server: 0 (default) = both at once, >0 = second only if the first is silent by then, -1 = off. Each row in `out/agent_server/` has `hedge_winner` and `hedge_requests` | `hedge.py`, `agent_server.py` | landed 2026-10-25; 6 live requests: each tool ran once, the winner alternated 0/1. Whether it cuts the tail is not yet measured on the real server (the probe is the evidence: p90 2.86 -> 1.94 s over 44 pairs) |

**Why the stall is after the transcript, not 500 ms after speech:** the transcript is ready about 1.0-1.3 s after the user stops, so 500 ms "after the end of speech" has always already passed when the agent starts. The owner chose 1.2 s after the transcript; it became 1.8 s after `fillers-3`.

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

## Dry run with `say` (87 prompts, Pi absent; rows `pipeline/out/agent_dryrun/latency-say-1..4`)

Before `say`: 85-88 exact. With `say`: 81 and 79, because the model called tools with no value for incomplete requests ("Set an alarm", "Message my boss"); the tool then asked the question, so the spoken answer was right but the call was wrong. A one-line prompt change (Case E gets no tool call and no `say`, `891f756`) gave 82 and 81. Still about 3-4 exact below before. Keeping `say` on is the working choice; turn it off if the spoken session shows no gain.

## Spoken session 1 with `--fillers` (`out/sessions/fillers-1`, 2026-10-24, owner speaking, Pi 80%)

Flags: `--aec --duck-db -15 --strip-echo-prefix --barge-warmup-s 1.0 --fillers`.

**Worked**
- **Give-up:** with the agent server paused, the stall line, the notice, and at 8 s the apology and silence; the loop stayed alive. Logged `gave_up`.
- **Fast path:** "Turn the lights on" took 0.0 s on the server; "Set the volume to sixty percent" 0.23 s (2.0 s to first sound). No filler on the second.
- **Routing:** weather, Wikipedia, docs right.

**Bug 1: the `say` sentence is spoken late.** The server sends it the instant the tool call starts (1.4 s), but the client's `SentenceStream` (`src/doc_chat/chain.py`) releases a sentence only once the next one starts, so it waited for the tool result (~5.4 s). Weather: first answer audio 6.9 s after the user stopped, the `say` behind the stall and the notice. Because `say` replaces the tool event, the tool's own filler line never played either. **Fix:** send the `say` as its own event, speak it directly (synthesise and push to the player, bypassing the splitter), and always send the tool event too.

**Bug 2: filler audio leaks into the transcript.** The user paused mid-question, fillers played, the user resumed, and the recogniser heard the filler's words: "...two Nobel Prizes? **Almost there. Got it.**" `--strip-echo-prefix` only removes the start of a transcript. **Fix:** the filler phrases are a fixed set, so strip any exact occurrence of them anywhere in the transcript.

**Smaller:** on the first turn "Mm, one moment." played although the fast-path answer was ready at 0.4 s (audio only started 1.3 s after the transcript: a cold first turn). The stall should check whether answer text has arrived, not only whether audio has started.

## Fixes landed 2026-10-25 (not yet heard in a spoken session)
- **Bug 1:** the server sends the `say` as `{"text", "flush": true}` and always sends `{"event": "tool", "name", "said"}`. The client marks the sentence with `FLUSH` (`chain.py`), so `SentenceStream` releases it at once. A tool event with `said` plays no tool filler.
- **Bug 2:** `strip_fillers` (`fillers.py`) removes any filler line from the heard text in `Session.handle`.
- **Smaller:** the client raises `{"event": "text"}` on the first reply text; `TurnFillers.text_arrived` then skips the stall.
- Tests: `tests/doc_chat/{test_stream,test_agent_client,test_fillers}.py`, `pipeline/tests/test_say.py`.

## Not yet done
- Repeat the same spoken script with `--fillers`; the `say` dry-run cost is still to re-measure.
- R4 (the owner's call), R6 (warm-up), R5.

## Spoken session 3 and what it changed (`out/sessions/fillers-3`, 2026-10-25, 13 turns)

**Worked:** one LLM request per tool turn (server `end_s` 1.3-2.3 s); the `say` flush (weather first audio 1.9 s while its tool took 5 s); one short sentence for LLM-routed commands; fast path 0.0 s on the server. **Problems found:** R5 read a Wikipedia failure aloud (fixed: failure sentences go to the model, `f210e9d`); the stall fired on 4 of 13 turns just before a normal answer; a `say` followed straight by the answer sounded artificial (Steinbeck question); weather had a silent gap after its `say`; one LLM spike of 7.5 s.

**Changes (owner's direction, same day):**
- **`say` is held 0.6 s** (`TurnTap.say_delay_s`). If the tool's sentence arrives first, the `say` is dropped and the answer is spoken alone. A `smart_home` command speaks its `say` at once as the reply, and the `say` always goes out before any text of the model's own.
- **Canned lines follow the `say`.** The client knows which sentence is the `say` (`SentenceStream.flushed`); its audio counts as speech for the echo guard and for barge-in but not as "the answer". An announced `say` makes the stall and notice wait for it to start (up to 3 s), finish, and `gap_s`. The tool's own line is no longer used when a `say` is announced.
- **Stall 1.2 -> 1.8 s.**
- Not yet heard in a spoken session.
