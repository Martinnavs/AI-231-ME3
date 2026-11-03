# Bottom line

**The highest-return change is to stream the grounded response into TTS, while keeping the freshness check.** Your streamed tests already showed text at **1.39–1.44 s** and completion at **1.63–1.87 s** after the call began. Compared with your **1.98 s** blocking median, that suggests roughly **0.3–0.6 s earlier speech**; this is an estimate from separate samples, not a paired result. Speak only a complete sentence, and only after the stream shows Google Search metadata.

| Rank | Lever | Expected saving | Evidence and risk |
|---|---|---:|---|
| 1 | Stream text to TTS after a complete sentence and search metadata arrive | **~0.3–0.6 s [estimate from your measurements]** | Low implementation risk. Speaking before the sentence or search is verified could expose a wrong answer. |
| 2 | Start a speculative request from a sufficiently stable interim transcript | **~0.3–0.9 s [estimate]** | A 2026 Android voice-assistant prototype measured **1.19 s** lower median time-to-first-audio, but it was not Gemini Search. A wrong partial transcript can waste a billable search or produce a mismatched answer. [Paper](https://arxiv.org/abs/2610.07641) |
| 3 | A/B test Priority inference, if your API project is eligible | **NOT FOUND** for grounded latency | Google supports it for `gemini-3.1-flash-lite` on GenerateContent for Tier 2/3 users, but publishes no grounded-latency gain. It costs **75–100% more per model token**; the Search query fee is separate. [Priority inference](https://ai.google.dev/gemini-api/docs/generate-content/priority-inference) |
| 4 | A/B test `gemini-3.5-flash-lite`, behind a mandatory-search gate | **NOT FOUND** for your AI Studio setup | A small Vertex benchmark measured a **0.65 s** lower median *full-call* latency than 3.1, but the 3.5 model returned no sources on **11/24** cases versus **4/24** for 3.1. That search-coverage regression conflicts with your requirement. [Benchmark](https://github.com/cwest/gemini-search-mcp/blob/main/evals/results/2026-07-22-default-model-sweep.md) |

Your own sample already shows why the gate matters: only **33 of 40** freshness-sensitive questions searched. For these turns, require non-empty `webSearchQueries` or grounding chunks before treating an answer as current. The API exposes those fields, but Google’s tool flow lets the model decide whether to search; a system instruction alone does not guarantee execution. [Search guide](https://ai.google.dev/gemini-api/docs/google-search?hl=en) · [GenerateContent reference](https://ai.google.dev/api/generate-content)

**Test budget note:** Google lists **5,000** monthly included Gemini 3+ Search requests, then **$14 per 1,000**; the docs say a prompt can produce multiple separately billed queries. A target of **12** paid query units is **$0.168**, leaving room for short-response token costs under the assumptions in the tests below. But the API documents no per-request query cap, so a test cannot be guaranteed to stay below **$0.20** if that included allowance is exhausted and one request fans out unexpectedly. [Pricing](https://aistudio.google.com/docs/pricing) · [Search billing behavior](https://ai.google.dev/gemini-api/docs/google-search?hl=en)

# Findings

## 1. Where the grounded call’s time goes

Google describes the sequence as: analyze the prompt, decide whether Search is useful, generate **one or more** queries, execute them, process results, and synthesize the answer. It publishes no stage timestamps or latency breakdown for query planning, Search RPCs, result processing, or the generation pass. **NOT FOUND:** a measured, official split of the roughly two seconds.

Your searched and non-searched medians differ by about **0.83 s** (**2.10 s − 1.27 s**, calculated from your figures). That is evidence for the combined cost of the search path in your sample; it does **not** isolate the Search service’s duration.

The current `google_search` schema exposes search types and a time-range filter, but no maximum-query field. The docs explicitly allow one or more queries, so “search once” in the prompt can be tried as a preference but cannot enforce a cap. The dynamic-retrieval threshold belongs to the older `google_search_retrieval` tool and decides whether retrieval triggers; it is not a query-count control for your Gemini 3 `google_search` request. [API reference](https://ai.google.dev/api/generate-content) · [Search guide](https://ai.google.dev/gemini-api/docs/google-search?hl=en)

I found no documented “fast” or “lite” Google Search grounding mode. Google Search works across supported languages, but the current `google_search` schema has no language option. Vertex can use latitude/longitude to localize results; Google does not claim that this reduces latency. Web Grounding for Enterprise is not a speed substitute: its fast-changing content is refreshed every **6 hours**, its full index every **24 hours**, and Google recommends regular Search when freshness is the priority. **NOT FOUND:** a measured speed advantage for Enterprise grounding. [Vertex Search grounding](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/grounding/grounding-with-google-search) · [Enterprise web grounding](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/grounding/web-grounding-enterprise)

## 2. Streaming behavior and what TTS should handle

`streamGenerateContent` sends incremental `GenerateContentResponse` chunks. Your measured first text at **1.39–1.44 s** confirms that you can receive text before the full response finishes. [GenerateContent streaming guide](https://ai.google.dev/gemini-api/docs/generate-content/text-generation?authuser=9)

For tool ordering, Google’s documented workflow shows Search calls and results before the grounded `model_output`. That suggests the **answer text** starts after Search completes, but Google gives no timing guarantee for `streamGenerateContent` with Search. **NOT FOUND:** a published grounded time-to-first-token distribution or a contract that says exactly when answer text may start relative to Search.

The stream emits text incrementally; the API documentation describes chunks, not a text-retraction mechanism. Treat text deltas as append-only, but do not treat a partial phrase as a settled answer. Buffer to a complete sentence.

Grounding metadata can also arrive incrementally. Google says streamed `groundingChunks` contain only chunks not already sent, and citation indices refer to the accumulated chunks. Accumulate grounding chunks and supports across the stream; do not assume the final chunk contains all citations. Gate TTS on a complete sentence **and** observed Search metadata. If metadata is absent at completion, treat the answer as ungrounded for a freshness-sensitive question. [GenerateContent reference](https://ai.google.dev/api/generate-content)

## 3. Model choice

Google currently lists stable `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`, and `gemini-3.8-flash` models. It also documents `gemini-flash-lite-latest` as a rolling alias that can be hot-swapped. For comparisons, use fixed model IDs and log GenerateContent’s `modelVersion`. [Models and aliases](https://ai.google.dev/gemini-api/docs/models?authuser=002) · [GenerateContent reference](https://ai.google.dev/api/generate-content)

**NOT FOUND:** an official grounded TTFT comparison for those models. Google’s 3.1 model card reports **363 output tokens/s**, but that is not grounded TTFT. The 3.5 card describes the model as latency-sensitive, but gives no grounded TTFT number. Neither number tells you how much time is spent before text can be generated after Search. [3.1 model card](https://deepmind.google/models/model-cards/gemini-3-1-flash-lite/) · [3.5 model card](https://deepmind.google/models/model-cards/gemini-3-5-flash-lite/)

The independent **July 2026** Vertex Global sweep is the most directly relevant comparison I found. Across **24** cases, its median full-call latency was **2.810 s** for 3.1 Flash-Lite, **2.162 s** for 3.5 Flash-Lite, and **2.330 s** for 3.6 Flash. But it was a small, different deployment; it measured full-call latency, not TTFT, and the newer models produced no sources on **11/24** and **13/24** cases, respectively, versus **4/24** for 3.1. Treat its speed results as a reason to test, not a reason to switch. [Sweep report and method](https://github.com/cwest/gemini-search-mcp/blob/main/evals/results/2026-07-22-default-model-sweep.md)

## 4. Endpoints, transport, and Live API

For your AI Studio endpoint, I found no Manila/Singapore region selector or published Manila routing benchmark. Vertex model availability is model-specific: 3.1 Flash-Lite lists `global`, `us`, and `eu`; 3.5 Flash-Lite adds `asia-south1` (Mumbai), but neither model’s list includes `asia-southeast1` (Singapore). Vertex’s global endpoint is documented as offering higher availability and reliability than single regions; that is not a latency promise or a guarantee of in-region processing. **NOT FOUND:** a measured Manila comparison of AI Studio global, Vertex global, and Vertex regional routes. [Vertex 3.1 regions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-flash-lite) · [Vertex 3.5 regions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash-lite?hl=en) · [Endpoint behavior](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/locations?hl=en)

The Google API proto exposes GenerateContent and a REST HTTP binding, so gRPC is represented in the service definition. I found no REST-versus-gRPC or HTTP/2-versus-REST benchmark for grounded calls. The Python GenAI SDK says its optional `aiohttp` async client can be faster than its default `httpx` client, but gives no improvement figure; with your warm keep-alive connection, measure before changing transport. [Service proto](https://github.com/googleapis/googleapis/blob/master/google/ai/generativelanguage/v1beta/generative_service.proto) · [Python SDK](https://googleapis.github.io/python-genai/)

The current `gemini-3.8-live` model supports Search and uses a stateful WebSocket, but its only response modality is audio; text is available as an audio transcript, not as a text-only response mode. So it is not a drop-in text-to-local-TTS replacement. Google describes Live as low-latency but publishes no grounded Search TTFT for it. **NOT FOUND:** evidence that Live cuts time to first grounded words in your text-output setup. [Live API overview](https://aistudio.google.com/docs/live-api) · [3.8 Live model](https://aistudio.google.com/docs/models/gemini-3.8-live)

Live’s current paid rates are **$3.00 per million input audio tokens** (listed as **$0.005/minute**) and **$12.00 per million output audio tokens** (listed as **$0.018/minute**); text input/output is priced separately. Search keeps the Gemini 3+ query fee. Enabling text transcription adds text-token charges on top of audio usage. The old `gemini-3.1-flash-live-preview` is scheduled to shut down on **November 17, 2026**, so it is not a durable experiment target. [Pricing](https://aistudio.google.com/docs/pricing) · [Live billing details](https://ai.google.dev/gemini-api/docs/live-api/best-practices?hl=en) · [Deprecations](https://ai.google.dev/gemini-api/docs/deprecations)

## 5. Request settings

You already use `thinkingLevel="minimal"`. Google says `minimal` matches no-thinking behavior for most queries, but does not guarantee thinking is fully off. The thinking docs warn that `max_output_tokens` is a hard limit that includes thought tokens; setting it too low can truncate or empty a response rather than make thinking more concise. Since your prompt already requests two short sentences, a tighter output cap is unlikely to help first text if Search and planning dominate. [Thinking docs](https://ai.google.dev/gemini-api/docs/generate-content/thinking?hl=en)

The highest-value settings are already in place: a short system instruction and a one-candidate response. `candidateCount` defaults to **1**; `text/plain` is the default MIME type. I found no grounded-latency evidence for changing MIME type or safety settings. Vertex’s Search documentation recommends temperature **1.0** for ideal Search grounding; I found no speed reason to lower it. A “one short query” instruction might reduce fanout in some cases, but the schema has no query cap and I found no measured accuracy/speed tradeoff for that instruction. [GenerateContent reference](https://ai.google.dev/api/generate-content) · [Vertex Search grounding](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/grounding/grounding-with-google-search)

## 6. Speculation, duplication, prefetching, and Tavily races

Speculative execution from interim speech recognition has the strongest research support among these strategies: a 2026 Android prototype predicted tool calls from partial ASR and measured median time-to-first-audio dropping from **5.79 s to 4.60 s**. That is not a Gemini Search measurement, and it does not predict your saving. For your assistant, reuse the speculative result only when the final transcript matches its intent and the result confirms Search; otherwise discard it and run the final query. A discarded or cancelled request may still have executed billable Search queries. [Paper](https://arxiv.org/abs/2610.07641)

I found no public production data showing voice assistants commonly hedge duplicate Google Search requests or race Tavily against Gemini and speak the first result. A duplicate can incur duplicate model and Search charges. Your own Tavily measurement (**1.25 s**) is only slightly ahead of your streamed Gemini first-text sample (**1.39–1.44 s**) and you report lower accuracy on changing facts. A raw “first answer wins” race would spend that accuracy. Waiting for Gemini to corroborate Tavily likely removes the speed advantage. Future-question prefetching is also hard to justify for one user; interim-transcript speculation is the more targeted form.

## 7. Billing

For Gemini 3+, Search is billed per executed query, not simply per API request. One prompt may issue several queries, and each is a billable unit. The current AI Studio page lists **5,000** included monthly Search requests shared across Gemini 3+ models, then **$14/1,000**; that is **$0.014/query** beyond the allowance. The page also lists a default daily cap of **1,500** Search requests per project. [Pricing](https://aistudio.google.com/docs/pricing) · [Search billing details](https://ai.google.dev/gemini-api/docs/google-search?hl=en)

I found no separate streaming price. The API still bills model tokens, and the pricing page does not list a streaming surcharge; the inference is that streaming changes delivery timing, not the token or Search unit rates. Live uses its audio/text token rates plus the same Search query billing. Priority’s premium is per model token, not a Search discount. [Pricing](https://aistudio.google.com/docs/pricing) · [Priority inference](https://ai.google.dev/gemini-api/docs/generate-content/priority-inference)

# Cheap screening tests

All thresholds below are **proposed screening criteria**, not Google guarantees. Keep prompts and thinking settings fixed, bypass your local 10-minute cache during each comparison, and manually check both current-fact correctness and Search metadata. Four prompt pairs are only a screen, not proof of non-inferior accuracy.

For a paid project beyond its included Search allowance, target at most **12** observed Search query units per test (**$0.168**) plus short-response token use. This stays under **$0.20** under the assumptions of at most **5,000 input tokens** and **128 output tokens** per call. Because Google exposes no per-request query cap, a single fanout can exceed that target before you can stop subsequent calls. If your included monthly Search allowance remains, the Search fees for these tests are covered by it.

1. **Streaming:** Compare four matched prompts using blocking and streaming GenerateContent: **8 requests**. Measure time to first spoken word, beginning TTS only after a complete sentence and Search metadata. **Pass:** streaming improves median time by at least **0.3 s**, all answers are correct, and all freshness-sensitive calls show Search metadata. Otherwise, keep the current TTS handoff.

2. **Interim-transcript speculation:** Compare four normal turns with four speculative turns, allowing one final-transcript retry per speculative turn: at most **12 requests**. Discard speculative answers when the final transcript changes or Search is absent. **Pass:** median first-audio time improves by at least **0.4 s** with no wrong or ungrounded answer spoken early.

3. **Priority:** If eligible, run four standard/Priority pairs: **8 requests**. Check the response service-tier field/header and exclude downgraded calls. **Pass:** Priority improves median first-text time by at least **0.25 s**, with Search and correctness unchanged. If it does not, the token premium has no demonstrated value for this workload.

4. **3.5 Flash-Lite:** Run four paired prompts on fixed IDs `gemini-3.1-flash-lite` and `gemini-3.5-flash-lite`: **8 requests**. Require Search metadata on both models for every freshness-sensitive prompt. **Pass:** 3.5 is at least **0.3 s** faster at median and all answers pass the same search/correctness checks. Any missed Search is a failure, even if its latency is lower.

# What I could not find

- **NOT FOUND:** A public stage-by-stage latency trace for Gemini Search grounding, a max-query setting, a Search `fast` mode, or a regional Search latency comparison from Manila.
- **NOT FOUND:** A published typical TTFT for grounded `gemini-3.1-flash-lite`, nor a reliable text-revision guarantee for streamed grounded answers. The docs do specify incremental chunks and accumulation of grounding metadata.
- **NOT FOUND:** A grounded TTFT comparison between current Gemini models, an HTTP/2 or gRPC speed advantage, or a Gemini Live Search timing comparison for text-first output.
- **NOT FOUND:** Public production evidence for duplicate hedging or Tavily/Gemini racing in voice assistants.
- **NOT FOUND:** A hard way to guarantee a paid Search test stays below **$0.20** after the free allowance: Google does not document or expose a per-request query cap.

# All URLs used

- [Gemini Search grounding](https://ai.google.dev/gemini-api/docs/google-search?hl=en)
- [GenerateContent API reference](https://ai.google.dev/api/generate-content)
- [GenerateContent streaming guide](https://ai.google.dev/gemini-api/docs/generate-content/text-generation?authuser=9)
- [Gemini thinking guide](https://ai.google.dev/gemini-api/docs/generate-content/thinking?hl=en)
- [Gemini API pricing](https://aistudio.google.com/docs/pricing)
- [Priority inference for GenerateContent](https://ai.google.dev/gemini-api/docs/generate-content/priority-inference)
- [Gemini models and aliases](https://ai.google.dev/gemini-api/docs/models?authuser=002)
- [Gemini 3.1 Flash-Lite model card](https://deepmind.google/models/model-cards/gemini-3-1-flash-lite/)
- [Gemini 3.5 Flash-Lite model card](https://deepmind.google/models/model-cards/gemini-3-5-flash-lite/)
- [Independent 2026 model sweep](https://github.com/cwest/gemini-search-mcp/blob/main/evals/results/2026-07-22-default-model-sweep.md)
- [Speculative voice-agent paper](https://arxiv.org/abs/2610.07641)
- [Vertex Gemini 3.1 Flash-Lite regions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-flash-lite)
- [Vertex Gemini 3.5 Flash-Lite regions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash-lite?hl=en)
- [Vertex endpoints and locations](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/locations?hl=en)
- [Web Grounding for Enterprise](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/grounding/web-grounding-enterprise)
- [Vertex Search grounding](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/grounding/grounding-with-google-search)
- [Google API service proto](https://github.com/googleapis/googleapis/blob/master/google/ai/generativelanguage/v1beta/generative_service.proto)
- [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/)
- [Gemini Live API overview](https://aistudio.google.com/docs/live-api)
- [Gemini 3.8 Live model](https://aistudio.google.com/docs/models/gemini-3.8-live)
- [Live API billing and best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices?hl=en)
- [Gemini model deprecations](https://ai.google.dev/gemini-api/docs/deprecations)