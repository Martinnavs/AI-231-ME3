# A close microphone against background speech: measured (laptop, Sony WH-1000XM4)

**Bottom line (2026-11-08).** The only thing that kept background speech out of the recording was a microphone close to the mouth: the XM4 worn on the head heard six of six test phrases exactly with a video playing, the laptop microphone one of six. Software did not help: an RNNoise filter left the background speech in and made the user's words worse, and the text-only addressee gate cannot tell a TV from the user. One speaker, one room, Whisper (small) transcripts rather than the project's recogniser, a single run per condition.

## What was measured

Both microphones recorded at the same time (`parecord`, 16 kHz); the same six phrases were spoken once per condition. Whisper small transcribed them; the project's `doc_chat.addressee.decide` judged the text.

| Condition | XM4 floor / speech (p90) | Laptop floor / speech (p90) | Phrases exact, XM4 | Phrases exact, laptop |
|---|---|---|---|---|
| Background video playing, headset worn | -61 / -20 dBFS | -44 / -27 dBFS | 6 of 6 (video only as faint fragments) | 1 of 6 |
| No background, headset worn | -74 / -18 dBFS | -44 / -28 dBFS | 4 of 6 | 5 of 6 |
| Background playing, headset **not** worn close | 5-10 dB quieter than the laptop in most windows | n/a | the video won over the voice | mostly right |

- In a quiet room the headset buys no accuracy (small slips on both: "can you *please* pass", "I forgot *the* water", "can you *play past*"). Its value is a lower floor and a louder voice when something competes.
- The not-worn run was the one that looked like a bug. It was not: the XM4's microphones are on the ear cups, and the laptop speaker (120 % volume) sat beside it.

## What did not help

| Tried | Result |
|---|---|
| RNNoise as a PipeWire filter-chain microphone | Background speech passed through (it is speech); output 60 dB quieter; words dropped ("Can you run the website search to get my data" became "To run the website search to get my details"). Removed. |
| Text-only addressee gate (`--addressee`), 64 requests + 22 side-talk lines | 1 of 64 requests wrongly dropped (`mom said you know ...`); with `--addressee-declaratives` 4 of 64. Side talk blocked only when it carries a name or kinship word and no request word: 11 of 22 as text, 4 of 12 through speech recognition. |

Through the real speech server (86 synthetic clips, ten voices): word error 5.9 %, every transcript capitalised, only 32 of 86 had any `, . ?`. The name rule needs a comma, so `Jamie dinner's ready` is not caught; names are misheard ("Hey Jamie" became "Hey, Jere").

Gate verdicts on the live XM4 phrases (both microphones agree):

| Said | Verdict |
|---|---|
| What's the weather tomorrow? | addressed |
| Dad, can you pass the salt? | addressed (leaks: `can_you`) |
| Play some jazz. | addressed |
| Hey Jamie, dinner's ready. | ignore |
| Set a timer for ten minutes. | addressed |
| I forgot to water the herbs. | unsure (answered, default mode) |

## Running with the headset

```bash
scripts/headset_mic.sh            # own terminal, leave open; Ctrl-C restores everything
```

- Connected: switches the headset to its call profile, makes its microphone the default source, sets volume 100 % and unmute, and holds the microphone open. The XM4 is playback-only (A2DP, no microphone) when idle and drops back about 20 s after the last recording stops; without the hold, the client's first seconds can be digital silence (`room -120 dBFS`).
- Not connected: sets the laptop microphone (`Mic1`) as the default source and exits. `HEADSET_MATCH` changes the name looked for.
- Then run the client with the input made explicit:

```bash
PULSE_SOURCE=<source printed by the script> uv run --group laptop python -W ignore -m doc_chat \
    --mic --mic-device pulse --addressee log ...
```

`--mic-device` takes a sounddevice index or part of a name (`python -m sounddevice` lists them). Without it the client opens PortAudio's default input, which on this laptop was the laptop microphone even while the PipeWire default source was the headset (a laptop-speaker tone read -33 dBFS on the default, -51 dBFS via `pulse` with the headset source).

### How to check

Say one phrase with the video playing and record both microphones for 10 s (`parecord -d <source> a.wav`, once for the headset source and once for the laptop one). The headset file should hold your words and little of the video; if the two transcripts look alike, the client is on the wrong input.

## Side fix found on the way

In `--addressee log` mode an interrupted turn was logged without the gate's verdict, and the unlogged verdict stayed set for the next turn. `_take_addressee_note` now attaches it once to whichever record is written (`src/doc_chat/__main__.py`).

## Not covered

The project's own recogniser (Kroko, Parakeet) on headset audio; the Pi deployment (a worn headset is not the product setup: it needs a USB array or close microphone of its own); several speakers or rooms; Bluetooth latency in the loop.
