# Echo cancellation across two devices (laptop mic, Pi speaker): measured, offline

**Bottom line (2026-10-23).** The laptop knows exactly what it sends to the Pi speaker, so WebRTC's echo canceller can use that stream as its reference even though the speaker is on another machine. Offline, on recordings from the production setup (Pi speaker at 80%), it removes most of the echo the speech recogniser hears and keeps the user's speech: the recogniser heard the whole echoed reply in 6 of 6 echo-only clips on the raw microphone and a 0-7 word fragment after cancelling. One session, one speaker, one room; not yet in the live loop.

The reason my first offline attempt looked poor (about 5 dB) was not the canceller: the two sound cards run on different clocks, and the echo drifts by about 137 ppm (8 ms per minute) against the reference. A fixed-delay reference then has almost no coherence with the microphone; WebRTC's canceller tracks the drift by itself, and a drift-corrected reference confirms it is a clean linear path.

## What was measured

Tools (both new): `scripts/echo_path_probe.py` (plays known noise and tone through the Pi speaker, records the laptop microphone, analyses the path) and `scripts/aec_offline.py` (rebuilds the reference of an existing `remote_echo_check.py` recording from the fixed clips in `tests/fixtures/pi_echo`, aligns it by phase-transform correlation, runs LiveKit's `AudioProcessingModule` with the reference fed 200-500 ms ahead, and reports detector and transcript changes). Run with `uv run --group laptop --with livekit ...` (livekit is not yet a project dependency).

**The path, from the probe (`out/remote_echo/probe-80`, Pi 80%, laptop mic, room -46.5 dBFS):**

| Quantity | Result |
|---|---|
| Echo level | loud burst (a reply's level) 20.0 dB above the room, quiet burst (12 dB lower) 8.9 dB |
| Delay | not constant: a ramp of 2.2 samples/s = 137 ppm = 8.2 ms per minute (the Pi's USB speaker clock against the laptop's microphone clock); 0.5 s blocks followed it smoothly |
| Linear coherence with a fixed-delay reference | 300-600 Hz 0.27; 600-4000 Hz at most 0.02, so a linear canceller could remove about 0.5 dB |
| Coherence with a drift-corrected reference | 300-600 Hz 0.76, 600-1000 0.79, 1-2 kHz 0.71, 2-4 kHz 0.51 |
| Nonlinearity | gain 2.2 dB lower at the loud level than at the quiet one; the 1 kHz tone's 2nd harmonic 36.5 dB below the fundamental, 3rd and 4th over 55 dB: mild |
| WebRTC canceller on the probe, reference 300 ms ahead, no drift correction | loud burst 29.7 dB quieter after 4 s, quiet burst 14.2 dB (these include the canceller's suppression of the noise floor, so they overstate echo-only removal) |

A synthetic check of the analysis: a clean linear path gives coherence 0.99, a 2 ms delay wobble 0.04, a clipping speaker is flagged by the gain change and harmonics. The first reply after idle arrives about 0.55 s later than the others (the Pi opening its sound device), which a live canceller has to absorb.

**Speech (`out/remote_echo/fresh80`: 6 talk-over clips with a beep at 2.5 s where the owner said "Wait, stop, I have a question.", and 6 echo-only clips; reference drift-corrected with the probe's 137 ppm, fed 300 ms ahead):**

| | Raw microphone | After cancelling |
|---|---|---|
| Echo-only clips: Silero (0.5, 7 of 10) fires | 6 of 6 | 1 of 6 (0.4 s into the clip, while converging) |
| Echo-only clips: what the speech recogniser heard | the full echoed reply in 6 of 6 (about 15 words each) | 1 empty, 1 "We're getting", then "Where did the crossing of the corner?", "For guide feet.", "Look at the routine.", "A lot of funday" |
| Talk-over clips: "Wait, stop, I have a question." heard correctly (4 s after the beep) | 4 of 6 ("Stark", "stop ever question") | 5 of 6; extra fragment "Our guide pays." once; "position" once |
| Talk-over clips: Silero first fires | at the beep (it was already firing on the echo) | 0.6-0.9 s after the beep, on the user |
| Total-power reduction while the clip plays | | median 5.6 dB: not informative, the room floor in that session was -35 dBFS and the echo only 5-20 dB over it |

## What this does not show

- Offline, on a reconstructed reference; the live loop (reference fed at send time, 10 ms frames, threads) is untested.
- 137 ppm is from one probe run; the offset between two clocks is stable but should be re-measured, and the live canceller must either track it (WebRTC did on noise) or be given a corrected reference.
- One speaker, one room, 12 clips; the residual fragments (3-7 words) would still reach the language model as a question, so text-level guards stay necessary.
- Speech is the owner's, close to the microphone; double-talk with a quieter or farther speaker is untested.
- The speaker position was close to the microphone in these recordings. How well it works with the speaker in front of the laptop (the consumer arrangement) is the next test.

## Next (done: built behind `--aec` and `--duck-db`, tried in `docs/LIVE-AGENT.md` session 3; the A/B and tuning are open)

Add the canceller to the live loop behind a flag (`doc_chat --aec`): the remote player feeds each slice it sends to the canceller's render stream, and the microphone blocks go through it before the guard and the speech recogniser; then repeat the interruption session with the speaker placed in front.
