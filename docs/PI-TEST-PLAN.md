# Pi test plan: does the assistant's own voice trip the detector on the real hardware?

**Who runs this:** you, on the Raspberry Pi with the USB speakerphone (the final hardware).
**Who reads the results:** whoever picks this up next (a person or an AI assistant), starting from `docs/HANDOFF.md`.
**Time:** about 20 minutes for the first condition, 10 for each further one.
**No speech server, no internet, no model on the Pi except the Silero VAD file** (the professor's rule holds: the test tool runs nothing else).

## Why this test exists

On the laptop we measured (`docs/BARGE-IN.md`, `docs/INTERRUPTS.md`):

- without echo cancellation the assistant's own voice trips the voice detector in **91-92% of blocks**, and the Silero VAD is the *worst* detector for it (the assistant's voice is speech);
- with software echo cancellation (PipeWire WebRTC) it dropped to **0%**, except on the very first play (leak about 11 dB above the room);
- in a simulation, every software detector failed once the leak reached -18 dB or louder.

Everything the industry does about this is *outside* the detector: echo cancellation in hardware or at the endpoint, a headset in development, ducking, a minimum speech time, a guard period (`docs/ECHO-RESEARCH.md`). Main's design bets on a **USB speakerphone with hardware echo cancellation** (`.scratch/project2-voice-agent/DESIGN.md`, decision "Hardware AEC"). This test checks that bet on the actual device, and measures what the Silero VAD does with whatever echo is left.

## What you will produce

For each condition below, two runs:

| Run | Command | What it measures | Needs you to speak? |
|---|---|---|---|
| `selftest` | `pi_echo_check.py selftest` | how loud the assistant's own voice is at the microphone (against the empty room), whether the detectors trip on it, the speaker-to-microphone delay, buffer underruns, and the Silero cost per 32 ms on the Pi | **No: stay silent** (about 2 minutes) |
| `doubletalk` | `pi_echo_check.py doubletalk` | the same, while you read a sentence over the playing reply at a beep; the recording is later scored for what you said | Yes (about 4 minutes) |

Results land in `out/pi/<time>-<kind>-<label>/` (`run.md` is readable, `run.json` is for tools, `*.wav` are the recordings).

## 0. Fill in the hardware sheet

Fill in the hardware sheet in `docs/pi-results/NOTES.md` (a template is there; the same fields are listed below). It makes the numbers interpretable later.

```
Pi model / RAM:                       (cat /proc/device-tree/model)
OS + audio server:                    (cat /etc/os-release; pactl info | grep "Server Name"  -> PipeWire or PulseAudio?)
Python version:                       (python3 --version)
Speakerphone make/model:
  firmware / mode switches:           (some have a "speaker/headset" or "echo cancel" switch)
  connection:                         USB direct / via hub
  how it appears:                     (python scripts/pi_echo_check.py devices; paste the line)
Room:                                 size, hard/soft surfaces, fans, anything humming
Distance speakerphone <-> your mouth:
Speakerphone placement:               (on a desk? on a cushion?)
Volume setting used (mixer %):
```

## 1. Set up the Pi (once)

```bash
# system packages (needs sudo; do it yourself, nothing here runs it for you)
sudo apt update
sudo apt install -y git libportaudio2 alsa-utils python3-venv curl

# uv (the project's package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh        # then open a new shell

# the code
git clone https://github.com/Martinnavs/AI-231-ME3.git && cd AI-231-ME3
git checkout agy/interrupts                            # see docs/BRANCHES.md if this moved

# only what the test needs: base dependencies + onnxruntime + sounddevice
uv sync --group pi --no-group dev
uv run python -c "import onnxruntime, sounddevice; print(onnxruntime.__version__, sounddevice.__version__)"
```

The Silero model (`assets/silero_vad.onnx`) and the six standard clips (`tests/fixtures/pi_echo/`) are in the repository; nothing else is downloaded. If `uv sync` cannot build a package on the Pi, stop and note which one (that is itself a useful result).

Check that the speakerphone is visible and pick it by a piece of its name:

```bash
uv run python scripts/pi_echo_check.py devices
arecord -l ; aplay -l                                   # the same, ALSA's view
```

You want **one** device that has both input and output channels (the speakerphone). Use `--device "<part of its name>"` for both microphone and speaker. **Never** play through one device and record through another for the main test: the speakerphone's echo canceller only sees the audio it plays itself (see the split-routing condition below).

## 2. Before every run

- Quiet room, door closed, no music, no video playing, phone on silent. Stay silent during `selftest`.
- Set the volume once and **do not touch it** during a run. Use the mixer (`alsamixer` or `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.6`) and write the setting down. A volume change mid-run breaks an echo canceller for a second or two.
- Make sure the speakerphone is not muted and no "speaker only" mode is selected.
- Let it warm up: play any sound through it for 10 seconds first (some devices' cancellers start cold).
- Plugged in, no throttling: `vcgencmd get_throttled` should say `0x0` before and after (the tool records it).

## 3. The conditions (do them in this order; skip any you cannot)

| # | Condition | Label to use | What it tells us |
|---|---|---|---|
| A | Speakerphone as **both** microphone and speaker, volume about 60% | `A speakerphone vol60` | the main bet: hardware echo cancellation |
| B | Same, volume about 90% | `B speakerphone vol90` | headroom: does it still hold when loud |
| C | **Split routing:** speaker = something else (3.5 mm jack, HDMI, other speaker), microphone = the speakerphone | `C split routing` | expected to FAIL; it documents the trap (the speakerphone never sees the reference signal) |
| D | A headset (if you have one) | `D headset` | the floor: echo should be negligible |
| E | Only if A fails: software cancellation (PipeWire echo-cancel, `scripts/aec.sh on`; see `docs/BARGE-IN.md`) with the speakerphone or the Pi's own speaker | `E software aec` | the fallback |

For each condition run, in this order:

```bash
# 1) silent self-trigger check (about 2 minutes) - STAY QUIET
uv run python scripts/pi_echo_check.py selftest --label "A speakerphone vol60" --device "<name>"

# 2) double talk (about 4 minutes) - you read a sentence at each beep
uv run python scripts/pi_echo_check.py doubletalk --label "A speakerphone vol60" --device "<name>"
```

Condition C needs the two devices separately:

```bash
uv run python scripts/pi_echo_check.py selftest --label "C split routing" --in-device "<speakerphone>" --out-device "<other speaker>"
```

If the device refuses the sample rate, add `--rate 48000` (or `44100`, `16000`). If a run fails half way, rerun it; runs are independent.

### What happens in `doubletalk`

For each of 6 trials the tool prints a sentence. You press Enter; the assistant's reply (two random sentences) starts after one second; **3 seconds into the reply a short beep sounds, and you read the printed sentence aloud at normal volume starting at the beep**, as you would to interrupt. The tool counts how often the detectors fired *before* the beep (the assistant's own voice, a false trigger) and *after* it (you, a true interruption), and saves the recording for scoring.

## 4. Reading `run.md` (pass / fail)

Numbers to compare, per condition. Thresholds are starting points chosen from the laptop measurements and the echo research, not standards; judge with the whole table.

| Quantity | Good | Worrying | Meaning |
|---|---|---|---|
| its own voice above the room, median (dB) | below 3 | above 6 | echo level; near 0 means the canceller hides it below the room |
| ... worst block / first second (dB) | below 8 | above 12 | the first play before the canceller has adapted |
| `silero_150ms` and `silero_250ms`: clips where its own voice tripped it | 0/6 | any | the number that decides the design |
| `energy` (loudness) tripped | 0/6 | any | cruder detector for comparison |
| first clip tripped, later clips not | -- | yes | a start-of-playback grace period is needed (LiveKit-style, 0.25-3 s: measure, do not guess) |
| round trip speaker-to-microphone (ms) | 20-150 | none or above 300 | tells whether the reference path is working; `None` means no sound reached the microphone (wrong device or muted) |
| buffer underruns | 0 | any | the Pi could not feed the speaker in time (CPU, USB) |
| Silero ms per 32 ms window | below 5 | above 20 | the Pi can run Silero in real time (needs below 32) |

Double talk: "its own voice tripped" before the beep should be 0 for Silero; "you, seconds after the beep" should be about 0.4-1.0 s (your reaction plus the detector). Then score what you said:

```bash
# copy the run folder to the laptop, then there:
uv run --group laptop python scripts/pi_double_talk_score.py out/pi/<double-talk-folder>
```

That decodes your speech from the recording with Parakeet and reports the word error against the sentences. Compare it with a quiet-room baseline (about 0-5% on these simple sentences). A canceller that suppresses your voice during overlap shows up as a high word error here, which a self-trigger table alone cannot show.

## 5. What each outcome means (decision guide)

| Result | Meaning | Do next |
|---|---|---|
| A passes (0/6, echo below ~3 dB, no first-clip trip) | the hardware bet holds | build the barge-in gate on the Pi as ticket 12 specifies: Silero at 150-250 ms, duck first, stop after the minimum speech, word confirmation; delete the laptop-only echo-learning code (`EchoModel`); see HANDOFF "Next actions" |
| A passes except the first clip | convergence gap | add a start-of-playback guard (measure the time in `onsets_after_playback_start_s`) |
| A trips Silero but the energy detector is quiet | Silero is sensitive to faint echo below the room noise | gate Silero with a level above the room (`--vad both` in `doc_chat`, or the same idea in the Pi pipeline) |
| A fails, B fails more | volume sensitivity | lower the playback gain, or a better speakerphone |
| A fails, E passes | the speakerphone's own cancellation is not working as the datasheet says | check the mode switch and firmware; use software cancellation (accept its cost: it lowered the laptop's room floor 12-17 dB, accuracy not yet tested) |
| everything fails except D | echo is a hardware problem here | headset for the demo; consider another speakerphone (`docs/ECHO-RESEARCH.md` lists candidates; prices there are unverified) |
| C passes | the speakerphone cancels audio it did not play (unexpected) | note the exact device; investigate before relying on it |
| `round trip: None` in A | no sound path | wrong device, muted, or output going elsewhere |

## 6. Send the results back

The recordings are large and git-ignored (`out/`). Commit only the small summaries:

```bash
scripts/pi_pack_results.sh            # copies out/pi/*/run.json + run.md into docs/pi-results/
git add docs/pi-results && git commit -m "pi: echo test results" && git push
```

Also write `docs/pi-results/NOTES.md`: the filled-in hardware sheet, anything odd you noticed (humming, a mode switch, the volume), and for `doubletalk` copy the recordings to the laptop with `scp -r pi@<ip>:AI-231-ME3/out/pi/<folder> out/pi/` and run the scorer there.

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `no input device matching ...` | name fragment wrong | `python scripts/pi_echo_check.py devices`, use the index number |
| `Invalid sample rate` / `Unanticipated host error` | device does not do the default rate | `--rate 48000` (or 44100/16000) |
| ALSA `underrun occurred` lines | CPU/USB contention | close other programs; check `vcgencmd get_throttled`; note the underrun count |
| `round trip: None` | microphone hears nothing of the speaker | speaker muted or volume zero; wrong device; the speakerphone's own mute button |
| everything "tripped" in a silent room | the room is not quiet (fan, TV, neighbours) or the speakerphone has automatic gain turned up | measure the room alone: the `empty-room level` line; fix the room first |
| `onnxruntime` will not install | Python version or wheel | note the Python version and error; try `uv python install 3.12` and `uv sync --python 3.12 --group pi --no-group dev` |
| `ModuleNotFoundError: sounddevice`/`PortAudio library not found` | missing system library | `sudo apt install libportaudio2` |

## 8. Optional: the real thing, from the Pi

If the Pi can reach the laptop's speech server (same hotspot), the full interruption loop can be tried from the Pi with the model-free responder. **Untested on the Pi** (the `laptop` dependency group pulls in packages that may not have aarch64 wheels):

```bash
# laptop: uv run --group laptop python -m lan_speech --stt-model parakeet --host 0.0.0.0 --port 8765 ...   (needs a token for non-loopback: see docs/LAPTOP-SPEECH.md)
# Pi:     uv sync --group laptop && uv run python -m doc_chat --mic --play --server ws://<laptop-ip>:8765 --no-docs --llm echo --stream --echo-random 6 --vad silero --barge-ms 150 --record out/sessions/pi-1
```

Then `scripts/session_report.py out/sessions/pi-1` (the interruptions section). This is the closest thing to the final system; only try it after A-D are done.
