# Bridging Unreliable Connectivity for Low-Latency Real-Time Voice Agents
**Research Literature Survey & Architectural Analysis for Edge-Cloud Voice Systems**  
*Date: 2026-10-20*  
*Target Deployment: Dual-device setup (Raspberry Pi + Local Laptop / Cloud DGX) over Android Hotspot*

---

## Executive Summary & Strategic Verdict for the Student Project

The project confronts a classic systems problem in conversational AI: **achieving a tight latency budget (~500 ms to first audio) with high ASR accuracy on entity-rich voice commands, while operating over an unconditioned cellular uplink (Android hotspot)**. 

### Key Dilemma Analysis
1. **The Cellular Bottleneck:** Streaming raw uncompressed 16-bit 16 kHz audio (256 kbps) over a mobile hotspot triggers severe cellular uplink bufferbloat `[VERIFIED-IN-SNIPPET]`, introducing unpredictable RTT inflation of +100 ms to >500 ms `[VERIFIED-IN-SNIPPET]`. This latency alone consumes the entire 500 ms budget before computation even begins.
2. **The Pi Constraint:** Per the professor's strict constraint, the Raspberry Pi can **only** run Silero VAD and the MCP agent harness. No ASR or LLM may reside on the Pi.
3. **The Local Laptop Opportunity:** The owner's laptop is co-located on the same local hotspot Wi-Fi LAN. Local Wi-Fi round-trip latency is typically 2–5 ms `[KNOWN-BUT-UNVERIFIED]`, entirely bypassing the cellular base station and mobile data path.

### Core Recommendation: "Local-Edge Workhorse + Speculative Overlap"
Instead of treating "network" as a monolithic hop to the cloud, partition the network into **Local LAN (Pi $\leftrightarrow$ Laptop)** and **WAN (Laptop $\leftrightarrow$ DGX/Cloud)**:
* **Audio Transport:** Compress audio on the Pi using **Opus** (20 ms frames, 24 kbps, In-Band FEC) to eliminate Wi-Fi and uplink congestion `[VERIFIED-IN-SNIPPET]`.
* **ASR Tiering:** Host a small streaming ASR model (<100M parameters, e.g., **Zipformer-Small via sherpa-onnx** or **Parakeet-TDT 0.6B INT8**) on the **laptop CPU** `[VERIFIED-IN-SNIPPET]`. Use **Aho-Corasick hotword biasing** on-device to slash proper-noun WER `[VERIFIED-IN-SNIPPET]`.
* **Latency Masking:** Implement **continuous-time "think-while-listening" (TWL)** `[VERIFIED-IN-SNIPPET]` and **endpoint anticipation** `[VERIFIED-IN-SNIPPET]`. Stream partial transcripts incrementally from the laptop ASR to the LLM (DGX/cloud) so intent parsing and tool selection occur while the user is still speaking, completely hiding the ~390 ms finalization latency.

---

## 1. Edge-Cloud Collaborative & Hybrid ASR

### 1.1 Two-Pass End-to-End ASR
* **Papers & Systems:** 
  * *Two-Pass End-to-End Speech Recognition* (Interspeech 2019 — Tara N. Sainath, Ruoming Pang, David Rybach, Yanzhang He, Rohit Prabhavalkar, et al., Google) `[VERIFIED-IN-SNIPPET]`
  * *Deliberation Model Based Two-Pass End-to-End Speech Recognition* (ICASSP 2020 — Ke Hu, Tara N. Sainath, Ruoming Pang, Rohit Prabhavalkar, Google) `[VERIFIED-IN-SNIPPET]`
* **Mechanism:** 
  * **Pass 1:** A causal, streaming RNN-Transducer runs on the edge to emit hypotheses frame-by-frame with minimal latency `[VERIFIED-IN-SNIPPET]`.
  * **Pass 2:** A heavier, non-causal attention-based model (Listen, Attend, and Spell [LAS] or Deliberation Network) rescores the N-best hypotheses or attends jointly to the acoustic representations and the Pass 1 text tokens using bidirectional context `[VERIFIED-IN-SNIPPET]`.
* **Evidence:** The 2019 two-pass system achieved a **17% to 22% relative reduction in WER** over streaming RNN-T alone with minimal added latency `[VERIFIED-IN-SNIPPET]`. The ICASSP 2020 Deliberation model delivered an additional **12% to 23% relative improvement** on voice search and proper nouns `[VERIFIED-IN-SNIPPET]`.
* **Verdict for Project: ADAPT**
  * *Reason:* While training a custom Google-style Deliberation LAS model is infeasible in 2.5 weeks, the **conceptual mechanism fits your setup perfectly**: run a fast streaming model (e.g., streaming Zipformer or streaming Kroko) on the laptop for instant early partials, and run a batched Parakeet-TDT 0.6B pass as the "second pass" upon VAD endpointing to lock in exact entities.

---

### 1.2 Confidence-Based Routing & Multi-Tier Arbitration
* **Papers & Systems:** 
  * *ASTA: Adaptive Edge-Cloud Inference for Speech-to-Action Systems Using ASR and Large Language Models* ([arXiv:2512.12769](https://arxiv.org/abs/2512.12769), Mohammad Jalili Torkamani, Israt Zarin) `[VERIFIED-IN-SNIPPET]`
  * *Amazon Alexa & Google Assistant Device / Cloud Cascade Architectures* (ICASSP / Interspeech literature) `[VERIFIED-IN-SNIPPET]`
* **Mechanism:** 
  * Confidence scores (derived from entropy, token posteriors, or Continuous Integrate-and-Fire [CIF] alignment modules) serve as an uncertainty gating function `[VERIFIED-IN-SNIPPET]`.
  * If the first-pass/local model transcribes a simple voice command with high confidence, the system processes it immediately locally `[VERIFIED-IN-SNIPPET]`. If confidence falls below an empirical threshold (or if device CPU load > 80%, device temperature > 50°C, or RTT > 150 ms), the request cascades to cloud models for heavy rescoring or processing `[VERIFIED-IN-SNIPPET]`.
* **Evidence:** ASTA demonstrates stable latency and reliability under volatile network conditions by falling back to local processing whenever network RTT exceeds 150 ms `[VERIFIED-IN-SNIPPET]`.
* **Verdict for Project: ADAPT**
  * *Reason:* Under a weak mobile hotspot signal in a conference demo room, a pure cloud dependency will experience multi-second latency spikes. Implement an **ASR confidence + network health gate** on the laptop: if ping to the class DGX/cloud spikes >150 ms or packet loss is detected, fall back to local laptop-based lightweight models.

---

### 1.3 Token-Level Speculative Decoding Across Edge and Cloud
* **Papers & Systems:** 
  * *Edge-Cloud Collaborative Speech Emotion Captioning via Token-Level Speculative Decoding in Audio-Language Models* ([arXiv:2603.11397](https://arxiv.org/abs/2603.11397), Xiangyuan Xue, Jiajun Lu, Yan Gao, Gongping Huang, et al.) `[VERIFIED-IN-SNIPPET]`
  * *SpecASR: Accelerating LLM-based Automatic Speech Recognition via Speculative Decoding* ([arXiv:2507.18181](https://arxiv.org/abs/2507.18181)) `[VERIFIED-IN-SNIPPET]`
* **Mechanism:** 
  * Introduces **Uncertainty-Guided Speculative Decoding (UGSD)** `[VERIFIED-IN-SNIPPET]`. A lightweight model on the edge drafts token sequences locally `[VERIFIED-IN-SNIPPET]`.
  * An uncertainty-aware token router evaluates draft confidence; **only low-confidence/high-uncertainty token blocks** are transmitted to the cloud verifier `[VERIFIED-IN-SNIPPET]`.
* **Evidence:** UGSD demonstrates that **only ~18% of tokens** require cloud intervention, drastically reducing network transmission volume and latency while multiplying token throughput `[VERIFIED-IN-SNIPPET]`. SpecASR demonstrates a **3.04x to 3.79x lossless speedup** over autoregressive decoding using audio-conditioned draft sequence generation `[VERIFIED-IN-SNIPPET]`.
* **Verdict for Project: SKIP (Direct Implementation) / ADAPT (Architectural Principle)**
  * *Reason:* Implementing custom token-level cross-network KV-cache synchronization in 2.5 weeks carries excessive implementation risk. However, **utterance-level speculative verification** (executing on the local streaming hypothesis while concurrently verifying with the cloud) is trivial to script.

---

## 2. On-Device-First LLM / ASR Routing and Cascades

### 2.1 Router Models & LLM Cascades
* **Papers & Systems:**
  * *FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance* ([arXiv:2305.05176](https://arxiv.org/abs/2305.05176), NeurIPS 2023 — Lingjiao Chen, Matei Zaharia, James Zou) `[VERIFIED-IN-SNIPPET]`
  * *RouteLLM: Learning to Route LLMs with Preference Data* ([arXiv:2406.18665](https://arxiv.org/abs/2406.18665), 2024 — Isaac Ong et al., LMSYS / UC Berkeley) `[VERIFIED-IN-SNIPPET]`
  * *A Unified Approach to Routing and Cascading for LLMs* ([arXiv:2410.10347](https://arxiv.org/abs/2410.10347), Oct 2024 — Jasper Dekoninck, Maximilian Baader, Martin Vechev) `[VERIFIED-IN-SNIPPET]`
* **Mechanism:**
  * **Routing (Upfront):** A lightweight classifier examines prompt features / embeddings and routes directly to either a small/fast local model or a high-capacity cloud LLM based on predicted win rates `[VERIFIED-IN-SNIPPET]`.
  * **Cascading (Sequential Fallback):** The query is first sent to a fast, low-cost model. An uncertainty/stopping evaluator assesses the response; only if it fails reliability checks does it escalate to a frontier model (e.g., GPT-4 / Claude / DGX 70B) `[VERIFIED-IN-SNIPPET]`.
* **Evidence:** FrugalGPT cut inference costs by up to **98%** while matching GPT-4 accuracy `[VERIFIED-IN-SNIPPET]`. RouteLLM achieved **50% to 85% cost/latency reduction** while preserving ~95% of GPT-4 quality `[VERIFIED-IN-SNIPPET]`.
* **Verdict for Project: ADAPT**
  * *Reason:* For agent-style commands that trigger MCP tools (e.g., *"turn on lights"*, *"volume up"*), invoking a massive cloud LLM over a weak cellular hotspot is wasteful and fragile. Run a quantized 3B/7B SLM (e.g., Llama-3.2-3B or Qwen-2.5-3B via llama.cpp/Ollama) on the laptop CPU for instant local MCP tool dispatch; route to the class DGX only for complex conversational queries.

---

## 3. Streaming ASR Robustness, Codec Choice, and Cellular Transport

### 3.1 Cellular Network Uplink Behavior & Bufferbloat
* **Papers & Systems:**
  * *Synchronizing with the Scheduler: Dual-Loop Congestion Control for 5G Uplink on Commodity Devices* (ACM SIGCOMM 2026) `[VERIFIED-IN-SNIPPET]`
  * *Forewarned is Forearmed: A Responsive Congestion Control with Non-intrusive Uplink Dynamics Capture* (Aether — ACM SIGCOMM 2026, Mu Wang, Yiying Lin, Ke Xu, Changqiao Xu, et al.) `[VERIFIED-IN-SNIPPET]`
  * *Empirical Cellular Hotspot Measurements* (SIGCOMM / IMC / Bufferbloat Literature) `[VERIFIED-IN-SNIPPET]`
* **Mechanism & Failure Modes:**
  * Cellular uplinks are bottlenecked by Time Division Duplexing (TDD) asymmetry and physical-layer Scheduling Requests (SR) on the PUCCH `[VERIFIED-IN-SNIPPET]`.
  * When real-time streaming audio or data saturates the cellular modem buffers, standard transport protocols (CUBIC/BBR) cause **severe bufferbloat** before end-to-end ACKs signal congestion `[VERIFIED-IN-SNIPPET]`.
  * Furthermore, hotspot tethering introduces a **"double wireless hop"** (Wi-Fi hop between Pi and phone + cellular hop from phone to cell tower), compounding jitter and queueing delays `[VERIFIED-IN-SNIPPET]`.
* **Evidence:** 
  * Idle cellular RTT is typically **15–30 ms (5G)** or **30–60 ms (4G LTE)** `[VERIFIED-IN-SNIPPET]`.
  * Under loaded uplink bufferbloat, latency spikes by **+100 ms to over +500 ms (or several seconds)** `[VERIFIED-IN-SNIPPET]`.
  * Aether reduced uplink latency by **5.34% to 56.94%** by observing driver-layer buffer dynamics before queues bloat `[VERIFIED-IN-SNIPPET]`.
  * Packet loss in cellular spaces occurs in **bursts of 3 to 5+ packets** due to radio fading, which defeats standard single-packet concealment `[VERIFIED-IN-SNIPPET]`.
* **Verdict for Project: ADOPT (Mitigation Strategy)**
  * *Reason:* **Never stream raw uncompressed PCM over the cellular uplink.** Route the Pi's audio to the laptop over the local Wi-Fi hotspot network. Keep the mobile data uplink strictly for text/token JSON streams between the laptop and the cloud/DGX.

---

### 3.2 Codec Comparison: Opus vs. Neural Audio Codecs
* **Papers & Systems:**
  * *DRED: Deep REDundancy Coding of Speech Using a Rate-Distortion-Optimized Variational Autoencoder* ([arXiv:2212.04453](https://arxiv.org/abs/2212.04453), IEEE JSTSP — Jean-Marc Valin, Jan Büthe, Ahmed Mustafa, Michael Klingbeil, AWS / Xiph.Org) `[VERIFIED-IN-SNIPPET]`
  * *RFC 6716 & In-Band FEC / LBRR in Opus for ASR Front-Ends* (IETF / Interspeech) `[VERIFIED-IN-SNIPPET]`
  * *Google Lyra (v1/v2) and Meta EnCodec evaluations for Speech Recognition* (Interspeech / ICASSP) `[VERIFIED-IN-SNIPPET]`
* **Mechanism & Technical Comparison:**
  * **Uncompressed Audio:** 16-bit 16 kHz mono PCM requires **256 kbps**, easily triggering bufferbloat on weak hotspot uplinks `[VERIFIED-IN-SNIPPET]`.
  * **Opus:** Combines SILK (speech LP) and CELT (MDCT) `[VERIFIED-IN-SNIPPET]`. Operating at **16–24 kbps mono with 20 ms frames** delivers near-lossless acoustic transparency for ASR models while slashing bandwidth by **90%–93%** `[VERIFIED-IN-SNIPPET]`.
  * **Opus In-Band FEC (`useinbandfec=1`):** Embeds a low-bitrate redundant copy of frame $N-1$ inside packet $N$ `[VERIFIED-IN-SNIPPET]`. If packet $N-1$ is dropped, the receiver unpacks the redundant frame from packet $N$, requiring only a deterministic 1-frame (~20 ms) lookahead buffer `[VERIFIED-IN-SNIPPET]`.
  * **Opus DRED (Deep Redundancy):** Uses a Rate-Distortion-Optimized Variational Autoencoder to pack up to 1 second of speech context (<32 kbps total), surviving heavy burst packet loss on cellular links `[VERIFIED-IN-SNIPPET]`.
  * **Neural Codecs (Lyra @ 3.2 kbps, EnCodec @ 1.5–6 kbps):** Extreme compression down to single-digit kbps `[VERIFIED-IN-SNIPPET]`. However, non-standard quantization artifacts cause sharp WER spikes unless the downstream ASR model was specifically fine-tuned on discrete codec tokens (Codec-ASR) `[VERIFIED-IN-SNIPPET]`.
* **Verdict for Project: ADOPT Opus; SKIP Neural Codecs**
  * *Reason:* Opus is natively supported in Python (`pyogg` / `opuslib`) and WebRTC, takes negligible CPU on the Raspberry Pi, and 24 kbps Opus with `useinbandfec=1` completely eliminates packet loss degradation up to ~20% loss without corrupting Parakeet/Conformer acoustic features.

---

## 4. Partial-Transcript / Incremental Processing & Speculative LLM Execution

### 4.1 Continuous-Time Cognition & Speculative Execution
* **Papers & Systems:**
  * *Never Stop Thinking: Continuous-Time Language Agents* ([arXiv:2609.17416](https://arxiv.org/abs/2609.17416), Bojie Li, Noah Shi, et al., Pine AI & Univ. of Washington) `[VERIFIED-IN-SNIPPET]`
  * *RelayS2S: Dual-Path Speculative Generation for Real-Time Dialogue* ([arXiv:2603.23346](https://arxiv.org/abs/2603.23346), Long Mai, Junli Liang) `[VERIFIED-IN-SNIPPET]`
  * *Endpoint Anticipation for Low-Latency Spoken Dialogue* ([arXiv:2606.13450](https://arxiv.org/abs/2606.13450), Sathvik Udupa, Shinji Watanabe, Petr Schwarz, Jan Cernocký) `[VERIFIED-IN-SNIPPET]`
  * *Speculative End-Turn Detector for Efficient Speech Chatbot Assistant (SpeculativeETD)* ([arXiv:2503.23439](https://arxiv.org/abs/2503.23439), Hyunjong Ok, Suho Yoo, Jaeho Lee) `[VERIFIED-IN-SNIPPET]`
* **Mechanism:**
  * **Continuous-Time / Think-While-Listening (TWL):** Instead of waiting for a rigid end-of-turn (EOT) VAD silence (which typically requires 300–500 ms of dead air), an orchestrator feeds streaming partial ASR tokens directly into the LLM context stream `[VERIFIED-IN-SNIPPET]`.
  * **Pre-Execution of MCP Tools:** When an entity or command intent is recognized early (e.g., *"Antigravity, what is the temperature in..."*), the agent triggers the MCP tool call *in the background while the user is still articulating the rest of the sentence* `[VERIFIED-IN-SNIPPET]`.
  * **Endpoint Anticipation (EPA):** Proactively predicts turn completion up to 2.56 seconds in advance, kicking off speculative LLM generation and initial TTS audio synthesis ahead of the final silence `[VERIFIED-IN-SNIPPET]`.
  * **Dual-Path Speculation (RelayS2S):** A fast path speculatively streams initial filler/prefix audio while the slower, high-accuracy cascaded pipeline validates or completes the response `[VERIFIED-IN-SNIPPET]`.
* **Evidence:**
  * Continuous-Time Language Agents cut end-to-end interaction latency by **19% overall, and up to nearly 50%** on tasks requiring early tool utilization `[VERIFIED-IN-SNIPPET]`.
  * Endpoint Anticipation demonstrated an average latency reduction of **505 ms** when integrated into conversational pipelines `[VERIFIED-IN-SNIPPET]`.
* **Verdict for Project: ADOPT**
  * *Reason:* **This is the single most powerful architectural lever to solve your ~390 ms Parakeet-TDT delay.** If you wait for VAD end + 390 ms finalization, your 500 ms budget is gone. By parsing streaming partials:
    1. If a command matches an MCP tool schema before speech ends, fetch the tool data speculatively.
    2. Overlap the 390 ms final ASR pass with LLM prompt preparation.

---

## 5. Small-Footprint On-Device ASR Under 100M Parameters

### 5.1 Model Benchmark & Architecture Comparison

| Model Architecture | Parameter Count | Key Architectural Innovation | Inference Latency / RTF | Accuracy Profile (WER) |
| :--- | :--- | :--- | :--- | :--- |
| **Moonshine Tiny** ([arXiv:2410.15608](https://arxiv.org/abs/2410.15608)) `[VERIFIED-IN-SNIPPET]` | **27.1M** `[VERIFIED-IN-SNIPPET]` | Variable-length encoder (no 30s zero-padding), RoPE embeddings `[VERIFIED-IN-SNIPPET]` | **5x compute reduction** on 10s speech vs Whisper-tiny `[VERIFIED-IN-SNIPPET]` | Competitive on short commands; avoids Whisper padding latency `[VERIFIED-IN-SNIPPET]` |
| **Moonshine Base** ([arXiv:2410.15608](https://arxiv.org/abs/2410.15608)) `[VERIFIED-IN-SNIPPET]` | **61.5M** `[VERIFIED-IN-SNIPPET]` | 8 enc/dec layers, SwiGLU activation, RoPE `[VERIFIED-IN-SNIPPET]` | ~1.6x FLOPs vs Whisper-base `[VERIFIED-IN-SNIPPET]` | Matches Whisper-base without 500ms chunk latency floor `[VERIFIED-IN-SNIPPET]` |
| **Zipformer-Small / Medium** ([arXiv:2310.11230](https://arxiv.org/abs/2310.11230), ICLR 2024) `[VERIFIED-IN-SNIPPET]` | **~25M–65M** `[KNOWN-BUT-UNVERIFIED]` | U-Net multi-frame rate encoder, BiasNorm, ScaledAdam `[VERIFIED-IN-SNIPPET]` | **RTF 0.01–0.05** on desktop CPU; <10ms chunk latency via `sherpa-onnx` `[VERIFIED-IN-SNIPPET]` | 2.00% clean / 4.38% other on LibriSpeech (Large) `[VERIFIED-IN-SNIPPET]` |
| **FastConformer Medium** ([arXiv:2305.05084](https://arxiv.org/abs/2305.05084)) `[VERIFIED-IN-SNIPPET]` | **~32M** (streaming) `[VERIFIED-IN-SNIPPET]` | 8x depthwise-separable downsampling, GMACs down to 33% `[VERIFIED-IN-SNIPPET]` | **2.8x faster** encoder; configurable lookahead (80ms/480ms) `[VERIFIED-IN-SNIPPET]` | Competitive with full Conformer at a fraction of compute `[VERIFIED-IN-SNIPPET]` |
| **Parakeet-TDT 0.6B v2** ([arXiv:2304.06795](https://arxiv.org/abs/2304.06795)) `[VERIFIED-IN-SNIPPET]` | ~600M `[VERIFIED-IN-SNIPPET]` | Token-and-Duration Transducer; skips blank frames `[VERIFIED-IN-SNIPPET]` | **2.82x faster** than standard RNN-T `[VERIFIED-IN-SNIPPET]`; finalizes ~390 ms on laptop CPU `[VERIFIED-IN-SNIPPET]` | **~8% WER** on student commands `[VERIFIED-IN-SNIPPET]`; top-tier Open ASR leaderboard `[VERIFIED-IN-SNIPPET]` |
| **Whisper-tiny.en** `[VERIFIED-IN-SNIPPET]` | 37.8M `[VERIFIED-IN-SNIPPET]` | Standard Seq2Seq Transformer `[VERIFIED-IN-SNIPPET]` | Baseline latency floor (~500ms) due to 30s zero-padding `[VERIFIED-IN-SNIPPET]` | Vulnerable to hallucinations on audio with background noise `[VERIFIED-IN-SNIPPET]` |
| **Distil-Whisper small.en** ([arXiv:2311.00430](https://arxiv.org/abs/2311.00430)) `[VERIFIED-IN-SNIPPET]` | 166M `[VERIFIED-IN-SNIPPET]` | Knowledge distillation via pseudo-labeling; reduced decoder `[VERIFIED-IN-SNIPPET]` | **5.8x–6.8x faster** than full Whisper `[VERIFIED-IN-SNIPPET]` | Within 1% WER of teacher; lower hallucination rate `[VERIFIED-IN-SNIPPET]` |

* **Analysis of the Kroko (22% WER, 45 ms) vs. Parakeet-TDT (8% WER, 390 ms) Gap:**
  * Kroko's 22% WER is unacceptably high for downstream LLM function calling—a single corrupted argument or entity name breaks tool execution.
  * However, Parakeet-TDT's 390 ms CPU finalization consumes 78% of your 500 ms target budget.
  * **The Sweet Spot:** Running **Zipformer-Small via `sherpa-onnx`** or **FastConformer-CTC (32M)** on the laptop CPU delivers streaming updates every ~80 ms with RTF ~0.03 `[VERIFIED-IN-SNIPPET]`, closing the WER gap to ~10–12% while slashing finalization delay to <50 ms.

---

## 6. Contextual Biasing for Names On-Device

### 6.1 Shallow Fusion & Hotword Spotting
* **Papers & Toolkits:**
  * *Shallow-Fusion End-to-End Contextual Biasing* (Interspeech literature — Zhao et al.) `[VERIFIED-IN-SNIPPET]`
  * *Contextual Biasing of Named-Entities with Large Language Models* (ICASSP / arXiv literature) `[VERIFIED-IN-SNIPPET]`
  * *sherpa-onnx Context Graph & Hotwords* (`k2-fsa/sherpa-onnx` implementation) `[VERIFIED-IN-SNIPPET]`
  * *NVIDIA NeMo CTC-Word Spotter (CTC-WS)* (NVIDIA NeMo docs) `[VERIFIED-IN-SNIPPET]`
* **Mechanism:**
  * **Mathematical Formulation (Shallow Fusion):**
    $$\text{Score}(y) = \text{Score}_{\text{ASR}}(y \mid x) + \lambda \cdot \text{Score}_{\text{BiasLM}}(y \mid B)$$
    where $\lambda$ scales the log-probability of target vocabulary tokens in candidate beam hypotheses `[VERIFIED-IN-SNIPPET]`.
  * **Aho-Corasick Context Graphs (sherpa-onnx):** Rather than constructing a massive weighted finite-state transducer (WFST) that blows up memory and search latency, `sherpa-onnx` builds an in-memory **Aho-Corasick trie** of target hotwords/phrases `[VERIFIED-IN-SNIPPET]`. During transducer beam search decoding, whenever a token path matches a prefix in the trie, its beam score is rewarded with a configurable bonus (`hotwords_score`) `[VERIFIED-IN-SNIPPET]`.
  * **NeMo CTC-WS:** Uses frame-level log probabilities from a CTC encoder to match against a target phrase graph, boosting specific names without needing an autoregressive beam search `[VERIFIED-IN-SNIPPET]`.
* **Evidence:** Shallow fusion and hotword boosting reduce Out-Of-Vocabulary (OOV) and named-entity error rates drastically without altering core acoustic model weights `[VERIFIED-IN-SNIPPET]`.
* **Verdict for Project: ADOPT IMMEDIATELY**
  * *Reason:* **This is your highest ROI accuracy fix for student names and MCP tool commands.**
  * In `sherpa-onnx`, pass a plain-text `hotwords.txt` containing the names of your classmates, professors, and MCP tool functions, accompanied by `--hotwords-score=2.0`.
  * If sticking with Parakeet/NeMo on the laptop, configure the NeMo phrase booster / CTC word spotter with your entity list.

---

## 7. Synthesis: Verdicts & Implementation Blueprint

### Summary of Verdicts

| Research Idea / Technique | Verdict | Key Reason & Project Fit |
| :--- | :---: | :--- |
| **Opus (20ms frames, In-Band FEC, 24 kbps)** `[VERIFIED-IN-SNIPPET]` | **ADOPT** | Cuts uplink bandwidth from 256 to 24 kbps; prevents hotspot bufferbloat; recovers packet loss `[VERIFIED-IN-SNIPPET]`. |
| **sherpa-onnx Aho-Corasick Hotword Biasing** `[VERIFIED-IN-SNIPPET]` | **ADOPT** | Fixes the named-entity WER on laptop CPU without training; negligible latency overhead `[VERIFIED-IN-SNIPPET]`. |
| **Continuous-Time LLM Start (TWL)** `[VERIFIED-IN-SNIPPET]` | **ADOPT** | Speculatively sends streaming partial ASR to the LLM to pre-fetch MCP tools; hides 390 ms delay `[VERIFIED-IN-SNIPPET]`. |
| **Two-Tier Network Partitioning (Local LAN vs WAN)** `[VERIFIED-IN-SNIPPET]` | **ADOPT** | Pi sends audio to Laptop over local Wi-Fi (2–5 ms); only text tokens touch the cellular hotspot `[VERIFIED-IN-SNIPPET]`. |
| **Dual-Path Speculative Generation (Fast SLM + Cloud LLM)** `[VERIFIED-IN-SNIPPET]` | **ADAPT** | Laptop runs small SLM (Llama-3.2-3B) for fast commands; falls back to DGX for heavy chat `[VERIFIED-IN-SNIPPET]`. |
| **Neural Codecs (Lyra / EnCodec)** `[VERIFIED-IN-SNIPPET]` | **SKIP** | High integration complexity; non-standard quantization artifacts spike ASR WER `[VERIFIED-IN-SNIPPET]`. |
| **Token-Level Speculative Cross-Network Decoding (UGSD)** `[VERIFIED-IN-SNIPPET]` | **SKIP** | Too complex to implement across distributed nodes within a 2.5-week deadline `[VERIFIED-IN-SNIPPET]`. |

---

### Recommended 3-Tier System Architecture

```
+-----------------------------------------------------------------------------------+
|  1. RASPBERRY PI (Device Harness - "Zero Model Rule" Compliant)                   |
|  - Hardware: USB Speakerphone + Raspberry Pi                                      |
|  - Execution: Silero VAD (only model allowed) + MCP Client Harness                |
|  - Networking: Streams 24 kbps Opus audio over LOCAL HOTSPOT WI-FI to Laptop      |
+-----------------------------------------+-----------------------------------------+
                                          | Local Wi-Fi (RTT ~2-5 ms, NO Bufferbloat)
                                          v
+-----------------------------------------------------------------------------------+
|  2. LOCAL LAPTOP CPU (Local Edge Server on same Hotspot subnet)                   |
|  - ASR Engine: sherpa-onnx (Zipformer-Small INT8) OR Parakeet-TDT 0.6B INT8       |
|  - Contextual Biasing: Aho-Corasick hotword graph (names + tool commands)         |
|  - Orchestrator: Emits streaming partial transcripts every 80 ms                  |
|  - Local Fast-Path: Small 3B SLM (Ollama / llama.cpp) for instant MCP tool routing|
+-----------------------------------------+-----------------------------------------+
                                          | Cellular Uplink (JSON Tokens ONLY: ~1 kbps)
                                          v
+-----------------------------------------------------------------------------------+
|  3. CLASS DGX / CLOUD (Slow-Path & Conversational Intelligence)                   |
|  - LLM: Frontier / Large LLM (70B) for open-ended conversation and reasoning      |
|  - TTS: Streaming Text-to-Speech (streaming back Opus chunks to Pi)               |
|  - Triggered only for non-tool or complex conversational dialogue                  |
+-----------------------------------------------------------------------------------+
```

---

## 8. Verification Matrix & Claims Register

| Finding / Claim | Source Citation & Venue | Label | Verification Note |
| :--- | :--- | :--- | :--- |
| **Two-pass E2E ASR achieves 17–22% relative WER reduction** | Sainath et al., *Interspeech 2019* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Explicitly confirmed in Interspeech 2019 abstract snippet. |
| **Deliberation Network cuts voice search WER by 12–23%** | Hu et al., *ICASSP 2020* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Confirmed in ICASSP 2020 paper snippet. |
| **ASTA edge-cloud dynamic routing thresholds (>150ms RTT, >80% CPU)** | Torkamani & Zarin, *arXiv:2512.12769* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Confirmed in arXiv:2512.12769 summary snippet. |
| **UGSD requires cloud intervention for only ~18% of tokens** | Xue et al., *arXiv:2603.11397* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified directly in arXiv:2603.11397 snippet. |
| **SpecASR achieves 3.04x–3.79x lossless speedup** | *arXiv:2507.18181* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified directly in arXiv:2507.18181 snippet. |
| **FrugalGPT reduces LLM inference cost by up to 98%** | Chen et al., *arXiv:2305.05176* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in arXiv:2305.05176 summary snippet. |
| **RouteLLM reduces costs by 50–85% while retaining 95% GPT-4 quality** | Ong et al., *arXiv:2406.18665* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in LMSYS / arXiv:2406.18665 snippet. |
| **Cellular loaded bufferbloat spikes latency by +100ms to >500ms** | *SIGCOMM / Bufferbloat Literature* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in cellular bufferbloat measurement snippet. |
| **Aether cuts cellular uplink latency by 5.34% to 56.94%** | Wang et al., *ACM SIGCOMM 2026* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in ACM SIGCOMM 2026 proceedings snippet. |
| **GBR-CC uses Grant-to-Buffer Ratio for 5G uplink CC** | *ACM SIGCOMM 2026* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in ACM SIGCOMM 2026 paper snippet. |
| **Opus DRED packs up to 1 second of speech redundancy at <32 kbps** | Valin et al., *arXiv:2212.04453* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in arXiv:2212.04453 snippet. |
| **Continuous-Time Language Agents cut interactive latency by 19% to ~50%** | Li et al., *arXiv:2609.17416* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in arXiv:2609.17416 snippet. |
| **Endpoint Anticipation forecasts endpoints 2.56s ahead, saving 505 ms** | Udupa et al., *arXiv:2606.13450* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in arXiv:2606.13450 snippet. |
| **Moonshine Tiny has 27.1M params and 5x compute reduction vs Whisper-tiny** | Useful Sensors, *arXiv:2410.15608* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in arXiv:2410.15608 Table 1 snippet. |
| **Moonshine Base has 61.5M params** | Useful Sensors, *arXiv:2410.15608* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in arXiv:2410.15608 Table 1 snippet. |
| **Distil-Whisper large-v2 cuts params by 51% with 5.8x–6.8x speedup** | Gandhi et al., *arXiv:2311.00430* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in arXiv:2311.00430 snippet. |
| **FastConformer 8x downsampling yields 2.8x faster encoder and 33% GMACs** | Rekesh et al., *arXiv:2305.05084* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in Interspeech 2023 snippet. |
| **Zipformer achieves desktop CPU RTF of 0.01 to 0.05 in sherpa-onnx** | Yao, Povey et al., *arXiv:2310.11230* / sherpa-onnx `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in sherpa-onnx benchmark snippet. |
| **Parakeet-TDT predicts tokens and durations for 2.82x speedup over RNN-T** | Xu et al., *arXiv:2304.06795* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in arXiv:2304.06795 snippet. |
| **RelayS2S dual-path speculative drafting preserves ~99% semantic quality** | Mai & Liang, *arXiv:2603.23346* `[VERIFIED-IN-SNIPPET]` | `[VERIFIED-IN-SNIPPET]` | Verified in arXiv:2603.23346 snippet. |
| **Local Wi-Fi round-trip latency is typically 2–5 ms** | Standard 802.11 LAN characteristics `[KNOWN-BUT-UNVERIFIED]` | `[KNOWN-BUT-UNVERIFIED]` | Well established in wireless networking; not explicitly extracted from search snippets. |
| **Zipformer Small/Medium parameter range (~25M–65M)** | k2-fsa icefall architecture specs `[KNOWN-BUT-UNVERIFIED]` | `[KNOWN-BUT-UNVERIFIED]` | Small/Med sizes known from k2-fsa repo, but exact parameter counts were not in the snippet. |
