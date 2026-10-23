# 18: Task-success suite and fast-model comparison (Anthropic, Groq, class DGX)

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 19, 20 (the suite exercises the media and assistant tools); transitively 10, 14, 15
**Status:** ready-for-agent

## Amendment 2026-10-23 (direction change: local speech, laptop mic + Pi loudspeaker, `doc_chat` findings)
Provider baseline is Google (Gemini API) per D4 once `AGY-GEMINI-FAST.md` is reviewed; Anthropic Haiku and Groq remain comparison rows. `OpenAICompatLLMClient` (section 2) is needed earlier than this ticket, by 10 and 11; schedule it as the first part (or move it into 10).

## Files
- `src/voice_agent/agent/llm_openai_compat.py` (OpenAI-compatible implementation of `LLMClient`)
- `src/voice_agent/config.py`, `config/agent.toml`, `.env.example` (provider fields + `GROQ_API_KEY`, `DGX_API_KEY`)
- `src/voice_agent/pipeline/app.py` (`build_deps` picks the fast client by provider)
- `src/voice_agent/preflight.py`, `tests/test_preflight.py` (ticket 16's `secrets` check follows the provider)
- `src/bench/tasks.py` (suite runner + scoring), `src/bench/llm_compare.py` (multi-provider driver + verdict)
- `bench/tasks/v1.json` (the task suite)
- `docs/BENCHMARK.md` (append "Task success" and "Comparing fast models")
- `tests/agent/test_llm_openai_compat.py`, `tests/agent/test_live_providers.py` (`live`), `tests/bench/test_tasks.py`, `tests/bench/test_llm_compare.py`, `tests/test_config.py` (extend)

## Action
Two jobs, one harness. The professor grades **correctness** (the ten base tasks, any phrasing, plus one random complex task), **latency** and **naturalness** (2026-10-15). The rig (tickets 13–15) measures latency and turn-taking; this ticket measures whether the agent does the right thing, at text level and through the real `AgentSession`, and uses the same suite to compare fast-model providers.

### 1. Provider choice for the fast model
- `AgentConfig.fast_provider: Literal["anthropic", "groq", "dgx"] = "anthropic"`; `AgentConfig.fast_base_url: str | None` (required for `dgx`, ignored for `anthropic`; `groq` defaults to Groq's documented OpenAI-compatible base URL); `Secrets.groq_api_key` (`GROQ_API_KEY`), `Secrets.dgx_api_key` (`DGX_API_KEY`, optional: the class server may not need one). A `claude-*` `fast_model` with a non-anthropic provider, or the reverse, is a `ConfigError` naming both fields. Shipped config stays on anthropic, with commented groq and dgx examples (dgx base URL and model left as `<ask the instructor>`: the transcript says the class DGX serves a Qwen model, but not its URL, model ID or auth).
- Only the **fast** model is swappable. The delegated subagent stays on Anthropic (web search, refusal fallback, effort settings from ticket 10), so `build_deps` creates two clients when the provider isn't anthropic.

### 2. `OpenAICompatLLMClient`
Implements ticket 10's `LLMClient` with the official `openai` SDK (`AsyncOpenAI(base_url=..., api_key=...)`, streaming chat completions with tools). Groq and vLLM-style servers both speak this API; one client covers both. WebFetch Groq's OpenAI-compatibility docs (base URL, tool-call streaming, error codes, current tool-capable models) and the vLLM tool-calling docs before coding; record the URLs and SDK version in the log. Default groq comparison model: `llama-3.3-70b-versatile` if still listed with tool support, else Groq's documented recommendation.
- **Translate in, inside `stream_turn`:** Anthropic-shaped system blocks → one system message (drop `cache_control`); `tool_use` blocks → assistant `tool_calls` (JSON string arguments); each `tool_result` → its own `role: "tool"` message with the matching `tool_call_id` (`is_error` → content prefixed `ERROR: `); tools `{name, description, input_schema}` → `{type: "function", function: {name, description, parameters}}` (drop `eager_input_streaming`).
- **Normalize out:** the same `LLMStreamEvent`s as `AnthropicLLMClient`: text deltas as they arrive; tool calls assembled from streamed deltas by index (JSON-parse arguments; invalid JSON → a tool_use whose input fails ticket 10's schema validation, so the existing invalid-input path handles it); `stop_reason` `stop→end_turn`, `tool_calls→tool_use`, `length→max_tokens`; usage → `input_tokens`/`output_tokens` (absent usage → zeros, not an error).
- **Errors:** SDK errors → ticket 10's `LLMConnectionError` / `LLMRateLimitError`; a provider's failed-tool-generation 400 (Groq documents one; verify its code) → `LLMError(cause="tool_use_failed")`; auth errors never retried.
- Kwargs other than `max_tokens` and `temperature` → `ValueError`.
- Some open models emit reasoning text (e.g. `<think>…</think>`) before the answer. Strip a leading `<think>` block from the text stream before it reaches the session (never speak it), and record that it happened in `usage["reasoning_stripped"]`. Note in the log which provider/model did this.

### 3. The task suite (`bench/tasks/v1.json`, `bench.tasks`)
Scripted conversations run through the **real** `AgentSession.respond()` with the **real** tool schemas: all configured MCP servers spawned through `McpClientManager` (home with an unreachable dashboard URL, media over a tmp `music_root` with a few generated tracks and its fake player, assistant over a tmp `state_dir` with weather mocked). Calls go through a **recording registry** that forwards media and assistant calls to the real servers (their state is checked) and intercepts home calls with canned dashboard-shaped results. `delegate` is backed by a `DelegationManager` stub that records the call and returns "started".

Each case: `{id, task, turns: [{user, expect}]}`, `expect` one of `{"tool": name, "args": {key: value | {"any_of": [...]} | {"regex": "..."}}}`, `{"tool": null}` (no tool; e.g. a clarifying question), or `{"asks": true}` (no tool and the reply ends with a question). Optionally `{"state": {...}}`: a check against a server's state after the turn (e.g. the media `now_playing` title).

Coverage (≥ 60 cases; ME2's ten task groups, the professor's examples, and the complex-task path):
| Task group | What the cases must include |
|---|---|
| music | play by artist, by title, "something relaxing"; the correction "no, not that, play Opalite" mid-playback → `media__play` with the new query and `now_playing` changes; pause/resume/next/stop/volume, each in 3 phrasings |
| lights | on/off/colour/brightness in 3 phrasings each; an unsupported colour → the model relays the device limit |
| temperature | "set it to 22", "make it warmer", "twenty-four degrees please" |
| weather | now / tomorrow / another city |
| time | "what time is it", "what's the date" |
| timer | "5 minutes", "a minute and a half", "set a timer for my eggs" → asks how long |
| alarm | "7:15 tomorrow", "quarter past seven", "wake me up at six thirty" |
| call / message | with contact; without contact → asks, next turn answers → the call/message happens with the right contact and text |
| reminders | add with and without a time; list; remove |
| conversation | small talk and a general-knowledge question → no tool; a birthday stated three ways in a verification-style dialogue ("January 1, 1990", "the first of January, ninety", "1/1/1990") → the model confirms the same date each time |
| complex | "build me a simple website about my cat", "research X and summarize it" → `delegate` with the right `kind` |

Scoring per case: every turn's `expect` matched (tool name, listed args, `asks`, state). Per task group: success rate. Per turn: `ttft_s`, `first_clause_s` (ticket 11's `clause_boundary`), `spoken_ok` (no markdown characters, no digits unless they appear in the user's turn, ≤ 3 sentences). Report: overall and per-group success, the failing cases with transcript excerpts, and spoken-format pass rate. `--fake` runs the suite with `ScriptedLLMClient` scripts derived from the expectations, so the harness is testable offline.

### 4. Provider comparison (`bench.llm_compare`)
Runs the suite once per configured provider (`--providers anthropic,groq,dgx`; skips one whose key/URL is missing, with a reason), `--reps` times (default 2), alternating provider order per rep. Report per provider: task success % (overall and per group), p50/p95/n of `ttft_s` and `first_clause_s` (ticket 14's `MetricStats`), spoken-format pass %, errors, `<think>` stripping count.

**Suggested verdict, thresholds fixed now:** a challenger replaces anthropic only if its p50 TTFT is ≥ 100 ms lower **and** its overall task success is within 3 percentage points **and** no task group drops by more than 10 points **and** spoken-format pass ≥ 95% **and** errors ≤ 5% of calls. Otherwise recommend anthropic. With two passing challengers, prefer the lower p50 TTFT. The report states which conditions passed. A human decides D4 and confirms with one rig run per finalist (ticket 15).

### 5. Docs
`docs/BENCHMARK.md`: "Task success" (how to run, how to add a case, what is scored) and "Comparing fast models" (command, cost ≈ cases × turns × reps × providers paid calls, the verdict rule, follow-up rig runs).

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| provider `groq`/`dgx` with key or base URL missing | `ConfigError` naming the variable/field at startup; no network |
| provider/model mismatch (`groq` + `claude-*`) | `ConfigError` naming `agent.fast_provider` and `agent.fast_model` |
| streamed tool-call arguments split across many deltas, two parallel calls interleaved by index | assembled per index; both emitted in order |
| tool-call arguments not valid JSON | tool_use with unparsed input → ticket 10's invalid-input path |
| failed tool generation 400 | `LLMError(cause="tool_use_failed")` → session's spoken `Error`; in the suite, the turn fails with that reason |
| 429 / connection error | mapped errors; suite records and continues |
| 401 | not retried; comparison skips that provider with "check <VAR>" (never prints the key) |
| model streams `<think>…</think>` first | stripped before the session sees text; counted |
| provider returns no `usage` | zeros; no error |
| unsupported kwarg | `ValueError` naming it |
| suite case names a tool no server lists | abort before any call, naming the case and the available tools |
| a server fails to spawn for the suite | abort: the suite needs the real schemas |
| a provider errors on > 5% of calls | marked "unreliable"; can't be recommended |
| output dir exists | refuse unless `--overwrite` |

### C — Contracts
```python
# src/voice_agent/config.py (additions)
#   AgentConfig.fast_provider: Literal["anthropic", "groq", "dgx"] = "anthropic"
#   AgentConfig.fast_base_url: str | None = None
#   Secrets.groq_api_key, Secrets.dgx_api_key: str | None

# src/voice_agent/agent/llm_openai_compat.py
class OpenAICompatLLMClient:            # implements ticket 10's LLMClient
    def __init__(self, *, base_url: str, api_key: str | None, provider: str,
                 max_retries: int = 2, timeout_s: float = 20.0) -> None: ...
    def stream_turn(self, *, model: str, system: list[dict[str, Any]], messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMStreamEvent]: ...
def to_chat_messages(system: list[dict[str, Any]], messages: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
def to_chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
class ThinkStripper:                    # incremental: feed(delta) -> str to emit; handles a tag split across deltas
    def feed(self, delta: str) -> str: ...
    @property
    def stripped(self) -> bool: ...

# src/bench/tasks.py
@dataclass(frozen=True)
class TurnResult:
    case_id: str
    turn: int
    ok: bool
    reason: str | None                  # first unmet expectation
    tool_called: str | None
    ttft_s: float | None
    first_clause_s: float | None
    spoken_ok: bool | None
    error: str | None

@dataclass(frozen=True)
class SuiteResult:
    provider: str
    model: str
    cases_ok: int
    cases_total: int
    by_group: dict[str, float]          # task group -> success rate
    turns: list[TurnResult]

def load_suite(path: Path) -> list[dict[str, Any]]: ...          # validates shape and tool names
async def run_suite(suite: list[dict[str, Any]], *, config: AppConfig, llm: LLMClient, provider: str) -> SuiteResult: ...
def spoken_ok(text: str, user_text: str) -> bool: ...
def main(argv: Sequence[str] | None = None) -> int: ...
#   python -m bench.tasks --suite bench/tasks/v1.json --run-id ID [--fake] [--overwrite]
#   writes out/bench/tasks/<run-id>/{results.json, report.md, meta.json}

# src/bench/llm_compare.py
@dataclass(frozen=True)
class Verdict:
    recommend: str                      # "anthropic" | "groq" | "dgx"
    conditions: dict[str, dict[str, bool]]   # per challenger: condition -> passed
def verdict(results: Mapping[str, list[SuiteResult]]) -> Verdict: ...
def main(argv: Sequence[str] | None = None) -> int: ...
#   python -m bench.llm_compare --suite bench/tasks/v1.json --providers anthropic,groq,dgx --reps 2 --run-id ID [--fake]
#   writes out/bench/llm-compare/<run-id>/{results.json, report.md, meta.json}
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | Groq serves an OpenAI-compatible chat API (base URL, streaming tool calls as indexed deltas in `choices[0].delta.tool_calls`) | Groq docs (WebFetch); P2 encodes the shape |
| A2 | A vLLM-served Qwen model supports OpenAI-style tool calls when the server enables a tool parser | vLLM tool-calling docs; if the class server doesn't enable it, `dgx` can't call tools: report that from the human's P9 run, don't work around it |
| A3 | `llama-3.3-70b-versatile` (or the documented successor) supports tools and streaming on Groq | Groq models page; record ID and date |
| A4 | The home, media and assistant servers list their tools with their backends offline (unreachable dashboard, fake player, mocked weather) | tickets 07, 19, 20 Edge rows; the suite relies on it |
| A5 | ticket 11's `clause_boundary()` exists with its contract signature | import it; never copy the rule |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_translation_round_trip` — text, two parallel `tool_use`, two `tool_result` (one `is_error`), `cache_control` system block → chat messages with matching `tool_call_id`s, `ERROR:` prefix, no `cache_control`; tools → function schemas without `eager_input_streaming` | `uv run pytest -q tests/agent/test_llm_openai_compat.py` |
| P2 | `test_stream_normalization` — mocked stream: text, then two tool calls whose arguments arrive in 5 interleaved fragments → events identical in shape to `AnthropicLLMClient`'s; stop reasons and usage mapped; missing usage → zeros | same |
| P3 | `test_error_mapping` — mocked 429, connection error, 401, tool-generation 400 → the mapped errors | same |
| P4 | `test_think_stripper` — `<think>` split across 3 deltas, then answer → only the answer emitted, `stripped=True`; text without the tag passes through unchanged | same |
| P5 | `test_session_runs_on_compat_client` — ticket 10's P1/P2 scenarios with the compat client over a mocked SDK give the same `AgentEvent` sequence as the Anthropic client | same |
| P6 | `test_suite_shape_and_coverage` — `bench/tasks/v1.json` loads; ≥ 60 cases; every task group in the table present; every `expect.tool` exists in the real servers' tool lists (spawned) | `uv run pytest -q tests/bench/test_tasks.py` |
| P7 | `test_suite_fake_end_to_end` — `bench.tasks.main(["--fake", ...])` passes 100% of cases; then a deliberately wrong script for the music-correction case → that case fails with reason naming the unmet expectation and the `now_playing` check | same |
| P8 | `test_compare_fake_and_verdict` — `llm_compare.main(["--fake", ...])` with injected delays (anthropic 400 ms, groq 150 ms, dgx 250 ms) and one failing group for dgx → TTFT p50s within 10 ms; verdict table: gain 99 ms → anthropic; gain 150 ms with −4 pts → anthropic; one group −11 pts → anthropic; all pass → challenger; two pass → lower TTFT | `uv run pytest -q tests/bench/test_llm_compare.py` |
| P9 | `test_live_providers` (`live`, human-run): one home-command turn and one clarification turn per configured provider; prints TTFT and tool calls | `VOICE_AGENT_LIVE=1 uv run pytest -q -m live tests/agent/test_live_providers.py` — **do not run** |
| P10 | `test_config_provider` and `test_secrets_check_follows_provider` — missing key/URL → `ConfigError` / preflight fail naming it; mismatch → `ConfigError`; keys masked | `uv run pytest -q tests/test_config.py tests/test_preflight.py` |

## Unknowns
- The class DGX endpoint's URL, model ID, auth and whether tool calling is enabled — ask the instructor (human checkpoint).
- Whether a challenger's TTFT advantage survives the Pi's network path — answered by the human's comparison run and rig runs (D4).

## Dependencies/license
`openai` (Apache-2.0).

## Acceptance Criteria
- [ ] `fast_provider` defaults to `anthropic`; tickets 10–12, 19, 20 tests still green
- [ ] `python -m bench.tasks --fake ...` and `python -m bench.llm_compare --fake ...` produce reports on the dev host
- [ ] P1–P8 and P10 green; P9 written and skipped; gate suite green

## Non-Goals
- Switching the default provider (human decision D4); making the subagent provider swappable
- Running any paid or remote model call, including the suite against a real provider
- Grading naturalness (human rating checkpoint; see DESIGN.md)

## Security-sensitive
no (new keys handled like the existing ones: env only, masked, never logged)

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/EVENT-LOG.md`, `docs/MCP-SERVERS.md`, `docs/BENCHMARK.md`

## Execution Log
