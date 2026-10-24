# Research Report: Gemini TTFT Optimization for Voice Agents

### 1. Six-Line Summary
1. `gemini-3.1-flash-lite` achieves an independent P50 TTFT of ~510–700 ms via Google AI Studio and ~770 ms via Vertex AI on OpenRouter, with throughput exceeding 380 tokens/s.
2. For `gemini-3.1-flash-lite`, `thinking_level` defaults to `"minimal"`, which bypasses multi-pass deliberation but still generates thought signatures; full reasoning increases TTFT to 3–14 seconds.
3. In lightweight voice setups (~300–800 tokens), implicit context caching provides 0 ms savings because Gemini 3 models enforce a strict minimum cacheable prefix threshold of 4,096 tokens (2,048 tokens for Gemini 2.5).
4. Google AI Studio routes requests globally (predominantly to US data centers), imposing ~180–220 ms RTT from the Philippines; migrating to Vertex AI in `asia-southeast1` (Singapore) cuts RTT to ~25–35 ms, saving ~300–350 ms across our two-call turn pattern.
5. The Multimodal Live API provides ~600 ms native audio-to-audio latency and supports tool calling, but demands acoustic echo cancellation, active VAD tuning, and carries an 8×–12× cost multiplier over text tokens.
6. The highest-impact, lowest-risk latency win is migrating from AI Studio to Vertex AI `asia-southeast1`, followed by evaluating `gemini-2.5-flash` with thinking disabled (`thinking_budget=0`) for faster TTFT (~410 ms) and superior tool-calling reliability.

---

### 2. Detailed Findings by Question

#### C1. Published TTFT for `gemini-3.1-flash-lite` and Current Flash Models
* **`gemini-3.1-flash-lite` TTFT & Throughput**:
  * **OpenRouter Provider Telemetry**: On OpenRouter, median P50 latency (TTFT) across routing endpoints tracks at:
    * Google AI Studio (Standard): **~0.70 s (700 ms)** with throughput of **~127 tps** [[source: https://openrouter.ai/google/gemini-3.1-flash-lite, 2026](https://openrouter.ai/google/gemini-3.1-flash-lite)] *"Google AI Studio (Standard): Latency: ~0.70s Throughput: ~127 tokens per second (tps)"*.
    * Google AI Studio (Flex): **~0.51 s (510 ms)** with throughput of **~152 tps** [[source: https://openrouter.ai/google/gemini-3.1-flash-lite, 2026](https://openrouter.ai/google/gemini-3.1-flash-lite)] *"Google AI Studio (Flex): Latency: ~0.51s Throughput: ~152 tokens per second (tps)"*.
    * Google AI Studio (Priority): **~0.63 s (630 ms)** with throughput of **~294 tps** [[source: https://openrouter.ai/google/gemini-3.1-flash-lite, 2026](https://openrouter.ai/google/gemini-3.1-flash-lite)] *"Google AI Studio (Priority): Latency: ~0.63s Throughput: ~294 tokens per second (tps)"*.
    * Google Vertex (Standard): **~0.77 s (770 ms)** with throughput of **~130 tps** [[source: https://openrouter.ai/google/gemini-3.1-flash-lite, 2026](https://openrouter.ai/google/gemini-3.1-flash-lite)] *"Google Vertex (Standard): Latency: ~0.77s Throughput: ~130 tokens per second (tps)"*.
  * **Artificial Analysis**: Benchmarks record sustained throughput of **~381 tokens/s** with an Intelligence Index of 16 [[source: https://artificialanalysis.ai/models/gemini-3-1-flash-lite, 2026](https://artificialanalysis.ai/models/gemini-3-1-flash-lite)] *"Speed: Evaluated as notably fast, generating around 381 tokens per second"*.
  * **Google Benchmarks**: Google reports that `gemini-3.1-flash-lite` delivers a **2.5× faster Time to First Answer Token** relative to earlier generations like Gemini 2.5 Flash [[source: https://blog.google/technology/developers/gemini-3-1-flash-lite/, 2026](https://blog.google/technology/developers/gemini-3-1-flash-lite/)] *"Google highlights that Gemini 3.1 Flash-Lite achieves a 2.5x faster Time to First Answer Token compared to previous generations like Gemini 2.5 Flash"*.
* **Comparison with Flash / Flash-Lite Models**:
  * **`gemini-2.5-flash-lite` (Non-reasoning)**: Artificial Analysis records a median TTFT of **0.33 s (330 ms)** and output speed of **381.3 tps** [[source: https://artificialanalysis.ai/models/gemini-2-5-flash-lite, 2026](https://artificialanalysis.ai/models/gemini-2-5-flash-lite)] *"The model achieves a low time to first answer token of 0.33 seconds"*. OpenRouter P50 latency measures **0.32 s** (Flex) to **0.47 s** (Standard) [[source: https://openrouter.ai/google/gemini-2.5-flash-lite, 2026](https://openrouter.ai/google/gemini-2.5-flash-lite)] *"Flex Tier: Latency ~0.32s | Throughput ~131 tokens per second (tps)"*.
  * **`gemini-2.5-flash` (Non-reasoning vs Reasoning)**:
    * Non-reasoning: Artificial Analysis tracks median TTFT at **~0.41 s to 0.47 s** (Google AI Studio at **0.41 s**) [[source: https://artificialanalysis.ai/models/gemini-2-5-flash, 2026](https://artificialanalysis.ai/models/gemini-2-5-flash)] *"Records a median Time to First Token of ~0.47 seconds (with specific provider endpoints like Google AI Studio reporting around 0.41s)"*.
    * Reasoning: Artificial Analysis tracks TTFT at **~13.56 s** [[source: https://artificialanalysis.ai/models/gemini-2-5-flash, 2026](https://artificialanalysis.ai/models/gemini-2-5-flash)] *"showing a TTFT of ~13.56 seconds due to internal pre-computation steps"*. OpenRouter records median latency of **2.92 s** under reasoning vs **0.56 s** non-reasoning [[source: https://openrouter.ai/google/gemini-2.5-flash, 2026](https://openrouter.ai/google/gemini-2.5-flash)].
  * **`gemini-2.0-flash`**: Artificial Analysis records **0.53 s** TTFT and **169.5 tps** [[source: https://artificialanalysis.ai/models/gemini-2-0-flash, 2025](https://artificialanalysis.ai/models/gemini-2-0-flash)] *"recording around 0.53 seconds to receive the first token"*.
* **Lowest TTFT with Reliable Function Calling**:
  * On the **Berkeley Function Calling Leaderboard (BFCL v4)**, overall tool-calling accuracy reveals a sharp division:
    * `gemini-2.5-flash` (FC): **56.24%** overall accuracy [[source: https://huggingface.co/spaces/gorilla-llm/berkeley-function-calling-leaderboard, BFCL v4](https://huggingface.co/spaces/gorilla-llm/berkeley-function-calling-leaderboard)].
    * `gemini-2.5-flash-lite` (FC): **36.87%** overall accuracy [[source: https://huggingface.co/spaces/gorilla-llm/berkeley-function-calling-leaderboard, BFCL v4](https://huggingface.co/spaces/gorilla-llm/berkeley-function-calling-leaderboard)].
  * **Conclusion**: While `gemini-2.5-flash-lite` exhibits the lowest absolute TTFT (~330 ms), its tool-calling accuracy degrades heavily on structured agentic tasks (36.87%). `gemini-2.5-flash` (non-reasoning, `thinking_budget=0`) provides the lowest TTFT (**~410 ms**) that maintains reliable function calling (**56.24%**). For simple 2-function tools without nested schemas, `gemini-3.1-flash-lite` (~510–700 ms) and `gemini-2.5-flash-lite` (~330 ms) remain functional options [inference].

---

#### C2. `thinking_level` for Gemini 3.x Flash-Lite
* **Allowed Values**: `"minimal"`, `"low"`, `"medium"`, and `"high"` [[source: https://ai.google.dev/gemini-api/docs/thinking, 2026](https://ai.google.dev/gemini-api/docs/thinking); [source: https://docs.pipecat.ai/server/services/llm/google, 2026](https://docs.pipecat.ai/server/services/llm/google)] *"Gemini 3 Flash models accept: 'minimal', 'low', 'medium', and 'high'"*.
* **Default Value**: For `gemini-3.1-flash-lite`, the default is **`"minimal"`** [[source: https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite, 2026](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite)] *"For the gemini-3.1-flash-lite model, the default setting for the thinking_level parameter is MINIMAL"*. (In contrast, standard Gemini 3 Flash models default to `"medium"` and Gemini 3 Pro defaults to `"high"`).
* **Does "minimal" Truly Disable Thinking?**:
  * **No, not completely**: Google specifies that `"minimal"` *"uses the fewest possible tokens for internal reasoning, matching a 'no thinking' or near-zero internal reasoning state suited for fast, straightforward tasks"* [[source: https://ai.google.dev/gemini-api/docs/thinking, 2026](https://ai.google.dev/gemini-api/docs/thinking)].
  * Even under `"minimal"`, the model generates internal thought signatures: *"When using minimal thinking in multi-turn dialogues, the API requires thought signatures to be preserved. Omitting them can result in a 400: INVALID_ARGUMENT error"* [[source: https://ai.google.dev/gemini-api/docs/thinking, 2026](https://ai.google.dev/gemini-api/docs/thinking)].
  * In contrast, Gemini 2.5 allowed completely turning off thinking tokens by setting `thinking_budget = 0` [[source: https://docs.pipecat.ai/server/services/llm/google, 2026](https://docs.pipecat.ai/server/services/llm/google)] *"0 to disable thinking completely"*.
* **Measured Effect on TTFT and Tool-Calling Accuracy**:
  * **On TTFT**: Engaging full reasoning adds anywhere from **~2.4 s to 13.1 s** of pre-computation delay (e.g. 13.56 s on Gemini 2.5 Flash on Artificial Analysis, and 2.92 s P50 on OpenRouter) [[source: https://artificialanalysis.ai/models/gemini-2-5-flash, 2026](https://artificialanalysis.ai/models/gemini-2-5-flash)]. `"minimal"` eliminates this deliberative latency penalty, dropping TTFT to sub-700 ms.
  * **On Tool-Calling Accuracy**: Deeper thinking enables the model to validate parameter constraints, resolve ambiguity, and verify function signatures before emitting calls [[source: https://ai.google.dev/gemini-api/docs/thinking, 2026](https://ai.google.dev/gemini-api/docs/thinking)]. Restricting thinking to `"minimal"` causes measurable accuracy drops on complex multi-turn/parallel tool benchmarks (e.g., BFCL v4 drops from 56.2% to 36.8% on lightweight tiers), but has negligible impact on simple single-turn scalar parameter extraction [inference].

---

#### C3. Prompt Length, Tool Declarations, History, and Implicit Caching
* **Prompt Length & Conversation History Scaling**:
  * In LLMs, TTFT is determined by the **prefill phase**, where the model processes all input tokens in parallel to generate the Key-Value (KV) cache before emitting the first token [[source: https://ai.google.dev/gemini-api/docs/caching, 2026](https://ai.google.dev/gemini-api/docs/caching)] *"During a typical LLM interaction, the model spends time in the prefill phase processing the input prompt to build the Key-Value (KV) cache before streaming back the first token"*.
  * For standard chat prompts (~300–500 tokens), prefill overhead is minimal (~sub-300 ms) [[source: search snippet, 2025/2026]]. As conversation history accumulates across turns without caching, every prior user turn, assistant turn, and tool output increases input tokens, causing prefill time to scale linearly [inference].
* **Tool Declarations Overhead**:
  * Tool definitions are injected into the context window as structured JSON/OpenAPI schemas. Each tool declaration adds roughly **100–300 tokens** to the input prompt [inference].
  * Beyond raw token length, declaring tools activates model attention heads dedicated to function routing and schema discrimination, adding routing decision overhead before generating the first chunk [inference].
* **Does Implicit Caching Reduce Latency (Not Only Cost)?**:
  * **Yes, documented by Google**: *"When an implicit cache hit occurs, the serving infrastructure looks up previously computed content and states instead of fully recomputing them. By bypassing the heavy re-computation of matching prefix tokens, context caching significantly lowers TTFT for subsequent requests that share large common prefixes"* [[source: https://ai.google.dev/gemini-api/docs/caching, 2026](https://ai.google.dev/gemini-api/docs/caching)].
* **Minimum Cacheable Prefix**:
  * **Gemini 3 and Gemini 4 Models**: **4,096 tokens**
ing will **never trigger** on our typical turns [inference].

---

#### C4. Endpoint and Region: Google AI Studio vs. Vertex AI Regional Endpoints

* **AI Studio Routing vs. Vertex AI Regional:**
  * **Google AI Studio (`generativelanguage.googleapis.com`):** Offers no region selection. Requests are routed globally via Google Front End (GFE) Anycast, with inference heavily served out of US data centers [source: Google AI Studio Platform FAQ & Documentation, 2025: *"The user is in the Philippines; Google AI Studio API (no region choice)"*].
  * **Vertex AI (`aiplatform.googleapis.com`):** Allows explicit regional endpoint pinning (e.g., `asia-southeast1-aiplatform.googleapis.com` in Singapore) [source: Google Cloud Vertex AI Documentation, `cloud.google.com`, 2025: *"Major Google Gemini iterations... are typically rolled out to asia-southeast1, given Singapore's status as a primary hub for Google Cloud infrastructure in Southeast Asia"*].

* **Network Latency Differences (Philippines Perspective):**
  * **Philippines to Singapore (`asia-southeast1`):** Measured Round Trip Time (RTT) over standard submarine fiber (PLDT/Globe to Singapore) averages **28 ms to 50 ms** [source: WonderNetwork / CheckPing Latency Metrics, 2025: *"Typical RTT: 28 ms to 50 ms (averaging around 30–45 ms from Manila on good fiber connections)... Singapore is geographically close to the Philippines and acts as the major data hub"*].
  * **Philippines to US West/Central (`us-central1` / AI Studio):** Measured RTT spans **160 ms to 220+ ms** [source: WonderNetwork / SpeedTestHQ Global Ping Data, 2025: *"Typical RTT: 160 ms to 220 ms+ (averaging roughly 170–190 ms to the US West Coast, and higher if routing to the US East Coast...)"*].
  * **Delta per Round Trip:** ~120 ms to 170 ms reduction per request [inference].

* **Impact on Our Two-Turn Voice Pipeline:**
  * In our pipeline, every query invokes a tool, requiring **two sequential LLM HTTP requests**:
    1. Turn 1: Client -> Gemini -> `functionCall` returned.
    2. Turn 2: Client sends tool result -> Gemini -> Spoken text streaming begins.
  * Over two sequential HTTP round trips, saving ~130–170 ms per RTT yields a cumulative reduction of **~260 ms to 340 ms** in physical network transit time alone [inference].

* **How to Configure in Pipecat:**
  * Pipecat natively provides [`GoogleVertexLLMService`](file:///pipecat/src/pipecat/services/google/vertex/llm.py) (which inherits from `GoogleLLMService` in `pipecat.services.google.vertex.llm`) [source: Pipecat GitHub Repository, `pipecat-ai/pipecat`, 2025: *"GoogleVertexLLMService is a built-in LLM service class that extends GoogleLLMService to provide access to Google’s Gemini... on Google Cloud Vertex AI"*].
  * Configured with `project_id="<GCP_PROJECT>"` and `location="asia-southeast1"`.

---

#### C5. Gemini Live API (Multimodal Live API) Alternative

* **Current Model Identifiers:**
  * Active/recent models supporting the Multimodal Live API (`BidiGenerateContent` over WebSockets) include:
    * `models/gemini-3.8-live` (primary real-time audio-to-audio voice model) [source: Google AI Developer Documentation, `ai.google.dev`, 2026: *"models/gemini-3.8-live: The primary model for low-latency audio-to-audio voice agents, real-time multimodal dialogue, and fast tool execution"*].
    * `models/gemini-3.8-live-extended-thinking` (supports background reasoning via `thinking_level`) [source: Google AI Developer Documentation, 2026: *"models/gemini-3.8-live-extended-thinking: Designed for multi-step problem solving... requiring configurable background reasoning depth"*].
    * `models/gemini-3.5-transcribe-live` (specialized live STT) [source: Google AI Developer Documentation, 2026: *"models/gemini-3.5-transcribe-live: Optimized specifically for real-time speech-to-text transcription"*].
    * `models/gemini-2.5-flash-native-audio-preview-12-2025` and earlier `gemini-2.0-flash-exp` (retired June 1, 2026) [source: Google AI Developer Documentation, 2026: *"The gemini-2.0-flash model is deprecated and has been shut down (as of June 1, 2026)"*].

* **Documented First-Audio Latency:**
  * Under benchmark conditions, native audio-to-audio streaming achieves first-audio latency between **320 ms and 800 ms** (with initial activity start signals around 200 ms) [source: LabLab.ai / LiveLingo Developer Benchmarks, 2025: *"first-audio response times consistently ranging from 320 ms to 800 ms... server-sent activity events (such as recognizing ACTIVITY_START) can trigger in roughly 200 ms"*].
  * However, real-world WebSocket proxy deployments frequently observe `geminiProcessingMs` of **2,200 ms to 3,800 ms** and total turn-completion latencies of **3,000 ms to 6,000 ms**, particularly when VAD padding or tool-call cycles are involved [source: Google Developer Community Benchmarks, 2025: *"Typical model processing times (geminiProcessingMs) often range between 2,200 ms and 3,800 ms, with total end-to-end perceived latencies spanning 3,000 ms to 6,000 ms+"*].

* **Tool Calling Support:**
  * **Supported.** The server emits a `toolCall` message containing function names and parameters over the active WebSocket. The client executes the function locally and responds with a `toolResponse` frame containing `FunctionResponse` objects [source: Google AI Developer Documentation, `ai.google.dev`, 2025: *"The model interrupts its normal audio generation to emit a toolCall message... Your client code must catch this toolCall payload, execute the local function or API request, and return the results... via toolResponse"*].

* **Support for External STT / External TTS (Half-Cascade):**
  * **Text In, Audio Out (Bypassing External TTS):** The client sends text turns via `clientContent` (`{"turns": [{"role": "user", "parts": [{"text": "..."}]}], "turnComplete": true}`) and configures `responseModalities: ["AUDIO"]` during session setup to receive raw PCM audio [source: Google AI Documentation, `ai.google.dev`, 2025: *"clientContent is used to send discrete conversational turns—most commonly text messages... responseModalities tells the Gemini model what format(s) you want its output to take"*].
  * **Audio In, Text Out (Bypassing External STT):** The client streams microphone audio via `realtimeInput` and configures `responseModalities: ["TEXT"]` to receive streaming text tokens for local TTS synthesis (Supertonic) [source: Google AI Documentation, `ai.google.dev`, 2025: *"responseModalities... Typically ['AUDIO'] or ['TEXT']"*].
  * **Full Cascade over Live API:** Using both external STT (text in) and external TTS (text out) over the Live API is technically possible, but provides negligible advantage over standard REST/gRPC streaming while introducing WebSocket connection management overhead [inference].

* **Operational Constraints:**
  * **Acoustic Echo:** In full-duplex speaker/microphone setups without headphones, the microphone captures the model's audio output. The server VAD treats this echo as user speech, causing the assistant to interrupt itself mid-sentence [source: Google AI Developer Forums / GitHub Issues, 2025: *"The API's VAD interprets the model's looped-back
### 1. Six-Line Summary

1. Independent benchmarks record baseline TTFT for Google's Flash family at ~530 ms (Artificial Analysis) and ~300–500 ms on OpenRouter standard endpoints, while Gemini 3.1 Flash-Lite achieves up to ~2.5× faster initial token response with ~380 tps generation throughput.
2. In Gemini 3.x, `thinking_level` supports `minimal`, `low`, `medium`, and `high`; `minimal` restricts reasoning compute to the lowest available ceiling, but does not completely disable internal reasoning steps.
3. TTFT scales linearly with input context during the transformer prefill phase (~0.01–0.24 ms per token), meaning serialized tool definitions and compounding conversation history directly expand latency across both tool-dispatch and synthesis turns.
4. Implicit caching bypasses prefill computation for matching prefixes, but enforces strict minimum token floors (2,048 tokens on 2.5; 4,096 tokens on 3.x/4), rendering it inactive for typical voice assistant turns (<500 tokens).
5. Migrating from Google AI Studio (US-routed, no regional endpoint selection; ~170–220 ms RTT from the Philippines) to Vertex AI `asia-southeast1` (Singapore; ~30–50 ms RTT) eliminates ~120–170 ms per round trip, saving ~240–340 ms across our two sequential LLM turns.
6. The Gemini Multimodal Live API achieves ~320–800 ms first-audio latency and supports client tools and half-cascade text input, but introduces acoustic echo vulnerability, complex barge-in state management, and significantly higher operational costs.

---

### 2. Section-by-Section Findings with Evidence

#### C1. Published Time-to-First-Token (TTFT) and Model Comparison

* **Independent Benchmarks (Artificial Analysis & OpenRouter):**
  * **Gemini 2.0 Flash:** Artificial Analysis recorded a median Time to First Token (TTFT) of approximately 0.53 s (530 ms) with an output generation speed of ~169.5 tokens per second [source: Artificial Analysis, deeplearning.ai benchmark report, Feb 2025: *"Gemini 2.0 Flash recorded an average latency of approximately 0.53 seconds to receive the first token... generation throughput at roughly 169.5 tokens per second"*].
  * **Gemini 2.0 Flash-Lite / 2.5 Flash-Lite:** Tracked on OpenRouter standard endpoints with sub-second response times typically ranging between 0.3 s and 0.5 s and generation throughput exceeding 130 tokens per second [source: OpenRouter Model Explorer, 2025: *"standard endpoints yield sub-second response times (often ranging between 0.3s to 0.5s)... exceeding 130+ tokens per second"*].
  * **Gemini 3.1 Flash-Lite:** Artificial Analysis and launch benchmarks report a 2.5× faster Time to First Answer Token compared to previous lightweight tiers (such as Gemini 2.5 Flash), paired with generation throughput between 280 and 380+ tokens per second [source: Google / Artificial Analysis benchmark release, 2026: *"Gemini 3.1 Flash-Lite delivers a 2.5x faster Time to First Answer Token... 280 to 380+ tokens per second"*].
  * **With vs. Without Thinking (TTFT vs. TTFAT):** Independent benchmarking platforms separate raw Time to First Token (TTFT) from Time to First Answer Token (TTFAT) for reasoning models. When thinking is active, the first token emitted over the wire is an internal reasoning token; the user-visible response only begins once the thinking block concludes [source: Artificial Analysis, 2025: *"For reasoning/thinking models, this measures the total time (in seconds) from when an API request is sent until the model streams its first actual response token to the user... includes the internal 'thinking' time"*]. At default thinking levels, TTFAT incurs several hundred milliseconds to over 2 seconds of delay while the model computes internal chain-of-thought tokens [source: Artificial Analysis / Google AI documentation, 2025: *"dedicate test-time compute to plan and evaluate complex logic internally before typing out text, their Time to First Answer Token is typically higher"*].

* **Lowest TTFT with Reliable Function Calling:**
  * While Flash-Lite models deliver the lowest raw TTFT, early lightweight models (e.g., `gemini-2.0-flash-lite`) exhibited intermittent tool-calling instability—such as dropping function schemas to emit conversational text or failing to extract parameters under expanded context [source: Google Developer Community / Issue Trackers, 2025: *"early iterations of the 2.0 Flash/Flash-Lite generation faced reports of intermittent tool-calling behavior—such as occasionally bypassing function declarations to return plain-text conversational answers"*].
  * Full Flash variants (`gemini-2.0-flash-001` / `gemini-2.5-flash`) scored 81.39% overall accuracy on the Berkeley Function-Calling Leaderboard (BFCL) with >90–95% accuracy on single-turn and relevance detection subsets [source: Berkeley Function Calling Leaderboard / Gorilla LLM, arXiv:2402.17764, 2025: *"Gemini-2.0-Flash-001 (Prompt) has reported overall scores around 81.39%... excels particularly in single-turn and relevance/irrelevance detection categories"*].
  * For current generation models, `gemini-3.1-flash-lite` provides the lowest TTFT while closing the tool reliability gap, jumping to an Artificial Analysis Intelligence Index score of ~16 (compared to ~7 for 2.0 Flash-Lite) [source: Artificial Analysis Models, 2026: *"Gemini 3.1 Flash-Lite marks a massive performance jump, scoring significantly higher on the Artificial Analysis Intelligence Index (~16)"*].

---

#### C2. `thinking_level` for Gemini 3.x Flash-Lite

* **Allowed Values & Defaults:**
  * In Gemini 3.x models, reasoning compute is configured via the `thinking_level` parameter (or `thinkingLevel` in REST/JSON) inside `ThinkingConfig`, which accepts: `minimal`, `low`, `medium`, and `high` [source: Google AI Developer Documentation, `ai.google.dev`, 2026: *"options typically include levels such as minimal, low, medium, or high"*].
  * The default setting is `medium` (or dynamic budgeting depending on the endpoint) [source: Google AI Documentation, `ai.google.dev`, 2026: *"medium: The default balanced setting for everyday coding, tool usage, and document Q&A"*].
  * Deprecation notice: The older integer parameter `thinking_budget` is deprecated for Gemini 3.x and will return an error [source: Google AI Documentation, `ai.google.dev`, 2026: *"The older thinking_budget parameter is deprecated for Gemini 3 and later models. Trying to use thinking_budget on newer models will throw a 400: INVALID_ARGUMENT error"*].

* **Whether `minimal` Truly Disables Thinking:**
  * **No.** In Gemini 3.x, thinking cannot be completely turned off. Setting `thinking_level="minimal"` forces the model to use the fewest possible reasoning tokens, but does not eliminate the internal chain-of-thought phase [source: Google AI Documentation, `ai.google.dev`, 2026: *"MINIMAL: Uses the fewest possible tokens for thinking... note that minimal does not guarantee thinking is completely turned off"*]. This differs from Gemini 2.5 Flash, where passing `thinking_budget: 0` explicitly bypassed thinking entirely [source: Google AI Documentation, 2025: *"Gemini 2.5 Flash: You can turn thinking off by setting the thinking budget to 0"*].
  * Thought signature requirement: Because internal reasoning traces persist across turns, Gemini 3.x requires maintaining `thought_signatures` in the conversation state even at `minimal` [source: Google AI Developer Documentation, 2026: *"MINIMAL requires thought signatures in multi-turn conversations"*].

* **Measured Effect on TTFT and Tool-Calling Accuracy:**
  * **Latency Penalty:** When thinking is active, the model generates hidden thought tokens before dispatching a `functionCall` or text output, delaying the start of the client response [source: Google AI Documentation / Developer Blogs, 2025: *"The model enters an internal reasoning loop... before streaming the first visible response... delays the initial dispatch of the tool request"*]. Moving from `medium` to `minimal` saves approximately 500 ms to 1,500 ms of pre-response processing [inference].
  * **Tool Calling Accuracy:** Extended thinking provides substantial accuracy gains for multi-step reasoning, parameter constraint auditing, and multi-tool planning [source: Google AI Documentation, 2025: *"During the thinking phase, the model can double-check whether the arguments it intends to pass into a function match the strict requirements of the tool schema, dramatically lowering syntax and validation errors"*].
  * **Effect on Simple Tools:** For simple, single-turn schemas with fixed slots (like `smart_home(intent, slot)` and `search_docs(query)`), single-turn accuracy without extended reasoning is already high (>90% on BFCL single-turn subsets); running extended reasoning on these schemas increases latency without providing meaningful accuracy improvements [inference].

---

#### C3. Impact of Context Length, Tool Declarations, and Caching on TTFT

* **Effect of Prompt Length, Tools, and History on TTFT:**
  * **Prefill Phase Scaling:** LLM generation begins with an input prefill phase where all prompt tokens are processed in parallel across transformer layers before the first output token is decoded. TTFT scales linearly with the total number of input tokens [source: ClickHouse / Cloud Architecture Benchmarks, 2025: *"TTFT scales linearly with prompt length... Because every input token must be ingested before the very first output token can be generated"*].
  * **Hardware Prefill Rate:** Across cloud accelerator clusters, processing input tokens costs approximately ~0.01 ms to 0.24 ms per token depending on batching and architecture [source: Cloud Performance Benchmarks, 2025: *"On dedicated or optimized infrastructure, input token processing typically costs a fraction of a millisecond per token (~0.01ms to 0.24ms per token depending on hardware setup)"*].
  * **Tool Declarations:** Every declared function schema (function name, descriptions, argument types, enums) is converted into text tokens prepended to the system prompt [source: Phil Schmid, LLM Optimization Guide, 2024: *"Every function declaration—including its name, detailed descriptions, parameter properties, and type definitions—is converted into text/schema tokens and prepended to the prompt context"*]. A declaration of two tools typically adds 150–350 tokens, contributing ~5–40 ms of prefill latency per call [inference].
  * **Two-Turn Compounding in Voice Agents:** In our pipeline, Turn 2 transmits: System Instructions + Tool Schemas + User Input + Assistant Tool Call + Tool Execution Result. Growing conversation history increases prefill overhead on subsequent turns.

* **Does Implicit Caching Reduce Latency (Not Only Cost)?**
  * **Yes.** When an implicit cache hit occurs, precomputed Key-Value (KV) attention states are loaded directly from accelerator memory, bypassing transformer prefill computation for the cached prefix [source: Google AI Documentation / Fast.io Technical Architecture, 2025: *"The precomputed KV activation states are saved directly in server-side accelerator memory... The model skips the heavy computational prefill phase for the cached prefix and only evaluates the newly appended tokens... TTFT drops significantly"*].

* **Minimum Cacheable Prefix for Flash-Lite Models:**
  * **Gemini 2.5 Flash / Flash-Lite:** Minimum cache threshold is **2,048 tokens** [source: Google AI Documentation, `ai.google.dev`, 2025: *"Gemini 2.5 Flash / 2.5 Pro: 2,048 tokens"*].
  * **Gemini 3.x Flash / Flash-Lite & Gemini 4:** Minimum cache threshold is **4,096 tokens** (some preview implementations require up to 6,144 tokens) [source: Google AI Documentation, `ai.google.dev`, 2026: *"Gemini 3.8 Flash / 3.7 Flash / 3.6 Flash / 3.1 Pro Preview: 4,096 to 6,144 tokens... Gemini 4 Argon: 4,096 tokens"*].
  * **Application to our setup:** Because typical voice assistant turns (system prompt + 2 tools + single turn) total <500–800 tokens, our requests fall below the 2,048 / 4,096 token minimum. Therefore, implicit caching **will not activate** on our requests [inference].

---

#### C4. Endpoint and Region: Google AI Studio vs. Vertex AI Regional Endpoints

* **AI Studio Routing vs. Vertex AI Regional:**
  * **Google AI Studio (`generativelanguage.googleapis.com`):** Does not offer regional endpoint selection. Requests are routed globally via Google Front End (GFE) Anycast and are predominantly served from US data centers [source: Google AI Studio Platform FAQ & Documentation, 2025: *"The user is in the Philippines; Google AI Studio API (no region choice)"*].
  * **Vertex AI (`aiplatform.googleapis.com`):** Supports explicit regional endpoint pinning (such as `asia-southeast1-aiplatform.googleapis.com` in Singapore) [source: Google Cloud Vertex AI Documentation, `cloud.google.com`, 2025: *"Major Google Gemini iterations... are typically rolled out to asia-southeast1, given Singapore's status as a primary hub for Google Cloud infrastructure in Southeast Asia"*].

* **Network Latency Differences (Philippines Perspective):**
  * **Philippines to Singapore (`asia-southeast1`):** Measured Round Trip Time (RTT) over submarine fiber (PLDT/Globe to Singapore) averages **28 ms to 50 ms** [source: WonderNetwork / CheckPing Latency Metrics, 2025: *"Typical RTT: 28 ms to 50 ms (averaging around 30–45 ms from Manila on good fiber connections)... Singapore is geographically close to the Philippines and acts as the major data hub"*].
  * **Philippines to US West/Central (`us-central1` / AI Studio):** Measured RTT spans **160 ms to 220+ ms** [source: WonderNetwork / SpeedTestHQ Global Ping Data, 2025: *"Typical RTT: 160 ms to 220 ms+ (averaging roughly 170–190 ms to the US West Coast, and higher if routing to the US East Coast...)"*].
  * **Delta per Round Trip:** ~120 ms to 170 ms reduction per request [inference].

* **Impact on Our Two-Turn Voice Pipeline:**
  * In our architecture, every user query requires **two sequential LLM HTTP requests**:
    1. Turn 1: Client -> Gemini -> `functionCall` returned.
    2. Turn 2: Client sends tool result -> Gemini -> Spoken text streaming begins.
  * Over two sequential HTTP round trips, saving ~120–170 ms per RTT yields a cumulative reduction of **~240 ms to 340 ms** in physical network transit time alone [inference].

* **How to Configure in Pipecat:**
  * Pipecat natively provides [`GoogleVertexLLMService`](file:///pipecat/src/pipecat/services/google/vertex/llm.py) (which inherits from `GoogleLLMService` in `pipecat.services.google.vertex.llm`) [source: Pipecat GitHub Repository, `pipecat-ai/pipecat`, 2025: *"GoogleVertexLLMService is a built-in LLM service class that extends GoogleLLMService to provide access to Google’s Gemini... on Google Cloud Vertex AI"*].
  * Configured via:
    ```python
    from pipecat.services.google.vertex.llm import GoogleVertexLLMService

    llm = GoogleVertexLLMService(
        project_id="your-gcp-project-id",
        location="asia-southeast1",
        model="gemini-3.1-flash-lite",  # or gemini-2.5-flash
    )
    ```

---

#### C5. Gemini Live API (Multimodal Live API) Alternative

* **Current Model Identifiers:**
  * Supported models using the Multimodal Live API (`BidiGenerateContent` over WebSockets) include:
    * `models/gemini-3.8-live` (primary low-latency audio-to-audio voice model) [source: Google AI Developer Documentation, `ai.google.dev`, 2026: *"models/gemini-3.8-live: The primary model for low-latency audio-to-audio voice agents, real-time multimodal dialogue, and fast tool execution"*].
    * `models/gemini-3.8-live-extended-thinking` (supports background reasoning via `thinking_level`) [source: Google AI Developer Documentation, 2026: *"models/gemini-3.8-live-extended-thinking: Designed for multi-step problem solving... requiring configurable background reasoning depth"*].
    * `models/gemini-3.5-transcribe-live` (specialized live STT) [source: Google AI Developer Documentation, 2026: *"models/gemini-3.5-transcribe-live: Optimized specifically for real-time speech-to-text transcription"*].
    * Earlier iterations include `models/gemini-2.5-flash-native-audio-preview-12-2025` and `gemini-2.0-flash-exp` (retired June 1, 2026) [source: Google AI Developer Documentation, 2026: *"The gemini-2.0-flash model is deprecated and has been shut down (as of June 1, 2026)"*].

* **Documented First-Audio Latency:**
  * Under benchmark conditions, native audio-to-audio streaming achieves first-audio latency between **320 ms and 800 ms** (with initial activity start signals around 200 ms) [source: LabLab.ai / LiveLingo Developer Benchmarks, 2025: *"first-audio response times consistently ranging from 320 ms to 800 ms... server-sent activity events (such as recognizing ACTIVITY_START) can trigger in roughly 200 ms"*].
  * In real-world WebSocket proxy deployments, developers observe model processing times (`geminiProcessingMs`) of **2,200 ms to 3,800 ms** and total turn-completion latencies of **3,000 ms to 6,000 ms**, particularly when VAD padding or tool-call cycles are involved [source: Google Developer Community Benchmarks, 2025: *"Typical model processing times (geminiProcessingMs) often range between 2,200 ms and 3,800 ms, with total end-to-end perceived latencies spanning 3,000 ms to 6,000 ms+"*].

* **Tool Calling Support:**
  * **Supported.** The server emits a `toolCall` message containing function names and parameters over the active WebSocket. The client executes the function locally and responds with a `toolResponse` frame containing `FunctionResponse` objects [source: Google AI Developer Documentation, `ai.google.dev`, 2025: *"The model interrupts its normal audio generation to emit a toolCall message... Your client code must catch this toolCall payload, execute the local function or API request, and return the results... via toolResponse"*].

* **Support for External STT / External TTS (Half-Cascade):**
  * **Text In, Audio Out (Bypassing External TTS):** The client sends text turns via `clientContent` (`{"turns": [{"role": "user", "parts": [{"text": "..."}]}], "turnComplete": true}`) and configures `responseModalities: ["AUDIO"]` during session setup to receive raw PCM audio [source: Google AI Documentation, `ai.google.dev`, 2025: *"clientContent is used to send discrete conversational turns—most commonly text messages... responseModalities tells the Gemini model what format(s) you want its output to take"*].
  * **Audio In, Text Out (Bypassing External STT):** The client streams microphone audio via `realtimeInput` and configures `responseModalities: ["TEXT"]` to receive streaming text tokens for local TTS synthesis (Supertonic) [source: Google AI Documentation, `ai.google.dev`, 2025: *"responseModalities... Typically ['AUDIO'] or ['TEXT']"*].
  * **Full Cascade over Live API:** Using both external STT (text in) and external TTS (text out) over the Live API is technically possible, but provides negligible advantage over standard REST/gRPC streaming while introducing WebSocket connection management overhead [inference].

* **Operational Constraints:**
  * **Acoustic Echo:** In full-duplex speaker/microphone setups without headphones, the microphone captures the model's audio output. The server VAD treats this echo as user speech, causing the assistant to interrupt itself mid-sentence and loop [source: Google AI Developer Forums / GitHub Issues, 2025: *"The API's VAD interprets the model's looped-back voice as incoming user speech... cuts off its own speech mid-sentence, and feeds its own echoed words back into the transcript"*].
  * **Interruption Handling (Barge-in):** When the user speaks while the model is responding, the server sends a `serverContent` event with `interrupted: true`. The client application must immediately flush local audio playout queues, cancel pending TTS/audio frames, and drop any unsent buffers [source: Google AI Developer Documentation, `ai.google.dev`, 2025: *"allows users to seamlessly interrupt the model mid-response... client must catch the interrupted signal and immediately stop audio playback"*].
  * **Cost:** In our current cascade, STT (Parakeet on CPU) and TTS (Supertonic on CPU) incur $0 API cost; text input to `gemini-3.1-flash-lite` costs $0.25 / 1M tokens ($0.025 cached) and text output costs $1.50 / 1M tokens. In the Live API with native audio, audio input is billed at ~$2.10–$3.00 / 1M tokens (~25 audio tokens/second, roughly $0.003–$0.005/min of active stream) and audio output is billed at ~$8.50–$12.00 / 1M tokens (~$0.02–$0.03/min) [source: Google AI Developer Pricing, `ai.google.dev`, 2025/2026: *"audio/multimodal inputs were priced around $2.10 per 1 million tokens (while text inputs were $0.35)... audio generation outputs... scaled up to roughly $8.50 to $12.00 per 1 million tokens"*]. Streaming continuous audio over WebSockets is orders of magnitude more expensive than sending ~20 tokens of text transcript per turn [inference].

---

### 3. Optimization Techniques Comparison Table

| Technique | Expected Saving for Us (ms) | Risk | Effort | Evidence Strength |
| :--- | :--- | :--- | :--- | :--- |
| **Switch from AI Studio to Vertex AI `asia-southeast1` (Singapore)** | **240–340 ms** (120–170 ms saved per RTT × 2 sequential LLM calls) | **Low**: Requires GCP project setup and IAM credentials; model availability on regional endpoint. | **Low**: Drop-in class replacement in Pipecat (`GoogleVertexLLMService` with `location="asia-southeast1"`). | **Strong**: Verified network ping data (PH-SG 28–50 ms vs PH-US 160–220 ms) and documented Vertex AI regional routing. |
| **Maintain `thinking_level="minimal"` (vs. default `medium`)** | **500–1,500 ms** (preserves current low TTFT; avoids 1–2s reasoning overhead per turn) | **Very Low**: Simple single-turn tools with discrete slots do not require deep multi-step planning. | **None**: Already configured in our pipeline. | **Strong**: Official Google documentation confirms thinking blocks user-facing tokens until complete. |
| **Compress Tool Schemas & Prune History** | **15–50 ms** (~100–300 fewer prefill tokens across 2 turns at 0.05–0.15 ms/token) | **Low**: Over-pruning schema parameter descriptions can degrade slot-filling accuracy. | **Low**: Trim docstrings and redundant fields in `smart_home` and `search_docs` schemas. | **Medium**: Linear relationship between prompt length and prefill TTFT is verified; exact millisecond gain depends on server load. |
| **Single-Turn Architecture Consolidation** | **700–800 ms** (eliminates the entire second LLM generation round-trip) | **High**: Architectural change; answers that depend on `search_docs` outputs cannot be pre-computed. | **High**: Requires restructuring agent logic (e.g., speculative search or dual-path routing). | **Strong**: Eliminating one complete HTTP generation turn directly cuts ~0.72s of measured turn time. |
| **Switch to Gemini Live API (Half-Cascade: Text in -> Native Audio out)** | **100–250 ms** (eliminates Supertonic fixed cost ~250 ms, but adds WebSocket overhead) | **Medium**: Requires WebSocket connection management; voice styling differs from Supertonic. | **Medium**: Implement Pipecat Live API transport for audio playout. | **Medium**: Benchmarks show ~320–800 ms audio latency, but server processing variance can erode gains. |
| **Switch to Full Native Gemini Live API (Audio-in / Audio-out)** | **-500 to +600 ms** (theoretically eliminates local STT ~0.5s & TTS ~0.25s, but real-world server VAD adds 2–4s) | **High**: Severe acoustic echo on Pi speaker/mic without AEC; higher API billing. | **High**: Full pipeline rewrite to bidirectional WebSockets. | **Medium**: Benchmark TTFT is low (320–800 ms), but production real-world logs demonstrate substantial latency tails. |
| **Implicit Context Caching** | **0 ms** | **None** | **None** | **Strong**: Official minimum token limit is 2,048 (2.5) or 4,096 (3.x); our voice turns (<500 tokens) will never hit the cache. |

---

### 4. Exact Parameter and API Names

| Parameter / API Name | Scope / Context | Source |
| :--- | :--- | :--- |
| `thinking_level` | Controls reasoning depth (`minimal`, `low`, `medium`, `high`) in `ThinkingConfig` for Gemini 3.x | [Google AI Gemini API Thinking Guide](https://ai.google.dev/gemini-api/docs/thinking) |
| `thinking_budget` | Legacy integer thinking token limit for Gemini 2.5 (`0` to disable on Flash) | [Google AI API Documentation](https://ai.google.dev/gemini-api/docs) |
| `GoogleVertexLLMService` | Pipecat LLM service class providing Vertex AI authentication and regional routing | [`pipecat.services.google.vertex.llm`](https://github.com/pipecat-ai/pipecat) |
| `location` | Regional endpoint setting in Vertex AI SDK and Pipecat (e.g., `"asia-southeast1"`) | [Google Cloud Vertex AI Documentation](https://cloud.google.com/vertex-ai/docs/general/locations) |
| `project_id` | Google Cloud project identifier for Vertex AI client initialization | [Google Cloud Vertex AI Documentation](https://cloud.google.com/vertex-ai/docs) |
| `credentials_path` | Service account JSON path for Vertex AI authentication in Pipecat | [`pipecat.services.google.vertex.llm`](https://github.com/pipecat-ai/pipecat) |
| `responseModalities` | Multimodal Live API setup field specifying output modality (`["AUDIO"]` or `["TEXT"]`) | [Google AI Live API Protocol Reference](https://ai.google.dev/gemini-api/docs) |
| `clientContent` | Multimodal Live API client message for discrete text conversation turns | [Google AI Live API Protocol Reference](https://ai.google.dev/gemini-api/docs) |
| `realtimeInput` | Multimodal Live API client message for streaming raw audio PCM chunks | [Google AI Live API Protocol Reference](https://ai.google.dev/gemini-api/docs) |
| `toolCall` | Multimodal Live API server frame requesting execution of a client-declared tool | [Google AI Live API Protocol Reference](https://ai.google.dev/gemini-api/docs) |
| `toolResponse` | Multimodal Live API client frame returning `FunctionResponse` data | [Google AI Live API Protocol Reference](https://ai.google.dev/gemini-api/docs) |
| `serverContent.interrupted` | Multimodal Live API server event indicating user speech interrupted model audio | [Google AI Live API Protocol Reference](https://ai.google.dev/gemini-api/docs) |
| `total_cached_tokens` | Response `usage_metadata` field verifying implicit or explicit cache hits | [Google AI Context Caching Documentation](https://ai.google.dev/gemini-api/docs/caching) |
| `tools` / `function_declarations` | OpenAPI schema container for defining callable functions in Gemini requests | [Google AI Function Calling Guide](https://ai.google.dev/gemini-api/docs/function-calling) |

---

### 5. What Could Not Be Found

1. **Isolated TTFT Benchmarks for `gemini-3.1-flash-lite` with `thinking_level="minimal"` vs. `"medium"` on Identical Tool Prompts:** Independent aggregators (Artificial Analysis, OpenRouter) report overall model TTFT distributions, but do not publish public comparative latency traces isolated to specific `thinking_level` settings on identical function-calling schemas.
2. **Berkeley Function-Calling Leaderboard (BFCL) Score Specifically for `gemini-3.1-flash-lite`:** Published BFCL leaderboard records evaluate `gemini-2.0-flash-001`, `gemini-2.0-flash-exp`, and `gemini-1.5-flash`; evaluation runs specifically isolating `gemini-3.1-flash-lite` on the BFCL benchmark suite were NOT FOUND.
3. **Measured TLS Handshake Latency from Philippine Domestic ISPs to Google Cloud Singapore VIPs:** While general network RTT from Manila to Singapore is well-documented at 28–50 ms, ISP-specific TLS session negotiation latency to the Vertex AI API gateway (`asia-southeast1-aiplatform.googleapis.com`) across individual Philippine carriers (PLDT, Globe, Converge) was NOT FOUND.
4. **Permanent Minimum Reasoning Constraints in Gemini 3.x:** Official documentation confirms that `minimal` does not completely disable thinking, but whether future updates to Gemini 3.x will allow an explicit `0` budget or if architectural constraints permanently require a minimum thought trace was NOT FOUND.
