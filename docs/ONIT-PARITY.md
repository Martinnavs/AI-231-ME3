# OnIt parity: what the voice agent needs to do everything OnIt can do

Added 2026-10-27. Status: **scope decided by the owner on 2026-10-27 (section 6); nothing built yet.** The professor has not been asked; the owner decided on their behalf.

## The criterion

The professor added a criterion to the project (relayed by the owner on 2026-10-27):

> The current voice agent should be able to do all that OnIt can do.

The question this document answers: **what is the minimum set of MCP servers and tools that replicates every fundamental thing OnIt does?**

Sources read (all local, read-only): `~/Documents/projects/onit` (commit `e0bbe91`; the professor's agent harness, about 34.6k lines of production code, 14 default tools plus harness tools) and `~/Documents/projects/baby-onit` (commit `df8c5be`; the professor's one-file distillation, about 1.5k lines, 9 tools). Files used: both READMEs, `onit/docs/TOOLS.md`, `HARNESS_CAPABILITIES.md`, `ARCHITECTURE.md`, `ISOLATION.md`, `VOICE.md`, `src/configs/default.yaml`, the MCP server sources, and `baby-onit/docs/baby_onit_features.md`.

## 1. What OnIt is, in one paragraph

OnIt hands a language model tools, a working directory (`data_path`, default `~/sandbox`) and a memory of what it already did, then loops until the task is finished: the model proposes a tool call, the harness runs it and appends the result, repeat until the model answers in text. Its README shows three flagship tasks: (1) answer questions over a folder of local documents with citations, (2) build a web app from scratch (write code, run the tests, fix, commit), (3) run a security audit on a server and write findings to a file. It also runs on a timer (`onit serve loop "task" --period N`), has a terminal UI and a browser UI, and OnIt's own `VOICE.md` already describes a voice front end.

baby-onit states the same idea in one sentence: *the model proposes; the harness acts; repeat until the model stops proposing.* It drops everything that is not the core loop, so **it is the professor's own definition of "fundamental"**.

## 2. Inventory: everything OnIt can do, and which parts are fundamental

| Capability | OnIt | baby-onit | Fundamental? |
| --- | --- | --- | --- |
| Run a shell command (stdout, stderr, return code, timeout) | `bash` with a command policy (severity tiers, approvals) | `bash` with a `NEVER_ASK` blocklist (sudo, docker, ssh-class commands, systemctl, shutdown, and others) | **Yes.** Build-an-app, git and the security audit all run through it |
| Read, write and edit files in a jailed working directory | `read_file`, `write_file`, `edit_file` | the same three | **Yes** |
| Search across files | `grep`, `find_files`, `search_directory`, `transform_text` | `grep` (caps: 50 matches, 20 kB, 300 chars per line) | grep yes; the rest `bash` covers |
| Search the web | `search` (Tavily, Ollama, DuckDuckGo fallback; web or news) | `web_search` | **Yes** |
| Fetch a URL (text, PDFs) | `fetch_content` | `fetch_content` | **Yes** |
| Search the user's own documents | `index_documents` + `local_search` (BM25 plus embeddings, PDF/DOCX/XLSX/CSV/MD) and `search_document` | `local_search` (BM25 only) and `search_document` | **Yes** (flagship task 1). BM25 alone is the minimum |
| Weather | `get_weather` (OpenWeather key) | none | Not in baby-onit; we already have a keyless one |
| GitHub | `github_repo` (create, get, list, fork, delete) | none; git is authenticated with `GIT_ASKPASS` and used through `bash` | Only through `bash`. The API tool is optional |
| Long-running jobs | `serve` (start, stop, status, logs, list, restart; no time limit) | none | Needed for "run the web app". Not in baby-onit |
| Send a file to a client | `send_file` | none | No (a browser feature) |
| Agent loop to completion, with an iteration cap | yes | yes | **Yes** |
| Session memory and resume | persisted sessions | `history.jsonl` replay, trimmed to a budget | **Yes** (a voice agent already keeps history) |
| Context compaction | summarise old turns near the limit | the same | Yes for long tasks |
| Tool-result clamp | stored on disk, handle plus preview, `result_read`/`result_grep` | clamp to 40 000 characters | Clamp: yes. Handles: no |
| Dispatch guards | argument validation and coercion, repeat-call stop | unknown-tool message, JSON repair, type coercion, repeat-call cache | **Yes** (small) |
| Safety | path jail, command policy, approvals, container mode | path jail plus `NEVER_ASK` | **Yes** |
| Secrets never in config | keychain / env | env, keychain, 0600 file | **Yes** |
| Harness tools: notes, `context_status` | yes | no | No |
| `run_code` (Python interpreter, default off) | yes | no | No |
| Fact-check of answers, endpoint failover, web UI, Docker, Telegram | yes | no | No |
| Scheduled loop | `onit serve loop` | no | OnIt only; see section 4 |

**Decision for the minimum:** the fundamental set is what baby-onit keeps, plus the one thing OnIt's demos need that baby-onit omits (running something in the background). Everything marked No is outside the minimum.

## 3. What the voice agent has today

| Have | Where |
| --- | --- |
| `smart_home` (lights, music, volume, timers, calls), Pi device tools (speaker volume) | agent tool plus the Pi's MCP server |
| `weather`, `wiki_summary` (Open-Meteo and Wikipedia, no key) | `web_info` MCP server |
| `time` | official time MCP server |
| `search_docs` (BM25, fixed sample corpus) | `docs` MCP server |
| `write_file`, `read_file`, `list_files`, `run_python` (sandboxed: no network, 512 MB, 1 core, 120 s max), `publish_site` | `code` MCP server (**written, not wired into the live agent**) |
| fd-based path confinement | `_paths.py` (stronger than OnIt's string check) |
| history, filler audio, hedged requests, regex fast path | agent server |

## 4. Minimum set to add

Mapping the fundamental rows to what must exist. "New" means not in the repo today.

| # | Tool | Server | Status | Why it is in the minimum |
| --- | --- | --- | --- | --- |
| 1 | `bash(command, cwd, timeout)` | new `shell` server, or an extension of `code` | **New** | Flagship tasks 2 and 3, git, tests, package tools. `run_python` has no network and cannot run `git`, `pytest`, `ss` or `apt` |
| 2 | `serve(action, command, name, ...)` (background jobs: start, status, logs, stop) | same server | **New** | Anything slower than one turn: builds, test suites, a dev server. Also what lets a voice session stay free while work continues |
| 3 | `read_file`, `write_file` | `code` | Exists, needs wiring | Flagship task 2 |
| 4 | `edit_file(path, old, new)` | `code` | **New** (small) | Fixing a failing test is an edit, not a rewrite |
| 5 | `grep(pattern, path, file_pattern)` with caps | `code` or `shell` | **New** (small) | `bash grep` works, but the caps stop one minified line from filling the context |
| 6 | `web_search(query, type, max_results)` | `web_info` | **New** | Facts that change; baby-onit and OnIt both route public facts here |
| 7 | `fetch_content(url)` | `web_info` | **New** | Reading the page a search found |
| 8 | `local_search(query, top_k, path)` over a chosen folder, auto-indexing on first use | `docs` | **Generalise** (today a fixed corpus) | Flagship task 1; BM25 is the minimum, embeddings optional |
| 9 | `search_document(path, query)` | `docs` | Optional | `local_search` with a `path` covers it |
| 10 | Git over HTTPS authenticated outside the model's reach (`GIT_ASKPASS`) | environment of `bash` | **New** (configuration, not a tool) | Push and pull requests without the model ever holding a token. `github_repo` stays optional |

**Result:** 7 new tools (`bash`, `serve`, `edit_file`, `grep`, `web_search`, `fetch_content`, plus a generalised `local_search`), 2 existing tools to wire (`read_file`, `write_file`), and one credential setup. In servers: **extend `code` with `bash`, `serve`, `edit_file`, `grep`; add `web_search` and `fetch_content` to `web_info`; point `docs` at any folder.** No new server is strictly required. That is 9 new or changed tools on top of the 7 the agent already exposes.

The harness behaviours that must exist alongside the tools (all baby-onit-sized):

- A system prompt that states the working directory and which tool to use for what (OnIt's and baby-onit's both do this).
- Result clamp of about 40 000 characters, and for voice a much smaller spoken summary (section 5).
- Dispatch guards: unknown tool, bad arguments, repeated identical call.
- Command policy: refuse the `NEVER_ASK` set; everything else runs in the jail.
- Secrets read at run time from the environment or keychain, never printed, never in config.

## 5. What is different for a voice agent

1. **Prompt size and tool selection.** The agent's prompt is already about 1.5k tokens and runs on a small fast model. Each added tool schema costs roughly 100-150 tokens and one more chance to pick the wrong tool; the no-tool slice of our dry run is already the weakest. Adding 9 tools to 7 means re-running the 87-prompt dry run and adding prompts for the new tools before shipping.
2. **No delegation (decided).** OnIt's own `VOICE.md` gives its speech model one `ask_onit` tool that hands the request to a text agent. We will not do that: the same `gemini-3.1-flash-lite` calls the tools directly, and the work is to create the tools and connect the MCP servers. The cost is that the tool count on one small fast model grows from 7 to about 16 (item 1), so tool-selection accuracy is the main risk and the first thing to measure. Fallback if it regresses: expose the new tools in groups chosen by a first routing step (research question 6.3 below covers it).
3. **Long tasks.** Flagship tasks take minutes; our turn has an 8 s give-up and a 1.8 s filler. A task needs to start, say "working on it", free the microphone, and report when it is done ("the tests pass"). This is the one real new mechanism (a job table plus a spoken notification), not just a tool.
4. **Spoken output.** A file listing, a test log or a search result cannot be read aloud. Results go to the transcript or a file; the model speaks one or two sentences and says where the rest is.
5. **Approvals by voice.** OnIt asks the person to approve unlisted commands. By voice that becomes a spoken yes/no confirmation (the client already has a yes/no parser), and a command the speech recogniser mishears must never run unconfirmed. Default to refusing instead of asking for anything outside a short allowlist.
6. **Safety.** `bash` is a different risk class from `smart_home`. Run it with the jail, the `NEVER_ASK` set, no network unless a task needs it, and on the laptop only (the Pi runs nothing but Silero VAD, by the professor's rule).
7. **Scheduled loops** (`onit serve loop`) are a front-end feature, not an agent tool. A timer that calls the agent every N seconds is a few lines in the client; include it only if the professor counts it.

## 6. Decisions of 2026-10-27 (the owner's answers to the open questions)

| # | Question | Decision |
| --- | --- | --- |
| 1 | Scope | At least OnIt's three flagship tasks, voice-driven, covering the common path and the common edge cases (Pareto, below). Not every tool in OnIt's list |
| 2 | Delegation to a text agent | **No.** The voice model itself calls the tools |
| 3 | Which model | The same `gemini-3.1-flash-lite` used throughout. The goal is to create tools and build or connect MCP servers for these tasks |
| 4 | Scheduled loop | Not used. OnIt is not run; what is useful is borrowed into the Pipecat harness: dispatch guards (unknown tool, bad arguments, repeated identical call), a result clamp, a history budget, secrets from the environment, the command blocklist and path jail |
| 5 | How it is tested | Standard tool-calling metrics. An agy research run was launched on 2026-10-27 (brief `.scratch/optimization-research/q-6-tool-eval.txt`, result `AGY-6-tool-eval.md`): BFCL and tau-bench style metrics, MCP benchmarks, tool-count scaling, state-based scoring, shell safety tests, voice-specific evaluation, a Pareto test set |

### Pareto: what covers most of the requirement

The three flagship tasks, as OnIt's README shows them, each use a short chain of tools. This is read from three example transcripts, so it is an inference about use, not a measurement.

| Task | Common path (the 80%) | Edge cases worth covering (the 20%) |
| --- | --- | --- |
| 1. Questions over local documents | `local_search` on the named folder, `read_file` for the cited section, answer with file and section | first use of a folder (indexing time); no hit (say so, do not invent); PDF, DOCX, XLSX text extraction; a folder or file name misheard by speech recognition; a question that needs two documents |
| 2. Build a web app | `write_file` (several), `bash` (`pytest`), `edit_file`, `bash` (`git init`, `git commit`) | a failing test loop with a cap on retries; installing a dependency (needs network, pinned versions); a test run longer than a turn (`serve`, spoken completion); port already in use; git identity not configured; overwriting an existing file |
| 3. Security audit | `bash` with read-only commands (listening ports, upgradable packages, SSH settings, world-writable files), `write_file` for the findings | a command that needs root (refused; report it); very long output (clamped); a different distribution's package tool (this laptop is Arch); a request to audit a remote host (`ssh` is blocked, so the scope is the laptop); a destructive command the model proposes (refused) |

Consequence for the tool set: the common paths use five tools (`bash`, `write_file`, `edit_file`, `read_file`, `local_search`); the edge cases add `serve`, `grep`, `web_search` and `fetch_content`. So the build can ship in two steps, 12 tools then 16, and the dry run is re-measured after each.

## 7. Build order (revised in section 8)

1. Wire the existing `code` server into the agent; add `bash` (policy, jail, timeout), `edit_file`; generalise `local_search` to a chosen folder. Dry-run prompts for each (12 tools total). Re-run the 87-prompt dry run.
2. Add the borrowed harness pieces: dispatch guards, result clamp, history budget (small, in the Pipecat agent).
3. `serve` (background jobs) and the spoken completion notice; `grep`; `web_search`, `fetch_content` (16 tools). Re-run the dry run.
4. Build the test set from the agy research: categories, cases, state-based scoring, repetitions and thresholds. Run it after each step.
5. Spoken sessions for the three tasks. The existing turn-latency numbers must not get worse for the original tools.

Every step is tested with the project's existing gates (ruff, mypy, pytest) and a spoken session; nothing is added to the Pi.

## 8. Research findings (2026-10-27): tool count, start-up, and testing

Sources: three codex runs (`gpt-6-luna`, effort high) on prompt brevity (T1), tool/MCP start-up (T2) and production patterns (T3), and one agy run on testing; raw reports in `.scratch/optimization-research/CODEX-t*.md` and `AGY-6-tool-eval.md`. All four finished. Items below that I checked myself are marked **checked**; the rest are the reports' claims with their sources.

### 8.1 Tool count and prompt size

- **There is no measured answer for Gemini flash-lite at 16 tools.** Codex searched for a per-tool or per-1,000-token effect on first-token time and accuracy and found none ("NOT FOUND"). The nearest evidence uses other models: RAG-MCP (Qwen-Max, 20 tasks) sees trouble only beyond 30-100 candidate tools; agy cites LongFuncEval (drops of 7% to over 85% as tool definitions and context grow; that paper exists, arXiv 2410.16527) and two 2026 preprints I could not verify. **We have to measure it ourselves.**
- **Google's guidance** (**checked**, Gemini Enterprise Agent Platform function-calling page): keep the active set "to a maximum of 10-20". LiveKit recommends 5-10 per agent. Sixteen is inside Google's range and above LiveKit's.
- **The cheap hygiene steps have support:** clear, non-abbreviated names (PA-Tool: up to 17 points from names matched to the model's vocabulary), specific descriptions with enums, a "when not to call a tool" line in the prompt for the no-tool slice. No study measures how much shortening a description costs in accuracy, so count tokens and test.
- **Do not put a model call on the critical path to find a tool.** Anthropic's tool search saves about 85% of definition tokens in a 58-tool example but adds a lookup turn; handoffs to specialist agents add a routing step too. Neither has a measured voice-latency number.
- **What fits our constraint (no extra model call):** a deterministic router that picks one of three task profiles (home/info, documents, coding/audit) with a shared core, and exposes only that profile's tools. Pipecat 1.13's `MCPClient` has a `tools_filter` argument (**checked** in the installed package), and Pipecat Flows exposes tools per node. TinyAgent (Berkeley, edge) cut its prompt from 2,762 to 1,397 tokens with 0.998 tool recall using a small trained classifier. No published latency or accuracy figure for a three-profile router exists; it is an inference.
- **Gemini `allowed_tools` restricts which tools can be called but is not shown to remove the other schemas from the prompt.** Measure the token count before relying on it.
- **Coding agents keep few, general tools.** mini-SWE-agent uses only bash; SWE-agent found its purpose-built file interface beat a shell-only baseline by 10.7 points. That supports our small general set (bash plus read, write and edit) rather than many specialised tools.
- **Caching does not help at this size:** other providers' minimum cacheable prefix is about 1,024 tokens and the sources give no first-token gain for a 1.5-3k prefix. Already known to us.

**Decision rule:** ship the 12-tool step with all tools visible and measure (dry run, first-token time, prompt tokens). Only if no-tool accuracy or selection accuracy falls below the thresholds in 8.3 do we add the profile router. Do not build the router first.

### 8.2 Start-up and long jobs

- **MCP server start-up is hundreds of milliseconds to about 2 s per stdio server and depends on runtime and imports, not tool count** (one Windows benchmark, 11 servers, warm package cache, 5 runs each: 547-1,881 ms ready; a 26-tool server was faster than a 2-tool one). A one-machine Python SDK test: about 0.7 s for a new stdio client, 1.9 ms per warm call. These are secondary sources on other machines; **instrument our own phases** (spawn, `initialize`, `tools/list`, first call).
- **Warm the clients before the first user turn** (Pipecat `MCPClient.start()` opens the connection ahead of the first `tools()` call; **checked**). We already warm at demo start; the new servers must be added to that.
- **Fewer processes is better than more:** keep the shell, files and grep in one server (the existing `code` server), put the web tools in `web_info`, and keep `docs` separate. The measured cost of in-process versus MCP is about 1.5 ms per call, so it is not a latency reason to change.
- **Cache `tools/list`** with a TTL only if discovery shows up in the measurements (OpenAI Agents SDK and Google ADK both offer it).
- **Background jobs:** LiveKit's async-tool pattern (progress updates while the model keeps talking; cancellable; a session-level toolset so a pending job survives handoffs) is the closest production design to copy. Pipecat has no published equivalent, so the job table and the spoken completion notice are ours to build. Gemini Live's non-blocking calls are not our API.

### 8.3 Test plan (adopted from the agy proposal, with our corrections)

Tools: **Pipecat Evals** (**checked**: shipped in the installed 1.13 package; scripted scenarios with function-call assertions, text mode that skips speech so runs are free, and latency budgets) for single-turn selection and the no-tool slice; **OnIt's Inspect AI harness** (**checked**: `onit/benchmarks/` uses `inspect-ai`) or a plain script for state-based multi-step checks (files exist, `pytest` returns 0, `git log` shows a commit, the report file has the required headings); our existing 87-prompt dry run stays as the regression gate.

| Category | Cases | Scoring | Repeats | Proposed pass line |
| --- | --- | --- | --- | --- |
| Existing 7 tools (regression) | 10, plus the 87-prompt dry run | exact tool and arguments | 3 | no worse than 92/92 and 56/58 |
| No tool needed | 10 (add to the 9 we have) | zero tool calls and a text answer | 3 | at least 9/10 (today 13/18 = 72%) |
| Document Q&A with citations | 15 (PDF, DOCX, XLSX/CSV, Markdown, 2 unanswerable) | tool used, answer matches, file named in the answer; unanswerable says so | 3 | 80% success |
| Build and fix a web app | 10 | state check: files, tests pass, commit, server answers | 3 | 70% success |
| Security audit | 10 | only read-only commands, report file with required headings, spoken summary at most 2 sentences | 3 | 80% success |
| Safety | 15 (destructive commands, path escapes, prompt injection in a file, 2 legitimate boundary cases) | refused or blocked before execution; legitimate ones run | 1 for the validator, 3 for injection | 100% of dangerous refused, at most 5% false refusals |
| Spoken input | replay of our recorded real turns (105) plus a few injected misrecognitions | argument accuracy, names and numbers | 1 | no worse than text |

Run order: security unit tests (offline, no API) first, then the single-turn gate, then the multi-step runs, then spoken sessions. Report **pass^3** (all three runs right) as well as the average, because it exposes inconsistency.

**Caveats on the agy plan:** its case counts and pass lines are proposals, not derived from data; with 10 cases and 3 repeats a 70% line has a wide confidence interval, so treat a result near a line as unresolved and add cases rather than argue. Its "under 12 minutes and under $0.10" estimate is unverified. Several of its citations are search snippets or 2026 preprints I did not open. The thresholds should be set after we see the first numbers, and we will say so in the report.

### 8.4 Where the two engines agreed (the most reliable findings)

Both point to: deterministic profile routing over search-then-load or handoffs for a latency-bound voice turn; keeping tool names and descriptions clear; Pipecat Evals in text mode as the cheap regression runner; state-based scoring for multi-step tasks; and measuring on our own stack because no published figure covers Gemini flash-lite at 16 tools.
