# 10: Agent core — fast voice agent, `delegate` tool, subagent runner

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 09; decision **D1** confirmed in `DESIGN.md` (model IDs)
**Status:** ready-for-agent

## Files
- `src/voice_agent/agent/llm.py` (LLM client protocol + Anthropic implementation + scripted fake)
- `src/voice_agent/agent/session.py` (fast user-facing agent)
- `src/voice_agent/agent/delegate.py` (delegation manager + subagent loop)
- `src/voice_agent/agent/prompts.py`
- `tests/agent/test_session.py`, `tests/agent/test_delegate.py`, `tests/agent/test_llm_anthropic.py`, `tests/agent/test_live_agent.py` (`live`)

## Action
Review §2 item 6: **no router**. The fast model answers every turn immediately, streaming text to TTS, and has two kinds of tools: the quick MCP tools (`AgentConfig.fast_tool_prefixes`: home, media, assistant; run inline; `hidden_tools` never shown) and a local `delegate(task, kind)` tool. `delegate` starts a subagent in the background and returns at once, so the fast model can say "on it" in the same turn; when the subagent finishes, the session produces a short spoken follow-up turn.

**Before writing any Anthropic SDK code, invoke the `claude-api` skill and read its Python `README.md`, `streaming.md` and `tool-use.md`.** Facts from that skill this ticket relies on (re-check them there, they override memory):
- Fast model `claude-haiku-4-5`: omit `thinking` entirely (latency); `tool_choice` auto.
- Subagent `claude-sonnet-5-5`: `thinking: {type: "disabled"}` is a **400** on this model — leave thinking adaptive and set `output_config: {"effort": "medium"}`; forced `tool_choice` (`any`/`tool`) is a **400** — use `auto`; enable server-side refusal fallback (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, which needs the `client.beta.messages` path), and always check `stop_reason` (`refusal`, `pause_turn`, `max_tokens`) before reading content.
- Research subagent: Anthropic server tool `{"type": "web_search_20260209", "name": "web_search"}` (D3) — handle `pause_turn` by re-sending.
- Streaming requests with client tools: `eager_input_streaming: true` on each client tool, and validate parsed tool input against its schema before running it (treat failure like invalid JSON → error `tool_result`).
- Parallel tool calls: run them concurrently, return **all** `tool_result` blocks in **one** user message; failed tool → `is_error: true`.
- Prompt caching: keep `tools` (sorted, from `ToolRegistry`) then `system` byte-stable; put a `cache_control` breakpoint after the system prompt; log `usage.cache_read_input_tokens`. Short prompts may fall under the model's minimum cacheable prefix — that is fine; just log it.
- Use SDK types and typed exceptions (`RateLimitError`, `APIConnectionError`, …), not hand-rolled ones.

**Fast agent system prompt** (`prompts.FAST_SYSTEM_PROMPT`, static text only, ≤ 2,200 characters). The professor's bar (2026-10-15): it should feel like talking to a person — natural, warm, not bland or neutral — understand any phrasing, and follow multi-turn requests. It must contain these rules, worded for a model, not copied verbatim (sources: the professor's brief, `FULL-DUPLEX-RESEARCH.md` §5):
1. Persona: a friendly, upbeat home assistant who talks like a person — contractions, natural acknowledgements ("Sure!", "Got it."), light warmth; never robotic or flat.
2. Plain spoken text only: no markdown, asterisks, lists, emojis, code or URLs.
3. Usually one or two short sentences, then stop; longer only when the user asks for detail.
4. Spell out numbers, times, dates and money as they are spoken ("four fifteen PM", "twenty dollars").
5. Understand any phrasing and any format ("January 1, 1990", "the first of January ninety"); convert to what the tool needs yourself, never ask the user to rephrase in a format.
6. If something a tool needs is missing (who to message, what time), ask one short question, then act on the answer.
7. If the user corrects or changes a request mid-action ("no, not that one, play Opalite"), act on the new request immediately (stop or replace the old action); don't apologise or explain.
8. If interrupted, never apologise or mention it; answer the new request directly.
9. Before calling `delegate`, say one short bridging phrase ("Let me look into that."); for quick tools, call them without a lead-in and confirm once done.
10. Never invent facts; use a tool or say you don't know.

Dynamic values (current local time, timezone, default location from config, what's playing) go **after** the cache breakpoint, in a separate system block, so the static prefix stays byte-stable. Keep only the last `history_turns` user/assistant exchanges (review §3 "short prompts").

`AgentSession` builds Anthropic-shaped `system`/`messages`/`tools`. A client for another provider (ticket 18) translates them inside its own `stream_turn`; nothing in `session.py` or `delegate.py` may assume the provider.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| network error / 5xx / 429 on the fast model | SDK retries (`max_retries=2`); client raises `LLMConnectionError`/`LLMRateLimitError`; session yields `AgentEvent.Error` with a short spoken apology text the pipeline can speak ("Sorry, I lost my connection.") |
| `respond()` cancelled (barge-in, speculative cancel) | stream closed promptly; partial assistant text recorded in history as spoken-so-far (caller passes `spoken_chars`); no tool started after cancel |
| fast model calls a quick tool (`home__*`, `media__*`, `assistant__*`) | executed via `ToolRegistry.call`; result fed back; model continues streaming in the same `respond()` |
| fast model calls `delegate` | `DelegationManager.start` returns a task id immediately; tool_result says "started <id>"; event `DelegationStarted` yielded so the pipeline can play a filler |
| more than 2 concurrent delegations | third is rejected with tool_result error "busy, try later" |
| subagent exceeds 8 tool rounds or 180 s | stopped; result = "I couldn't finish that" + whatever it had |
| subagent `stop_reason == "refusal"` (after fallback) | result = neutral "I can't help with that one." |
| subagent result ready while user is mid-turn | queued; `pending_announcements()` delivers it after the current turn ends |
| invalid tool input (schema validation fails) | `tool_result` `is_error` "invalid input: …"; model retries or apologizes |
| history exceeds `history_turns` | oldest exchanges dropped as whole user+assistant(+tool) groups, never splitting a tool_use from its tool_result |
| empty transcript passed to `respond` | yields nothing (pipeline shouldn't call it, but be safe) |

### C — Contracts
```python
# src/voice_agent/agent/llm.py
class AgentEvent:                       # tagged union via dataclasses
    @dataclass(frozen=True)
    class FirstToken: t_mono_ns: int
    @dataclass(frozen=True)
    class TextDelta: text: str
    @dataclass(frozen=True)
    class ToolCall: name: str; arguments: Mapping[str, Any]
    @dataclass(frozen=True)
    class ToolResult: name: str; is_error: bool; duration_s: float
    @dataclass(frozen=True)
    class DelegationStarted: task_id: str; kind: str; task: str
    @dataclass(frozen=True)
    class Done: stop_reason: str; usage: Mapping[str, int]
    @dataclass(frozen=True)
    class Error: spoken_text: str; cause: str

class LLMClient(Protocol):
    def stream_turn(self, *, model: str, system: list[dict[str, Any]], messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator["LLMStreamEvent"]: ...
# LLMStreamEvent: normalized text deltas, completed tool_use blocks, final message (content + stop_reason + usage)

class LLMError(Exception): ...          # provider-neutral; raised by any LLMClient after its own retries
class LLMConnectionError(LLMError): ... # network / 5xx / timeout
class LLMRateLimitError(LLMError): ...  # 429
# AgentSession catches LLMError only, never a provider SDK's exception types (ticket 18 adds a second provider)

class AnthropicLLMClient: ...           # implements LLMClient with anthropic.AsyncAnthropic; client created once at startup; wraps the SDK's typed errors in LLMError subclasses
class ScriptedLLMClient: ...            # implements LLMClient from a list of scripted turns; for tests

# src/voice_agent/agent/session.py
class AgentSession:
    def __init__(self, *, llm: LLMClient, tools: ToolRegistry, delegations: "DelegationManager",
                 config: AgentConfig, stage_logger: StageLogger | None = None) -> None: ...
    def respond(self, user_text: str, *, turn_id: int) -> AsyncIterator[AgentEvent]: ...
    def record_interruption(self, turn_id: int, spoken_chars: int) -> None: ...
    def pending_announcements(self) -> list["DelegationResult"]: ...
    def announce(self, result: "DelegationResult", *, turn_id: int) -> AsyncIterator[AgentEvent]: ...   # short spoken summary turn
    async def warm_up(self) -> None: ...  # opens the HTTP connection pool (e.g. a cheap models.retrieve call is NOT allowed in tests; real impl only)

# src/voice_agent/agent/delegate.py
DELEGATE_TOOL: dict[str, Any]           # {"name":"delegate","description":...,"input_schema":{task: str, kind: "research"|"code"}, "eager_input_streaming": True}

@dataclass(frozen=True)
class DelegationResult:
    task_id: str
    kind: Literal["research", "code"]
    task: str
    ok: bool
    summary: str                        # ≤ 600 chars, written for speech
    duration_s: float

class DelegationManager:
    def __init__(self, *, llm: LLMClient, tools: ToolRegistry, config: AgentConfig,
                 max_concurrent: int = 2, max_rounds: int = 8, deadline_s: float = 180.0,
                 stage_logger: StageLogger | None = None) -> None: ...
    def start(self, task: str, kind: Literal["research", "code"], *, turn_id: int) -> str: ...  # raises Busy
    def results(self) -> AsyncIterator[DelegationResult]: ...
    async def aclose(self) -> None: ...  # cancels running subagents
```
Stage events emitted (names from `docs/EVENT-LOG.md`): `LLM_REQUEST`, `LLM_FIRST_TOKEN`, `TOOL_CALL`, `DELEGATION_STARTED`, `DELEGATION_FINISHED`.

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | The API facts listed in Action are current for the installed `anthropic` version | `claude-api` skill + `uv pip show anthropic`; note the version in the log |
| A2 | `claude-haiku-4-5` accepts streaming + client tools + `eager_input_streaming` | skill docs; P7 (live, human) confirms |
| A3 | Beta fallbacks and `web_search_20260209` are available on the Claude API for `claude-sonnet-5-5` | skill docs (platform-availability); P7 confirms |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_plain_answer_streams_text_then_done` (scripted LLM) — events order `FirstToken`, `TextDelta`+, `Done` | `uv run pytest -q tests/agent/test_session.py` |
| P2 | `test_home_tool_inline` — scripted tool_use `home__control_lights` → registry stub called → follow-up text streamed in same `respond()` | same |
| P3 | `test_parallel_tool_results_in_one_message` — two tool_use blocks → next request has exactly one user message with two `tool_result` blocks | same |
| P4 | `test_delegate_returns_immediately_and_announces` — `DelegationStarted` within the turn; subagent (scripted) finishes later; `pending_announcements()` yields it; `announce()` streams a summary | `uv run pytest -q tests/agent/test_delegate.py` |
| P5 | `test_cancel_mid_stream_records_spoken_prefix` and `test_history_trim_keeps_tool_pairs` | `tests/agent/test_session.py` |
| P6 | `test_request_shapes` — capture kwargs sent by `AnthropicLLMClient` to a mocked SDK: fast model has no `thinking`; subagent has `output_config.effort="medium"`, no forced `tool_choice`, fallback beta + `fallbacks="default"`, `eager_input_streaming` on client tools, tools sorted, `cache_control` after system | `uv run pytest -q tests/agent/test_llm_anthropic.py` |
| P8 | `test_fast_prompt_rules` — `FAST_SYSTEM_PROMPT` ≤ 2,200 chars, contains no `{` placeholders, and mentions each of the ten rules (one keyword assertion per rule: persona/warm, markdown, sentences, spell out, format, ask, correct, interrupt, delegate, invent); `hidden_tools` absent from the tools sent; the dynamic block is a separate system entry after the `cache_control` block | `uv run pytest -q tests/agent/test_session.py` |
| P7 | `test_live_agent` (`live`, human-run): one real turn "turn off the lights" against fake home registry; prints TTFT; one real delegation "what's the weather in Manila" | `VOICE_AGENT_LIVE=1 uv run pytest -q -m live tests/agent/test_live_agent.py` — **do not run** |

## Unknowns
- Whether the fast model's TTFT meets the ~200–400 ms budget (review §3) — measured in P7 and the benchmark, not here.

## Dependencies/license
`anthropic` (MIT), `jsonschema` (MIT) for tool-input validation.

## Acceptance Criteria
- [ ] Contracts implemented; no network in default tests
- [ ] P1–P6 and P8 green; P7 written and skipped; gate suite green

## Non-Goals
- No Pipecat wiring (ticket 11)
- No speculative/early LLM start (ticket 12)
- No email tools

## Security-sensitive
no (tool execution is sandboxed by ticket 08; keys never logged)

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/EVENT-LOG.md`, `docs/MCP-SERVERS.md`

## Execution Log
