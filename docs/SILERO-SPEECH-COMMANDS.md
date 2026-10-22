# Does Silero respond to a clean one-word "stop"? (laptop test, 2026-10-22)

**Bottom line.** Yes. On clean Speech Commands v2 clips Silero's median peak confidence on "stop" is 0.94, and 98-100% of clips peak above 0.2. So the Pi result (silent-room "Stop" peaking at 0.08-0.18, 0 of 4 detected) is **not a limit of Silero on single words**; look at the Pi's microphone path, the room, or what was actually said. Separately, the **250 ms hold costs detections on short words**: clean "stop" clears 0.5 for only about 0.1-0.2 s.

This corrects the sentence in `PI-RESULTS.md` that said Silero "did not respond to a one-word Stop at all". That was true on the Pi's recordings; it is not true of the model.

## What was run

`scripts/silero_speech_commands.py` (reproducible: `fetch` then `run`). Per word, 300 random clips from Google Speech Commands v2 (Warden, arXiv:1804.03209, CC BY 4.0), fed to the repository's own `SileroOnset` (512-sample windows, 64-sample context, state carried across windows, a fresh detector per clip, clips padded to 1 s). "Fires" means that gate would trigger an onset. The `-20 dB` rows scale the clip down by 0.1. The noise row uses 396 one-second slices of the dataset's background-noise files.

| Set | n | Peak confidence, median | Peak < 0.2 | 0.7 / 250 ms (current spec) | 0.5 / 250 ms | 0.5 / 100 ms |
|---|---|---|---|---|---|---|
| "stop" | 300 | 0.94 | 0% | 36% | 58% | **95%** |
| "stop", -20 dB | 300 | 0.93 | 2% | 23% | 50% | 92% |
| "no" | 300 | 0.94 | 2% | 88% | 93% | 97% |
| "no", -20 dB | 300 | 0.92 | 3% | 74% | 86% | 93% |
| "go" | 300 | 0.92 | 1% | 77% | 89% | 97% |
| "go", -20 dB | 300 | 0.91 | 2% | 66% | 82% | 92% |
| "yes" | 300 | 0.96 | 0% | 89% | 92% | 98% |
| "yes", -20 dB | 300 | 0.96 | 1% | 86% | 91% | 96% |
| "up" | 300 | 0.86 | 2% | 6% | 22% | 87% |
| "up", -20 dB | 300 | 0.83 | 4% | 3% | 17% | 78% |
| background noise | 396 | 0.10 | - | 0% | 0% | 0% |

## Reading it

1. **The model hears the word.** Peaks of 0.9+ on every word, and a 20 dB quieter clip barely moves them. A 0.18 peak on the Pi is therefore a property of what the Pi captured (microphone path, room, gain, distance, the word said), not of Silero.
2. **Hold time matters for short words.** With the 0.7 spec gate only 36% of clean "stop" clips (6% of "up") would interrupt; 0.5 with a 100 ms hold fires on 87-98% of all five words. Words with more voiced time ("no", "yes", "go") fare better at 250 ms.
3. **No false fires on noise at any gate**, including 100 ms. This is background noise only; it says nothing about the assistant's own echo, where a shorter hold is untested.

## What this does not show

- These are clean, near-field, crowd-sourced clips, not our microphone, room or playback. It shows what the model can do, not what the Pi delivers.
- Peak confidence is one pass per clip; the gate columns are fresh-state onsets on a 1 s clip, not a stream with the assistant talking.
- It does not say why the Pi is low. The four Pi "Stop" holds were identified by hold length, not by a record of what was said (`PI-RESULTS.md`, silent-room control), so even "it was a one-word Stop" is unconfirmed.
- Nothing about echo, AEC, or double-talk.

## Next steps (see `RESEARCH-HANDOFF-PI.md`)

Play these same kinds of clips through the Pi's own speaker and microphone (`tests/fixtures/pi_stop/`, via `pi_echo_check.py selftest --clips ...`) and compare the per-clip max confidence with the table above. A big drop localises the loss to the Pi's chain.
