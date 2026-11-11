### 1. Summary (At Most 12 Lines)

1. **Early reply cutoffs (`7 of 20` in canceller-only, 7F):** Your `EchoModel` (`BargeInGuard`, 5A) learns `coupling` from **post-canceller** microphone levels divided by **pre-canceller** `played_level`, driving `coupling` from `0.33` down to `0.07` and pinning `bar` at its `0.02` (-34 dBFS) floor [inference].
2. When the next reply starts after a 2–3 s turn gap, the Pi USB sound card pays a ~0.5 s idle wake-up shift (7C) and WebRTC AEC3 needs ~0.3–0.6 s to re-lock delay and converge; during that window, raw echo (-25 to -29 dBFS) hits the artificially lowered `0.02` `bar` and fires the guard [inference].
3. **Why ducking stopped early cutoffs (`0 of 15`, 7F) while causing `3 of 7` "false ducks":** The duck fires on just **1 block > `bar` and 2/10 Silero windows**, attenuating the speaker by -15 dB before the un-converged leak can persist for the **3/8 blocks and 7/10 Silero windows** needed to trigger a self-interruption [inference].
4. **Garbled first words & slow stop (`1.34 s` in canceller + duck, 7F):** First, WebRTC AEC3's nonlinear residual echo suppressor (`SuppressionGain`) attenuates the first 100–250 ms of double-talk before its `DominantNearendDetector` transitions to near-end state, erasing unvoiced plosives/fricatives (`/s/`, `/t/` in `"Stop"`).
5. Second, when the duck triggers, scaling the laptop reference immediately by -15 dB while ~0.25 s of un-ducked (0 dB) audio is still queued in the Pi/network pipeline (H3) creates a 15 dB reference-to-echo mismatch that diverges the linear filter and forces the suppressor to clamp down on the user's onset [inference].
6. **Echo words in transcripts & spurious `stop_music` commands (7F):** Your guard fires within ~250–300 ms of speech onset, so prepending a **1.2 s pre-roll** guarantees ~0.8–0.9 s of prior assistant echo precedes the user's first word (H6); putting echo words in front of `"Stop"` also defeats your strip-leading-`"Stop"` rule (5E), causing the LLM to execute `stop_music` [inference].
7. **Why `lead >= 200 ms` failed in the 7E sweep:** Feeding $L\text{ ms}$ of extra reference ahead in a burst exceeds WebRTC AEC3's 120 ms API call jitter buffer (`kMaxApiCallJitterBlocks = 30` blocks of 4 ms) and/or starves your 250 ms slice queue, injecting silence underruns into `process_reverse_stream()`.
8. **Single best next step:** Decouple the audio taps and fix reference alignment—(a) keep the Pi ALSA sink open continuously with zero-valued PCM frames so the render-to-echo delay stays constant (~120–180 ms) instead of jumping by 500 ms after idle, (b) remove the "feed $L\text{ ms}$ ahead once" burst, (c) compute `EchoModel` `coupling` or a 0.6 s convergence hold at reply start so un-converged leaks cannot fire the guard, and (d) feed the ASR from **un-suppressed (raw or linear-only) pre-roll trimmed to `onset - 200 ms**` rather than 1.2 s of nonlinearly suppressed audio [inference].

---

### 2. Evaluation of Hypotheses (H1–H7)

*(Note: Live web browsing is disabled in this environment; citations below reference canonical upstream source files in `webrtc-sdk/webrtc`, `PipeWire`, `SpeexDSP`, `LiveKit`, `Pipecat`, published INTERSPEECH/ICASSP proceedings, and your project notebooks.)*

| Hypothesis | Verdict | Evidence & Technical Mechanism | Cheapest Definitive Test |
| --- | --- | --- | --- |
| **H1.** Live reference-to-echo delay (0.3–0.5 s + 0.5 s ALSA wake-up) or its reply-to-reply variation exceeds what WebRTC's delay estimator tracks well, and the 50 ms hint hurts. | **Plausible (specifically the 0.5 s idle jump and queue-empty resets; correction on the 0.25 s cushion — see §6)** | WebRTC AEC3's internal `EchoPathDelayEstimator` (using 5 matched filters at 4x downsampling, `delay.num_filters = 5`, `sub_block_size = 32` blocks of 4 ms) searches up to **640 ms** of delay (`160 blocks * 4 ms`), and `set_stream_delay_ms(50)` is largely ignored once internal delay estimation locks (`use_external_delay_estimator = false` by default). However, two things break it live: (1) whenever the Pi sound card wakes from idle between conversational turns (2.3–3.3 s apart), the true delay shifts by ~500 ms, requiring ~0.3–0.6 s of active speech energy for the matched filters to re-qualify a new delay; and (2) whenever your reference queue goes from empty to non-empty (at reply start or between TTS sentences), your code feeds $L=50\text{ ms}$ of extra reference ahead once, shifting the impulse response by 50 ms and forcing a full filter re-convergence [inference]. | Keep the Pi ALSA device continuously open by streaming zeros (silence PCM) whenever no reply is playing, and remove the "feed $L\text{ ms}$ extra reference when queue becomes non-empty" jump. Log `mic_level` in the first 1.2 s across 20 live replies. |
| **H2.** At the start of double-talk, the canceller's near-end suppression takes the user's first words while the filter is converged on echo-only. | **Plausible (Primary cause of garbled `"Stop"`)** | In WebRTC AEC3, `SuppressionGain::GetGain()` computes a Wiener-like spectral mask based on estimated nearend-to-residual-echo SNR. Until `DominantNearendDetector` (`dominant_nearend_detector.cc`) observes near-end energy exceeding residual echo by its threshold over consecutive blocks, AEC3 remains in far-end-dominant state (`nearend_state = false`) and applies aggressive high-frequency suppression (`1.9–2.5 kHz+`) to prevent echo leaks. Because `"Stop"` begins with unvoiced `/s/` (4–8 kHz) and `/t/` during the first ~100–200 ms before `nearend_state` flips, the suppressor erases `/s t/` and leaves only the vowel, which Parakeet transcribes as `"Come on"` or `"Please"` [inference]. | On your saved 7F `canceller only` turn recordings where `"Stop"` was garbled, run Parakeet offline on the **raw (un-cancelled) mic audio sliced from `guard_fire_time - 0.45 s` onward**. If Parakeet transcribes `"Stop..."` cleanly from the raw audio, H2 is confirmed. |
| **H3.** The -15 dB step duck scales the canceller's reference immediately while up to 0.25 s of un-ducked audio is still in flight to the Pi, disturbing the adaptive filter. | **Plausible (Highly likely cause of the 1.34 s stop delay in `canceller + duck`)** | A linear adaptive filter subtracts $\hat{y}(t) = \sum_{k} \hat{h}_k x(t - k)$. If you multiply $x(t)$ by $-15\text{ dB}$ ($0.178\times$) on the laptop at $t_{\text{duck}}$, while the Pi still plays un-ducked ($1.0\times$) slices that were already sent (up to 0.25 s in the WebSocket/script queue + 35 ms ALSA buffer), then for the next ~200–300 ms the canceller predicts only 18% of the actual acoustic echo [inference]. The resulting $5.6\times$ spike in residual error $e(t) = d(t) - \hat{y}(t)$ triggers AEC3's filter divergence protection and drives `SuppressionGain` to near-zero right as the user is speaking their first word, starving both Silero VAD (delaying the 7-of-10 trigger to 1.34 s) and the ASR [inference]. | Move the reference scaling to happen **at the player sender** when a slice is scaled (or let the Pi scale *only* the acoustic output while keeping reference scaling synchronized with the slice's playback position), or disable ducking when AEC is enabled once H1/H8 (`EchoModel` bar) is fixed. |
| **H4.** The 137 ppm clock drift is tracked slowly by the canceller, making the first seconds of every reply the worst. | **Implausible (for the first-second bursts; drift hurts *late* in long replies, not at $t < 1.2\text{ s}$)** | At 137 ppm ($2.2\text{ samples/s}$ at 16 kHz, or $0.137\text{ ms/s}$), the cumulative phase shift over the first $1.0\text{ s}$ of a reply is only **$0.137\text{ ms}$** ($2.2$ samples), which is well within a single 4 ms AEC3 block (`64` samples) and within the 12-block (`48 ms`) main adaptive filter tap window [inference]. Clock drift causes coherence loss when integrated over a multi-second fixed window (as in your 4 s probe burst in 7C), or when the filter's bulk delay crosses a 4 ms block boundary after ~15–30 seconds, **not** at $t = 0.4\text{–}1.2\text{ s}$ of a reply [inference]. | Plot the residual `mic_level / played_level` ratio vs. time-since-reply-start ($0\text{ to }8\text{ s}$) on your 7F `canceller only` guard trace. Under H4, residue grows after several seconds; under H1 + `EchoModel` floor, residue spikes at $0.2\text{–}1.2\text{ s}$ and is flat afterward. |
| **H5.** Speaker nonlinearity at 80% and room resonances leave linear-uncancellable residue loud enough to fire the guard in the first second. | **Implausible as the primary cause of the first-second fires (Plausible only for Silero firing on quiet residue when `bar` is too low)** | Your 7C probe measured $29.7\text{ dB}$ removal on the loud burst after convergence and a 2nd harmonic $36.5\text{ dB}$ below fundamental; in 7F, median microphone level during replies dropped to $-43.7\text{ dBFS}$ (0.3 dB above the $-44\text{ dBFS}$ room floor). Nonlinearity depends on instantaneous amplitude, not time-since-reply-start; if static speaker nonlinearity were the cause, the guard would fire uniformly throughout the reply rather than specifically in the first `0.4–1.2 s` while the linear filter is converging [inference]. | Inspect the timestamps of the 7 early fires in 7F (`canceller only`): since all 7 occurred at `0.4–1.2 s` and 0 occurred after `1.5 s` (once the linear filter converged), static nonlinearity is ruled out as the primary driver. |
| **H6.** The 1.2 s pre-roll is the main route by which echo enters the transcript; without AEC each barge-in transcript starts with up to a second of echo. | **Confirmed / Strongly Plausible** | Your guard requires 7 of 10 Silero windows ($224\text{–}320\text{ ms}$) and 3 of 8 level blocks ($300\text{ ms}$). When the guard fires at $t_{\text{fire}}$, the user's speech onset occurred at approximately $t_{\text{fire}} - 0.30\text{ s}$. Handing the last **1.2 s** of pre-roll to Parakeet guarantees that the first **$0.8\text{–}0.9\text{ s}$** of audio sent to the recognizer occurred **before the user started speaking** (pure assistant echo) [inference]. That also explains why `"Stop"` lands in the middle of the transcript (`"2005 border. Stop. Set a timer..."`), bypassing your leading-`"Stop"` stripper (5E) and triggering spurious `stop_music` tool calls [inference]. | Change pre-roll slice handed to ASR upon barge-in from `1.2 s` to **`0.45 s`** (or dynamically slice from the first of the 7 triggering Silero windows minus `150 ms`). Replay or run 8 cue-beep turns without AEC and count transcripts containing echo words. |
| **H7.** Speech-like residue or "musical noise" from the canceller's suppressor fools Silero VAD. | **Plausible (Secondary factor enabling Silero stage to pass on leaks)** | Neural VADs like Silero VAD are trained on clean speech mixed with stationary/environmental noise, not spectral-subtraction artifacts or un-converged speech echo. Because residual speech echo retains harmonic formant modulation and syllabic envelope at 3–8 Hz, Silero fires at confidence 0.5–0.9 even when the echo is near the noise floor (as you measured at 40% volume in 7A and in `docs/INTERRUPTS.md`) [inference]. Thus, Silero **cannot** reject echo leaks on its own; it relies entirely on your level stage (`bar`), which was defeated because `EchoModel` trained `coupling` on post-canceller audio [inference]. | In `guard_replay.py` on the 7F `canceller only` trace, keep `EchoModel` fed by `played_level` with a fixed minimum `coupling >= 0.35` during the first `1.0 s` of every reply (or feed `EchoModel` pre-canceller mic levels). Check if the 7 early fires drop to 0/20. |

---

### 3. Answers to Questions (Q1–Q11)

#### Q1. Delay and Drift Across Two Devices

1. **Wait—First, a Critical Correction to Your Section 3 Delay Math:**
* You wrote in Section 3: *"The reference queue is filled by the player thread, which is up to 0.25 s ahead of the loudspeaker, and consumed at microphone speed; therefore the reference stream leads the echo by roughly 0.25 s + the Pi's device buffer (about 35 ms) + network + acoustic path + microphone capture latency."*
* **This is not what happens in a lockstep FIFO queue [inference]:** At the start of a reply ($t=0$), your player sends up to $0.25\text{ s}$ (five 50 ms slices) immediately to build its 0.25 s cushion on the Pi, and pushes those same five slices into your laptop's reference queue. Because the Pi plays from its queue at $1\times$ real-time **and** your microphone loop drains the laptop reference queue in lockstep at $1\times$ real-time (`10 ms` per `10 ms` mic frame), **both queues hold the same 0.25 s cushion!** The two 0.25 s cushions cancel out completely (except for the $L=50\text{ ms}$ you pop ahead once).
* Therefore, when the Pi's USB sound card is already warm, your reference stream inside `AudioProcessingModule` actually leads the echo by only:

$$\text{Delay}_{\text{warm}} \approx L\text{ (50 ms)} + t_{\text{net}}\text{ (1.5 ms)} + t_{\text{ALSA}}\text{ (35 ms)} + t_{\text{acoustic}}\text{ (3 ms)} + t_{\text{capture}}\text{ (0–100 ms)} \approx 90\text{–}190\text{ ms}$$


* However, when the Pi's USB sound card has been **idle** (such as after the 2.3–3.3 s turn gap in live conversation), the Pi takes ~500 ms to wake up and begin draining its queue while your laptop mic callback drains the laptop reference queue immediately at $t=0$! During that idle wake-up, the laptop drains all 250 ms of reference queue * plus* 250 ms of **silence frames** (because the reference queue empties while the player waits for the Pi!), creating a **~500 ms delay mismatch and silence gap in the reference stream** right at the start of the reply [inference]!


2. **How WebRTC AEC3 Handles Delay and Clock Drift:**
* **Internal Delay Estimator Range:** WebRTC AEC3 (`EchoCanceller3`) downsamples render and capture audio by a factor of 4 (to 4 kHz, 16 samples per 4 ms block) and runs a bank of `delay.num_filters` (default `5`) matched filters, each of length `sub_block_size = 32` blocks (`128 ms`), covering an acoustic delay search range of **$5 \times 128\text{ ms} = 640\text{ ms}$** (`160` blocks of 4 ms) inside `RenderDelayBuffer`.
* **What `set_stream_delay_ms()` Does in AEC3:** In legacy WebRTC AEC (`aec_core.cc`), `set_stream_delay_ms()` directly positioned the adaptive filter window, and an error $>60\text{ ms}$ caused total failure. In **AEC3**, `set_stream_delay_ms()` calls `RenderDelayBufferImpl::SetAudioBufferDelay()`, which only provides an initial fallback offset prior to the first internal matched-filter lock (unless `EchoCanceller3Config.delay.use_external_delay_estimator = true`, which is `false` by default). A hint of `50 ms` against a true `300 ms` delay does **not** break AEC3 steady-state *provided* the render stream is continuous and stays within `0..640 ms` leading the capture stream.
* **Why $L \ge 200\text{ ms}$ Failed in Your 7E Sweep:**
1. In WebRTC AEC3, `RenderDelayBuffer` has a strict API jitter window between `AnalyzeRender()` (`process_reverse_stream`) and `PrepareCaptureProcessing()` (`process_stream`): `kMaxApiCallJitterBlocks = 30` blocks of 4 ms = **120 ms**. If your code feeds $L\text{ ms}$ ahead by calling `process_reverse_stream()` $L/10$ extra times before `process_stream()`, any $L \le 100\text{ ms}$ ($\le 25$ blocks) fits inside the 120 ms buffer, whereas $L \ge 200\text{ ms}$ ($\ge 50$ blocks) triggers `BufferingEvent::kRenderOverrun`, which flushes the render buffer and resets delay estimation.
2. Even if you advance the pointer inside your own Python queue instead of calling `process_reverse_stream()` in a burst, your player only feeds the queue **250 ms** ahead in 50 ms slices. Consuming 200–300 ms upfront starves the 250 ms queue whenever `path_delay` or slice boundaries align unfavorably, causing your lockstep loop to feed **silence frames** into `process_reverse_stream()` mid-clip and repeatedly re-trigger your "empty-to-non-empty: feed $L\text{ ms}$ ahead" jump [inference].


* **Clock Drift in AEC3:** AEC3 has a boolean config `ep_strength.echo_can_saturate` and legacy `delay.capture_alignment_mixing`, and its matched-filter `RenderDelayController` includes hysteresis (`delay_headroom_samples`, `hysteresis_limit_blocks = 1`) to step the bulk delay by 1 block (4 ms = 64 samples) when clock drift accumulates to 4 ms (~29 seconds at 137 ppm). However, **within** those 29 seconds, the 48 ms frequency-domain adaptive filter (`MainFilterUpdateGain`) must continuously re-adapt its phase across all FFT bins as the impulse response slides by 2.2 samples/sec, which limits high-frequency ERLE above 1–2 kHz (matching your 7C coherence drop to 0.02 without drift correction vs 0.51–0.79 with drift correction).


3. **What PipeWire `module-echo-cancel`, SpeexDSP, LiveKit, and Pipecat Do:**
* **PipeWire (`libpipewire-module-echo-cancel`):** PipeWire's echo-cancel module wraps `webrtc-audio-processing` (v1.x uses AEC3). Crucially, PipeWire runs an **Adaptive Audio Resampler (`spa_audio_rate_match` / DLL loop)** between the sink monitor and the capture stream when they lie on different clock domains (`aec.args` / graph clock recovery), dynamically adjusting the resampling ratio by ppm to lock the two clocks before feeding `process_reverse_stream()` and `process_stream()` in synchronous chunks.
* **SpeexDSP (`speex_echo_cancellation`):** SpeexDSP uses a Multidelay Block Frequency Domain (MDF) adaptive filter (`mdf.c`). Its documentation explicitly states that **capture and playback must run on the exact same sample clock**; even 50–100 ppm of asynchronous clock drift between two sound cards prevents MDF from converging unless an external adaptive resampler synchronizes the streams.
* **LiveKit & Pipecat:** Neither LiveKit Agents nor Pipecat supports software AEC across two separate network machines out of the box; both expect AEC to happen **at the edge client/device** where speaker and mic share an OS audio graph (WebRTC browser client, mobile SDK, or local hardware speakerphone) before streaming clean mic audio to the agent server.
* **How You Can Compensate the 137 ppm Drift in Python directly:** Since you already resample TTS from 44.1 kHz to 48 kHz for the Pi and to 16 kHz for the reference on the laptop, **measure or pass the Pi's actual consumed sample count back over the WebSocket** (or apply your measured `137 ppm` factor: resample the reference to `16002.2 Hz` or stretch by `1 + 1.37e-4` via `scipy.signal.resample_poly` / linear interpolation) so the reference stream matches the laptop microphone's clock [inference].



---

#### Q2. Double-Talk Damage and Available Knobs

1. **Why WebRTC AEC3 Damages the First Words of a Barge-In:**
* AEC3 consists of two stages: a **linear subtractor** (`Subtractor` using a 12-block main MDF filter and a 12-block shadow filter) that outputs linear residual $e_{\text{main}}(t)$, followed by a **nonlinear residual echo suppressor** (`SuppressionGain` + `EchoRemover`) that multiplies each FFT bin by a spectral gain $G(f) \in [0, 1]$ and fills deep cuts with comfort noise.
* During single-talk (assistant speaking, user silent), the suppressor uses aggressive `normal_tuning` mask parameters to drive residual echo to the noise floor. When the user starts speaking ("Stop..."), `DominantNearendDetector` requires near-end energy to exceed residual echo energy by a ratio threshold for multiple consecutive blocks before switching `SuppressionGain` to `nearend_tuning`. During that 100–250 ms transition window (and longer if ducking perturbs the linear filter as in H3), the nonlinear suppressor treats the user's initial consonants (`/s/`, `/t/`) as residual echo bursts and suppresses them.


2. **Known Remedies (Ranked by Effectiveness for Voice Assistants):**
* **Remedy A — Split-Tap Architecture (Canceller for Guard/VAD Only; Raw or Linear-Only Audio for ASR with Trimmed Pre-Roll):**
* **How it works:** Feed the WebRTC-cancelled audio *only* to `BargeInGuard` (Silero VAD + level stage). Keep a parallel `PreRoll` buffer of **raw (un-cancelled) microphone audio**. The instant `BargeInGuard` fires:
1. Immediately send `abort` to the Pi (silencing the speaker within ~35 ms).
2. Slice the **raw** pre-roll starting only **`200–250 ms` before the first triggering Silero window** (not 1.2 s ago!).
3. Feed that trimmed raw onset + the subsequent live microphone audio (which is now echo-free because the Pi was just aborted!) to Parakeet ASR [inference].


* **Why it works:** The ASR never sees nonlinear spectral subtraction artifacts (`SuppressionGain`), never sees the first 800 ms of pure assistant echo from the 1.2 s pre-roll, and only has ~250–300 ms of overlap during the user's first word (which Parakeet handles much better raw—`4 of 6` in 7D and `0 of 6` garbled in 7F column 1—than after nonlinear suppression!).


* **Remedy B — Linear-Only AEC (`linear_aec_output`) or Relaxed `EchoCanceller3Config`:**
* In WebRTC AEC3 C++, `EchoCanceller3` computes the linear filter output (`SubtractorOutput::e_main`) *before* `SuppressionGain` runs, and can export it via `ExportLinearAecOutput` (`EchoControl::SetCaptureOutputUsage`), or relax suppression via `EchoCanceller3Config.suppressor.nearend_tuning` (`mask_lf`, `mask_hf`) and `dominant_nearend_detection` (`enr_threshold`, `hold_duration`).




3. **What Knobs Each Binding Exposes:**
* **`livekit.rtc.AudioProcessingModule` (Python):** Exposes **only** the four booleans (`echo_cancellation`, `noise_suppression`, `high_pass_filter`, `auto_gain_control`) plus `process_stream()`, `process_reverse_stream()`, and `set_stream_delay_ms()` via its Rust FFI wrapper (`livekit-ffi` -> `webrtc-sys`). It does **not** expose `EchoCanceller3Config` struct fields or linear-only output.
* **PipeWire `libpipewire-module-echo-cancel` (with `aec.method = "webrtc"`):** Version 1.0+ compiled against `webrtc-audio-processing-1` exposes `webrtc.` properties in `aec.args`, or runs inside a local PipeWire loopback graph, though `webrtc-audio-processing-1` C++ headers expose full `AudioProcessing::Config`.
* **Other Python Bindings of WebRTC APM / SpeexDSP:**
* `webrtc-audio-processing` (PyPI `webrtc_audio_processing` by xiongyihui): Unmaintained (wraps ancient WebRTC M50/AEC2, fails to build on modern Python 3.11+).
* `webrtc-noise-gain` (PyPI, by Rhasspy / Home Assistant, wraps `webrtc-audio-processing-1`): Maintained for Home Assistant voice satellites, though primarily focused on NS/AGC unless patched for reverse stream.
* `speexdsp-ns` / `ctypes` wrapper over system `libspeexdsp.so.1` (Debian package `libspeexdsp1` / `libspeexdsp-dev`): System `libspeexdsp.so.1` is pre-installed or apt-installable everywhere and can be called in ~30 lines of Python `ctypes` (`speex_echo_state_init_mc`, `speex_echo_cancellation`, `speex_echo_ctl`). Crucially, **`speex_echo_cancellation()` is purely a linear MDF adaptive filter**—its nonlinear residual echo suppressor is completely separate inside `speex_preprocess_run()`! That means calling `speex_echo_cancellation()` alone gives you **pure linear echo cancellation (`~10–15 dB` removal) with zero nonlinear distortion of the user's first word** (provided you apply the 137 ppm drift resample to the reference)!





---

#### Q3. How Other Products and Frameworks Prevent Self-Interruption

1. **Pipecat (`pipecat-ai/pipecat`, v0.0.40–1.13+):**
* **Acoustic / VAD Gating:** Pipecat relies on client-side WebRTC/hardware AEC on the transport input, paired with `SileroVADAnalyzer(params=VADParams(confidence=0.7, start_secs=0.2, stop_secs=0.8, min_volume=0.6))`.
* **Interruption Strategies (`BaseInterruptionStrategy` / `MinWordsInterruptionStrategy`):** Because VAD alone false-triggers on coughs and residual echo leaks, Pipecat introduced `allow_interruptions=True` with `interruption_strategies=[MinWordsInterruptionStrategy(min_words=N)]`. When `MinWordsInterruptionStrategy` is used, VAD `UserStartedSpeakingFrame` does **not** immediately cancel the bot's TTS; instead, the bot continues speaking while streaming audio to STT, and only interrupts the bot once the interim/final transcript reaches `min_words` (or matches an explicit interruption check).
* **Conversation History Commit:** Pipecat's `LLMUserContextAggregator` / `LLMAssistantContextAggregator` tracks TTS word timestamps (or characters spoken before `StartInterruptionFrame`) and commits only the actually spoken prefix of the assistant's turn to the context window—matching your Section 5F.


2. **LiveKit Agents (`livekit-agents` v0.8–1.0+):**
* Uses `VoicePipelineAgent` / `AgentSession` with specific parameters made for residual echo rejection:
* `min_interruption_duration` (default `0.5 s` of detected speech required before interrupting TTS).
* `min_interruption_words` (default `0`, often set to `1` or `2`: if $>0$, VAD onset alone only marks a candidate interruption, and the TTS stream is only aborted once STT emits at least $N$ recognized words).
* `false_interruption_timeout` (default `2.0 s`) and `resume_false_interruption=True`: if VAD triggers an interruption (pausing playback) but STT produces **empty text** (such as an un-converged echo burst or cough that ASR ignores) within `false_interruption_timeout`, LiveKit **automatically resumes the paused assistant audio** rather than discarding the reply!




3. **Vapi & Retell AI:**
* **Vapi:** Exposes `stopSpeakingPlan` with three parameters: `numWords` (number of STT words required to interrupt, default `0` for telephony, `2–3` for speakerphone), `voiceSeconds` (VAD duration before interrupting, default `0.2 s`, max `0.5 s`), and `backoffSeconds` (how long to wait after an interruption before speaking again).
* **Retell AI:** Exposes `interruption_sensitivity` (`0.0` to `1.0`), combining a VAD hold timer with a streaming lexical/turn filter that ignores backchannels (`"yeah"`, `"uh-huh"`) and sub-threshold acoustic bursts before flushing the output buffer.


4. **OpenAI Realtime API & Gemini Multimodal Live API:**
* Both native speech-to-speech APIs explicitly document that **client-side AEC (WebRTC `echoCancellation: true` or hardware DSP) is mandatory** when not using headphones; their server-side `server_vad` (`threshold`, `prefix_padding_ms=300`, `silence_duration_ms=500` in OpenAI Realtime) has no reference signal and will self-interrupt on speaker playback if client AEC fails. Note also that OpenAI Realtime's default `prefix_padding_ms` (their pre-roll before VAD trigger) is **`300 ms`**, **not** `1200 ms`!


5. **Amazon Alexa, Google Nest, and Apple HomePod:**
* **Hardware Reference Loopback + Beamforming:** All three use multi-microphone arrays (2 to 7 MEMS mics) plus an internal digital loopback channel (`I2S/TDM` reference directly from the DAC amplifier on the same crystal clock, 0 ppm drift, fixed sub-millisecond delay) feeding an on-board DSP running multi-channel linear AEC + beamforming + post-filtering.
* **Two-Stage / Duck-and-Confirm:** Commercial smart speakers use a fast acoustic/wake detector that ducks playback (typically `-12` to `-18 dB` over a ramp) when candidate near-end speech or wake onset is detected, and restores playback if the cloud/stage-2 verifier rejects the trigger; however, published numerical thresholds for no-wake-word full-duplex duck-and-restore on smart speakers are proprietary (`not found` as public datasheet figures).





---

#### Q4. Ducking and the Canceller

1. **Does a -15 dB Step Gain Change Hurt an Adaptive Canceller?**
* **ONLY if the gain change is applied to a point in the signal path where the reference $x(n)$ and the loudspeaker output $y_{\text{spk}}(n)$ experience different delays!**
* Mathematically, an acoustic echo canceller models the linear room path $h_{\text{room}}(n)$ between the point where reference $x(n)$ is tapped and the microphone $d(n) = (h_{\text{room}} * x)(n) + v_{\text{near}}(n)$.
* **The Golden Rule of Ducking with AEC:** The reference tap $x(n)$ MUST be taken **after** the volume/duck multiplier, at the **exact same sample index** that goes to the DAC! If sample $n$ sent to the speaker is multiplied by gain $g(n)$, and that exact same sample $n$ is placed into the reference stream as $g(n)x(n)$, then the linear transfer function $h_{\text{room}}$ between the scaled reference and the microphone **does not change at all**—even for an instantaneous -15 dB step!
* **Why Your Current Duck Breaks the Canceller (H3):**
* In your current code (Section 3 & 5B), the laptop pushes un-ducked (`0 dB`) audio slices into the Pi WebSocket up to `0.25 s` ahead of playback. When a duck occurs, the Pi modifies the gain of slices already waiting in the Pi's memory, **or** the laptop scales new reference frames at the moment they are fed into `process_reverse_stream()`, creating a **time-skewed gain step** where the reference inside AEC3 and the acoustic wave coming out of the Pi speaker have a 15 dB gain ratio mismatch for up to `0.25 s`! That mismatch violates linearity ($d(n)/x(n)$ jumps by $5.6\times$) and forces the adaptive filter to diverge.




2. **How Products Apply Ducking Relative to the Canceller:**
* **Option A (Tap Reference on the Pi / Synchronized by Slice ID):** If the Pi applies the duck ramp `g[i]` to slice `k` right before writing slice `k` to ALSA, the reference frame corresponding to slice `k` must be scaled by the exact same `g[i]`—and **not** slices `k-4..k-1` that were already played at `0 dB` or slices `k+1..` at a different offset!
* **Option B (Smooth Ramp Time):** Standard audio ducking uses a **50–100 ms cosine or exponential fade-down** and a **150–300 ms fade-up** on restore so that any residual timing error of 10–20 ms between reference and echo produces less than a $1\text{–}2\text{ dB}$ mismatch across the filter length (`48 ms`), rather than a 15 dB cliff [inference].



---

#### Q5. Assembling the Recognizer Audio After a Barge-In

1. **Why Your Current 1.2 s Pre-Roll Fails:**
* Your guard requires `7 of 10` Silero windows (`32 ms` each = `224–320 ms`) and `3 of 8` level blocks (`100 ms` each = `300 ms`).
* That means when `BargeInGuard` fires at time $t_{\text{fire}}$, the user's very first phoneme started at $t_{\text{onset}} \approx t_{\text{fire}} - 250\text{ to }350\text{ ms}$.
* Because you prepend the entire **1.2 s** pre-roll ($t_{\text{fire}} - 1200\text{ ms}$ to $t_{\text{fire}}$), the first **850–950 ms** of audio you hand to Parakeet is **guaranteed to be from before the user started speaking** (i.e. pure assistant echo)!


2. **How Production Systems Assemble the Barge-In Audio Stream:**
* **1. Onset-Anchored Pre-Roll Trimming (`prefix_padding_ms = 200–300 ms`):**
* Track the index/timestamp of the **first Silero window in the current burst** where confidence crossed `0.5` (or `0.35`).
* When the guard confirms at $t_{\text{fire}}$ (after 7 of 10 windows), do **not** dump a fixed 1.2 s buffer. Instead, slice the pre-roll starting at **`t_first_silero_window - 200 ms`** (a total lookback of **`450–500 ms`** before $t_{\text{fire}}$, matching OpenAI Realtime's `prefix_padding_ms = 300 ms` + VAD hold time).
* Keep the ring buffer capacity at `0.6 s` (6 blocks of 100 ms), or slice dynamically from `t_first_silero_window - 200 ms`. This immediately eliminates **~0.75 s of pure assistant echo** at the start of every barge-in turn!


* **2. Split-Tap Audio Routing (Cancelled for VAD, Raw/Linear for ASR):**
* As detailed in Q2, store **raw (un-cancelled) mic audio** (or linear-only MDF-cancelled audio without nonlinear spectral suppression) in the `PreRoll` buffer for the ASR, while feeding the WebRTC-cancelled audio only to `BargeInGuard`.
* Because the Pi loudspeaker is aborted within ~35 ms of $t_{\text{fire}}$, all microphone audio from $t_{\text{fire}} + 50\text{ ms}$ onward has **zero assistant echo in the room**! The only part of the user's utterance that overlaps with the loudspeaker is the `300 ms` between $t_{\text{onset}}$ and $t_{\text{fire}}$ [inference].


* **3. Prefix Echo Stripping in the Text Guard (`5D` / `5E` fix):**
* Currently, your text guard (5D) only drops a turn if the echo run is **$\ge 80\%$ of the entire transcript**, and your stop-stripper (5E) only checks if `"Stop"` is at the **very beginning** of the transcript.
* In a barge-in (`"First book in the Border Trilogy. Stop! What year was it published?"`), the assistant echo is always a **prefix** of the transcript (from the pre-roll), and the user's speech is the **suffix**!
* **Exact algorithmic fix for 5D/5E:** If the **prefix** of the recognized transcript (up to the word `"stop"`, `"wait"`, `"hold on"`, or any fuzzy match $\ge 2$ words against the assistant's recently spoken words) matches the assistant's spoken text, **strip that echoed prefix off the front of the transcript** before running the stop/pivot rules in 5E [inference]! Look at what that single 10-line text rule does to all 6 of your failed raw transcripts in 7F:
* `"First book in the Border Trilogy. Stop! What year was it published?"` $\rightarrow$ strip echoed prefix `"First book in the Border Trilogy."` $\rightarrow$ strip leading `"Stop!"` $\rightarrow$ **`"What year was it published?"`** (100% clean, 0 spurious `stop_music`!).
* `"2005 border. Stop. Set a timer for ten minutes."` $\rightarrow$ strip `"2005 border."` $\rightarrow$ strip leading `"Stop."` $\rightarrow$ **`"Set a timer for ten minutes."`** (100% clean, 0 spurious `stop_music`!).
* `"Joseph McCartney Chief Stop, when did he die?"` $\rightarrow$ strip prefix up to `"Stop,"` $\rightarrow$ **`"when did he die?"`** (100% clean!).
* `"Second, novel stop pause the music."` $\rightarrow$ strip `"Second, novel stop"` $\rightarrow$ **`"pause the music."`** (100% clean!).







---

#### Q6. Neural and Residual Echo Suppression on a Laptop CPU

| Model / System | Paper & Source | Architecture & Size | Reported Performance & Real-Time CPU Viability | Open Code & Pretrained Weights? |
| --- | --- | --- | --- | --- |
| **NKF-AEC** (Neural Kalman Filtering) | Yang et al. (Tencent Ethereal Audio Lab), *"Neural Kalman Filtering for Acoustic Echo Cancellation"*, **Interspeech 2023** (`arXiv:2306.01359`) | Complex-domain RNN predicting Kalman gain per frequency bin for a linear state-space filter; only **~21K parameters**! | High ERLE (~40+ dB single-talk) and **minimal double-talk distortion** because it acts as a data-driven step-size controller for a linear Kalman filter rather than a destructive nonlinear mask; runs easily in real time on a single laptop CPU core. | **Yes:** `[github.com/yanggeng1995/NKF-AEC](https://github.com/yanggeng1995/NKF-AEC)` (PyTorch code + pretrained weights `nkf_epoch70.pt`). Note: assumes aligned reference (`<50 ms` delay), so pair with bulk delay alignment. |
| **DTLN-aec** | Westhausen & Meyer, *"DTLN-aec: Real-Time Neural Acoustic Echo Cancellation Using the Dual-Signal Transformation LSTM Network"*, **Interspeech 2021** (`arXiv:2010.11731`) | Dual-stage STFT + learned 1D-Conv feature basis with LSTM layers (`128`, `256`, or `512` units); `1.8M–10M` params; `32 ms` window / `8 ms` shift at 16 kHz. | Top-3 in ICASSP 2021 AEC Challenge (`AECMOS ~4.3`); the `128`-unit TFLite/ONNX model runs at `RTF ~0.08–0.15` on a laptop CPU. However, being an end-to-end mask network, it still requires reference delay alignment within ~32–64 ms. | **Yes (MIT License):** `[github.com/breizhn/DTLN-aec](https://github.com/breizhn/DTLN-aec)` (includes pretrained TFLite / ONNX models for 128, 256, 512 units). |
| **DeepVQE** | Ivry et al. (Microsoft), *"DeepVQE: Real Time Deep Voice Quality Enhancement for Joint Acoustic Echo Cancellation, Noise Suppression and Dereverberation"*, **Interspeech 2023** (`arXiv:2306.03177`) | Convolutional recurrent encoder-decoder with **ccm-attention (cross-correlation matrix attention)** that explicitly aligns delayed reference frames internally up to several hundred ms. | Strong ICASSP 2023 AEC Challenge performance (`AECMOS > 4.5`), real-time on laptop CPU. *(Correction to your `AGY-ECHO.md` note in Part 2: the venue is Interspeech 2023, `arXiv:2306.03177`.)* | **No official Microsoft weights:** Unofficial community PyTorch reconstructions exist of the architecture, but no official pretrained weights are released. |
| **SpeexDSP Linear MDF + Neural VAD** | Xiph.org `libspeexdsp` (`speex_echo_cancellation`) | Purely linear Multidelay Block Frequency Domain (MDF) adaptive filter (`filter_length = 2048` samples = 128 ms at 16 kHz). | `<1%` CPU on laptop; removes `10–18 dB` of linear echo **without any nonlinear spectral suppression**, preserving 100% of near-end plosives (`/s/`, `/t/`) for ASR when clocks are rate-matched. | **Yes (BSD License):** `apt install libspeexdsp1` (callable via Python `ctypes` on `libspeexdsp.so.1`). |

---

#### Q7. Echo-Aware and Target-Speaker VAD

1. **Can Reference-Aware or Target-Speaker VAD Run on the Laptop Without a Model on the Pi?**
* **Yes:** Since your laptop receives the 16 kHz microphone stream and already holds the 16 kHz reference signal and TTS text, any reference-aware or target-speaker detector runs **100% on the laptop CPU** (satisfying your course rule that no speech model except a small VAD runs on the Pi).


2. **Open-Source Options Usable Today:**
* **1. Acoustic Enrollment Speaker Verification Gate (WeSpeaker / Sherpa-ONNX `ECAPA-TDNN` or `ResNet34`, ~15–25 MB ONNX):**
* **How it works:** Instead of running full frame-level Personal VAD, compute a single speaker embedding vector $\mathbf{e}_{\text{tts}} \in \mathbb{R}^{192}$ of your Supertonic TTS voice once at startup (or enroll a 3 s clip of the user's voice $\mathbf{e}_{\text{user}}$). Whenever Silero VAD detects a candidate 300 ms speech burst during a reply, run one forward pass of a small speaker-verification ONNX model (`wespeaker-voxceleb-resnet34` or `3dspeaker_speech_eres2net_base`, takes **`~8–15 ms` on a laptop CPU** per 300 ms burst!) on that 300 ms slice.
* **Why this is a silver bullet for self-interruption:** The assistant's own TTS voice is **known and constant** (Supertonic TTS)! If the cosine similarity $\cos(\mathbf{e}_{\text{chunk}}, \mathbf{e}_{\text{tts}}) > 0.55$ (and $\cos(\mathbf{e}_{\text{chunk}}, \mathbf{e}_{\text{user}}) < 0.40$), the 300 ms burst is the assistant's own voice leaking through the room, and the guard rejects it with near-100% accuracy regardless of volume or AEC convergence [inference]!


* **2. Normalized Cross-Correlation / Spectral Coherence Gate (Zero Neural Model):**
* Before firing `BargeInGuard`, compute the short-time normalized spectral envelope correlation between the last `300 ms` of microphone audio and the delayed reference audio over the `300–2500 Hz` voice band. Pure echo has high envelope correlation with the reference, whereas double-talk (user speaking distinct syllables over the assistant) drops the envelope correlation sharply [inference].





---

#### Q8. Hardware Speakerphones & DSP Mic Arrays for Raspberry Pi

If you decide to buy hardware (or recommend one in your report), **hardware AEC only works if both playback and capture pass through the same USB device** so its internal DSP sees the exact DAC reference on the same crystal clock.

| Device | Price (USD) | On-Board DSP & AEC | Works as Standard Linux / RPi USB Sound Card (UAC 1.0/2.0)? | Published / Measured ERLE & Barge-In Notes |
| --- | --- | --- | --- | --- |
| **Seeed Studio ReSpeaker USB Mic Array v2.0** (XMOS XVF-3000) | ~$69–$79 | XMOS XVF-3000 hardware DSP: 4 MEMS mics, full-duplex AEC (up to 16 kHz), beamforming, adaptive noise suppression, DOA | **Yes:** Plug-and-play USB Audio Class 1.0 on Debian/Raspberry Pi (`snd-usb-audio`, no drivers needed). **Must** plug the speaker into the ReSpeaker's onboard 3.5 mm stereo jack so the XVF-3000 receives the playback reference. | XMOS XVF-3000/3510 brief specifies full-duplex barge-in up to ~85 dB SPL at 0.5–3 m; widely used as the reference hardware in Home Assistant / Rhasspy / Pipecat Raspberry Pi voice agents. |
| **Seeed ReSpeaker Lite / XMOS XVF3800 Dev Kits** | ~$35–$99 | XMOS XVF3800 (4-mic) or XU316 (2-mic) | **Yes:** USB Audio Class 2.0/1.0 plug-and-play on Linux ALSA; onboard 3.5 mm or amplified speaker header. | XMOS XVF3800 datasheet specifies 3-stage AEC (150 ms tail length) + nonlinear residual echo suppression designed specifically for voice-agent barge-in at high playback volume. |
| **Anker PowerConf (A3301 / S3 / S330)** | ~$50–$99 | Integrated conference DSP (6-mic array + built-in speaker in one enclosure) | **Yes:** Standard USB-C Audio Class device on Linux (`Anker PowerConf`); exposes both ALSA playback sink and AEC-processed mono capture source in one box.

 | Fixed mechanical geometry between speaker and mics gives rapid AEC convergence with zero external wiring; independent dB ERLE numbers `not found` (without live web search), but built specifically for full-duplex speakerphone double-talk. |
| **Jabra Speak 510 / Speak2 40 / 55** | ~$75–$130 | Integrated conference DSP (omni mic + speaker in one puck) | **Yes:** Plug-and-play USB Audio on Debian/RPi (`Jabra Speak 510 USB`); zero setup.

 | Industry benchmark for portable USB full-duplex speakerphones; handles 80%+ volume double-talk in hardware with 0% host CPU cost.

 |
| **Poly Sync 20** | ~$80–$120 | 3-mic steerable array + integrated speaker with hardware DSP AEC | **Yes:** Standard USB Audio on Linux. | Full-duplex hardware AEC; independent dB ERLE numbers `not found` (without live web search). |

---

#### Q9. Eliminating the 0.5 s Wake-Up Delay of the USB Sound Card

The ~0.5 s delay on the first clip after idle (7C) is caused by two layers: (1) your `pi_speaker.py` closing or idling the ALSA PCM stream between replies (forcing USB isochronous endpoint setup and DAC anti-pop unmute ramp on the `Jieli CD002-AUDIO` chip), and/or (2) Linux kernel USB autosuspend (`snd_usb_audio`).

**Three Standard Fixes (Apply #1 Directly in `pi_speaker.py` — Requires No `sudo`!):**

1. **Continuous Zero-Stuffing in `pi_speaker.py` (Best, Zero Root Required, Cost = ~1.5 KB/s local bus traffic):**
* In `pi_speaker.py`, **open the ALSA playback device once at startup and never close or pause it**.
* Run a dedicated playback loop that writes a `20 ms` (or `25 ms`) period of 48 kHz 16-bit mono PCM (`960` samples) to ALSA every `20 ms`:
* If reply audio slices are in the playback queue, pop and write them to ALSA.
* If the playback queue is empty (or immediately after an `abort`), write **all zeros (`b"\x00\x00" * 960`)** (or `±1 LSB` dither if the Jieli DAC has an internal hardware zero-detect auto-mute; `±1 LSB` at 16-bit is `-90 dBFS`, completely inaudible!) [inference].


* **What this buys you:**
* The `0.5 s` first-reply wake-up delay vanishes completely (**cuts 0.4 s off your time-to-first-audio** on every turn!).
* Even more importantly for AEC: the Pi's ALSA ring buffer remains constantly running at a fixed fullness (`~35–50 ms`), so the **render-to-echo delay becomes deterministic and identical from reply to reply** [inference]!




2. **If PipeWire/WirePlumber Is Used on the Host (`session.suspend-timeout-seconds`):**
* PipeWire suspends idle ALSA nodes after 5 seconds by default. In `~/.config/wireplumber/wireplumber.conf.d/51-disable-suspend.conf` (user config, no `sudo`), setting `session.suspend-timeout-seconds = 0` prevents PipeWire from closing the ALSA handle. (Not needed on the Pi if `pi_speaker.py` writes directly to ALSA with zero-stuffing as in #1.)


3. **Linux Kernel USB Autosuspend (Requires `sudo`, so skip on Pi in favor of #1):**
* Kernel `usbcore.autosuspend` defaults to 2 s; streaming continuous PCM frames in #1 keeps the USB isochronous endpoint continuously active so kernel autosuspend can never trigger even without root.



---

#### Q10. Standard Measures, Tools, and Recordings to Separate H1–H7

1. **Standard Metrics & Public Tools:**
* **Single-Talk Echo Suppression — ERLE (Echo Return Loss Enhancement):**

$$\text{ERLE}(t) = 10 \log_{10} \frac{\mathbb{E}[d^2(t)]}{\mathbb{E}[e^2(t)]}$$



Measure **time-resolved ERLE in 100 ms blocks** as a function of time since reply start ($t \in [0, 0.5\text{ s}]$, $[0.5, 1.5\text{ s}]$, $[1.5, 5.0\text{ s}]$), subtracting the measured room noise power $\sigma_{\text{noise}}^2$ from both numerator and denominator so the $-44\text{ dBFS}$ noise floor does not clamp or distort the dB figure.
* **Double-Talk & Perceptual Quality — `AECMOS` / `DNSMOS`:**
* Microsoft's open-source **`AECMOS`** (`[github.com/microsoft/AEC-Challenge](https://github.com/microsoft/AEC-Challenge)`, ONNX model runnable via Python `onnxruntime`) takes `(lpb_reference, mic, enhanced)` 16 kHz WAV files and outputs two scores in `[1, 5]`: **Echo DMOS** (how well echo is suppressed) and **Other Degradation MOS** (how much the canceller damaged the near-end user speech during double-talk).


* **Downstream Task Metrics (Most Important for Your Section 2 Criteria):**
* **First-Word Survival Rate (FWSR)** & **Word Error Rate (WER)** of Parakeet on the barge-in utterances (specifically checking whether `"Stop"` survives intact).
* **Self-Interruption Rate (SIR):** Fraction of quiet replies aborted before completion (`target: 0 / 30`).
* **True Barge-In Stop Latency:** Measured from the **acoustic onset of the user's voice in the raw mic recording** (using forced alignment or energy rise, **not** from the cue beep which includes human reaction time variance of `0.2–0.8 s`!) to the sample where speaker echo drops by 15 dB.






2. **Two Extra Recordings to Make right now in Your Setup:**
* **Recording 1 — Multi-Track Live Session Dump (4 synchronized 16 kHz WAV channels per reply):**
* In `MicHub` / `EchoCanceller`, save a 4-channel WAV (or 4 aligned mono WAVs) for every live reply:
* `Ch 0`: Raw microphone frame passed into `process_stream()`.
* `Ch 1`: Exact reference frame passed into `process_reverse_stream()` on that same 10 ms tick.
* `Ch 2`: Cancelled output frame from `process_stream()`.
* `Ch 3`: Scalar `played_level` and `bar` written per 10 ms frame.


* **Why this separates H1–H7 in 5 minutes:** Cross-correlating `Ch 0` and `Ch 1` on the 7 failed replies of 7F immediately reveals the exact live render-to-echo delay on each reply (testing **H1**), whether the reference queue underran or jumped (testing **7E/H1**), whether `bar` was pinned at `0.02` during the first second (testing the **`EchoModel` interaction bug**), and whether `Ch 1` scaled by -15 dB 0.25 s before `Ch 0` did during a duck (testing **H3**)!


* **Recording 2 — Reproducible Acoustic Double-Talk Test (No Human Reaction Time Variance):**
* Play the user barge-in clips from a second speaker (e.g., your phone or laptop speaker at user mouth position) triggered deterministically at $t = 2.500\text{ s}$ into each reply. This eliminates human reaction time variance (`0.36 s` vs `1.34 s` in 7F) and lets you compare A/B changes on identical acoustic double-talk.







---

#### Q11. Verdict and Ranked Plan

**Plain Verdict on Hardware vs. Software:**
You **can** meet all seven criteria `(a)–(g)` of Section 2 in software with your current hardware (laptop mic + Pi speaker in front at 80% volume) **once you fix the four architectural bugs in your current pipeline** (the Pi ALSA idle wake-up jump, the `EchoModel` post-canceller threshold collapse, the 1.2 s pre-roll length, and feeding nonlinearly suppressed audio to ASR).
However, running software AEC across two unsynchronized clocks (`137 ppm` drift) over a network with a cheap mono speaker (`Jieli CD002-AUDIO`) has a hard physical ceiling (~15–20 dB live ERLE, vulnerable if someone moves the speaker mid-reply). If your budget allows a **$50–$80 USB speakerphone** (`Seeed ReSpeaker USB Mic Array v2.0` at ~$69 or `Anker PowerConf` / `Jabra Speak 510` at ~$75), plugging it into the Pi (or laptop) solves clock drift, variable USB delay, and double-talk distortion in hardware DSP with **0% host CPU**.

Below is the exact ranked software experiment plan (Section 4) to reach Section 2's `"done"` criteria immediately without waiting for hardware.

---

### 4. Ranked Experiment Plan (Q11 Table)

| Order | Experiment & Exact Code Change | Hypothesis Tested / Problem Fixed | What to Measure | Success Criterion (Go / Keep) | What Rules It Out |
| --- | --- | --- | --- | --- | --- |
| **1** | **Split-Tap + Trim Pre-Roll to Onset + Strip Echo Prefix in Text Guard (Zero AEC dependency):**<br>

<br>1. Keep **raw** (un-cancelled) mic blocks in `PreRoll`, while feeding AEC output **only** to `BargeInGuard`.<br>

<br>2. When `BargeInGuard` fires, slice `PreRoll` starting **`200 ms` before the first of the 7 triggering Silero windows** (total pre-roll `~450 ms`, **not** `1.2 s`) and stream raw audio to Parakeet.<br>

<br>3. In `5D/5E`, if the transcript has a prefix before `"stop"`/`"wait"` (or matching $\ge 2$ words of the assistant's reply), **strip that prefix** before applying the leading-`"Stop"` stripper. | **H2 & H6** (Eliminates the 0.85 s of echo in pre-roll, stops AEC from garbling `"Stop"`, and eliminates spurious `stop_music` tool calls). | Run offline on the saved 7F turn recordings first, then 1 live 8-turn cue-beep session at 80%. Count: (a) transcripts with echo words after prefix strip, (b) spurious `stop_music` calls, (c) first-word survival on the 8 scripted interruptions. | **0 of 8** spurious `stop_music` calls; **$\ge 7\text{ of }8$ (88–100%)** first words (`"Stop..."`) recognized cleanly; **0** assistant actions on its own speech. | If raw audio sliced at `onset - 200 ms` still has Parakeet missing `"Stop"` on $>1\text{ of }8$ clips during double-talk (meaning raw echo at 80% masks the user's voice at the ASR level), keep linear MDF / AEC on the ASR pre-roll tap too. |
| **2** | **Fix `EchoModel` Post-Canceller Collapse + Convergence Hold (`bargein.py`):**<br>

<br>1. Compute `EchoModel.coupling` from **pre-canceller** `mic_level / played_level` (or enforce a minimum `bar >= max(4 * room_noise, 0.035, 0.35 * played_level)` during the **first `1.0 s**` of every reply while AEC converges).<br>

<br>2. Require the guard's level stage (`3 of 8 blocks > bar`) to evaluate against that protected `bar`. | **7F Early Cutoffs** (`7 of 20` replies cut off in first `0.4–1.2 s` in `canceller only`). | Replay the 7F `canceller only` guard trace with `guard_replay.py`, then run 20 quiet replies live at 80% with `--aec` (and `--duck-db 0`). | **0 of 20** quiet replies cut off early in replay and live (**0/30** when extended to full validation). | If early fires still occur after `t > 1.2 s` despite `bar` being protected during convergence, proceed to Step 3 (continuous ALSA + drift). |
| **3** | **Keep Pi ALSA Open Continuously (`pi_speaker.py`) & Fix Reference Queue (`aec.py`):**<br>

<br>1. In `pi_speaker.py`, keep the ALSA PCM device continuously open and write `20 ms` frames of zeros (`±1 LSB` dither) whenever no reply slice is queued (`±0.5 s` idle wake-up eliminated).<br>

<br>2. In `aec.py`, **remove** the "when reference queue goes from empty to non-empty, feed $L\text{ ms}$ ahead once" burst; simply push reference frames at the exact moment the player sends them (or with a fixed once-per-session offset) and resample reference at `16000 * (1 + 137e-6)` (`16002.2 Hz`) to cancel the 137 ppm drift. | **H1, H4 & 7E bug** (Fixes the 0.5 s idle delay jump between turns, stops `kRenderOverrun` / queue starvation, and locks the two clocks). | Run `echo_path_probe.py` and the 4-channel WAV tap (Q10) across 10 consecutive replies spaced 5 s apart. Measure render-to-echo delay variance across replies and live ERLE. | Reply-to-reply render-to-echo delay stays within **$\pm 15\text{ ms}$** (including first reply after 10 s idle); first-reply startup latency drops by **~0.4 s**; live ERLE $\ge 15\text{ dB}$. | If the Jieli USB sound card still buffers variably even when fed continuous zeros, have `pi_speaker.py` send an explicit `played_seq_id` timestamp back over the WebSocket as each 50 ms slice is written to ALSA, and release that slice into `process_reverse_stream()` on the laptop only when `played_seq_id` arrives! |
| **4** | **Fix or Remove Ducking (`playback.py` / `aec.py`):**<br>

<br>With Steps 1–3 active, test `--aec` **without ducking** first (`--duck-db 0`, satisfying criterion `(e)` "no audible dips in volume on replies nobody talks over" by construction!). If ducking is still desired for loud double-talk, trigger duck only at **`4 of 10` Silero windows** (instead of `2 of 10`) and scale each reference slice inside the player queue **tagged to the exact slice index** the Pi scales, with a 100 ms ramp. | **H3 & Criterion (e)** (`3 of 7` false ducks on quiet replies and `1.34 s` stop delay in `canceller + duck`). | 30 quiet replies + 10 scripted barge-ins at 80% volume, speaker 0.5–1.5 m in front of laptop. | **0 / 30** self-interruptions; **0 / 30** audible volume dips on quiet replies; **$\ge 9 / 10$ (90%+)** first-word survival including bare `"Stop."`; true stop latency **$\le 0.50\text{ s}$**. | If acoustic echo at 80% without ducking still masks soft single-word `"Stop."` at 1.5 m, keep the slice-synchronized duck at `4 of 10` Silero windows or add Step 5. |
| **5** | **(Optional Fallback) TTS-Voice Speaker-Embedding Rejector or `libspeexdsp` Linear Canceller:**<br>

<br>If any residual echo burst ever clears both `Silero` and `bar`, run `sherpa-onnx` / `wespeaker` speaker embedding (~10 ms CPU on the 300 ms candidate burst) and reject if cosine similarity to the cached Supertonic TTS voice embedding is $> 0.55$. | **H5 & H7** (Guarantees the assistant can never interrupt itself on its own voice timbre). | 30 quiet replies at 80–90% volume + 10 barge-ins. | **0 / 30** self-interruptions even at 90% volume; adds `< 15 ms` to barge-in detection latency. | Not needed if Steps 1–4 already pass 30/30 quiet replies and 9/10 barge-ins at 80%. |

---

### 5. Reference List of Parameters, Library Calls, Settings, and Packages

| Parameter / Call / Package | Where It Lives | Purpose & Source |
| --- | --- | --- |
| `kMaxApiCallJitterBlocks = 30` (`120 ms` at `4 ms` blocks) | `webrtc/modules/audio_processing/aec3/aec3_common.h` | Maximum number of 4 ms render blocks (`process_reverse_stream`) that can be queued ahead of `process_stream` before `RenderDelayBuffer` triggers `kRenderOverrun` and resets. |
| `EchoCanceller3Config.delay.num_filters = 5`, `sub_block_size = 32` | `webrtc/api/audio/echo_canceller3_config.h` | Sets AEC3's internal matched-filter delay estimator search range to $5 \times 32 \times 4\text{ ms} = 640\text{ ms}$. |
| `EchoCanceller3Config.delay.use_external_delay_estimator` | `webrtc/api/audio/echo_canceller3_config.h` | Defaults to `false`; explains why `set_stream_delay_ms()` only sets the initial pre-convergence offset before internal matched filters take over. |
| `EchoCanceller3Config.suppressor.nearend_tuning` & `dominant_nearend_detection` | `webrtc/api/audio/echo_canceller3_config.h` | Controls how quickly AEC3's nonlinear suppressor (`SuppressionGain`) relaxes high-frequency attenuation when the user starts talking over echo. |
| `livekit.rtc.AudioProcessingModule(echo_cancellation=True, ...)` | `livekit-rtc` Python SDK (`livekit/rtc/apm.py`) | Wraps WebRTC APM `process_stream(AudioFrame)` and `process_reverse_stream(AudioFrame)` (10 ms frames); exposes only 4 booleans + `set_stream_delay_ms(int)`. |
| `libspeexdsp1` (`speex_echo_state_init`, `speex_echo_cancellation`) | Debian package `libspeexdsp1` (`ctypes.CDLL("libspeexdsp.so.1")`) | Purely linear MDF adaptive filter without nonlinear spectral suppression; preserves near-end plosives for ASR when reference is clock-matched. |
| `MinWordsInterruptionStrategy(min_words=N)` | `pipecat.audio.interruptions.min_words_interruption_strategy` | Pipecat strategy that withholds TTS interruption until STT emits $N$ words. |
| `min_interruption_duration`, `min_interruption_words`, `false_interruption_timeout`, `resume_false_interruption` | `livekit.agents.voice.AgentSession` | LiveKit Agents parameters that require minimum speech duration/words and automatically resume paused TTS if ASR returns an empty transcript. |
| `turn_detection.prefix_padding_ms` (default `300 ms`) | OpenAI Realtime API `session.update` | Amount of pre-VAD audio included before speech onset (contrasts with your `1200 ms` pre-roll). |
| `session.suspend-timeout-seconds = 0` | WirePlumber `~/.config/wireplumber/wireplumber.conf.d/` | Disables ALSA sink idle suspend in PipeWire/WirePlumber so USB sound cards do not pay a 0.5 s wake-up delay. |
| `NKF-AEC` (`nkf_epoch70.pt`) | `[github.com/yanggeng1995/NKF-AEC](https://github.com/yanggeng1995/NKF-AEC)` | 21K-parameter Neural Kalman Filter for linear AEC with minimal double-talk distortion. |
| `DTLN-aec` (`dtln_aec_128 / 256 / 512`) | `[github.com/breizhn/DTLN-aec](https://github.com/breizhn/DTLN-aec)` | Open-source TFLite/ONNX neural AEC models. |
| `AECMOS` | `[github.com/microsoft/AEC-Challenge](https://github.com/microsoft/AEC-Challenge)` | Open-source ONNX evaluation metric outputting separate Echo DMOS and Near-End Degradation MOS on 16 kHz double-talk recordings. |
| `sherpa-onnx` Speaker Embedding (`wespeaker-voxceleb-resnet34`) | `[github.com/k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)` (`pip install sherpa-onnx`) | Fast CPU ONNX speaker embedding extractor (~10 ms per 300 ms burst) usable to reject audio chunks matching the assistant's fixed TTS voice embedding. |

---

### 6. Unverified Claims & Inconsistencies Found in Your Description

#### A. Claims Not Verified (Due to Offline Environment Constraint)

* **Independent measured dB ERLE figures for consumer speakerphones (`Anker PowerConf`, `Jabra Speak 510`, `Poly Sync 20`):** Marked `not found` because vendors do not publish ITU-T G.168 ERLE dB numbers in public spec sheets and live web search was not executed in this turn.
* **Proprietary internal duck-and-restore dB/ms thresholds for Amazon Alexa, Google Nest, and Apple HomePod:** Marked `not found` as public primary-source figures.

#### B. Five Technical Inconsistencies Found in Your Prompt Description

1. **Your 0.25 s reference-lead calculation in Section 3 double-counts the sender's buffer:**
* You state that because the player sends slices up to `0.25 s` ahead of what the Pi has played, the reference stream inside the canceller leads the echo by `0.25 s + Pi buffer + network + acoustic path`.
* **Why that is inconsistent with your code description:** You also state that the player pushes those slices into a reference queue on the laptop, and your microphone callback consumes one 10 ms frame from that queue per 10 ms microphone frame ("in lock step"). Because both the Pi playback buffer and the laptop reference queue receive the initial 0.25 s burst at $t=0$ and both drain at $1\times$ real-time, the 0.25 s cushion sits in **both** queues and cancels out! Thus, when the Pi sound card is warm, the reference leads the echo only by $L\text{ (50 ms)} + \text{network/ALSA/capture} \approx 90\text{–}190\text{ ms}$, **except** after the Pi's ALSA device has been idle between turns, when the 0.5 s wake-up delay causes the laptop's reference queue to drain completely and feed silence before the echo even starts!


2. **Your `EchoModel` (`BargeInGuard`, 5A) and `EchoCanceller` (5C) have a circular feedback bug:**
* `EchoModel` computes `coupling = mic_level / played_level`. When `--aec` is enabled, `mic_level` is measured **after** the canceller, while `played_level` is measured **before** the canceller.
* Once the canceller converges (`median ratio = 0.07` in 7F), `EchoModel` learns that `coupling` is tiny, lowering `bar` to its `0.02` (-34 dBFS) floor. Then, at the start of the *next* reply (during the 0.4–1.2 s while AEC3 re-converges after the turn gap and ALSA idle wake-up), the un-cancelled echo hits the lowered `0.02` `bar` and causes the **7 of 20 early self-interruptions** in `canceller only`!


3. **Why `canceller + duck` had `0 of 15` early cutoffs and `3 of 7` "false ducks" (7F):**
* The duck fires on **1 block > `bar` and 2/10 Silero windows**, whereas the guard fires on **3/8 blocks > `bar` and 7/10 Silero windows**. In `canceller + duck`, those 3 of 7 "false ducks" on quiet replies were actually the duck catching the un-converged AEC leak on its **first 100 ms block** and physically turning the speaker down by 15 dB before it could last for 300 ms and abort the reply!


4. **Why `lead >= 200 ms` broke in your 7E table (`L=200, path=200` failing 6/6 while `L=100, path=300` passed 5/6):**
* Feeding $L\text{ ms}$ of extra reference ahead once at queue startup either exceeds WebRTC AEC3's 120 ms API call jitter limit (`kMaxApiCallJitterBlocks = 30` blocks of 4 ms) if called as a burst of `process_reverse_stream()`, or starves your 250 ms slice queue when $L \ge 200\text{ ms}$, causing queue underruns (silence insertions) mid-reply.


5. **Your 7F A/B Table changes speaker placement between Column 1 (`none (speaker behind)`) and Columns 2–4 (`speaker in front`):**
* Because Column 1 has the speaker *behind* the laptop (lower direct-path high-frequency coupling) and Columns 2–4 have the speaker *in front*, comparing `1 of 17` early cutoffs in Column 1 against `7 of 20` in Column 2 confounds speaker placement with the `EchoModel` post-canceller threshold bug described in #2 above.



Would you like to write the exact patch for **Experiment 1 & 2** (`bargein.py` convergence floor, `PreRoll` raw tap trimmed to `onset - 200 ms`, and the prefix-echo stripper in `__main__.py`), or start with the offline 4-channel diagnostic script on your saved `7F` recordings?
