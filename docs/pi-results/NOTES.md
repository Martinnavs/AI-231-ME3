# Pi test notes

Fill this in while testing (see `docs/PI-TEST-PLAN.md`, section 0). Anything odd matters: humming, a mode switch, a hot Pi, a volume that moved.

## Hardware sheet

```
Date / time (PHT):
Pi model / RAM:
OS + audio server (PipeWire or PulseAudio):
Python version:
Speakerphone make/model:
  firmware / mode switches:
  connection (USB direct / hub):
  how it appears (line from `pi_echo_check.py devices`):
Room (size, surfaces, fans, hum):
Distance speakerphone <-> mouth:
Placement (desk / cushion):
Volume used for A / B (mixer %):
vcgencmd get_throttled before / after:
```

## Conditions run

| # | Label used | selftest folder | doubletalk folder | One-line impression |
|---|---|---|---|---|
| A | | | | |
| B | | | | |
| C | | | | |
| D | | | | |
| E | | | | |

## Observations

(What you heard; whether the reply sounded clipped or ducked; whether the beep was loud; anything the tool did not capture.)

## Double-talk scores (from `pi_double_talk_score.py` on the laptop)

| Condition | Word error | Own voice tripped (before beep) | Your onset after beep |
|---|---|---|---|
| A | | | |
