# Research Report: Acoustic Echo Cancellation, Barge-In, and Full-Duplex Voice Agents

---

## (a) One-Paragraph Answer

The root cause of self-interruption in full-duplex agents is that software Voice Activity Detection (VAD) cannot distinguish between near-end user speech and acoustic speaker bleed without an explicit reference signal and an adaptive echo canceller [SEEN-IN-SNIPPET]. Commercial smart speakers (Amazon Echo, Google Nest, Apple HomePod) solve this at the acoustic/hardware front-end by routing the exact digital playback stream as an AEC reference into a multi-microphone DSP array running linear adaptive filtering, beamforming, and deep residual echo suppression (RES) [SEEN-IN-SNIPPET]. Voice agent frameworks (LiveKit, Pipecat, Vapi, Twilio) push AEC entirely to the edge (browsers via WebRTC AEC3, native mobile hardware DSPs, or telephony gateways) [SEEN-IN-SNIPPET] and handle barge-in via guard times (`min_speech_duration` $\approx$ 150–250 ms, `voiceSeconds` $\approx$ 0.2 s), word-count thresholds, and instantaneous TTS/LLM buffer purging [SEEN-IN-SNIPPET]. For your constraints—where the Raspberry Pi cannot run neural models—the only stable architecture for Setup 2 (Pi + speakerphone) is **hardware AEC on the USB speakerphone** (e.g., Poly Sync 20 or XMOS XVF3800), routing both playback and capture through that single USB endpoint so its internal DSP performs physical reference cancellation before passing audio to Silero VAD [SEEN-IN-SNIPPET]. For Setup 1 (laptop speakers/mic), PipeWire’s WebRTC AEC module cancels converged echo (20–40 dB ERLE) [SEEN-IN-SNIPPET], but leaves a 1–2 second initial convergence gap that requires a software guardrail: an initial grace period, volume ducking (12–18 dB) upon tentative VAD triggers, and ASR transcript cross-matching against active TTS spans [SEEN-IN-SNIPPET]. For Setup 3 (headset), acoustic isolation physically eliminates feedback, allowing raw Silero VAD to operate without AEC [SEEN-IN-SNIPPET].

---

## (b) Master Overview: Who, Mechanism, AEC Location, Interruption Policy, and Source

| Entity / System | Acoustic Echo Handling | Where AEC Runs | Interruption & Barge-In Policy | Source / Reference |
| :--- | :--- | :--- | :--- | :--- |
| **Amazon Echo / Alexa** | Multi-mic circular array (7 mics) [SEEN-IN-SNIPPET], digital playback loopback reference [SEEN-IN-SNIPPET], linear adaptive filter + beamforming [SEEN-IN-SNIPPET], audio ducking on trigger [SEEN-IN-SNIPPET]. | On-device DSP / AFE (hardware front-end) [SEEN-IN-SNIPPET]. | Historically wake-word-only barge-in ("Alexa, stop") [SEEN-IN-SNIPPET]; now supports device-directed speech detection (DDSD) for conversational follow-ups [SEEN-IN-SNIPPET]. | Amazon Science [SEEN-IN-SNIPPET]; Cornell et al., arXiv:2111.10639 [SEEN-IN-SNIPPET]. |
| **Google Nest / Assistant** | Multichannel AEC subtracting digital playback reference [SEEN-IN-SNIPPET], spatial beamforming [KNOWN-BUT-UNVERIFIED], media ducking upon trigger detection [SEEN-IN-SNIPPET]. | On-device DSP / SoC [SEEN-IN-SNIPPET]. | Wake-word barge-in ("Hey Google") or Quick Phrases; media volume ducks immediately when speech activity is suspected [SEEN-IN-SNIPPET]. | Google Nest Architecture Docs [SEEN-IN-SNIPPET]; TechRadar [SEEN-IN-SNIPPET]. |
| **Apple HomePod / Siri** | Multichannel Echo Cancellation (MCEC) via linear adaptive filters [SEEN-IN-SNIPPET] + Deep Learning Residual Echo Suppression (RES) [SEEN-IN-SNIPPET] + 6-mic beamforming [SEEN-IN-SNIPPET]. | Onboard Apple A8 / S-series SoC [SEEN-IN-SNIPPET]. | Barge-in on "Hey Siri" across the room even with music 30–40 dB louder than speech at the microphones [SEEN-IN-SNIPPET]. | Apple Machine Learning Research (HomePod Far-Field Siri) [SEEN-IN-SNIPPET]. |
| **OpenAI Realtime API** | Relies on client-side WebRTC/OS hardware AEC [SEEN-IN-SNIPPET]; optional backend `noise_reduction: {type: "far_field"}` [SEEN-IN-SNIPPET]. | Client device (browser WebRTC or mobile OS DSP) [SEEN-IN-SNIPPET]. | `server_vad` with configurable `threshold` (0.5–0.8), `prefix_padding_ms` (300 ms), `silence_duration_ms` (500 ms) [SEEN-IN-SNIPPET]. Client flushes audio and calls `conversation.item.truncate` [SEEN-IN-SNIPPET]. | OpenAI API Reference [SEEN-IN-SNIPPET]; Latent Space [SEEN-IN-SNIPPET]. |
| **Pipecat** | Uses client transport AEC (WebRTC via Daily / SmallWebRTC / LiveKit) [SEEN-IN-SNIPPET]; recommends headphones for local laptop development [SEEN-IN-SNIPPET]. | Client browser/device or external transport gateway [SEEN-IN-SNIPPET]. | `enable_interruptions=True` [SEEN-IN-SNIPPET]; broadcasts `InterruptionFrame` upstream/downstream [SEEN-IN-SNIPPET]; flushes TTS buffer [SEEN-IN-SNIPPET]; commits only words actually heard into LLM context [SEEN-IN-SNIPPET]. | Pipecat Documentation & GitHub [SEEN-IN-SNIPPET]. |
| **LiveKit Agents** | Client WebRTC AEC (`echoCancellation: true`) [SEEN-IN-SNIPPET]; server-side Krisp / ai-coustics on SIP/telephony tracks [SEEN-IN-SNIPPET]. | Client endpoint or server-side media gateway [SEEN-IN-SNIPPET]. | Adaptive interruption model (analyzes pitch, speech onset strength, duration) [SEEN-IN-SNIPPET]; `min_speech_duration` filter [SEEN-IN-SNIPPET]; sub-200 ms TTS cancellation & context truncation [SEEN-IN-SNIPPET]. | LiveKit Agents Docs [SEEN-IN-SNIPPET]. |
| **Vapi** | Relies on client-side browser AEC or telephony gateway DSP [KNOWN-BUT-UNVERIFIED]. | Client / Telephony bridge [KNOWN-BUT-UNVERIFIED]. | `stopSpeakingPlan`: `numWords` (0 to 3), `voiceSeconds` ($\approx$0.2 s), `backoffSeconds` (cooldown after cutoff $\approx$1.0 s) [SEEN-IN-SNIPPET], `interruptionPhrases` [SEEN-IN-SNIPPET]. | Vapi Voice Configuration Docs [SEEN-IN-SNIPPET]. |
| **Retell AI** | Relies on full-duplex telephony / client WebRTC AEC; integrates server-side denoising modes [SEEN-IN-SNIPPET]. | Client / Telephony bridge [SEEN-IN-SNIPPET]. | Interruption sensitivity slider (0 to 1) [SEEN-IN-SNIPPET]; response wait time slider (`responsiveness`: 0 to 1) [SEEN-IN-SNIPPET]; backchannel filtering [SEEN-IN-SNIPPET]. | Retell AI Documentation [SEEN-IN-SNIPPET]. |
| **Twilio Media Streams** | Inbound/outbound WebSocket audio [SEEN-IN-SNIPPET]; telephony network lacks local room acoustic echo cancellation (must be done at endpoints) [SEEN-IN-SNIPPET]. | User phone handset hardware or backend media processor [SEEN-IN-SNIPPET]. | Full-duplex WebSocket stream; on barge-in, server must send `{"event": "clear"}` over WebSocket to purge Twilio playback buffer [SEEN-IN-SNIPPET]. | Twilio Media Streams API Docs [SEEN-IN-SNIPPET]. |
| **Deepgram Voice Agent API** | Recommends headsets during development [SEEN-IN-SNIPPET]; leverages browser WebRTC constraints in web production [SEEN-IN-SNIPPET]. | Client browser/device [SEEN-IN-SNIPPET]. | Server detects `UserStartedSpeaking` [SEEN-IN-SNIPPET]; client must immediately flush local audio playback queues [SEEN-IN-SNIPPET]. | Deepgram Agent API Documentation [SEEN-IN-SNIPPET]. |
| **Azure Speech SDK** | Microsoft Audio Stack (MAS): default DSP pipeline (AEC, beamforming, NS) or V2 ML-based echo suppression [SEEN-IN-SNIPPET]. | Host OS (Windows/Linux via Speech SDK MAS) [SEEN-IN-SNIPPET]. | In Azure Voice Live API: server semantic VAD with `interrupt_response: true`, `auto_truncate: true` [SEEN-IN-SNIPPET]. | Microsoft Azure Cognitive Services Documentation [SEEN-IN-SNIPPET]. |

---

## 1. Commercial Assistants

### 1.1 Microphones, Beamforming, and Playback Reference Routing
* **Digital Reference Signal Loopback:** Smart speakers (Echo, Nest, HomePod) send digital audio to their internal Digital-to-Analog Converters (DACs) and power amplifiers while simultaneously tapping that identical digital PCM stream as an internal **reference signal** fed directly to the Acoustic Echo Cancellation (AEC) DSP block [SEEN-IN-SNIPPET].
* **Microphone Arrays & Spatial Beamforming:** 
  * Amazon Echo devices typically employ circular multi-microphone arrays (such as the classic 7-microphone array) [SEEN-IN-SNIPPET].
  * Beamforming applies delay-and-sum and adaptive spatial filtering across microphones to form a high-gain directional "listening beam" oriented toward the talker, while placing spatial nulls toward the direction of the speaker driver or room reflections [SEEN-IN-SNIPPET].
  * Apple HomePod uses a 6-microphone circular array running continuous covariance-matrix spatial analysis to isolate user speech from acoustic reflections [SEEN-IN-SNIPPET].

### 1.2 Linear Adaptive Filtering and Residual Echo Suppression (RES)
* **The Loudness Disparity:** On devices like the Apple HomePod, internal loudspeaker output can be **30 to 40 dB louder** at the microphone array than the user's voice arriving from across the room [SEEN-IN-SNIPPET].
* **Multichannel Echo Cancellation (MCEC):** HomePod uses an MCEC linear adaptive filter running on the onboard Apple SoC (e.g., A8) [SEEN-IN-SNIPPET]. Linear filters model the acoustic transfer function between the speaker drivers and each microphone [SEEN-IN-SNIPPET].
* **Residual Echo Suppression (RES):** Because physical loudspeaker non-linearities (cone distortion, cabinet vibration) and time-varying room dynamics leave non-linear residual echo sitting 10–20 dB above the user's speech, linear filters alone cannot eliminate feedback [SEEN-IN-SNIPPET]. Apple, Amazon, and Google deploy deep-learning-based RES masks downstream of the linear filter to suppress non-linear components without degrading user speech during double-talk [SEEN-IN-SNIPPET].

### 1.3 Audio Ducking and Wake-Word Barge-In
* **Wake-Word-Gated Barge-In:** On Amazon Echo and Google Nest, "barge-in" during media playback has historically been gated by the on-device wake-word engine [SEEN-IN-SNIPPET]. The device runs the wake-word detector on the AEC-cleaned stream continuously [SEEN-IN-SNIPPET].
* **Acoustic Ducking:** When the wake word (or high-probability speech activity) is detected, the playback volume of media or the assistant’s voice is **ducked immediately (attenuated by 12–20 dB)** [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED]. Ducking dramatically increases the Signal-to-Echo Ratio (SER), allowing the ASR engine to decode user commands without clipping or echo interference [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].

### 1.4 Web and Mobile Implementations (ChatGPT Voice, Gemini Live)
* **Mobile Operating Systems (iOS & Android):** Smartphones possess dedicated hardware audio DSPs with calibrated acoustic profiles [SEEN-IN-SNIPPET]. The OS audio layer (`AVAudioEngine` / Android AudioTrack & AudioRecord in Voice Communication mode) provides hardware AEC that subtracts the speakerphone output from the mic input at the driver level [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].
* **Browser Constraints (`getUserMedia`):** Browser-based voice clients rely on W3C media capture constraints:
  ```javascript
  navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false // Frequently disabled to prevent gain-pumping artifacts [SEEN-IN-SNIPPET]
    }
  });
  ```
  WebRTC inside Chrome and Safari invokes the browser’s internal WebRTC APM (Audio Processing Module) or delegates to OS hardware AEC [SEEN-IN-SNIPPET]. When running on raw laptop speakers without hardware-tuned DSP, WebRTC AEC can experience brief leakage before convergence [SEEN-IN-SNIPPET].

---

## 2. Voice-Agent Frameworks and Platforms

### 2.1 Where AEC Runs: Architectural Division
* **LiveKit Agents:** Offloads AEC to the client browser or mobile application using native WebRTC client processing (`echoCancellation: true`) [SEEN-IN-SNIPPET]. For server-side integrations or telephony (SIP), LiveKit Cloud routes audio through server-side deep learning models (Krisp, ai-coustics) to strip background noise and echo before feeding the agent’s VAD and STT [SEEN-IN-SNIPPET].
* **Pipecat:** Relies on WebRTC transport wrappers (Daily, SmallWebRTC, LiveKit) for AEC [SEEN-IN-SNIPPET]. In raw WebSocket or local development setups, Pipecat explicitly notes that running over open laptop speakers causes self-interruption loops and advises developers to use headphones [SEEN-IN-SNIPPET].
* **Twilio Media Streams:** Twilio acts as a full-duplex WebSocket bridge [SEEN-IN-SNIPPET]. The telephony carrier does not remove acoustic echo created by a user's open speakerphone in a room [SEEN-IN-SNIPPET]. Developers must handle echo at the server endpoint or rely on user-handset AEC [SEEN-IN-SNIPPET].
* **OpenAI Realtime API & Azure Voice Live API:** Both APIs push AEC responsibility to the client endpoint [SEEN-IN-SNIPPET]. OpenAI’s `server_vad` processes raw incoming audio deltas; if the client lacks AEC, `server_vad` triggers false barge-ins on echoed TTS deltas [SEEN-IN-SNIPPET].

### 2.2 Interruption Thresholds, Guard Times, and Policies
Real-world voice agent platforms do not use bare, instantaneous VAD triggers. They apply strict timing parameters:

1. **Minimum Speech Duration (`min_speech_duration` / `voiceSeconds`):**
   * *Vapi:* Exposes `stopSpeakingPlan.voiceSeconds` (default: **0.2 s / 200 ms**) [SEEN-IN-SNIPPET]. User speech must sustain for $\ge$200 ms before an interruption is recognized [SEEN-IN-SNIPPET].
   * *LiveKit:* Exposes `min_speech_duration` (typically configured between **150 ms and 300 ms**) to prevent clicks, coughs, or brief echo spikes from halting playback [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].
2. **Word Count Thresholds (`numWords`):**
   * *Vapi:* Exposes `stopSpeakingPlan.numWords` [SEEN-IN-SNIPPET]. Setting `numWords: 0` triggers on pure VAD [SEEN-IN-SNIPPET]; setting `numWords: 2` or `3` requires the streaming ASR engine to transcribe at least 2–3 words before interrupting [SEEN-IN-SNIPPET], ignoring short backchannel tokens ("uh-huh", "yeah") [SEEN-IN-SNIPPET].
3. **Grace Periods and Post-Interruption Backoff:**
   * *Vapi:* Exposes `backoffSeconds` (default: **1.0 s**) as a cooldown after an interruption before the assistant resumes readiness [SEEN-IN-SNIPPET].
   * *Post-TTS Safety Buffer:* Many production implementations inject a **200–500 ms safety buffer** immediately upon starting TTS playback to avoid false triggers during initial speaker onset [SEEN-IN-SNIPPET].
4. **Adaptive Turn Detection & History Truncation:**
   * *LiveKit Adaptive Interruption Model:* Evaluates acoustic features (spectral shape, pitch continuity, onset energy) over the first few hundred milliseconds to differentiate true barge-in from noise [SEEN-IN-SNIPPET].
   * *Context Truncation:* When a barge-in is validated, LiveKit and Pipecat immediately send a cancel frame to the TTS provider, flush the audio playback buffer, and **truncate the conversation history** so that only words actually rendered to the user are committed to the LLM context [SEEN-IN-SNIPPET].

### 2.3 Documentation Recommendations: Speakers vs. Headsets
* Framework documentation across Pipecat, LiveKit, and Deepgram explicitly directs developers to **use headphones during local development** [SEEN-IN-SNIPPET].
* Without acoustic isolation or specialized hardware DSPs, software running on development machines creates an immediate acoustic feedback loop, causing self-interruption spirals [SEEN-IN-SNIPPET].

---

## 3. Classical AEC Algorithms and Libraries (Usable on Pi 4/5 and Laptop)

### 3.1 Algorithm Comparison: WebRTC AEC3, AECm, SpeexDSP, FDAF, and Kalman

| Algorithm / Library | Mathematical Basis | Typical ERLE | Convergence Time | Double-Talk Handling | CPU on Raspberry Pi 4/5 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **WebRTC AEC3** (`libspa-aec-webrtc`) | Multi-delay linear adaptive filter + non-linear suppressor (NLP) [SEEN-IN-SNIPPET]. | **20 to 40 dB** (with NLP) [SEEN-IN-SNIPPET]. | **1.0 to 2.0 s** [SEEN-IN-SNIPPET]. | Excellent; NLP dynamically suppresses residual echo during single-talk and balances transparency during double-talk [SEEN-IN-SNIPPET]. | Moderate to high; viable on Pi 4/5 (Cortex-A72/A76) in C/C++, but can saturate a core in unoptimized Python wrappers [SEEN-IN-SNIPPET]. |
| **WebRTC AECm** (Mobile) | Low-complexity fixed-point adaptive filter [SEEN-IN-SNIPPET]. | **15 to 25 dB** [KNOWN-BUT-UNVERIFIED]. | **0.5 to 1.5 s** [KNOWN-BUT-UNVERIFIED]. | Moderate; prone to clipping or echo leakage during double-talk [SEEN-IN-SNIPPET]. | **Very low**; specifically optimized for low-power ARM architectures [SEEN-IN-SNIPPET]. |
| **SpeexDSP AEC** (`libspeexdsp`) | Multi-Delay Block Frequency Domain (MDF) adaptive filter [SEEN-IN-SNIPPET]. | **20 to 30 dB** (linear block) [SEEN-IN-SNIPPET]. | **0.5 to 1.0 s** (under stationary, ideal conditions) [SEEN-IN-SNIPPET]. | Sensitive; requires precise tuning of double-talk detector (DTD) and hangover timers to prevent divergence [SEEN-IN-SNIPPET]. | **Very low** (<5–10% of a single Pi 4 core at 16 kHz) [KNOWN-BUT-UNVERIFIED]. |
| **Normalized LMS / PBFDAF** | Partitioned Block Frequency-Domain Adaptive Filter [SEEN-IN-SNIPPET]. | **15 to 25 dB** (linear only, no NLP) [KNOWN-BUT-UNVERIFIED]. | **1.0 to 3.0 s** [KNOWN-BUT-UNVERIFIED]. | Degrades rapidly without explicit DTD; near-end speech acts as large error signal, destabilizing filter weights [SEEN-IN-SNIPPET]. | Low to moderate [KNOWN-BUT-UNVERIFIED]. |
| **Kalman-Based AEC** (FDKF) | Frequency-Domain Kalman Filter; state-space modeling of room impulse response [SEEN-IN-SNIPPET]. | **25 to 35 dB** (linear filter) [SEEN-IN-SNIPPET]. | **Rapid (<0.5 s)** [SEEN-IN-SNIPPET]. | Inherently robust; explicit error covariance matrices prevent coefficient divergence during double-talk without rigid DTDs [SEEN-IN-SNIPPET]. | High; matrix operations increase computational complexity per frequency bin [KNOWN-BUT-UNVERIFIED]. |

### 3.2 The First-Utterance Convergence Gap
* When an adaptive filter initializes, its filter weights are zero [KNOWN-BUT-UNVERIFIED].
* During the first **0.5 to 2.0 seconds** of playback, the filter must observe the cross-correlation between the reference and the microphone capture to adapt its weights [SEEN-IN-SNIPPET].
* **Measured Leak:** Before convergence, the echo is barely attenuated, leaking **10 to 15 dB above the room noise floor** [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED]. This directly matches your measurement (11 dB leak on first play) [SEEN-IN-SNIPPET]. If VAD is active during this initial window, it triggers an immediate false barge-in [SEEN-IN-SNIPPET].

### 3.3 Delay Estimation and Clock Drift
* **Delay Estimation:** AEC requires the reference signal to arrive at the canceller slightly ahead of or aligned with the microphone echo [SEEN-IN-SNIPPET]. If system latency shifts by more than the filter tail length (e.g., >64–128 ms for Speex, or >250 ms for WebRTC), the filter cannot cancel the echo [SEEN-IN-SNIPPET]. WebRTC AEC3 includes a dedicated delay estimator that dynamically scans cross-correlation peaks across buffers [SEEN-IN-SNIPPET].
* **Clock Drift:** When the output DAC (speaker) and input ADC (microphone) run on separate physical crystal oscillators (e.g., built-in laptop speakers + USB microphone), their sample rates drift apart by **10 to 100 parts per million (ppm)** [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED]. Over several minutes, this micro-drift shifts the delay alignment, causing the adaptive filter to de-converge and ERLE to plummet [SEEN-IN-SNIPPET].

### 3.4 PipeWire WebRTC Module & Noise Floor Suppression
* In PipeWire (`libpipewire-module-echo-cancel` with `aec/libspa-aec-webrtc`), WebRTC’s internal Noise Suppression (NS) and Non-Linear Processing (NLP) are enabled by default [SEEN-IN-SNIPPET].
* **Noise Floor Drop:** This explains why your room noise floor dropped by 12–17 dB [SEEN-IN-SNIPPET]. The NLP clamps spectral bins with low SNR. While this guarantees 0% false VAD triggers during steady-state playback, aggressive clamping can distort initial user speech consonants and degrade STT Word Error Rate (WER) [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED]. Disabling WebRTC AGC and tuning NS in PipeWire (`webrtc.noise_suppression = false`, `webrtc.gain_control = false`) isolates linear AEC [SEEN-IN-SNIPPET].

### 3.5 Python Bindings
* **`webrtc-audio-processing` / `aec-audio-processing`:** Python wrappers around Google WebRTC’s Audio Processing Module (APM) [SEEN-IN-SNIPPET]. Strict requirement: audio must be fed in **exact 10 ms chunks** (160 samples at 16 kHz) for both capture and reference (`process_reverse_stream`) [SEEN-IN-SNIPPET].
* **`speexdsp-python`:** Cython/SWIG wrapper around `libspeexdsp` [SEEN-IN-SNIPPET]. Exposes `EchoCanceller.create(frame_size, tail_length, sample_rate)` [SEEN-IN-SNIPPET].
* **`pyaec`:** Python package based on Rust (`aec-rs`) providing cross-platform precompiled binaries [SEEN-IN-SNIPPET].
* **Note on `py-webrtcvad`:** `py-webrtcvad` exposes *only* the Gaussian Mixture Model Voice Activity Detector from WebRTC; **it contains zero echo cancellation capabilities** [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].

---

## 4. Neural and Academic Approaches (Laptop Real-Time)

### 4.1 Microsoft AEC Challenge & Deep Residual Echo Suppression
* The annual ICASSP AEC Challenge (organized by Microsoft) benchmarks systems across synthetic and real-world noisy environments [SEEN-IN-SNIPPET].
* **Standard Hybrid Architecture:** Top-performing systems universally employ a two-stage hybrid pipeline:
  1. *Stage 1 (Linear AEC):* A classical linear adaptive filter (e.g., Partitioned Block Frequency-Domain Adaptive Filter, PBFDAF) models the linear echo path and provides baseline attenuation [SEEN-IN-SNIPPET].
  2. *Stage 2 (Neural RES):* A lightweight Convolutional Recurrent Network (CRN) or LSTM operates on the error signal and reference spectrum to suppress non-linear speaker distortions and residual echo [SEEN-IN-SNIPPET].

### 4.2 DeepVQE (Deep Voice Quality Enhancement)
* **Citation:** Microsoft (ICASSP 2023, arXiv:2302.13327) [SEEN-IN-SNIPPET].
* **Architecture:** Jointly solves Acoustic Echo Cancellation (AEC), Noise Suppression (NS), and Dereverberation (DR) in a unified U-Net model with residual blocks and RNN temporal layers [SEEN-IN-SNIPPET].
* **Alignment Mechanism:** Uses a cross-attention alignment block that softly aligns the microphone input with the reference signal, eliminating the need for a separate hand-engineered delay estimator [SEEN-IN-SNIPPET].
* **Feasibility:** Quantized ONNX/GGUF models can run in real time on modern x86 laptop CPUs (<15 ms latency per frame) [SEEN-IN-SNIPPET], but are too heavy for an embedded Pi 4 without hardware acceleration [KNOWN-BUT-UNVERIFIED].

### 4.3 Personalized VAD and Target-Speaker VAD (TS-VAD)
* **Concept:** Instead of predicting binary speech/non-speech, Personalized VAD (pVAD) conditions frame classification on a pre-enrolled user voice embedding (d-vector / x-vector) [SEEN-IN-SNIPPET].
* **Barge-In Application:** Classifies incoming frames into: *Target Speaker Speech*, *Non-Target Speech*, or *Noise/Echo* [SEEN-IN-SNIPPET]. Even if residual echo leaks through at -6 dB, the model rejects it because the acoustic features match the agent’s synthetic voice profile rather than the enrolled user’s vocal tract [SEEN-IN-SNIPPET].

### 4.4 Implicit AEC and Device-Directed Speech Detection (DDSD)
* **Implicit AEC (Amazon Alexa AI):** Cornell, Balestri, Sénéchal (*"Implicit Acoustic Echo Cancellation for Keyword Spotting and Device-Directed Speech Detection"*, arXiv:2111.10639) [SEEN-IN-SNIPPET]. Proposes feeding the reference channel directly into the downstream classification network without an explicit subtraction stage, training the neural network to ignore the reference audio [SEEN-IN-SNIPPET].
* **Barge-In Verification:** Deshmukh et al. (*"Barge-In Verification Using Spoken Language and Lexically Infused Speech Representations"*, arXiv:2211.13280) trains classifiers to distinguish accidental barge-in triggers from genuine user commands [SEEN-IN-SNIPPET].

### 4.5 Text-Based Self-Echo Rejection (Semantic Guardrail)
* **Mechanism:** When ASR yields a streaming transcript, a sliding-window fuzzy string matcher (e.g., Levenshtein ratio > 0.85 or token overlap) compares the recognized hypothesis against the text segments dispatched to TTS over the preceding 0.5–2.0 seconds [SEEN-IN-SNIPPET].
* **Limitation:** As you observed, this filters out runaway feedback loops (cutting loops 5–8x) [SEEN-IN-SNIPPET], but **cannot prevent the initial false interruption** because the TTS stream has already been stopped by the time ASR produces words [SEEN-IN-SNIPPET].

---

## 5. Acoustic and Hardware Practice: USB Speakerphones & Headsets

### 5.1 USB Speakerphone Hardware AEC Internals
USB speakerphones (Jabra Speak, Poly Sync, Anker PowerConf, eMeet, Seeed ReSpeaker) contain dedicated DSP SoCs (such as the XMOS XVF3510 / XVF3800 or Cirrus Logic audio processors) [SEEN-IN-SNIPPET].

* **Own-Speaker Cancellation:** Hardware AEC operates **strictly on audio played through the speakerphone's own physical loudspeaker** [SEEN-IN-SNIPPET]. The DSP chip sits directly on the audio bus between the USB DAC and the physical driver, tapping the digital reference internally with zero software jitter and known hardware latency [SEEN-IN-SNIPPET].
* **Host Playback via ALSA / PipeWire:** When the host OS configures the speakerphone as its default output sink (e.g., via ALSA `hw:X,0` or PipeWire) and plays audio through it, the internal DSP automatically receives the reference signal and strips it from the microphone stream [SEEN-IN-SNIPPET].
* **The Fatal Trap (Split Audio Routing):** If the host plays agent audio out of the laptop speakers while using the USB speakerphone only as a microphone, the speakerphone’s internal DSP receives no reference signal on its USB bus [SEEN-IN-SNIPPET]. In this misconfigured state, **hardware AEC cannot function**, and the speakerphone behaves like a raw, uncancelled microphone [SEEN-IN-SNIPPET].

### 5.2 Market Devices, Specifications, and Prices

| Device | Typical Price | Mic Array & Pickup | Stated AEC & Duplex Capabilities | Hardware / Chipset Details |
| :--- | :--- | :--- | :--- | :--- |
| **Seeed Studio ReSpeaker XVF3800** | ~$60 – $80 [KNOWN-BUT-UNVERIFIED] | 4-mic linear/circular array [SEEN-IN-SNIPPET]. | Full-duplex AEC, noise suppression, de-reverberation, beamforming [SEEN-IN-SNIPPET]. | XMOS XVF3800 voice processor [SEEN-IN-SNIPPET]; provides automated delay estimation ($\pm150$ ms) [SEEN-IN-SNIPPET]. |
| **Anker PowerConf S3 / S330** | ~$60 – $120 [SEEN-IN-SNIPPET] | 6-mic 360° array [SEEN-IN-SNIPPET]. | Full-duplex communication, automated AEC, noise reduction [SEEN-IN-SNIPPET]. | Custom DSP SoC [SEEN-IN-SNIPPET]; internal battery on S3, wired USB on S330 [SEEN-IN-SNIPPET]. |
| **Poly Sync 20 / 20+** | ~$120 – $160 [SEEN-IN-SNIPPET] | 3-mic steerable array (2m range) [SEEN-IN-SNIPPET]. | Full-duplex audio, dynamic noise & echo reduction [SEEN-IN-SNIPPET]. | Certified for Microsoft Teams / Zoom; hardware DSP [SEEN-IN-SNIPPET]. |
| **Jabra Speak2 55 / 75** | ~$140 – $280 [SEEN-IN-SNIPPET] | 4 beamforming MEMS mics [SEEN-IN-SNIPPET]. | Full-duplex audio, advanced acoustic echo cancellation, voice level normalization [SEEN-IN-SNIPPET]. | Upgraded wideband DSP pipeline [SEEN-IN-SNIPPET]. |
| **eMeet M0 / M2** | ~$40 – $90 [KNOWN-BUT-UNVERIFIED] | 4-mic array [KNOWN-BUT-UNVERIFIED]. | Basic full-duplex AEC, VoiceIA algorithm [KNOWN-BUT-UNVERIFIED]. | Entry-level commercial DSP [KNOWN-BUT-UNVERIFIED]. |

* **Vendor Specifications:** Commercial conference speakerphones generally specify **full-duplex compliance** (ITU-T G.168 / P.340 standards) [KNOWN-BUT-UNVERIFIED]. Combined linear ERLE and non-linear residual suppression achieve **>35 to 50 dB** total echo attenuation in steady-state operation [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].

### 5.3 What a Headset Changes
* **Acoustic Isolation:** A headset introduces physical separation between the acoustic transducer (earpad/in-ear driver) and the microphone capsule [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].
* **Acoustic Coupling Loss:** The acoustic transfer gain drops by **40 to 60+ dB** compared to open speakers [KNOWN-BUT-UNVERIFIED].
* **VAD Impact:** Bleed from a headset sits well below typical room ambient noise floor levels (-40 to -50 dBFS) [KNOWN-BUT-UNVERIFIED]. Silero VAD will not trigger on headset bleed under ordinary operating volumes [SEEN-IN-SNIPPET].

---

## 6. Practical Recipe and Architecture for Your Three Setups

### 6.1 State Machine: The "Duck-Then-Stop" Policy
To eliminate self-interruption without losing responsiveness, do not treat instantaneous VAD triggers as an immediate hard stop. Implement a **staged interruption state machine**:

```
[Agent Speaking: Audio Playing]
       │
       ▼ (Silero VAD flags frame > 0.6 probability)
[State: Suspected Barge-In]
       ├─► Instantly duck TTS playback volume by 15 dB (latency <20 ms)
       ├─► Start confirmation guard timer (window: 200 ms)
       │
       ├──► (Speech ends before 200 ms -> e.g., click/transient)
       │       └─► Restore playback volume smoothly (anti-clipping ramp)
       │
       └──► (Speech sustains >= 200 ms -> Confirmed Barge-In)
               ├─► Send immediate Flush/Stop to audio output buffer
               ├─► Cancel pending LLM tokens
               └─► Truncate conversation history to words played before cutoff
```

### 6.2 Recommended Setup-by-Setup Configuration

```
                     ┌────────────────────────────────────────────────────────┐
                     │                  THREE TARGET SETUPS                   │
                     └────────────────────────────────────────────────────────┘
                                 │                  │                  │
         ┌───────────────────────┘                  │                  └───────────────────────┐
         ▼                                          ▼                                          ▼
┌───────────────────────────────┐  ┌─────────────────────────────────┐  ┌───────────────────────────────┐
│           SETUP 1             │  │             SETUP 2             │  │            SETUP 3            │
│       Laptop Built-in         │  │     Raspberry Pi 4/5 + USB      │  │        Any Platform +         │
│         Mic / Speakers        │  │          Speakerphone           │  │            Headset            │
├───────────────────────────────┤  ├─────────────────────────────────┤  ├───────────────────────────────┤
│ • PipeWire WebRTC AEC Module  │  │ • Hardware AEC on Speakerphone  │  │ • No AEC Module Needed        │
│   (webrtc.noise_suppr=false)  │  │ • ALSA Shared USB Endpoint      │  │ • Direct Mic Capture          │
│ • Initial Warm-up Playback    │  │ • Python: Raw Silero VAD        │  │ • Raw Silero VAD              │
│ • 250ms Start Grace Period    │  │ • 150ms Speech Guard Time       │  │ • 100ms Speech Guard Time     │
│ • 15dB Duck-then-Stop Policy  │  │ • 15dB Duck-then-Stop Policy    │  │ • Immediate Hard Interruption │
│ • Streaming ASR Text Match    │  │ • No Neural AEC on Pi           │  │ • Zero Feedback Leakage       │
└───────────────────────────────┘  └─────────────────────────────────┘  └───────────────────────────────┘
```

#### Setup 1: Laptop with Built-in Speakers and Microphone
1. **AEC Layer:** Configure PipeWire’s WebRTC AEC module (`libspa-aec-webrtc`) [SEEN-IN-SNIPPET]. In `99-echo-cancel.conf`, set `webrtc.extended_filter = true` and `webrtc.gain_control = false` [SEEN-IN-SNIPPET]. Set `webrtc.noise_suppression = false` if you observe room noise degradation that hurts STT accuracy [SEEN-IN-SNIPPET].
2. **Convergence Warm-Up:** When initializing the voice agent, play a 500 ms sub-audible or low-volume calibration tone/chime before active dialogue begins, allowing the linear filter to converge its initial delay estimate [KNOWN-BUT-UNVERIFIED].
3. **Guardrails:**
   * Apply a **250 ms grace period** at the start of any new TTS turn where VAD barge-in is masked or requires higher confidence (>0.85) [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].
   * Apply **duck-then-stop**: duck by 15 dB upon initial VAD trigger; require 200 ms continuous speech before purging TTS buffers [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].
   * Retain streaming ASR fuzzy-matching against active TTS text as a secondary loop-breaker [SEEN-IN-SNIPPET].

#### Setup 2: Raspberry Pi 4/5 with USB Speakerphone
*(Strict rule: No neural models on the Pi except Silero VAD)*
1. **AEC Layer:** **Hardware AEC strictly inside the USB speakerphone** (e.g., Poly Sync 20 or Seeed ReSpeaker XVF3800) [SEEN-IN-SNIPPET]. Configure your Python application to open the speakerphone's USB ALSA card for **both audio playback and microphone capture** [SEEN-IN-SNIPPET]. The hardware DSP handles linear cancellation, delay tracking, and residual suppression with 0% Pi CPU load [SEEN-IN-SNIPPET].
2. **VAD Layer:** Run standard Silero VAD ONNX runtime on the cleaned microphone stream [SEEN-IN-SNIPPET].
3. **Guardrails:**
   * Set `min_speech_duration = 150–200 ms` in the Silero aggregator to ignore residual double-talk clicks [SEEN-IN-SNIPPET].
   * Implement duck-then-stop in Python audio output: when Silero flags speech, multiply the outgoing audio buffer by 0.2 (-14 dB) for 200 ms; if speech persists, drop the buffer entirely and clear the pipeline [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].

#### Setup 3: Headset (Any Platform)
1. **AEC Layer:** None required. Disable software AEC and bypass PipeWire echo-cancel modules [SEEN-IN-SNIPPET].
2. **VAD Layer:** Raw Silero VAD with `min_speech_duration = 100 ms` [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].
3. **Guardrails:** Direct interruption without ducking; instantaneous TTS buffer flush [SEEN-IN-SNIPPET].

### 6.3 Remaining Failure Modes
1. **Initial Utterance Leak (Cold Start):** Software adaptive filters have an ERLE near 0 dB during the first 500–1500 ms [SEEN-IN-SNIPPET]. Unattenuated speech leaks into the VAD during the first syllable [SEEN-IN-SNIPPET].
2. **Physical Volume Step Changes:** If the user adjusts the hardware speaker volume knob or OS slider mid-utterance, the acoustic transfer function changes instantly, breaking filter convergence and causing a 1–2 second echo burst [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].
3. **Bluetooth HFP Profile Latency:** Bluetooth headsets or speakerphones operating in Hands-Free Profile (HFP) introduce dynamic packet batching latencies (100–250 ms) and poor mSBC/CVSD codec audio quality, disrupting software AEC delay estimators [KNOWN-BUT-UNVERIFIED].
4. **Independent USB Crystal Clock Drift:** Using a USB microphone with built-in laptop speakers causes clock slippage over time, forcing adaptive filters to re-estimate delays periodically [SEEN-IN-SNIPPET].
5. **Non-linear Laptop Chassis Resonances:** Cheap laptop speaker enclosures rattle at specific resonant frequencies; linear filters cannot cancel these harmonic distortions, letting residual buzz trigger the VAD unless aggressive non-linear suppression is active [SEEN-IN-SNIPPET].

---

## (c) Ranked Shortlist of 3–5 Concrete Things to Try

### 1. Route Full-Duplex Audio Through a Dedicated USB Hardware Speakerphone (Setup 2)
* **What to do:** Connect a hardware AEC speakerphone (e.g., Poly Sync 20 or Seeed ReSpeaker XVF3800) to the Pi. Route both ALSA playback and microphone recording to that device. In Python, run only Silero VAD.
* **How to Measure:**
  * *Self-Trigger Rate:* Play 100 random assistant TTS utterances at normal operating volume (65–70 dBA) with no user speaking. Target: **0% self-interruptions**.
  * *Time to Silence:* Measure latency from user interruption onset to speaker silence using a synchronized test audio track. Target: **<250 ms**.
  * *STT Accuracy:* Compute Word Error Rate (WER) on standard test sentences spoken by a user while the speakerphone is actively playing audio (double-talk).

### 2. Implement the "Duck-Then-Stop" Staged Interruption Controller (Setups 1 & 2)
* **What to do:** In your Python audio output engine, do not instantly abort playback on the first Silero VAD speech frame. When VAD crosses 0.6 probability, immediately attenuate playback volume by 15 dB (ducking) and start a 200 ms confirmation timer. If VAD speech sustains $\ge$200 ms, issue a hard cancel and flush buffers. If speech ceases before 200 ms, ramp volume back up.
* **How to Measure:**
  * *False Interruption Loops:* Run 50 continuous multi-turn conversations where the agent outputs long paragraphs. Verify that runaway self-interruption loops are reduced to **0**.
  * *Barge-In Usability:* Measure user perception and failure rate of intentional barge-in attempts.

### 3. Configure PipeWire WebRTC AEC with Denoising Isolation (Setup 1)
* **What to do:** In `pipewire.conf.d/99-echo-cancel.conf`, isolate WebRTC AEC parameters: set `webrtc.extended_filter = true`, `webrtc.gain_control = false`, and test `webrtc.noise_suppression = false` to eliminate the 12–17 dB room noise floor drop.
* **How to Measure:**
  * *Recognition Accuracy (WER):* Record 50 spoken test utterances with AEC active vs. AEC disabled in quiet and ambient noise conditions. Measure if the 12–17 dB noise suppression altered Whisper/Vosk/STT transcript WER.
  * *Residual Leak dB:* Measure RMS power in the microphone stream during agent playback before and after convergence. Target: Residual leak $\le$ 3 dB above ambient floor.

### 4. Implement a 300 ms Start-of-Turn Grace Period and ASR Cross-Matching (Setup 1)
* **What to do:** Mask or elevate the VAD threshold (to 0.9) during the first 300 ms of any new agent utterance to cover the initial WebRTC convergence window. Pair this with a streaming ASR token comparison that ignores matches against the active TTS sentence window.
* **How to Measure:**
  * *First-Utterance Self-Trigger Rate:* Specifically measure false stops occurring within the first 1.0 second of playback. Compare baseline vs. 300 ms grace period. Target: **0 initial stops**.

---

## (d) What Could NOT Be Found

1. **Exact Internal Interruption Thresholds for Proprietary Cloud Assistants:** Neither Apple nor Google publish exact production millisecond thresholds or energy ratios for Siri or Google Assistant barge-in detection; their architectures rely on proprietary, continuous on-device machine learning models evaluated end-to-end [KNOWN-BUT-UNVERIFIED].
2. **Published ERLE Figures for Anker PowerConf and eMeet Speakerphones:** Consumer and business peripheral manufacturers (Anker, eMeet, Jabra) advertise "full-duplex" and "noise reduction" but do not publish formal acoustic lab test reports with standardized ITU-T G.168 ERLE decibel numbers in their public datasheets [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED].
3. **Public Pure-Python Kalman AEC Implementations with Production C-Extensions:** While Frequency-Domain Kalman Filter (FDKF) AEC is extensively documented in academic literature (e.g., RWTH Aachen research), no production-grade, packaged Python wheel (with SIMD/C acceleration) was found on PyPI matching WebRTC APM's availability [SEEN-IN-SNIPPET], [KNOWN-BUT-UNVERIFIED]. Available open-source Kalman AEC repositories are predominantly unoptimized MATLAB or NumPy educational scripts unsuitable for low-latency streaming on a Raspberry Pi [KNOWN-BUT-UNVERIFIED].
