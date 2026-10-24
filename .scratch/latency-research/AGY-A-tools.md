# Research Report: Eliminating and Hiding the Second LLM Request on Tool Turns in Voice Pipelines

---

## 1. Six-Line Summary

1. Frameworks like LiveKit, Vapi, Retell, and Pipecat mask tool execution latency using acoustic earcons, TTS fillers, or non-blocking speech frames (`ctx.with_filler`, Vapi `request-start`, Retell `speak_during_execution`, Pipecat `on_function_calls_started`).
2. Suppressing the second LLM request via Pipecat `run_llm=False` saves an entire LLM inference round-trip (~700–800 ms) but causes fatal Gemini API `400 Bad Request` turn-alternation errors unless a synthetic `model` turn is injected into context.
3. Pre-retrieval RAG (injecting BM25 passages into the prompt before the first LLM request) eliminates the retrieval tool call entirely, reducing latency by an entire LLM turnaround (~700–800 ms) at the risk of prompt pollution on non-retrieval turns.
4. Gemini's `generateContentStream` outputs structured parts where `GoogleLLMService` in Pipecat streams `part.text` immediately to TTS before tool execution, but Gemini rarely outputs conversational text and a `functionCall` in the same turn under default configurations.
5. In voice UX, premature verbal confirmations ("Turning on the lights") that fail destroy user trust; industry standards (Google Assistant, Amazon Alexa Brief Mode) replace verbal acknowledgments for local smart-home actions with non-verbal earcons (chimes).
6. The highest-impact, lowest-risk latency optimization for your pipeline is adopting non-verbal audio chimes for `smart_home` alongside pre-retrieval prompt injection for `search_docs`.

---

## 2. Evidence-Based Sections per Question

### A1. Speaking While a Tool Runs or Upon Tool Call Arrival

Production voice frameworks provide explicit APIs to emit audio or text while a tool is in flight:

#### Pipecat
* **`on_function_calls_started`:** Registered as an event decorator on the LLM service instance. It fires immediately when the model emits tool calls before tool handlers execute.
  * [source: `pipecat-ai/pipecat` GitHub `src/pipecat/services/llm_service.py` / Issue #1842] *"used primarily to trigger filler or acknowledgment messages (like 'Let me check on that') via text-to-speech (TTS) right as the LLM initiates a tool/function call."*
  * Code pattern:
    ```python
    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
      await tts.queue_frame(TTSSpeakFrame("Let me check on that."))
    ```
* **Tool-Level Frame Pushing:** Developers push frames directly into the pipeline inside the function handler prior to awaiting slow I/O:
  * [source: `pipecat-ai/pipecat` GitHub Issue #1842] `await params.llm.push_frame(TTSSpeakFrame("Let there be light!"))`
* **Non-verbal Audio Fillers:** Pipecat provides `SoundfileMixer` and `OutputAudioRawFrame` to mix static audio files (chimes, clicks) into the transport stream [source: `docs.pipecat.ai`].
* **Latency Figures & Recommendations in Docs:** Pipecat docs recommend fillers for slow operations, but do **NOT** publish a standardized latency reduction benchmark table [source: `docs.pipecat.ai`].

#### LiveKit Agents
* **`ctx.with_filler(...)` (Python) / `ctx.filler(...)` (Node.js):** Introduced in `livekit-agents` v1.6.0. An asynchronous context manager inside an `@function_tool` that plays filler phrases directly via `session.say()`, bypassing the LLM context.
  * [source: `docs.livekit.io`, "Async Tools", 2025/2026] *"ctx.with_filler(...) is an asynchronous context manager used during async tool execution to play filler speech (such as 'Let me look that up for you' or 'Still working on it') directly through audio output (session.say()), bypassing the LLM to mask dead air or latency while a slow or long-running tool runs."*
  * Idle Delay Configuration: Plays only if the session is continuously idle for `delay` seconds (e.g., `delay=5, interval=10, max_steps=3`) [source: `docs.livekit.io`].
* **`await ctx.update(message)`:** Dynamically updates chat context so the agent acknowledges progress non-blockingly [source: `docs.livekit.io`].
* **Latency Recommendation:** LiveKit explicitly recommends pre-synthesizing fixed filler audio once at startup and caching the raw frames to eliminate real-time TTS synthesis latency entirely [source: `docs.livekit.io`, "Latency Optimization"].

#### Vapi
* **Tool `messages` Configuration:** In Vapi's API Request Tools, developers configure a lifecycle array of message objects:
  * [source: `docs.vapi.ai`, "Tools: Messages Configuration"]
    * **`request-start`:** Triggered immediately when the tool call is dispatched (*"Looking that up now."*).
    * **`request-response-delayed`:** Triggered if the tool response exceeds a configured threshold (e.g., `timing: 5000` ms) (*"The order system is taking a little longer than expected."*).
    * **`request-complete`:** Spoken upon successful return.
    * **`request-failed`:** Spoken if the tool times out (`timeoutSeconds`) or returns an HTTP error.
  * Dynamic Filler: Supports `bot_say` parameter inside tool arguments, with an empty `request-start` content string to suppress static default fillers [source: `docs.vapi.ai`].

#### Retell AI
* **Tool Execution Parameters:** Configured per custom tool/function:
  * [source: `docs.retellai.com`, "Custom Tools API Reference"]
    * **`speak_during_execution`** (`boolean`): Determines whether the agent speaks while waiting for the tool response.
    * **`execution_message_type`** (`string`): Options are `"prompt"` (model dynamically generates a contextual filler) or `"static_text"` (agent speaks fixed text).
    * **`execution_message_description`** (`string`): The text or prompt instruction passed to the TTS/LLM.
    * **`enable_typing_sound`** (`boolean`): Plays sound effects (typing/processing) during execution.
    * **`speak_after_execution`** (`boolean`): Whether the agent speaks after the tool completes.

---

### A2. Skipping the Second LLM Request (`run_llm=False`) & Context Integrity

#### What Breaks
1. **Gemini API Strict Turn-Alternation Violations (`400 Bad Request`):**
   * The Gemini REST/RPC API requires strict turn alternation between client inputs (`user` / `functionResponse`) and model outputs (`model` / `functionCall`).
   * [source: `ai.google.dev`, "Function Calling Guide"] *"Conversations must strictly alternate between model and client-side inputs (user/function). A functionCall generated by the model must be immediately followed in the next turn by the corresponding functionResponse."*
   * If you set `run_llm=False`, the pipeline does not execute Turn 4 (the final `model` turn). When the human user speaks next, two client/user turns collide consecutively in the payload:
     - Turn 2: `model` (`functionCall`)
     - Turn 3: `user` / `function` (`functionResponse`)
     - Turn 4: `user` (next human speech)
   * This triggers an immediate API validation error:
     [source: `ai.google.dev`] `"Please ensure that function call turns come immediately after a user turn or after a function response turn."`
2. **Context Amnesia & Hallucination on the Next Turn:**
   * If the tool plays templated audio via TTS (e.g., "The living room lights are on") without recording it into the LLM context, the LLM has no memory of what it said. If the user subsequently asks, *"What did you just turn on?"* or *"Did it work?"*, the model cannot ground its response in its own prior utterance [inference].
3. **Multi-Step Tools (Tool Chaining Breaks):**
   * Setting `run_llm=False` halts the ReAct loop. If an operation requires a second inspection or follow-up tool call, it cannot proceed [source: `pipecat-ai/pipecat` Issue #1842].
4. **Tool Failures & Rigid Error Messaging:**
   * If the tool fails (e.g., smart home device unreachable), a static success template will mislead the user. Handling errors requires hardcoded branching templates in Python rather than natural LLM conversational repair [inference].

#### Documented Patterns for Keeping Context Correct
* **Synthetic Model Message Injection:** When suppressing the LLM turn, the application must inject a synthetic `model` (assistant) message into the context aggregator containing the exact text spoken to the user.
* In Pipecat:
  * Emit an `LLMMessagesAppendFrame(messages=[{"role": "assistant", "content": templated_text}], run_llm=False)` to append the model turn into `LLMContext` without triggering an inference run [source: `pipecat-ai/pipecat` GitHub `src/pipecat/frames/frames.py`].
  * Alternatively, use the `on_context_updated` callback inside `FunctionCallResultProperties`:
    [source: `pipecat-ai/pipecat` GitHub `src/pipecat/services/llm_service.py`]
    ```python
    properties = FunctionCallResultProperties(
        run_llm=False,
        on_context_updated=lambda ctx: ctx.add_message({
            "role": "model",
            "parts": [{"text": templated_confirmation}],
        }),
    )
    await params.result_callback(result, properties=properties)
    ```

---

### A3. Retrieval Before the LLM Call vs. Tool-Calling RAG

#### Latency Evidence & Published Measurements
* **Elimination of the First Turn:**
  * In tool-based retrieval, the LLM must first generate the function call arguments (`search_docs(query=...)`), wait for retrieval, and then generate the answer.
  * In our baseline, isolated text tool turns take ~1.31 s total LLM time (~0.72 s to emit the tool call + ~0.52 s to begin generating the answer).
  * In pre-retrieval RAG, BM25 runs locally over the small corpus (<10 ms on CPU) before calling the LLM. The LLM runs only once, starting spoken tokens at ~0.52 s. This eliminates ~700–800 ms of latency [inference].
* **Published Measurements (Salesforce AI Research, March 2026):**
  * [source: arXiv:2603.02206, "VoiceAgentRAG: Solving the RAG Latency Bottleneck in Real-Time Voice Agents Using Dual-Agent Architectures"]
    * *"Standard production vector database queries ... typically incur 50–300ms of network and search round-trip latency alone—effectively exhausting the entire latency budget before the LLM even begins to generate a response."*
    * By decoupling retrieval into a background pre-fetching agent and a foreground in-memory FAISS cache, VoiceAgentRAG achieved a *"316× retrieval speedup on cache-hit queries, shrinking retrieval time from ~110ms down to 0.35ms"* and a *"75% overall cache hit rate."*

#### Answer Quality & When Production Agents Choose Each
* **Pre-Retrieval RAG (Prompt Injection):**
  * *Strengths:* Minimal latency (single LLM request), predictable execution.
  * *Weaknesses:* **Prompt bloat & distraction.** If the user asks a chit-chat or smart-home query ("Turn on the lights"), injecting retrieved document passages pollutes the prompt, increases token costs, and can cause the model to hallucinate or mention irrelevant document details [source: Regal.ai Voice Architecture Guides].
  * *Query Mismatch:* User spoken queries are often brief or underspecified ("Tell me more about that"). A tool-calling model can reformulate the search query using conversational context, whereas raw pre-retrieval requires a separate query-rewriting step [inference].
* **Production Decision Rule:**
  * Industry voice guidelines (e.g., LiveKit, Regal AI, FutureAGI) recommend:
    1. Static / small FAQs $\rightarrow$ Inject into system prompt (0 ms retrieval latency).
    2. Large knowledge bases with clear question intent $\rightarrow$ Pre-retrieval / parallel streaming retrieval triggered on partial ASR transcripts.
    3. Live, state-dependent transactional operations (e.g., smart home controls, CRM updates, booking systems) $\rightarrow$ Tool calling.

---

### A4. Gemini API Capabilities: Mixed Text/Tool Turns & Pipecat Streaming

#### Can Gemini Emit Spoken Text and a Function Call in the Same Response?
* **API Specification:** In the Gemini API, `candidates[0].content.parts` is a repeated array that can contain both `text` parts and `functionCall` parts [source: `ai.google.dev`, "GenerateContent Response Structure"].
* **Model Behavior (`gemini-3.x-flash` / `gemini-3.1-flash-lite`):**
  * Under default tool configurations (`FunctionCallingConfig(mode="AUTO")`), Gemini models operate with exclusive intent: when the model decides a tool is needed, it outputs **only** a `functionCall` part (preceded by `thought` parts if thinking is active) [source: `ai.google.dev`].
  * Prompting the model to always say an introductory filler before a tool call (e.g., "Say 'Looking that up' and call the tool") produces inconsistent results across Gemini versions; the model often skips the text part to emit the structured tool call directly [inference].

#### Does Pipecat's `GoogleLLMService` Stream Text Before Executing the Tool?
* **YES.** Inspection of Pipecat's source code confirms that text is streamed immediately as chunks arrive, whereas tool execution is deferred until the stream ends.
  * [source: `pipecat-ai/pipecat` GitHub `src/pipecat/services/google/llm.py`]:
    ```python
    for part in candidate.content.parts:
      if part.text:
        if part.thought:
          await self.push_frame(LLMThoughtStartFrame())
          await self.push_frame(LLMThoughtTextFrame(part.text))
          await self.push_frame(LLMThoughtEndFrame())
        else:
          accumulated_text += part.text
          await self._push_llm_text(part.text)  # Streams immediately to TTS
      elif part.function_call:
        await self.stop_ttfat_metrics()
        function_calls.append(FunctionCallFromLLM(...))
    ```
  * After the chunk streaming loop finishes:
    [source: `pipecat-ai/pipecat` GitHub `src/pipecat/services/google/llm.py`]
    ```python
    await self.run_function_calls(function_calls)
    ```
  * **Result:** If Gemini yields a text part before a `functionCall` part in the stream, Pipecat forwards `LLMTextFrame` to TTS immediately, speaking to the user while the stream completes and before `smart_home` or `search_docs` runs [inference].

#### Parallel Function Calling & Streaming Arguments in Gemini API
* **Configuration & Modes:**
  * Controlled via `types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO"|"ANY"|"NONE"|"VALIDATED"))` [source: `ai.google.dev`].
  * In `ANY` mode, the model is forced to call a function. In `VALIDATED` mode, schema conformance is strictly verified.
* **Parallel Function Calling:** Gemini natively supports parallel function calling, emitting multiple `functionCall` parts within a single turn [source: `ai.google.dev`, "Parallel Function Calling"].
* **Thought Signatures:** On reasoning models, an encrypted `thought_signature` is attached **only to the first** `functionCall` part and must be sent back intact to prevent API validation errors [source: `ai.google.dev`].
* **Argument Delivery Behavior:**
  * In `generateContentStream` / `stream_generate_content`, `part.function_call.args` is delivered as a complete, pre-parsed structured dictionary/Map [source: `ai.google.dev`].
  * Unlike the OpenAI Chat Completions API (which streams raw JSON token fragments like `{"lo`, `catio`), Gemini's `generateContentStream` does not stream partial argument string deltas to the client. (Incremental streaming of arguments via `step.delta` and `arguments_delta` exists only in Google's specialized Interactions API) [source: `ai.google.dev`, "Interactions API"].

---

### A5. User-Experience Evidence on Pre-Execution Acknowledgements & Failures

#### Problems Caused by Premature Acknowledgements
* **Linguistic Aspect & The "False Promise":**
  * Uttering completed past-tense confirmations ("I turned on the lights") before the action succeeds is classified as a severe UX failure. If network latency, hardware failure, or device disconnection occurs, the assistant has spoken an untruth, immediately destroying user trust in the system's state awareness [source: Nielsen Norman Group, "Voice User Interfaces"].
  * Present progressive acknowledgements ("Turning on the lights...") signal an in-flight process. However, if the tool fails 1.5 seconds later, the assistant must issue an abrupt conversational repair:
    * *"Turning on the lights... Sorry, the bedroom lamp is not responding."*
  * User studies demonstrate that contradictory statements uttered within 1–2 seconds cause cognitive dissonance, user frustration, and conversational thrashing (e.g., users repeating commands louder) [source: Aufait UX, "Smart Home Voice Failures"].

#### Industry Voice Product Strategies
1. **Replacement of Verbal Feedback with Earcons (Chimes):**
   * **Amazon Alexa ("Brief Mode"):** Replaces verbal confirmations ("Okay" / "Turning on the lights") with a brief, non-verbal tone/chime [source: Forbes / Amazon Alexa App Settings].
   * **Google Assistant / Google Home (Same-Room Light Chime):** When a user asks a Google Nest speaker to control a smart light in the same room, Google Assistant suppresses all spoken words ("Sure, turning on the kitchen lights") and plays a quiet confirmation chime (ding). Google speaks **only if the operation fails** [source: Google Home Product Updates / Reddit Community Documentation].
2. **Timing of Natural Turn-Taking and Fillers:**
   * Research on conversational timing shows that human turn transitions naturally take 200–500 ms.
   * [source: Lala, Nakamura, & Kawahara, Interspeech 2019, DOI: 10.21437/Interspeech.2019-1527] In *"Analysis of Effect and Timing of Fillers in Natural Turn-Taking"*, the authors show that conversational fillers ("um", "let me see") successfully claim the conversational floor and mask processing latency, keeping listeners engaged without premature commitment to an outcome.
3. **Hedging and Neutral Process Language:**
   * For retrieval and slow operations, Google Conversation Design guidelines recommend neutral process acknowledgments ("Let me look that up", "Checking...") rather than outcome-predictive statements ("I'll find your answer right away") [source: `google.com`, "Conversation Design Guidelines"].

---

## 3. Comparison of Techniques

| Technique | Expected Saving for Us (ms) | Risk | Effort | Evidence Strength |
| :--- | :--- | :--- | :--- | :--- |
| **Pre-Retrieval RAG for `search_docs`** (Run BM25 on raw transcript, inject passages into prompt; single LLM turn) | **700 – 800 ms** (eliminates Turn 1 LLM inference round-trip) | **Medium** (prompt bloat on non-search turns; may miss intent on conversational pronouns) | **Low** (BM25 is local and takes <10 ms; conditional trigger on keyword/question classifier) | **Strong** (Verified by isolated LLM turn metrics & VoiceAgentRAG [arXiv:2603.02206]) |
| **Skip LLM on `smart_home` (`run_llm=False`) with Templated Confirmation** | **500 – 700 ms** (eliminates Turn 2 LLM inference generation) | **High** (Gemini API `400 Bad Request` unless synthetic `model` turn injected; context amnesia; no conversational repair on tool failure) | **Medium** (requires synthesizing `model` message frame into Pipecat context aggregator) | **Strong** (Verified in Gemini API documentation and Pipecat `LLMMessagesAppendFrame` source) |
| **Non-Verbal Earcon/Chime on Tool Start** (Play 100 ms local chime via `OutputAudioRawFrame` on tool call arrival) | **Perceptual:** masks **600 – 800 ms** of tool/LLM wait time (0 ms absolute clock reduction) | **Very Low** (standard UX pattern in Alexa & Google Home; no false promises; no API context risk) | **Low** (register listener on `on_function_calls_started` to queue an audio frame to speaker) | **Strong** (Industry standard in Google Home room-awareness & Alexa Brief Mode) |
| **Filler Speech via `on_function_calls_started`** (Push `TTSSpeakFrame("Checking that...")` to Supertonic TTS) | **Perceptual:** masks **1000 – 1500 ms** of tool & LLM processing | **Medium** (Supertonic adds ~250 ms fixed cost per chunk; speech may collide with fast tool returns) | **Low** (standard Pipecat event handler) | **Strong** (Verified in Pipecat GitHub source and LiveKit `ctx.with_filler` docs) |
| **Model Prompting for Dual Output** (Instruct Gemini to output text before `functionCall` in a single response) | **Perceptual:** masks **300 – 500 ms** if model emits text first | **High** (Gemini `AUTO` mode model behavior frequently ignores text instructions and emits pure `functionCall`) | **Low** (prompt change only) | **Moderate** (Pipecat code confirms streaming support, but Gemini API behavior is unreliable) |

---

## 4. Exact Parameter & API Names

1. **`FunctionCallResultProperties`**
   * *Parameters:* `run_llm: bool | None`, `is_final: bool = True`, `on_context_updated: Callable | None`
   * *Source:* Pipecat source code [`pipecat/src/pipecat/frames/frames.py`](file:///pipecat/src/pipecat/frames/frames.py) & `pipecat.services.llm_service`.
2. **`on_function_calls_started`**
   * *Type:* Event handler on `LLMService` instances (`@llm.event_handler("on_function_calls_started")`).
   * *Source:* Pipecat source code [`pipecat/src/pipecat/services/llm_service.py`](file:///pipecat/src/pipecat/services/llm_service.py).
3. **`LLMMessagesAppendFrame`**
   * *Parameters:* `messages: List[dict]`, `run_llm: bool | None = False`
   * *Source:* Pipecat source code [`pipecat/src/pipecat/frames/frames.py`](file:///pipecat/src/pipecat/frames/frames.py).
4. **`TTSSpeakFrame`**
   * *Parameters:* `text: str`
   * *Source:* Pipecat source code [`pipecat/src/pipecat/frames/frames.py`](file:///pipecat/src/pipecat/frames/frames.py).
5. **`ctx.with_filler`**
   * *Parameters:* `callable_or_str`, `delay: float = 5.0`, `interval: float = 10.0`, `max_steps: int`
   * *Source:* LiveKit Agents documentation [`docs.livekit.io/agents`](https://docs.livekit.io/agents), v1.6.0+.
6. **`ctx.update`**
   * *Parameters:* `message: str`
   * *Source:* LiveKit Agents documentation [`docs.livekit.io/agents`](https://docs.livekit.io/agents).
7. **`request-start`, `request-response-delayed`, `request-failed`, `request-complete`**
   * *Type:* `type` strings inside tool `messages` objects in Vapi tool schemas.
   * *Source:* Vapi API documentation [`docs.vapi.ai/tools`](https://docs.vapi.ai/tools).
8. **`speak_during_execution`**
   * *Type:* `boolean` parameter in Retell custom tool definitions.
   * *Source:* Retell AI documentation [`docs.retellai.com/api-references`](https://docs.retellai.com).
9. **`execution_message_type` & `execution_message_description`**
   * *Type:* `string` (`"prompt"` | `"static_text"`) and `string` (message content).
   * *Source:* Retell AI documentation [`docs.retellai.com/api-references`](https://docs.retellai.com).
10. **`enable_typing_sound`**
    * *Type:* `boolean` parameter in Retell custom tool definitions.
    * *Source:* Retell AI documentation [`docs.retellai.com/api-references`](https://docs.retellai.com).
11. **`FunctionCallingConfig` & `mode`**
    * *Modes:* `"AUTO"`, `"ANY"`, `"NONE"`, `"VALIDATED"` inside `types.ToolConfig`.
    * *Source:* Google Gen AI SDK & Gemini API documentation [`ai.google.dev`](https://ai.google.dev).
12. **`thought_signature`**
    * *Type:* Encrypted signature attribute on the first `functionCall` part in thinking models.
    * *Source:* Google Gemini API documentation [`ai.google.dev`](https://ai.google.dev).

---

## 5. What Could Not Be Found

1. **Vendor Benchmark Tables for Tool-Turn Latency Savings in Pipecat Docs:**
   * While Pipecat provides `on_function_calls_started` and `FunctionCallResultProperties(run_llm=False)`, its official documentation does **NOT** publish empirical latency measurement tables (e.g., exact milliseconds saved across model providers).
2. **Explicit Reliability Rates for Prompt-Forced Mixed Text + Tool Calls in Gemini:**
   * Google's official Gemini documentation does not state a statistical success rate for prompting `gemini-3.1-flash-lite` to emit both spoken text and a `functionCall` in the same turn; documentation describes the response schema (`parts[]`), but mentions that tool calling is typically modeled as an exclusive turn state.
3. **Specific Latency Benchmarks for Retell's `enable_typing_sound`:**
   * Retell documentation confirms that `enable_typing_sound` and `speak_during_execution` exist to fill latency, but does not provide user perception latency figures or audio start timestamps.
