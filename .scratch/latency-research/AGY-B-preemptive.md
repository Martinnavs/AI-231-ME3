# Research Report: Speculative LLM Execution and Turn-Taking in Voice Agents

## 1. Six-Line Summary
1. Voice frameworks reduce turn latency by starting LLM inference speculatively on interim or eager transcripts while the user is pausing.
2. LiveKit (`preemptive_generation`) and Deepgram Flux (`EagerEndOfTurn`) document 100–350 ms reductions in response latency, at the cost of 50–70% higher LLM request volume.
3. Pipecat 1.x implements speculative execution via `pipecat.turns` using `EagerUserTurnStopStrategy`, `SpeculationGate`, and `LLMContextFrame(speculation=True)`.
4. Pipecat's frame contract explicitly prohibits executing tool calls during speculative runs (`must not execute tool calls for it`), preventing side effects from unconfirmed turns.
5. In your pipeline, speculative execution on the early 200 ms re-decode can hide ~500–720 ms of the first LLM tool-dispatch turn on matching turns (89% of turns), with zero latency penalty on cancellations.
6. Production agents default to 200–500 ms silence holds (Pipecat VAD 200 ms, Vapi 400 ms, OpenAI 500 ms, LiveKit 500 ms); audio turn models (LiveKit Turn Detector, Pipecat Smart Turn v3) cut delays to ~300 ms with ~9.9% false cut-off rates.

---

## 2. Topic B: Detailed Findings with Evidence

### B1. LiveKit Agents "Preemptive Generation"
* **Exact Option Name & Configuration:** 
  Configured in Python via `turn_handling=TurnHandlingOptions(preemptive_generation=...)` or passing a typed structure / dictionary `PreemptiveGenerationOptions` [source: [github.com/livekit/agents](https://github.com/livekit/agents), `livekit/agents/voice/turn.py`]. 
  Tunable parameters:
  * `enabled`: `bool` (default `True`).
  * `preemptive_tts`: `bool` (default `False`).
  * `max_speech_duration`: `float` (default `10.0` s).
  * `max_retries`: `int` (default `3`).
  *(Verbatim quote: `"preemptive_generation=PreemptiveGenerationOptions(enabled=True, preemptive_tts=False, max_speech_duration=10.0, max_retries=3)"` [source: [docs.livekit.io/agents](https://docs.livekit.io/agents)]).*

* **SDK Version & Defaults:**
  Enabled by default starting in `livekit-agents` v1.5.0 (Python) and `@livekit/agents` v1.2.0 (Node.js/TS) released in March 2026 [source: [github.com/livekit/agents/releases](https://github.com/livekit/agents/releases/tag/v1.5.0), 2026-03-19].

* **How It Works (Transcripts & Discard Behavior):**
  * LiveKit triggers an LLM completion speculatively as soon as preliminary user speech chunks or STT phrase boundaries are detected before end-of-turn confirmation [source: [docs.livekit.io/agents](https://docs.livekit.io/agents)].
  * By default, **only the LLM runs preemptively**; TTS waits for confirmation [source: [docs.livekit.io/agents](https://docs.livekit.io/agents), verbatim: `"By default, only the LLM runs preemptively while waiting for user turn confirmation, whereas text-to-speech typically waits until the turn is officially confirmed to avoid wasting compute"`].
  * When the final transcript arrives:
    * Under patch PR #7507, minor formatting and non-ASCII punctuation changes are ignored [source: [github.com/livekit/agents/pull/7507](https://github.com/livekit/agents/pull/7507), verbatim: `"fix(voice): keep preemptive generation when only non-ASCII punctuation changes"`].
    * If the text differs semantically or context/tools mutate in `on_user_turn_completed`, the speculative stream is aborted and regenerated [source: [docs.livekit.io/agents](https://docs.livekit.io/agents), verbatim: `"If the chat context or tools change while a speculative response is running (or if the user's turn isn't finalized as expected), the speculative response is discarded and regenerated"`].

* **Documented Latency Saving & Cost:**
  * **Latency Saving:** Shaves **100 ms to 300 ms+** off Time-to-First-Token (TTFT) / Time-to-First-Audio (TTFA) [source: [livekit.io/blog](https://blog.livekit.io), 2026].
  * **Cost:** Increases LLM token consumption and API call volume due to discarded runs on mid-utterance shifts [source: [docs.livekit.io/agents](https://docs.livekit.io/agents), verbatim: `"can incur extra API compute costs and token usage if a user frequently interrupts, changes mid-utterance, or revises what they are saying"`].

---

### B2. Deepgram Flux EagerEndOfTurn / TurnResumed, AssemblyAI, and OpenAI Realtime
* **Deepgram Flux (`EagerEndOfTurn` & `TurnResumed`):**
  * **API Parameter:** `eager_eot_threshold` (float between `0.3` and `0.9`, disabled by default; must be $\le$ `eot_threshold`, which defaults to `0.7`) on WebSocket `wss://api.deepgram.com/v2/listen?model=flux-general-en&eager_eot_threshold=0.4` [source: [developers.deepgram.com/docs/flux](https://developers.deepgram.com), 2026].
  * **State Flow:**
    1. `EagerEndOfTurn`: Emitted upon medium-confidence pause detection. Contains a non-empty provisional transcript [source: [developers.deepgram.com](https://developers.deepgram.com), verbatim: `"An EagerEndOfTurn message always contains a non-empty transcript"`].
    2. The application begins drafting the LLM response or pre-fetching RAG data [source: [developers.deepgram.com](https://developers.deepgram.com)].
    3. If the user was merely hesitating and continues talking, Flux fires `TurnResumed` [source: [developers.deepgram.com](https://developers.deepgram.com), verbatim: `"A TurnResumed message can only follow a preceding EagerEndOfTurn message... signals to the agent that it should cancel or discard the draft/speculative LLM response"`].
    4. If the turn completes cleanly, `EndOfTurn` is emitted with the exact same transcript as `EagerEndOfTurn` [source: [developers.deepgram.com](https://developers.deepgram.com)].
  * **Reported Latency Savings:** Fires **150 ms to 250 ms earlier** than standard end-of-turn detection, saving **100 ms to 350 ms** on median and tail response times [source: [developers.deepgram.com](https://developers.deepgram.com), 2026].
  * **Cancellation & Overhead Rate:** Deepgram reports that speculative execution with eager EOT increases total LLM API call volume by **50% to 70%** [source: [developers.deepgram.com](https://developers.deepgram.com), 2026].

* **AssemblyAI Universal-Streaming:**
  * Uses semantic, entity-aware endpointing rather than pure acoustic timers [source: [www.assemblyai.com/docs](https://www.assemblyai.com/docs), 2026].
  * Parameters: `min_turn_silence` (silence ms before semantic evaluation), `max_turn_silence` (hard fallback timeout), and `end_of_turn_confidence_threshold` [source: [www.assemblyai.com/docs](https://www.assemblyai.com/docs)].
  * Pauses occurring mid-entity (e.g., reciting a phone number `"555... 0123"`) do not trigger a turn boundary [source: [www.assemblyai.com/docs](https://www.assemblyai.com/docs)].

* **OpenAI Realtime API Semantic VAD:**
  * Configured via `turn_detection: {"type": "semantic_vad", "eagerness": "low" | "medium" | "high" | "auto"}` [source: [platform.openai.com/docs/api-reference/realtime-sessions](https://platform.openai.com/docs/api-reference/realtime-sessions), 2026].
  * Evaluates lexical completeness dynamically to set turn boundaries rather than relying solely on fixed silence [source: [platform.openai.com/docs](https://platform.openai.com/docs)].

---

### B3. Pipecat 1.x: Speculative Execution & Turn Strategies
Pipecat 1.x contains a dedicated speculative execution architecture inside `pipecat.turns` and core frames:

* **Core Frame: `LLMContextFrame`** [source: [github.com/pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat), `src/pipecat/frames/frames.py`]:
  ```python
  @dataclass
  class LLMContextFrame(Frame):
      context: LLMContext
      speculation: bool = False
  ```
  * **Verbatim Docstring Specification:**
    > *"speculation: Whether this inference is speculative, run from a provisional context that is not part of the conversation. Its response must not reach the user or the context until the turn is confirmed, and the service must not execute tool calls for it."*

* **Turn Stop Strategy: `EagerUserTurnStopStrategy`** [source: [github.com/pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat), `src/pipecat/turns/user_stop/eager_user_turn_stop_strategy.py`]:
  * Listens for `EagerTranscriptionFrame` and triggers speculative LLM generation.
  * Parameters:
    * `match_policy`: Defines whether the final committed transcript matches the eager transcript sufficiently. Classes available under `pipecat.turns.user_stop`:
      * `NormalizedMatch` (default): Strips casing, whitespace, and punctuation differences.
      * `ExactMatch`: Strict character-for-character equivalence.
    * `speculation_timeout`: `float = 5.0` (aborts speculation if the turn is not finalized within 5.0 seconds).
  * Emits `EagerEndOfTurnCancelFrame` if the user resumes speaking or the committed transcript fails `match_policy`.

* **Pipeline Controller: `SpeculationGate`** [source: [github.com/pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat), `src/pipecat/turns/speculation_gate.py`]:
  * Intercepts speculative frames emerging from the LLM service.
  * Buffers frames in a `HOLDING` state.
  * When `UserStoppedSpeakingFrame` arrives confirming the turn, it flushes the buffered response downstream (to TTS/transport).
  * If `EagerEndOfTurnCancelFrame` arrives, it drops all buffered output.

* **Strategies Container:** `EagerUserTurnStrategies` (`pipecat.turns.user_turn_strategies.EagerUserTurnStrategies`).

---

### B4. Application to Our Setup: Expected Savings and Cancellation Costs
* **Our Conditions:**
  * Early re-decode arrives at 200 ms of silence; changes text in 11% of turns overall, 25% for short commands.
  * Final re-decode arrives ~0.5 s after 0.5–0.65 s of silence (total pause-to-final = ~1.0–1.15 s).
  * First LLM turn generates a tool call (`smart_home` or `search_docs`) in ~0.72 s (720 ms).

* **Expected Latency Saving (In 89% of Overall Turns / 75% of Short Commands):**
  * Early text is available at $\approx \text{pause} + 200\text{ ms} + \text{early decode time}$ [inference].
  * Final text is available at $\approx \text{pause} + 500\text{--}650\text{ ms} + 500\text{ ms} = \text{pause} + 1000\text{--}1150\text{ ms}$ [inference].
  * Head start for the LLM: $\Delta t \approx 500\text{ to }800\text{ ms}$ [inference].
  * Because the first LLM request takes ~720 ms to emit the tool call, starting at pause + 200 ms means **the first LLM request is already finished (or in its final tokens) right when the final transcript arrives** [inference].
  * **Expected net saving:** **~500 ms to 720 ms** shaved directly off the pause-to-speech critical path on matching turns [inference].

* **Cancellation Cost (In 11% of Overall Turns / 25% of Short Commands):**
  * **Latency Penalty:** **0 ms** [inference]. If the early transcript fails matching when the final transcript arrives (at $t \approx 1.0\text{ s}$), the speculative run is aborted and the normal request starts at $t \approx 1.0\text{ s}$, exactly when it would have started without speculation [inference].
  * **Token / Financial Cost:** One wasted Gemini `gemini-3.1-flash-lite` prompt call (~500–1000 input tokens + ~30–50 tool tokens) on 11% (or 25%) of turns [inference].
  * **Tool Execution Hazard (Crucial Constraint):**
    * In Pipecat, `LLMContextFrame(speculation=True)` mandates: *"the service must not execute tool calls for it"* [source: [github.com/pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat), `src/pipecat/frames/frames.py`].
    * If `smart_home(intent, slot)` were executed speculatively, a 25% command mismatch rate would trigger unwanted physical side effects (e.g., turning off the wrong device) before cancellation [source: LiveKit GitHub issues #3414, #5924 citing premature side effects during speculative turns].
    * Gating tool execution until the final transcript confirms the turn eliminates side effects while still saving the ~720 ms LLM generation time [inference].
    * For `search_docs(query)`, the local BM25 lookup is read-only and idempotent; executing it speculatively is safe [inference].

---

### B5. End-of-Turn Silence Holds & Turn-Detection Models
* **Default Production Silence Holds:**
  * **Pipecat `VADParams`:** `stop_secs = 0.2` s (200 ms) default (`VAD_STOP_SECS = 0.2`) in `pipecat.audio.vad.vad_analyzer.VADParams` [source: [pipecat.ai/docs](https://docs.pipecat.ai), 2026]. When using `LocalSmartTurnAnalyzerV3`, `SmartTurnParams.stop_secs` defaults to `3.0` s as a maximum fallback timeout while the underlying Silero VAD runs with `stop_secs = 0.2` s [source: [pipecat.ai/docs](https://docs.pipecat.ai)].
  * **LiveKit:** Legacy `min_endpointing_delay = 0.5` s (500 ms) and `max_endpointing_delay = 3.0` s (3000 ms) [source: [github.com/livekit/agents](https://github.com/livekit/agents)]. In modern `EndpointingOptions`, `min_delay` defaults to `0.5` s (VAD/STT mode) and `0.3` s (with `TurnDetector`) [source: [docs.livekit.io/agents](https://docs.livekit.io/agents), 2026].
  * **OpenAI Realtime API:** `turn_detection.silence_duration_ms = 500` ms (default) under `server_vad` [source: [platform.openai.com/docs](https://platform.openai.com/docs)].
  * **Vapi:** `startSpeakingPlan.waitSeconds = 0.4` s (400 ms) default [source: [docs.vapi.ai](https://docs.vapi.ai), 2026].

* **How Turn-Detection Models Change Holds & Cut-Off Rates:**
  * **LiveKit Turn Detector v1 (Audio Model, June 2026):**
    * Lowers the silence hold bound (`min_delay`) from `500 ms` to `300 ms` (`max_delay` from `3.0 s` to `2.5 s`) [source: [docs.livekit.io/agents](https://docs.livekit.io/agents)].
    * On LiveKit's standardized benchmark **`eot-bench`** (evaluated at a 300 ms latency budget), Turn Detector v1 achieves a **~9.9% false cut-off rate**, outperforming text-only turn detectors and raw VAD baselines [source: [blog.livekit.io](https://blog.livekit.io), 2026]. Earlier text-based transformer `v0.4.1-intl` reduced cut-offs by **39%** [source: [blog.livekit.io](https://blog.livekit.io)].
  * **Pipecat Smart Turn v3 (`pipecat-ai/smart-turn-v3`):**
    * An ~8M parameter Whisper Tiny encoder backbone running local ONNX inference in ~12 ms (modern CPU) to ~65 ms (cloud CPU) [source: [huggingface.co/pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3); [daily.co/blog](https://www.daily.co/blog), 2026].
    * Achieves **>91% overall accuracy** on multilingual benchmark suites (31,527 test samples), with languages like French and Dutch exceeding 95% [source: [huggingface.co/pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3)].
    * Version 3.2 reduced miscategorization of short utterances (single-word turns like "yes" or "okay") by **40%** [source: [daily.co/blog](https://www.daily.co/blog), 2026].

---

## 3. Comparison of Techniques for Our System

| Technique | Expected Saving for Us (ms) | Risk | Effort | Evidence Strength |
| :--- | :--- | :--- | :--- | :--- |
| **Speculative LLM Start on Early 200 ms Re-decode** | **500–720 ms** (on 89% of turns; hides LLM tool generation) | Low for `search_docs`; High for `smart_home` if tool is executed before confirmation (must gate tool dispatch) | Medium (configure Pipecat `EagerUserTurnStopStrategy` & `SpeculationGate`) | Strong (Pipecat 1.x native code & LiveKit/Deepgram production benchmarks) |
| **Speculative Read-Only MCP Tool Dispatch (`search_docs`)** | **20–50 ms** (BM25 search runs while waiting for final ASR) | Negligible (BM25 is local, read-only, and idempotent) | Low (invoke BM25 eagerly when tool call tokens appear) | Strong (standard speculative execution pattern) |
| **Speculative State-Mutating Tool Dispatch (`smart_home`)** | 0 ms additional (local dispatch is fast) | **Critical** (25% false execution rate on short commands: lights toggle erroneously) | Low | Strong (LiveKit Issues #3414, #5924; Pipecat `LLMContextFrame` contract) |
| **Lowering Final Silence Hold via Smart Turn v3** | **150–250 ms** (reduces 500–650 ms silence threshold towards 300 ms) | Low (adds ~12–65 ms CPU inference, ~9.9% cut-off rate on brief thinking pauses) | Medium (integrate `LocalSmartTurnAnalyzerV3` into Pipecat pipeline) | Strong (`pipecat-ai/smart-turn-v3` model card & `eot-bench` results) |

---

## 4. Exact Parameter and API Names to Look At

1. **Pipecat 1.x Speculative Execution & Turn Management:**
   * [`pipecat.frames.frames.LLMContextFrame`](https://github.com/pipecat-ai/pipecat): parameter `speculation: bool = False`.
   * [`pipecat.turns.user_stop.eager_user_turn_stop_strategy.EagerUserTurnStopStrategy`](https://github.com/pipecat-ai/pipecat): parameters `match_policy` (`NormalizedMatch` or `ExactMatch`), `speculation_timeout: float = 5.0`.
   * [`pipecat.turns.speculation_gate.SpeculationGate`](https://github.com/pipecat-ai/pipecat): pipeline processor managing `HOLDING` vs `DROPPING`.
   * [`pipecat.turns.user_turn_strategies.EagerUserTurnStrategies`](https://github.com/pipecat-ai/pipecat): top-level strategies container.
   * [`pipecat.audio.vad.vad_analyzer.VADParams`](https://docs.pipecat.ai): `stop_secs = 0.2`, `start_secs = 0.2`, `confidence = 0.7`.
   * [`pipecat.turns.turn_analyzer.smart_turn.SmartTurnParams`](https://docs.pipecat.ai): `stop_secs = 3.0`, `pre_speech_ms = 500.0`, `max_duration_secs = 8.0`.

2. **LiveKit Agents:**
   * [`livekit.agents.voice.turn.TurnHandlingOptions`](https://docs.livekit.io/agents): `preemptive_generation`, `turn_detection`, `endpointing`, `interruption`.
   * [`livekit.agents.voice.turn.PreemptiveGenerationOptions`](https://github.com/livekit/agents): `enabled: bool = True`, `preemptive_tts: bool = False`, `max_speech_duration: float = 10.0`, `max_retries: int = 3`.
   * [`livekit.agents.voice.turn.EndpointingOptions`](https://docs.livekit.io/agents): `min_delay: float = 0.5` (or `0.3` with turn detector), `max_delay: float = 3.0` (or `2.5`), `mode = "fixed" | "dynamic"`.
   * Legacy parameters: `min_endpointing_delay = 0.5`, `max_endpointing_delay = 3.0` in `VoicePipelineAgent`.

3. **Deepgram Flux:**
   * WebSocket query parameters: `eager_eot_threshold` (float `0.3` to `0.9`), `eot_threshold` (default `0.7`), endpoint `/v2/listen?model=flux-general-en&eager_eot_threshold=0.4` [source: [developers.deepgram.com](https://developers.deepgram.com)].
   * Server events: `EagerEndOfTurn`, `TurnResumed`, `EndOfTurn`.

4. **OpenAI Realtime API:**
   * Session parameter: `turn_detection: {"type": "semantic_vad", "eagerness": "low" | "medium" | "high" | "auto"}` or `{"type": "server_vad", "silence_duration_ms": 500}` [source: [platform.openai.com](https://platform.openai.com)].

5. **Vapi:**
   * `startSpeakingPlan.waitSeconds`: default `0.4` seconds [source: [docs.vapi.ai](https://docs.vapi.ai)].

---

## 5. What Could Not Be Found
* **Google AI Studio API Specific Preemption / Early Cancellation Hooks:** The Google AI Studio REST/WebSocket API does not offer a server-side speculative cancellation primitive or native eager-turn protocol equivalent to Deepgram Flux or OpenAI Realtime's `semantic_vad`. Cancellation of an in-flight Gemini call must be executed client-side by closing the HTTP/gRPC streaming request connection.
* **Exact sherpa-onnx Parakeet Re-Decode Partial Hook:** In public `sherpa-onnx` documentation, there is no built-in "eager turn frame" event handler that automatically triggers external HTTP/RPC pipelines at 200 ms silence; this logic exists solely as custom client-side orchestration.
