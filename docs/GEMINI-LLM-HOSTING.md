To squeeze every millisecond out of `llama.cpp` (`llama-server`) on your HP EliteBook 840 G8 without starving your STT, TTS, and VAD pipelines, you need to optimize across four layers: **Backend & Tensor Placement**, **`llama-server` Runtime Flags**, **Prompt KV-Cache Topology**, and **Linux Kernel / Process Isolation**.

Because your Tiger Lake i5 only has 4 physical cores and shares a single DDR4-3200 memory controller (~51.2 GB/s) with the Iris Xe iGPU, an unthrottled `llama-server` process will saturate the memory bus and induce audio crackles or STT lag.

---

## 1. Backend & Quantization Optimizations

### Vulkan Over SYCL on Iris Xe (Plus Partial Offload)

While Intel's oneAPI SYCL backend was historically recommended for Intel GPUs, recent `llama.cpp` benchmarks on Intel integrated architectures show the **Vulkan backend (`-DGGML_VULKAN=ON`) frequently outperforming SYCL** in token generation (`tg`) and Flash Attention stability—without requiring a 10GB+ Intel oneAPI toolkit installation.

* **Why Partial Offload (`-ngl`) Beats Full Offload on Unified Memory:** Since the Iris Xe iGPU and i5 CPU read from the exact same physical RAM sticks, offloading 100% of layers to the iGPU for single-batch token decoding (`batch_size = 1`) often runs *slower* than the CPU because the iGPU has higher kernel-launch overhead and smaller caches than the CPU's AVX-512 VNNI units.
* **The Hybrid Trick (`--override-tensor` / `-ot`):** If you run a Mixture-of-Experts model (like `LFM2.5-8B-A1B` or `Qwen3-30B-A3B`), offload the shared attention layers and prompt processing to the Vulkan iGPU (`-ngl 99`), but explicitly pin the sparse MoE feed-forward weights to the CPU using `--override-tensor "\.ffn_.*_exps\.=CPU"`. This splits the compute load cleanly across silicon without duplicating memory transfers.

### CPU-Optimized Quantization (`Q4_0_4_8` or `IQ4_XS` + `--repack`)

Standard `Q4_K_M` is great for GPUs, but on a Tiger Lake CPU with **AVX-512 VNNI**, unpacking `K`-quants adds minor instruction overhead during matrix-vector multiplication.

* Pass **`--repack`** (or use `Q4_0` with runtime repacking enabled in recent `llama.cpp` builds). `llama.cpp` will automatically repack `Q4_0` weights in RAM at startup into interleaved `Q4_0_8_8` / `Q4_0_4_8` layouts tailored to your AVX-512 register width, yielding a **10–18% boost in CPU token generation speed** at zero quality loss compared to `Q4_0`.

---

## 2. Production `llama-server` Launch Configuration

When running a single-user full-duplex voice agent, default `llama-server` flags waste RAM and compute on multi-user concurrency. Use a tightly scoped launch profile:

```bash
llama-server \
  -m /path/to/model.gguf \
  --threads 2 \
  --threads-batch 4 \
  --ctx-size 4096 \
  --parallel 1 \
  --batch-size 512 \
  --ubatch-size 256 \
  --flash-attn \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --cache-reuse 256 \
  --mlock \
  --no-mmap \
  --cont-batching \
  --metrics \
  --port 8080

```

### Why Each Flag Matters for Your Stack:

* **`--threads 2` vs. `--threads-batch 4` (Asymmetric Threading):** This is the single most important flag for a 4-core voice box.
* **Prompt Evaluation (`--threads-batch 4`):** When the user finishes speaking and STT hands off the transcript, TTS is silent. You want all 4 physical cores (plus the iGPU) hammering the prefill phase to minimize Time-To-First-Token (TTFT).
* **Token Generation (`--threads 2`):** Once token #1 is emitted, TTS starts synthesizing audio in parallel and VAD/AEC are actively monitoring the mic for barge-in. Because token generation is *memory-bandwidth bound* (not compute bound), **2 physical AVX-512 cores can already saturate ~85% of your dual-channel DDR4 bandwidth**. Limiting decode to 2 threads leaves 2 full physical cores completely uncontested for TTS and STT, preventing CPU context-switching jitter.


* **`--parallel 1` (Single Slot):** Restricts the server to 1 active KV cache slot. Ensures 100% of `--ctx-size 4096` belongs to your active voice session and prevents `llama-server` from allocating unnecessary multi-slot buffers.
* **`--cache-reuse 256` (Automatic Prefix Shifting):** Even when middle turns of a conversation get truncated or modified (such as when a user barges in and you rewrite the assistant's interrupted message), `--cache-reuse` allows `llama-server` to shift KV cache chunks inside the slot without re-evaluating the surrounding context.
* **`--mlock` + `--no-mmap`:** Because you have 48GB of RAM, you never want the Linux page cache evicting model weights or triggering page faults mid-sentence. `--no-mmap` loads the model directly into heap memory inside your fast dual-channel RAM region, and `--mlock` pins it there permanently.

---

## 3. Squeezing Out LLM Latency at the Request Layer

### 1. Slot Pinning (`id_slot: 0`) & KV Cache Warming

In every HTTP `POST /v1/chat/completions` or `/completion` payload, explicitly pass `"id_slot": 0` and `"cache_prompt": true`.

* **Pre-Warming on Boot:** While your Python orchestrator initializes the microphone, send a dummy request to `llama-server` containing your exact system prompt + tool schemas with `n_predict: 1`. This pre-populates Slot 0's KV cache so the user's *very first* spoken utterance experiences zero system-prompt prefill delay.

### 2. Zero-Model Speculative Decoding (`--lookup-ngram-min 3`)

In tool-calling workflows, the LLM spends a lot of time emitting boilerplate JSON keys (`{"name": "get_weather", "arguments": {"location": ...}}`) that already exist verbatim in your system prompt's tool definitions.

* By enabling prompt lookup decoding (`--lookup-ngram-min 3` on the server or `"lookup": true` per request), `llama-server` checks its existing prompt KV cache for matching n-grams and drafts 4–8 tokens at once. Because verifying 5 drafted tokens in a single forward pass takes almost the same memory bandwidth as generating 1 token, **JSON tool call generation speeds up by 1.8x–2.5x** without loading a separate draft model into RAM.

### 3. Lazy Grammar Constraints (Trigger-Based GBNF)

Never apply a strict JSON schema grammar to the entire response if the model might just reply conversationally—grammars add per-token sampling overhead and force awkward JSON wrapping around normal speech.

* Instead, use `llama-server`'s native tool parser with **lazy grammar triggers**. The model generates unconstrained natural speech at maximum speed, and the GBNF grammar state machine only activates the instant the model emits the `<tool_call>` trigger token.

---

## 4. Orchestrating & Tracking the Whole System (CPU Pinning & Telemetry)

To prevent the components from stepping on each other on your 4-core / 8-thread i5 (Cores `0-3` physical, `4-7` SMT siblings), isolate them at the OS level and monitor everything from a unified asyncio control loop.

### Hardware Core Pinning Topology (`taskset`)

Use `lscpu -e` to verify your core mapping, then launch each service pinned to dedicated silicon:

| Component | Assigned Cores (`taskset -c`) | Process Priority (`nice`) | Why This Layout Works |
| --- | --- | --- | --- |
| **Audio I/O + WebRTC AEC + Silero VAD** | Core `0` | `nice -n -15` (Real-time priority) | Audio buffers run on 10ms–30ms deadlines. Core 0 is never touched by LLM token generation, guaranteeing zero mic dropouts or echo-cancellation drift. |
| **Moonshine / Faster-Whisper STT** | Core `0,1` (Shared burst) | `nice -n -5` | Only runs for ~60–80ms right *before* the LLM starts generating, or during a barge-in (when LLM generation is immediately aborted). |
| **Kokoro / Piper TTS** | Core `1` (+ SMT `5`) | `nice -n -10` | Runs concurrently with `llama-server` token decoding. Having Core 1 dedicated to ONNX Runtime ensures TTS chunks synthesize without waiting on LLM thread locks. |
| **`llama-server` (Decode & Prefill)** | Cores `2,3` (Decode) / `0,1,2,3` (Prefill) | `nice -n 0` | Pinned primarily to Cores 2 & 3 (`taskset -c 2,3` for steady decode) so LLM math never starves the audio pipeline on Cores 0 & 1. |

### Tracking Component Health & Latency in Real Time

When debugging full-duplex loops, you need visibility into whether latency came from VAD tail-padding, STT, LLM prefill, or TTS synthesis:

1. **Poll `llama-server` Native Telemetry (`/slots` & `/metrics`):**
* Hitting `GET http://localhost:8080/slots` returns a tiny JSON payload showing the exact state of Slot 0 (`is_processing`, `n_past` KV cache depth, and `n_decoded`).
* In every streaming completion response, include `"stream_options": {"include_usage": true}` (or read the final `timings` object in `/completion`). `llama-server` reports exact millisecond breakdowns:
* `prompt_ms` / `prompt_per_second` (Prefill latency — tells you if `cache_prompt` missed!).
* `predicted_ms` / `predicted_per_second` (Decode throughput — tells you if thermal throttling or single-channel RAM spillover occurred).




2. **Instant Slot Cancellation on Barge-In:**
When using Python `httpx` or `aiohttp` to stream from `llama-server`, simply closing the client HTTP connection (`await response.aclose()`) immediately triggers `llama-server`'s disconnect handler, aborting the active forward pass within **1 token (~35ms)** and freeing the memory bus for your STT engine.
3. **End-to-End Turn Stopwatch (Per-Turn Structured Logging):**
In your Python orchestrator, stamp `time.perf_counter()` at four boundaries for every voice turn:
* `t_vad_end` $\rightarrow$ `t_stt_done` *(Target: less than 80ms)*
* `t_stt_done` $\rightarrow$ `t_llm_first_token` *(Target: less than 150ms with warm KV cache)*
* `t_llm_first_token` $\rightarrow$ `t_llm_first_clause` *(Target: ~180ms for 4–5 tokens)*
* `t_llm_first_clause` $\rightarrow$ `t_tts_first_pcm_byte` *(Target: less than 60ms)*



For a side-by-side comparison of how the `llama.cpp` backends behave on Intel graphics hardware, check out this benchmark of [llama.cpp SYCL vs Vulkan on Intel Arc](https://www.youtube.com/watch?v=X7sM4YGj_48), which walks through testing `llama-bench` with real prompts across both execution backends.

 *YouTube video views will be stored in your YouTube History, and your data will be stored and used by YouTube according to its [Terms of Service*](https://www.google.com/search?q=https%3A%2F%2Fwww.youtube.com%2Fstatic%3Ftemplate%3Dterms)
