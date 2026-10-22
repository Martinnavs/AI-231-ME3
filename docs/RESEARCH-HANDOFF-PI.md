# Research and ideation handoff for the Pi agent (2026-10-22)

**Read this first if you are the agent working on the Raspberry Pi.** It says what we have learned since the Pi echo test (`PI-RESULTS.md`), which leads are closed, which are open, and the next experiments in order. Facts first, then guesses; guesses are marked `[HYPOTHESIS]`.

## Where things stand

The gate works for a **loud sentence** at 75% volume with confidence 0.5 (interrupts 76% at 0.38 s, no false events in a 5-minute soak). It fails for **normal-volume speech and one-word interruptions** ("stop"). Research this session narrowed *why*:

1. **Silero is not the limit on a one-word "stop".** On 300 clean Speech Commands clips its median peak is 0.94 (`SILERO-SPEECH-COMMANDS.md`). The Pi's silent-room "Stop" peaked at 0.08-0.18. So the loss is in what the Pi captures (mic path, room, gain, distance, or what was said), not in the model. **This is the top open question.**
2. **The 250 ms hold costs short words.** Clean "stop" clears 0.5 for only about 0.1-0.2 s: 0.7/250 ms fires on 36% of clean clips, 0.5/250 ms on 58%, 0.5/100 ms on 95%. A shorter hold helps in principle; its false-trigger cost with the assistant's echo is **untested**.
3. **Software AEC is unexplained, not solved.** Own-voice trips at 60% volume (up to 4/6 clips), round-trip jumps (205 ms vs about 950 ms), and a user-onset delay of about 1 s with AEC have no verified cause.

## Closed leads (do not redo)

| Idea | Why it is closed |
|---|---|
| "Silero loses its state between windows" (agy v2) | `SileroOnset` (`src/doc_chat/bargein.py`) already carries the recurrent state and 64-sample context across every window. |
| Normalise or add digital gain to the mic | Tested: no gain with AEC; without AEC it amplified the echo into 75 false interrupts (`PI-RESULTS.md`, mic gain check). |
| Turn AGC off (`webrtc.gain_control = false`) | Already the default in PipeWire master (`gain_control` default false). Our config sets no keys. |
| Raise the confidence threshold while the assistant speaks (agy v2 Experiment 3) | The 0.7 spec gate interrupted 39% vs 76% for 0.5. Wrong direction. |
| Replace Silero with WebRTC GMM VAD | Picovoice's benchmark (vendor, conditions unstated) has Silero 87.7% vs WebRTC 50% true positives at 5% false positives. No data on short words, none favouring WebRTC. |
| Buy or reposition hardware | The owner fixed the hardware. Software, configuration, algorithms only. |

## What is verified, and where

I opened each page myself. "Vendor" means a company that sells a competing product.

| Fact | Source | Note |
|---|---|---|
| PipeWire's aec-webrtc source reads `webrtc.gain_control` (default false), `noise_suppression` (true), `high_pass_filter` (true), `transient_suppression` (true), `voice_detection` (true), `mobile_mode` (false), and, in a legacy branch, `extended_filter` and `delay_agnostic` (both true) | https://github.com/PipeWire/pipewire/blob/master/spa/plugins/aec/aec-webrtc.cpp | master only. The plugin on the laptop (PipeWire 1.6.8, webrtc-audio-processing-1) has the six new keys and **not** `extended_filter` / `delay_agnostic`. Which set the Pi's 1.4.2 build has is **unchecked**. |
| Full-Duplex-Bench defines Takeover Rate and "Latency After Interruption"; no "stop latency" | https://arxiv.org/html/2503.04721v2 | Measures time to the model's response, not time to silence. Our acoustic stop time is not covered by it. |
| Google turned "stop" into "a combination wake word and command" | https://voicebot.ai/2022/01/26/new-stop-command-silences-google-assistant-without-hey-google-wake-word/ | A dedicated platform keyword feature, not general VAD. |
| Silero beats WebRTC and RMS VAD; hysteresis helps WebRTC | https://arxiv.org/abs/2601.17270 (abstract) | Window-size study; says nothing on short words or far-field. |
| PocketSphinx acceptance 48.0% (1 false alarm per 10 h, 10 dB SNR) | https://picovoice.ai/improve-wake-word-accuracy/ | Vendor, wake-word task, not one syllable under echo. Treat as "unproven", not "bad". |
| Silero 87.7% vs WebRTC 50% true positives at 5% false positives | https://picovoice.ai/blog/best-voice-activity-detection-vad/ | Vendor; dataset not stated. |

Reported by a subagent but **not opened by me**: WebRTC's suppressor attenuates near-end speech during double-talk (switchboard.audio); delay estimation breaks with large, moving delay (a Bluetooth article); WebRTC AEC3's clock-drift detector source. They fit our symptoms but are general, not PipeWire-on-USB evidence.

**Not found anywhere:** a cause for the 60% instability or the 950 ms jump; PipeWire documentation of drift compensation (1.4 source was inaccessible: GitLab blocks bots, GitHub `1.4.x` returned 404); published accuracy of any non-neural detector on one-syllable words under echo.

## How much to trust the reports in `.scratch/speech-hosting/`

- `AGY-PI-NEXT-v1.md` and `AGY-PI-NEXT.md` are agy's two answers to `PROMPT-PI-NEXT.md`. **Do not rely on either.** v1 cited a physics paper as a DTW paper, quoted pages that do not contain the quotes, and produced a 404 URL. v2 gave Google search-redirect URLs instead of sources, its quotes of the Silero README and PocketSphinx tutorial are not on those pages, and its lead #1 contradicted the code. The only things from them worth keeping are already in the table above.
- The agy assistant can only see search snippets, so asking it for page quotes invites invented ones. Use a fetch-capable agent and require a verbatim quote from a fetched page.

## Next experiments, in order (each under an hour)

1. **Which PipeWire keys does the Pi have?** `strings $(find / -name libspa-aec-webrtc.so 2>/dev/null | head -1) | grep '^webrtc\.'` and `pipewire --version`. Cheap; settles whether `extended_filter` / `delay_agnostic` exist on 1.4.2.
2. **Proxy "stop" test through the Pi's own chain.** Play `tests/fixtures/pi_stop/` (20 Speech Commands clips: 10 "stop", 5 "no", 5 "up") through the speaker and record the mic: `python scripts/pi_echo_check.py selftest --clips tests/fixtures/pi_stop --in-device UACDemo --out-device CD002 --label "stop-proxy C vol75"` (add the `ALSA_CONFIG_PATH` / `--in-device va_in --out-device va_out --rate 48000` form for AEC; setup in `PI-RESULTS.md`). The usual self-trigger counts are inverted here: an onset means the word **was heard**. Read each clip's `max_confidence` in `run.json` and compare with the laptop table (about 0.94). A large drop localises the loss to the Pi's speaker-to-mic path; a small one points at the human voice or distance instead. This is a proxy: the sound comes from the speaker, not a mouth.
3. **A recorded, scripted silent-room "Stop".** Repeat the silent-room control, but log exactly what was said and when (a written list of 10 words and phrases), at a fixed distance, no AEC, speaker silent. The earlier control grouped holds by length, not by recorded content, so "one-word Stop" is unconfirmed. Use `scripts/pi_live_pilot.py calibrate` for the key-to-voice timing. Record the mic distance and gain.
4. **Shorter hold on existing recordings.** Replay the saved live-pilot audio with 0.5/100 ms and 0.5/150 ms through `scripts/pi_gate_variants.py` (see `--help`) and count false events on the 5-minute soak and on the 90%-volume selftests. This is where the laptop's no-echo noise result can fail.
5. **Is noise suppression what delays the user under AEC?** `[HYPOTHESIS]`. In `config/pipewire-echo-cancel.conf` add inside the module args `aec.args = { webrtc.noise_suppression = false }`, restart with `scripts/aec_pi.sh off && scripts/aec_pi.sh on`, and rerun the 60% selftest and the double-talk onset test. Success: fewer own-voice trips at 60% or an earlier user onset. If neither moves, drop it.
6. **Measure acoustic stop time** (not built). Play a steady tone, trigger the stop at a logged monotonic time, find when the mic envelope falls 30 dB. Needs a small new script; `INTERRUPTS.md` and `PI-RESULTS.md` both list it as unmeasured.

## Ideas, ranked, not yet tested

1. A shorter hold with a two-stage rule: duck on the first window, interrupt on 100 ms only if the duck was not followed by echo-like energy. `[HYPOTHESIS]`
2. A non-neural keyword spotter (PocketSphinx keyphrase mode, or DTW templates of the owner's own "stop") running in parallel for the single word "stop". **Needs the professor's answer** on the "no model on the Pi" rule first. PocketSphinx's vendor figure is poor but unrelated to this task; the only way to know is to test it on `tests/fixtures/pi_stop/` and the owner's recordings.
3. Word-count and keyword rules (Pipecat's minimum-words strategy, Vapi's `numWords`) are **not usable**: they need a speech recogniser, which the Pi does not run.

## Questions for the owner

- May a non-neural keyword spotter run on the Pi under the professor's rule? (Idea 2.)
- Which of the pending decisions in `HANDOFF.md` should wait for experiments 2-4: deleting `EchoModel` / `--barge-echo-*`, making `--vad silero` the default, and adding duck-then-stop?

## Working agreements (unchanged)

Never read or print API keys or secrets. Do not use `--dangerously-skip-permissions` for the agy assistant; its prompts are search-only. Do not touch the owner's untracked files (`docs/VOICE-AGENT-EVALUATION-METRICS.md`, `kek.txt`, `resources/building-voice-agents/`); `git add` by file name, never `-A`. No model on the Pi except Silero VAD. Do not kill processes you did not start. Gate commits on ruff, mypy and pytest. Push to origin only when asked; never force, never push `main`.
