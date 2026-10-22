# Research brief for agy (v2): short-utterance barge-in and echo cancellation on a Raspberry Pi

Search only. You can see search-result snippets, not full pages. So you can only support a claim with text that appeared in a snippet. Everything else is "Unverified". A short, honest report is better than a long, confident one. Padding, invented sources and plausible-sounding mechanisms are worse than gaps.

## Why this is v2: what went wrong in v1 (do not repeat)

A first report was checked against the sources. Failures:
- An arXiv ID was cited for a topic it does not cover (it was a physics paper). **Never give an arXiv ID unless a snippet showed that ID next to the title.**
- Quotes were attributed to pages that do not contain them (README, docs page). **A quote must be copied word for word from a snippet.** Do not paraphrase inside quotation marks.
- "URLs" were bare domains or guessed paths (one was a 404). **Give the full URL exactly as the search result showed it, or no URL.**
- Source names that look invented (a "MathWorks / ProbeCheck" page). If you cannot name a real page, say so.
- Config option names were stated as fact and several do not exist in the plugin version we checked. **Option names, defaults and file paths need a snippet from the project's own docs or source; otherwise "Unverified".**
- Experiments contradicted our measurements (listed below). **Check every recommendation against "Already tested" before proposing it.**
- Mechanisms ("the LSTM state is starved", "the AGC gain-chases") were presented as diagnoses with no source. **Label a guess `[HYPOTHESIS]` and give no supporting quote for it.**

## Our system (facts)

- Raspberry Pi 4B, Debian 13, PipeWire 1.4.2. No sudo. Python.
- **Rule: no neural model on the Pi except Silero VAD.** Classical DSP and non-neural detectors are allowed in principle. Anything neural must be labelled `[NEURAL]` so the owner can ask the professor.
- **Hardware is fixed.** Do not recommend buying, swapping or repositioning any device. Software, configuration and algorithms only.
- Speaker and microphone are two separate USB devices. Mic hardware gain is at maximum.
- Barge-in gate: Silero VAD per 32 ms window; duck -15 dB at the first window over the confidence, interrupt after the speech holds 250 ms, restore if it stops first. No speech recogniser runs on the Pi, so rules that need recognised words are not usable.
- Echo handling: none, or PipeWire's WebRTC echo-cancel module with default options (no `aec.args` set).

## Already measured (do not re-propose what these rule out)

1. Loud sentence over 75% playback with AEC: confidence threshold 0.7 / 250 ms hold interrupts 39% (median 1.41 s); threshold **0.5** / 250 ms interrupts 76% (0.38 s), 0 false events in a 5-minute silent soak. Raising the threshold while the assistant speaks is therefore the wrong direction.
2. Normal-volume and sub-second speech over playback is almost never detected (about 1/30 and 0/16).
3. A one-word "Stop" in a **silent room** had Silero peak confidence **0.08-0.18** (0 of 4 detected). Full sentences were 8 of 8. So the limit is Silero's confidence, **not the hold time**; shortening the hold or lowering the threshold to 0.35 cannot catch confidence 0.18.
4. Digital gain on the recorded mic (+6 to +18 dB) did not raise Silero's confidence on speech with AEC, and without AEC it raised the assistant's own echo until 75 false interrupts fired. Amplitude normalisation is the same thing and is ruled out.
5. Self-trigger on the assistant's own voice: without AEC at 90% volume (6/6 clips); with AEC unstable at 60% (up to 4/6 clips) but clean at 75% and 90%. Cause unknown.
6. AEC round trip is about 205 ms, but two runs read about 950 ms.
7. With AEC the user is detected about 1 s later than without.
8. A loudness-only detector never heard the user on this mic.

## Questions (answer only these; skip any you cannot support)

### Q1. A one-word interrupt ("stop")
- Which **non-neural** methods detect a short spoken command word on a CPU-only device? Look for classical keyword spotting (HMM, e.g. PocketSphinx keyphrase mode; template or DTW matching) and for classical VADs (WebRTC GMM VAD, energy/pitch/harmonicity features). For each: the project or paper, one snippet that shows what it does, and any snippet that reports accuracy or false-alarm rate on short or noisy speech.
- Any published or documented comparison of Silero VAD and WebRTC VAD **on short utterances or far-field speech**.
- How do assistants and frameworks document one-word interruption ("stop")? Only report what a snippet shows (product docs, engineering blogs).

### Q2. PipeWire echo-cancel options, version 1.4
- What are the **actual option names** accepted under `aec.args` for `libspa-aec-webrtc` in PipeWire 1.4, and what do they do? Source: PipeWire's own documentation or source tree only. If a snippet does not show the option, put it under Unverified. (We checked the plugin shipped in PipeWire 1.6: it has `webrtc.gain_control`, `webrtc.high_pass_filter`, `webrtc.mobile_mode`, `webrtc.noise_suppression`, `webrtc.transient_suppression`, `webrtc.voice_detection`, and **no** `extended_filter` or `delay_agnostic`. Tell us if 1.4 differs.)
- Is there documented drift compensation between separate capture and playback devices in PipeWire's echo-cancel module? Quote the doc or say none was found.
- Documented causes of an echo canceller's delay estimate jumping (205 ms vs 950 ms) or of cancellation failing intermittently with two USB devices. Only cite posts, bug reports or docs a snippet shows.

### Q3. Gate rules without a speech recogniser
- Documented VAD gate designs that use "N of the last M windows", hysteresis, or ramped ducking, with the values and the stated trade-offs. Name the project and show the snippet.
- Silero VAD's documented parameters (threshold, negative threshold, minimum speech duration, window size per sample rate) and any documented behaviour on very short words. Only from the Silero repository's README or wiki as shown in a snippet.

### Q4. Evaluation
- Do public benchmarks for full-duplex or barge-in (e.g. Full-Duplex-Bench) define interruption-success or stop-latency metrics? Quote the abstract line that says so, or say it did not show.
- Is Google Speech Commands v2 documented to contain the words "stop" and "no"? One snippet is enough.

## Output format (short)

1. **Bottom line** (at most 5 lines): the best 3 leads, and for each whether it respects "Already measured".
2. **One table per question**: option | what it is (one line) | exact URL | verbatim snippet | `[NEURAL]` flag | consistent with "Already measured"? (yes/no, why).
3. **At most 3 experiments** for the Pi, each: exact setting, expected result, and which "Already measured" item it addresses. No code longer than 10 lines.
4. **Unverified / not found**: everything you believe but could not show in a snippet. Put guesses here, not in the tables.

Rules for numbers: CPU use, prices, dB figures and latencies need a snippet or are left out. No estimates dressed as findings.
Say plainly when sources disagree or when an option would not help. Never ask for or print API keys or secrets.
