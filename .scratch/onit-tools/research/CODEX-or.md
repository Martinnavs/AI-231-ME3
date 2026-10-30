## 1) Six-line summary

1. **Claude Haiku 5.5** — strongest affordable provisional pick: OpenRouter reports TAU-Bench provider results and tool-call error rates, but that is not a shared, independently controlled comparison. [Source: OpenRouter model page, accessed 2026-10-29; “TAU-Bench” and “Tool call error rate”](https://openrouter.ai/anthropic/claude-haiku-5.5/providers)  
2. **MiniMax M3.1 Flash** — its model card reports Automation-Bench and Terminal-Bench 4.0 results; the card’s Terminal-Bench score trails some listed peers, so benchmark setup matters. [Source: MiniMax model card, accessed 2026-10-29; “Automation-Bench v1.0.6” and “Terminal Bench 4.0”](https://huggingface.co/MiniMaxAI/MiniMax-M3.1-Flash)  
3. **Qwen3.8 Flash** — attractive price, tool support, and an official Qwen post with coding and agentic results; the accessible evidence did not establish its accuracy on your exact shell workflow. [Source: OpenRouter, released 2026-08-26; “suited for…agentic workflows”](https://openrouter.ai/qwen/qwen3.8-flash/providers), [Qwen Team, August 2026; “Terminal Bench 2.1”](https://qwen.ai/blog?id=qwen3.8)  
4. **GPT-6 Luna** — low listed price and official tool/function-calling support; I did not find comparable agent-loop results for it. [Source: OpenAI model docs, accessed 2026-10-29; “Use the Responses API for built-in tools and function calling”](https://developers.openai.com/api/docs/models/gpt-6-luna)  
5. **GLM-5.3 FlashX** — tool calling and a vendor-stated speed target make it a candidate, but I did not find a suitable controlled task benchmark in the sources read. [Source: OpenRouter, released 2026-09-18; “up to 200 tokens/s”](https://openrouter.ai/z-ai/glm-5.3-flashx/providers)  
6. **Recommendation:** benchmark these five plus Gemini 3.1 Flash-Lite as the baseline; treat the ranking as provisional because model-specific benchmarks use different harnesses, effort settings, and providers. [Inference]

## 2) Model comparison

Prices below are the candidate prices in your prompt unless the row says otherwise. I did not independently verify every candidate’s current price or tool support. “NOT FOUND” means I did not find adequate evidence in the sources read; it does not mean the capability is absent. Dates refer to release dates shown on the linked pages or the date accessed.

| Model | Price in/out per 1M | Cached-input price | Agentic-tool-use evidence | Math evidence | Typical task tokens / benchmark-run tokens | Speed | Known tool failure modes | Verdict |
|---|---:|---:|---|---|---|---|---|---|
| Gemini 3.8 Flash | $0.83 / $4.12 (provided list; page showed temporary discount) | NOT FOUND | OpenRouter describes gains in agentic tasks, but no comparable task score found. [Source: OpenRouter, released 2026-09-02; “significant gains…across…agentic tasks”](https://openrouter.ai/google/gemini-3.8-flash/providers) | NOT FOUND | NOT FOUND | First-token latency and output speed for the target harness: NOT FOUND | NOT FOUND | Stronger-sounding candidate, but price is well above baseline and evidence is not task-specific. |
| Gemini 3.5 Flash-Lite | $0.30 / $2.50 (provided list) | NOT FOUND | Google describes it as optimized for “high-volume agentic tasks”; no result for the proposed terminal loop found. [Source: Google API pricing, accessed 2026-10-29](https://ai.google.dev/gemini-api/docs/pricing) | NOT FOUND | NOT FOUND | NOT FOUND | NOT FOUND | Consider as another baseline only if it is actually available under this model ID. |
| Qwen3.8 Flash | $0.15 / $0.47 | $0.016/M read; cache creation $0.20/M (OpenRouter page) | Function calling is supported. Qwen’s official post includes Terminal-Bench 2.1 results, but the retrieved excerpt did not expose the table’s model-to-column mapping. [Source: OpenRouter, released 2026-08-26](https://openrouter.ai/qwen/qwen3.8-flash/providers), [Qwen Team, August 2026](https://qwen.ai/blog?id=qwen3.8) | Qwen post mentions AIME24 improvement, but the excerpt does not establish that result for this API model. [Same Qwen source; “+2.7-point gain”] | NOT FOUND | Provider-specific first-token latency: NOT FOUND | NOT FOUND | Best low-cost open-weight candidate to test; confirm the benchmark row and tool-call quality in your own harness. |
| Qwen3.8 27B | $0.04 / $1.25 | NOT FOUND | OpenRouter lists 18 providers, but comparable agentic task evidence and provider-specific tool error rates: NOT FOUND. [Source: OpenRouter, accessed 2026-10-29](https://openrouter.ai/qwen/qwen3.8-27b) | Third-party model page lists Terminal-Bench 2.1, but I did not verify its methodology or math scores. [Source: Qwen3-8 benchmark page, accessed 2026-10-29](https://qwen3-8.org/benchmark) | NOT FOUND | NOT FOUND | NOT FOUND | Its cheap input price is offset by relatively expensive output; benchmark before choosing it for a reasoning-heavy loop. |
| Qwen3.7 Plus | $0.32 / $1.28 | NOT FOUND | NOT FOUND | NOT FOUND | NOT FOUND | NOT FOUND | NOT FOUND | No evidence found to justify keeping it ahead of Qwen3.8 Flash. |
| GLM-5.3 FlashX | $0.37 / $1.25 | $0.09/M (OpenRouter page) | Supports tool calling. Page states “up to 200 tokens/s,” but I found no comparable end-to-end agent benchmark for this model. [Source: OpenRouter, released 2026-09-18](https://openrouter.ai/z-ai/glm-5.3-flashx/providers) | NOT FOUND | NOT FOUND | “Up to 200 tokens/s” is a stated maximum, not measured task latency. First-token latency: NOT FOUND. [Same source] | Page says JSON output lacks JSON-schema enforcement; no tool loop failure analysis found. [Same source] | Worth a trial if generation speed matters; not yet a proven value pick. |
| GLM-5.3 | $0.07 / $1.40 | NOT FOUND | A third-party OpenRouter mirror reports provider-level TAU-Bench data, but I did not establish its date or methodology well enough to rank it. [Source: mirror, accessed 2026-10-29](https://openrouter.chista.org/z-ai/glm-5.3) | NOT FOUND | NOT FOUND | NOT FOUND | NOT FOUND | Hold as a low-input-price candidate; require a direct provider/model page and local task results. |
| DeepSeek V4.1 Flash | $0.04 / $0.50 | NOT FOUND | DeepSeek’s release page publishes an agentic benchmark comparison image; the accessible text did not expose its scores. [Source: DeepSeek, accessed 2026-10-29](https://deepseek.com/en/news/deepseek-v4-1-flash/) | NOT FOUND | NOT FOUND | NOT FOUND | NOT FOUND | Lowest modeled cost in the estimate, but insufficient extracted evidence to recommend as the first delegate. |
| MiniMax M3.1 Flash | $0.15 / $0.60 | $0.015/M (OpenRouter page) | MiniMax’s model card reports Automation-Bench 62.8 and Terminal-Bench 4.0 38.1; it also lists other models, but the benchmark setup must be checked before comparing. [Source: MiniMax model card, accessed 2026-10-29; “Automation-Bench v1.0.6”](https://huggingface.co/MiniMaxAI/MiniMax-M3.1-Flash) | No AIME result found in the model card excerpt. | NOT FOUND | OpenRouter lists provider performance; TTFT was not established. [Source: OpenRouter, accessed 2026-10-29](https://openrouter.ai/minimax/minimax-m3.1-flash/providers) | OpenRouter reports provider-dependent tool-call error rates; exact figures vary by provider. [Same source] | Include in the shortlist, with provider pinned for evaluation. |
| Kimi K2.7 Code | $0.56 / $3.10 | NOT FOUND | OpenRouter calls it coding-focused and says it “always operates in a thinking mode”; no comparable benchmark score found in the source read. [Source: OpenRouter, released 2026-06-12](https://openrouter.ai/moonshotai/kimi-k2.7-code) | NOT FOUND | Always-on reasoning may increase billed output; actual task-token distribution: NOT FOUND. [Same source] | NOT FOUND | NOT FOUND | Keep only as a higher-cost coding comparison, not the default delegate. |
| Xiaomi MiMo V2.6 Flash | $0.14 / $0.28 | NOT FOUND | OpenRouter describes the architecture, but I found no controlled agentic task result. [Source: OpenRouter, accessed 2026-10-29](https://openrouter.ai/xiaomi/mimo-v2.6-flash/) | NOT FOUND | NOT FOUND | NOT FOUND | NOT FOUND | Very low output price; evidence is too thin to rank it above the shortlist. |
| Claude Haiku 5.5 | $0.10 / $0.50 | $0.01/M cache read (OpenRouter page) | OpenRouter provider data includes TAU-Bench and tool-call error rates; these are provider observations, not a shared terminal harness. [Source: OpenRouter, released 2026-10-07](https://openrouter.ai/anthropic/claude-haiku-5.5/providers) | NOT FOUND | NOT FOUND | Page reports provider latency/throughput; first-token measurement for your setup: NOT FOUND. [Same source] | Provider-specific tool-call error rates are reported; the page shows variation by endpoint. [Same source] | Strongest evidence-to-price balance among candidates I reviewed; first trial. |
| GPT-6 Luna | $0.10 / $0.50 | OpenAI direct: $0.01/M cached input; OpenRouter cache price NOT FOUND. [Source: OpenAI, accessed 2026-10-29](https://developers.openai.com/api/docs/models/gpt-6-luna) | Official docs confirm built-in tools and function calling. No comparable agent-loop benchmark found. [Same source] | NOT FOUND | NOT FOUND | NOT FOUND | NOT FOUND | Include as a useful closed-model control; agentic effectiveness remains unverified here. |

**Benchmark caveat:** MiniMax’s model card reports one Terminal-Bench 4.0 result, while Qwen’s official page excerpt references Terminal-Bench 2.1. Those are different benchmark versions, so their scores should not be compared directly. MiniMax’s model card lists “Terminal Bench 4.0 | 38.1”; Qwen’s post lists “Terminal Bench 2.1.” [Sources: MiniMax model card, accessed 2026-10-29](https://huggingface.co/MiniMaxAI/MiniMax-M3.1-Flash), [Qwen Team, August 2026](https://qwen.ai/blog?id=qwen3.8)

## 3) Estimated cost for one run of each task

These are **token-cost estimates, not measured task costs**. I assume cumulative billed usage across the whole loop—including resubmitted context and tool outputs—of: static website **200k input + 20k output**; multi-step math **90k input + 8k output**; read-only Linux audit/report **250k input + 30k output**. That assumes the loop stays within 25 model tool calls, but actual calls, retries, reasoning tokens, provider cache behavior, and task success can change usage substantially. **Inference.** The formula is `input_tokens × input_price + output_tokens × output_price`, divided by 1,000,000. Estimates use the prices in your prompt; cached tokens are not discounted here.

| Model | Static website | Multi-step math | Linux audit/report |
|---|---:|---:|---:|
| Gemini 3.8 Flash | $0.2484 | $0.1077 | $0.3311 |
| Gemini 3.5 Flash-Lite | $0.1100 | $0.0470 | $0.1500 |
| Qwen3.8 Flash | $0.0394 | $0.0173 | $0.0516 |
| Qwen3.8 27B | $0.0330 | $0.0136 | $0.0475 |
| Qwen3.7 Plus | $0.0896 | $0.0390 | $0.1184 |
| GLM-5.3 FlashX | $0.0990 | $0.0433 | $0.1300 |
| GLM-5.3 | $0.0420 | $0.0175 | $0.0595 |
| DeepSeek V4.1 Flash | $0.0180 | $0.0076 | $0.0250 |
| MiniMax M3.1 Flash | $0.0420 | $0.0183 | $0.0555 |
| Kimi K2.7 Code | $0.1740 | $0.0752 | $0.2330 |
| MiMo V2.6 Flash | $0.0336 | $0.0148 | $0.0434 |
| Claude Haiku 5.5 | $0.0300 | $0.0130 | $0.0400 |
| GPT-6 Luna | $0.0300 | $0.0130 | $0.0400 |

The provider’s billed “output” may include reasoning tokens. Google’s Gemini pricing page explicitly says “Output price (including thinking tokens).” [Source: Google API pricing, accessed 2026-10-29](https://ai.google.dev/gemini-api/docs/pricing) Actual reasoning/output-token counts per task and benchmark-run token counts were **NOT FOUND** for these models in the sources reviewed.

## 4) Stretching the $7 budgets

**OpenRouter**

- OpenRouter says inference uses provider list prices, and its Standard plan fee is charged when credits are bought: **5.5%**. A new $7 credit purchase would therefore incur about **$0.39** in fee; if the $7 is already in account credits, that purchase fee has already been handled. [Source: OpenRouter pricing, accessed 2026-10-29; “fee is charged when you buy credits”](https://openrouter.ai/pricing)
- Use a provider/model route whose cache price and cache hit rate are visible. OpenRouter says to inspect `usage.prompt_tokens_details.cached_tokens`; a positive value confirms cache hits. Repeated loops only save money when the same prefix actually hits cache. [Source: OpenRouter caching guide, updated 2026-09-24; “cached_tokens value above zero confirms a hit”](https://openrouter.ai/blog/tutorials/prompt-caching-sticky-routing/)
- Pin a provider during benchmarking: OpenRouter’s provider pages show different per-provider prices, speeds, and tool-call error rates for the same model. [Source: OpenRouter MiniMax provider page, accessed 2026-10-29](https://openrouter.ai/minimax/minimax-m3.1-flash/providers)
- Free variants can stretch the budget for smoke tests, but OpenRouter notes free models can have request caps and provider limits. Don’t rely on them for the timed demo without checking current availability. [Source: OpenRouter free-model collection, accessed 2026-10-29; “$0 prompt pricing”](https://openrouter.ai/collections/free-models)
- I found no general OpenRouter batch discount; its pricing page says inference is not discounted by the plan. [Source: OpenRouter pricing, accessed 2026-10-29](https://openrouter.ai/pricing)

**Gemini API direct**

- Gemini 3.1 Flash-Lite is listed at **$0.25/M input and $1.50/M output**, with **$0.025/M cached input**. The same page says the free tier is free of charge, but usage is subject to tier limits; check the project’s live limits in AI Studio before depending on it for the demo. [Source: Google Gemini API pricing, accessed 2026-10-29](https://ai.google.dev/gemini-api/docs/pricing), [rate-limit docs, accessed 2026-10-29](https://ai.google.dev/gemini-api/docs/rate-limits)
- Gemini Batch API is priced at **50% of standard interactive API cost**, but it is asynchronous, so it is unsuitable for the user-facing delegate response if that response must arrive promptly. [Source: Google Batch API docs, accessed 2026-10-29; “50% of the standard cost”](https://ai.google.dev/gemini-api/docs/batch-api)
- For the demo, reserve the Gemini direct $7 for the front model or fallback, and route only difficult background work to the strongest measured delegate. **Inference:** this reduces the chance that repeated hard-task loops consume the voice agent’s budget.

## 5) What I could not find

- A same-harness comparison of all candidates on your exact 25-call agent loop, including static-site creation, multi-step math with Python, and Linux read-only audit/report work.
- Reliable task-level input/output/reasoning token distributions, or cost-per-success data, for most models.
- First-token latency and end-to-end completion-time comparisons under your exact provider, region, concurrency, and tool scaffold.
- Independently reproduced math results for the candidates at comparable reasoning settings.
- Well-documented tool-failure analysis for most candidates; provider-level error rates are available for only some OpenRouter model pages.
- Independently verified current prices, cached-input rates, and tool-call support for every model in the supplied candidate list.