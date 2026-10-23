### 1. Google Models Supporting Function Calling and Streaming

The Gemini API exposes both stable (GA) and preview models that support bidirectional streaming (`streamGenerateContent`) and function calling (`tools.functionDeclarations`). 

*Note: Access reflects the active Gemini lineup as of late 2026 documentation.*

| Model Name | Exact Model ID | Status | Primary Modality & Focus |
| :--- | :--- | :--- | :--- |
| **Gemini 3.8 Flash** | `gemini-3.8-flash` | GA / Stable | Low-latency workhorse, agentic workflows, complex tool-use |
| **Gemini 3.6 Flash** | `gemini-3.6-flash` | GA / Stable | Predecessor stable workhorse |
| **Gemini 3 Flash Preview** | `gemini-3-flash-preview` | Preview | Preview iteration of Gemini 3 Flash |
| **Gemini 3.5 Flash-Lite** | `gemini-3.5-flash-lite` | GA / Stable | Cost- and speed-optimized lightweight model |
| **Gemini 3.1 Flash-Lite Preview** | `gemini-3.1-flash-lite-preview` | Preview | High-frequency, low-latency lightweight preview |
| **Gemini 3.8 Live** | `gemini-3.8-live` | Preview / GA | Bidirectional WebSocket audio/text streaming with non-blocking tool calls |
| **Gemini 4 Argon** | `gemini-4-argon` | GA / Stable | Frontier reasoning flagship (not latency-optimized) |
| **Gemini 3.1 Pro Preview** | `gemini-3.1-pro-preview` | Preview | Frontier multimodal reasoning (not latency-optimized) |
| **Gemini 2.5 Flash** | `gemini-2.5-flash` | Legacy / Restricted | Legacy tier (restricted to grandfathered accounts) |
| **Gemini 2.5 Flash-Lite** | `gemini-2.5-flash-lite` | Legacy / Restricted | Legacy tier (restricted to grandfathered accounts) |

*(Older models such as `gemini-2.0-flash`, `gemini-2.0-flash-lite`, and `gemini-1.5-flash` have reached end-of-life / shutdown).*

---

### 2. Pricing and Free Tier Rate Limits

*(Source dates: Google Developer Documentation & CostGoat, mid-to-late 2026).*

| Exact Model ID | Input Price / 1M Tokens | Output Price / 1M Tokens | Free Tier Available? | Free Tier Limits (RPM / RPD) |
| :--- | :--- | :--- | :--- | :--- |
| `gemini-3.5-flash-lite` | **$0.25** | **$1.50** | Yes | **5 RPM** / **20 RPD** (250k TPM) |
| `gemini-3.1-flash-lite-preview` | **$0.25** | **$1.50** | Yes | **5 RPM** / **20 RPD** |
| `gemini-3.8-flash` | **$0.75** *(promo through Dec 31, 2026; $1.50 std)* | **$3.75** *(promo through Dec 31, 2026; $7.50 std)* | Yes | **5 RPM** / **20 RPD** (250k TPM) |
| `gemini-3.6-flash` | **$0.75** *(promo through Dec 31, 2026)* | **$3.75** | Yes | **5 RPM** / **20 RPD** |
| `gemini-3-flash-preview` | **$0.50** | **$3.00** | Yes (Restricted) | Project-specific (typically **5 RPM** / **20 RPD**) |
| `gemini-2.5-flash` *(Legacy)* | **$0.30** *(prompts ≤ 200k tokens)* | **$2.50** | Grandfathered only | **5 RPM** / **20 RPD** |
| `gemini-2.5-flash-lite` *(Legacy)* | **$0.10** | **$0.40** | Grandfathered only | Grandfathered project quotas |
| `gemini-4-argon` | **$2.00** *(increases to $4.00 Dec 1, 2026)* | **$10.00** *(increases to $20.00 Dec 1, 2026)* | **No** | Paid billing required |
| `gemini-3.1-pro-preview` | **$2.00** | **$12.00** | **No** | Paid billing required |
| `gemini-3.8-live` | Base text equivalent + audio token multiplier | Audio stream billing rates | **No** (or unlisted) | Quota allocated on paid projects |

*Note on Free Tier:* Free tier traffic across Google AI Studio is shared across projects, and user prompts/responses may be logged and reviewed by human annotators to train Google models. Paid tiers eliminate training usage and expand rate limits to spend-based thresholds ($10 to $200 per 10 minutes) with up to 1,000+ RPM.

---

### 3. Cheapest vs. Fastest Models (Latency & TTFT Benchmarks)

* **Cheapest with reliable function calling:** **`gemini-3.5-flash-lite`** ($0.25 input / $1.50 output per 1M tokens) is the cheapest active model that executes function calling. For complex multi-tool schemas, **`gemini-3.8-flash`** ($0.75 / $3.75) is significantly more reliable against schema hallucinations. *(For grandfathered users, legacy `gemini-2.5-flash-lite` at $0.10 / $0.40 is the cheapest).*
* **Fastest in Time-to-First-Token (TTFT):** **`gemini-3.5-flash-lite`** (and legacy `gemini-2.5-flash-lite`).

#### Latency Benchmarks
* **OpenRouter Independent Benchmarks (P50 latency & throughput, 2026):**
  * `gemini-2.5-flash-lite`: **0.33s – 0.50s** roundtrip latency; **117 – 149 tps** throughput.
  * `gemini-3.1-flash-lite`: **0.52s – 1.03s** P50 latency; **122 – 190 tps** throughput.
  * `gemini-3.8-flash`: **1.51s – 2.57s** standard roundtrip latency (with default dynamic thinking active); **87 – 137 tps** throughput.
* **Artificial Analysis Independent Benchmarks (2026):**
  * Flash-tier non-reasoning / minimal thinking TTFT: **~0.40s – 0.45s** (e.g., ~430 ms).
  * Flash reasoning with thinking enabled: Artificial Analysis tracks "Time to First Answer Token," noting that internal thinking generates hidden deliberation tokens, adding hundreds of milliseconds to multiple seconds before the first visible token streams.
  * *Comparison points (Artificial Analysis)*: Groq LPU endpoints running open models (Llama 3.x) reach **~0.4s – 0.8s TTFT** with **500–800+ tps** throughput; Claude 3.5/4.5 Haiku typically registers **~0.5s – 1.0s TTFT**.
* **Google Vendor Claims [Vendor Claim]:**
  * Google claims Flash-class models are built for sub-second agent loops, and the Multimodal Live API (`gemini-3.8-live`) targets **300ms – 600ms** end-to-end conversational voice latency.

---

### 4. Thinking / Reasoning Controls

* **Does it think by default?** **Yes.** All Gemini 2.5 and 3.x Flash models engage in **dynamic thinking by default**, automatically determining how many reasoning tokens to allocate before generating an answer or calling a tool.
* **How is it turned off or limited?**
  * **Gemini 3.x / 4.x models (`thinking_level`):** Controlled via semantic reasoning tiers (`MINIMAL`, `LOW`, `MEDIUM`, `HIGH`). Setting `thinking_level = "MINIMAL"` restricts reasoning to the absolute minimum. *(Note: Attempting to pass numeric `thinking_budget` to Gemini 3.x models triggers a `400 Bad Request`)*.
  * **Gemini 2.5 models (`thinking_budget`):** Controlled via numeric token count in `ThinkingConfig`. Setting `thinking_budget = 0` completely turns off reasoning.
* **Impact of turning off / minimizing thinking:**
  * **Latency:** TTFT drops dramatically (saving 500ms to 2000ms+), which is critical for real-time voice barge-in and conversational pacing.
  * **Tool-Call Quality:** For simple, single-turn tool calls (e.g., `lookup_weather(location="Boston")`), accuracy remains intact. However, for multi-step reasoning, ambiguous parameters, or complex nested schemas, turning off thinking degrades reliability and can increase tool argument errors. In addition, when thinking is on, Gemini produces encrypted "thought signatures" that client applications must echo back in multi-turn tool loops to prevent context corruption.

---

### 5. Function Calling Details for Streaming (OpenAI-Compatible Endpoint)

The Gemini API provides an OpenAI-compatible endpoint at `https://generativelanguage.googleapis.com/v1beta/openai/`. When streaming function calls (`stream=True`), several known bugs and limitations affect production voice pipelines:

1. **`finish_reason` Discrepancy:** The streaming endpoint frequently emits `finish_reason: "stop"` on the final chunk instead of `"tool_calls"`. Strict agent libraries (e.g., OpenAI SDK, LangChain, Vercel AI SDK) expecting `"tool_calls"` can miss the execution trigger.
2. **`tool_call_id` Handling:** Standard OpenAI schemas require the subsequent `role: "tool"` message to supply a matching `tool_call_id`. Gemini's compatibility translator does not manage native tool IDs identically to OpenAI; missing or non-matching string IDs frequently cause `400 Invalid Argument: The messages.N.tool_call_id field is required` errors.
3. **Parallel Tool Calls:** While Gemini models natively support parallel calls, the OpenAI-compatible streaming translation occasionally fails to output distinct delta `index` attributes properly, leading to merged tool argument strings across chunk boundaries.
4. **Schema Restrictions:** Gemini's schema parser is stricter than OpenAI's. Parameterless functions without an explicit empty object schema (`{"type": "object", "properties": {}}`) fail with `400 Invalid Argument`. Unsupported JSON schema keywords (certain `anyOf` constructs, untyped unions) also fail.
5. **Forced Tool Choice:** Passing strict forced tool options (e.g., `tool_choice={"type": "function", "function": {"name": "..."}}` or `"required"`) has patchy support over the OpenAI shim compared to native SDK `tool_config={"function_calling_config": {"mode": "ANY"}}`.

*Architecture Takeaway:* For a latency-critical voice agent with streaming tools, bypass the OpenAI compatibility shim and use the **native Google Gen AI SDK (`google-genai`)** or raw REST SSE (`streamGenerateContent`).

---

### 6. Context Caching and Implicit Caching

The Gemini API supports both **Implicit Caching** (automatic for repeated prompt prefixes) and **Explicit Caching** (manual cache handles created via the API).

* **Supported Models:**
  * `gemini-4-argon`: Implicit caching only.
  * `gemini-3.8-flash` / `gemini-3.6-flash`: Implicit and explicit caching.
  * `gemini-3.5-flash-lite`: Implicit caching.
  * `gemini-2.5-flash` / `gemini-2.5-pro`: Implicit and explicit caching.
* **Minimum Prefix Size:**
  * `gemini-4-argon`, `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.1-pro`: **4,096 tokens**.
  * `gemini-2.5-flash` / `gemini-2.5-pro`: **2,048 tokens** (some early 2.5 Flash docs noted 1,024 tokens).
  * Prompts under these token thresholds do not qualify for caching discounts.
* **Price Discount:**
  * Implicit caching discounts range between **75% and 95%** off standard input token rates. For example, `gemini-4-argon` receives a **95% discount** ($0.10 per 1M cached tokens vs $2.00 base); `gemini-3.8-flash` charges **$0.075 per 1M cached tokens** (a 90% discount off the $0.75 base rate).
  * Explicit caching provides comparable token discounts (~90%), but incurs an hourly storage fee (~$1.00 per 1M tokens per hour after initial TTL).

---

### 7. Deprecation and Retirement Schedules

*(Source dates: Google AI Documentation & Release Notes, 2025–2026).*

* **Google Deprecation Policy:** Preview models have at least a **2-week notice** before deprecation and endpoint shutdown. `latest` aliases provide 2 weeks notice before hot-swapping underlying versions.
* **Model ID Status:**
  * `gemini-3.8-flash`: Active GA production model. Introductory pricing ends December 31, 2026. **Retirement date: None announced.**
  * `gemini-3.6-flash`: Active stable release. **Retirement date: None announced.**
  * `gemini-3.5-flash-lite`: Active GA production model. **Retirement date: None announced.**
  * `gemini-3-flash-preview` / `gemini-3.1-flash-lite-preview`: Active preview models. Subject to minimum 2-week notice upon replacement by subsequent stable/preview drops.
  * `gemini-2.5-flash` / `gemini-2.5-flash-lite`: Marked as **Legacy / Restricted Availability** in 2026 (restricted to grandfathered accounts to conserve cluster capacity; migration to 3.x recommended). Exact shutdown dates are tracked dynamically on `ai.google.dev/gemini-api/docs/deprecations`.
  * `gemini-2.0-flash` / `gemini-2.0-flash-lite` / `gemini-1.5-flash`: **Officially shut down / retired.** Endpoints return errors or redirect.

---

### 8. Ranking: Top Three Candidates for a Latency-First Voice Agent with Tools

1. **`gemini-3.5-flash-lite`**
   * *Reason:* Delivers the lowest TTFT (~330ms–520ms P50 on OpenRouter) and lowest cost ($0.25/1M input) in the active lineup, making it the closest Google equivalent to Groq and Claude Haiku for standard speech-to-text -> LLM -> text-to-speech pipelines.
2. **`gemini-3.8-flash`** (with `thinking_level: "MINIMAL"`)
   * *Reason:* Provides the highest function-calling reliability and strict schema execution while maintaining low Flash-class latency once default dynamic thinking is suppressed to minimal.
3. **`gemini-3.8-live`** (via Multimodal Live API / WebSockets)
   * *Reason:* Offers native bidirectional audio streaming and non-blocking background tool calling (`behavior: NON_BLOCKING`), allowing the assistant to speak and handle barge-in naturally while tools execute asynchronously.

---

### Sources

* [Google AI Developer Models Documentation (`ai.google.dev/gemini-api/docs/models`)](https://ai.google.dev/gemini-api/docs/models) — *2026*
* [Google AI Pricing Documentation (`ai.google.dev/gemini-api/docs/pricing`)](https://ai.google.dev/gemini-api/docs/pricing) — *2026*
* [Google AI Rate Limits Documentation (`ai.google.dev/gemini-api/docs/rate-limits`)](https://ai.google.dev/gemini-api/docs/rate-limits) — *2026*
* [Google AI Model Deprecations Schedule (`ai.google.dev/gemini-api/docs/deprecations`)](https://ai.google.dev/gemini-api/docs/deprecations) — *2026*
* [Google AI OpenAI Compatibility Guide (`ai.google.dev/gemini-api/docs/openai`)](https://ai.google.dev/gemini-api/docs/openai) — *2025/2026*
* [Google Multimodal Live API Documentation (`ai.google.dev/gemini-api/docs/live`)](https://ai.google.dev/gemini-api/docs/live) — *2026*
* [CostGoat LLM API Pricing Index (`costgoat.com`)](https://costgoat.com) — *2026*
* [OpenRouter Model Latency & Throughput Leaderboard (`openrouter.ai`)](https://openrouter.ai) — *2026*
* [Artificial Analysis LLM Benchmarking & Latency Index (`artificialanalysis.ai`)](https://artificialanalysis.ai) — *2025/2026*

### Claims That Could Not Be Verified

* **Exact calendar shutdown date for `gemini-2.5-flash`:** The official documentation marks it as a legacy model with restricted access to past users and notes that scheduled shutdowns are published on the live deprecations table, but a specific calendar day in late 2026/2027 was not published in static summaries (*labeled as "not found"*).
* **Exact Free Tier RPM/RPD for `gemini-3.8-live`:** The Multimodal Live API over WebSockets is billed on active audio/text streams; an independent standalone free-tier daily quota (RPD) could not be verified in public tables (*labeled as "not found" / unlisted*).
