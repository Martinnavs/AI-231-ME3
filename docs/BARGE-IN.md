# Interrupting the assistant (barge-in) on the laptop

Status (updated 2026-10-21): step 0 measurements done (below). Barge-in **was built differently from this design**, in `doc_chat` (`--barge-in`, on by default with `--mic`; `src/doc_chat/speech.py`, `bargein.py`): recognition stays open, a detector (loudness or Silero, `--vad`) decides you started talking, the speaker stops at once with a short fade, and a cut-off turn is joined to what you say next. Not built from the plan below: pausing and resuming the speech, confirming with recognised words before stopping, `--aec`, the 3 s start grace. What was measured since, and what to do next: `docs/INTERRUPTS.md`, `docs/ECHO-RESEARCH.md`, `docs/PI-TEST-PLAN.md`. The original design is kept below for reference.

## Design

- **Speech recognition stays open and always receives live mic audio.** No gating, no pre-roll, no re-`start`. The industry stacks checked in source (LiveKit Agents, Pipecat) never close recognition while the agent speaks; LiveKit even feeds *silence* rather than closing the stream when it discards audio. Recognition running is what lets us count words, ignore "uh-huh", and spot our own echo by text.
- **Silero VAD pauses the speaker at its first detection** (full pause, like LiveKit): a short fade-out, then silence, position kept so playback can resume.
- **Recognition confirms**: at least 1 non-backchannel word that does not match what the assistant is currently saying, or 600 ms of sustained speech as a fallback. On confirmation: abort playback, cancel TTS, close the Qwen stream. With no confirmation within 2 s: resume the speech.
- **Speaking before the assistant makes a sound** merges into the previous question.
- Flags (planned): `--barge-in` (off by default), `--barge-confirm-words 1`, `--barge-fallback-ms 600`, `--barge-resume-s 2.0`, `--aec`.

Why not close recognition while the assistant talks: with only a VAD you cannot tell a cough or "yeah" from "wait", cannot filter echo by text, and a late or false stop feels broken. See the research notes in the session log; sources checked: `livekit/agents` (`voice/turn.py`, `agent_activity.py`) and `pipecat` (`turns/`).

## Step 0 measurements (this laptop, 2026-10-18)

| Question | Result |
|---|---|
| Silero VAD, threshold 0.6, min speech 0.05 s, 50 ms blocks, on 24 recorded sentences | fires **140 ms (p50) / 398 ms (p95)** after the first sound; 0 of 4 noise clips (typing, breathing, tapping) trigger a 250 ms detection; 0 missed sentences; 0.23 ms per block. Threshold 0.5 gave one false trigger on the noise clips. |
| Does the assistant's own voice trip the VAD without echo cancellation? | **Yes: 91-92% of blocks flagged, runs of 4.5-7.3 s.** The echo is as loud as your speech (about -28 dB against a -45 dB room). |
| With PipeWire WebRTC echo cancel (`scripts/aec.sh on`) | **0%** of blocks flagged, echo below the room floor (-62 dB) after the first play. The **first play** still leaks (about 11 dB above the floor) without tripping the VAD, so ignore barge-in for the first 3 s (LiveKit does the same). |
| Time to silence after a stop command, acoustic, mic delay included (about 20 ms) | `abort()` with PortAudio `latency='low'`: **45-55 ms**; fade-then-silence pause, low latency: **69-98 ms**. With the **default** latency: 196-458 ms (abort), 117-330 ms (pause). **The player must open its stream with `latency='low'`.** |
| Echo cancel side effect | The room floor drops by 12-17 dB (the module also suppresses noise). Whether Kroko's accuracy survives this is **not yet tested** with real speech. |
| Audio routing | `aec.sh` never changes default devices; the client must select `va_aec_source` for the mic and `va_aec_sink` for the speakers (`PULSE_SOURCE` / `PULSE_SINK` with the `pulse` PortAudio device). |

Measurement pitfalls found: PortAudio's output into PulseAudio can carry a remembered per-app volume, so test playback with `paplay`; other apps playing sound (a browser video) contaminate echo tests, so pause media first.

## Tools

```bash
scripts/aec.sh on|off|status     # load/unload the echo canceller; leaves default devices alone
uv run --group laptop python scripts/echo_check.py --clip some_speech.wav --gain-db -12
                                 # plays speech, records the mic, reports VAD self-triggers with AEC off and on
```
