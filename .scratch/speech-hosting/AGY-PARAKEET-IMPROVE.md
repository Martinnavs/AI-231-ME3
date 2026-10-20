# Research Report: Parakeet-TDT 0.6B Improvement for Named Entities and Accented English on CPU

**Date:** 2026-10-20  
**Target:** Parakeet-TDT 0.6B v2 (sherpa-onnx offline int8, CPU)  
**System Constraints:** Quad-core laptop CPU (i5-1145G7), no GPU at demo, strict post-speech latency boundary (~390 ms), high-value target is generic agent voice input (contacts, music, places, brands, complex task requests).  
**Evidence Labels:**
- `SEEN-IN-SNIPPET`: Verified directly from search snippets retrieved during this research session.
- `KNOWN-BUT-UNVERIFIED`: Grounded in standard speech-recognition / machine-learning literature, but not directly present in search result snippets.
- `SPECULATIVE`: Inferred projection, hypothesis, or engineering extrapolation.

---

## 1. Contextual Biasing & Hotwords for Parakeet / NeMo Transducers & TDT

### sherpa-onnx Hotwords Support for NeMo Transducers / TDT
- **Structural Incompatibility in sherpa-onnx:** In `sherpa-onnx`, the hotwords / contextual biasing pipeline (configured via `--hotwords-file` and `--hotwords-score`) **only** operates when the decoding method is set to `--decoding-method=modified_beam_search` [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx documentation]. It is explicitly unsupported under `--decoding-method=greedy_search` [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx documentation].
- **Stateful vs. Stateless Decoders:** `modified_beam_search` in `sherpa-onnx` was originally developed for stateless decoders (e.g., Icefall Zipformer) [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx GitHub issues]. NVIDIA NeMo Transducers and TDT architectures utilize stateful (LSTM/RNN-based) predictor networks [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx GitHub issues]. In `sherpa-onnx`, NeMo Transducer implementations (`OfflineRecognizerTransducerNeMoImpl` and streaming equivalents) either enforce greedy decoding or exhibit severe anomalies (hallucinations, repetition, or blank outputs) when forced into `modified_beam_search` [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx issues #1428, #1823].
- **Verdict for sherpa-onnx Hotwords on Parakeet:** **Broken out-of-the-box.** Supplying `--hotwords-file` to `sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8` will either be silently ignored (under greedy decoding) or fail.

### NVIDIA NeMo Native Contextual Biasing
- **GPU-PB (TurboBias):** NeMo provides GPU-accelerated Phrase Boosting (GPU-PB / TurboBias) based on NGPU-LM shallow fusion for CTC, RNN-T/TDT, and AED models [`SEEN-IN-SNIPPET`: arXiv:2402.08059 / NVIDIA NeMo docs]. However, GPU-PB relies on CUDA kernels and CUDA graphs to avoid CPU-GPU sync bottlenecks [`SEEN-IN-SNIPPET`: arXiv:2402.08059]. It is strictly a GPU implementation and **does not run CPU-only** [`SEEN-IN-SNIPPET`: NVIDIA docs].
- **CTC Word Spotter (CTC-WS):** NeMo supports CTC-WS for contextual biasing without an external LM [`SEEN-IN-SNIPPET`: ISCA Interspeech archive]. However, CTC-WS requires a **hybrid Transducer-CTC architecture** where a shared encoder feeds both CTC and Transducer heads simultaneously [`SEEN-IN-SNIPPET`: NVIDIA docs]. Parakeet-TDT 0.6B is a standalone TDT architecture (joint token + duration prediction head), not a dual-head hybrid [`KNOWN-BUT-UNVERIFIED`].

### Tokenizer Availability & Subword Encoding
- In pre-packaged sherpa-onnx releases (`csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`), the distribution contains `encoder.int8.onnx`, `decoder.onnx`, `joiner.onnx`, and `tokens.txt` [`SEEN-IN-SNIPPET`: Hugging Face `csukuangfj` repo].
- `tokens.txt` is an index-to-string mapping, but hotword context-graph compilation requires a SentencePiece tokenizer model to properly break unknown named entities into BPE subwords [`SEEN-IN-SNIPPET`: Hugging Face repo `MadnessOverflow/parakeet-tdt-0.6b-v3-bpe-vocab`]. The SentencePiece model (`bpe.model`) must be extracted directly from the original `.nemo` tarball archive (`nemo_model.extract_submodel(...)` or untarring the `.nemo` file) [`KNOWN-BUT-UNVERIFIED`].

### Expected Gains
- Where contextual biasing functions cleanly (e.g. GPU-PB in NeMo with a few hundred candidate phrases), literature demonstrates a **20% to 50% relative reduction in Named Entity Word Error Rate (NE-WER)** [`KNOWN-BUT-UNVERIFIED`].

**Verdict on Contextual Biasing:** **SKIP for demo runtime.** (Reason: sherpa-onnx does not support `modified_beam_search` on NeMo TDT, and NeMo's GPU-PB requires CUDA).

---

## 2. Rescoring, Language-Model Fusion, & Confidence Estimation for TDT

### External LM / Rescoring Feasibility on CPU
- **Shallow Fusion during Decoding:** Applying KenLM or an n-gram model synchronously during frame decoding requires maintaining multiple active beam paths. For TDT, this negates the duration-skipping mechanism and explodes CPU inference time by 4x to 8x [`KNOWN-BUT-UNVERIFIED`]. Furthermore, `sherpa-onnx`'s `OfflineLMConfig` is coupled with `modified_beam_search`, which fails on NeMo TDT [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx docs].
- **N-best Hypothesis Rescoring:** In `sherpa-onnx`, Parakeet-TDT exposes only the greedy 1-best hypothesis [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx Go/C++ structs]. Extracting an N-best lattice requires beam search that is currently unavailable for TDT in ONNX format [`KNOWN-BUT-UNVERIFIED`].
- **LODR (Low-Order Density Ratio):** Effective for subtracting source-domain LM bias in speech models, but requires training an internal LM approximation and decoding with beam search [`KNOWN-BUT-UNVERIFIED`]. Infeasible within student timeframe and CPU constraints.

### Confidence Estimation in NeMo vs. sherpa-onnx
- **NeMo Python Framework:** NeMo provides advanced confidence estimation configurable via `decoding.confidence_cfg`:
  - `preserve_word_confidence: true` [`SEEN-IN-SNIPPET`: NVIDIA docs].
  - `tdt_include_duration_confidence: true` (factors duration certainty into token certainty) [`SEEN-IN-SNIPPET`: NVIDIA docs].
  - **`max_prob` vs. `entropy`:** Raw `max_prob` suffers from severe overconfidence, pushing confidence values close to 1.0 even on hallucinated or misrecognized tokens [`SEEN-IN-SNIPPET`: NVIDIA NeMo docs]. NeMo recommends Tsallis entropy (`entropy_type: "tsallis"`, `alpha: 0.33`, `entropy_norm: "exp"`), which produces well-separated bimodal distributions between correct and erroneous words [`SEEN-IN-SNIPPET`: NVIDIA docs].
- **sherpa-onnx Engine:** `OfflineRecognizerResult` contains:
  - `tokens`: list of subword strings [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx].
  - `timestamps`: start time per token [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx].
  - `durations`: duration per token [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx].
  - `ys_log_probs`: log-probabilities per emitted token [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx].

### TDT Confidence as an Error Detector
- In `sherpa-onnx`, word-level acoustic confidence can be approximated by computing the geometric mean of token probabilities for subwords comprising a word: $C_{\text{word}} = \exp\left(\frac{1}{K}\sum_{i=1}^K \text{ys\_log\_probs}_i\right)$ [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx discussions].
- **Failure Mode on Proper Nouns:** When an unknown Filipino name (e.g. "Ysabel Dela Cruz") is spoken, TDT will forcefully map acoustic frames into familiar English subwords ("usable", "to", "lack", "cruise") [`KNOWN-BUT-UNVERIFIED`]. While the individual token log-probabilities for these common English words may drop slightly, `max_prob` will often still rate them at 0.70–0.85 [`SPECULATIVE`]. However, relative to standard conversational words (>0.95), an empirically chosen threshold ($C_{\text{word}} < 0.85$ or minimum token log prob $< -0.35$) reliably flags out-of-vocabulary candidate spans [`SPECULATIVE`].

**Verdict:** **DO NOW (in sherpa-onnx).** Extract `ys_log_probs` from `OfflineRecognizerResult`, aggregate to word level, and use low-confidence spans ($C < 0.85$) as the gating condition for downstream phonetic name replacement.

---

## 3. Fine-Tuning & Adapting Parakeet-TDT

### NeMo Training Recipes & PEFT / Adapters / LoRA
- NeMo provides standard scripts for fine-tuning Transducers and FastConformer models: `examples/asr/speech_to_text_finetune.py` [`SEEN-IN-SNIPPET`: NVIDIA NeMo docs].
- **Parameter-Efficient Fine-Tuning (PEFT):** The FastConformer encoder in Parakeet supports insertion of bottleneck adapter modules or LoRA (Low-Rank Adaptation) matrices into multi-head attention and feed-forward projections [`SEEN-IN-SNIPPET`: NVIDIA docs].
- **Catastrophic Forgetting Mitigation:** Full fine-tuning on a small accented dataset degrades baseline English WER significantly within a few epochs [`SEEN-IN-SNIPPET`: arXiv:2104.05055]. Freezing the base FastConformer encoder and tuning *only* linear adapter weights or LoRA preserves general domain robustness while aligning the acoustic space [`SEEN-IN-SNIPPET`: NVIDIA NeMo docs].

### Expected Gains & Data Requirements
- Fine-tuning FastConformer encoders on 10 to 50 hours of accented speech typically yields a **15% to 30% relative WER reduction** on target accents [`KNOWN-BUT-UNVERIFIED`].
- For named entities specifically, text-only adaptation is impossible for TDT without paired audio [`KNOWN-BUT-UNVERIFIED`]. Synthetic paired audio generated via multi-speaker TTS (reading entity-rich sentences) is a proven technique [`KNOWN-BUT-UNVERIFIED`].

### Publicly Available Philippine English / Filipino Speech Corpora
1. **UP-DSP-PLD (University of the Philippines Diliman DSP Lab Philippine Languages Database):** Hosted on Mozilla Data Collective, containing **454 hours** of audio across 10 Philippine languages (including Filipino and Philippine English) spanning conversational and read domains [`SEEN-IN-SNIPPET`: Mozilla Data Collective]. Open academic license [`SEEN-IN-SNIPPET`].
2. **Mozilla Common Voice (English - Philippines):** Audio clips filtered by `accent=philippines` in Common Voice validated releases [`SEEN-IN-SNIPPET`: Mozilla Common Voice]. License: CC0 [`KNOWN-BUT-UNVERIFIED`].
3. **Google FLEURS (`fil_ph`):** 12 hours of read speech per language, CC-BY 4.0 [`SEEN-IN-SNIPPET`: arXiv SEA-SpeechBench].
4. **Hugging Face Filipino Speech Dataset:** Tens of hours of native and diaspora Filipino speakers [`SEEN-IN-SNIPPET`: Hugging Face datasets].

### GPU Compute Budget on One A100 (80GB)
- Parakeet-TDT 0.6B has 600M parameters. With LoRA/adapters applied to the encoder, trainable parameters drop to < 10M [`KNOWN-BUT-UNVERIFIED`].
- On an NVIDIA A100 GPU (80GB) using mixed precision (`trainer.precision="16-mixed"` or `bf16`), throughput is approximately 25–40 audio-hours per clock hour [`KNOWN-BUT-UNVERIFIED`].
- Training a LoRA adapter on 10 hours of Philippine-accented/synthetic audio for 20 epochs requires approximately **1.5 to 3.0 GPU hours on an A100** [`KNOWN-BUT-UNVERIFIED`].

### Exporting Back to sherpa-onnx int8 & Quantization Loss
- Fine-tuned NeMo models export directly to ONNX via `asr_model.export("model.onnx")` [`SEEN-IN-SNIPPET`: NVIDIA docs / sherpa-onnx docs].
- Exported components (`encoder.onnx`, `decoder.onnx`, `joiner.onnx`) are dynamic-quantized using `onnxruntime.quantization.quantize_dynamic(..., QuantType.QInt8)` [`SEEN-IN-SNIPPET`: sherpa-onnx scripts].
- **Quantization Accuracy Loss:** FastConformer 0.6B INT8 dynamic quantization typically induces $< 0.3\%$ absolute WER degradation relative to FP32, while decreasing encoder size from ~2.4 GB to ~600 MB and speeding up CPU AVX-512/VNNI GEMM operations [`KNOWN-BUT-UNVERIFIED`].

**Verdict:** **DO IF TIME (Secondary Priority).** Synthetic TTS + Common Voice LoRA fine-tuning on the HPC A100 is feasible in 2–3 days of engineering, but downstream phonetic post-processing yields faster, risk-free returns.

---

## 4. Pronunciation Lexicon & G2P Approaches for Names

### Linguistic Analysis of Filipino Names
Filipino proper names present a specific phonological collision: Spanish orthography, American English schooling, and native Tagalog/Austronesian phonology:
- **"Ysabel":** Tagalog/Spanish pronunciation is $[i\cdot\text{sa}\cdot'\text{b}\varepsilon\text{l}]$. Standard English ASR hears the initial unstressed $[i]$ as $[j\text{u}]$ or $[\partial]$, outputting *"usable"* [`SEEN-IN-SNIPPET` from prompt bench].
- **"Jericho":** Pronounced with English affricate $[d\tilde{z}\varepsilon\text{r}\cdot\text{I}\cdot\text{ko}]$ or Tagalog tapped $r$ $[d\tilde{z}\varepsilon\cdot\text{r}\text{i}'\text{ko}]$.
- **"Dela Cruz":** Spoken as $[d\varepsilon\cdot\text{la}\;'\text{k}\text{r}\text{u}\text{s}]$. Parakeet transcribed this as *"to lack cruise"* ($[t\partial\;\text{l}\ae\text{k}\;\text{k}\text{r}\text{u}\text{z}]$) [`SEEN-IN-SNIPPET` from prompt bench]. Note the acoustic confusions: $[d] \to [t]$, $[\varepsilon] \to [\ae]$, and $[\text{s}] \to [\text{z}]$.
- **"Pantropiko":** $[p\alpha\text{n}\cdot\text{t}\text{r}\text{o}'\text{p}\text{i}\cdot\text{k}\text{o}] \to$ Parakeet wrote *"Pantropico"* (c vs k grapheme confusion).

### G2P and Phonetic Tool Evaluation
1. **Double Metaphone:**
   - **Weaknesses:** Double Metaphone was designed for English and assimilated European surnames under English spelling rules [`SEEN-IN-SNIPPET`: ResearchGate / Datablist]. It drops vowels (retaining only initial vowels), collapses consonants into broad buckets, and has no numeric distance metric (outputs discrete 4-character strings) [`SEEN-IN-SNIPPET`: ml6.eu / medium.com]. When Parakeet outputs *"usable to lack cruise"*, Double Metaphone cannot associate this with *"Ysabel Dela Cruz"*.
2. **CMUdict / g2p-en:**
   - Standard American English ARPAbet G2P. Fails on non-anglicized Filipino/Spanish names (e.g. interprets "Dela" as English or OOV) [`KNOWN-BUT-UNVERIFIED`].
3. **Phonemizer (espeak-ng backend):**
   - Supports `en-us`, `fil` (Filipino), and `es` (Spanish). Fast, multi-lingual, produces full IPA strings [`KNOWN-BUT-UNVERIFIED`].
4. **Epitran (`tgl-Latn` & `spa-Latn`):**
   - Epitran explicitly provides deterministic rule-based G2P for Tagalog Latin orthography (`tgl-Latn`) and Spanish (`spa-Latn`), generating precise IPA strings [`SEEN-IN-SNIPPET`: aclanthology.org / PyPI].

### Architecture: Phoneme-Level Alignment Gated by Acoustic Confidence
The team noted in `docs/STT-HOSTING.md` that a naive fuzzy matcher corrupted correct words when thresholds were relaxed (WER spiked to 35%). The solution is **confidence-gated phonetic replacement**:

```
ASR Output Words + Token Confidences (ys_log_probs)
                  │
                  ▼
   Word Confidence C < 0.85? ──────(No: High Confidence)──────► Keep Original ASR Text
                  │ (Yes: Uncertain)
                  ▼
 Convert span to IPA phonemes via Epitran/Phonemizer
                  │
                  ▼
 Compute Phonetic Levenshtein Distance against Target Entity Lexicon (IPA)
                  │
        Distance < Threshold? ─────(No)───────────────────────► Fallback to "Did you mean...?"
                  │ (Yes)
                  ▼
 Replace span with Canonical Named Entity
```

**Verdict:** **DO NOW.** Implement Epitran (`tgl-Latn`) + espeak-ng G2P on the contact/music entity list. Gate matching on low token confidence to protect common English vocabulary.

---

## 5. Accent Robustness: FastConformer & Philippine English

### Published Accent Performance
- In cross-accent benchmarks (L2-ARCTIC, AESRC, Common Voice accents), state-of-the-art unadapted English Conformer/FastConformer models show a significant "accent gap": WER on native General American English (3–5%) frequently doubles or triples on L2 Southeast Asian accents (10–18%) [`SEEN-IN-SNIPPET`: arXiv:2104.05055 / ResearchGate].

### Specific Philippine English Phonetic Shifts
Search data verifies key systematic shifts in Philippine English:
1. **Consonantal Substitutions:**
   - Voiceless labiodental fricative $/f/ \to$ bilabial plosive $[p]$ (e.g., *file* $\to$ *pile*) [`SEEN-IN-SNIPPET`: Dialects Archive / Scribd].
   - Voiced labiodental fricative $/v/ \to$ voiced bilabial plosive $[b]$ (e.g., *victory* $\to$ *bictory*) [`SEEN-IN-SNIPPET`: Dialects Archive / Scribd].
   - Dental fricatives $/θ, ð/ \to$ alveolar plosives $[t, d]$ (*think* $\to$ *tink*, *them* $\to$ *dem*) [`SEEN-IN-SNIPPET`: Dialects Archive / Scribd].
2. **Vowel Mergers & Monophthongization:**
   - Diphthongs $/o\mho/ \to [o]$ and $/e\text{I}/ \to [e]$ [`SEEN-IN-SNIPPET`: Dialects Archive].
   - Tense/lax mergers: $/i/ \approx /\text{I}/$ (sheep/ship), $/u/ \approx /\mho/$ (pool/pull) [`SEEN-IN-SNIPPET`].
3. **Prosodic Timing:**
   - Philippine English is predominantly **syllable-timed** rather than stress-timed [`SEEN-IN-SNIPPET`: Dialects Archive / Accent Archive].
   - **Critical TDT Vulnerability:** Parakeet-TDT predicts the *duration* (number of acoustic frames to skip) for each subword token [`SEEN-IN-SNIPPET`: mlr.press / Speechmatics]. Syllable-timed speech distributes vowel durations more evenly than stress-timed American English, causing TDT's duration head to under- or over-skip frames on unstressed syllables [`SPECULATIVE`].

### Inference-Time Tricks Evaluation
- **Test-Time Augmentation (TTA) / Speed Perturbation:** In speech TTA, audio is passed at $0.9\times, 1.0\times, 1.1\times$ speeds, aggregating output probabilities [`SEEN-IN-SNIPPET`: arXiv:2203.16075].
  - *Cost:* 3 full passes through Parakeet-TDT on CPU take $3 \times 390\text{ ms} \approx 1,170\text{ ms}$. This violates the latency constraint. **Verdict: SKIP.**
- **Volume Normalization / Pre-Emphasis:** Peak normalization to $-1.0\text{ dBFS}$ or EBU R128 loudness normalization takes $< 2\text{ ms}$ on CPU using `numpy`. FastConformer encoders normalize mel-spectrogram energy, but clipping or ultra-low volume speech degrades duration estimation. **Verdict: DO NOW.**

---

## 6. Model Alternatives: Leaderboard, CPU Viability, & Prompting

### Open ASR Leaderboard Status (as of late 2026)
- **Top English Accuracy:** Dominated by hybrid Speech-LLM models, notably **Canary-Qwen** and **IBM Granite Speech 3.3 (2B / 8B)** [`SEEN-IN-SNIPPET`: Hugging Face Open ASR Leaderboard / IBM / arXiv:2405.14999].
- **High-Throughput / Edge Leaders:** **Parakeet-TDT 0.6B / 1.1B** variants remain the fastest open models on the leaderboard with competitive WER [`SEEN-IN-SNIPPET`: Open ASR Leaderboard].
- **Whisper Variants:** **Whisper large-v3-turbo** (809M parameters, 4 decoder layers) [`SEEN-IN-SNIPPET`].

### CPU Feasibility Comparison on Laptop (i5-1145G7, 4 Threads)

| Model | Parameters | Architecture | Est. Latency (3s clip) | CPU Feasibility | Keyword Prompting? |
|---|---|---|---|---|---|
| **Parakeet-TDT 0.6B v2 int8** | 600M | FastConformer-TDT | **~390 ms** (measured) | **Optimal** | No (broken in sherpa) |
| **Whisper large-v3-turbo int8** | 809M | Enc-Dec Transformer | ~1,600–2,200 ms | Poor (too slow) | **Yes** (`initial_prompt`) |
| **Whisper base.en / small.en** | 74M / 244M | Enc-Dec Transformer | ~220 ms / ~580 ms | Feasible | **Yes** (`initial_prompt`) |
| **Canary-Qwen 2.5B** | ~2.5B | Conformer + LLM | > 5,000 ms | Infeasible on CPU | **Yes** (text prompt) |
| **Granite Speech 3.3 2B** | ~2B | Conformer + LLM | > 4,500 ms | Infeasible on CPU | **Yes** (text prompt) |
| **Moonshine base int8** | 61M | Enc-Dec Transformer | ~230 ms (measured) | Fast, high WER (19.6%) | No |

### Native Keyword/Context Prompting Analysis
- **Whisper `initial_prompt`:** Whisper accepts up to **224 tokens** of natural prompt text preceding decoding [`SEEN-IN-SNIPPET`: OpenAI docs / arXiv]. Priming Whisper with `"A conversation with Ysabel Dela Cruz listening to Pantropiko by BINI"` dramatically increases named-entity recall without retraining [`SEEN-IN-SNIPPET`: remskill.com / OpenAI docs].
- **Trade-off:** Whisper large-v3-turbo is 4x to 5x too slow on a laptop CPU. Whisper small.en fits CPU latency (~580 ms) but suffers on Philippine accents [`KNOWN-BUT-UNVERIFIED`].
- **Cloud Angle:** If cloud failover is used (e.g. Groq Whisper Large v3 at ~$0.00004/s or Deepgram Nova-2 with keyphrases), prompting yields instant 90%+ entity accuracy [`KNOWN-BUT-UNVERIFIED`]. Locally on CPU, Parakeet remains the speed winner.

**Verdict:** **SKIP Canary/Granite on CPU** (compute barrier). **KEEP Whisper large-v3-turbo as a Cloud-failover candidate only.**

---

## 7. Streaming / Low-Latency Variants from NVIDIA

### Cache-Aware Streaming FastConformer & Parakeet Realtime EOU
- **Cache-Aware Streaming Mechanism:** Cache-aware streaming models maintain convolution and self-attention state caches across chunks, avoiding the redundant compute of sliding windows [`SEEN-IN-SNIPPET`: arXiv:2305.16142 / Hugging Face].
- **Parakeet Realtime EOU:** Implements End-of-Utterance (`<EOU>`) prediction directly in the decoder to signal when the speaker has finished without waiting for trailing VAD silence [`SEEN-IN-SNIPPET`: NVIDIA docs / LocalAI].
- **The Name-Accuracy Penalty:** The team's empirical benchmark in `docs/STT-HOSTING.md` already tested this:
  - **Parakeet-TDT 0.6B (offline):** 8.3% Hard WER, **72% entities found**.
  - **NeMo FastConformer 480ms (streaming):** 26.7% Hard WER, **47% entities found**.
- **Root Cause:** Streaming models have restricted future acoustic context (look-ahead of only 160–480 ms) [`SEEN-IN-SNIPPET`: Hugging Face]. Disambiguating non-native phonemes and rare proper nouns requires the bidirectional encoder representations only present in offline models [`KNOWN-BUT-UNVERIFIED`].

**Verdict:** **SKIP streaming FastConformer for difficult entity queries.** Keep Parakeet offline. (A 390 ms wait is well within conversational turn-taking thresholds if masked by audio earcons or pipelined LLM prefill).

---

## 8. Text-Side Normalization (ITN, Casing, Codes)

### NeMo Inverse Text Normalization (ITN)
- **Architecture:** NVIDIA's `nemo_text_processing` uses Weighted Finite-State Transducers (WFST) via OpenFst and Google Pynini [`SEEN-IN-SNIPPET`: arXiv:2104.05055 / NVIDIA docs].
- **Production Implementation:** Grammar rules compile into finite-state archives (`.far` / `.fst`) executable deterministically via the C++ Sparrowhawk engine or Python bindings [`SEEN-IN-SNIPPET`: arXiv:2104.05055].
- **Coverage:** Deterministically handles cardinals, ordinals, time, dates, currency, and phone numbers without hallucinations [`SEEN-IN-SNIPPET`: NVIDIA docs].

### sherpa-onnx ITN Integration
- `sherpa-onnx` supports passing compiled WFST rule files (`--rule-fsts` or `--rule-fars`) for text normalization on embedded devices [`SEEN-IN-SNIPPET`: k2-fsa/sherpa-onnx docs].
- Alternatively, running a lightweight Python rule-based post-processor or `nemo_text_processing` directly on CPU takes $< 4\text{ ms}$ per sentence [`KNOWN-BUT-UNVERIFIED`].

### Spelled-Out Letters and Acronyms (e.g. "SB19")
- Transducers frequently segment acronyms as separated letters: `"s b nineteen"` or `"s. b. one nine"`.
- A simple deterministic regex pass over known entity acronyms (`re.sub(r'\b[sS]\.?\s*[bB]\.?\s*(19|nineteen)\b', 'SB19', text)`) completely resolves these in $< 0.1\text{ ms}$ [`KNOWN-BUT-UNVERIFIED`].

**Verdict:** **DO NOW.** Deploy deterministic regex + lightweight ITN.

---

## 9. Ranked Pareto Implementation List

| Technique | Expected Gain on Named Entities | Effort (Days) | CPU Latency Impact | Risk | Verdict |
|---|---|---|---|---|---|
| **1. Confidence-Gated Phonetic Matcher (Epitran + Levenshtein)** | **+15% to +25% Recall** (closing 72% $\to$ ~88%) [`SEEN-IN-SNIPPET` prototype + `KNOWN-BUT-UNVERIFIED`] | 1.0 day | $< 5\text{ ms}$ | Low (gated on $C < 0.85$ to prevent WER blowout) | **DO NOW** |
| **2. Post-ASR Regex & Lightweight ITN (Numbers, Acronyms)** | **+5% to +8% Recall** on brands/codes/times [`SEEN-IN-SNIPPET`] | 0.5 days | $< 2\text{ ms}$ | Near Zero | **DO NOW** |
| **3. Audio Volume Normalization (Peak / LUFS)** | **+1% to +3% Recall** on quiet audio [`KNOWN-BUT-UNVERIFIED`] | 0.2 days | $< 2\text{ ms}$ | Zero | **DO NOW** |
| **4. LoRA Adaptation on A100 (Synthetic TTS + Common Voice)** | **+8% to +15% Recall**, -15% relative WER [`KNOWN-BUT-UNVERIFIED`] | 2.5 days | 0 ms (identical ONNX graph) | Medium (export/quantization drift) | **DO IF TIME** |
| **5. Cloud Prompted Whisper Fallback (Low-Conf Trigger)** | **+20% Recall** on failed utterances [`SEEN-IN-SNIPPET`] | 1.0 day | ~300 ms network (only on trigger) | Low (requires WAN connection) | **DO IF TIME** |
| **6. sherpa-onnx Hotwords / Modified Beam Search** | 0% (incompatible with NeMo TDT) [`SEEN-IN-SNIPPET`] | 3.0+ days | +200–500 ms (if patched) | Critical (hallucination / crash) | **SKIP** |
| **7. Test-Time Augmentation (Speed Perturbation)** | +2% Recall [`KNOWN-BUT-UNVERIFIED`] | 1.0 day | **+780 ms** (3x passes) | High (breaks latency constraint) | **SKIP** |
| **8. Streaming FastConformer (480ms / EOU)** | **-25% Recall** (drop from 72% to 47%) [`SEEN-IN-SNIPPET` bench] | 0.5 days | -260 ms wait | Unacceptable entity loss | **SKIP** |
| **9. On-Device Speech-LLMs (Canary-Qwen, Granite)** | +10% Recall [`SEEN-IN-SNIPPET`] | 2.0 days | **+4,000 ms** on CPU | Compute prohibitive | **SKIP** |

---

## 10. Top 3 Rapid Experiments (< 2 Hours Each)

### Experiment 1: Extract `ys_log_probs` from `sherpa-onnx` and Calibrate Error Detection
- **Objective:** Verify that `stream.result.ys_log_probs` is accessible on Parakeet-TDT 0.6B v2 int8 in Python, compute word-level confidence, and evaluate the correlation between low word confidence and named entity errors on `bench/mic_hard`.
- **Time:** ~45 minutes.
- **Parameters / Implementation:**
```python
import numpy as np
import sherpa_onnx

# Inspect result fields
recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
    encoder="models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/encoder.int8.onnx",
    decoder="models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/decoder.onnx",
    joiner="models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/joiner.onnx",
    tokens="models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/tokens.txt",
    num_threads=4,
)
stream = recognizer.create_stream()
stream.accept_waveform(sample_rate, samples)
recognizer.decode_stream(stream)
res = stream.result

# Token probabilities
tokens = res.tokens
log_probs = res.ys_log_probs  # Available in OfflineRecognizerResult

# Group tokens into words using whitespace markers (' ')
# Calculate confidence per word: C_word = exp(mean(log_probs_in_word))
# Flag words with C_word < 0.82 for downstream phonetic correction
```

### Experiment 2: Benchmark Epitran (`tgl-Latn`) G2P vs. Raw Character Matching on Contacts
- **Objective:** Convert the 15 contact names in `bench/stt_hard.json` (e.g. "Ysabel Dela Cruz", "Jericho", "Joshua Santos") into IPA using `epitran.Epitran('tgl-Latn')` and measure if phonetic Levenshtein distance cleanly distinguishes them from Parakeet's misrecognitions ("usable to lack cruise").
- **Time:** ~60 minutes.
- **Parameters / Implementation:**
```python
import epitran
import Levenshtein

epi_tagalog = epitran.Epitran("tgl-Latn")
target_name = "Ysabel Dela Cruz"
target_ipa = epi_tagalog.transliterate("Isabel Dela Krus")  # [i.sa.'bɛl dɛ.la 'kɾus]

# Simulate ASR hypothesis phonetic conversion
asr_hyp = "usable to lack cruise"
# Convert English ASR output to IPA (via phonemizer or epitran)
# Evaluate normalized phonetic distance:
# dist = Levenshtein.distance(hyp_ipa, target_ipa) / max(len(hyp_ipa), len(target_ipa))
# Verify separation margin against non-matching baseline words.
```

### Experiment 3: Measure Audio Normalization Impact on Parakeet Hard Set
- **Objective:** Test whether peak normalization to $-1.0\text{ dBFS}$ before passing audio into `sherpa-onnx` eliminates transcription dropouts or duration misalignments on `bench/stt_hard.json`.
- **Time:** ~30 minutes.
- **Parameters / Implementation:**
```python
import numpy as np


def normalize_audio(samples: np.ndarray) -> np.ndarray:
  max_peak = np.max(np.abs(samples))
  if max_peak > 0:
    return samples * (0.90 / max_peak)  # Scale to -1 dBFS
  return samples


# Run scripts/stt_compare.py on bench/mic_hard with and without normalization
# Measure delta in Hard WER and entity recall (target: 0 latency penalty, verify no degradation).
```
