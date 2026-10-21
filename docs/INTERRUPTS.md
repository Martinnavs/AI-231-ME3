# Interruptions: which detector, measured

Branch `agy/interrupts`. No language model and no human: `python -m doc_chat --llm echo` reads
random seeded sentences; `scripts/interrupt_bench.py` puts a scripted voice on the microphone and
a speaker that plays at real speed, makes no sound, and leaks what it plays back into the
microphone at a chosen level (the echo, in dB: -30 is a headset, -12 to -6 is loud laptop speakers).
Parakeet speech server, real synthesiser, 2 repetitions per cell, one synthetic voice.

```bash
uv run --group laptop python scripts/interrupt_bench.py --ablation --reps 2     # about 60 minutes
uv run --group laptop python scripts/interrupt_bench.py --reps 2                # the full scenario set
```

## Ablation (what decides that you started talking over it)


Cells are counts of trials; 'self' = it stopped itself with nobody speaking (x loops).

| configuration | sentence: stopped, p50 ms, word err | 'stop' heard | +echo -12: stopped by you / by itself | echo -18 self | echo -12 self | echo -6 self | noise 0.3 s self | lost |
|---|---|---|---|---|---|---|---|---|
| energy | 2/2, 255, 0% | stopped 2/2, heard 2/2 | 2/2 / 0/2 | 1/2 | 2/2 (x7) | 2/2 (x35) | 2/2 | 0 |
| energy+echo | 2/2, 227, 0% | stopped 2/2, heard 2/2 | 2/2 / 0/2 | 0/2 | 2/2 | 1/2 | 2/2 | 0 |
| energy+echo+guard | 2/2, 212, 0% | stopped 1/2, heard 1/2 | 2/2 / 0/2 | 0/2 | 1/2 | 1/2 | 2/2 | 0 |
| silero | 2/2, 252, 0% | stopped 0/2, heard 0/2 | 0/2 / 2/2 | 2/2 (x36) | 2/2 (x34) | 2/2 (x34) | 0/2 | 0 |
| silero 150ms | 2/2, 148, 0% | stopped 2/2, heard 2/2 | 0/2 / 2/2 | 2/2 (x20) | 2/2 (x37) | 2/2 (x37) | 0/2 | 0 |
| silero+guard | 2/2, 435, 0% | stopped 0/2, heard 0/2 | 0/2 / 2/2 | 2/2 (x6) | 2/2 (x4) | 2/2 (x4) | 0/2 | 0 |
| silero&echo | 2/2, 338, 0% | stopped 0/2, heard 0/2 | 2/2 / 0/2 | 2/2 | 2/2 (x4) | 2/2 (x6) | 0/2 | 0 |
| silero&echo+guard | 2/2, 334, 0% | stopped 0/2, heard 0/2 | 2/2 / 0/2 | 1/2 | 2/2 (x5) | 1/2 | 0/2 | 0 |

- **energy**: loudness above 4x the room noise for 0.3 s, no allowance for its own voice.
- **+echo**: it learns how loud its own voice comes back and requires you to exceed 1.8x that.
- **+guard**: an "interruption" made of its own words is ignored.
- **silero**: the Silero VAD model main's design uses (Pipecat's file, confidence 0.7, 250 ms; 150 ms in the second row).
- **silero&echo**: Silero says speech AND the level is above the echo allowance.

## Reading

1. **Everything works with no echo.** Talking over it is heard right (0% word error) and it goes
   quiet 150-340 ms after the voice starts, in every configuration.
2. **Its own voice is the whole problem.** From -18 dB of leak upwards every configuration stops
   itself at least sometimes, and at -6 dB plain energy loops 35 times per trial. Silero is worse
   than energy here: its own voice *is* speech, so it loops at every level tested (34-37 times per
   trial); the text guard cuts the loops to 4-6 but not the first false stop.
   Main's design avoids this with a hardware-echo-cancelling speakerphone; a software detector
   cannot tell its voice from yours without that.
3. **Silero's real advantage is non-speech noise.** A 0.3 s burst of noise stopped every energy
   configuration (2/2) and none of the Silero ones (0/2).
4. **Silero needs a shorter start window for a bare "stop".** At 250 ms it never heard it (0/2);
   at 150 ms it did, and it went quiet in 148 ms instead of ~250.
5. **The echo allowance helps a little and does not fix it** (-6 dB: 35 loops -> 1 of 2 trials;
   -18 dB: 1/2 -> 0/2; -12 dB still 2/2). The text guard cuts the loops further.

## Not covered

Silero and the others at -30/-24 dB or with perfect echo cancellation (the headset case); real
coughs and knocks (the burst is white noise); room echo and delay (the leak is a flat gain);
more than one voice; more than 2 repetitions. The "+echo -12" column only looks at the first 2 s of
the reply, which is why Silero shows "by itself" there.
