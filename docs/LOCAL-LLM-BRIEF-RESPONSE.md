**Bottom Line Up Front:** **No**, a local LLM on the i5-1145G7 cannot beat your hosted `gemini-3.1-flash-lite` baseline (0.84 s hedged p50, 1.0 s p90) while maintaining your 87-prompt tool accuracy bar (92/92 `smart_home`, 56/58 mixed, 72% no-tool).

My earlier two documents contained over-optimistic throughput estimates, internal contradictions on memory/offload topology, and outdated `llama.cpp` CLI flags. Your audit in Section 9 is spot-on. Below is the corrected arithmetic, a point-by-point retraction/correction of the previous advice, an analysis of whether a smaller local router makes sense (it does not), and a 1-hour falsifiable benchmark protocol if you want empirical proof for your project report.

---

## 1. The Arithmetic: Why Local Loses to 0.84 s on This CPU

### Hardware Bandwidth Ceiling (Source vs. Estimate)

* **Theoretical Peak (Source):** Dual-channel DDR4-3200 has a theoretical peak bandwidth of **51.2 GB/s** ($3200\text{ MT/s} \times 8\text{ bytes} \times 2\text{ channels}$). However, you have 48 GB installed (32 GB + 16 GB SODIMMs), meaning Intel Flex Memory runs the first 32 GB in dual-channel and the upper 16 GB in single-channel (25.6 GB/s).
* **Achievable Stream Bandwidth (Estimate):** On mobile 15–28 W Tiger Lake (i5-1145G7) under the `powersave` governor, sustained `llama.cpp` memory reads reach ~60–68% of theoretical dual-channel peak—roughly **31–35 GB/s** (and ~18–22 GB/s if allocations land in the single-channel upper 16 GB).
* **Token Generation Ceiling Formula:**

$$\text{Decode Speed (tok/s)} \approx \frac{\text{Effective Memory Bandwidth (GB/s)}}{\text{Active Model Weights per Token (GB)}}$$


* **Prefill Throughput (Estimate scaled from your i7-10700 reference):** Your cited i7-10700 benchmark (8 cores, 65 W desktop, AVX2) achieved 237 tok/s prefill (0.8B) and 114 tok/s (2B). The i5-1145G7 has half the physical cores (4 cores) and thermal-throttles under sustained all-core loads, though it has AVX-512 VNNI. Even with AVX-512 VNNI compensating partially for core count on `q4_0`/`q8_0` dot products, concurrent CPU contention from Kroko, Parakeet-TDT, AEC, and Pipecat will hold practical prefill to roughly **55–75% of the i7-10700 numbers**:
* **0.8B–1B class:** ~130–160 tok/s prefill | ~25–32 tok/s decode
* **1.5B–2B class:** ~65–85 tok/s prefill | ~13–17 tok/s decode
* **3B–4B class (e.g., Granite 3B / Qwen 4B, ~2.2–2.8 GB):** ~35–50 tok/s prefill | **~10–12.5 tok/s decode**



### Turn Latency Breakdown (Tool Call Turn)

Assume the model's 1,500-token system prompt + tool schemas are already warm in the KV cache (`cache_prompt: true`), and a new turn arrives with **40 new tokens** (20 tokens utterance + 20 tokens chat wrapper/history delta) and must emit **32 output tokens** (function name + JSON args + `say` string):

$$T_{\text{LLM}} = T_{\text{prefill\_delta}} + T_{\text{decode}}$$

| Model Class | Active Weights (`Q4`) | Assumed Prefill (tok/s) | Prefill 40 tok (s) | Assumed Decode (tok/s) | Decode 32 tok (s) | **Total Local LLM Time** | **vs. Hosted (`0.84 s` p50)** | Tool Accuracy Expectation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **3B–4B Dense** *(Granite 3B / Qwen 4B)* | ~2.2–2.8 GB | 42 tok/s | ~0.95 s *(cold: ~35 s!)* | 11 tok/s | ~2.91 s | **~3.86 s** *(~2.3 s w/ ngram spec)* | **+1.5 to +3.0 s slower** | Closest to Gemini, still lower on `no-tool` |
| **1.5B–2B Dense** *(Qwen 2B)* | ~1.2–1.5 GB | 75 tok/s | ~0.53 s | 15 tok/s | ~2.13 s | **~2.66 s** *(~1.6 s w/ ngram spec)* | **+0.8 to +1.8 s slower** | Drops below bar (BFCL-V4 43.6) |
| **0.5B–0.8B Dense** *(Qwen 0.8B)* | ~0.5–0.6 GB | 145 tok/s | ~0.28 s | 28 tok/s | ~1.14 s | **~1.42 s** *(~0.9 s w/ ngram spec)* | **Ties at best** | Fails accuracy bar badly (BFCL-V4 25.3) |

**Verdict on Question 1:** Even if prompt caching works flawlessly (0 ms cost for the 1.5k static prefix) and n-gram speculative decoding gives a generous 1.7x decode speedup on JSON keys, a **3B–4B model takes ~2.3–3.9 seconds** just to emit the tool call, blowing past your 1.8 s stall-filler threshold on every single turn. A **0.8B model** can get down to ~0.9–1.4 s, merely tying your hedged Gemini p50 (0.84 s) while failing your 87-prompt accuracy requirement. Furthermore, on plain-answer turns (25% of traffic), a local model removes your ability to hedge requests, degrading your p90/p95 tail whenever a prior turn's context shifts.

---

## 2. Concrete Model & Build to Test First (and Drop Criteria)

If you want to run a single falsifiable test to prove this empirically for your project documentation, do **not** test a hybrid recurrent model first. Test a pure full-attention transformer so KV prefix truncation (`llama_memory_seq_rm`) works deterministically without context-checkpoint memory bloat or full prompt re-processing.

* **Candidate Model:** **`Qwen2.5-3B-Instruct` (or `Granite-3.2-2B-Instruct` / `Llama-3.2-3B-Instruct`)** quantized to **`Q4_0`** (so `llama.cpp`'s automatic online AVX-512 VNNI weight repacking triggers at load time). Why a standard full-attention 2B–3B instead of Qwen3.5/LFM2.5? Because it has standard GQA attention across all layers—meaning `--cache-prompt` works via simple KV sequence rollback without hybrid recurrent checkpoint bugs.
* **Drop Criteria (Stop the experiment immediately if ANY of these trigger during the 1-hour test):**
1. **Warm Prefill + 32-Token Decode > 1.0 s:** If a warm request (1,500 cached tokens + 30 new tokens $\rightarrow$ 32 generated tokens) takes more than **1,000 ms** on 3 threads (since Gemini p50 is 840 ms and p90 is 1,000 ms).
2. **Cold 1.5k Prompt Prefill > 15 s:** Indicates thermal throttling or single-channel memory placement.
3. **Dry-Run Accuracy < 88/92 on `smart_home` or < 13/18 on `no-tool`:** Any regression on the `no-tool` slice means false tool triggers in live demos.



---

## 3. Point-by-Point Response to Your Concerns (Section 9)

You caught major flaws in the previous two responses. Here is the direct accounting:

1. **Speed Estimates (18–24 tok/s for Qwen3.5-4B):** **Withdrawn.** You are right. At 2.8 GB (`Q4_K_M`) over a 51.2 GB/s theoretical bus, $51.2 / 2.8 = 18.28\text{ tok/s}$ is the *100% theoretical hardware ceiling* assuming zero memory contention, dual-channel lock, and 0% instruction overhead. At a realistic 60–65% bandwidth efficiency under `powersave` on a 15–28 W mobile package, **10–12 tok/s** is the true physical range. My previous 18–24 tok/s figure was wrong.
2. **Tool-Call Arithmetic:** **Agreed.** Your arithmetic holds. Generating 25–40 tokens at 10–12 tok/s costs 2.1–4.0 s of decode time alone. Speculative decoding cannot bridge a 3x gap when the target model's verification pass is still memory-bandwidth bound on CPU.
3. **Prompt Caching on Hybrid Models (Qwen3.5/3.6 Gated DeltaNet & LFM2.5 Conv):** **You are completely right; my previous claim is withdrawn.**
* *Source:* As documented in `llama.cpp` server internals and recent engineering analyses ([Particula Tech: Full Prompt Re-Processing in llama.cpp](https://particula.tech/blog/prompt-reprocessing-swa-hybrid-models-kv-cache) and [GitHub Discussion #30664](https://github.com/ggml-org/llama.cpp/discussions/30664)), full-attention models can roll back their KV cache to an arbitrary token index using `llama_memory_seq_rm`. Hybrid recurrent/convolution models (`Qwen3.5/3.6`, `LFM2`) and Sliding Window Attention (SWA) models **cannot** truncate recurrent state arbitrarily.
* Instead, `llama-server` saves periodic partial-state snapshots (`--ctx-checkpoints`, 60–215 MiB each). When a new turn diverges at a token index that doesn't align with a saved checkpoint, `llama-server` silently logs `forcing full prompt re-processing due to lack of cache data (likely due to SWA or hybrid/recurrent memory)` at trace verbosity (`-lv 4`, hidden at default info verbosity `-lv 3`) and **re-evaluates the entire 1.5k prompt from scratch**. Worse, `--cache-reuse` does not fix recurrent state rollback. Therefore, **only a pure full-attention dense model** is safe for zero-reprocessing prefix reuse in `llama.cpp` today.


4. **Internal Contradictions:** **Withdrawn.**
* Recommending a 23 GB model (`Qwen3.6-35B-A3B`) contradicted the 32 GB dual-channel Flex Mode boundary once OS, STT, TTS, and KV caches are added.
* Splitting prefill onto the iGPU and decode onto the CPU is **impossible** in `llama.cpp` because layers are statically assigned to devices at load time (`-ngl`), not dynamically routed by inference phase. Furthermore, since you have no Vulkan, OpenCL, or SYCL runtime installed on the laptop, iGPU offload is off the table entirely.
* The thread count flip-flop (3 vs. 2) was sloppy.


5. **Outdated or Incorrect Flags:** **Corrected with Sources.** `llama.cpp` overhauled its CLI flags in mid-to-late 2026:
* **`--repack` / `Q4_0_4_8`:** Deprecated and removed. Online weight repacking for AVX-512 VNNI / ARM Neon is now **enabled by default** when loading standard `Q4_0` models; the opt-out flag is `--no-repack`.
* **`--no-mmap` and `--mlock`:** Removed in PR #20834 / PR #28334 (September 2026, build `b10875+`). They were replaced by `-lm, --load-mode`.
* **`--lookup-ngram-min` and `"lookup": true`:** Removed in PR #22838 (May 2026, build `b9109+`). Speculative n-gram lookup in `llama-server` is now configured via `--spec-type ngram-simple` (or `ngram-map-k`), `--spec-ngram-simple-size-n`, and `--spec-draft-n-max` (Source: [Debian manpages `llama-server(1)` Oct 2026](https://manpages.debian.org/unstable/llama.cpp-tools/llama-server.1.en.html) and [2026 llama.cpp Flags Guide](https://openclawdc.com/blog/llama-cpp-flags-explained/)).


6. **Shared CPU & `nice -n -15`:** **Withdrawn.** Running `nice` below `0` requires `CAP_SYS_NICE` / root privileges. More importantly, when Kroko + Parakeet-TDT finish decoding, your Python orchestrator, MCP servers, and TTS immediately need CPU cycles. If `llama-server` occupies 3–4 cores, it competes directly with audio/network loops and triggers thermal throttling under `powersave`.
7. **Replacing STT/TTS:** **Withdrawn.** You already benchmarked streaming ASR alternatives (21.8% WER on Zipformer) and need Parakeet-TDT + hotword boosting + name repair for near-field Filipino-English speech, plus Supertonic with pre-rendered caches. Touching STT or TTS would break a working, measured pipeline.
8. **Endpointing Excluded from Targets:** **Acknowledged.** With a fixed 0.60 s VAD pause rule + ~0.25 s Parakeet-TDT re-decode, **~0.85 s is already spent before the LLM sees a single token**. Claiming sub-800 ms end-to-end TTFA was impossible given your pipeline architecture.

---

## 4. Does a Smaller Local Role (e.g., Home-Control Router) Pay Off?

**No. Do not build a local LLM router for `smart_home` commands.** Looking at your Section 7 traffic mix and Section 5 latency breakdown, the math strictly rules it out:

1. **Tiny Traffic Share (10%):** Only 10% of turns (7 of 69 turns, 6 of them music/volume) are `smart_home` commands that miss your regex fast path. Even if a local router magically cut LLM time from `0.84 s` to `0.44 s` (a 0.40 s win, which is already optimistic on CPU), the expected end-to-end improvement across all turns is:

$$0.10 \times 0.40\text{ s} = \mathbf{0.04\text{ s}\ (40\text{ ms})}$$


2. **Router Fall-Through Penalty Makes Net Latency *Worse*:** How do you know a turn belongs to the 10% `smart_home` slice *before* running the router?
* **If you run the local router serially before Gemini:** On the other **76% of turns** (51% other tools + 25% plain answers), if the local router takes even `0.30 s` to output `"route": "cloud"`, you just added **+300 ms of dead latency to 76% of your traffic** ($0.76 \times 0.30\text{ s} = +0.23\text{ s}$ penalty) to save 40 ms on 10% of traffic.
* **If you run the local router in parallel with Gemini (hedging local vs. cloud):** Running a local model on every turn burns 2–3 CPU cores right as Parakeet-TDT finishes and right as Supertonic TTS needs to synthesize the first sentence—slowing down TTS and hurting p90/p95 across the board. Moreover, on the 9 `no-tool` prompts (where Gemini gets 72%), small 0.5B–1.5B models have much higher false-positive tool-call rates. Since `smart_home` has **side effects** (lights, music, volume), a false positive from a parallel local model is catastrophic for a demo.


3. **Expanding Your Regex / Deterministic Fast Path Is 10x More Effective:** Your regex fast path already handles 14% of turns in **1.0 s end-to-end** (0 ms LLM time, 100% precision, 0 CPU contention). Since 6 of the 7 remaining `smart_home` turns were **music or volume commands**, adding 5–10 deterministic intent patterns (or rapid fuzzy matching with `rapidfuzz` in <1 ms) for music/volume phrasing will capture most of that 10% slice with **0.84 s saved per hit** and zero model RAM/CPU cost.

### Where Your Real Latency Wins Actually Are (From Section 5)

Your data proves the LLM is **not** your bottleneck anymore (`0.84 s` p50, `1.0 s` p90 is already better than or equal to commercial voice agent LLM legs):

* **Weather tool HTTP latency (`1.3–4.2 s`):** This single tool takes **1.5x to 5x longer than Gemini itself**. Your planned open items—**caching weather/place lookups and running a background home-forecast refresh** (plus emitting the LLM's `say` argument to Supertonic TTS *before* awaiting the Open-Meteo HTTP response, if not already overlapped)—will cut median tool-turn latency by **1.0–2.5 seconds**, dwarfing anything a local LLM could ever do.

---

## 5. Under-1-Hour Empirical Benchmark Protocol (For Your Report)

If you want hard numbers on the i5-1145G7 to include a table in your project report showing *why* you stayed with `gemini-3.1-flash-lite`, run this exact 30-minute CPU-only test on Arch Linux. It installs nothing permanent, sends zero data externally, and uses current 2026 `llama.cpp` flags.

### Step 1: Build Current `llama.cpp` (CPU-Only, AVX-512 VNNI Enabled, ~5 mins)

```bash
# Clone shallow master into /tmp
git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama-cpp-bench
cd /tmp/llama-cpp-bench

# Configure CPU-only build with native Tiger Lake AVX-512 + VNNI
cmake -B build \
  -DGGML_NATIVE=ON \
  -DGGML_AVX512=ON \
  -DGGML_AVX512_VNNI=ON \
  -DCMAKE_BUILD_TYPE=Release

# Compile only llama-bench and llama-server using all threads
cmake --build build --config Release -j $(nproc) --target llama-bench llama-server

```

### Step 2: Download One Full-Attention 3B `Q4_0` Model (~3 mins)

We use `Q4_0` so `llama.cpp`'s default online repacking automatically converts the weights in RAM to the AVX-512 interleaved layout (`Q4_0_8_8`):

```bash
curl -L -o /tmp/qwen2.5-3b-instruct-q4_0.gguf \
  https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_0.gguf

```

### Step 3: Measure Raw Prefill (`pp1536`) & Decode (`tg32`) Across Thread Counts (~5 mins)

This tests your exact 1.5k prompt size (`-p 1536`) and your exact 32-token tool-call output size (`-n 32`) across 2, 3, and 4 physical cores:

```bash
/tmp/llama-cpp-bench/build/bin/llama-bench \
  -m /tmp/qwen2.5-3b-instruct-q4_0.gguf \
  -p 1536 -n 32 \
  -t 2,3,4 \
  -fa 1

```

* **What to record:** Look at `pp1536` (tok/s) and `tg32` (tok/s). Divide `32 / tg32` to get pure decode time in seconds. If `32 / tg32 > 0.84 s`, the model cannot beat Gemini even with an infinitely fast prefill.

### Step 4: Measure Cold vs. Warm Prompt Cache + N-Gram Speculative Decoding (~10 mins)

Launch `llama-server` using current 2026 flags (no deprecated `--mlock`/`--no-mmap`/`--lookup-ngram-min`):

```bash
/tmp/llama-cpp-bench/build/bin/llama-server \
  -m /tmp/qwen2.5-3b-instruct-q4_0.gguf \
  -t 3 -tb 4 \
  -c 4096 -np 1 \
  -fa on \
  --cache-prompt \
  --spec-type ngram-simple \
  --spec-ngram-simple-size-n 4 \
  --spec-draft-n-max 5 \
  --port 8089 \
  -lv 4

```

*(Note: `-lv 4` enables trace logging so you can verify `slot update_slots: ... prompt_cache` hits and confirm zero `forcing full prompt re-processing` events).*

Send your real 1.5k-token system prompt + tool schema twice using `curl` to `/v1/chat/completions` (Run 1 = Cold Prefill; Run 2 = Warm Prefix Cache + 1 new user utterance) and inspect the `timings` object in the JSON response:

* **`timings.prompt_ms` (Run 1 vs. Run 2):** Gives your exact Cold vs. Warm prefill latency in milliseconds.
* **`timings.predicted_ms` (Run 2):** Gives your exact tool-call generation latency with n-gram speculation enabled.
* **Sum `prompt_ms + predicted_ms` on Run 2:** Compare directly against `840 ms` (your hosted Gemini hedged median). If it exceeds `840 ms`—or if your 87-prompt dry-run script against `http://localhost:8089/v1` drops below 92/92 on `smart_home` or 13/18 on `no-tool`—you have complete, reproducible data justifying keeping `gemini-3.1-flash-lite`.

For a visual walkthrough of how `llama.cpp`'s native context-lookup speculative decoding operates without a draft model, see [llama.cpp Prompt Lookup Decoding: Speculation With No Draft Model](https://www.youtube.com/watch?v=tRuboGEYkAo).
