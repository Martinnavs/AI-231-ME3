# Branches: what exists, how they relate, how to land them

Written 2026-10-21. Facts below were computed from the repository that day (commands at the end so you can recheck).

## The picture

The three `agy/*` branches are **one linear stack** on top of the local `main`. Nothing diverged, so there is nothing to reconcile today: each branch contains all the commits of the one below it.

```
origin/main  ff3b256  tickets: laptop speech track (21 server + benchmark gate, 22 lan backend, 23 laptop dev loop)
   |
main (local) 6ecb0f7  +3 commits NOT on origin (below)
   |
agy/laptop-speech            a7038c1   +30 commits   105 files   +18.9k lines   41 test files
   |
agy/smart-turn-experiment    7a57cd0   + 8 commits    30 files    +4.0k lines   45 test files
   |
agy/interrupts               (tip)     + 6 commits    28 files    +2.8k lines   46 test files, 529 tests pass
```

`git merge-tree --write-tree main agy/interrupts` reports a **clean merge**; `main` is an ancestor of all three, so landing them on today's `main` is a fast-forward.

### The three commits that are on local `main` but not on `origin/main`

```
0eb3afd 21: laptop speech Phase 1 benchmark - NO-GO, parked
f772738 21: log Supertonic 3 evaluation and add power_mode.sh
6ecb0f7 tickets: amend 21 (Supertonic 3, human GO override) and add 24 voice doc-chat; agy guardrails
```

Pushing any `agy/*` branch uploads these three commits too (they are ancestors). `origin/main` itself is **not** moved by a branch push. Whether to push `main` is your decision; nothing here does it.

## What each branch holds

| Branch | What it is | Status |
|---|---|---|
| `agy/laptop-speech` | The laptop speech server and the STT work: Parakeet-TDT 0.6B v2 + hotwords + entity repair ladder, early decode, the `doc_chat` voice test client with an improved loop (client-side end of turn, document names, clarifying questions, history), plus the STT experiments, scripts and findings (`docs/STT-HOSTING.md`). Also tickets 21 and 24's earlier work (Kroko, Supertonic 3, Qwen scaffold). | **Product code + experiments. Ready for review.** 444 tests at its tip. |
| `agy/smart-turn-experiment` | Adds: Gemini backend (`--llm gemini`), per-run recording (`--record`) and `scripts/session_report.py`, "anything to add?" (`--ask-to-add`, **off by default**), barge-in in `doc_chat` (the microphone stays open while it thinks and speaks), the stop-from-the-writing-thread audio fix, and the end-of-turn experiments (Smart Turn v3 audio model, Turnsense text model, the confirm-and-stitch simulation). | **Mixed.** Barge-in, recording, Gemini and the audio fix are product code. The Smart Turn / Turnsense / simulation scripts are **experiments** whose conclusion is "do not adopt as is" (`docs/SMART-TURN.md`). |
| `agy/interrupts` | Adds: the model-free echo responder (`--llm echo`) and random phrases, the automated interruption bench and its ablation, echo-aware barge-in (`EchoModel`), the Silero VAD detector (`--vad silero|both`), a graceful stop with a fade, and the **Pi test kit** (`scripts/pi_echo_check.py`, standard clips, the vendored Silero model) with the research and runbooks. | **Experiment tooling + the Pi kit. The Pi kit is the part to use next.** `EchoModel` is a candidate for deletion after the Pi results (see below). |

Commit message prefixes tell the story: `24:` and `21:` are the original tickets (earlier sessions); `stt:`, `lan_speech:`, `hybrid STT:` are the STT work; `doc_chat:` is the test client; `Smart Turn` / `Text-based` / `Simulate` are experiments.

## Recommended way to land them

**Option A (recommended): three stacked pull requests, merged bottom-up.** Each PR is reviewable on its own and the order is forced by the stack.

```bash
git fetch origin
# PR 1: the STT work and the doc_chat test client
gh pr create --base main               --head agy/laptop-speech            --title "Laptop speech: Parakeet + hotwords + repair, doc_chat test client"
# PR 2: needs PR 1 merged first, or base it on PR 1's branch:
gh pr create --base agy/laptop-speech  --head agy/smart-turn-experiment    --title "doc_chat: barge-in, Gemini, recording; end-of-turn experiments"
# PR 3:
gh pr create --base agy/smart-turn-experiment --head agy/interrupts        --title "Interruption bench, Silero option, Pi test kit"
# after merging PR 1, GitHub retargets PR 2 to main; repeat for PR 3
```

**Option B: one merge of everything** (fast-forward, because the stack is linear):

```bash
git checkout main && git merge --ff-only agy/interrupts      # only valid while main has not moved
```

**Option C: land the product code, park the experiments.** If the experiments should not enter `main`, merge `agy/laptop-speech` (A.1) and then cherry-pick only the product commits of the other two. Product commits, by message: `doc_chat: Gemini backend`, `doc_chat: --record ...`, `doc_chat: barge-in ...`, `doc_chat: stop the speaker from its own thread ...`, `doc_chat: echo responder ...` (tooling), `Pi test kit: ...`. Leave out `Smart Turn v3 experiment`, `Text-based end-of-turn check`, `Simulate end-early ...`, `doc_chat: --ask-to-add ...` (off by default, but unproven) and the `agy research:` commits (they are only notes). Cherry-picking across this stack conflicts in `pyproject.toml`/`uv.lock`/`docs/DOC-CHAT.md` (each commit edited them): resolve as below.

Whatever you pick, **do not merge `agy/interrupts` before you have decided about `EchoModel`** if you want it gone from `main`: delete it first on the branch (it is one class in `src/doc_chat/bargein.py`, the `--barge-echo-*` flags in `src/doc_chat/__main__.py`, and the tests with "echo" in `tests/doc_chat/test_bargein.py`).

## If `main` moves before you land them

Today the merge is clean. If someone commits to `main` first, expect conflicts only in files both sides touched. Everything below is a file that `main` already has and the stack changes:

| File | Why it conflicts | How to resolve |
|---|---|---|
| `pyproject.toml` | the stack adds dependency groups `laptop` (more packages, `tokenizers`) and `pi`, and adds `lan_speech` and `doc_chat` to the mypy and wheel package lists | keep `main`'s `[project] dependencies` untouched (the Pi's default install must not change; the stack does not change it); re-add our `[dependency-groups]` entries `laptop` and `pi`, and the package lists |
| `uv.lock` | generated | never merge by hand: finish `pyproject.toml`, then `git checkout origin/main -- uv.lock && uv lock` |
| `docs/LAPTOP-SPEECH.md` | the stack appends phases (Kroko decision, Phase 3) | keep both: `main`'s edits above, ours appended at the end |
| `.scratch/project2-voice-agent/tickets/21-laptop-speech-server.md`, `.../24-voice-doc-chat.md` | the Execution Logs are append-only and both sides appended | keep both sets of entries, in date order; never rewrite earlier lines |
| `.gitignore` | one added line | union of both |
| `scripts/fetch_laptop_models.py`, `scripts/laptop_speech_bench.py` | small edits (Parakeet manifest, helpers) | read both diffs, keep both behaviours |

Resolution recipe (merge, the safe form; rebase works the same but rewrites history, so use it only on branches nobody else has):

```bash
git fetch origin
git checkout agy/laptop-speech && git merge origin/main        # resolve with the table, then:
uv lock && uv run --group laptop ruff check src tests scripts && uv run --group laptop mypy && uv run --group laptop pytest -q
git checkout agy/smart-turn-experiment && git merge agy/laptop-speech   # usually clean after the first
git checkout agy/interrupts && git merge agy/smart-turn-experiment
```

Expected after a clean resolution at the tip: ruff and mypy clean, **529 passed, 9 skipped, 2 xfailed**.

## Rules the stack already follows (check them after any merge)

- The Pi's default dependency set is unchanged: nothing was added to `[project] dependencies`. New packages are only in the `laptop` group and the new `pi` group. (`.scratch/project2-voice-agent/AGY-GUARDRAILS.md` states this rule for the earlier unattended runs.)
- `src/audio_engine`, `src/voice_agent`, `src/bench` and the tests that exist on `main` are untouched by the stack. `src/mcp_servers` does not exist on `main`; the MCP servers live in `src/voice_agent/mcp_servers`, also untouched.
- Binary files: `assets/silero_vad.onnx` (2.3 MB) and six WAV clips in `tests/fixtures/pi_echo/` (1.1 MB). `models/`, `out/`, recordings of the owner's voice (`bench/mic_*`) are git-ignored and **not** in history.
- No secrets: the history was searched for key-like strings, bearer tokens and credential file names; none found. API keys are read at run time from `~/.secrets/...` and never printed.

## About the history

- Two early commits on `agy/laptop-speech` were rebuilt (with `git commit-tree`) to keep three of the owner's untracked files out of history: `docs/VOICE-AGENT-EVALUATION-METRICS.md`, `kek.txt`, `resources/building-voice-agents/`. The old commits survive only in the local reflog; they are not on any branch and are not pushed. Those three paths appear in **no** commit reachable from the pushed branches (checked with `git log -- <path>`).
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Recheck commands

```bash
git log --graph --oneline --decorate --all -45
for b in agy/laptop-speech agy/smart-turn-experiment agy/interrupts; do git merge-base --is-ancestor main $b && echo "$b: main is an ancestor"; done
git merge-tree --write-tree main agy/interrupts >/dev/null && echo "clean merge"
git diff --stat main..agy/interrupts | tail -1
git log --format='%h %s' origin/main..main
```
