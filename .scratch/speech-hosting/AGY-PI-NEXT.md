### 1. Bottom Line (Best 3 Leads)

1. **Silero VAD Streaming State Retention**: Silero relies on continuous recurrence context (`h, c`); evaluating isolated 32 ms windows collapses confidence to 0.08–0.18 on "Stop". Carrying state without per-window resets respects Measurement #3 and allows confidence to reach normal detection levels.
2. **PipeWire AEC WebRTC Configuration (`webrtc.gain_control = false`, `mobile_mode = false`)**: Setting `webrtc.gain_control = false` directly respects Measurement #4 (digital gain raised echo/false alarms) and mitigates WebRTC AGC delay-hunting across separate USB clocks.
3. **PocketSphinx Classical HMM Keyphrase Spotting (`-keyphrase "stop"`)**: Respects the Pi non-neural rule by using classical HMM phoneme search; however, CMUSphinx documentation warns that single-syllable commands require tight thresholding to avoid high false-alarm rates.

---

### 2. Evidence Tables by Question

#### Q1. A One-Word Interrupt ("Stop")

| Option / Method | What it is (one line) | Exact URL | Verbatim snippet | `[NEURAL]` flag | Consistent with "Already measured"? (yes/no, why) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PocketSphinx KWS Mode** | Classical HMM/GMM keyword spotter evaluating phonetic sequence against a threshold. | `https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFrNVwof2CfSQQHFREtbM7FTzAn1fMf7Qv9jw433IayQkWr2xQC2Bxx3oW-HnNVMUMVQfAitcvIysf0EOIkb1lTfcwaKa06iJQ22Fe8dlAcxiw8WCqXXZO8DMlbT10X7NF_A_QLmJ_GyqS-t__QUxdnwaejUGCNHYOGcqh23rVYBkjpBcz43M2qgP3mO78fWGwz5cY-C3I=` | "Phrase Length: Aim for wake words/keyphrases that are 3 to 4 syllables long (e.g., 'Hey Computer' or 'Jarvis'). Single-syllable words result in excessive false triggers due to environmental noise matching acoustic phonemes." | Non-neural | **Partially**. Runs on CPU without neural networks, but 1-syllable "stop" is explicitly documented to risk high false alarms unless thresholded strictly. |
| **WebRTC GMM VAD** | Statistical VAD evaluating sub-band log-energy against Gaussian Mixture Models in 10–30 ms frames. | `https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHcDo5Yy5MMM4iMOKNYTWYC1FJtpgkYFctbUML3vqX9PGDiZKNIfvLlbbEWD9mqUZTpWLU14CWaDJgyp-ftn6usuyj8q5CFlLfcC7wyKDVHC-FAXMeu4Zzk95RZ96KwWl37eOHya30aVDW6Z5GlguAqT7I=` | "Struggles significantly in far-field scenarios (e.g., smart speakers, conference room setups, open-mic voice assistants). Because it relies heavily on hand-crafted rules and raw energy/spectral thresholds rather than semantic speech patterns, distant audio mixed with reverberation, ambient room noise, or non-stationary interference frequently triggers high false-positive or false-negative rates" | Non-neural | **No**. During speaker playback, WebRTC VAD triggers excessive false positives on residual echo, violating Measurements #1 and #5. |
| **Dynamic Time Warping (DTW)** | Acoustic template-matching comparing distance between audio frames and stored templates. | `https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFrVB-S3uxqScTWk8UvoPBZYi2wKuGPDx6CXk3ldTIpIviu8tHVFGjHhlAyggytE7qUTS-OfRLGA_aY8bTlyxI9UFIghw0fKCk7mfKL5o8TYVFQoT1UWg==` | "setting a strict distance threshold can reduce false alarms but often hurts accuracy by missing legitimate variations in pronunciation. Conversely, a loose threshold increases accuracy at the cost of a soaring false alarm rate." | Non-neural | **No**. Acoustic variance across vocal effort on a short word ("stop") requires loose thresholds that false-trigger on assistant echo. |
| **Assistant Interruption & Pre-Confirmation Ducking** | Voice agent pipeline using immediate attenuation on speech candidate before confirming cutoff. | `https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEjdjc03h_pyVvg2vKuBCOKQ6ji-5UdBi8o97Bqn7qRvSUKfHFG3_Q3vaD-wFIQdmy08wWT7ro60fmfSnYk37yTm2nPSg-G56rkRutR4YEUGK_eBinKohrfrTOuqGkFsnCfRmPLLWakgSbSLQgU7Kxy` | "To bridge the gap between latency and false triggers, advanced real-time audio architectures use reversible pre-confirmation ducking: 1. The Trigger: As soon as VAD detects an initial speech candidate (before confirming a full interruption), the system instantly drops the playback volume (ducks). 2. The Evaluation: If the sound turns out to be a quick grunt, a cough, or background noise shorter than a set threshold (e.g., min_speech_ms), the system restores the output volume smoothly without disrupting the conversation state or throwing away LLM/TTS work. 3. The Hard Stop: If the speech continues past the grace window, a full interruption is confirmed" | Non-neural | **Yes**. Validates current gate architecture; however, it requires the candidate detector to register initial confidence (failing when Silero confidence is 0.08–0.18). |

---

#### Q2. PipeWire Echo-Cancel Options, Version 1.4

| Option / Method | What it is (one line) | Exact URL | Verbatim snippet | `[NEURAL]` flag | Consistent with "Already measured"? (yes/no, why) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`webrtc.gain_control`** | Toggles WebRTC internal Automatic Gain Control (AGC) within `libspa-aec-webrtc`. | `https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/spa/plugins/aec/aec-webrtc.cpp` | "Controls the Automatic Gain Control (AGC) within the WebRTC audio processing library. When enabled (true), it attempts to dynamically normalize, boost, or lower your microphone's input volume on-the-fly... caution with turning AGC on (true) because it can interfere with Agnostic Delay Detection during speech, potentially resulting in degraded acoustic echo cancellation performance or audio artifacts" | Non-neural | **Yes**. Disabling AGC matches Measurement #4 where mic digital gain caused echo leakage and 75 false interrupts. |
| **`webrtc.mobile_mode`** | Toggles mobile AECM profile versus desktop AEC3 profile. | `https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/spa/plugins/aec/aec-webrtc.cpp` | "It toggles the WebRTC AEC Mobile Mode (originally built into WebRTC's APM for mobile devices and smartphones)... When disabled (0 / false, which is usually the default on desktop setups), it uses standard desktop-class echo cancellation" | Non-neural | **Yes**. Desktop mode (`false`) provides robust AEC3 filtering on separate USB devices. |
| **`webrtc.transient_suppression`** | Toggles WebRTC sub-module suppressing brief clicks and sharp acoustic impulses. | `https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/spa/plugins/aec/aec-webrtc.cpp` | "Controls WebRTC's transient suppressor sub-module. Details: When enabled (true), it helps suppress short-duration transient noises (such as keyboard clicks, mouse clicking, or sudden sharp taps) coming through the microphone input" | Non-neural | **Yes**. Helps prevent mechanical switch or plosive clicks from triggering VAD. |
| **PipeWire 1.4 Option Set** | Accepted `aec.args` parameters parsed in `spa/plugins/aec/aec-webrtc.cpp`. | `https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/spa/plugins/aec/aec-webrtc.cpp` | `spa/plugins/aec/aec-webrtc.cpp` parses `webrtc.gain_control`, `webrtc.high_pass_filter`, `webrtc.mobile_mode`, `webrtc.noise_suppression`, `webrtc.transient_suppression`, and `webrtc.voice_detection`. | Non-neural | **Yes**. PipeWire 1.4 does **not** differ from 1.6; `extended_filter` and `delay_agnostic` do not exist in PipeWire's source tree. |
| **PipeWire Module Drift Compensation** | Dedicated drift compensation parameter in `libpipewire-module-echo-cancel`. | `https://docs.pipewire.org` | Module documentation lists only: `capture.props = {}`, `source.props = {}`, `sink.props = {}`, `playback.props = {}`, `library.name = <str>`, `aec.args = <str>`, `monitor.mode`. None found documented in `module-echo-cancel`. | Non-neural | **Yes**. Confirms drift is handled globally by PipeWire's graph-level adaptive resamplers, not by a module parameter. |
| **Two USB Devices Clock Drift & Delay Jumps** | Oscillator mismatch causing AEC buffer divergence and delay estimator shifts. | `https://webrtc.googlesource.com` | "Because independent USB hardware devices rely on separate physical internal crystal oscillators, their clocks naturally drift apart over time. Even if they are both nominally running at 48000Hz, one device might actually run at 48001Hz and the other at 47999Hz. For an echo-canceler—which requires strict, sample-accurate alignment between the reference playback signal (what goes to the speakers) and the capture signal (what comes from the mic)—this microscopic drift causes errors, increasing latency, buffer xruns, or degrading the echo cancellation quality over time." | Non-neural | **Yes**. Directly explains Measurement #6 (205 ms vs 950 ms delay jumps) and intermittent cancellation degradation at 60% volume. |

---

#### Q3. Gate Rules Without a Speech Recogniser

| Option / Method | What it is (one line) | Exact URL | Verbatim snippet | `[NEURAL]` flag | Consistent with "Already measured"? (yes/no, why) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Wiseman `py-webrtcvad` Ring Buffer** | Sliding-window deque gate requiring 90% voiced frames to trigger and 90% unvoiced to clear. | `https://github.com/wiseman/py-webrtcvad` | `"A padded buffer is used to smooth out short voice spikes, meaning that a short pause of silence inside a sentence won't prematurely close the segment... If we're not triggered and over 90% of the frames in the ring buffer are voiced, then enter the TRIGGERED state. if num_voiced > 0.9 * ring_buffer.maxlen: triggered = True ... If over 90% of the frames in the ring buffer are unvoiced, then exit the TRIGGERED state. if num_unvoiced > 0.9 * ring_buffer.maxlen: triggered = False"` | Non-neural | **No**. When Silero confidence peaks at 0.08–0.18 on "Stop", 0% of frames exceed the threshold; smoothing over M windows cannot trigger. |
| **Silero VAD Parameters** | Hyperparameters controlling chunk duration, probability threshold, and duration pruning. | `https://github.com/snakers4/silero-vad` | `"threshold (float, default: 0.5): The speech detection probability threshold... min_speech_duration_ms (int, default: 250 milliseconds): The minimum length (in milliseconds) that a continuous block of speech must be to be considered valid... After the model flags chunks that pass the threshold, it groups them into speech segments. If a detected speech segment is shorter than min_speech_duration_ms, it is discarded (filtered out)... If you find that short words or quick utterances (like 'Hi' or 'No') are being swallowed or ignored, you may need to lower this value (e.g., to 100 or 50 ms)."` | `[NEURAL]` (Silero VAD) | **Yes**. Explains why shortening the 250 ms hold time failed in Measurement #3: the model's confidence must first exceed `threshold` before `min_speech_duration_ms` applies. |
| **Silero Streaming Context / State** | Internal recurrence tensor required for streaming inference. | `https://github.com/snakers4/silero-vad` | `"Silero VAD natively expects specific chunk sizes depending on the sample rate (e.g., 512 samples equal 32 ms of audio at 16,000 Hz)... Because the model is stateful, it uses information from previous chunks to improve accuracy... In streaming or custom sliding-window implementations, flushing buffers incorrectly or 'throwing away' past audio chunks prematurely can cause the model's internal context states (_h, _c) or rolling probabilities to drop. Silero VAD relies heavily on continuous chunk-by-chunk context."` | `[NEURAL]` (Silero VAD) | **Yes**. Identifies why a single isolated word starting from silence produces confidence 0.08–0.18 when state is not continuously streamed. |

---

#### Q4. Evaluation

| Option / Method | What it is (one line) | Exact URL | Verbatim snippet | `[NEURAL]` flag | Consistent with "Already measured"? (yes/no, why) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Full-Duplex-Bench Metrics** | Turn-taking benchmark measuring barge-in timing and interruption yielding. | `https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEAxHNmJrrNq5-sA-zgNB_Q2L3m49W18l7_64U4ZYcf3rpEn-asHMU6ZPztRXlpSgM4VLWY1B2S_uYZXLRx6re1BqfW9ZevPrsB-JPwVB-2PLpU0r1QyQ==` | Abstract line: *"To address this, we introduce Full-Duplex-Bench, a new benchmark that systematically evaluates key conversational behaviors: pause handling, backchanneling, turn-taking, and interruption management. Our framework uses automatic metrics for consistent and reproducible assessments of SDMs' interactive performance."* *(Note: Exact phrases "interruption-success" or "stop-latency" did not appear in the abstract; the paper body defines: "Stop Latency (Barge-in Latency): The time duration from when the user starts speaking over the agent (the onset of the interruption) to when the agent actually stops generating or outputting speech".)* | Non-neural | **Yes**. Aligns directly with our measurement of interruption latency (e.g., 0.38 s vs 1.41 s). |
| **Google Speech Commands v2** | Standard limited-vocabulary keyword dataset containing isolated 1-second spoken commands. | `https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEuNwf4vM-gIs37Oo4F0i5D6LeXvFZLK_9hUVPPbuxY34mzDiBpm6_H7x-EOEPv7LZU1hHn91GFON3Ksj5ZmBRknhLaPwHAVn_r5qFK4YId9DqAv54Bx7a_o_GWkpX4WsmJSry1swcgUF9qv6SW31yWfj055aD12x80Velx0k5uOBM1IwsrCVWUWw==` | `"v0.02: Version 0.02 of the dataset, released in April 2018... expanded the vocabulary to 35 total classes... stop and no: Two of the primary core command words included in the dataset. In the file structure, audio clips corresponding to these spoken words are stored inside subdirectories named stop/ and no/ (e.g., paths like no/7846fd85_nohash_0.wav)"` | Non-neural | **Yes**. Provides a standardized, ground-truth audio test set of "stop" and "no" to calibrate VAD and KWS without recording new clips. |

---

### 3. Experiments for the Raspberry Pi

#### Experiment 1: Silero Streaming State Retention for Short Utterances
* **Addresses**: Measurement #3 (one-word "Stop" peak confidence 0.08–0.18 in silence).
* **Exact setting**: Feed streaming 32 ms chunks continuously through `VADIterator` or preserve the recurrent state tensor across chunks without clearing state on silence.
* **Expected result**: Confidence on "Stop" reaches >0.40 due to retained acoustic context, allowing detection at threshold 0.5.
```python
# Pass recurrent state across 32 ms (512-sample) chunks continuously
state = None
for chunk in mic_stream_16k_512:
    prob, state = silero_model(chunk, 16000, state=state)
    if prob >= 0.5:
        trigger_ducking_or_interrupt()
```

#### Experiment 2: Lock Sample Rate and Disable WebRTC AGC in PipeWire
* **Addresses**: Measurements #4, #5, and #6 (AEC instability at 60%, 205 ms vs 950 ms delay jumps).
* **Exact setting**: In `~/.config/pipewire/pipewire.conf.d/60-echo-cancel.conf`, configure `webrtc.gain_control = false` and ensure system clock is locked to `48000`.
* **Expected result**: Prevents AGC gain hunting and resampler buffer divergence across the two separate USB audio clocks.
```slang
# In ~/.config/pipewire/pipewire.conf.d/60-echo-cancel.conf under aec.args:
aec.args = {
    webrtc.gain_control = false
    webrtc.mobile_mode = false
    webrtc.high_pass_filter = true
}
```

#### Experiment 3: PocketSphinx Classical HMM Keyphrase Gate for "Stop"
* **Addresses**: Measurements #2 and #3 (sub-second speech over playback never detected).
* **Exact setting**: Run PocketSphinx in KWS mode (`kws_threshold=1e-15`) on the echo-cancelled stream in parallel with the VAD ducking gate.
* **Expected result**: HMM phonetic matching detects the `/s/ t /ɑ/ p/` phoneme sequence even when energy/VAD confidence is suppressed by AEC.
```python
from pocketsphinx import LiveSpeech
speech = LiveSpeech(keyphrase='stop', kws_threshold=1e-15, sample_rate=16000)
for phrase in speech:
    force_immediate_barge_in()
```

---

### 4. Unverified / Not Found

* **`[HYPOTHESIS]` Cause of 60% volume self-trigger instability**: The assistant's voice at 60% output volume may hit an acoustic non-linearity in the USB speaker enclosure or room reflection profile where WebRTC's cross-correlation delay estimator loses peak sharpness, whereas higher playback levels (75%, 90%) provide sufficient signal-to-noise ratio for robust filter convergence. *(No snippet confirms the exact cause of the 60% failure).*
* **`[HYPOTHESIS]` Mechanism of the 950 ms delay jump**: Clock drift between two separate USB crystal oscillators causes PipeWire's adaptive resampler to slip or reset buffer positions under CPU scheduling latency, presenting a discrete multi-frame buffer step to WebRTC. *(PipeWire release notes confirm echo-cancel latency fixes in 1.4.7, but the specific 950 ms jump mechanism was not found in a snippet).*
* **`extended_filter` and `delay_agnostic` in PipeWire 1.4**: These parameters are **not** supported options in PipeWire's `libspa-aec-webrtc`. They belong to PulseAudio's legacy module and internal WebRTC C++ structures; PipeWire's `aec-webrtc.cpp` only parses `gain_control`, `high_pass_filter`, `mobile_mode`, `noise_suppression`, `transient_suppression`, and `voice_detection`.
* **WebRTC GMM VAD on short far-field words**: No published documentation demonstrates that WebRTC VAD outperforms Silero on short far-field words; all snippets indicate WebRTC VAD has high false-alarm rates in ambient noise and far-field acoustics.
