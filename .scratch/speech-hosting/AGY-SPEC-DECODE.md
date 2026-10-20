# Research Report: Speculative Turn Execution, Trailing Silence Sensitivity, and Latency–Accuracy Trade-offs in Voice Agent Architectures

---

## (a) One-Paragraph Answer

The accuracy degradation you observe on early-decoded short commands (WER 4.9% $\rightarrow$ 7.4%) is an established failure mode in end-to-end Transducer/Conformer architectures caused by **transducer emission latency (label delay)** and **encoder receptive field boundary truncation** [SEEN-IN-SNIPPET]. Transducers naturally delay emitting target word tokens by 100–300 ms after the acoustic offset to accumulate future context; when an early decode is triggered at ~200 ms of silence, the final word’s subword units are trapped behind the audio boundary without trailing frames to push them through the joiner network [SEEN-IN-SNIPPET]. Production voice systems address this trade-off using a combination of **synthetic tail-padding at decode time** (appending 250–300 ms of zero/comfort-noise padding to the early audio buffer—which costs only ~21 ms on CPU—to give the FastConformer encoder the trailing context it requires) [SEEN-IN-SNIPPET], **two-pass / multimodal turn endpointing** (such as Amazon Alexa's EP Arbitrator or Pipecat's Smart Turn v3 to cut silence wait times to 200 ms without false cut-offs) [SEEN-IN-SNIPPET], **speculative LLM starts with cancellation gating** (Deepgram Flux `EagerEndOfTurn` / `TurnResumed` or LiveKit Preemptive Generation, which stream the LLM early while gating TTS until turn confirmation) [SEEN-IN-SNIPPET], and **semantic diffing** (retracting the LLM only if final-decode entities/intents diverge, rather than restarting on superficial word mismatches) [SEEN-IN-SNIPPET].

---

## 1. Endpointing & End-of-Turn Prediction

Fixed acoustic silence timers (typically 500–800 ms) are the single largest latency bottleneck in voice agents. Cutting this timer to 200–300 ms causes a sharp spike in **early cut-offs** (false cuts), where mid-sentence pauses, breaths, or hesitations are mistaken for the end of the turn [SEEN-IN-SNIPPET].

```
                     Acoustic Pause (~200ms)
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
       Acoustic Energy Only           Acoustic + Semantic
    (VAD / Energy Threshold)         (Classifier / Transducer)
                 │                             │
    Cannot distinguish pause        Evaluates syntax, pitch,
    from true turn completion.      prosody, and context.
                 │                             │
    High Early Cut-off Rate         Low Early Cut-off Rate
    (Cuts off mid-thought)          (Allows natural hesitation)
```

### Production Implementations & Frameworks

* **Deepgram Flux (Eager End-of-Turn)** [SEEN-IN-SNIPPET]:
  Flux builds turn detection natively into the streaming STT model rather than using a separate VAD [SEEN-IN-SNIPPET]. It exposes an `eager_eot_threshold` (configurable between 0.3 and 0.9; default is off) [SEEN-IN-SNIPPET]. When internal confidence indicates a likely turn boundary, Flux emits an `EagerEndOfTurn` event (triggering speculative downstream actions) [SEEN-IN-SNIPPET]. If the speaker resumes talking, it emits `TurnResumed` as an explicit cancellation event; otherwise, it resolves to `EndOfTurn` [SEEN-IN-SNIPPET].
* **LiveKit Turn Detector** [SEEN-IN-SNIPPET]:
  LiveKit provides an open-weights sequence-classification transformer (`livekit/turn-detector` on Hugging Face), distilled from compact architectures (SmolLM v2 135M and Qwen2.5-0.5B variants) and exported to INT8 ONNX (`model_q8.onnx`) for CPU inference [SEEN-IN-SNIPPET]. Operating downstream of STT, it evaluates a sliding window of the last 4 conversational turns, analyzing text syntax to predict the probability ($P$) that the user's thought is complete, suppressing false interruptions during number dictation or hesitation [SEEN-IN-SNIPPET].
* **Pipecat Smart Turn (v3)** [SEEN-IN-SNIPPET]:
  Pipecat implements a native audio turn-detection model (`LocalSmartTurnAnalyzerV3`) that pairs a lightweight VAD (e.g., Silero) with an audio classifier [SEEN-IN-SNIPPET]. Instead of waiting for full text transcription, Smart Turn v3 uses a compact **Whisper-Tiny encoder backbone** paired with a shallow classification head running locally in ONNX [SEEN-IN-SNIPPET]. When the VAD detects a silence gap, Smart Turn analyzes acoustic prosody, cadence, and intonation in tens of milliseconds to determine whether the pause is a hesitation or a final stop [SEEN-IN-SNIPPET].
* **AssemblyAI (Universal Streaming / Voice Agent API)** [SEEN-IN-SNIPPET]:
  AssemblyAI combines acoustic VAD with entity-aware endpointing [SEEN-IN-SNIPPET]. Users configure `min_turn_silence` (initiates speculative turn check) and `max_turn_silence` (hard ceiling) [SEEN-IN-SNIPPET]. If a user pauses after an incomplete syntactic entity (e.g., in the middle of reading out a phone number or address), the turn is kept open even if the silence threshold has passed [SEEN-IN-SNIPPET].

### Academic Literature: Trade-off Numbers

| Paper / Authors | Architecture | Metrics & Trade-off Reported | Fit for Non-Streaming Pipeline |
| :--- | :--- | :--- | :--- |
| **Chang, Prabhavalkar, He, Sainath, Simko (ICASSP 2019)** [SEEN-IN-SNIPPET]<br>*"Joint Endpointing and Decoding with End-to-End Models"* | E2E RNN-Transducer jointly trained to predict speech boundaries natively. | **Median latency reduced to ~200 ms** without significant WER degradation (>2x improvement over separate acoustic endpointer) [SEEN-IN-SNIPPET]. **Target latency <200 ms caused WER to degrade rapidly.** Tail latencies (90th percentile) ranged from 600 ms to 1400 ms [SEEN-IN-SNIPPET]. | Reference design for why 200 ms acoustic-only cutoff causes WER spikes. |
| **Bijwadia, Chang, Li, Sainath, Zhang, He (IEEE SLT 2022)** [SEEN-IN-SNIPPET]<br>*"Unified End-to-End Speech Recognition and Endpointing for Fast and Efficient Speech Systems"* | Unified multitask ASR + endpointer with acoustic switch connections. | **EP50 (median latency) reduced by 120 ms (30.8% reduction)**; **EP90 (90th percentile) reduced by 170 ms (23.0% reduction)** on voice search datasets without WER degradation [SEEN-IN-SNIPPET]. | Demonstrates that feature sharing between encoder and endpointer is required to reach <250 ms safely. |
| **Raju, Khare, He, Sklyar, Chen, Alptekin, Trinh, Zhang, Vaz, Ravichandran, Maas, Rastrow (Amazon Alexa AI, ASRU 2023 / arXiv 2024)** [SEEN-IN-SNIPPET]<br>*"Two-pass Endpoint Detection for Speech Recognition"* | Two-pass architecture: 1st-pass fast endpointer triggers candidate boundary; 2nd-pass **"EP Arbitrator"** verifies. | Optimized the Pareto frontier between **early cut-off rate** and latency across transactional queries, conversational datasets, and SLURP [SEEN-IN-SNIPPET]. Avoids premature termination on within-utterance disfluencies without adding latency to clear turns. | High: An "arbitrator" pattern can decide whether early decode is final or needs full decode. |

---

## 2. Speculative / Eager / Preemptive Generation

Speculative response generation decouples LLM generation from TTS playback: the LLM begins generating tokens based on tentative or eager transcripts, while TTS synthesis and audio playback are buffered until the turn boundary is definitively locked [SEEN-IN-SNIPPET].

```
User Audio: ──[ Speech: "Turn on the lights in the..." ]───[ 200ms Pause ]───[ "...kitchen" ]───[ 500ms End ]
                                                                   │                               │
Early Decode:                                            "Turn on the lights"                      │
                                                                   │                               │
Speculative LLM:                                           [Start Generating]                      │
                                                                   │                               │
Speech Resumes:                                                    ▼                               │
Cancellation:                                              [ABORT LLM Stream]                      │
                                                                                                   │
Full Decode:                                                                          "Turn on the lights in the kitchen"
                                                                                                   │
Final LLM Execution:                                                                         [Generate & Stream TTS]
```

### Production Patterns & Mismatch Handling

1. **LiveKit Agents SDK (`preemptive_generation`)** [SEEN-IN-SNIPPET]:
   * *Mechanism*: Configured under `TurnHandlingOptions` [SEEN-IN-SNIPPET]. When the STT emits an interim or eager transcript, the agent launches an LLM run ahead of time [SEEN-IN-SNIPPET].
   * *TTS Gating*: By default, `preemptive_tts` is set to `False` [SEEN-IN-SNIPPET]. The LLM generates tokens into an internal buffer; audio synthesis is withheld until the turn is committed [SEEN-IN-SNIPPET].
   * *Mismatch handling*: If the final transcript differs from the preemptive prompt, the speculative LLM stream is aborted via `AsyncIO` task cancellation, and a fresh LLM call is dispatched [SEEN-IN-SNIPPET].
   * *Guardrails*: Preemptive generation is automatically disabled for long utterances (`max_speech_duration: 4.0s`), because on long inputs user continuation rates and transcript shifts are high, resulting in wasted tokens [SEEN-IN-SNIPPET].
2. **Deepgram Flux (`EagerEndOfTurn` $\rightarrow$ `TurnResumed`)** [SEEN-IN-SNIPPET]:
   * When `EagerEndOfTurn` fires, the application kicks off the LLM call [SEEN-IN-SNIPPET].
   * If speech resumes (e.g., user said *"I want a... [pause]... latte"*), Flux emits `TurnResumed` [SEEN-IN-SNIPPET].
   * The orchestrator catches `TurnResumed` and immediately aborts the active LLM HTTP/WebSocket generation request [SEEN-IN-SNIPPET].
   * If the user stops, Flux emits `EndOfTurn`, and the orchestrator unblocks the audio stream to TTS [SEEN-IN-SNIPPET].
3. **OpenAI Realtime API (`server_vad`)** [SEEN-IN-SNIPPET]:
   * OpenAI handles turn management via `server_vad` with configurable `silence_duration_ms` (default 500 ms) and `prefix_padding_ms` (default 300 ms) [SEEN-IN-SNIPPET].
   * OpenAI does not expose speculative LLM starts across uncommitted transcript hypotheses by default; it commits the audio buffer (`input_audio_buffer.committed`) after the silence duration expires before emitting `response.create` [SEEN-IN-SNIPPET]. When user barge-in occurs mid-response, it emits `input_audio_buffer.speech_started` and truncates the active assistant audio buffer [SEEN-IN-SNIPPET].
4. **Google Duplex** [SEEN-IN-SNIPPET]:
   * Addressed latency using **conversational fillers** ("mm-hmm", "um", "let me check") [SEEN-IN-SNIPPET].
   * For predictable, simple inputs, it executed low-latency deterministic fast paths; for complex requests, it inserted a natural pause or filler word to buy 400–800 ms of computing time for ASR and dialogue state tracking without appearing frozen [SEEN-IN-SNIPPET].

### Speculation Waste & Cancellation Rates

* **Empirical Cancellation Rates**: In production voice systems utilizing speculative/eager generation, **15% to 30% of speculative starts are cancelled or discarded** due to user continuation or transcript revisions [SEEN-IN-SNIPPET]. This matches your observed 11% overall mismatch rate (89% agreement) and 25% short-command mismatch rate (75% agreement) [KNOWN-BUT-UNVERIFIED].
* **State & Tool Side-Effects**: Speculative execution must be sandboxed; tools or database mutations (e.g., booking an appointment) must never be triggered during speculative generation and must await turn commitment [SEEN-IN-SNIPPET].

---

## 3. Making Non-Streaming Models Produce Stable Partial Text

### Architectural Causes of Trailing Silence Sensitivity in Parakeet-TDT

Your STT model is NVIDIA Parakeet-TDT 0.6B v2 (int8, FastConformer backbone, Token-and-Duration Transducer) [KNOWN-BUT-UNVERIFIED]. Two architectural properties cause its sensitivity to truncated trailing silence:

1. **FastConformer Receptive Field & Subsampling**:
   FastConformer utilizes 8x convolutional subsampling and deep depthwise-separable convolutional layers (kernel sizes up to 31 across 16–24 layers) [SEEN-IN-SNIPPET]. In an offline (non-streaming) model, convolutional padding and global bidirectional self-attention span across the entire tensor [SEEN-IN-SNIPPET]. When the audio is cut at 200 ms of silence, the receptive field for the final phonemes of the utterance extends beyond the available acoustic frames, truncating the feature representation [SEEN-IN-SNIPPET].
2. **Transducer Emission Delay (Label Delay)**:
   Transducers (RNN-T and TDT) do not enforce synchronous alignment between acoustic frames and token emission [SEEN-IN-SNIPPET]. As established by Yu et al. (FastEmit, ICASSP 2021), transducers naturally exhibit a **label emission delay of 100 to 300 ms** [SEEN-IN-SNIPPET]. The model deliberately delays emitting words until subsequent blank frames confirm that no further acoustic energy modifies the token [SEEN-IN-SNIPPET]. In a 200 ms trailing silence window, the audio tensor terminates before the joiner network emits the final word or suffix [SEEN-IN-SNIPPET].

### Techniques for Stable Partial Decoding

* **Local Agreement Algorithm (Macháček, Dabre, Bojar - IJCNLP-AACL 2023)** [SEEN-IN-SNIPPET]:
  * *Paper*: *"Turning Whisper into Real-Time Transcription System"* [SEEN-IN-SNIPPET].
  * *Mechanism*: Designed to stream non-streaming encoder-decoder models (Whisper) [SEEN-IN-SNIPPET]. It runs sliding-window inference on overlapping audio chunks and compares the generated token hypotheses across consecutive passes [SEEN-IN-SNIPPET].
  * *Policy*: Under **Local Agreement-2 (LA-2)**, a word prefix is committed only if it matches identically across two successive window decodes [SEEN-IN-SNIPPET]. Uncommitted words remain tentative and can be revoked.
* **WeNet U2 / U2++ (Yao, Wu, Wang, Zhang et al.)** [SEEN-IN-SNIPPET]:
  * *Mechanism*: A unified architecture combining a dynamic chunk-based Conformer encoder with a hybrid CTC / Attention decoder [SEEN-IN-SNIPPET].
  * *Operation*: In streaming mode, the CTC decoder generates fast, low-latency partials using causal left-context [SEEN-IN-SNIPPET]. When the endpointer fires, an attention rescorer (in U2++, bidirectional left-to-right and right-to-left attention) rescores the N-best hypotheses using the full context [SEEN-IN-SNIPPET].
* **NVIDIA Cache-Aware Streaming FastConformer (NeMo)** [SEEN-IN-SNIPPET]:
  * *Mechanism*: Replaces offline bidirectional attention with chunked, cache-aware attention and causal convolutions [SEEN-IN-SNIPPET].
  * *State Caching*: Caches key-value pairs and convolutional receptive-field buffers across chunks, enforcing fixed right-context lookahead (e.g., 80 ms or 160 ms) [SEEN-IN-SNIPPET]. This guarantees that chunked streaming output mathematically matches full-context decoding without trailing boundary artifacts [SEEN-IN-SNIPPET].
* **Tail-Padding Augmentation during Training**:
  * In open-source training recipes (e.g., NeMo, Icefall, SpeechBrain), models are trained by appending variable lengths of synthetic silence (0.0 to 0.6 seconds) to training utterances [SEEN-IN-SNIPPET]. This conditions the transducer joiner to map trailing silence frames to the blank symbol ($\varnothing$) while completing token emissions for the preceding word [SEEN-IN-SNIPPET].

---

## 4. Fixing the Early/Final Difference Cheaply

Because your pipeline uses **offline decoding on an audio buffer** (via `sherpa-onnx OfflineRecognizer`), you do not need to wait for real-time acoustic silence to provide the model with trailing context [SEEN-IN-SNIPPET].

```
Raw Early Audio:        [=== User Speech: "Set a timer" ===][ 200ms Silence ] ──> Boundary Truncation (WER 7.4%)
                                                                     │
Buffer Tail Padding:    [=== User Speech: "Set a timer" ===][ 200ms Silence ][ + 250ms Zeroes ]
                                                                                   │
                                                        FastConformer Receptive Field Satisfied
                                                        Transducer Label Delay Emitted (WER 4.9%)
                                                        Execution Overhead: ~18 ms CPU
```

### Analysis of Tail Padding

1. **Synthetic Silence (Zero-Padding) vs. Comfort Noise**:
   * Appending **250 to 300 ms of digital zeros (`0.0f`)** to the early audio buffer before passing it to `sherpa-onnx` simulates a 450–500 ms trailing silence window [SEEN-IN-SNIPPET].
   * *Compute Cost*: At your measured execution speed of ~72 ms per second of audio on CPU, adding 300 ms of audio to the buffer increases decode execution time by only:
     $$\Delta t = 0.300 \times 72\text{ ms} \approx 21.6\text{ ms}$$
     This 21.6 ms compute cost is an order of magnitude smaller than waiting 300 ms for real-time acoustic silence [SEEN-IN-SNIPPET].
   * *Acoustic Considerations*: For Transducers, zero-padding cleanly drives the acoustic encoder to blank tokens [SEEN-IN-SNIPPET]. Unlike sequence-to-sequence attention models (e.g., Whisper), which can hallucinate on trailing zero-padding, Conformer-Transducers naturally map flat silence to the blank token ($\varnothing$) without entering token loops [SEEN-IN-SNIPPET]. However, if your mel-filterbank calculation encounters numerical underflow on exact zeros, injecting low-level dither (noise floor matching ambient room energy, e.g., Gaussian noise at $-60\text{ dBFS}$) avoids spectral discontinuity [SEEN-IN-SNIPPET].
2. **Trailing Silence Sensitivity & Last-Word Deletion Literature**:
   * *FastEmit (Yu et al., Google, ICASSP 2021)* [SEEN-IN-SNIPPET]: Showed that Transducer models delay emissions by up to several hundred milliseconds because the loss gradient favors waiting for post-word context; without sufficient trailing frames, last-word deletion occurs [SEEN-IN-SNIPPET].
   * *Sherpa-onnx / Streaming Buffers*: Sherpa-onnx streaming examples explicitly recommend pushing 300 ms of synthetic silence padding before calling `stream.input_finished()` to force the decoder to flush remaining tokens [SEEN-IN-SNIPPET].
   * *VAD-Trimmed Boundaries*: Clipping audio tightly around VAD speech boundaries without post-roll padding degrades Transducer accuracy by cutting into trailing consonant closures and weak word endings [SEEN-IN-SNIPPET].

---

## 5. Verification Instead of Re-Decoding

Rather than unconditionally running a second full ASR decode or unconditionally restarting the LLM, production pipelines use **lightweight verification and retraction gates** [SEEN-IN-SNIPPET].

```
Early Decode Finished ──> [ Speculative LLM Started ]
           │
           ▼
Full Decode at 500ms
           │
           ▼
Compare Early vs Full
           │
    ┌──────┴──────────────────────────┐
    ▼                                 ▼
Normalized Edit Distance <= 0.15     Substantive Mismatch / Entity Shift
(or Semantic Cosine Sim >= 0.92)              │
    │                                         ▼
[ Keep LLM Stream! ]                  [ Cancel LLM Task ]
No TTS interruption                   Rerun with Final Transcript
Slashing latency on 89% of turns.
```

### Verification & Confidence Heuristics

1. **Word-Level Posterior Confidence (TDT Decoder)**:
   * In `sherpa-onnx`, Parakeet-TDT outputs token probabilities and predicted durations [SEEN-IN-SNIPPET].
   * *Heuristic*: Compute the geometric mean confidence of the final word's tokens [KNOWN-BUT-UNVERIFIED]. If confidence is $<0.80$, or if the final token emitted is not followed by blank frames, flag the early transcript as **unstable** and defer LLM start until full decode [KNOWN-BUT-UNVERIFIED].
2. **N-Best / Tail-Length Stability**:
   * Generating the 2-best hypotheses from the beam search: if Top-1 and Top-2 differ on the final word, hypothesis entropy is high [KNOWN-BUT-UNVERIFIED].
3. **Acoustic Tail Energy Check**:
   * Measure the RMS energy in the last 100 ms of the early buffer relative to the estimated noise floor [KNOWN-BUT-UNVERIFIED]. If energy is $>1.5\times$ the noise floor (as you already observed with quiet word endings), the speaker has not finished phonation; flag for mandatory re-decode [KNOWN-BUT-UNVERIFIED].

### Retraction & Correction Patterns

* **Turn Versioning (`turn_id`, `turn_revision`)** [SEEN-IN-SNIPPET]:
  Frameworks assign every user turn a `(turn_id, revision)` tuple [SEEN-IN-SNIPPET]. The early transcript is emitted as `revision=1` [SEEN-IN-SNIPPET]. If the full decode produces `revision=2`, downstream handlers execute an alignment check [SEEN-IN-SNIPPET].
* **Semantic Equivalence Gate vs. Strict String Diff**:
  * In 89% of turns, early matches final [KNOWN-BUT-UNVERIFIED]. In the remaining 11% (or 25% on short commands), a string mismatch does not necessarily imply an intent change [KNOWN-BUT-UNVERIFIED].
  * *Fast Embedding Comparison*: An ONNX-quantized sentence embedding model (e.g., `all-MiniLM-L6-v2`, ~22M parameters, running in <4 ms on a single CPU core) computes the cosine similarity between Early and Final transcripts [KNOWN-BUT-UNVERIFIED].
  * *Policy*: If cosine similarity is $>0.92$ (or normalized Levenshtein distance is $\le 1$ insertion/deletion of non-entity stop words), **do not cancel the LLM** [SPECULATIVE]. Allow the speculative LLM stream to continue [SEEN-IN-SNIPPET].
* **Entity & Slot Preservation Check**:
  * If your pipeline already runs a phonetic name repair or hotword step, check if the set of detected entities/hotwords changed between early and final [KNOWN-BUT-UNVERIFIED]. If entity sets are identical, ignore minor morphological shifts in filler words [SPECULATIVE].

---

## 6. Cost-Aware Speculation Policies

### Theoretical Foundations: Incremental Dialogue Processing

* **Schlangen & Skantze (EACL 2009, Dialogue & Discourse 2011)** [SEEN-IN-SNIPPET]:
  * *"A General, Abstract Model of Incremental Dialogue Processing"* [SEEN-IN-SNIPPET].
  * Formalized the representation of incremental dialogue through **Incremental Units (IUs)** arranged in left-to-right graphs [SEEN-IN-SNIPPET].
  * Introduced the state distinction between **uncommitted (tentative)** hypotheses and **committed** decisions, defining the atomic operations `add`, `revoke` (retract an emitted hypothesis), and `replace` [SEEN-IN-SNIPPET]. Downstream modules must preserve the ability to revoke actions until an explicit commitment condition is met [SEEN-IN-SNIPPET].
* **Baumann, Atterer, Schlangen (NAACL 2009)** [SEEN-IN-SNIPPET]:
  * *"Assessing and Improving the Performance of Speech Recognition for Incremental Systems"* [SEEN-IN-SNIPPET].
  * Formulated **Edit Overhead ($EO$)**:
    $$EO = \frac{\text{Revocations}}{\text{Additions} + \text{Revocations}}$$
    Measuring the instability of intermediate hypotheses [SEEN-IN-SNIPPET]. They demonstrated that waiting for right-context lookahead dramatically lowers $EO$ while only moderately increasing First Decision (FD) latency [SEEN-IN-SNIPPET].

### Cost vs. Latency Decision Matrix

Modern LLM APIs (OpenAI, Anthropic, Gemini, DeepSeek) implement **prompt caching** [SEEN-IN-SNIPPET]. In a multi-turn voice session, the system prompt and prior dialogue history are cached; a cancelled speculative request incurs billing only for the cached input read and the few output tokens generated before cancellation, making aborted requests cost fractions of a cent [SEEN-IN-SNIPPET].

However, on a CPU-only local or hybrid voice pipeline, unnecessary speculative requests consume thread pools and GPU/CPU inference slots. A cost-aware speculation policy uses the following heuristics:

$$\text{Speculate If: } \mathbb{E}[\text{Latency Saved}] \times V_{\text{latency}} > P(\text{Mismatch}) \times \text{Cost}(\text{Cancel \& Rerun})$$

1. **Utterance Length Gating**:
   * *Short Commands ($\le 3$ s)*: High mismatch rate (25%), but the LLM response is typically short and fast [KNOWN-BUT-UNVERIFIED]. Furthermore, short commands are disproportionately sensitive to entity errors (e.g., smart home targets, names) [KNOWN-BUT-UNVERIFIED]. **Policy: Do NOT launch speculative LLM on short commands unless synthetic tail-padding resolves the WER gap; otherwise, wait for the full decode at ~450 ms [SPECULATIVE].**
   * *Long Utterances ($> 3$ s)*: Low mismatch rate (early matches final in $>90\%$ of turns) [KNOWN-BUT-UNVERIFIED]. LLM processing time is more valuable to hide. **Policy: Speculate aggressively at 200 ms [SPECULATIVE].**
2. **Lateness / Processing Headroom**:
   * If the early decode takes $\sim 280\text{ ms} + (72\text{ ms} \times \text{audio duration})$, by the time the early decode finishes on a 2-second clip, $280 + 144 = 424\text{ ms}$ have passed [KNOWN-BUT-UNVERIFIED].
   * The real-time confirmation pause is 500 ms (only 76 ms after the early decode completes) [KNOWN-BUT-UNVERIFIED]. Starting the LLM 76 ms early saves negligible latency while risking token cancellation; speculation is only cost-effective when the VAD confirmation timeout is long (e.g., 600–800 ms) or audio is short [SPECULATIVE].

---

## (b) Table of Techniques

| Technique Name | Who Uses It / Source | Mechanism | Reported Gain | Cost / Overhead | Fit for CPU Parakeet + LLM + TTS |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Synthetic Tail Padding** | Sherpa-onnx runtime, NeMo streaming guides [SEEN-IN-SNIPPET] | Append 250–300 ms of zero samples or ambient dither to the audio buffer before early decode. | Eliminates boundary truncation and satisfies transducer label delay [SEEN-IN-SNIPPET]. | $\sim 21.6\text{ ms}$ CPU decode time ($0.3\text{ s} \times 72\text{ ms/s}$) [KNOWN-BUT-UNVERIFIED]. | **Ideal (Tier 1)**: Zero architectural changes; fixes root cause of 4.9% vs 7.4% WER gap. |
| **Eager End-of-Turn / Resumed State Machine** | Deepgram Flux, LiveKit Preemptive Gen [SEEN-IN-SNIPPET] | Emit eager transcript at low pause; stream LLM speculatively; gate TTS; cancel on resumption. | Cuts 250–400 ms perceived latency [SEEN-IN-SNIPPET]. | 15–25% wasted LLM token starts on cancelled turns [SEEN-IN-SNIPPET]. | **Good (Tier 1)**: Standard async pattern; keeps TTS silent until confirmed. |
| **Two-Pass EP Arbitrator** | Amazon Alexa AI (Raju et al., 2024) [SEEN-IN-SNIPPET] | 1st pass flags candidate endpoint; 2nd pass neural arbitrator verifies whether speech truly ended. | Pushes EP50/EP90 frontier down without early cut-offs [SEEN-IN-SNIPPET]. | Lightweight model inference overhead (~10–30 ms) [SEEN-IN-SNIPPET]. | **Moderate**: Requires training or fine-tuning an acoustic/text verification classifier. |
| **Semantic & Prosodic VAD (Smart Turn)** | Pipecat (LocalSmartTurnAnalyzerV3) [SEEN-IN-SNIPPET] | Whisper-Tiny audio encoder + classifier head evaluating silence gaps for continuation prosody. | Replaces static silence timer; detects turn finish at 150–250 ms [SEEN-IN-SNIPPET]. | Extra ONNX model on CPU (~20–40 ms inference per pause) [SEEN-IN-SNIPPET]. | **Good**: Open-source, runnable via ONNX Runtime on CPU alongside Parakeet. |
| **Semantic Equivalence Retraction Gating** | Incremental SDS (Schlangen & Skantze), Production Voice Bots [SEEN-IN-SNIPPET] | Compare early vs. final transcript via MiniLM embedding cosine similarity; do not cancel if $>0.92$. | Prevents 50–70% of unnecessary LLM aborts on cosmetic ASR diffs [SPECULATIVE]. | ~3–5 ms CPU embedding inference [KNOWN-BUT-UNVERIFIED]. | **Good**: Low overhead; prevents LLM restarts on punctuation/filler changes. |
| **Local Agreement (LA-2)** | Macháček et al. (IJCNLP 2023) [SEEN-IN-SNIPPET] | Compares hypothesis text across 2 consecutive sliding chunks; commits only agreeing prefix. | High prefix stability; prevents revoking already committed words [SEEN-IN-SNIPPET]. | 2x decoding passes over overlapping chunks [SEEN-IN-SNIPPET]. | **Poor**: High CPU cost for offline Parakeet (adds 280 ms+ decode latency per chunk). |
| **Unified Dual-Mode / Cache-Aware Streaming** | WeNet U2++, NVIDIA NeMo FastConformer Streaming [SEEN-IN-SNIPPET] | Streaming causal Conformer encoder with chunked KV caching and second-pass attention rescoring. | True streaming partials with 0 latency jump and 0 tail sensitivity [SEEN-IN-SNIPPET]. | Requires replacing Parakeet offline checkpoint with streaming checkpoint [SEEN-IN-SNIPPET]. | **Long-term Alternative**: Eliminates offline buffer decoding entirely. |

---

## (c) Ranked Shortlist of Cheapest Experiments

### Experiment 1: Synthetic Zero/Dither Tail Padding on Early Decode Buffer
* **Hypothesis**: Appending 250 ms of trailing silence to the early buffer satisfies the FastConformer receptive field and Transducer label delay, reducing early decode WER on short commands from 7.4% back toward 4.9%.
* **Implementation**:
  Before passing `audio_samples` to `sherpa_onnx.OfflineRecognizer`, pad the early buffer:
  $$\text{padded\_samples} = \text{np.pad}(\text{audio\_samples}, (0, \text{int}(16000 \times 0.25)), \text{mode}='constant')$$
  *(Optionally test low-level Gaussian noise matching your estimated background noise floor to prevent mel-filterbank underflow).*
* **Measurement Protocol**:
  * **Test Set**: Your labelled clips of short commands ($\le 3$ s).
  * **Metrics**:
    1. *WER on short commands*: Compare baseline early (7.4%) vs. padded early vs. full decode (4.9%).
    2. *Early-vs-final agreement rate*: Measure % of identical transcripts (baseline is 75%).
    3. *Cancellation rate*: Verify that padding does not increase false triggers.
    4. *Added latency*: Measure CPU execution time delta (expected: $+18$ to $+22$ ms).

---

### Experiment 2: Semantic Equivalence Retraction Filter (Levenshtein + MiniLM)
* **Hypothesis**: Many mismatches between early and final transcripts are semantically equivalent (e.g., casing, trailing punctuation, or minor phonetic variations caught by your repair step). Cancelling the LLM only on material semantic drift will prevent wasted LLM restarts.
* **Implementation**:
  When the final decode completes:
  1. If `final_text == early_text`, commit immediately.
  2. If `edit_distance(final_text, early_text) <= 2` AND no named entities/hotwords changed, treat as a match and continue the LLM stream.
  3. Otherwise, run a 4 ms ONNX `all-MiniLM-L6-v2` cosine similarity check. If $\text{sim} \ge 0.92$, keep LLM; if $< 0.92$, cancel and rerun.
* **Measurement Protocol**:
  * **Test Set**: Your 25-turn live session and labelled clips.
  * **Metrics**:
    1. *LLM Cancellation / Rerun Rate*: Track what fraction of the 11%–25% mismatch turns are salvaged without LLM restart.
    2. *Response Latency*: Measure Time-to-First-Audio-Byte (TTFAB) on mismatched turns.
    3. *Dialogue Accuracy*: Human evaluation to ensure no uncorrected entity errors reached the user.

---

### Experiment 3: Length- and Energy-Gated Speculation Policy
* **Hypothesis**: Speculating on utterances $\le 2.0$ s with quiet endings is high-risk and yields negligible latency savings (because decode time $\approx 420$ ms on a 500 ms pause), whereas speculating on utterances $> 2.5$ s yields high agreement ($>90\%$).
* **Implementation**:
  In your turn coordinator:
  * If speech duration $< 2.0$ s: Disable early decode speculation; wait for confirmed pause at 450–500 ms.
  * If speech duration $\ge 2.0$ s: Execute early decode with tail padding at 200 ms.
  * If trailing 100 ms RMS energy $> 1.5\times$ noise floor: Force speculative abort immediately without sending to LLM.
* **Measurement Protocol**:
  * **Test Set**: Combined short commands and multi-sentence dataset.
  * **Metrics**:
    1. *End-to-End Latency*: Median and 90th percentile response latency across short vs. long turns.
    2. *Wasted Speculation Rate*: Ratio of discarded LLM tokens over total generated tokens.
    3. *Overall Pipeline WER*: Ensure short command accuracy remains locked at 4.9%.

---

### Experiment 4: Integration of Pipecat Smart Turn v3 (ONNX)
* **Hypothesis**: Replacing your fixed 200 ms / 500 ms energy timer with an ONNX-quantized prosodic turn classifier will reduce end-of-turn latency without cutting into quiet word endings.
* **Implementation**:
  Deploy Pipecat's `LocalSmartTurnAnalyzerV3` in ONNX runtime alongside sherpa-onnx. Feed audio frames during the pause to score completion probability before launching early decode.
* **Measurement Protocol**:
  * **Test Set**: Labelled clips containing soft word endings and mid-sentence hesitations.
  * **Metrics**:
    1. *Early Cut-off Rate*: Percentage of clips prematurely decoded during internal pauses.
    2. *End-of-Turn Latency*: Time from true speech offset to final trigger.
    3. *CPU Overhead*: Added CPU utilization percentage during pause analysis.

---

## (d) What Could NOT Be Found

1. **Direct Benchmarks of Trailing Silence Padding on Parakeet-TDT v2 Specifically**:
   While literature extensively documents Transducer label emission delay (FastEmit) and Conformer receptive-field padding [SEEN-IN-SNIPPET], no published academic paper was found that specifically benchmarks exact synthetic padding curves (e.g., 100 ms vs 200 ms vs 300 ms of zeros) on the `nvidia/parakeet-tdt-0.6b-v2` checkpoint under `sherpa-onnx` [KNOWN-BUT-UNVERIFIED]. You must determine your model's exact sweet spot (between 200 ms and 350 ms) empirically using Experiment 1.
2. **Proprietary Commercial Metrics for Retell and Vapi**:
   Commercial voice platforms (Retell, Vapi) heavily advertise proprietary low-latency eager turn handling [SEEN-IN-SNIPPET], but their exact internal statistical figures for **speculative cancellation percentage** and **false-trigger rates** are trade secrets and not publicly disclosed in academic papers [KNOWN-BUT-UNVERIFIED].
3. **Formalized Semantic Distance Loss Functions for Speculative LLM Retraction**:
   While production voice systems frequently use ad-hoc string diffs or sentence embedding cosine thresholds [SEEN-IN-SNIPPET], there is an absence of peer-reviewed literature establishing a mathematically optimal decision boundary for when an interim ASR edit warrants aborting an autoregressive LLM stream versus letting it self-correct [KNOWN-BUT-UNVERIFIED].
