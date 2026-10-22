# Pi echo test: results (2026-10-22)

What was run from `docs/PI-TEST-PLAN.md` on the Raspberry Pi 4 Model B, what it showed, and what it did not. Raw run folders and the hardware notes are in `docs/pi-results/` (`NOTES.md`); recordings stay in `out/pi/` (git-ignored). One speaker (the owner), one room, one day. Word error was **not** scored (the scorer needs Parakeet, which must not run on the Pi).

## Hardware and what that excluded

The Pi has **no duplex speakerphone**: the speaker is a USB Jieli CD002-AUDIO and the microphone is a separate USB Jieli UACDemoV1.0. The plan's conditions A, B (speakerphone) and D (headset) were therefore impossible. Two conditions were run:

| Condition | Meaning |
|---|---|
| **C** | split routing, no echo cancellation (the plan's "expected to fail" case; here it is simply the hardware) |
| **E** | software echo cancellation: PipeWire WebRTC module (`config/pipewire-echo-cancel.conf`, started by `scripts/aec_pi.sh on`), run as a separate `pipewire -c` client, no root |

## Method

- **selftest**: six standard clips (`tests/fixtures/pi_echo/`) played through the speaker, the owner silent; counts clips on which each detector (loudness, Silero 150 ms, Silero 250 ms) fired on the assistant's own voice.
- **doubletalk**: 10 trials. A two-sentence reply plays; a beep sounds 3 s in; the owner reads a sentence aloud from the beep. The first 6 sentences are the repository's (`prompts.json`); 4 more were added in the same vocabulary. Enter presses were fed automatically (no keyboard on the Pi), one trial every 14 s. Onsets are measured **from the beep**, so they include the owner's reaction time (unmeasured) and Silero's minimum speech time.
- Volumes are the PipeWire sink volume (`wpctl set-volume`), set once before each run.

## Results

### selftest (own voice tripped the detector; clips out of 6, per run)

| Volume | C (no AEC): Silero 150 ms | E (AEC): Silero 150 ms | Loudness |
|---|---|---|---|
| 40% | 0 (1 run) | not run | 0 |
| 60% | 0, 0 (2 runs) | 4, 4, 3, 1 (4 runs) | 0 |
| 75% | 0, 0 (2 runs) | 0, 0 (2 runs) | 0 |
| 90% | 6, 6 (2 runs; echo 4.3 dB median / 12.0 dB worst) | 0, 0, 0 (3 runs) | 0 |

Silero cost on the Pi 4: 1.6-2.0 ms per 32 ms window (real-time needs < 32). Zero buffer underruns in every run; throttled `0x0` throughout. Round trip speaker to microphone: about 100 ms without AEC, about 205 ms with AEC (two E runs read 948 and 974 ms; unexplained).

### doubletalk (10 trials each)

| Run | Own voice tripped a detector | Silero 150 ms heard the user | Silero 250 ms | Loudness |
|---|---|---|---|---|
| C, 60% | 0/10 | 10/10, 1.0-1.3 s after the beep | 10/10 | 0/10 |
| C, 75% | 0/10 | 7/10, 1.1-2.1 s (trials 2, 3, 9 missed though speech was recorded) | 6/10 | 0/10 |
| E, 75% | 0/10 | 10/10, 1.0-2.7 s (median about 2.0 s) | 10/10 | 0/10 |
| E, 90% | 0/10 | 10/10, 1.2-3.4 s (median about 2.3 s) | 10/10 | 0/10 |

An earlier C 75% run was discarded because nobody spoke (mic level did not change after the beep). The owner found 60% playback too quiet for real use; 75% was chosen as the compromise.

## What this shows

1. Without AEC the usable volume is capped: Silero trips on its own voice at 90% (6/6 clips, twice) while the loudness detector stays quiet; at 60-75% it does not.
2. With AEC the own-voice trips disappear at 75% and 90% in 5 selftest runs and 20 doubletalk trials, but at 60% they do not (up to 4/6). The pattern does not follow volume; cause unknown. A possible cause (unverified) is two separate USB clocks feeding one canceller.
3. With AEC the user is always detected but about 1 s later than without it (median 2.0-2.3 s versus 1.0-1.3 s at 60%). Whether the canceller weakens the user's voice or the user started later is unverified.
4. The loudness detector never heard the user in any doubletalk run on this microphone; only Silero is usable here.

## What it does not show

- Word error (does AEC clip the start of the user's sentences?): not scored.
- Detector latency: the onsets above include the owner's reaction time. The time from the user starting to talk to the speaker going quiet was not measured on the Pi.
- Sample size: 10 trials per cell, one speaker; selftest cells are 1-4 runs of 6 clips.
- The plan's thresholds were not met by any setting on all counts at once (loud reply, no self-trigger, fast barge-in).

## Setup notes (all outside the repository)

No sudo was available: `uv` was installed with its user installer; `libportaudio2` and `pipewire-alsa` `.deb` files were unpacked under `~/.local/portaudio` and `~/.local/pwalsa`, and a copy of `/usr/share/alsa/alsa.conf` with two added PCMs (`va_in`, `va_out`) exposes the AEC nodes to PortAudio. Each shell needs `PATH=$HOME/.local/bin:$PATH`, `LD_LIBRARY_PATH=$HOME/.local/portaudio/usr/lib/aarch64-linux-gnu`, and for E `ALSA_CONFIG_PATH=$HOME/.local/pwalsa/asound.conf` with `--in-device va_in --out-device va_out --rate 48000`.

## Live barge-in pilot (same day, later)

The beep-based doubletalk could not separate the owner's reaction time from the detector, so a live pilot was built (`src/doc_chat/pi_pilot.py`, `scripts/pi_live_pilot.py`, replay of gate variants in `scripts/pi_gate_variants.py`; tests in `tests/doc_chat/test_pi_pilot.py`). The assistant plays the standard clips with random gaps; the owner holds Right-Ctrl while talking (the key events are the ground truth, read from `/dev/input` on the kernel's monotonic clock, keyboard grabbed); the gate is main's ticket-12 design: duck -15 dB at the first Silero window >= confidence, interrupt after the speech holds for 250 ms (15 ms fade), restore if it stops first. Onsets are taken from the audio (first window >= 0.5 inside the hold); the key is only a window. Everything is timestamped on one clock; `run.json` records the gate settings.

**Calibration** (2 min, speaker silent): 15 real key presses (105 raw ones: this keyboard chatters, so presses closer than 0.3 s are merged), 11 with measurable speech; the owner starts talking a median 0.53 s after the key (p10 0.22, p90 0.81).

**What went wrong first, so it is not repeated:** (1) short bursts under 1 s and normal-volume speech over the 75% playback almost never reached the gate (1/30 and 0/16 ducked); a loud, clear phrase did. Silero's confidence, not the microphone, was the limit: the proven offline detector gave the same maximum (0.14) on the saved audio. (2) A first dry run found a window-timing bug (a start time of 0.0 treated as missing); (3) the keyboard chatter above.

**Results, E (software AEC), volume 75%, owner saying "Wait, stop, I have a question" loudly while holding the key, one speaker:**

| Gate | Barge-ins over assistant speech | Ducked | Interrupted | Median voice onset to interrupt | False gate events |
|---|---|---|---|---|---|
| Spec: confidence 0.7, 250 ms (live, run 1) | 18 | 16 (89%) | 7 (39%) | 1.41 s | 4 ducks + 1 interrupt, all within the last 3 s of the run |
| Confidence 0.5, 250 ms (live, run 2) | 21 | 20 (95%) | 16 (76%) | 0.38 s (duck 0.13 s) | 0 |

Same-recording replays (live resume rule applied) agree and make the comparison paired: the spec gate on run 2's audio interrupts 14/21 at 0.83 s, on run 1's audio 8/18 at 1.30 s; confidence 0.5 gives 16/21 at 0.26 s and 15/18 at 0.67 s. A "7 of the last 10 windows" rule at 0.5 gives 18/21 and 16/18 (0.22 s and 0.45 s) in replay but was not run live. A shorter 150 ms gate at 0.7 is about as good as lowering the confidence (17/21 and 14/18). Internal processing from detection to the output ramp is about 0.1 s.

**False triggers of the looser gate:** a 5-minute silent soak (assistant alone, AEC, 75%, confidence 0.5): 0 ducks and 0 interrupts in 4.16 min of assistant speech, highest confidence on its own voice 0.10 (one buffer underrun). Replayed over the earlier selftest recordings, the 0.5 variants add no events at 75% with or without AEC; at 90% with AEC they add 1 (0.4/min); at 90% without AEC and at 60% with AEC every variant, including the spec's, fires repeatedly (the self-trigger failures already seen).

**Limits:** one speaker, one room, 18 and 21 events per cell; the two gates were compared in different sessions (the replays are what make the comparison like-for-like); the loud phrase is not natural speech; normal-volume and sub-second speech were not detected at all at this mic gain and volume, so the numbers above describe a loud interruption; the no-AEC loud check had only 5 events; the soak bounds the false-interrupt rate only to roughly 0.7 per minute at 95% confidence; the time from the speaker ramp to actual silence was not measured acoustically.

## Mic gain check (offline, same day)

Question: is quiet speech missed because the signal is small? The mic's hardware gain is already at its maximum (ALSA `Mic` 147/147, range -28.4 to -0.94 dB; Auto Gain Control off), so the check added digital gain (+0, +6, +12, +18 dB) to the recorded `mic.wav` of live runs and re-ran Silero.

| Run | Gain | Speech windows >= 0.5 / >= 0.7 | Assistant-only windows >= 0.5 | Interrupts at 0.5 gate (hit / false) |
|---|---|---|---|---|
| E, confidence 0.5 (21 barge-ins) | +0 / +18 dB | 33% / 26% -> 30% / 23% | 0% | 16/21 / 0 -> 16/21 / 0 |
| E, spec 0.7 (18 barge-ins) | +0 / +6 dB | 29% / 20% -> 27% / 18% | 2% | 15/18 / 1 -> 12/18 / 1 |
| C loud check (5 barge-ins) | +0 / +12 dB | 26% / 12% -> 44% / 35% | 0% -> 2% | 5/5 / 0 -> 5/5 / 0 |
| C short phrases (13 barge-ins) | +0 / +12 dB | 0% -> 87% | 0% -> **74%** | 0/13 / 0 -> 13/13 / **75** |

Reading: with AEC, more gain does nothing (confidence on speech drifts down slightly). Without AEC, gain lifts the assistant's own echo to the same level as speech, so the apparent detections at +12 dB are echo (75 false interrupts). The AEC does not visibly suppress the owner's voice (loud check: 26% without AEC, 29-33% with). The limit is Silero's confidence on this mic and room, not level. Remaining levers are physical (mic placement or a different mic). Caveat: the no-AEC loud check has only 5 events.

## Bottom line across all experiments

Hardware: speaker (CD002) and mic (UACDemo) are separate USB devices, so conditions A/B (speakerphone) and D (headset) could not be run; only C (split routing, no AEC) and E (software AEC) were.

1. **Self-trigger (assistant's voice tripping the detector).** Volume decides it. At 90% C trips Silero on all 6 clips; at 60% and 75% C is clean. E is clean at 75% and 90% in most runs but unstable at 60%. 75% is the working point; the owner found 60% too quiet.
2. **Does AEC help?** Only at high volume (90%), where it removes the self-trigger. At 75% it is not needed, and it does not visibly harm the owner's speech. Word error under AEC was never scored (needs the laptop scorer).
3. **Beep-based double-talk (10 sentences per cell).** Could not separate reaction time from detector latency, so it was replaced by the live pilot.
4. **Live pilot, E at 75%, loud phrase.** The spec gate (0.7, 250 ms) ducks 89% but interrupts only 39%, median 1.41 s after voice onset. Confidence 0.5 interrupts 76% at 0.38 s (duck 0.13 s) with 0 false events and a clean 5-minute soak (0 events, peak confidence 0.10). Paired replays agree.
5. **Quiet speech.** Normal-volume and sub-second speech over 75% playback is barely detected, and neither extra gain nor the AEC changes that.

**Verdict.** Interruption works for a loud, clear interjection with the 0.5 gate at 75% volume: the speaker ducks in about 0.13 s and stops in about 0.4 s, without false triggers in the conditions tested. It does not work for normal-volume or very short speech. Do not read this as meeting the plan's thresholds: that would need natural-volume detection, word-error scoring, an acoustic stop-time measurement and more than one speaker. Recommended settings: 75% volume, confidence 0.5 (optionally the 7-of-10 rule, tested only in replay), AEC on if volume goes up. Next steps: mic placement or a different mic; a quiet-utterance ("stop") test; the laptop scorer for the doubletalk folders.

## Silent-room control and stated limitation (same day, last)

**Control.** The speaker was silent, no AEC, the owner seated normally, 12 key holds with a known script (`out/pi/20261022-132235-pilot-calibrate-calib-script`, analysed with a scratch script, not in the repo). One hold was a mistake, so the groups below are assigned by hold length (about 1.2-1.5 s versus 2 s or more), not by a record of what was said; the short ones are taken to be "Stop".

| Group | Holds | Silero peak confidence | Longest run >= 0.7 | Spec gate (0.7, 250 ms) | Confidence 0.5, 250 ms |
|---|---|---|---|---|---|
| Single word, "Stop" | 4 | 0.08-0.18 | 0 s | 0 of 4 | 0 of 4 |
| Sentence, normal voice | 5 | 0.79-0.91 | 0.16-0.38 s | 4 of 5 | 5 of 5 |
| Sentence, raised voice | 3 | 0.83-0.91 | 0.35-0.61 s | 3 of 3 | 3 of 3 |

The room outside the holds peaked at 0.08. Key-to-voice offset: median 0.77 s (8 measurable holds).

**Correction to the earlier reading.** An earlier calibration (content not recorded) suggested the playback and AEC cost almost nothing. With a known script, sentences are detected 8 of 8 in a silent room, against 16 of 21 (76%) at the same gate over the 75% playback, so the playback and AEC do cost some detections (small samples: 8 and 21).

**Limitation, stated as scope.** A barge-in is an utterance, not a single word. The gate needs at least 250 ms of continuous speech above the confidence (a duck can happen sooner, at the first window), and in this setup Silero did not respond to a one-word "Stop" at all, even in a silent room. The system therefore supports interruptions that are a phrase or sentence of about a second or more. The exact shortest utterance that works was not measured (we have one-word and full-sentence points only). One-word interruptions would need a different detector or a dedicated keyword model; that was not pursued. Industry practice was not researched in this session; the repository's earlier research digest on echo and barge-in handling is the place to start.

**Update (laptop test, same day).** The statement above that Silero "did not respond to a one-word Stop at all" holds for the Pi's recordings, not for the model: on 300 clean Speech Commands "stop" clips its median peak confidence is 0.94 (`SILERO-SPEECH-COMMANDS.md`). The low Pi confidence therefore points at the Pi's capture path, the room, or what was said, and the 250 ms hold also costs short words. Next steps are in `RESEARCH-HANDOFF-PI.md`.

## Why the signal is weak, and what close-range speech gives (2026-10-22, evening)

**The USB microphone is weak, and nothing in the software path is losing level.** The owner's earlier good audio (`STT-HOSTING.md`, 94-turn tryout: floor -36 dBFS, speech median -21 dBFS, about 14 dB signal to noise) came from the laptop's built-in microphone, not this USB mic, so the two are not comparable.

| Check | Result |
|---|---|
| Room floor, our pipeline / raw `arecord` | -44.5 dBFS / -47.0 dBFS (same) |
| Speech in the scripted calibration (speaker silent, owner at about 0.25-0.5 m) | loudest 100 ms only 2-10 dB above the floor; whole-hold rms -41 to -47 dBFS |
| Raw `arecord` speech take | peaks -25.7 dBFS, loudest blocks -36 to -42 dBFS; Silero sees it only in pieces (most stretches at confidence >= 0.5 last 0.03-0.32 s, two reach 0.86 s and 1.09 s) |
| Mic hardware gain | already at its maximum (-0.94 dB); `Auto Gain Control` was off |
| Mic AGC switched on (rerun of the proxy clips and the scripted calibration) | no change: floor -44.6 dBFS, hold levels the same, sentence-like holds passed the spec gate 6 of 13 and the 0.5 gate 11 of 13 (AGC off: 7 of 8 and 8 of 8, but the distance may have differed); "Stop"-length holds peaked 0.00-0.11 |
| One-word clips (`tests/fixtures/pi_stop`, 20 clips) played at 75% through speaker and mic | median peak confidence 0.04 (repeat run 0.083; AGC on 0.065), against 0.94 for the same kind of clean clips on the laptop (`SILERO-SPEECH-COMMANDS.md`); the source clips are as loud as the standard clips (peak -6.8 against -6.0 dBFS) |
| Digital gain added to recordings | no help with AEC; without AEC it raises the echo to false interrupts (see the mic gain check above) |

Reading: the mic gives speech only 5-11 dB above its own noise. Silero is fine on clean audio and loses speech at that margin. The loss is the microphone and its signal to noise, not Silero, the AEC, the PipeWire path or the gate. `arecord` through ALSA and our pipeline measure the same.

Process notes: the USB mic's ALSA card number changed from 4 to 3 during the session (address it by name, `plughw:CARD=UACDemoV10,DEV=0`; `amixer -c UACDemoV10`). One AGC take recorded no key presses because the pilot's default keyboard (first `*-event-kbd` in `/dev/input/by-id`, alphabetical) became a USB mouse's keyboard interface after the mouse was plugged in; pass `--kbd /dev/input/by-id/usb-SINO_WEALTH_RK_Bluetooth_Keyboar-event-kbd`. The AGC-on take had 21 holds, not 12, so its holds could not be matched to the script by order.

**Close-range speech works for sentences.** Silent speaker, no AEC, AGC off, owner at 5-10 cm (owner's estimate), scripted holds in the original order (11 holds were recorded, one raised-voice hold short; groups are by script order):

| Group | Holds | Loudest 100 ms | Silero peak | Longest run >= 0.7 | Spec gate (0.7, 250 ms) | 0.5 gate (250 ms) |
|---|---|---|---|---|---|---|
| Sentence, normal voice | 4 | -26.5 to -33.7 dBFS | 0.84-0.90 | 0.35-0.54 s | 4 of 4 | 4 of 4 |
| "Stop" | 4 | -22.7 to -35.2 dBFS | 0.26-0.65 | 0 s | 0 of 4 | 0 of 4 |
| Sentence, raised voice | 3 | -22.6 to -23.5 dBFS | 0.88-0.97 | 0.45-0.58 s | 3 of 3 | 3 of 3 |

Speech was 8-13 dB louder than at the farther distance and the floor was unchanged (about -46 dBFS). A single "Stop" is no longer invisible (peaks 0.26-0.65, against 0.00-0.18 before) but stays above 0.5 for only 0.06-0.13 s, so a 250 ms hold still misses it, as the laptop test predicted.

**Live pilot, close range** (C, no AEC, 75% volume, confidence 0.5 with the 250 ms hold, owner at 5-10 cm, alternating "Wait, stop, I have a question" and "Stop."; 4 minutes, `out/pi/20261022-205354-pilot-live-c-vol75-close-5-10cm-conf0-5`, not committed):

| Barge-ins over assistant speech | Ducked | Interrupted | Median s from voice onset to interrupt | Duck latency | False ducks or interrupts |
|---|---|---|---|---|---|
| 31 (plus 3 holds in assistant pauses) | 27 (87%) | 18 (58%) | 0.67 s (p90 1.03 s) | 0.12 s median | 0 in 2.78 min of assistant speech; a noise press triggered nothing |

Split at the median hold length (the holds were not clearly two groups, so this is a guess that the short ones are "Stop"): longer holds 12 of 15 interrupted (80%, 0.74 s), shorter holds 6 of 16 (38%, 0.59 s). Replaying other gates on the same recording (31 barge-ins, 0 false in the run): spec 0.7/250 ms 15 (0.83 s); 0.5/250 ms 18 (0.61 s); allow-1-dip @0.5 18 (0.37 s); leaky @0.5 19 (0.38 s); 7 of last 10 @0.5 21 (68%, 0.26 s, not run live). Over the earlier selftest recordings at 75% every variant has 0 false events per minute, with or without AEC.

## Updated bottom line (after the microphone findings)

- The limit is the microphone: the USB mic delivers speech only a few dB above its noise. It is not the model, the AEC, the PipeWire path or the gate.
- The workaround that works is speaking close to the Pi (5-10 cm): sentences are reliably heard and interrupt about 80% of the time within a second, with no false triggers at 75% volume and the 0.5 gate.
- A one-word "Stop" is the weak case (about 38% interrupted, and 0 of 4 in the silent-room control at the 250 ms hold). A shorter hold or the 7-of-10 rule recovers some in replay; neither was run live and the false-trigger cost against echo at other volumes is not established.
- Not shown: word error under AEC, the acoustic time to silence, more than one speaker, and the mic at normal conversational distance. Samples are small (31 barge-ins, one speaker, one room).
