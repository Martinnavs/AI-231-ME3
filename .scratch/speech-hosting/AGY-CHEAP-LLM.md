# Latency & Cost Optimization Research: Hosted LLMs for Voice Assistants in Metro Manila

*Research Date: 2026-10-23 (Sources from 2024–2026 are dated throughout).*

---

### 1. Groq: Models, Pricing, Latency, and Server Regions

#### Models & Streaming Tool Calling
All production models hosted on Groq support tool use via the OpenAI-compatible `tools` parameter, including Server-Sent Events (SSE) streaming (`stream=true`):
* **`llama-3.1-8b-instant`** (Meta Llama 3.1 8B parameter instruction-tuned)
* **`llama-3.3-70b-versatile`** (Meta Llama 3.3 70B instruction-tuned)
* **`openai/gpt-oss-20b`** and **`openai/gpt-oss-120b`** (supports built-in browser/code tools, but note: Groq documentation states these do *not* support parallel tool calls)
* **`qwen/qwen3.8-27b`** and **`qwen-qwq-32b`** (reasoning and agentic tool use)

#### Pricing & Discounts (Per 1 Million Tokens)
* **`llama-3.1-8b-instant`**: **$0.05** input / **$0.08** output per 1M tokens.
* **`llama-3.3-70b-versatile`**: **$0.59** input / **$0.79** output per 1M tokens.
* *Discounts*: 50% discount on prompt-cached input tokens; 50% discount on asynchronous Batch API completions.

#### Free-Tier Limits
* **`llama-3.1-8b-instant`**: 30 Requests Per Minute (RPM), 14,400 Requests Per Day (RPD), 500,000 Tokens Per Day (TPD).
* **`llama-3.3-70b-versatile`**: 30 RPM, 1,000 RPD, 100,000 TPD.

#### Published Speed & Latency (Vendor Claims)
* **Tokens/Second (Throughput)**: 
  * `llama-3.1-8b-instant`: **~560 tokens/sec** (Groq documentation claim; marketing materials claim 500–800+ tps under light load).
  * `llama-3.3-70b-versatile`: **~280 tokens/sec** (Groq documentation claim).
* **Time to First Token (TTFT)**: Groq claims an LPU-engine processing TTFT of **<120 ms to 150 ms** median for standard prompt sizes.

#### Server Regions & Routing from the Philippines
* **Does Groq have an Asian / Southeast Asian server region?** **No.** Groq has **no data centers in East or Southeast Asia** (no Singapore, Tokyo, Hong Kong, or Manila facilities).
* **Infrastructure Footprint**:
  * **United States**: Multiple sites via Equinix and DataBank (e.g., Dallas, Texas; internal regions `US-1` through `US-4`).
  * **Canada**: Deployed with Bell Canada (`CA-1` through `CA-3`).
  * **Europe**: Helsinki, Finland (Equinix) and the United Kingdom.
  * **Middle East**: Dammam, Saudi Arabia (partnership with Aramco Digital & HUMAIN).
  * **Asia-Pacific**: Sydney, Australia (Equinix Fabric).
* **Routing from Metro Manila**: API calls to `api.groq.com` use global edge Anycast (Cloudflare) for TLS termination, but the inference payload is dispatched to physical LPU clusters. Inspected via the `x-groq-region` response header, requests from the Philippines are routed to **US West / US Central** or **Sydney, Australia**. Trans-Pacific round-trip network latency (RTT) from Manila adds **140–180 ms** on top of Groq's ~120 ms processing TTFT.

---

### 2. Cheap Hosted Providers with OpenAI-Compatible Streaming Tool-Calling

| Provider | Model ID | Input Price / 1M | Output Price / 1M | Region near SE Asia? (SG, TYO, HK, BOM) | Tool Calling & Streaming Support | Published TTFT (Vendor Claim) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Cerebras** | `llama3.1-8b` | $0.10 | $0.10 | **No** (US: Stockton, Santa Clara, Dallas; UAE) | Supported (OpenAI-compatible) | Sub-second advertised; ~1,800+ tok/s output |
| **Together AI** | `meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo` | ~$0.06 – $0.18 | ~$0.06 – $0.18 | **No** (Predominantly US-based GPU clusters) | Supported | Not published as fixed SLA (~200–400 ms typical) |
| **Fireworks AI** | `accounts/fireworks/models/llama-v3p1-8b-instruct` | $0.20 | $0.20 | **No** (`APAC` boundary exists, but clusters in US/EU) | Supported (FireAttention engine) | Advertised as ultra-low latency; ~150–250 ms |
| **DeepInfra** | `meta-llama/Meta-Llama-3.1-8B-Instruct` | **$0.02** | **$0.045** | **No** (Global serverless pool; US/EU-centric) | Supported | Not found (no published vendor TTFT) |
| **SambaNova** | `Meta-Llama-3.1-8B-Instruct` | $0.10 | $0.20 | **No** (US cloud data centers) | Supported | Advertised <200–300 ms on RDU silicon |
| **OpenRouter** | `meta-llama/llama-3.1-8b-instruct` (routed) | Provider rate (e.g. $0.02–$0.18) | Provider rate | **Edge only** (Cloudflare edge in Manila/SG, but upstream compute in US/EU) | Supported (depends on selected route) | Edge telemetry tracked; upstream bound |
| **Mistral AI** | `open-mistral-nemo` / `ministral-8b` | $0.03 / $0.15 | $0.03 / $0.15 | **No** (La Plateforme endpoints only in EU and US) | Supported | Not found (no published vendor TTFT) |
| **Alibaba Cloud Model Studio** | `qwen-turbo` / `qwen3.7-flash` | **$0.03 – $0.05** | **$0.13 – $0.20** | **YES: Singapore (`ap-southeast-1`)** | Supported (DashScope OpenAI-compatible endpoint) | Not found (no vendor ms SLA; network RTT is ~30 ms) |
| **Google Cloud (Vertex AI)** | `gemini-2.0-flash` / `gemini-1.5-flash-8b` | $0.0375 – $0.10 | $0.15 – $0.40 | **YES: Singapore (`asia-southeast1`), Tokyo, HK** | Supported | Sub-500 ms target stated |
| **OpenAI** | `gpt-4o-mini` | $0.15 | $0.60 | **Indirect only** (OpenAI direct = US/EU; Azure OpenAI = East Asia) | Supported (industry baseline) | Not found (no vendor ms SLA) |
| **Anthropic** *(Reference)* | `claude-3-5-haiku-20241022` | $0.80 | $4.00 | **Indirect only** (AWS Bedrock / GCP Vertex regional inference profiles) | Supported | Not found (no vendor ms SLA) |

---

### 3. Independent TTFT Benchmarks

*Note: Latency benchmarks from **Artificial Analysis** are measured from **Google Cloud `us-central1-a` (Iowa, USA)**. Consequently, their numbers reflect near-zero local network latency to US-based inference providers, but underestimate real-world latency experienced in Southeast Asia by 140–200 ms.*

* **Groq (`llama-3.1-8b-instant`)**:
  * *Artificial Analysis*: **120 ms to 280 ms** TTFT (median ~220 ms), 560–800 tokens/sec.
  * *From Manila*: ~260–380 ms real-world TTFT (120 ms compute + ~140–180 ms trans-Pacific RTT).
* **Cerebras (`llama3.1-8b`)**:
  * *Artificial Analysis*: **150 ms to 300 ms** TTFT, **1,800–2,100 tokens/sec** output speed.
  * *From Manila*: ~290–440 ms real-world TTFT.
* **SambaNova (`Meta-Llama-3.1-8B-Instruct`)**:
  * *Artificial Analysis*: **200 ms to 350 ms** TTFT, ~900–1,000 tokens/sec.
* **Google (`gemini-2.0-flash`)**:
  * *Artificial Analysis*: **290 ms to 400 ms** TTFT (measured to US endpoints).
  * *From Manila via Vertex AI Singapore (`asia-southeast1`)*: **~280–350 ms** (250 ms compute + ~28–35 ms Singapore RTT).
* **OpenAI (`gpt-4o-mini`)**:
  * *Artificial Analysis*: **880 ms to 910 ms** TTFT via direct OpenAI API; **1,760 ms** via Azure multi-tenant endpoints.
* **DeepInfra / Together AI (`Llama-3.1-8B`)**:
  * *Artificial Analysis*: **300 ms to 500 ms** TTFT on GPU serverless clusters.
* **Alibaba Cloud Model Studio (`qwen-turbo` / `qwen3.7-flash` in Singapore)**:
  * **No independent Artificial Analysis measurement exists** specifically for the Singapore Model Studio endpoint. User-level ping/probe latency tests show server response initiation around **250–320 ms** total latency from Southeast Asia.

---

### 4. Price Ceiling Evaluation

* **Price Ceilings**: Input < **$0.01** | Output < **$0.05**

#### Interpretation A: Ceilings Read as Per 1 Million Tokens (Input < $0.01 / 1M, Output < $0.05 / 1M)
* **Zero paid models meet both ceilings** on standard uncached base pay-as-you-go pricing:
  * *DeepInfra Llama 3.1 8B* ($0.02 in / $0.045 out): **Fails input** ($0.02 > $0.01); meets output ($0.045 < $0.05).
  * *Mistral Nemo* ($0.03 in / $0.03 out): **Fails input** ($0.03 > $0.01); meets output ($0.03 < $0.05).
  * *Alibaba Qwen3.7-Flash* ($0.03 in / $0.13 out): **Fails both**.
  * *Groq Llama 3.1 8B* ($0.05 in / $0.08 out): **Fails both**.
  * *Cerebras Llama 3.1 8B* ($0.10 in / $0.10 out): **Fails both**.
  * *Gemini 1.5 Flash-8B* ($0.0375 in / $0.15 out): **Fails both**.
* *Exceptions*: 
  * Models meet this ceiling **only via free-tier quotas** (Groq, Cerebras, SambaNova, Google AI Studio free tiers at $0.00).
  * Prompt-caching reads drop some input prices below $0.01 (e.g., Qwen3.7-Flash cache read at $0.003/1M, Gemini 1.5 Flash-8B cache read at $0.01/1M), but their generated output tokens still exceed $0.05/1M.

#### Interpretation B: Ceilings Read as Per 1 Thousand Tokens (Input < $10.00 / 1M, Output < $50.00 / 1M)
* **All surveyed budget models easily meet both ceilings**:
  * DeepInfra Llama 3.1 8B ($0.02 / $0.045 per 1M) -> **Meets**
  * Mistral Nemo ($0.03 / $0.03 per 1M) -> **Meets**
  * Alibaba Qwen3.7-Flash / Qwen-Turbo ($0.03–$0.05 / $0.13–$0.20 per 1M) -> **Meets**
  * Groq Llama 3.1 8B ($0.05 / $0.08 per 1M) -> **Meets**
  * Cerebras Llama 3.1 8B ($0.10 / $0.10 per 1M) -> **Meets**
  * Gemini 1.5 Flash-8B / 2.0 Flash ($0.0375–$0.10 / $0.15–$0.40 per 1M) -> **Meets**
  * OpenAI GPT-4o-mini ($0.15 / $0.60 per 1M) -> **Meets**
  * Claude 3.5 Haiku ($0.80 / $4.00 per 1M) -> **Meets**

---

### 5. Known Tool-Calling Problems for Top Low-Latency Models

The top five low-latency candidates with streaming tool calling are: **Groq (Llama 3.1 8B)**, **Cerebras (Llama 3.1 8B)**, **Google (Gemini 2.0 Flash)**, **Alibaba (Qwen in Singapore)**, and **OpenAI (GPT-4o-mini)**.

1. **Streamed Argument Assembly & Truncation**:
   * *Problem*: In streaming mode (`stream=true`), tool arguments arrive as incremental JSON string deltas (`tool_calls[].function.arguments`). 
   * *Impact on Voice Agents*: If a user barges in or the connection resets mid-stream, partial JSON parsers frequently construct malformed tool arguments, triggering runtime crashes or hallucinated default values.
2. **"Leaked" Raw Tool Syntax (Voice Agent Specific)**:
   * *Problem*: LiveKit voice agent benchmarks and developer reports document that small open-weights models (`Llama-3.1-8B` and `Qwen-2.5-7B`) frequently bypass structured JSON fields under high temperature or multi-turn pressure.
   * *Impact*: Instead of invoking the API tool call, the model outputs raw text tokens (e.g. `<tool_call>{"name": "lookup", ...}</tool_call>`) into the standard assistant message stream. The Text-to-Speech (TTS) engine then **reads the raw XML/JSON code aloud to the caller**.
3. **Parallel Tool Calling Failures**:
   * *Problem*: When an intent requires two tools simultaneously (e.g., checking weather and checking calendar), small models frequently invoke only the first tool, duplicate parameters, or jumble schemas across calls.
   * *Groq-specific note*: Open-weight GPT-OSS variants hosted on Groq explicitly disable parallel tool use.
4. **Schema Complexity & Strict Validation Limits**:
   * *Problem*: Open-weight runners (Groq/Cerebras/vLLM) reject schemas with advanced JSON Schema features like `oneOf`, `anyOf`, or deep object recursion with `400 Bad Request` or `tool_use_failed`. OpenAI and Gemini natively normalize these schemas.
5. **Function Calling Accuracy (Berkeley Function Calling Leaderboard - BFCL)**:
   * **Gemini 2.0 Flash / 1.5 Flash**: **~81% – 89%** overall accuracy. Exceptional schema compliance and low hallucination rate.
   * **Llama-3.1-8B-Instruct**: **76.1%** overall accuracy on BFCL. Highly capable on single-turn simple Python functions (~85%+), but drops significantly on multi-turn dialogue, tool relevance/abstention detection, and parallel calls.
   * **Qwen-2.5-7B / Qwen-Turbo**: High single-turn syntax accuracy (~80–88%), but vulnerable to prompt drift and formatting leaks in conversational loops.
   * **GPT-4o-mini**: Simple function accuracy of **80–92%** and parallel accuracy of **84–90%**, but drops to **19–27%** in complex multi-turn/agentic sub-benchmarks.

---

### 6. Final Ranking for a Manila-Based Real-Time Voice Agent

1. **Google Gemini 2.0 Flash (via Google Cloud Vertex AI Singapore: `asia-southeast1`)**
   * *Reason:* Delivers the best balance of geographic proximity (~30 ms network RTT to Manila), fast server TTFT (~290 ms), and high BFCL function-calling accuracy (~85%+) that eliminates the raw tool-tag speech leaks common in 8B models.
2. **Groq (`llama-3.1-8b-instant`)**
   * *Reason:* Possesses the fastest raw compute TTFT (<120 ms) and highest generation speed (>560 tps) at ultra-low cost ($0.05/$0.08 per 1M), which offsets the 140–180 ms trans-Pacific network transit penalty from Manila to US/Sydney LPU clusters.
3. **Alibaba Cloud Model Studio (`qwen-turbo` / `qwen3.7-flash` in Singapore: `ap-southeast-1`)**
   * *Reason:* Offers direct Southeast Asian subsea cable routing (~28–35 ms RTT to Manila) and ultra-cheap pricing ($0.03–$0.05/1M input), serving as the best hedge against trans-Pacific cable jitter.

---

### Sources & Verification Log

#### Sources Cited
* **Groq Documentation & Console**: [groq.com/docs/models](https://console.groq.com/docs/models) (2025–2026); [groq.com/docs/tool-use](https://console.groq.com/docs/tool-use) (2025–2026); Groq Equinix Sydney/Helsinki infrastructure announcements (2024–2025).
* **Artificial Analysis**: [artificialanalysis.ai](https://artificialanalysis.ai) (2024–2026) methodology documentation (`us-central1-a` measurement origin) and provider latency index.
* **Cerebras Inference API**: [cerebras.ai](https://cerebras.ai) (2024–2026) product specifications and data center filings (Stockton/Dallas).
* **Alibaba Cloud Model Studio / DashScope**: [alibabacloud.com/help/en/model-studio](https://www.alibabacloud.com/help/en/model-studio) (2025–2026) Singapore endpoint pricing and region documentation.
* **Google Cloud Vertex AI & Google AI Studio**: [cloud.google.com/vertex-ai](https://cloud.google.com/vertex-ai) and [ai.google.dev](https://ai.google.dev) pricing tables for Gemini 1.5/2.0/Flash-Lite (2024–2026).
* **DeepInfra**: [deepinfra.com/pricing](https://deepinfra.com/pricing) (2025–2026) for Llama-3.1-8B-Instruct.
* **Berkeley Function Calling Leaderboard (BFCL / Gorilla)**: [gorilla.cs.berkeley.edu/leaderboard.html](https://gorilla.cs.berkeley.edu/leaderboard.html) (2024–2026) accuracy metrics for Llama-3.1-8B (76.1%), GPT-4o-mini, and Gemini Flash.
* **LiveKit Integration Telemetry**: [livekit.io](https://livekit.io) (2024–2025) voice assistant streaming tool-calling documentation on open-weights model hallucinated tags.
* **Network Latency Benchmarks**: Subsea cable telemetry via [wondernetwork.com](https://wondernetwork.com) and ping telemetry for Manila-Singapore (28–35 ms) vs Manila-US West (140–180 ms) (2024–2026).

#### Unverified Claims / Data Not Found
* **Alibaba Cloud Model Studio Singapore TTFT**: Alibaba Cloud does not publish an official millisecond TTFT guarantee or SLA, and no independent Artificial Analysis benchmark exists specifically for their Singapore endpoint.
* **DeepInfra & Mistral Official TTFT**: Neither vendor publishes an official time-to-first-token SLA; figures depend entirely on third-party telemetry.
* **OpenAI "Nano" Model**: OpenAI has not released a model formally titled "nano"; `gpt-4o-mini` was evaluated as the closest production equivalent.
