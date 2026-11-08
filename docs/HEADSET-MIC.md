# A close microphone against background speech: measured (laptop, Sony WH-1000XM4)

**Bottom line (2026-11-08).** A microphone close to the mouth helped in one Whisper run (the XM4 worn on the head heard six of six test phrases exactly with a video playing, the laptop microphone one of six), but it did not hold up on the repeat or through the project's recogniser: a second video-on run gave six of six on both microphones with Whisper, and the live client (Kroko) with the headset still turned the video into six of its twelve turns, none dropped by the addressee gate. Software did not help either: an RNNoise filter left the background speech in and made the user's words worse, and the text-only gate cannot tell a TV from the user. One speaker, one room, the video's level varies between runs, one run per condition.

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

## Live through the client, headset, video on (Kroko, second video-on run)

`doc_chat --mic --mic-device pulse --addressee log` with `scripts/headset_mic.sh` holding the XM4, the same six phrases once, a video playing. Twelve turns reached the gate.

| Turn | Heard (Kroko) | Verdict |
|---|---|---|
| 2 | What's no other tomorrow? (weather, misheard) | addressed |
| 4 | Dad, can you pass the salt | addressed (leak) |
| 5 | the video's words + "May some jazz" | unsure |
| 7 | Hey, Jamie, dinner's ready | unsure (`followup` + `vocative`: a short turn right after a reply counts as a follow-up, so it is not dropped) |
| 8 | Set a timer for ten minutes | addressed |
| 9 | the video's words + "I forgot the water the herbs" | unsure |
| 1, 3, 6, 10, 11, 12 | only the video ("which photo do you guys think was like the most?" was `addressed`) | unsure x5, addressed x1 |

- The XM4 recording of the same run: floor -52 dBFS and speech p90 -29 dBFS, against -43 and -31 on the laptop microphone, so a 9 dB lower floor and 2 dB louder speech (the first run had 17 dB and 7 dB). Whisper heard all six phrases on both microphones this time and none of the video.
- So with the project's recogniser the headset did not keep the video out of the transcript, and the gate let all of it through.

## A partial check with the project's recogniser (Kroko)

The same recordings were cut into phrases (boundaries from the Whisper word timings) and replayed through `doc_chat --wav` and the speech server (Kroko; Parakeet is not downloaded on this laptop). The replay was unreliable: with short or padded tails it returned empty turns and results one clip late, so there is **no clean per-microphone comparison**. What the runs did show:

- XM4, quiet room: all six phrases came out, one misheard ("Place some jaz" for "Play some jazz", so `unsure` instead of `addressed`); `Dad, can you please pass the salt?` was addressed (leak) and `Hey, Jamie, dinner's ready` was ignored.
- Both microphones, video playing: Kroko wrote down words of the video (`We say long-term relationship and it might scare away any short-term casual danger`, `A lot of girls`) and ran them into the user's phrases. It is more distracted by background speech than Whisper was, so the 6-of-6 against 1-of-6 above comes from Whisper and may overstate the gap for the project's recogniser.

A fair comparison needs a live run through the client with the headset script, speaking the six phrases with the video on and then off, once per microphone.

## Not covered

Parakeet on headset audio; the Pi deployment (a worn headset is not the product setup: it needs a USB array or close microphone of its own); several speakers or rooms; Bluetooth latency in the loop.
