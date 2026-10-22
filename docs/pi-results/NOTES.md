# Pi test notes

Fill this in while testing (see `docs/PI-TEST-PLAN.md`, section 0). Anything odd matters: humming, a mode switch, a hot Pi, a volume that moved.

## Hardware sheet

```
Date / time (PHT):                    2026-10-22 ~08:45-09:05 (clock as reported by the Pi)
Pi model / RAM:                       Raspberry Pi 4 Model B Rev 1.2 / RAM not recorded
OS + audio server (PipeWire or PulseAudio): Debian 13 trixie, PipeWire 1.4.2 (+ pipewire-pulse)
Python version:                       system 3.13.5; project venv 3.12.15 (uv)
Speakerphone make/model:              NONE. Two separate USB devices: speaker CD002-AUDIO (Jieli CD002), mic UACDemoV1.0 (Jieli UACDemo). Conditions A/B not possible.
  firmware / mode switches:
  connection (USB direct / hub):      not recorded
  how it appears (line from `pi_echo_check.py devices`): 1 CD002-AUDIO: USB Audio (hw:3,0) 0 in/2 out; 2 UACDemoV1.0: USB Audio (hw:4,0) 1 in/0 out
Room (size, surfaces, fans, hum):
Distance speakerphone <-> mouth:
Placement (desk / cushion):
Volume used for A / B (mixer %):      n/a; C and E run at 40, 60 and 90 (wpctl sink volume)
vcgencmd get_throttled before / after: 0x0 / 0x0 (recorded per run)
```

## Conditions run

| # | Label used | selftest folder | doubletalk folder | One-line impression |
|---|---|---|---|---|
| A | | | | |
| B | | | | |
| C | C split routing (vol 40/60/90) | 084458, 084745, 084857, 090231 (+ vol60 rep) | not run | Clean at vol60 (0/6 Silero); at vol90 Silero trips 6/6 twice, loudness 0/6 |
| D | | | | |
| E | E software aec (vol 60/90, PipeWire WebRTC via pipewire-alsa PCMs) | 085047, 085201 and reps | not run | vol90 0/6 (3 runs, but 948 ms round trip in the first); vol60 Silero 4/6, 4/6, 3/6, 1/6 |

## Observations

(What you heard; whether the reply sounded clipped or ducked; whether the beep was loud; anything the tool did not capture.)

## Double-talk scores (from `pi_double_talk_score.py` on the laptop)

| Condition | Word error | Own voice tripped (before beep) | Your onset after beep |
|---|---|---|---|
| A | | | |

## Observations (added by the assistant, not by a human listener)

- Nobody listened to the playback; levels are from the tool only. Room floor with AEC off was about -47 to -49 dBFS; with AEC on it read -57 to -85 dBFS (the canceller/noise suppression gates the mic), so "above room" is not comparable between C and E.
- C at vol60: 0/6 on all detectors, twice. C at vol90: Silero 150/250 ms tripped 6/6 clips both times, loudness 0/6 (matches the decision-guide row "Silero trips but energy is quiet").
- E at vol60 (stable volume, warm-up played first): Silero tripped 4/6, 3/6, 1/6 over three runs, i.e. worse than C at the same volume. E at vol90: 0/6 over three runs. Not monotonic in volume; unexplained. The worst block was 17-27 dB in most E runs (first-play leak). Possible cause (unverified): speaker and mic are two USB clocks, which a WebRTC canceller handles poorly.
- Each cell rests on 1 run of 6 clips (E vol60: 4 runs, E vol90: 3, C vol90: 2, C vol60: 2). The first E vol60 run came right after a volume change.
- Doubletalk was not run (needs a human speaking). Word error under AEC is still unmeasured.
- Sudoless setup: libportaudio2 and pipewire-alsa .deb files were unpacked under ~/.local; a local ALSA config exposes PCMs va_in/va_out for va_aec_source/va_aec_sink.

## Double talk, 10 sentences each (added by the assistant; word error NOT scored yet)

- User feedback (owner): playback at 60% volume is too quiet for real use. The practical level is therefore 90%, where C (no AEC) self-triggers Silero 6/6 clips.
- C vol60 (`20261022-091350-doubletalk-c-split-routing-vol60-10-sentences`): own voice tripped 0/10 (all detectors); Silero heard the user 10/10 at 1.0-1.3 s (150 ms) after the beep; the loudness detector heard the user 0/10.
- E vol90 (`20261022-091839-doubletalk-e-software-aec-vol90-10-sentences`): own voice tripped 0/10; Silero heard the user 10/10 but later, 1.2-3.4 s (150 ms; median about 2.3 s); loudness 0/10. AEC at 90% removes the self-trigger but delays or weakens detection of the user's overlapping voice. Cause unverified (the canceller may attenuate the user's voice during overlap; word error would show it).
- One speaker, one session each, 10 trials. Not yet scored with pi_double_talk_score.py (laptop-only, Parakeet).

### 75% ablation (added by the assistant; word error NOT scored yet)

- Selftest at vol75, 2 runs each of 6 clips: C 0/6 and 0/6 (Silero and loudness), echo 0.6 dB median / 3.5-3.6 dB worst; E 0/6 and 0/6 (round trip 974 ms in one run, 206 ms in the other).
- C vol75 doubletalk (`20261022-093401-doubletalk-c-split-routing-vol75-10-sentences`): own voice tripped 0/10. Silero 150 ms heard the user in 7/10 trials (1.1-2.1 s after the beep), Silero 250 ms in 6/10; trials 2, 3 and 9 missed by both although the mic level rose 3-4 dB after the beep (speech was recorded). Loudness 0/10. A first attempt at 75% was discarded: nobody spoke (mic level did not change after the beep).
- Compared with C vol60 (10/10 detected at 1.0-1.3 s), 75% trades some barge-in sensitivity (louder reply masks the user) for a louder, more usable reply. One speaker, 10 trials.
- E vol75 doubletalk (`20261022-093838-doubletalk-e-software-aec-vol75-10-sentences`): own voice tripped 0/10; Silero 150 ms heard the user 10/10 (1.0-2.7 s after the beep, median about 2.0 s), 250 ms 10/10; loudness 0/10. Mic level before the beep was -80 to -93 dBFS (AEC gates the mic) and -48 to -54 after, so speech was recorded. One speaker, 10 trials, word error not scored.
