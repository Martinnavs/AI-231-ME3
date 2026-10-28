To achieve a sub-800ms time-to-first-audio (TTFA) full-duplex voice agent on the HP EliteBook 840 G8, you need to design around your primary bottleneck: **compute-bound prompt processing and memory-bandwidth-bound token generation**.

While 48GB of RAM gives you massive headroom to hold models in memory without swapping, the 11th Gen Intel i5 (Tiger Lake, 4 cores / 8 threads, ~51 GB/s dual-channel DDR4-3200 bandwidth) will choke if you run a dense 8B+ model alongside real-time STT and TTS. However, the i5-1135G7/1145G7 possesses two secret weapons that make this completely viable without a cloud dependency: native **AVX-512 + AVX-512 VNNI** instruction sets on the CPU, and an **80-Execution Unit Intel Iris Xe iGPU** that shares your 48GB system memory.

---

## 1. Hardware-Level Compute Partitioning

In a full-duplex loop, the user might speak *while* the TTS is playing or the LLM is generating. If STT, LLM, and TTS fight for the same 4 CPU cores, audio buffers will underrun and latency will spike. You must partition the hardware across your CPU and iGPU:

* **Intel Iris Xe iGPU (via SYCL / OpenVINO or Vulkan):** Offload the **LLM prompt evaluation (prefill)** or the **STT encoder** here. Prompt processing (reading the system prompt, tool definitions, and conversation history) is compute-bound (matrix-matrix multiplication). The Iris Xe has 80 EUs capable of native INT8/FP16 acceleration, which drastically reduces Time-To-First-Token (TTFT) compared to 4 CPU cores.
* **4-Core Tiger Lake CPU (via AVX-512 VNNI):** Dedicate physical cores to token generation, VAD, and TTS. Tiger Lake was the last Intel client architecture to ship with full **AVX-512** and **Vector Neural Network Instructions (VNNI)** enabled before Alder Lake disabled them. Compiling your inference engine with `-march=tigerlake` unlocks single-instruction INT8 dot-product accumulation (`vpdpbusd`), boosting CPU throughput by ~35–50% over standard AVX2.
* **The 48GB RAM Advantage (Asymmetric Dual-Channel Warning):** A 48GB configuration uses a **32GB + 16GB** SODIMM pair (Intel Flex Mode). The first **32GB** runs in true dual-channel mode (~51.2 GB/s), while the remaining 16GB drops to single-channel (~25.6 GB/s). Keep your entire active pipeline footprint under 16GB so the OS allocates the LLM weights inside the dual-channel memory region, and lock the pages in RAM (`mlock`) so they never drift or swap.

---

## 2. Recommended Model Stack

Because token generation speed on CPU/iGPU is strictly governed by `Memory Bandwidth / Active Parameter Bytes`, you want either a **1.5B–4B dense model** or a **fine-grained Mixture-of-Experts (MoE) model** with ~1B–3B active parameters. Your 48GB of RAM allows you to load a large total-parameter MoE into memory while paying only the compute/bandwidth cost of a tiny model per token.

### The LLM (Tool-Calling Brain)

| Model | Architecture | Quantization & Size | Expected Speed (i5 G8) | Why It Works for Voice + Tools |
| --- | --- | --- | --- | --- |
| **Qwen3.5-4B** *(Sweet Spot)* | Dense (4.7B) | `Q4_K_M` (~2.8 GB) | ~18–24 tok/s | Native `qwen3_coder` tool parser, rocks-solid JSON schema adherence, and stores KV cache in only 8 of 32 layers—slashing prompt eval latency. |
| **LFM2.5-8B-A1B** *(Speed + Smarts)* | Hybrid MoE (8.3B total, **1.5B active**) | `Q4_K_M` (~4.8 GB) | ~30–38 tok/s | Uses your abundant RAM to hold 8B parameters of knowledge, but only routes through 1.5B parameters per token. Emits concise, Pythonic function calls (fewer tokens than verbose JSON). |
| **Gemma 4 E4B** *(Direct Audio Option)* | Edge Dense (4.5B effective) | `Q4_K_M` (~2.7 GB) | ~18–22 tok/s | Built specifically for laptop edge inference with 512-token sliding-window layers and native function calling. Can also ingest raw audio tokens directly into its embedding space, allowing you to bypass STT entirely if desired. |
| **Granite 4.2 3B** | Dense (3.7B) | `Q4_K_M` (~2.2 GB) | ~22–28 tok/s | Purpose-built by IBM for enterprise agentic tool-calling and RAG on constrained hardware; rarely hallucinates tool arguments. |
| **Qwen3.6-35B-A3B** *(Heavyweight MoE)* | MoE (35B total, **3B active**) | `Q4_K_M` (~23 GB) | ~12–16 tok/s | Only possible because of your 48GB RAM. Gives you 35B-class reasoning and multi-step tool routing at the speed of a 3B dense model. |

### The Audio Front-End (VAD + STT) & Back-End (TTS)

* **VAD (Turn Detection + Barge-In):** **Silero VAD v5 (ONNX)** + **Smart Turn v2**. Silero processes 30ms audio chunks in less than 0.5ms on a single CPU thread. Pair it with a lightweight semantic turn detector so the system knows whether a user pause is the end of a thought or just a mid-sentence breath.
* **STT (Speech-to-Text):** **Moonshine Base (ONNX/OpenVINO)** or **Faster-Whisper `distil-small.en` (INT8)**. Moonshine is specifically architected for live voice agents: unlike Whisper, which pads every audio clip to a fixed 30-second window (wasting compute on short 2-second voice commands), Moonshine scales compute proportionally to the audio length, cutting short-utterance transcription latency to **under 80ms** on a mobile i5.
* **TTS (Text-to-Speech):**
* **Kokoro-82M (ONNX INT8/FP16):** An 82M-parameter StyleTTS2-based model that punches way above its weight class in prosody and naturalness while running faster than real-time on CPU.
* **Piper TTS (ONNX VITS) or KittenTTS (15M–80M):** If Kokoro takes too much CPU share during simultaneous duplex decoding, Piper or KittenTTS hits a Real-Time Factor (RTF) of ~0.05–0.09 on two Tiger Lake threads, delivering the first audio packet in **under 40ms**.



---

## 3. Low-Latency Optimization Playbook

To make a local 4-core laptop feel instantaneous and truly full-duplex, standard Ollama defaults won't cut it. Implement these runtime and architectural optimizations:

### A. Inference Engine & Compiler Optimizations

1. **Use `llama-server` (llama.cpp) Compiled from Source or OpenVINO GenAI:**
Pre-built binaries often target generic `x86-64-v3` (AVX2) for compatibility. Compile `llama.cpp` directly on your machine with:
```bash
cmake -B build -DGGML_NATIVE=ON -DGGML_AVX512=ON -DGGML_AVX512_VNNI=ON -DGGML_SYCL=ON

```


Alternatively, convert your LLM and Whisper models to **Intel OpenVINO IR (INT4/INT8)**. OpenVINO can run the LLM on the Iris Xe iGPU while keeping the CPU entirely free for audio DSP, VAD, and TTS.
2. **Thread Pinning (`taskset` / Affinity):**
Hyperthreading (8 logical threads on 4 physical cores) actually *hurts* `llama.cpp` decoding speed due to L1/L2 cache thrashing and memory bus contention. Set `llama-server --threads 3` (leaving 1 physical core + SMT threads for STT/TTS/OS audio loops) and pin processes to isolated physical cores.
3. **Flash Attention & KV Cache Quantization:**
Enable `-fa` (Flash Attention) and quantize the KV cache (`-ctk q8_0 -ctv q8_0`). On a memory-bandwidth-starved CPU/iGPU, shrinking the KV cache by 50% noticeably speeds up token generation as conversation history grows.

### B. Tool-Calling & Prompt Latency Tricks

1. **Prompt Caching (`cache_prompt: true`):**
Tool-calling models require injecting JSON schemas for every available tool into the system prompt (often 500–1,500 tokens). On an i5 CPU, re-evaluating 1,000 tokens takes 2–4 seconds. By keeping your system prompt and tool definitions strictly static at the top of the context array and passing `cache_prompt: true` to `llama-server`, those tokens are evaluated **once** at startup and reused in **0ms** on subsequent turns.
2. **Grammar-Constrained Tool Routing & Early Interception:**
Instead of letting the LLM generate a preamble before calling a tool, configure your tool router to emit a control token (`<tool_call>`) as the very first token when an action is needed. Because the LLM is streaming tokens, your orchestrator can inspect token #1:
* If token #1 is normal text, pipe the token stream directly into the TTS chunker.
* If token #1 is `<tool_call>`, immediately trigger a pre-synthesized filler phrase (e.g., *"Let me check that..."* or a subtle keyboard click earcon) in the audio output thread while `llama.cpp` finishes generating the JSON arguments under a strict GBNF grammar constraint.


3. **Speculative Decoding / Prompt Lookup (`--lookup-ngram-min`):**
When calling tools, roughly 70% of the generated output consists of exact strings copied from the tool schema (function names, parameter keys) or the user's transcript. Enabling **Prompt Lookup Decoding** (n-gram speculative decoding, which requires zero extra draft model RAM) allows `llama-server` to draft 4–8 JSON tokens at once from the prompt cache, doubling or tripling tool-call generation speed.

### C. Full-Duplex Audio Pipeline Mechanics

1. **Clause-Level Streaming Chunking:**
Never wait for a full sentence to finish generating before starting TTS. Accumulate LLM output tokens until the **first 4–6 words** or a weak punctuation boundary (`,`, `—`, `:`, `.`) is hit, and immediately flush that micro-chunk to Piper/Kokoro. While the user listens to those first 1.5 seconds of audio, the LLM and TTS have ample time to synthesize the rest of the sentence seamlessly.
2. **Acoustic Echo Cancellation (AEC) + Instant Barge-In Cancellation:**
Full-duplex requires the mic to stay hot while the speaker plays TTS. Run **SpeexDSP** or **WebRTC AudioProcessing (AEC3)** on the audio input stream (or use a headset) so the VAD only triggers on the user's voice. The exact millisecond Silero VAD detects sustained user speech (>150ms) during playback:
* Flush the local ALSA/PipeWire audio playback ringbuffer immediately.
* Send an HTTP abort / cancel signal to the `llama-server` slot so it halts generation immediately, saving CPU cycles for the incoming STT pass.
* Truncate the assistant's message in the conversation history to match the exact word where TTS playback was interrupted, so the LLM knows what it actually managed to say out loud.



---

## 4. End-to-End Resource Budget on Your EliteBook

Running this entire stack simultaneously will consume less than **8 GB of your 48 GB RAM** (keeping everything comfortably inside your fast dual-channel memory zone):

* **OS + Audio Server (PipeWire/WebRTC AEC):** ~1.5 GB RAM | <5% CPU
* **Silero VAD + Moonshine Base STT (INT8):** ~0.3 GB RAM | 1 CPU Core (bursts for <80ms on speech end)
* **Qwen3.5-4B or LFM2.5-8B-A1B (`Q4_K_M` + 8K KV Cache):** ~3.5–5.2 GB RAM | Iris Xe iGPU (Prefill) + 3 Physical CPU Cores (Decode)
* **Kokoro-82M or Piper TTS (ONNX INT8):** ~0.2 GB RAM | 1 CPU Core (active after LLM chunk #1)
